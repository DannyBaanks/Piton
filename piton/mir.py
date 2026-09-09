"""MIR canónico mínimo para pruebas y trazas, no una VM de producción."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
import json
import operator
from typing import Any, Dict, List, Optional, Sequence

from piton.hir import HIRKind, HIRNode


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "params": list(self.params),
            "blocks": [block.to_dict() for block in self.blocks],
        }


@dataclass(slots=True)
class MIRModule:
    functions: List[MIRFunction] = field(default_factory=list)
    classes: dict = field(default_factory=dict)
    class_parents: dict = field(default_factory=dict)

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
        self.exception_handlers: list[tuple[str, str | None]] = []
        self.loop_counter = 0
        self.module_aliases: dict[str, str] = {}
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
        self.async_functions: set[str] = set()
        self.module_aliases: dict[str, str] = {}

    def lower(self, hir: HIRNode, modules: dict[str, HIRNode] | None = None, from_imports: dict | None = None) -> MIRModule:
        self.functions = []
        self.generators = {}
        self.classes = {}
        self.class_parents = {}
        self.async_functions = set()
        self.module_aliases = {}
        self.from_import_aliases = {}  # {local_name: qualified_name}
        imported_modules = modules or {}
        if hir.kind == HIRKind.MODULE:
            module_body = getattr(hir, "body", [])
            self.async_functions = {
                node.name for node in module_body
                if node.kind == HIRKind.FUNC_DEF and getattr(node, "is_async", False)
            }
            for node in module_body:
                if node.kind == HIRKind.IMPORT:
                    for alias in node.names:
                        if alias.name not in imported_modules and alias.name not in {"asyncio", "math"}:
                            raise MIRLoweringError(f"native module not supplied: {alias.name}")
                        self.module_aliases[alias.asname or alias.name] = alias.name
                elif node.kind == HIRKind.IMPORT_FROM:
                    mod_name = getattr(node, "module", None)
                    if mod_name:
                        if mod_name not in imported_modules and mod_name not in {"asyncio", "math"}:
                            raise MIRLoweringError(f"native from-import module not supplied: {mod_name}")
                        for alias in node.names:
                            local = alias.asname or alias.name
                            if mod_name in {"asyncio", "math"}:
                                self.from_import_aliases[local] = f"{mod_name}.{alias.name}"
                            else:
                                self.from_import_aliases[local] = f"{mod_name}__{alias.name}"
            for module_name, imported in sorted(imported_modules.items()):
                for item in imported.body:
                    if item.kind != HIRKind.FUNC_DEF:
                        raise MIRLoweringError("native imported modules currently support functions only")
                    self._lower_function(item, f"{module_name}__{item.name}")
            for node in module_body:
                if node.kind != HIRKind.CLASS_DEF:
                    continue
                if node.keywords or node.decorators or any(item.kind != HIRKind.FUNC_DEF for item in node.body):
                    raise MIRLoweringError("native classes currently require methods only, no keywords or decorators")
                if len(node.bases) > 1:
                    raise MIRLoweringError("native classes support at most one base class")
                parent_name = node.bases[0].name if node.bases else None
                if parent_name and parent_name not in self.classes:
                    raise MIRLoweringError(f"native base class '{parent_name}' must be defined before '{node.name}'")
                self.classes[node.name] = {method.name for method in node.body}
                self.class_parents[node.name] = parent_name
                for method in node.body:
                    self._lower_function(method, f"{node.name}__{method.name}")
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
            self._lower_statements(module, [node for node in module_body if node.kind != HIRKind.FUNC_DEF])
            self.functions.insert(0, module.function)
        else:
            builder = _Builder("<module>")
            self._lower_statement(builder, hir)
            self.functions.append(builder.function)
        module = MIRModule(self.functions)
        module.classes = dict(self.classes)
        module.class_parents = dict(self.class_parents)
        return module

    def _lower_function(
        self, node: HIRNode, qualified_name: str | None = None,
        captures: tuple[str, ...] = (),
    ) -> None:
        args = getattr(node, "args", None)
        params = list(getattr(args, "args", []) or []) if args else []
        body = list(getattr(node, "body", []))
        builder = _Builder(qualified_name or node.name, [*captures, *params])
        builder.is_async = bool(getattr(node, "is_async", False))
        builder.module_aliases = dict(self.module_aliases)
        local_names = set(params) | self._assigned_names(body)
        for nested in (item for item in body if item.kind == HIRKind.FUNC_DEF):
            nested_args = getattr(nested, "args", None)
            nested_params = set(getattr(nested_args, "args", []) or []) if nested_args else set()
            nested_locals = nested_params | self._assigned_names(nested.body)
            if self._contains_kind(nested.body, {HIRKind.NONLOCAL, HIRKind.GLOBAL}):
                raise MIRLoweringError("native closures do not support nonlocal or global declarations yet")
            free_loads = self._loaded_names(nested.body) - nested_locals
            nested_captures = tuple(name for name in sorted(free_loads) if name in local_names)
            lifted_name = f"{builder.function.name}__{nested.name}"
            builder.closures[nested.name] = (lifted_name, nested_captures)
            self._lower_function(nested, lifted_name, nested_captures)
        self._lower_statements(builder, [item for item in body if item.kind != HIRKind.FUNC_DEF])
        if not builder.current.instructions or builder.current.instructions[-1].op not in {"return", "jump", "branch"}:
            builder.emit("return", None)
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
            if node.value and node.value.kind == HIRKind.LOAD and node.value.name in builder.closures:
                raise MIRLoweringError("native closures cannot escape their defining function yet")
            builder.emit("return", self._lower_expr(builder, node.value) if node.value else None)
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
            builder.emit("catch_clear")
            self._lower_statements(builder, handler.body)
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
        if node.exc is None or node.cause is not None:
            raise MIRLoweringError("native raise requires an explicit exception without cause")
        if node.exc.kind != HIRKind.CALL or node.exc.func.kind != HIRKind.LOAD:
            raise MIRLoweringError("native raise requires a builtin exception constructor")
        exception_type = node.exc.func.name
        supported = {"Exception", "ValueError", "TypeError", "RuntimeError"}
        if exception_type not in supported or len(node.exc.args) > 1 or node.exc.keywords:
            raise MIRLoweringError("unsupported native exception constructor")
        payload = self._lower_expr(builder, node.exc.args[0]) if node.exc.args else None
        # Find the active handler block for the catch_flag check
        handler_label = None
        for label, accepted in reversed(builder.exception_handlers):
            if accepted is None or accepted == "Exception" or accepted == exception_type:
                handler_label = label
                break
        builder.emit("raise_typed", exception_type, payload, handler_label)

    def _store(self, builder: _Builder, target: HIRNode, value: Any) -> None:
        if target.kind == HIRKind.STORE:
            builder.emit("store", target.name, value)
        elif target.kind == HIRKind.ATTR and target.value.kind in {HIRKind.LOAD, HIRKind.STORE}:
            owner = self._lower_expr(builder, target.value)
            builder.emit("set_attr", owner, target.attr, value)
        else:
            builder.emit("runtime_call", "set_target", target.kind.name, value)

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
                raise MIRLoweringError("native closure values are only supported in direct calls")
            resolved = self.from_import_aliases.get(node.name, node.name)
            result = builder.temp()
            builder.emit("load", resolved, result=result)
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
            if node.func.kind == HIRKind.LOAD and node.func.name in self.generators:
                raise MIRLoweringError("native generator values cannot escape a direct for loop yet")
            if node.func.kind == HIRKind.LOAD and node.func.name in self.async_functions and not builder.awaiting:
                raise MIRLoweringError("native coroutine must be awaited or passed directly to asyncio.run")
            # Handle from-imported builtins (e.g. `desde math importar sqrt; sqrt(16)`)
            if node.func.kind == HIRKind.LOAD and node.func.name in self.from_import_aliases:
                qualified = self.from_import_aliases[node.func.name]
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
                # Check if class or any parent has __init__
                has_init = "__init__" in self.classes[node.func.name]
                check_class = node.func.name
                while not has_init and check_class:
                    check_class = self.class_parents.get(check_class)
                    if check_class and check_class in self.classes:
                        has_init = "__init__" in self.classes[check_class]
                init_class = node.func.name
                if not has_init:
                    # Walk up to find which class defines __init__
                    c = node.func.name
                    while c:
                        parent = self.class_parents.get(c)
                        if parent and parent in self.classes and "__init__" in self.classes[parent]:
                            init_class = parent
                            has_init = True
                            break
                        c = parent
                if has_init:
                    args = (result, *(self._lower_expr(builder, arg) for arg in node.args))
                    ignored = builder.temp()
                    builder.emit("method_call", init_class, "__init__", result, args[1:], result=ignored)
                elif node.args:
                    raise MIRLoweringError("native class without __init__ takes no arguments")
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
                builder.emit("load", f"{module_name}__{node.func.attr}", result=function)
                args = tuple(self._lower_expr(builder, arg) for arg in node.args)
                result = builder.temp()
                builder.emit("call", function, args, result=result)
                return result
            if node.func.kind == HIRKind.ATTR:
                if node.keywords:
                    raise MIRLoweringError("native method calls do not support keyword arguments yet")
                owner = self._lower_expr(builder, node.func.value)
                args = tuple(self._lower_expr(builder, arg) for arg in node.args)
                result = builder.temp()
                builder.emit("method_call", None, node.func.attr, owner, args, result=result)
                return result
            closure = builder.closures.get(node.func.name) if node.func.kind == HIRKind.LOAD else None
            if closure:
                lifted_name, capture_names = closure
                function = builder.temp()
                builder.emit("load", lifted_name, result=function)
                captures = []
                for name in capture_names:
                    capture = builder.temp()
                    builder.emit("load", name, result=capture)
                    captures.append(capture)
                args = (*captures, *(self._lower_expr(builder, arg) for arg in node.args))
            else:
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


def lower_hir_to_mir(hir: HIRNode, modules: dict[str, HIRNode] | None = None, from_imports: dict | None = None) -> MIRModule:
    return MIRLowerer().lower(hir, modules, from_imports=from_imports)


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
