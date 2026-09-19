"""MIR canónico mínimo para pruebas y trazas, no una VM de producción."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
import json
import operator
from typing import Any, Dict, List, Optional, Sequence

from piton.hir import HIRKind, HIRNode, Keyword, With


_BUILTIN_EXCEPTIONS = {"Exception", "BaseException", "ValueError", "TypeError", "RuntimeError", "StopIteration"}

# TASK_SCHEDULER_V1 magic values, mirrored with native_runtime.c / linux_x86.py.
PITON_TASK_MAGIC = 0x5049544E54414B4B
PITON_GATHER_MAGIC = 0x5049544E47415448
PITON_SLEEP0_MAGIC = 0x5049544E53503030


class MIRLoweringError(RuntimeError):
    pass


class _PyGenerator:
    """Python-side lazy generator simulation for the MIR interpreter.

    Collects yields lazily by running the generator function body as a Python
    generator. Each call to __next__ executes until the next yield.
    """

    def __init__(self, gen_func, args=(), kwargs=None):
        self._gen = gen_func(*args, **(kwargs or {}))
        self._exhausted = False

    def __next__(self):
        if self._exhausted:
            raise StopIteration
        try:
            return next(self._gen)
        except StopIteration:
            self._exhausted = True
            raise


@dataclass(frozen=True, slots=True)
class MIRInstruction:
    op: str
    args: tuple[Any, ...] = ()
    result: Optional[str] = None
    # ME — effect lattice (gates EFFECT_CLASSIFICATION_V1 / EFFECT_TOKEN_CHAIN_V1).
    # `effects` holds exactly one class from MIR_OP_EFFECTS once the lowering pass
    # has classified the instruction; it stays empty for instructions created
    # outside the lowering pipeline (e.g. optimizer rebuilds or hand-made MIR),
    # which is precisely what EFFECT_CLASSIFICATION_V1 rejects at verify time.
    # `token`/`token_prev` form the effect-token chain in block-list order for
    # every non-PURE instruction of a function (None for PURE instructions).
    effects: tuple[str, ...] = ()
    token: Optional[int] = None
    token_prev: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "args": list(self.args),
            "result": self.result,
            "effects": list(self.effects),
            "token": self.token,
            "token_prev": self.token_prev,
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
    frame_abi: bool = False
    is_generator: bool = False
    is_coroutine: bool = False
    is_async_generator: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "params": list(self.params),
            "defaults": list(self.defaults),
            "vararg": self.vararg,
            "kwarg": self.kwarg,
            "cell_vars": list(self.cell_vars),
            "frame_abi": self.frame_abi,
            "is_generator": self.is_generator,
            "is_coroutine": self.is_coroutine,
            "is_async_generator": self.is_async_generator,
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


# ---------------------------------------------------------------------------
# ME — Effect lattice (EFFECT_CLASSIFICATION_V1 / EFFECT_TOKEN_CHAIN_V1)
#
# Every MIR op is classified into exactly one effect class. The lattice is a
# pure annotator: it never changes what a backend emits, it only records what
# each op may do to *observable* state, so a future dataflow scheduler knows
# which nodes float and which are chained.
#
#   PURE   — result depends only on explicit args; no observable interaction.
#            Local lexical slots are treated as dataflow values here (their
#            ordering constraint is a data edge, not an effect). Controlled
#            traps (e.g. integer division by zero) become control edges, not
#            side effects.
#   READ   — reads shared state: closure cells, collections, exception channel.
#   WRITE  — mutates shared state or the exception/handler channels.
#   IO     — observable outside the process (scheduler wait, console).
#   OPAQUE — may execute arbitrary user code or unknown runtime machinery;
#            never reorderable across anything.
#
# Any op missing from this table fails EFFECT_CLASSIFICATION_V1 closed: adding
# a new op without classifying it breaks lowering loudly instead of silently
# shipping a node that could be mis-scheduled by the dataflow pass.

MIR_EFFECT_CLASSES = frozenset({"PURE", "READ", "WRITE", "IO", "OPAQUE"})

MIR_OP_EFFECTS: dict[str, str] = {
    # PURE
    "const": "PURE", "load": "PURE", "store": "PURE",
    "jump": "PURE", "branch": "PURE", "return": "PURE",
    "binary": "PURE", "unary": "PURE", "compare": "PURE",
    "math_sqrt": "PURE",
    "math_floor": "PURE", "math_ceil": "PURE", "math_trunc": "PURE",
    "math_fabs": "PURE", "math_gcd": "PURE",
    "object_new": "PURE", "closure_new": "PURE", "cell_new": "PURE",
    "build_collection": "PURE", "genexpr_new": "PURE", "gen_init": "PURE",
    "gather_new": "PURE",
    # READ
    "cell_load": "READ", "get_item": "READ", "collection_len": "READ",
    "catch_type": "READ", "catch_message": "READ", "catch_flag": "READ",
    # WRITE
    "cell_store": "WRITE", "dict_put": "WRITE", "list_append": "WRITE",
    "set_add": "WRITE",
    "try_push": "WRITE", "try_pop": "WRITE",
    "catch_bind": "WRITE", "catch_clear": "WRITE", "reraise_save": "WRITE",
    "raise_typed": "WRITE", "raise_active": "WRITE", "raise_active_dynamic": "WRITE", "raise_chain": "WRITE",
    "task_new": "WRITE", "task_cancel": "WRITE", "gather_add": "WRITE",
    # IO
    "sleep0": "IO",
    # OPAQUE
    "call": "OPAQUE", "call_unpack": "OPAQUE", "method_call": "OPAQUE", "frame_call": "OPAQUE",
    "closure_call": "OPAQUE", "runtime_call": "OPAQUE",
    "get_attr": "OPAQUE", "set_attr": "OPAQUE", "del_attr": "OPAQUE",
    "iter_new": "OPAQUE", "builtin_iter_new": "OPAQUE", "iter_next": "OPAQUE",
    "gen_yield": "OPAQUE", "gen_send": "OPAQUE", "gen_throw": "OPAQUE",
    "gen_close": "OPAQUE", "gen_next": "OPAQUE", "gen_collect": "OPAQUE",
    "gen_retval": "READ",
    "agen_emit": "OPAQUE", "agen_next": "OPAQUE", "agen_done": "OPAQUE",
    "event_run": "OPAQUE", "coro_run": "OPAQUE",
}


def classify_instruction_effects(instruction: MIRInstruction) -> str:
    """Return the effect class of one instruction; fail closed on unknown ops."""
    klass = MIR_OP_EFFECTS.get(instruction.op)
    if klass is None:
        raise MIRLoweringError(
            f"EFFECT_CLASSIFICATION_V1: op '{instruction.op}' has no effect classification"
        )
    if klass not in MIR_EFFECT_CLASSES:
        raise MIRLoweringError(
            f"EFFECT_CLASSIFICATION_V1: op '{instruction.op}' has invalid class '{klass}'"
        )
    return klass


def annotate_module_effects(module: MIRModule) -> MIRModule:
    """Classify every instruction of every function (in place, via replace)."""
    for function in module.functions:
        for block in function.blocks:
            for index, instruction in enumerate(block.instructions):
                klass = classify_instruction_effects(instruction)
                block.instructions[index] = replace(instruction, effects=(klass,))
    return module


def chain_effect_tokens(module: MIRModule) -> MIRModule:
    """Thread the effect token through every non-PURE instruction of a function.

    The chain follows the deterministic block-list order of the function
    (emission order), so it is a linear order that never under-constrains any
    execution path. PURE instructions keep token=None. The chain is verifiable:
    verify_effect_chain(module) recomputes it independently and fails on
    tampering or on an unclassified instruction.
    """
    for function in module.functions:
        expected = 0
        for block in function.blocks:
            for index, instruction in enumerate(block.instructions):
                klass = instruction.effects[0] if instruction.effects else classify_instruction_effects(instruction)
                if klass == "PURE":
                    continue
                block.instructions[index] = replace(
                    instruction,
                    token=expected,
                    token_prev=expected - 1 if expected else None,
                )
                expected += 1
    return module


def verify_effect_chain(module: MIRModule) -> None:
    """Recompute and check the effect-token chain of every function.

    Raises MIRLoweringError if any instruction is unclassified, any PURE
    instruction carries a token, or any non-PURE instruction does not hold
    exactly the expected (token, token_prev) pair for its position in the
    chain. This is the EFFECT_TOKEN_CHAIN_V1 verifier.
    """
    for function in module.functions:
        expected = 0
        for block in function.blocks:
            for instruction in block.instructions:
                if not instruction.effects:
                    raise MIRLoweringError(
                        f"EFFECT_TOKEN_CHAIN_V1: op '{instruction.op}' in "
                        f"{function.name}:{block.label} is unclassified"
                    )
                if instruction.effects[0] == "PURE":
                    if instruction.token is not None or instruction.token_prev is not None:
                        raise MIRLoweringError(
                            f"EFFECT_TOKEN_CHAIN_V1: PURE op '{instruction.op}' in "
                            f"{function.name}:{block.label} carries a token"
                        )
                    continue
                want_prev = expected - 1 if expected else None
                if instruction.token != expected or instruction.token_prev != want_prev:
                    raise MIRLoweringError(
                        f"EFFECT_TOKEN_CHAIN_V1: chain broken at '{instruction.op}' in "
                        f"{function.name}:{block.label}: got token={instruction.token} "
                        f"token_prev={instruction.token_prev}, expected "
                        f"{expected}/{want_prev}"
                    )
                expected += 1


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
        self._loop_stack: list[tuple[str, str, str | None]] = []
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


def _active_handler(builder: "_Builder") -> str | None:
    """Handler label of the innermost active try-block, if any.

    M14: ``call`` ops embed it so builtins that raise (pow/ord/chr/round…)
    route to user ``intentar/excepto`` handlers on both backends instead of
    always aborting the process.
    """
    return builder.exception_handlers[-1][0] if builder.exception_handlers else None


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
        self.async_generators: set[str] = set()
        self.module_aliases: dict[str, str] = {}
        self.decorated_symbols: dict[int, str] = {}

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
        self.async_generators = set()
        self.module_aliases = {}
        self.decorated_symbols = {}
        self.from_import_aliases = {}  # {local_name: qualified_name}
        self._module_scope_stack: list[tuple[dict[str, str], dict[str, str]]] = []
        self.module_meta = module_meta or {}
        self.function_params: dict[str, list[str]] = {}
        self.function_defaults: dict[str, dict[str, Any]] = {}
        self.function_varargs: dict[str, str | None] = {}
        self.function_kwargs: dict[str, str | None] = {}
        self.function_posonly: dict[str, list[str]] = {}
        self.function_frame_abi = {}
        self.function_kwonly: dict[str, list[str]] = {}
        self.function_frame_abi: dict[str, bool] = {}
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
                    if item.kind not in {HIRKind.FUNC_DEF, HIRKind.IMPORT, HIRKind.IMPORT_FROM, HIRKind.PASS}:
                        raise MIRLoweringError("native imported modules currently support functions, imports, and pasar only")
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
                if node.kind == HIRKind.FUNC_DEF and any(
                    item.kind in {HIRKind.YIELD, HIRKind.YIELD_FROM}
                    for item in node.body
                ):
                    args = getattr(node, "args", None)
                    params = list(getattr(args, "args", []) or []) if args else []
                    if not params and node.body and all(
                        item.kind == HIRKind.YIELD and item.value is not None
                        for item in node.body
                    ):
                        self.generators[node.name] = tuple(item.value for item in node.body)
                    else:
                        self.generators[node.name] = True
            # ASYNC_GENERATOR_V1: an async function whose body contains produ
            # (yield) anywhere is an async generator, not a coroutine: calling it
            # creates an object (no "must be awaited"), and `asincrono para` drives
            # it. Nested yields are walked (unlike the sync-generator scan, which
            # only inspects top-level statements).
            self.async_generators = {
                node.name for node in module_body
                if node.kind == HIRKind.FUNC_DEF
                and getattr(node, "is_async", False)
                and self._contains_kind(node.body, {HIRKind.YIELD, HIRKind.YIELD_FROM})
            }
            for node in module_body:
                if node.kind == HIRKind.FUNC_DEF:
                    is_gen = node.name in self.generators
                    decorators = list(getattr(node, "decorators", None) or [])
                    if decorators:
                        if is_gen or getattr(node, "is_async", False):
                            raise MIRLoweringError("native decorators on generators/coroutines are not supported yet")
                        symbol = f"__decorated__{node.name}"
                        self.decorated_symbols[id(node)] = symbol
                        self._lower_function(node, symbol, is_generator=is_gen)
                    else:
                        self._lower_function(node, is_generator=is_gen)

            lambda_counter = 0
            module_builder = _Builder("<module>")
            module_builder.module_aliases = dict(self.module_aliases)
            module_builder.from_import_aliases = dict(self.from_import_aliases)
            module_available = self._assigned_names(module_body)
            for lambda_node in (item for item in self._walk(module_body) if item.kind == HIRKind.LAMBDA):
                if self._contains_kind(lambda_node.body, {HIRKind.GLOBAL}):
                    raise MIRLoweringError("native closures do not support global declarations yet")
                lambda_captures = tuple(name for name in sorted(self._nested_free_loads(lambda_node)) if name in module_available)
                lambda_name = f"<lambda_{lambda_counter}>"
                lifted_name = f"__module__lambda_{lambda_counter}"
                lambda_counter += 1
                lambda_args = getattr(lambda_node, "args", None)
                lambda_n_args = len(
                    list(getattr(lambda_args, "posonlyargs", []) or []) + list(getattr(lambda_args, "args", []) or [])
                )
                module_builder.closures[lambda_name] = (lifted_name, lambda_captures, lambda_n_args)
                self._lower_function(
                    lambda_node, lifted_name, lambda_captures,
                    cell_vars=list(lambda_captures), frame_abi=True,
                )

            module = module_builder
            module.module_aliases = dict(self.module_aliases)
            module.from_import_aliases = dict(self.from_import_aliases)
            self._lower_module_metadata(module, entry_file, module_meta)
            self._lower_statements(module, [
                node for node in module_body
                if node.kind != HIRKind.FUNC_DEF or getattr(node, "decorators", None)
            ])
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
        # ME — effect lattice: every emitted op is classified and chained at
        # lowering time, so a new op born without classification fails closed.
        annotate_module_effects(module)
        chain_effect_tokens(module)
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
                if level >= 1:
                    # M8 IMPORT_RELATIVE_V2: N levels supported. Drop (level-1)
                    # trailing segments of base so `desde .. importar x` walks up.
                    up = level - 1
                    if up > 0:
                        head = base.split(".")
                        if up > len(head) - 1:
                            raise MIRLoweringError(
                                f"relative import level {level} escapes the package at '{base}'"
                            )
                        base = ".".join(head[:-up])
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
            module_obj = module_object(name, file_path, package)
            entries.append((name, module_obj))
            # Keep imported module names available to native emitters. The
            # catalog above models sys.modules; this binding models `import a`.
            binding = name.split(".", 1)[0]
            if binding.isidentifier() and binding not in {item[0] for item in entries[:-1]}:
                emit_store(binding, module_obj)
        modules_dict = builder.temp()
        builder.emit("build_collection", "dict", tuple(entries), result=modules_dict)
        builder.emit("set_attr", sys_obj, "modules", modules_dict)
        builder.emit("store", "sys", sys_obj)

    def _lower_function(
        self, node: HIRNode, qualified_name: str | None = None,
        captures: tuple[str, ...] = (), cell_vars: list[str] | None = None,
        super_context: tuple[str, str] | None = None,
        frame_abi: bool = False, is_generator: bool = False,
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
        builder.function.frame_abi = frame_abi
        builder.function.is_generator = is_generator
        if super_context:
            builder.super_class, builder.super_self = super_context
            builder.function.self_class = super_context[0]
        builder.is_async = bool(getattr(node, "is_async", False))
        builder.function.is_coroutine = builder.is_async
        builder.function.is_async_generator = (qualified_name or node.name) in self.async_generators
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
        total_params = len(positional_params) + len(kwonly_params) + int(bool(vararg_name)) + int(bool(kwarg_name))
        use_frame_abi = frame_abi or total_params > 4
        if vararg_name:
            builder.function.vararg = vararg_name
            builder.function.params.append(vararg_name)
        builder.function.params.extend(kwonly_params)
        if kwarg_name:
            builder.function.kwarg = kwarg_name
            builder.function.params.append(kwarg_name)
        builder.function.frame_abi = use_frame_abi
        builder.function.defaults = [defaults_map.get(p) for p in builder.function.params]
        self.function_params[qualified_name or node.name] = list(builder.function.params)
        self.function_defaults[qualified_name or node.name] = defaults_map
        self.function_varargs[qualified_name or node.name] = vararg_name
        self.function_kwargs[qualified_name or node.name] = kwarg_name
        self.function_posonly[qualified_name or node.name] = posonly_params
        self.function_kwonly[qualified_name or node.name] = kwonly_params
        self.function_frame_abi[qualified_name or node.name] = use_frame_abi
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

        lambda_counter = 0
        for lambda_node in (item for item in self._walk(body) if item.kind == HIRKind.LAMBDA):
            if self._contains_kind(lambda_node.body, {HIRKind.GLOBAL}):
                raise MIRLoweringError("native closures do not support global declarations yet")
            lambda_captures = tuple(name for name in sorted(self._nested_free_loads(lambda_node)) if name in available)
            lambda_name = f"<lambda_{lambda_counter}>"
            lifted_name = f"{builder.function.name}__lambda_{lambda_counter}"
            lambda_counter += 1
            lambda_args = getattr(lambda_node, "args", None)
            lambda_n_args = len(
                list(getattr(lambda_args, "posonlyargs", []) or []) + list(getattr(lambda_args, "args", []) or [])
            )
            all_nested_captures[lambda_name] = (lifted_name, lambda_captures, lambda_n_args)
            builder.closures[lambda_name] = (lifted_name, lambda_captures, lambda_n_args)

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
            if getattr(nested, "decorators", None):
                raise MIRLoweringError("native decorators are only supported on module-level functions")
            nested_captures = tuple(name for name in sorted(self._nested_free_loads(nested)) if name in available)
            lifted_name = f"{builder.function.name}__{nested.name}"
            inner_cell_vars = [c for c in nested_captures if c in captured_var_names]
            self._lower_function(
                nested, lifted_name, nested_captures,
                cell_vars=inner_cell_vars, frame_abi=True,
            )

        lambda_counter = 0
        for lambda_node in (item for item in self._walk(body) if item.kind == HIRKind.LAMBDA):
            lambda_captures = tuple(name for name in sorted(self._nested_free_loads(lambda_node)) if name in available)
            lambda_name = f"<lambda_{lambda_counter}>"
            lifted_name = f"{builder.function.name}__lambda_{lambda_counter}"
            lambda_counter += 1
            inner_cell_vars = [c for c in lambda_captures if c in captured_var_names]
            self._lower_function(
                lambda_node, lifted_name, lambda_captures,
                cell_vars=inner_cell_vars, frame_abi=True,
            )

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
        if kind == HIRKind.FUNC_DEF:
            decorators = list(getattr(node, "decorators", None) or [])
            symbol = self.decorated_symbols.get(id(node))
            if not decorators or not symbol:
                return
            current = builder.temp()
            builder.emit("load", symbol, result=current)
            for decorator in reversed(decorators):
                if decorator.kind != HIRKind.LOAD:
                    raise MIRLoweringError("native decorators must be plain names")
                decorator_value = builder.temp()
                builder.emit("load", decorator.name, result=decorator_value)
                applied = builder.temp()
                builder.emit("call", decorator_value, (current,), _active_handler(builder), result=applied)
                current = applied
            builder.emit("store", node.name, current)
        elif kind == HIRKind.ASSIGN:
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
        elif kind == HIRKind.BREAK:
            if not builder._loop_stack:
                raise MIRLoweringError("'romper' outside loop")
            break_label, _, flag_name = builder._loop_stack[-1]
            if flag_name:
                one = builder.temp()
                builder.emit("const", 1, result=one)
                builder.emit("store", flag_name, one)
            builder.emit("jump", break_label)
        elif kind == HIRKind.CONTINUE:
            if not builder._loop_stack:
                raise MIRLoweringError("'continuar' outside loop")
            _, continue_label, _ = builder._loop_stack[-1]
            builder.emit("jump", continue_label)
        elif kind == HIRKind.YIELD:
            val = self._lower_expr(builder, node.value) if node.value else None
            if getattr(builder.function, "is_async_generator", False):
                # ASYNC_GENERATOR_V1: produ (data yield) inside an async
                # generator — same suspension as gen_yield but the backend marks
                # it as data (await marker 0) so piton_agen_next returns it.
                builder.emit("agen_emit", val, result=val)
            else:
                builder.emit("gen_yield", val, result=val)
            after_block = builder.new_block()
            builder.emit("jump", after_block.label)
            builder.current = after_block
            return
        elif kind == HIRKind.YIELD_FROM:
            # YIELD_FROM_V1: delegation to a generator object. send(None) ==
            # next() inside the sub, and the sub return value is captured from
            # the generator slot into the enclosing statement context (the MIR
            # statement form discards it — assignment form is V2).
            # Scope deliberately bounded: the source must be a call to a known
            # generator function (so it lowers to gen_init) or a name bound to
            # a generator value. Anything else is fail-closed.
            if not builder.is_async and not getattr(builder.function, "is_generator", False):
                raise MIRLoweringError("'producir desde' is only valid inside a generator")
            source_node = node.value
            is_gen_call = (
                source_node.kind == HIRKind.CALL
                and source_node.func.kind == HIRKind.LOAD
                and source_node.func.name in self.generators
            )
            if not is_gen_call:
                raise MIRLoweringError(
                    "native yield from over non-generator values (lists, dicts) is not supported yet; use producir desde gen(...) with a generator"
                )
            sub = self._lower_expr(builder, node.value)
            done = builder.new_block()
            loop = builder.new_block()
            v0 = builder.temp()
            builder.emit("try_push")
            builder.emit("iter_next", sub, done.label, result=v0)
            builder.emit("jump", loop.label)
            builder.current = loop
            sent = builder.temp()
            builder.emit("gen_yield", v0, result=sent)
            nxt = builder.temp()
            builder.emit("gen_send", sub, sent, done.label, result=nxt)
            builder.emit("store", v0, nxt)
            builder.emit("jump", loop.label)
            builder.current = done
            builder.emit("catch_clear")
            builder.emit("try_pop")
            retv = builder.temp()
            builder.emit("gen_retval", sub, result=retv)
            return
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
        elif kind == HIRKind.WITH:
            self._lower_with(builder, node)
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
        if not builder.current.instructions or builder.current.instructions[-1].op not in {"jump", "branch", "return"}:
            builder.emit("jump", end_block.label)
        builder.current = else_block
        if len(node.orelse) == 1 and node.orelse[0].kind == HIRKind.IF:
            self._lower_if(builder, node.orelse[0])
        else:
            self._lower_statements(builder, node.orelse)
            if not builder.current.instructions or builder.current.instructions[-1].op not in {"jump", "branch", "return"}:
                builder.emit("jump", end_block.label)
        builder.current = end_block

    def _lower_while(self, builder: _Builder, node: HIRNode) -> None:
        condition_block = builder.new_block()
        body_block = builder.new_block()
        end_block = builder.new_block()
        has_else = bool(node.orelse)
        flag_name = f"@while_else_{builder.loop_counter}" if has_else else None
        builder.loop_counter += 1
        if has_else:
            zero = builder.temp()
            builder.emit("const", 0, result=zero)
            builder.emit("store", flag_name, zero)
        builder._loop_stack.append((end_block.label, condition_block.label, flag_name))
        builder.emit("jump", condition_block.label)
        builder.current = condition_block
        condition = self._lower_expr(builder, node.test)
        builder.emit("branch", condition, body_block.label, end_block.label)
        builder.current = body_block
        self._lower_statements(builder, node.body)
        builder.emit("jump", condition_block.label)
        builder.current = end_block
        builder._loop_stack.pop()
        if has_else:
            flag_val = builder.temp()
            builder.emit("load", flag_name, result=flag_val)
            zero = builder.temp()
            builder.emit("const", 0, result=zero)
            else_flag = self._compare(builder, "==", flag_val, zero)
            else_block = builder.new_block()
            after_else = builder.new_block()
            builder.emit("branch", else_flag, else_block.label, after_else.label)
            builder.current = else_block
            self._lower_statements(builder, node.orelse)
            builder.emit("jump", after_else.label)
            builder.current = after_else

    def _lower_for(self, builder: _Builder, node: HIRNode) -> None:
        if node.is_async:
            if not builder.is_async:
                raise MIRLoweringError("'asincrono para' is only valid inside a native async function")
            self._lower_async_for(builder, node)
            return
        if node.iter.kind in {HIRKind.LIST,HIRKind.TUPLE}:
            generator = self._lower_expr(builder, node.iter)
        elif (
            node.iter.kind == HIRKind.CALL
            and node.iter.func.kind == HIRKind.LOAD
            and node.iter.func.name in self.generators
        ):
            gen_val = self.generators[node.iter.func.name]
            if isinstance(gen_val, tuple):
                values = tuple(self._lower_expr(builder, value) for value in gen_val)
                generator = builder.temp()
                builder.emit("build_collection", "tuple", values, result=generator)
            else:
                raise MIRLoweringError("native generators with parameters in for-loops require next() or iter()")
        else:
            raise MIRLoweringError("native for currently supports list/tuple or a generator call")
        index_name = f"@for_index_{builder.loop_counter}"
        builder.loop_counter += 1
        zero = builder.temp()
        builder.emit("const", 0, result=zero)
        builder.emit("store", index_name, zero)
        condition_block = builder.new_block()
        body_block = builder.new_block()
        increment_block = builder.new_block()
        end_block = builder.new_block()
        has_else = bool(node.orelse)
        flag_name = f"@for_else_{builder.loop_counter}" if has_else else None
        builder.loop_counter += 1
        if has_else:
            zero_f = builder.temp()
            builder.emit("const", 0, result=zero_f)
            builder.emit("store", flag_name, zero_f)
        builder._loop_stack.append((end_block.label, increment_block.label, flag_name))
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
        builder.emit("jump", increment_block.label)
        builder.current = increment_block
        current = builder.temp()
        one = builder.temp()
        builder.emit("load", index_name, result=current)
        builder.emit("const", 1, result=one)
        following = self._binary(builder, "+", current, one)
        builder.emit("store", index_name, following)
        builder.emit("jump", condition_block.label)
        builder.current = end_block
        builder._loop_stack.pop()
        if has_else:
            flag_val = builder.temp()
            builder.emit("load", flag_name, result=flag_val)
            zero_f2 = builder.temp()
            builder.emit("const", 0, result=zero_f2)
            else_flag = self._compare(builder, "==", flag_val, zero_f2)
            else_block = builder.new_block()
            after_else = builder.new_block()
            builder.emit("branch", else_flag, else_block.label, after_else.label)
            builder.current = else_block
            self._lower_statements(builder, node.orelse)
            builder.emit("jump", after_else.label)
            builder.current = after_else

    def _lower_async_for(self, builder: _Builder, node: HIRNode) -> None:
        """Lower ``asincrono para x en <async-gen-call>: body`` (plus orelse).

        ASYNC_FOR_V1: the iterable must be a direct async-generator call. It
        becomes a gen_init object, then the loop drives it with agen_next
        (returns the next data yield) + agen_done (1 when StopAsyncIteration).
        The driver itself handles awaits inside the async generator body.
        """
        if not (
            node.iter.kind == HIRKind.CALL
            and node.iter.func.kind == HIRKind.LOAD
            and node.iter.func.name in self.async_generators
        ):
            raise MIRLoweringError("native async for currently requires an async generator call")
        func_name = node.iter.func.name
        arg_vals = tuple(self._lower_expr(builder, a) for a in node.iter.args)
        agen = builder.temp()
        builder.emit("gen_init", func_name, arg_vals, result=agen)
        has_else = bool(node.orelse)
        flag_name = f"@for_else_{builder.loop_counter}" if has_else else None
        builder.loop_counter += 1
        condition_block = builder.new_block()
        body_block = builder.new_block()
        end_block = builder.new_block()
        if has_else:
            zero_f = builder.temp()
            builder.emit("const", 0, result=zero_f)
            builder.emit("store", flag_name, zero_f)
        builder._loop_stack.append((end_block.label, condition_block.label, flag_name))
        builder.emit("jump", condition_block.label)
        builder.current = condition_block
        value = builder.temp()
        builder.emit("agen_next", agen, result=value)
        done = builder.temp()
        builder.emit("agen_done", agen, result=done)
        builder.emit("branch", done, end_block.label, body_block.label)
        builder.current = body_block
        if node.target.kind not in {HIRKind.LOAD, HIRKind.STORE}:
            raise MIRLoweringError("native async generator loop target must be a name")
        builder.emit("store", node.target.name, value)
        self._lower_statements(builder, node.body)
        builder.emit("jump", condition_block.label)
        builder.current = end_block
        builder._loop_stack.pop()
        if has_else:
            flag_val = builder.temp()
            builder.emit("load", flag_name, result=flag_val)
            zero_f2 = builder.temp()
            builder.emit("const", 0, result=zero_f2)
            else_flag = self._compare(builder, "==", flag_val, zero_f2)
            else_block = builder.new_block()
            after_else = builder.new_block()
            builder.emit("branch", else_flag, else_block.label, after_else.label)
            builder.current = else_block
            self._lower_statements(builder, node.orelse)
            builder.emit("jump", after_else.label)
            builder.current = after_else

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
            if not hasattr(handler, "body") or handler.is_star:
                raise MIRLoweringError("native except does not support except* yet")
            handler_block = builder.new_block()
            handler_bind_name = handler.name
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
            # EXCEPTION_BINDING_V1: bind the caught exception message BEFORE
            # catch_clear wipes it.
            bind_name = handler_bind_name
            if bind_name:
                bound = builder.temp()
                builder.emit("catch_bind", bind_name, result=bound)
                builder.emit("store", bind_name, bound)
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

    def _with_mro_method(
        self, class_name: str, method: str
    ) -> str | None:
        """First MRO class (self.classes only) that defines ``method``."""
        mro = self._mro_memo.get(class_name) or self._compute_mro(class_name)
        for cls in (mro or ()):
            if cls in self.classes and method in self.classes[cls]:
                return cls
        return None

    def _innermost_static_accepted(self, builder: _Builder) -> str | None:
        """Static accepted type of the innermost enclosing except handler.

        Mirrors the bare re-raise constraint: catch-all handlers fail closed
        (no statically-known type to re-raise with). Returns None when no
        enclosing handler exists — the with-body exception becomes an
        unhandled exit there, which is runtime-correct.
        """
        for _, accepted in reversed(builder.exception_handlers):
            if accepted is None or accepted == "Exception":
                raise MIRLoweringError(
                    "native with-body exception propagation inside a catch-all "
                    "(excepto Exception / bare excepto) region is not supported yet"
                )
            return accepted
        return None

    def _lower_with(self, builder: _Builder, node: HIRNode) -> None:
        """WITH_PROTOCOL_V1: single-item ``con CM(...) como x:`` lowerer.

        V1 subset and divergences (all deliberate, all documented):
        - one context manager, non-async, as-target must be a simple name;
        - context must be a direct call to a native class constructor;
        - ``__enter__``/``__exit__`` resolved through the MRO, fail closed if
          missing on the chain;
        - ``__exit__`` receives ``(type_name_str, message_str, None)`` instead
          of the exception object + traceback (V1 divergence, CPython passes
          the sub-exception of ``with_traceback``);
        - suppression: truthy ``__exit__`` result clears the exception;
        - re-raise: static label of the innermost enclosing statically-typed
          handler (catch-all enclosing regions fail closed), None → unhandled
          exit; the propagated type is the RUNTIME type (dynamic re-raise);
        - ``devolver``/``romper``/``continuar`` inside the body skip
          ``__exit__`` (same flag-unwind limitation as try/finally on return).
        """
        is_async_with = bool(getattr(node, "is_async", False))
        if len(node.items) != 1:
            # WITH_MULTIPLE_V1: multiple managers lower as nested withs —
            # inner __exit__ runs first, matching CPython.
            if len(node.items) == 0:
                raise MIRLoweringError("with requires at least one context manager")
            first = node.items[0]
            rest = list(node.items[1:])
            first_node = With(
                items=[first],
                body=[With(items=rest, body=node.body, is_async=node.is_async)],
                is_async=node.is_async,
            )
            self._lower_with(builder, first_node)
            return
        item = node.items[0]
        expr = item.context_expr
        if (
            expr.kind != HIRKind.CALL
            or expr.func.kind != HIRKind.LOAD
            or expr.func.name not in self.classes
        ):
            raise MIRLoweringError(
                "native with context must be a direct call to a native class constructor"
            )
        class_name = expr.func.name
        enter_name = "__aenter__" if is_async_with else "__enter__"
        exit_name = "__aexit__" if is_async_with else "__exit__"
        enter_cls = self._with_mro_method(class_name, enter_name)
        exit_cls = self._with_mro_method(class_name, exit_name)
        if enter_cls is None or exit_cls is None:
            raise MIRLoweringError(
                f"native class '{class_name}' used in `con` must define {enter_name} and {exit_name}"
            )
        var = item.optional_vars
        if var is not None and var.kind != HIRKind.LOAD:
            raise MIRLoweringError("native with as-target must be a simple name")

        def await_context_method(method_class: str, method_name: str, args: tuple[str, ...]) -> str:
            if not is_async_with:
                value = builder.temp()
                builder.emit("method_call", method_class, method_name, ctx, args, result=value)
                return value
            # ASYNC_WITH_V1: class async methods are lowered as coroutine
            # state machines.  The receiver is the first ABI argument.
            coroutine = builder.temp()
            builder.emit("gen_init", f"{method_class}__{method_name}", (ctx, *args), result=coroutine)
            value = builder.temp()
            builder.emit("gen_yield", coroutine, result=value)
            return value

        # __enter__()/__aenter__() → bind
        ctx = self._lower_expr(builder, expr)
        entered = await_context_method(enter_cls, enter_name, ())
        if var is not None:
            builder.emit("store", var.name, entered)

        end_block = builder.new_block()
        body_block = builder.new_block()
        handler_block = builder.new_block()

        # body: inside a synthetic try that catches everything (accepted=None),
        # so ANY body raise is routed through the handler (cleanup runs).
        builder.emit("try_push")
        builder.emit("jump", body_block.label)
        builder.current = body_block
        builder.exception_handlers.append((handler_block.label, None))
        self._lower_statements(builder, node.body)
        builder.exception_handlers.pop()
        builder.emit("try_pop")

        # normal path: __exit__/__aexit__(None, None, None)
        none_val = builder.temp()
        builder.emit("const", None, result=none_val)
        ignored = await_context_method(exit_cls, exit_name, (none_val, none_val, none_val))
        builder.emit("jump", end_block.label)

        # exception path: __exit__(type_str, message_str, None)
        builder.current = handler_block
        builder.emit("reraise_save")
        exc_type = builder.temp()
        builder.emit("catch_type", result=exc_type)
        exc_msg = builder.temp()
        builder.emit("catch_message", result=exc_msg)
        none2 = builder.temp()
        builder.emit("const", None, result=none2)
        exit_rc = await_context_method(exit_cls, exit_name, (exc_type, exc_msg, none2))
        suppressed_block = builder.new_block()
        propagate_block = builder.new_block()
        builder.emit("branch", exit_rc, suppressed_block.label, propagate_block.label)

        # suppressed: truthy __exit__ → swallow the exception
        builder.current = suppressed_block
        builder.emit("catch_clear")
        builder.emit("try_pop")
        builder.emit("jump", end_block.label)

        # propagate: falsy __exit__ → re-raise the RUNTIME exception
        builder.current = propagate_block
        builder.emit("try_pop")
        # RERAISE_COMPLETE_V1: raise_active_dynamic accepts ANY enclosing
        # handler (including catch-alls and nested withs), because the route
        # only needs a label, not a static type. None → unhandled exit.
        handler_label = builder.exception_handlers[-1][0] if builder.exception_handlers else None
        builder.emit("raise_active_dynamic", handler_label)

        builder.current = end_block

    def _lower_raise(self, builder: _Builder, node: HIRNode) -> None:
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
        if node.cause is not None:
            # EXCEPTION_CHAINING_V1: lanzar A(...) desde B(...) records the
            # cause on the exception record; `desde Nada` suppresses it.
            cause_type = None
            cause_payload = None
            if node.cause.kind == HIRKind.CONST and node.cause.value is None:
                pass
            elif node.cause.kind == HIRKind.CALL and node.cause.func.kind == HIRKind.LOAD:
                cause_type = node.cause.func.name
                if cause_type not in _BUILTIN_EXCEPTIONS and cause_type not in self.classes:
                    raise MIRLoweringError(
                        f"native raise-cause requires a builtin or Exception subclass, got '{cause_type}'"
                    )
                if len(node.cause.args) > 1 or node.cause.keywords:
                    raise MIRLoweringError("native raise-cause constructor is limited to one positional argument")
                cause_payload = self._lower_expr(builder, node.cause.args[0]) if node.cause.args else None
            else:
                raise MIRLoweringError("native raise supports `desde` only with an exception constructor or Nada")
        if node.cause is not None and cause_type is not None:
            builder.emit("raise_chain", exception_type, payload, cause_type, cause_payload, handler_label)
        else:
            builder.emit("raise_typed", exception_type, payload, handler_label)

    def _lower_reraise(self, builder: _Builder) -> None:
        if not builder.in_except_handler:
            raise MIRLoweringError("native bare re-raise requires an enclosing except handler")
        # RERAISE_COMPLETE_V1: bare lanzar from a catch-all re-raises with the
        # runtime type kept in the reraise slots — mirrors the with-body rule:
        # route one frame out, unhandled exit if there is none.
        if builder.reraise_type in (None, "Exception", "BaseException"):
            # exception_handlers already popped the current handler before its
            # body is lowered, so [-1] IS the enclosing one.
            outer = builder.exception_handlers[-1][0] if builder.exception_handlers else None
            builder.emit("raise_active_dynamic", outer)
            return
        handler_label = self._find_exception_handler(builder, builder.reraise_type)
        builder.emit("raise_active", builder.reraise_type, handler_label)

    def _exception_chain(self, name: str) -> list[str]:
        # BASE_EXCEPTION_V1: the chain terminates at BaseException; every
        # builtin root and user Exception subclass eventually reaches it.
        chain: list[str] = []
        current: str | None = name
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            chain.append(current)
            if current == "BaseException":
                break
            if current in _BUILTIN_EXCEPTIONS:
                current = "Exception" if current != "Exception" else "BaseException"
            else:
                mro = self._mro_memo.get(current) or self._compute_mro(current)
                if mro and len(mro) > 1 and mro[1] not in seen:
                    current = mro[1]
                else:
                    current = self.class_parents.get(current)
        if chain and chain[-1] != "BaseException":
            chain.append("BaseException")
        return chain

    def _find_exception_handler(
        self, builder: _Builder, exception_type: str
    ) -> str | None:
        chain = self._exception_chain(exception_type)
        for label, accepted in reversed(builder.exception_handlers):
            if accepted is None or accepted in {"Exception", "BaseException"} or accepted in chain:
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
                # VARIADIC_CLOSURE_V1: variadic lifted functions mark the
                # closure so the dispatcher packs extras into a tuple.
                has_vararg = 1 if self.function_varargs.get(lifted_name) else 0
                builder.emit("closure_new", lifted_name, n_args, tuple(capture_ops), has_vararg, result=result)
                return result
            if node.name in builder.cell_params:
                result = builder.temp()
                builder.emit("cell_load", node.name, result=result)
                return result
            # M8: aliases de módulo creados por `desde .. importar X` dentro
            # de un paquete son marcadores estáticos. Si el nombre del alias
            # resuelve a un módulo importado, emitimos un placeholder (None) en
            # vez de un load que no existe en el backend.
            alias_target = builder.module_aliases.get(node.name)
            if alias_target and alias_target != node.name and alias_target in self.imported_modules:
                result = builder.temp()
                builder.emit("const", None, result=result)
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
        if kind == HIRKind.LAMBDA:
            if node.name not in builder.closures:
                raise MIRLoweringError(f"lambda '{node.name}' not lifted")
            lifted_name, _, n_args = builder.closures[node.name]
            capture_ops: list[Any] = []
            for capture_name in builder.closures[node.name][1]:
                capture = builder.temp()
                builder.emit("load", capture_name, result=capture)
                cell = builder.temp()
                builder.emit("cell_new", capture, result=cell)
                capture_ops.append(cell)
            result = builder.temp()
            builder.emit("closure_new", lifted_name, n_args, tuple(capture_ops), 0, result=result)
            return result
        if kind == HIRKind.CALL:
            if self._call_has_dynamic_unpack(node):
                return self._lower_call_unpack(builder, node)
            node.args, node.keywords = self._expand_literal_call(node)
            if node.func.kind == HIRKind.LOAD and node.func.name == "super":
                raise MIRLoweringError(
                    "native super() must appear as super().method(...) directly inside a "
                    "class method call"
                )
            if node.func.kind == HIRKind.LOAD and node.func.name in {"iter", "iterar"}:
                if len(node.args) not in {1, 2} or node.keywords:
                    raise MIRLoweringError("native iter requires one iterable or callable+sentinel")
                if len(node.args) == 2:
                    # ITER_PROTOCOL callback form: iter(callable, sentinel)
                    callable_expr = self._lower_expr(builder, node.args[0])
                    sentinel_expr = self._lower_expr(builder, node.args[1])
                    result = builder.temp()
                    builder.emit("builtin_iter_new", "calliter", (callable_expr, sentinel_expr), None, result=result)
                    return result
                value = self._lower_expr(builder, node.args[0])
                result = builder.temp()
                builder.emit("iter_new", value, result=result)
                return result
            if node.func.kind == HIRKind.LOAD and node.func.name in {"next", "siguiente"}:
                if len(node.args) not in {1, 2} or node.keywords:
                    raise MIRLoweringError("native next requires one iterator and optional default")
                iterator = self._lower_expr(builder, node.args[0])
                result = builder.temp()
                if len(node.args) == 1:
                    builder.emit(
                        "iter_next",
                        iterator,
                        self._find_exception_handler(builder, "StopIteration"),
                        result=result,
                    )
                    return result
                # next(it, default): on StopIteration, bind default instead of propagating.
                default_val = self._lower_expr(builder, node.args[1])
                handler = builder.new_block()
                end = builder.new_block()
                builder.emit("try_push")
                builder.emit("iter_next", iterator, handler.label, result=result)
                builder.emit("try_pop")
                builder.emit("jump", end.label)
                builder.current = handler
                builder.emit("catch_clear")
                builder.emit("try_pop")
                builder.emit("store", result, default_val)
                builder.emit("jump", end.label)
                builder.current = end
                return result
            if node.func.kind == HIRKind.ATTR and node.func.value.kind == HIRKind.LOAD:
                attr_name = node.func.attr
                owner = self._lower_expr(builder, node.func.value)
                if attr_name in {"throw", "arrojar"}:
                    # GENERATOR_THROW_V1: generators cannot catch (yield-in-try is
                    # fail-closed), so throw() = mark finished + raise at the
                    # caller's enclosing handler, mirroring CPython when the
                    # generator does not catch the exception.
                    if len(node.args) != 1 or node.keywords:
                        raise MIRLoweringError("native generator throw requires one argument")
                    exc_node = node.args[0]
                    if exc_node.kind == HIRKind.CALL and exc_node.func.kind == HIRKind.LOAD and not exc_node.args and not exc_node.keywords:
                        exc_type = exc_node.func.name
                    elif exc_node.kind == HIRKind.LOAD:
                        exc_type = exc_node.name
                    else:
                        raise MIRLoweringError("native generator throw requires an exception type")
                    if exc_type not in _BUILTIN_EXCEPTIONS and exc_type not in self.classes:
                        raise MIRLoweringError(f"unsupported native exception type '{exc_type}'")
                    result = builder.temp()
                    builder.emit(
                        "gen_throw", owner, exc_type,
                        self._find_exception_handler(builder, exc_type),
                        result=result,
                    )
                    return result
                if attr_name in {"close", "cerrar"}:
                    if node.args or node.keywords:
                        raise MIRLoweringError("native generator close takes no arguments")
                    result = builder.temp()
                    builder.emit("gen_close", owner, result=result)
                    return result
                if attr_name in {"send", "enviar"}:
                    if len(node.args) != 1 or node.keywords:
                        raise MIRLoweringError("native generator send requires one argument")
                    value = self._lower_expr(builder, node.args[0])
                    result = builder.temp()
                    builder.emit("gen_send", owner, value, self._find_exception_handler(builder, "TypeError"), result=result)
                    return result
            if node.func.kind == HIRKind.LOAD and node.func.name in {"enumerate", "enumerar"}:
                if len(node.args) not in {1, 2} or node.keywords:
                    raise MIRLoweringError("native enumerate requires one iterable and optional start")
                source = self._lower_expr(builder, node.args[0])
                start = self._lower_expr(builder, node.args[1]) if len(node.args) == 2 else None
                result = builder.temp()
                builder.emit("builtin_iter_new", "enumerate", source, start, result=result)
                return result
            if node.func.kind == HIRKind.LOAD and node.func.name in {"reversed", "reverso"}:
                if len(node.args) != 1 or node.keywords:
                    raise MIRLoweringError("native reversed requires one iterable")
                source = self._lower_expr(builder, node.args[0])
                result = builder.temp()
                builder.emit("builtin_iter_new", "reversed", source, None, result=result)
                return result
            if node.func.kind == HIRKind.LOAD and node.func.name in {"zip", "combinar"}:
                if len(node.args) != 2 or node.keywords:
                    raise MIRLoweringError("native zip currently requires two iterables")
                left = self._lower_expr(builder, node.args[0])
                right = self._lower_expr(builder, node.args[1])
                result = builder.temp()
                builder.emit("builtin_iter_new", "zip", (left, right), None, result=result)
                return result
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in {"map", "filtrar", "filter"}
                and node.func.name not in builder.closures
            ):
                if len(node.args) != 2 or node.keywords:
                    raise MIRLoweringError("native map/filter require a unary callback and one iterable")
                callback = self._lower_expr(builder, node.args[0])
                source = self._lower_expr(builder, node.args[1])
                result = builder.temp()
                builder.emit("builtin_iter_new", "map" if node.func.name == "map" else "filter", source, callback, result=result)
                return result
            if node.func.kind == HIRKind.LOAD and node.func.name in self.generators:
                func_name = node.func.name
                arg_vals = tuple(self._lower_expr(builder, a) for a in node.args)
                result = builder.temp()
                builder.emit("gen_init", func_name, arg_vals, result=result)
                return result
            if node.func.kind == HIRKind.LOAD and node.func.name in self.async_generators:
                # ASYNC_GENERATOR_V1: an async generator call creates an async
                # generator object (same gen_init state machine) in ANY context —
                # unlike a coroutine, it must not be awaited; `asincrono para`
                # (or later anext()) drives it.
                func_name = node.func.name
                arg_vals = tuple(self._lower_expr(builder, a) for a in node.args)
                result = builder.temp()
                builder.emit("gen_init", func_name, arg_vals, result=result)
                return result
            if node.func.kind == HIRKind.LOAD and node.func.name in self.async_functions:
                if not builder.awaiting:
                    raise MIRLoweringError("native coroutine must be awaited or passed directly to asyncio.run")
                # COROUTINE_OBJECT_V1: an awaited async call creates a real
                # coroutine object (a suspendible state machine, like a
                # generator) rather than inlining its body.
                func_name = node.func.name
                arg_vals = tuple(self._lower_expr(builder, a) for a in node.args)
                result = builder.temp()
                builder.emit("gen_init", func_name, arg_vals, result=result)
                return result
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
                    builder.emit("call", function, args, _active_handler(builder), result=result)
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
                        coro_ref = self._lower_expr(builder, node.args[0])
                    finally:
                        builder.awaiting = False
                    # TASK_SCHEDULER_V1: run the coroutine inside a root task on
                    # the cooperative event loop (piton_event_run). The root
                    # task can create_task/gather additional work; its result is
                    # the coroutine's return value, like CPython.
                    result = builder.temp()
                    builder.emit("event_run", coro_ref, result=result)
                    return result
                if module_name == "asyncio" and node.func.attr == "create_task":
                    if len(node.args) != 1 or node.keywords or node.args[0].kind != HIRKind.CALL:
                        raise MIRLoweringError("native asyncio.create_task requires one direct coroutine call")
                    # awaiting=True only so the direct coroutine call lowers to
                    # gen_init (the coroutine OBJECT); create_task does not await
                    # it — the loop drives it as a task.
                    builder.awaiting = True
                    try:
                        coro_ref = self._lower_expr(builder, node.args[0])
                    finally:
                        builder.awaiting = False
                    result = builder.temp()
                    builder.emit("task_new", coro_ref, result=result)
                    return result
                if module_name == "asyncio" and node.func.attr == "sleep":
                    if len(node.args) != 1 or node.keywords:
                        raise MIRLoweringError("native asyncio.sleep requires one positional argument")
                    delay = self._lower_expr(builder, node.args[0])
                    result = builder.temp()
                    # sleep0 checks delay != 0 at runtime (fail closed: real
                    # timers are NOT_DEMONSTRATED in TASK_SCHEDULER_V1).
                    builder.emit("sleep0", delay, result=result)
                    return result
                if module_name == "asyncio" and node.func.attr == "gather":
                    if node.keywords:
                        raise MIRLoweringError("native asyncio.gather does not support keyword arguments yet")
                    if not node.args:
                        raise MIRLoweringError("native asyncio.gather requires at least one coroutine")
                    task_refs = []
                    for arg in node.args:
                        if arg.kind == HIRKind.CALL:
                            builder.awaiting = True
                            try:
                                coro_ref = self._lower_expr(builder, arg)
                            finally:
                                builder.awaiting = False
                            task = builder.temp()
                            builder.emit("task_new", coro_ref, result=task)
                        else:
                            # a variable already holding a task (created with
                            # asyncio.create_task) shares the running task: the
                            # runtime tracks already-finished/cancelled members.
                            task = self._lower_expr(builder, arg)
                        task_refs.append(task)
                    result = builder.temp()
                    builder.emit("gather_new", len(task_refs), result=result)
                    for index, task in enumerate(task_refs):
                        slot = builder.temp()
                        builder.emit("gather_add", result, index, task, result=slot)
                    return result
                if module_name == "math":
                    attr = node.func.attr
                    kwargs_rejected = node.keywords
                    if kwargs_rejected:
                        raise MIRLoweringError("native math does not support keyword arguments")
                    if attr == "sqrt" and len(node.args) == 1:
                        value = self._lower_expr(builder, node.args[0])
                        result = builder.temp()
                        builder.emit("math_sqrt", value, result=result)
                        return result
                    if attr in {"floor", "ceil", "trunc", "fabs"} and len(node.args) == 1:
                        value = self._lower_expr(builder, node.args[0])
                        result = builder.temp()
                        builder.emit(f"math_{attr}", value, _active_handler(builder), result=result)
                        return result
                    if attr == "gcd" and len(node.args) == 2:
                        left = self._lower_expr(builder, node.args[0])
                        right = self._lower_expr(builder, node.args[1])
                        result = builder.temp()
                        builder.emit("math_gcd", left, right, _active_handler(builder), result=result)
                        return result
                    raise MIRLoweringError(
                        "native math supports sqrt/floor/ceil/trunc/fabs(x) and gcd(a, b) (MATH_TIER1_V1)"
                    )
                function = builder.temp()
                builder.emit("load", f"{module_name.replace('.', '__')}__{node.func.attr}", result=function)
                args = tuple(self._lower_expr(builder, arg) for arg in node.args)
                result = builder.temp()
                builder.emit("call", function, args, _active_handler(builder), result=result)
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
                if node.func.attr == "cancel":
                    # TASK_SCHEDULER_V1: t.cancel() — the runtime dispatches by
                    # magic: a Task sets cancel_requested; anything else fails
                    # closed with TypeError (object has no attribute 'cancel').
                    # KNOWN LIMITATION (V1): a user class that defines its own
                    # cancel() method called on an instance would be intercepted
                    # here; method_call for statically-known classes is bypassed
                    # for this attribute name.
                    if node.args:
                        raise MIRLoweringError("native task.cancel() takes no arguments")
                    owner = self._lower_expr(builder, node.func.value)
                    result = builder.temp()
                    builder.emit("task_cancel", owner, result=result)
                    return result
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
                result = builder.temp()
                builder.emit("frame_call", function, args, result=result)
                return result
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
            if node.func.kind == HIRKind.LOAD and self.function_frame_abi.get(node.func.name, False):
                builder.emit("frame_call", function, args, result=result)
            else:
                builder.emit("call", function, args, _active_handler(builder), result=result)
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
            # MATH_TIER1_V1: math.pi / math.e lower to float constants.
            # (_module_attr_chain deliberately excludes asyncio/math/sys, so we
            #  match the module alias directly here.)
            if (
                node.value.kind == HIRKind.LOAD
                and node.value.name in builder.module_aliases
                and builder.module_aliases[node.value.name] == "math"
                and node.attr in {"pi", "e"}
            ):
                result = builder.temp()
                const_value = 3.141592653589793 if node.attr == "pi" else 2.718281828459045
                builder.emit("const", const_value, result=result)
                return result
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
            if (
                node.value.kind == HIRKind.CALL
                and node.value.func.kind == HIRKind.LOAD
                and node.value.func.name in self.async_generators
            ):
                # CPython: async generator objects do not implement __await__.
                raise MIRLoweringError("cannot await an async generator object")
            builder.awaiting = True
            try:
                awaited = self._lower_expr(builder, node.value)
            finally:
                builder.awaiting = False
            # AWAIT_PROTOCOL_V1: await suspends the coroutine on the awaited
            # coroutine; the scheduler feeds the awaited result back through
            # sent_value at the resume point (same mechanism as gen_yield).
            result = builder.temp()
            builder.emit("gen_yield", awaited, result=result)
            return result
        if kind == HIRKind.LIST_COMP:
            return self._lower_listcomp(builder, node)
        if kind == HIRKind.SET_COMP:
            return self._lower_setcomp(builder, node)
        if kind == HIRKind.DICT_COMP:
            return self._lower_dictcomp(builder, node)
        if kind == HIRKind.GEN_EXPR:
            return self._lower_genexpr(builder, node)
        result = builder.temp()
        builder.emit("runtime_call", "unsupported", kind.name, result=result)
        return result

    def _lower_comp_generators(self, builder: _Builder, generators: list, body_fn):
        """Lower chained comprehension generators recursively.

        body_fn(builder) is called at the innermost loop body to emit the
        accumulation instruction.
        """
        if not generators:
            body_fn(builder)
            return
        gen = generators[0]
        iter_val = self._lower_expr(builder, gen.iter)
        index_name = f"@comp_index_{builder.loop_counter}"
        builder.loop_counter += 1
        zero = builder.temp()
        builder.emit("const", 0, result=zero)
        builder.emit("store", index_name, zero)
        condition_block = builder.new_block()
        body_block = builder.new_block()
        increment_block = builder.new_block()
        end_block = builder.new_block()
        builder.emit("jump", condition_block.label)
        builder.current = condition_block
        index = builder.temp()
        builder.emit("load", index_name, result=index)
        length = builder.temp()
        builder.emit("collection_len", iter_val, result=length)
        condition = self._compare(builder, "<", index, length)
        builder.emit("branch", condition, body_block.label, end_block.label)
        builder.current = body_block
        item = builder.temp()
        builder.emit("get_item", iter_val, index, result=item)
        if gen.target.kind not in {HIRKind.LOAD, HIRKind.STORE}:
            raise MIRLoweringError("native comprehension target must be a name")
        builder.emit("store", gen.target.name, item)
        rest_fn = lambda b: self._lower_comp_generators(b, generators[1:], body_fn)
        self._lower_comp_ifs(builder, gen.ifs, rest_fn)
        builder.emit("jump", increment_block.label)
        builder.current = increment_block
        current = builder.temp()
        one = builder.temp()
        builder.emit("load", index_name, result=current)
        builder.emit("const", 1, result=one)
        following = self._binary(builder, "+", current, one)
        builder.emit("store", index_name, following)
        builder.emit("jump", condition_block.label)
        builder.current = end_block

    def _lower_comp_ifs(self, builder: _Builder, ifs: list, body_fn):
        """Lower chained if conditions in a comprehension generator."""
        if not ifs:
            body_fn(builder)
            return
        cond = self._lower_expr(builder, ifs[0])
        then_block = builder.new_block()
        end_block = builder.new_block()
        builder.emit("branch", cond, then_block.label, end_block.label)
        builder.current = then_block
        self._lower_comp_ifs(builder, ifs[1:], body_fn)
        builder.emit("jump", end_block.label)
        builder.current = end_block

    def _lower_listcomp(self, builder: _Builder, node) -> str:
        result = builder.temp()
        builder.emit("build_collection", "list", (), result=result)
        if not node.generators:
            return result
        def append_elt(b):
            elt_val = self._lower_expr(b, node.elt)
            b.emit("list_append", result, elt_val)
        self._lower_comp_generators(builder, node.generators, append_elt)
        return result

    def _lower_setcomp(self, builder: _Builder, node) -> str:
        result = builder.temp()
        builder.emit("build_collection", "set", (), result=result)
        if not node.generators:
            return result
        def add_elt(b):
            elt_val = self._lower_expr(b, node.elt)
            b.emit("set_add", result, elt_val)
        self._lower_comp_generators(builder, node.generators, add_elt)
        return result

    def _lower_dictcomp(self, builder: _Builder, node) -> str:
        result = builder.temp()
        builder.emit("build_collection", "dict", (), result=result)
        if not node.generators:
            return result
        def add_entry(b):
            key_val = self._lower_expr(b, node.key)
            value_val = self._lower_expr(b, node.value)
            b.emit("dict_put", result, key_val, value_val)
        self._lower_comp_generators(builder, node.generators, add_entry)
        return result

    def _lower_genexpr(self, builder: _Builder, node) -> str:
        values = self._lower_listcomp(builder, node)
        result = builder.temp()
        builder.emit("genexpr_new", values, result=result)
        return result

    def _binary(self, builder: _Builder, op: str, left: Any, right: Any) -> str:
        result = builder.temp()
        builder.emit("binary", op, left, right, result=result)
        return result

    def _expand_literal_call(self, node: HIRNode) -> tuple[list[HIRNode], list[Keyword]]:
        """Expand compile-time sequence/mapping arguments without a new ABI."""
        starred = {id(arg) for arg in getattr(node, "starred_args", [])}
        positional: list[HIRNode] = []
        for arg in node.args:
            if id(arg) not in starred:
                positional.append(arg)
                continue
            if arg.kind not in {HIRKind.LIST, HIRKind.TUPLE}:
                raise MIRLoweringError(
                    "native positional unpacking currently requires a list or tuple literal"
                )
            positional.extend(arg.elts)

        keywords: list[Keyword] = []
        for keyword in node.keywords:
            if keyword.arg is not None:
                keywords.append(keyword)
                continue
            mapping = keyword.value
            if mapping is None or mapping.kind != HIRKind.DICT:
                raise MIRLoweringError(
                    "native keyword unpacking currently requires a dict literal"
                )
            for key, value in zip(mapping.keys, mapping.values):
                if key is None or key.kind != HIRKind.CONST or not isinstance(key.value, str):
                    raise MIRLoweringError(
                        "native keyword unpacking requires string keys"
                    )
                keywords.append(Keyword(arg=key.value, value=value))
        return positional, keywords

    def _call_has_dynamic_unpack(self, node: HIRNode) -> bool:
        """True iff the call needs runtime unpacking (CALL_UNPACKING_DYNAMIC4_V1).

        Literal expansions stay on the literal path; a call is dynamic iff it
        carries a starred argument that is not a list/tuple literal, or a
        `**` mapping that is not a dict literal with constant string keys.
        """
        starred = {id(arg) for arg in getattr(node, "starred_args", ())}
        for arg in node.args:
            if id(arg) in starred and arg.kind not in {HIRKind.LIST, HIRKind.TUPLE}:
                return True
        for keyword in node.keywords:
            if keyword.arg is not None:
                continue
            mapping = keyword.value
            if mapping is None or mapping.kind != HIRKind.DICT:
                return True
            for key, _value in zip(mapping.keys, mapping.values):
                if key is None or key.kind != HIRKind.CONST or not isinstance(key.value, str):
                    return True
        return False

    def _lower_call_unpack(self, builder: _Builder, node: HIRNode) -> str:
        """CALL_UNPACKING_DYNAMIC4_V1: lower runtime `*seq` / `**mapping`
        unpacking for a statically-known callee with a plain signature.

        Scope (fail-closed otherwise):
          - callee resolves to a module-level function name statically;
          - signature is plain: <= 4 params, no positional-only marker,
            no keyword-only marker, no *args, no **kwargs;
          - `*seq` requires a statically sequence-typed operand at emit time
            (list/tuple), `**mapping` a string-keyed dict literal lineage.
        """
        func = node.func
        if func.kind != HIRKind.LOAD or func.name not in self.function_params:
            raise MIRLoweringError(
                "native dynamic call unpacking requires a statically-known function"
            )
        name = func.name
        params = self.function_params[name]
        if len(params) > 4:
            raise MIRLoweringError(
                "CALL_UNPACKING_DYNAMIC4_V1 supports callees with at most four parameters"
            )
        if self.function_posonly.get(name) or self.function_kwonly.get(name):
            raise MIRLoweringError(
                "CALL_UNPACKING_DYNAMIC4_V1 does not support positional-only or keyword-only callees yet"
            )
        if self.function_varargs.get(name) or self.function_kwargs.get(name):
            raise MIRLoweringError(
                "CALL_UNPACKING_DYNAMIC4_V1 does not support variadic callees yet"
            )
        starred = {id(arg) for arg in getattr(node, "starred_args", ())}
        positional_parts: list[tuple[str, str]] = []
        for arg in node.args:
            if id(arg) in starred:
                positional_parts.append(("star", self._lower_expr(builder, arg)))
            else:
                positional_parts.append(("value", self._lower_expr(builder, arg)))
        keyword_parts: list[tuple[str, Any, str] | tuple[str, str, Any]] = []
        for keyword in node.keywords:
            if keyword.arg is None:
                keyword_parts.append(("kwstar", "", self._lower_expr(builder, keyword.value)))
            else:
                if keyword.arg not in params:
                    raise MIRLoweringError(f"unexpected keyword argument: {keyword.arg}")
                keyword_parts.append(("keyword", keyword.arg, self._lower_expr(builder, keyword.value)))
        result = builder.temp()
        builder.emit(
            "call_unpack",
            name,
            tuple(positional_parts),
            tuple(keyword_parts),
            self._find_exception_handler(builder, "TypeError"),
            result=result,
        )
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
        else:
            return None

    def _resume_generator(self, generator: dict[str, Any]) -> Any:
        """Resume one MIR generator until its next yield or completion."""
        function = self.functions[generator["function"]]
        env = generator["env"]
        blocks = {block.label: block for block in function.blocks}
        label = generator["label"]
        ip = generator["ip"]
        # If we have a sent value from send(), inject it at the yield result slot
        sent_value = generator.pop("sent_value", None)
        if sent_value is not None:
            # Find the yield instruction that we're resuming from (ip - 1)
            # and inject the sent value into its result slot
            prev_block = blocks[label]
            if ip > 0 and ip - 1 < len(prev_block.instructions):
                prev_inst = prev_block.instructions[ip - 1]
                if prev_inst.op == "gen_yield" and prev_inst.result:
                    env[prev_inst.result] = sent_value
        while True:
            block = blocks[label]
            while ip < len(block.instructions):
                instruction = block.instructions[ip]
                self.trace.append({"function": function.name, "block": label, "op": instruction.op})
                jump = self._execute(instruction, env)
                if isinstance(jump, tuple) and jump[0] == "yield":
                    generator["yielded"] = True
                    generator["started"] = True
                    generator["label"], generator["ip"] = label, ip + 1
                    return jump[1]
                if isinstance(jump, tuple) and jump[0] == "jump":
                    label, ip = jump[1], 0
                    break
                if isinstance(jump, tuple) and jump[0] == "return":
                    generator["yielded"] = False
                    generator["done"] = True
                    return None
                ip += 1
            else:
                generator["yielded"] = False
                generator["done"] = True
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
        elif op == "list_append":
            lst = self._value(args[0], env)
            val = self._value(args[1], env)
            lst.append(val)
        elif op == "set_add":
            target = self._value(args[0], env)
            target.add(self._value(args[1], env))
        elif op == "dict_put":
            target = self._value(args[0], env)
            target[self._value(args[1], env)] = self._value(args[2], env)
        elif op == "genexpr_new":
            env[instruction.result] = iter(self._value(args[0], env))
        elif op == "iter_new":
            source = self._value(args[0], env)
            if isinstance(source, dict) and source.get("__generator__"):
                env[instruction.result] = source
            else:
                env[instruction.result] = iter(source)
        elif op == "iter_next":
            iterator = self._value(args[0], env)
            if isinstance(iterator, dict) and iterator.get("__generator__"):
                if iterator["done"]:
                    raise StopIteration
                value = self._resume_generator(iterator)
                if not iterator["yielded"]:
                    raise StopIteration
                env[instruction.result] = value
            else:
                env[instruction.result] = next(iterator)
        elif op == "gen_init":
            func_name = args[0]
            gen_args = tuple(self._value(a, env) for a in args[1]) if len(args) > 1 and args[1] else ()
            target = self.functions[func_name]
            env[instruction.result] = {
                "__generator__": True,
                "function": func_name,
                "env": dict(zip(target.params, gen_args)),
                "label": target.blocks[0].label,
                "ip": 0,
                "done": False,
                "yielded": False,
                "started": False,
            }
        elif op == "gen_collect":
            gen_ref = self._value(args[0], env)
            if isinstance(gen_ref, dict) and gen_ref.get("__generator__"):
                collected = []
                while not gen_ref["done"]:
                    value = self._resume_generator(gen_ref)
                    if gen_ref["yielded"]:
                        collected.append(value)
                env[instruction.result] = collected
            else:
                env[instruction.result] = list(gen_ref)
        elif op == "gen_next":
            gen_ref = self._value(args[0], env)
            if isinstance(gen_ref, dict) and gen_ref.get("__generator__"):
                if gen_ref["done"]:
                    raise StopIteration
                value = self._resume_generator(gen_ref)
                if not gen_ref["yielded"]:
                    raise StopIteration
                env[instruction.result] = value
            elif hasattr(gen_ref, '__next__'):
                env[instruction.result] = next(gen_ref)
            else:
                env[instruction.result] = next(iter(gen_ref))
        elif op == "gen_send":
            gen_ref = self._value(args[0], env)
            sent_value = self._value(args[1], env) if len(args) > 1 else None
            if isinstance(gen_ref, dict) and gen_ref.get("__generator__"):
                if gen_ref["done"]:
                    raise StopIteration
                if not gen_ref.get("started", False) and sent_value is not None:
                    raise TypeError("can't send non-None value to a just-started generator")
                gen_ref["sent_value"] = sent_value
                value = self._resume_generator(gen_ref)
                if not gen_ref["yielded"]:
                    raise StopIteration
                env[instruction.result] = value
            else:
                raise TypeError("send() requires a generator")
        elif op == "gen_throw":
            gen_ref = self._value(args[0], env)
            exc_name = args[1]
            if isinstance(gen_ref, dict) and gen_ref.get("__generator__"):
                gen_ref["done"] = True
                gen_ref["yielded"] = False
                exc_cls = {
                    "StopIteration": StopIteration,
                    "ValueError": ValueError,
                    "TypeError": TypeError,
                    "RuntimeError": RuntimeError,
                    "Exception": Exception,
                }.get(exc_name, Exception)
                raise exc_cls(exc_name)
            raise TypeError("throw() requires a generator")
        elif op == "gen_close":
            gen_ref = self._value(args[0], env)
            if isinstance(gen_ref, dict) and gen_ref.get("__generator__"):
                gen_ref["done"] = True
                gen_ref["yielded"] = False
                env[instruction.result] = None
            else:
                raise TypeError("close() requires a generator")
        elif op == "gen_yield":
            val = self._value(args[0], env) if args else None
            env[instruction.result] = val
            return ("yield", val)
        elif op == "agen_emit":
            val = self._value(args[0], env) if args else None
            env[instruction.result] = val
            return ("yield", val)
        elif op == "agen_next":
            gen_ref = self._value(args[0], env)
            if isinstance(gen_ref, dict) and gen_ref.get("__generator__"):
                value = self._resume_generator(gen_ref)
                env[instruction.result] = value if gen_ref["yielded"] else None
            else:
                raise TypeError("agen_next() requires an async generator")
        elif op == "agen_done":
            gen_ref = self._value(args[0], env)
            if isinstance(gen_ref, dict) and gen_ref.get("__generator__"):
                env[instruction.result] = int(bool(gen_ref.get("done")))
            else:
                raise TypeError("agen_done() requires an async generator")
        elif op == "task_new":
            coro = self._value(args[0], env)
            if not (isinstance(coro, dict) and coro.get("__generator__")):
                raise TypeError("create_task() requires a coroutine")
            env[instruction.result] = {
                "__task__": True,
                "chain": [coro],
                "state": 0,
                "result": 0,
                "cancel_requested": 0,
                "waiters": [],
                "gather_owner": None,
                "gather_slot": 0,
            }
        elif op == "task_cancel":
            task = self._value(args[0], env)
            if not (isinstance(task, dict) and task.get("__task__")):
                raise TypeError("object has no attribute 'cancel'")
            task["cancel_requested"] = 1
            env[instruction.result] = 0
        elif op == "sleep0":
            delay = self._value(args[0], env)
            if delay != 0:
                raise TypeError("native asyncio.sleep currently supports sleep(0) only")
            env[instruction.result] = PITON_SLEEP0_MAGIC
        elif op == "gather_new":
            n = int(args[0])
            env[instruction.result] = {
                "__gather__": True,
                "n": n,
                "remaining": n,
                "results": [0] * n,
                "tasks": [None] * n,
                "waiters": [],
            }
        elif op == "gather_add":
            gather = self._value(args[0], env)
            index = int(self._value(args[1], env))
            task = self._value(args[2], env)
            if not (isinstance(gather, dict) and gather.get("__gather__")):
                raise TypeError("object is not a gather")
            if not (isinstance(task, dict) and task.get("__task__")):
                raise TypeError("gather requires tasks")
            gather["tasks"][index] = task
            task["gather_owner"] = gather
            task["gather_slot"] = index
            env[instruction.result] = 0
        elif op == "event_run":
            root = self._value(args[0], env)
            if not (isinstance(root, dict) and root.get("__generator__")):
                raise TypeError("object is not a coroutine")
            root_task = {
                "__task__": True,
                "chain": [root],
                "state": 0,
                "result": 0,
                "cancel_requested": 0,
                "waiters": [],
                "gather_owner": None,
                "gather_slot": 0,
            }
            ready = [root_task]

            # Mirror of piton_step_task (native_runtime.c): drive the task's
            # inline chain depth-first; a loop value (sleep0/task/gather)
            # suspends the WHOLE chain (recorded on the task) and requeues it.
            def step(task):
                while task["chain"]:
                    coro = task["chain"][-1]
                    value = self._resume_generator(coro)
                    if coro["done"]:
                        # evaluator loses explicit return values (pre-existing)
                        task["chain"].pop()
                        if not task["chain"]:
                            task["state"] = 3
                            task["result"] = 0
                            return True
                        task["chain"][-1]["sent_value"] = 0
                        continue
                    if value == PITON_SLEEP0_MAGIC:
                        ready.append(task)
                        return False
                    if not isinstance(value, dict):
                        raise TypeError("object is not awaitable")
                    if value.get("__generator__"):
                        task["chain"].append(value)
                        continue
                    if value.get("__task__"):
                        other = value
                        if other["cancel_requested"] or other["state"] == 4:
                            task["cancel_requested"] = 1
                            ready.append(task)
                            return False
                        other["waiters"].append(task)
                        if other["state"] == 0:
                            ready.append(other)
                        return False
                    if value.get("__gather__"):
                        gather = value
                        if gather["remaining"] == 0:
                            coro["sent_value"] = list(gather["results"])
                            continue
                        gather["waiters"].append(task)
                        for sub in gather["tasks"]:
                            if sub is not None and sub["state"] == 0:
                                ready.append(sub)
                        return False
                    raise TypeError("object is not awaitable")
                task["state"] = 3
                task["result"] = 0
                return True

            while ready:
                task = ready.pop(0)
                if task["cancel_requested"] and task["state"] != 3:
                    task["state"] = 4
                    for waiter in task["waiters"]:
                        waiter["cancel_requested"] = 1
                        ready.append(waiter)
                    task["waiters"] = []
                    continue
                if step(task):
                    if task["gather_owner"] is not None:
                        gather = task["gather_owner"]
                        gather["results"][task["gather_slot"]] = task["result"]
                        gather["remaining"] -= 1
                        if gather["remaining"] == 0:
                            lst = list(gather["results"])
                            for waiter in gather["waiters"]:
                                waiter["chain"][-1]["sent_value"] = lst
                                ready.append(waiter)
                            gather["waiters"] = []
                    for waiter in task["waiters"]:
                        waiter["chain"][-1]["sent_value"] = task["result"]
                        ready.append(waiter)
                    task["waiters"] = []
            if root_task["cancel_requested"] or root_task["state"] == 4:
                raise Exception("CancelledError")
            env[instruction.result] = root_task["result"]
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
                if function in self.generators:
                    env[instruction.result] = {
                        "__generator__": True,
                        "function": function,
                        "env": dict(zip(target.params, values)),
                        "label": target.blocks[0].label,
                        "ip": 0,
                        "done": False,
                        "yielded": False,
                    }
                else:
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
