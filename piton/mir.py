"""MIR canónico mínimo para pruebas y trazas, no una VM de producción."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
import json
import operator
from typing import Any, Dict, List, Optional, Sequence

from piton.hir import HIRKind, HIRNode


_BUILTIN_EXCEPTIONS = {"Exception", "ValueError", "TypeError", "RuntimeError"}


class MIRLoweringError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MIRInstruction:
    op: str
    args: tuple[Any, ...] = ()
    result: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "args": list(self.args),
            "result": self.result,
        }


@dataclass(slots=True)
class MIRBlock:
    label: str
    instructions: List[MIRInstruction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "instructions": [instruction.to_dict() for instruction in self.instructions],
        }


@dataclass(slots=True)
class MIRFunction:
    name: str
    params: List[str] = field(default_factory=list)
    blocks: List[MIRBlock] = field(default_factory=list)
    defaults: List[Optional[Any]] = field(default_factory=list)
    vararg: Optional[str] = None
    kwarg: Optional[str] = None
    cell_vars: List[str] = field(default_factory=list)
    self_class: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "params": list(self.params),
            "defaults": list(self.defaults),
            "vararg": self.vararg,
            "kwarg": self.kwarg,
            "cell_vars": list(self.cell_vars),
            "blocks": [block.to_dict() for block in self.blocks],
        }


@dataclass(slots=True)
class MIRModule:
    functions: List[MIRFunction] = field(default_factory=list)
    classes: dict = field(default_factory=dict)
    class_parents: dict = field(default_factory=dict)
    class_mro: dict = field(default_factory=dict)
    class_properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"functions": [function.to_dict() for function in self.functions]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _Builder:
    def __init__(self, name: str, params: Sequence[str] = ()):
        self.function = MIRFunction(name=name, params=list(params))
        self.temp_counter = 0
        self.block_counter = 0
        self.current = self.new_block("entry")
        self.closures: dict[str, tuple[str, tuple[str, ...]]] = {}
        self.cell_params: set[str] = set()
        self.exception_handlers: list[tuple[str, str | None]] = []
        self.in_except_handler = False
        self.reraise_type: str | None = None
        self.loop_counter = 0
        self.module_aliases: dict[str, str] = {}
        self.from_import_aliases: dict[str, str] = {}
        self.super_class: str | None = None
        self.super_self: str | None = None
        self.is_async = False
        self.awaiting = False

    def new_block(self, label: Optional[str] = None) -> MIRBlock:
        if label is None:
            label = f"b{self.block_counter}"
        self.block_counter += 1
        block = MIRBlock(label)
        self.function.blocks.append(block)
        return block

    def temp(self) -> str:
        name = f"%{self.temp_counter}"
        self.temp_counter += 1
        return name

    def emit(self, op: str, *args: Any, result: Optional[str] = None) -> Optional[str]:
        self.current.instructions.append(MIRInstruction(op, tuple(args), result))
        return result


class MIRLowerer:
    def __init__(self):
        self.functions: List[MIRFunction] = []
        self.generators: dict[str, tuple[HIRNode, ...]] = {}
        self.classes: dict[str, set[str]] = {}
        self.class_parents: dict[str, str | None] = {}
        self.class_base_list: dict[str, list[str]] = {}
        self.class_mro: dict[str, list[str]] = {}
        self._mro_memo: dict[str, list[str]] = {}
        self.class_properties: dict[str, dict[str, dict[str, str | None]]] = {}
        self.async_functions: set[str] = set()
        self.module_aliases: dict[str, str] = {}

    def lower(
        self, hir: HIRNode, modules: dict[str, HIRNode] | None = None,
        from_imports: dict | None = None,
        entry_file: str | None = None,
        module_meta: dict[str, tuple[str, bool]] | None = None,
    ) -> MIRModule:
        self.functions = []
        self.generators = {}
        self.classes = {}
        self.class_parents = {}
        self.class_base_list = {}
        self.class_mro = {}
        self._mro_memo = {}
        self.class_properties = {}
        self.async_functions = set()
        self.module_aliases = {}
        self.from_import_aliases = {}  # {local_name: qualified_name}
        self._module_scope_stack: list[tuple[dict[str, str], dict[str, str]]] = []
        self.module_meta = module_meta or {}
        self.function_params: dict[str, list[str]] = {}
        self.function_defaults: dict[str, dict[str, Any]] = {}
        self.function_varargs: dict[str, str | None] = {}
        self.function_kwargs: dict[str, str | None] = {}
        self.function_posonly: dict[str, list[str]] = {}
        self.function_kwonly: dict[str, list[str]] = {}
        imported_modules = modules or {}
        self.imported_modules = imported_modules
        if hir.kind == HIRKind.MODULE:
            module_body = getattr(hir, "body", [])
            self.async_functions = {
                node.name for node in module_body
                if node.kind == HIRKind.FUNC_DEF and getattr(node, "is_async", False)
            }
            for node in module_body:
                if node.kind == HIRKind.IMPORT:
                    for alias in node.names:
                        if alias.name in {"asyncio", "math", "sys"}:
                            self.module_aliases[alias.asname or alias.name] = alias.name
                            continue
                        if "." in alias.name:
                            top = alias.name.split(".")[0]
                            if top not in imported_modules:
                                raise MIRLoweringError(f"native module not supplied: {alias.name}")
                            if alias.asname:
                                self.module_aliases[alias.asname] = alias.name
                            else:
                                self.module_aliases[alias.name] = top
                                self.module_aliases[top] = top
                            continue
                        if alias.name not in imported_modules:
                            raise MIRLoweringError(f"native module not supplied: {alias.name}")
                        self.module_aliases[alias.asname or alias.name] = alias.name
                elif node.kind == HIRKind.IMPORT_FROM:
                    mod_name = getattr(node, "module", None)
                    if getattr(node, "is_star", False):
                        if mod_name in {"asyncio", "math", "sys"}:
                            raise MIRLoweringError("native star imports from builtin modules are not supported")
                        if not mod_name or getattr(node, "level", 0) or 0 > 0:
                            raise MIRLoweringError("native relative star imports are not supported")
                        if mod_name not in imported_modules:
                            raise MIRLoweringError(f"native star import module not supplied: {mod_name}")
                        prefix = f"{mod_name.replace('.', '__')}__"
                        for item in imported_modules[mod_name].body:
                            if item.kind == HIRKind.FUNC_DEF and not item.name.startswith("_"):
                                self.from_import_aliases[item.name] = f"{prefix}{item.name}"
                        continue
                    if mod_name:
                        if mod_name not in imported_modules and mod_name not in {"asyncio", "math", "sys"}:
                            raise MIRLoweringError(f"native from-import module not supplied: {mod_name}")
                        for alias in node.names:
                            local = alias.asname or alias.name
                            if mod_name in {"asyncio", "math"}:
                                self.from_import_aliases[local] = f"{mod_name}.{alias.name}"
                            else:
                                self.from_import_aliases[local] = f"{mod_name.replace('.', '__')}__{alias.name}"
            for module_name, imported in sorted(imported_modules.items()):
                for item in imported.body:
                    if item.kind not in {HIRKind.FUNC_DEF, HIRKind.IMPORT, HIRKind.IMPORT_FROM}:
                        raise MIRLoweringError("native imported modules currently support functions only")
            for module_name, imported in sorted(imported_modules.items()):
                scope_names, scope_modules = self._build_module_scope(module_name, imported)
                self._module_scope_stack.append((scope_names, scope_modules))
                try:
                    for item in imported.body:
                        if item.kind == HIRKind.FUNC_DEF:
                            self._lower_function(item, f"{module_name.replace('.', '__')}__{item.name}")
                finally:
                    self._module_scope_stack.pop()
            for node in module_body:
                if node.kind != HIRKind.CLASS_DEF:
                    continue
                if node.keywords or node.decorators or any(
                    item.kind not in {HIRKind.FUNC_DEF, HIRKind.PASS}
                    for item in node.body
                ):
                    raise MIRLoweringError("native classes currently require methods only, no keywords or class-level decorators")
                bases = [base.name for base in node.bases if getattr(base, "name", None)]
                for base in bases:
                    if base not in self.classes and base not in _BUILTIN_EXCEPTIONS:
                        raise MIRLoweringError(f"native base class '{base}' must be defined before '{node.name}'")
                self.classes[node.name] = set()
                self.class_base_list[node.name] = bases
                self.class_parents[node.name] = bases[0] if bases else None
                property_methods: dict[str, dict[str, str | None]] = {}
                used_symbols: set[str] = set()
                for method in node.body:
                    if method.kind == HIRKind.PASS:
                        continue
                    symbol, role, prop_name = self._class_method_symbol(node.name, method, property_methods)
                    if used_symbols and symbol in used_symbols:
                        raise MIRLoweringError(
                            f"native '{node.name}.{method.name}' collides with another member lowered to symbol '{symbol}'"
                        )
                    used_symbols.add(symbol)
                    method_params = list(getattr(getattr(method, "args", None), "args", []) or [])
                    method_params = [*list(getattr(getattr(method, "args", None), "posonlyargs", []) or []), *method_params]
                    super_self = method_params[0] if method_params else None
                    self._lower_function(
                        method, symbol,
                        super_context=(node.name, super_self) if super_self else None,
                    )
                    if role is None:
                        self.classes[node.name].add(method.name)
                    else:
                        self.class_properties.setdefault(node.name, {}).setdefault(prop_name, {})[role] = symbol
            for node in module_body:
                if node.kind == HIRKind.FUNC_DEF and any(item.kind in {HIRKind.YIELD, HIRKind.YIELD_FROM} for item in node.body):
                    args = getattr(node, "args", None)
                    params = list(getattr(args, "args", []) or []) if args else []
                    if params or not node.body or any(item.kind != HIRKind.YIELD or item.value is None for item in node.body):
                        raise MIRLoweringError("native generators must be parameterless finite pure yields")
                    self.generators[node.name] = tuple(item.value for item in node.body)
            for node in module_body:
                if node.kind == HIRKind.FUNC_DEF and node.name not in self.generators:
                    self._lower_function(node)
            module = _Builder("<module>")
            module.module_aliases = dict(self.module_aliases)
            module.from_import_aliases = dict(self.from_import_aliases)
            self._lower_module_metadata(module, entry_file, module_meta)
            self._lower_statements(module, [node for node in module_body if node.kind != HIRKind.FUNC_DEF])
            self.functions.insert(0, module.function)
        else:
            builder = _Builder("<module>")
            self._lower_statement(builder, hir)
            self.functions.append(builder.function)
        module = MIRModule(self.functions)
        module.classes = dict(self.classes)
        module.class_parents = dict(self.class_parents)
        self._finalize_mro()
        module.class_mro = dict(self.class_mro)
        module.class_properties = dict(self.class_properties)
        return module

    def _compute_mro(self, name: str) -> list[str] | None:
        """C3 linearization for ``name``, memoized. Returns the full MRO (head +
        ancestors) or ``None`` on an inconsistent merge (spurious callers already
        validated every base is defined or a builtin exception, so a failure here
        is a genuine C3 conflict and must fail closed just like CPython's
        TypeError. Builtin exception bases are leaves ``[name]``; builtin classes
        are not user-linearizable into the graph and may only appear as leaves
        when they are the lone base."""
        if name in self._mro_memo:
            return self._mro_memo[name]
        bases = self.class_base_list.get(name) or []
        if not bases:
            self._mro_memo[name] = [name]
            return [name]
        seqs: list[list[str]] = []
        for base in bases:
            sub = self._compute_mro(base)
            if sub is None:
                self._mro_memo[name] = None
                return None
            seqs.append(list(sub))
            seqs.append([base])
        merged: list[str] = []
        all_bases = list(bases)
        for s in seqs:
            for b in s:
                if b not in all_bases:
                    all_bases.append(b)
        for _ in range(len(all_bases) + 2):
            candidate = None
            for s in seqs:
                if not s:
                    continue
                head = s[0]
                if all(head not in tail[1:] for tail in seqs):
                    candidate = head
                    break
            if candidate is None:
                self._mro_memo[name] = None
                return None
            merged.append(candidate)
            for s in seqs:
                if s and s[0] == candidate:
                    s.pop(0)
            if all(not s for s in seqs):
                break
        if any(s for s in seqs):
            self._mro_memo[name] = None
            return None
        self._mro_memo[name] = [name, *merged]
        return self._mro_memo[name]

    def _finalize_mro(self) -> None:
        for name in list(self.class_base_list) + [n for n in self.classes if n not in self.class_base_list]:
            mro = self._compute_mro(name)
            if mro is None:
                raise MIRLoweringError(
                    f"native class '{name}' has an inconsistent method resolution order (C3 merge); "
                    "cannot construct a linearization for multiple inheritance"
                )
            self.class_mro[name] = mro

    def _build_module_scope(self, module_name: str, imported: HIRNode) -> tuple[dict[str, str], dict[str, str]]:
        """Per-module name scope for an imported module body: imported module
        bodies are NOT executed, so their functions must resolve same-module
        names, from-imports and module aliases against the module's OWN context
        (IMPORT_CYCLIC_V1), never the entry's. Returns ``(names, modules)``:
        names = local callable name -> qualified symbol, modules = local alias
        -> dotted module."""
        names: dict[str, str] = {}
        modules: dict[str, str] = {}
        meta = self.module_meta.get(module_name)
        is_package = bool(meta and meta[1])
        base = module_name.rsplit(".", 1)[0] if ("." in module_name and not is_package) else module_name
        for item in imported.body:
            if item.kind == HIRKind.FUNC_DEF:
                names[item.name] = f"{module_name.replace('.', '__')}__{item.name}"
            elif item.kind == HIRKind.IMPORT:
                for alias in item.names:
                    if alias.name in {"asyncio", "math", "sys"}:
                        modules[alias.asname or alias.name] = alias.name
                        continue
                    if "." in alias.name:
                        top = alias.name.split(".")[0]
                        if top not in self.imported_modules:
                            raise MIRLoweringError(f"native module not supplied: {alias.name}")
                        if alias.asname:
                            modules[alias.asname] = alias.name
                        else:
                            modules[alias.name] = top
                            modules[top] = top
                        continue
                    if alias.name not in self.imported_modules:
                        raise MIRLoweringError(f"native module not supplied: {alias.name}")
                    modules[alias.asname or alias.name] = alias.name
            elif item.kind == HIRKind.IMPORT_FROM:
                mod_name = getattr(item, "module", None)
                level = getattr(item, "level", 0) or 0
                is_star = bool(getattr(item, "is_star", False))
                if level == 1:
                    if is_star:
                        raise MIRLoweringError("native star imports are only supported at the entry module")
                    if mod_name:
                        target = f"{base}.{mod_name}"
                        if target not in self.imported_modules:
                            raise MIRLoweringError(f"native from-import module not supplied: {target}")
                        for alias in item.names:
                            names[alias.asname or alias.name] = f"{target.replace('.', '__')}__{alias.name}"
                    else:
                        for alias in item.names:
                            target = f"{base}.{alias.asname or alias.name}"
                            if target not in self.imported_modules:
                                raise MIRLoweringError(f"native module not supplied: {target}")
                            modules[alias.asname or alias.name] = target
                    continue
                if mod_name in {"asyncio", "math", "sys"}:
                    for alias in item.names:
                        names[alias.asname or alias.name] = f"{mod_name}.{alias.name}"
                    continue
                if is_star:
                    if not mod_name:
                        raise MIRLoweringError("native relative star imports are not supported")
                    if mod_name not in self.imported_modules:
                        raise MIRLoweringError(f"native star import module not supplied: {mod_name}")
                    prefix = f"{mod_name.replace('.', '__')}__"
                    for item2 in self.imported_modules[mod_name].body:
                        if item2.kind == HIRKind.FUNC_DEF and not item2.name.startswith("_"):
                            names[item2.name] = f"{prefix}{item2.name}"
                    continue
                if mod_name:
                    if mod_name not in self.imported_modules:
                        raise MIRLoweringError(f"native from-import module not supplied: {mod_name}")
                    for alias in item.names:
                        names[alias.asname or alias.name] = f"{mod_name.replace('.', '__')}__{alias.name}"
        return names, modules

    def _lower_module_metadata(
        self, builder: "_Builder", entry_file: str | None, module_meta: dict[str, tuple[str, bool]] | None
    ) -> None:
        """MODULE_METADATA_V1: seeds the entry module's metadata globals and the
        ``sys`` module with its ``modules`` catalog of module objects.

        ``sys`` and ``__main__`` are always present; every imported native module
        (``module_meta``: dotted name -> (file path, is_package)) is added to the
        catalog. The entry ``__main__`` object mirrors CPython ``-c`` semantics:
        ``__name__ = "__main__"``, ``__package__ = None``; ``__file__`` exists only
        in files-mode (when ``entry_file`` is provided) exactly like a script."""
        def emit_const(value: Any) -> str:
            result = builder.temp()
            builder.emit("const", value, result=result)
            return result

        def emit_store(name: str, value: str) -> None:
            builder.emit("store", name, value)

        def module_object(name: str, file: str | None, package: Any) -> str:
            obj = builder.temp()
            builder.emit("object_new", "module", None, result=obj)
            builder.emit("set_attr", obj, "__name__", emit_const(name))
            builder.emit("set_attr", obj, "__package__", emit_const(package))
            if file is not None:
                builder.emit("set_attr", obj, "__file__", emit_const(file))
            return obj

        emit_store("__name__", emit_const("__main__"))
        emit_store("__package__", emit_const(None))
        if entry_file:
            emit_store("__file__", emit_const(entry_file))
        sys_obj = module_object("sys", None, "")
        entries: list[tuple[str, str]] = [
            ("sys", sys_obj),
            ("__main__", module_object("__main__", entry_file, None)),
        ]
        for name in sorted(module_meta or {}):
            file_path, is_package = module_meta[name]
            package = name.rsplit(".", 1)[0] if "." in name else (name if is_package else "")
            entries.append((name, module_object(name, file_path, package)))
        modules_dict = builder.temp()
        builder.emit("build_collection", "dict", tuple(entries), result=modules_dict)
        builder.emit("set_attr", sys_obj, "modules", modules_dict)
        builder.emit("store", "sys", sys_obj)

    def _lower_function(
        self, node: HIRNode, qualified_name: str | None = None,
        captures: tuple[str, ...] = (), cell_vars: list[str] | None = None,
        super_context: tuple[str, str] | None = None,
    ) -> None:
        args = getattr(node, "args", None)
        posonly_params = list(getattr(args, "posonlyargs", []) or []) if args else []
        params = list(getattr(args, "args", []) or []) if args else []
        kwonly_params = list(getattr(args, "kwonlyargs", []) or []) if args else []
        positional_params = [*posonly_params, *params]
        vararg_name = getattr(args, "vararg", None) if args else None
        kwarg_name = getattr(args, "kwarg", None) if args else None
        body = list(getattr(node, "body", []))
        builder = _Builder(qualified_name or node.name, [*captures, *positional_params])
        if super_context:
            builder.super_class, builder.super_self = super_context
            builder.function.self_class = super_context[0]
        builder.is_async = bool(getattr(node, "is_async", False))
        if self._module_scope_stack:
            scope_names, scope_modules = self._module_scope_stack[-1]
            builder.module_aliases = dict(scope_modules)
            builder.from_import_aliases = dict(scope_names)
        else:
            builder.module_aliases = dict(self.module_aliases)
            builder.from_import_aliases = dict(self.from_import_aliases)
        if cell_vars:
            builder.cell_params = set(cell_vars)
            if qualified_name and qualified_name != node.name:
                nested_args = getattr(node, "args", None)
                nested_n_args = len(
                    list(getattr(nested_args, "posonlyargs", []) or []) + list(getattr(nested_args, "args", []) or [])
                )
                builder.closures[node.name] = (qualified_name, tuple(sorted(cell_vars)), nested_n_args)
        defaults_map: dict[str, Any] = {}
        if args:
            raw_defaults = list(getattr(args, "defaults", []) or [])
            default_params = positional_params[-len(raw_defaults):] if raw_defaults else []
            for param, default_node in zip(default_params, raw_defaults):
                if default_node.kind != HIRKind.CONST:
                    raise MIRLoweringError("native function defaults currently support constant values only")
                defaults_map[param] = default_node.value
            for param, default_node in zip(kwonly_params, getattr(args, "kw_defaults", []) or []):
                if default_node is None:
                    continue
                if default_node.kind != HIRKind.CONST:
                    raise MIRLoweringError("native function defaults currently support constant values only")
                defaults_map[param] = default_node.value
        if (
            (posonly_params or kwonly_params or vararg_name or kwarg_name)
            and len(positional_params) + len(kwonly_params) + int(bool(vararg_name)) + int(bool(kwarg_name)) > 4
        ):
            raise MIRLoweringError("native variadic functions support at most four ABI parameters")
        if vararg_name:
            builder.function.vararg = vararg_name
            builder.function.params.append(vararg_name)
        builder.function.params.extend(kwonly_params)
        if kwarg_name:
            builder.function.kwarg = kwarg_name
            builder.function.params.append(kwarg_name)
        builder.function.defaults = [defaults_map.get(p) for p in builder.function.params]
        self.function_params[qualified_name or node.name] = list(builder.function.params)
        self.function_defaults[qualified_name or node.name] = defaults_map
        self.function_varargs[qualified_name or node.name] = vararg_name
        self.function_kwargs[qualified_name or node.name] = kwarg_name
        self.function_posonly[qualified_name or node.name] = posonly_params
        self.function_kwonly[qualified_name or node.name] = kwonly_params
        local_names = set(builder.function.params) | self._assigned_names(body)

        all_nested_captures: dict[str, tuple[str, tuple[str, ...]]] = {}
        available = local_names | (set(cell_vars) if cell_vars else set())
        for nested in (item for item in body if item.kind == HIRKind.FUNC_DEF):
            if self._contains_kind(nested.body, {HIRKind.GLOBAL}):
                raise MIRLoweringError("native closures do not support global declarations yet")
            nested_captures = tuple(name for name in sorted(self._nested_free_loads(nested)) if name in available)
            lifted_name = f"{builder.function.name}__{nested.name}"
            nested_args = getattr(nested, "args", None)
            nested_n_args = len(
                list(getattr(nested_args, "posonlyargs", []) or []) + list(getattr(nested_args, "args", []) or [])
            )
            all_nested_captures[nested.name] = (lifted_name, nested_captures, nested_n_args)
            builder.closures[nested.name] = (lifted_name, nested_captures, nested_n_args)

        captured_var_names = set()
        for _, (_, caps, _) in all_nested_captures.items():
            captured_var_names.update(caps)

        if captured_var_names:
            builder.cell_params = captured_var_names
            for cap in sorted(captured_var_names):
                if cap in (captures or ()):
                    continue
                if cap in builder.function.params:
                    cap_val = builder.temp()
                    builder.emit("load", cap, result=cap_val)
                else:
                    cap_val = None
                cell_ptr = builder.temp()
                builder.emit("cell_new", cap_val, result=cell_ptr)
                builder.emit("store", cap, cell_ptr)

        for nested in (item for item in body if item.kind == HIRKind.FUNC_DEF):
            nested_captures = tuple(name for name in sorted(self._nested_free_loads(nested)) if name in available)
            lifted_name = f"{builder.function.name}__{nested.name}"
            inner_cell_vars = [c for c in nested_captures if c in captured_var_names]
            self._lower_function(nested, lifted_name, nested_captures, cell_vars=inner_cell_vars)

        self._lower_statements(builder, [item for item in body if item.kind != HIRKind.FUNC_DEF])
        if not builder.current.instructions or builder.current.instructions[-1].op not in {"return", "jump", "branch"}:
            builder.emit("return", None)
        builder.function.cell_vars = sorted(captured_var_names)
        if cell_vars:
            builder.function.cell_vars = list(cell_vars)
        self.functions.append(builder.function)

    def _walk(self, value: Any):
        if isinstance(value, HIRNode):
            yield value
            if value.kind == HIRKind.FUNC_DEF:
                return
            for descriptor in fields(value):
                if descriptor.name in {"kind", "line", "col", "annotations"}:
                    continue
                yield from self._walk(getattr(value, descriptor.name))
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from self._walk(item)

    def _assigned_names(self, body: Sequence[HIRNode]) -> set[str]:
        return {
            node.name for node in self._walk(body)
            if node.kind == HIRKind.STORE and getattr(node, "name", None)
        }

    def _nested_free_loads(self, node: HIRNode) -> set[str]:
        args = getattr(node, "args", None)
        params_here = set(getattr(args, "args", []) or []) if args else set()
        locals_here = params_here | self._assigned_names(node.body)
        free = self._loaded_names(node.body) - locals_here
        free |= {
            name
            for statement in node.body
            if getattr(statement, "kind", None) == HIRKind.NONLOCAL
            for name in getattr(statement, "names", [])
        }
        for nested in (item for item in node.body if item.kind == HIRKind.FUNC_DEF):
            free |= {name for name in self._nested_free_loads(nested) if name not in locals_here}
        return free

    def _loaded_names(self, body: Sequence[HIRNode]) -> set[str]:
        return {
            node.name for node in self._walk(body)
            if node.kind == HIRKind.LOAD and getattr(node, "name", None)
        }

    def _contains_kind(self, body: Sequence[HIRNode], kinds: set[HIRKind]) -> bool:
        return any(node.kind in kinds for node in self._walk(body))

    def _lower_statements(self, builder: _Builder, statements: Sequence[HIRNode]) -> None:
        for statement in statements:
            self._lower_statement(builder, statement)

    def _lower_statement(self, builder: _Builder, node: HIRNode) -> None:
        kind = node.kind
        if kind == HIRKind.ASSIGN:
            value = self._lower_expr(builder, node.value)
            for target in node.targets:
                self._store(builder, target, value)
        elif kind == HIRKind.ANN_ASSIGN:
            value = self._lower_expr(builder, node.value) if node.value else self._lower_expr(builder, node.annotation)
            self._store(builder, node.target, value)
        elif kind == HIRKind.AUG_ASSIGN:
            left = self._lower_expr(builder, node.target)
            right = self._lower_expr(builder, node.value)
            result = self._binary(builder, node.op.rstrip("="), left, right)
            self._store(builder, node.target, result)
        elif kind == HIRKind.RETURN:
            builder.emit("return", self._lower_expr(builder, node.value) if node.value else None)
        elif kind == HIRKind.NONLOCAL:
            return
        elif kind == HIRKind.IF:
            self._lower_if(builder, node)
        elif kind == HIRKind.WHILE:
            self._lower_while(builder, node)
        elif kind == HIRKind.FOR:
            self._lower_for(builder, node)
        elif kind == HIRKind.TRY:
            self._lower_try(builder, node)
        elif kind == HIRKind.RAISE:
            self._lower_raise(builder, node)
        elif kind == HIRKind.IMPORT:
            return
        elif kind == HIRKind.IMPORT_FROM:
            return
        elif kind == HIRKind.EXPR if hasattr(HIRKind, "EXPR") else False:
            self._lower_expr(builder, node)
        elif kind == HIRKind.DELETE:
            for target in node.targets:
                if target.kind != HIRKind.ATTR:
                    raise MIRLoweringError("native del currently supports only attribute deletion (del obj.attr)")
                owner = self._lower_expr(builder, target.value)
                builder.emit("del_attr", owner, target.attr)
        else:
            if kind not in {HIRKind.FUNC_DEF, HIRKind.CLASS_DEF}:
                self._lower_expr(builder, node)

    def _lower_if(self, builder: _Builder, node: HIRNode) -> None:
        condition = self._lower_expr(builder, node.test)
        then_block = builder.new_block()
        else_block = builder.new_block()
        end_block = builder.new_block()
        builder.emit("branch", condition, then_block.label, else_block.label)
        builder.current = then_block
        self._lower_statements(builder, node.body)
        builder.emit("jump", end_block.label)
        builder.current = else_block
        if len(node.orelse) == 1 and node.orelse[0].kind == HIRKind.IF:
            self._lower_if(builder, node.orelse[0])
        else:
            self._lower_statements(builder, node.orelse)
            builder.emit("jump", end_block.label)
        builder.current = end_block

    def _lower_while(self, builder: _Builder, node: HIRNode) -> None:
        condition_block = builder.new_block()
        body_block = builder.new_block()
        end_block = builder.new_block()
        builder.emit("jump", condition_block.label)
        builder.current = condition_block
        condition = self._lower_expr(builder, node.test)
        builder.emit("branch", condition, body_block.label, end_block.label)
        builder.current = body_block
        self._lower_statements(builder, node.body)
        builder.emit("jump", condition_block.label)
        builder.current = end_block

    def _lower_for(self, builder: _Builder, node: HIRNode) -> None:
        if (
            node.is_async or node.iter.kind != HIRKind.CALL
            or node.iter.func.kind != HIRKind.LOAD
            or node.iter.func.name not in self.generators
            or node.iter.args or node.iter.keywords
        ):
            raise MIRLoweringError("native for currently requires a direct finite generator call")
        values = tuple(self._lower_expr(builder, value) for value in self.generators[node.iter.func.name])
        generator = builder.temp()
        builder.emit("build_collection", "tuple", values, result=generator)
        index_name = f"@for_index_{builder.loop_counter}"
        builder.loop_counter += 1
        zero = builder.temp()
        builder.emit("const", 0, result=zero)
        builder.emit("store", index_name, zero)
        condition_block = builder.new_block()
        body_block = builder.new_block()
        end_block = builder.new_block()
        builder.emit("jump", condition_block.label)
        builder.current = condition_block
        index = builder.temp()
        builder.emit("load", index_name, result=index)
        length = builder.temp()
        builder.emit("collection_len", generator, result=length)
        condition = self._compare(builder, "<", index, length)
        builder.emit("branch", condition, body_block.label, end_block.label)
        builder.current = body_block
        item = builder.temp()
        builder.emit("get_item", generator, index, result=item)
        if node.target.kind not in {HIRKind.LOAD, HIRKind.STORE}:
            raise MIRLoweringError("native generator loop target must be a name")
        builder.emit("store", node.target.name, item)
        self._lower_statements(builder, node.body)
        current = builder.temp()
        one = builder.temp()
        builder.emit("load", index_name, result=current)
        builder.emit("const", 1, result=one)
        following = self._binary(builder, "+", current, one)
        builder.emit("store", index_name, following)
        builder.emit("jump", condition_block.label)
        builder.current = end_block
        self._lower_statements(builder, node.orelse)

    def _lower_try(self, builder: _Builder, node: HIRNode) -> None:
        if node.orelse:
            raise MIRLoweringError("native try does not support else yet")
        if len(node.handlers) > 1:
            raise MIRLoweringError("native try supports at most one except handler")
        finally_body = list(getattr(node, "finalbody", []) or [])

        handler_block = None
        accepted = None
        end_block = builder.new_block()
        try_body_block = builder.new_block()
        finally_block = builder.new_block() if finally_body else None

        if node.handlers:
            handler = node.handlers[0]
            if not hasattr(handler, "body") or handler.name or handler.is_star:
                raise MIRLoweringError("native except does not support binding or except* yet")
            handler_block = builder.new_block()
            accepted = None
            if handler.type_ is not None:
                if handler.type_.kind != HIRKind.LOAD:
                    raise MIRLoweringError("native except type must be a builtin exception name")
                accepted = handler.type_.name

        # try_push
        builder.emit("try_push")
        builder.emit("jump", try_body_block.label)

        # try body block
        builder.current = try_body_block
        if handler_block:
            builder.exception_handlers.append((handler_block.label, accepted))
        self._lower_statements(builder, node.body)
        if handler_block:
            builder.exception_handlers.pop()
        builder.emit("try_pop")
        if finally_body:
            builder.emit("jump", finally_block.label)
        else:
            builder.emit("jump", end_block.label)

        # handler block (reached via raise_typed's catch_flag check)
        if handler_block:
            builder.current = handler_block
            builder.emit("reraise_save")
            builder.emit("catch_clear")
            previous_reraise_type = builder.reraise_type
            previous_in_handler = builder.in_except_handler
            builder.reraise_type = accepted
            builder.in_except_handler = True
            self._lower_statements(builder, handler.body)
            builder.reraise_type = previous_reraise_type
            builder.in_except_handler = previous_in_handler
            builder.emit("try_pop")
            if finally_body:
                builder.emit("jump", finally_block.label)
            else:
                builder.emit("jump", end_block.label)

        # finally block
        if finally_body:
            builder.current = finally_block
            self._lower_statements(builder, finally_body)
            builder.emit("jump", end_block.label)

        builder.current = end_block

    def _lower_raise(self, builder: _Builder, node: HIRNode) -> None:
        if node.cause is not None:
            raise MIRLoweringError("native raise does not support an explicit cause yet")
        if node.exc is None:
            self._lower_reraise(builder)
            return
        if node.exc.kind != HIRKind.CALL or node.exc.func.kind != HIRKind.LOAD:
            raise MIRLoweringError("native raise requires a call to an exception constructor")
        exception_type = node.exc.func.name
        if exception_type in _BUILTIN_EXCEPTIONS:
            pass
        elif exception_type in self.classes:
            if "Exception" not in self._exception_chain(exception_type):
                raise MIRLoweringError(
                    f"native custom exception '{exception_type}' must subclass Exception"
                )
        else:
            raise MIRLoweringError(f"unsupported native exception type '{exception_type}'")
        if len(node.exc.args) > 1 or node.exc.keywords:
            raise MIRLoweringError("unsupported native exception constructor")
        payload = self._lower_expr(builder, node.exc.args[0]) if node.exc.args else None
        handler_label = self._find_exception_handler(builder, exception_type)
        builder.emit("raise_typed", exception_type, payload, handler_label)

    def _lower_reraise(self, builder: _Builder) -> None:
        if not builder.in_except_handler:
            raise MIRLoweringError("native bare re-raise requires an enclosing except handler")
        if builder.reraise_type in (None, "Exception"):
            raise MIRLoweringError(
                "native bare re-raise from a catch-all (excepto Exception) handler is not supported yet"
            )
        handler_label = self._find_exception_handler(builder, builder.reraise_type)
        builder.emit("raise_active", builder.reraise_type, handler_label)

    def _exception_chain(self, name: str) -> list[str]:
        chain: list[str] = []
        current: str | None = name
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            chain.append(current)
            if current in _BUILTIN_EXCEPTIONS:
                current = "Exception" if current != "Exception" else None
            else:
                mro = self._mro_memo.get(current) or self._compute_mro(current)
                if mro and len(mro) > 1 and mro[1] not in seen:
                    current = mro[1]
                else:
                    current = self.class_parents.get(current)
        return chain

    def _find_exception_handler(
        self, builder: _Builder, exception_type: str
    ) -> str | None:
        chain = self._exception_chain(exception_type)
        for label, accepted in reversed(builder.exception_handlers):
            if accepted is None or accepted == "Exception" or accepted in chain:
                return label
        return None

    def _class_method_symbol(self, class_name: str, method: HIRNode, property_methods: dict[str, dict[str, str | None]]) -> tuple[str, str | None, str]:
        """Classifies one class-body method and returns ``(symbol, role, prop)``.

        ``role`` is ``"getter"``/``"setter"``/``"deleter"`` for the supported
        ``@property`` / ``@<name>.setter`` / ``@<name>.deleter`` decorators,
        ``None`` for an ordinary method (the only other accepted form). For
        ordinary methods ``prop`` is the method name (unused). Anything else
        fails closed.
        """
        decorators = list(getattr(method, "decorators", None) or [])
        if len(decorators) > 1:
            raise MIRLoweringError("native class methods support at most one @property decorator")
        if not decorators:
            return f"{class_name}__{method.name}", None, method.name
        decorator = decorators[0]
        if decorator.kind == HIRKind.LOAD and getattr(decorator, "name", None) == "property":
            if method.name in property_methods:
                raise MIRLoweringError(f"native duplicate @property '{method.name}' on '{class_name}'")
            property_methods[method.name] = {"getter": None, "setter": None, "deleter": None}
            return f"{class_name}__{method.name}", "getter", method.name
        if decorator.kind == HIRKind.ATTR:
            prop = getattr(decorator, "attr", "")
            if getattr(getattr(decorator, "value", None), "kind", None) != HIRKind.LOAD:
                raise MIRLoweringError(f"native property decorator '@{method.name}.{prop}' must reference the property name")
            if getattr(decorator.value, "name", None) != method.name:
                raise MIRLoweringError(f"native property decorator '@{decorator.value.name}.{prop}' must match the method name '{method.name}'")
            if prop not in {"setter", "deleter"}:
                raise MIRLoweringError("native class methods support only @property, @<name>.setter, @<name>.deleter")
            existing = property_methods.get(method.name)
            if existing is None:
                raise MIRLoweringError(
                    f"native @{method.name}.{prop} requires the property getter '@property def {method.name}' first in class '{class_name}'"
                )
            if existing[prop] is not None:
                raise MIRLoweringError(f"native duplicate @{method.name}.{prop} on '{class_name}'")
            return f"{class_name}__{method.name}__{prop}", prop, method.name
        raise MIRLoweringError(f"native class methods support only @property, @<name>.setter, @<name>.deleter")

    def _store(self, builder: _Builder, target: HIRNode, value: Any) -> None:
        if target.kind == HIRKind.STORE:
            if target.name in builder.cell_params:
                builder.emit("cell_store", target.name, value)
            else:
                builder.emit("store", target.name, value)
        elif target.kind == HIRKind.ATTR and target.value.kind in {HIRKind.LOAD, HIRKind.STORE}:
            owner = self._lower_expr(builder, target.value)
            builder.emit("set_attr", owner, target.attr, value)
        else:
            builder.emit("runtime_call", "set_target", target.kind.name, value)

    def _module_attr_chain(self, builder: "_Builder", node: Optional[HIRNode]) -> Optional[tuple[str, list[str]]]:
        """Returns ``(alias, [attr, ...])`` when ``node`` is an attribute chain
        rooted at a module alias load (``pkg.sub``, ``pkg.obj.met``); None otherwise."""
        attrs: list[str] = []
        current = node
        while getattr(current, "kind", None) == HIRKind.ATTR:
            attrs.append(current.attr)
            current = current.value
        if getattr(current, "kind", None) == HIRKind.LOAD and current.name in builder.module_aliases:
            if current.name not in {"asyncio", "math", "sys"}:
                return current.name, list(reversed(attrs))
        return None

    def _lower_super_call(self, builder: _Builder, attr: str, user_args: tuple) -> str:
        if not builder.super_class:
            raise MIRLoweringError("native super() must be called directly inside a class method")
        mro = self._mro_memo.get(builder.super_class) or self._compute_mro(builder.super_class)
        if not mro:
            raise MIRLoweringError(
                f"native super() error: class '{builder.super_class}' has no consistent MRO"
            )
        current_class = builder.super_class
        found_current = False
        next_class = None
        for cls in mro:
            if found_current:
                if cls in self.classes and attr in self.classes.get(cls, set()):
                    next_class = cls
                    break
                continue
            if cls == current_class:
                found_current = True
        if next_class is None:
            raise MIRLoweringError(
                f"native super().{attr}: no base class in MRO of '{current_class}' defines '{attr}'"
            )
        if builder.super_self in builder.cell_params:
            receiver = builder.temp()
            builder.emit("cell_load", builder.super_self, result=receiver)
        else:
            receiver = builder.temp()
            builder.emit("load", builder.super_self, result=receiver)
        args = tuple(self._lower_expr(builder, arg) for arg in user_args)
        if len(args) > 3:
            raise MIRLoweringError("native super() method calls support at most 3 user arguments")
        result = builder.temp()
        builder.emit("method_call", next_class, attr, receiver, args, result=result)
        return result

    def _lower_expr(self, builder: _Builder, node: Optional[HIRNode]) -> Any:
        if node is None:
            return None
        kind = node.kind
        if kind == HIRKind.CONST:
            result = builder.temp()
            builder.emit("const", node.value, result=result)
            return result
        if kind == HIRKind.LOAD:
            if node.name in builder.closures:
                lifted_name, _, n_args = builder.closures[node.name]
                capture_ops: list[Any] = []
                for capture_name in builder.closures[node.name][1]:
                    capture = builder.temp()
                    builder.emit("load", capture_name, result=capture)
                    capture_ops.append(capture)
                result = builder.temp()
                builder.emit("closure_new", lifted_name, n_args, tuple(capture_ops), result=result)
                return result
            if node.name in builder.cell_params:
                result = builder.temp()
                builder.emit("cell_load", node.name, result=result)
                return result
            result = builder.temp()
            builder.emit("load", builder.from_import_aliases.get(node.name, node.name), result=result)
            return result
        if kind == HIRKind.STORE:
            result = builder.temp()
            builder.emit("load", node.name, result=result)
            return result
        if kind == HIRKind.BINOP:
            return self._binary(builder, node.op, self._lower_expr(builder, node.left), self._lower_expr(builder, node.right))
        if kind == HIRKind.UNOP:
            operand = self._lower_expr(builder, node.operand)
            result = builder.temp()
            builder.emit("unary", node.op, operand, result=result)
            return result
        if kind == HIRKind.COMPARE:
            left = self._lower_expr(builder, node.left)
            for op, comparator in zip(node.ops, node.comparators):
                left = self._compare(builder, op, left, self._lower_expr(builder, comparator))
            return left
        if kind == HIRKind.CALL:
            if node.func.kind == HIRKind.LOAD and node.func.name == "super":
                raise MIRLoweringError(
                    "native super() must appear as super().method(...) directly inside a "
                    "class method call"
                )
            if getattr(node, "starred_args", []):
                raise MIRLoweringError("native positional unpacking is not supported yet")
            if node.func.kind == HIRKind.LOAD and node.func.name in self.generators:
                raise MIRLoweringError("native generator values cannot escape a direct for loop yet")
            if node.func.kind == HIRKind.LOAD and node.func.name in self.async_functions and not builder.awaiting:
                raise MIRLoweringError("native coroutine must be awaited or passed directly to asyncio.run")
            # Handle from-imported builtins (e.g. `desde math importar sqrt; sqrt(16)`)
            if node.func.kind == HIRKind.LOAD and node.func.name in builder.from_import_aliases:
                qualified = builder.from_import_aliases[node.func.name]
                if qualified == "math.sqrt":
                    if len(node.args) != 1 or node.keywords:
                        raise MIRLoweringError("native math.sqrt requires one positional argument")
                    value = self._lower_expr(builder, node.args[0])
                    result = builder.temp()
                    builder.emit("math_sqrt", value, result=result)
                    return result
            if node.func.kind == HIRKind.LOAD and node.func.name in self.classes:
                if node.keywords:
                    raise MIRLoweringError("native class constructors do not support keyword arguments yet")
                result = builder.temp()
                parent_name = self.class_parents.get(node.func.name)
                builder.emit("object_new", node.func.name, parent_name, result=result)
                mro = self._mro_memo.get(node.func.name) or self._compute_mro(node.func.name)
                init_class = None
                for cls in (mro or ()):
                    if cls in self.classes and "__init__" in self.classes[cls]:
                        init_class = cls
                        break
                if init_class is not None:
                    args = (result, *(self._lower_expr(builder, arg) for arg in node.args))
                    ignored = builder.temp()
                    builder.emit("method_call", init_class, "__init__", result, args[1:], result=ignored)
                elif node.args:
                    raise MIRLoweringError("native class without __init__ takes no arguments")
                return result
            if node.func.kind == HIRKind.ATTR and node.func.value.kind == HIRKind.ATTR:
                chain = self._module_attr_chain(builder, node.func)
                if chain is not None:
                    if node.keywords:
                        raise MIRLoweringError("native module calls do not support keyword arguments yet")
                    alias_name, attrs = chain
                    module_name = builder.module_aliases[alias_name]
                    symbol = f"{module_name.replace('.', '__')}__{'__'.join(attrs)}"
                    function = builder.temp()
                    builder.emit("load", symbol, result=function)
                    args = tuple(self._lower_expr(builder, arg) for arg in node.args)
                    result = builder.temp()
                    builder.emit("call", function, args, result=result)
                    return result
            if (
                node.func.kind == HIRKind.ATTR
                and node.func.value.kind == HIRKind.LOAD
                and node.func.value.name in builder.module_aliases
            ):
                if node.keywords:
                    raise MIRLoweringError("native module calls do not support keyword arguments yet")
                module_name = builder.module_aliases[node.func.value.name]
                if module_name == "asyncio" and node.func.attr == "run":
                    if len(node.args) != 1 or node.keywords or node.args[0].kind != HIRKind.CALL:
                        raise MIRLoweringError("native asyncio.run requires one direct coroutine call")
                    builder.awaiting = True
                    try:
                        return self._lower_expr(builder, node.args[0])
                    finally:
                        builder.awaiting = False
                if module_name == "math":
                    if node.func.attr != "sqrt" or len(node.args) != 1 or node.keywords:
                        raise MIRLoweringError("native math currently supports sqrt(value) only")
                    value = self._lower_expr(builder, node.args[0])
                    result = builder.temp()
                    builder.emit("math_sqrt", value, result=result)
                    return result
                function = builder.temp()
                builder.emit("load", f"{module_name.replace('.', '__')}__{node.func.attr}", result=function)
                args = tuple(self._lower_expr(builder, arg) for arg in node.args)
                result = builder.temp()
                builder.emit("call", function, args, result=result)
                return result
            if node.func.kind == HIRKind.ATTR:
                if node.keywords:
                    raise MIRLoweringError("native method calls do not support keyword arguments yet")
                super_call = node.func.value if node.func.value.kind == HIRKind.CALL else None
                if (
                    super_call is not None
                    and len(super_call.args) == 0
                    and not getattr(super_call, "keywords", None)
                    and super_call.func.kind == HIRKind.LOAD
                    and super_call.func.name == "super"
                ):
                    return self._lower_super_call(builder, node.func.attr, node.args)
                owner = self._lower_expr(builder, node.func.value)
                args = tuple(self._lower_expr(builder, arg) for arg in node.args)
                result = builder.temp()
                builder.emit("method_call", None, node.func.attr, owner, args, result=result)
                return result
            closure = builder.closures.get(node.func.name) if node.func.kind == HIRKind.LOAD else None
            if closure:
                if node.keywords:
                    raise MIRLoweringError("native closure calls do not support keyword arguments yet")
                lifted_name, capture_names, _ = closure
                function = builder.temp()
                builder.emit("load", lifted_name, result=function)
                captures = []
                for name in capture_names:
                    capture = builder.temp()
                    builder.emit("load", name, result=capture)
                    captures.append(capture)
                args = (*captures, *(self._lower_expr(builder, arg) for arg in node.args))
            elif node.func.kind == HIRKind.LOAD and (
                self.function_varargs.get(node.func.name) is not None
                or self.function_kwargs.get(node.func.name) is not None
                or self.function_posonly.get(node.func.name)
                or self.function_kwonly.get(node.func.name)
            ):
                args = self._lower_variadic_call(builder, node.func.name, node.args, node.keywords)
                function = builder.temp()
                builder.emit("load", node.func.name, result=function)
            elif node.func.kind == HIRKind.LOAD and node.keywords:
                args = self._resolve_call_args(builder, node.func.name, node.args, node.keywords)
                function = builder.temp()
                builder.emit("load", node.func.name, result=function)
            else:
                if node.keywords:
                    raise MIRLoweringError("native keyword args require a known function")
                function = self._lower_expr(builder, node.func)
                args = tuple(self._lower_expr(builder, arg) for arg in node.args)
            result = builder.temp()
            builder.emit("call", function, args, result=result)
            return result
        if kind in {HIRKind.LIST, HIRKind.TUPLE, HIRKind.SET}:
            items = tuple(self._lower_expr(builder, item) for item in node.elts)
            result = builder.temp()
            builder.emit("build_collection", kind.name.lower(), items, result=result)
            return result
        if kind == HIRKind.DICT:
            items = tuple(
                (self._lower_expr(builder, key), self._lower_expr(builder, value))
                for key, value in zip(node.keys, node.values)
            )
            result = builder.temp()
            builder.emit("build_collection", "dict", items, result=result)
            return result
        if kind == HIRKind.SUBSCR:
            result = builder.temp()
            builder.emit(
                "get_item", self._lower_expr(builder, node.value),
                self._lower_expr(builder, node.slice), result=result,
            )
            return result
        if kind == HIRKind.ATTR:
            if self._module_attr_chain(builder, node) is not None:
                raise MIRLoweringError(
                    "native module attribute value access is not supported yet; "
                    "use module function calls (e.g. 'pkg.sub.fn(...)')"
                )
            if builder.super_class and node.value.kind == HIRKind.LOAD and node.value.name == "super":
                raise MIRLoweringError(
                    "native super() must appear as super().method(...) directly inside a "
                    "class method call; bare attribute access on super() is not supported"
                )
            if (
                node.value.kind == HIRKind.CALL
                and len(getattr(node.value, "args", None) or []) == 0
                and node.value.func.kind == HIRKind.LOAD
                and node.value.func.name == "super"
            ):
                raise MIRLoweringError(
                    "native super().attr is only supported as super().attr(...) (a call)"
                )
            result = builder.temp()
            builder.emit("get_attr", self._lower_expr(builder, node.value), node.attr, result=result)
            return result
        if kind == HIRKind.AWAIT:
            if not builder.is_async:
                raise MIRLoweringError("await is only valid inside a native async function")
            builder.awaiting = True
            try:
                return self._lower_expr(builder, node.value)
            finally:
                builder.awaiting = False
        result = builder.temp()
        builder.emit("runtime_call", "unsupported", kind.name, result=result)
        return result

    def _binary(self, builder: _Builder, op: str, left: Any, right: Any) -> str:
        result = builder.temp()
        builder.emit("binary", op, left, right, result=result)
        return result

    def _compare(self, builder: _Builder, op: str, left: Any, right: Any) -> str:
        result = builder.temp()
        builder.emit("compare", op, left, right, result=result)
        return result

    def _lower_variadic_call(
        self, builder: _Builder, func_name: str,
        positional: Sequence[HIRNode], keywords: Sequence[HIRNode],
    ) -> tuple[str, ...]:
        positional_only = self.function_posonly[func_name]
        keyword_only = self.function_kwonly[func_name]
        vararg = self.function_varargs[func_name]
        kwarg = self.function_kwargs[func_name]
        if any(keyword.arg is None for keyword in keywords):
            raise MIRLoweringError("native keyword unpacking is not supported yet")
        params = self.function_params[func_name]
        regular_count = len(params) - len(positional_only) - len(keyword_only) - int(bool(vararg)) - int(bool(kwarg))
        regular_params = params[len(positional_only):len(positional_only) + regular_count]
        positional_params = [*positional_only, *regular_params]
        defaults = self.function_defaults[func_name]
        positional_values: list[str | None] = [None] * len(positional_params)
        keyword_values: list[str | None] = [None] * len(keyword_only)
        for index, arg in enumerate(positional[:len(positional_params)]):
            positional_values[index] = self._lower_expr(builder, arg)
        extra_keywords: list[HIRNode] = []
        for keyword in keywords:
            if keyword.arg in regular_params:
                index = len(positional_only) + regular_params.index(keyword.arg)
                if positional_values[index] is not None:
                    raise MIRLoweringError(f"multiple values for argument: {keyword.arg}")
                positional_values[index] = self._lower_expr(builder, keyword.value)
            elif keyword.arg in keyword_only:
                index = keyword_only.index(keyword.arg)
                if keyword_values[index] is not None:
                    raise MIRLoweringError(f"multiple values for argument: {keyword.arg}")
                keyword_values[index] = self._lower_expr(builder, keyword.value)
            else:
                extra_keywords.append(keyword)
        for index, name in enumerate(positional_params):
            if positional_values[index] is None:
                if name not in defaults:
                    raise MIRLoweringError(f"missing required argument: {name}")
                default = builder.temp()
                builder.emit("const", defaults[name], result=default)
                positional_values[index] = default
        for index, name in enumerate(keyword_only):
            if keyword_values[index] is None:
                if name not in defaults:
                    raise MIRLoweringError(f"missing keyword-only argument: {name}")
                default = builder.temp()
                builder.emit("const", defaults[name], result=default)
                keyword_values[index] = default
        extra_values = tuple(
            self._lower_expr(builder, arg) for arg in positional[len(positional_params):]
        )
        if extra_values and not vararg:
            raise MIRLoweringError(f"too many positional arguments to {func_name}")
        if extra_keywords and not kwarg:
            raise MIRLoweringError(f"unexpected keyword argument: {extra_keywords[0].arg}")
        packed: list[str] = []
        if vararg:
            rest = builder.temp()
            builder.emit("build_collection", "tuple", extra_values, result=rest)
            packed.append(rest)
        if kwarg:
            items = []
            for keyword in extra_keywords:
                key = builder.temp()
                builder.emit("const", keyword.arg, result=key)
                items.append((key, self._lower_expr(builder, keyword.value)))
            rest = builder.temp()
            builder.emit("build_collection", "dict", tuple(items), result=rest)
            packed.append(rest)
        if vararg:
            return (*positional_values, packed.pop(0), *keyword_values, *packed)
        return (*positional_values, *keyword_values, *packed)

    def _resolve_call_args(
        self, builder: _Builder, func_name: str,
        positional: Sequence[HIRNode], keywords: Sequence[HIRNode],
    ) -> tuple[str, ...]:
        params = self.function_params.get(func_name)
        if params is None:
            raise MIRLoweringError(f"native keyword call requires known function: {func_name}")
        defaults = self.function_defaults.get(func_name, {})
        n = len(params)
        if len(positional) > n:
            raise MIRLoweringError(f"too many positional arguments to {func_name}")
        values: list[str | None] = [None] * n
        for i, arg in enumerate(positional):
            values[i] = self._lower_expr(builder, arg)
        for kw in keywords:
            if kw.arg is None:
                raise MIRLoweringError("native **kwargs is not supported yet")
            if kw.arg not in params:
                raise MIRLoweringError(f"unexpected keyword argument: {kw.arg}")
            idx = params.index(kw.arg)
            if values[idx] is not None:
                raise MIRLoweringError(f"multiple values for argument: {kw.arg}")
            values[idx] = self._lower_expr(builder, kw.value)
        filled = [i for i, v in enumerate(values) if v is not None]
        max_filled = max(filled) if filled else -1
        for i in range(max_filled + 1):
            if values[i] is None:
                if params[i] not in defaults:
                    raise MIRLoweringError(f"missing required argument: {params[i]}")
                result = builder.temp()
                builder.emit("const", defaults[params[i]], result=result)
                values[i] = result
        for i, p in enumerate(params):
            if p not in defaults and values[i] is None:
                raise MIRLoweringError(f"missing required argument: {p}")
        return tuple(v for v in values[:max_filled + 1] if v is not None)


def lower_hir_to_mir(
    hir: HIRNode, modules: dict[str, HIRNode] | None = None, from_imports: dict | None = None,
    entry_file: str | None = None, module_meta: dict[str, tuple[str, bool]] | None = None,
) -> MIRModule:
    return MIRLowerer().lower(hir, modules, from_imports=from_imports, entry_file=entry_file, module_meta=module_meta)


class MIREvaluator:
    """Evaluator pequeño para validar MIR y producir trazas reproducibles."""

    def __init__(self, module: MIRModule):
        self.module = module
        self.functions = {function.name: function for function in module.functions}
        self.trace: List[dict[str, Any]] = []

    def run(self, name: str = "<module>") -> Any:
        self.trace = []
        return self._run_function(self.functions[name], {})

    def _run_function(self, function: MIRFunction, incoming: Dict[str, Any]) -> Any:
        env = dict(incoming)
        blocks = {block.label: block for block in function.blocks}
        label = function.blocks[0].label
        ip = 0
        while True:
            block = blocks[label]
            while ip < len(block.instructions):
                instruction = block.instructions[ip]
                self.trace.append({"function": function.name, "block": label, "op": instruction.op})
                jump = self._execute(instruction, env)
                if isinstance(jump, tuple) and jump[0] == "jump":
                    label, ip = jump[1], 0
                    break
                if isinstance(jump, tuple) and jump[0] == "return":
                    return jump[1]
                ip += 1
            else:
                return None

    def _value(self, value: Any, env: Dict[str, Any]) -> Any:
        return env.get(value, value) if isinstance(value, str) and value.startswith("%") else value

    def _execute(self, instruction: MIRInstruction, env: Dict[str, Any]) -> Any:
        op = instruction.op
        args = instruction.args
        if op == "const":
            env[instruction.result] = args[0]
        elif op == "load":
            env[instruction.result] = env.get(args[0], args[0] if args[0] in self.functions or args[0] in {"print", "imprimir", "range", "rango"} else None)
        elif op == "store":
            env[args[0]] = self._value(args[1], env)
        elif op == "binary":
            operations = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv, "//": operator.floordiv, "%": operator.mod}
            env[instruction.result] = operations[args[0]](self._value(args[1], env), self._value(args[2], env))
        elif op == "unary":
            operations = {"+": operator.pos, "-": operator.neg, "~": operator.invert, "not": operator.not_, "no": operator.not_}
            env[instruction.result] = operations[args[0]](self._value(args[1], env))
        elif op == "compare":
            operations = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
            env[instruction.result] = operations[args[0]](self._value(args[1], env), self._value(args[2], env))
        elif op == "build_collection":
            kind, raw_items = args
            if kind == "dict":
                env[instruction.result] = {
                    self._value(key, env): self._value(value, env) for key, value in raw_items
                }
            else:
                items = [self._value(item, env) for item in raw_items]
                env[instruction.result] = tuple(items) if kind == "tuple" else set(items) if kind == "set" else items
        elif op == "get_item":
            env[instruction.result] = self._value(args[0], env)[self._value(args[1], env)]
        elif op == "collection_len":
            env[instruction.result] = len(self._value(args[0], env))
        elif op == "object_new":
            env[instruction.result] = {"__class__": args[0]}
        elif op == "set_attr":
            self._value(args[0], env)[args[1]] = self._value(args[2], env)
        elif op == "get_attr":
            env[instruction.result] = self._value(args[0], env)[args[1]]
        elif op == "method_call":
            class_name, method_name, owner, raw_values = args
            instance = self._value(owner, env)
            target = self.functions[f"{class_name or instance['__class__']}__{method_name}"]
            values = [instance, *(self._value(value, env) for value in raw_values)]
            env[instruction.result] = self._run_function(target, dict(zip(target.params, values)))
        elif op == "math_sqrt":
            import math
            env[instruction.result] = math.sqrt(self._value(args[0], env))
        elif op == "call":
            function = self._value(args[0], env)
            values = [self._value(value, env) for value in args[1]]
            if isinstance(function, str) and function in self.functions:
                target = self.functions[function]
                env[instruction.result] = self._run_function(target, dict(zip(target.params, values)))
            elif callable(function):
                env[instruction.result] = function(*values)
            else:
                builtins = {"print": print, "imprimir": print, "range": range, "rango": range}
                env[instruction.result] = builtins[function](*values)
        elif op == "runtime_call":
            env[instruction.result] = self._value(args[-1], env) if instruction.result else None
        elif op == "branch":
            return ("jump", args[1] if self._value(args[0], env) else args[2])
        elif op == "jump":
            return ("jump", args[0])
        elif op == "return":
            return ("return", self._value(args[0], env))
        return None


def evaluate_mir(module: MIRModule, name: str = "<module>") -> tuple[Any, List[dict[str, Any]]]:
    evaluator = MIREvaluator(module)
    result = evaluator.run(name)
    return result, evaluator.trace
