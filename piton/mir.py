"""MIR canónico mínimo para pruebas y trazas, no una VM de producción."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
import json
import operator
from typing import Any, Dict, List, Optional, Sequence

from piton.hir import BoolOp, Call, ExceptHandler, HIRKind, HIRNode, Keyword, With


_BUILTIN_EXCEPTIONS = {"Exception", "BaseException", "ValueError", "TypeError", "RuntimeError", "StopIteration", "AssertionError", "KeyError", "IndexError", "AttributeError", "NameError", "ZeroDivisionError", "OverflowError", "NotImplementedError"}
# EXC_HIERARCHY_V1: builtins cuya madre NO es Exception. Se declaran aparte
# para no aplanar la jerarquia (un `excepto UserWarning` NO debe atrapar un
# Warning pelado). warnings.py usa Warning como default y los construye.
_BUILTIN_EXCEPTION_PARENTS = {
    "Warning": "Exception",
    "UserWarning": "Warning",
    "DeprecationWarning": "Warning",
    "PendingDeprecationWarning": "Warning",
    "SyntaxWarning": "Warning",
    "RuntimeWarning": "Warning",
    "FutureWarning": "Warning",
    "ImportWarning": "Warning",
    "UnicodeWarning": "Warning",
    "BytesWarning": "Warning",
    "ResourceWarning": "Warning",
    "EncodingWarning": "Warning",
}
_BUILTIN_EXCEPTIONS |= set(_BUILTIN_EXCEPTION_PARENTS)
# EXC_OSError_V1: raise/except de la familia OSError — el backend solo ve el
# nombre como string (piton_raise_set + strcmp en handlers), asi que no
# requiere runtime nuevo. IOError/EnvironmentError son aliases de OSError.
_BUILTIN_EXCEPTIONS |= {"OSError", "IOError", "EnvironmentError"}
# EXC_BUILTIN_FULL_V1: el resto de excepciones builtin de CPython. El
# backend las maneja por NOMBRE (piton_raise_set + comparacion en el
# handler), asi que no hace falta runtime nuevo: sin esta lista,
# `raise SystemExit(code)` (sitebuiltins.Quitter) fallaba cerrado por
# "unsupported native exception type".
_BUILTIN_EXCEPTIONS |= {
    "SystemExit", "KeyboardInterrupt", "StopAsyncIteration", "UserWarning",
    "DeprecationWarning", "RuntimeWarning", "FutureWarning", "PendingDeprecationWarning",
    "ArithmeticError", "FloatingPointError", "LookupError",
    "ReferenceError", "BufferError", "MemoryError", "RecursionError",
    "ImportError", "ModuleNotFoundError", "EOFError",
    "SyntaxError", "IndentationError", "TabError",
    "UnicodeError", "UnicodeDecodeError", "UnicodeEncodeError", "UnicodeTranslateError",
    "FileNotFoundError", "FileExistsError", "IsADirectoryError", "NotADirectoryError",
    "PermissionError", "ProcessLookupError", "TimeoutError", "ConnectionError",
    "BrokenPipeError", "ChildProcessError", "BlockingIOError", "InterruptedError",
}
# EXC_GROUP_V1: ExceptionGroup / BaseExceptionGroup se manejan por NOMBRE,
# igual que el resto de la familia builtin (piton_raise_set_args con la tupla
# .args completa: mensaje + lista de sub-excepciones). El runtime no modela
# todavia .exceptions ni el reparto de `except*` (fail-closed en
# _lower_except); lo observable aqui es el tipo y el mensaje, que es lo que
# ejercita errors.py (tutorial: raise dentro de try/except BaseException).
_BUILTIN_EXCEPTION_PARENTS |= {
    "ExceptionGroup": "Exception",
    "BaseExceptionGroup": "BaseException",
}
_BUILTIN_EXCEPTIONS |= {"ExceptionGroup", "BaseExceptionGroup"}
# BASE_BUILTIN_V1: bases builtin sin definicion previa (ver uso en class loop).
# BASE_BUILTIN_V1: bases builtin sin definicion previa (ver uso en class loop).
# `staticmethod` (abc) y `property` son del mismo tipo: no aportan metodos
# de usuario, asi que un super() hacia ellas es un no-op.
_BUILTIN_BASES = {"object", "classmethod", "staticmethod", "property"}
# NATIVE_MODULE_CONSTS_V1: modulos de la stdlib que el backend provee con
# sus CONSTANTES (no con codigo): `stat.S_IFDIR`, `__future__.division`...
# Los valores son los POSIX/estandar, no los del host. OJO: en CPython los
# `stat.S_IS*` son FUNCIONES (S_ISDIR(mode)) y los `__future__.*` son
# objetos _Feature — no son constantes, asi que no se proveen aqui (el
# backend falla cerrado al usarlos).
_NATIVE_MODULE_CONSTS: dict[str, dict[str, Any]] = {
    "stat": {
        "S_IFDIR": 0o040000, "S_IFCHR": 0o020000, "S_IFBLK": 0o060000,
        "S_IFREG": 0o100000, "S_IFIFO": 0o010000, "S_IFLNK": 0o120000,
        "S_IFSOCK": 0o140000, "S_IFMT": 0o170000, "S_ISUID": 0o4000,
        "S_ISGID": 0o2000, "S_ISVTX": 0o1000, "S_IRWXU": 0o700,
        "S_IRUSR": 0o400, "S_IWUSR": 0o200, "S_IXUSR": 0o100,
        "S_IRWXG": 0o070, "S_IRGRP": 0o040, "S_IWGRP": 0o020,
        "S_IXGRP": 0o010, "S_IRWXO": 0o007, "S_IROTH": 0o004,
        "S_IWOTH": 0o002, "S_IXOTH": 0o001, "S_IMODE": 0o7777,
    },
    "__future__": {
        "division": True, "absolute_import": True, "with_statement": True,
        "print_function": True, "unicode_literals": True, "generator_stop": True,
        "nested_scopes": True, "generators": True, "annotations": True,
        "barry_as_FLUFL": False, "braces": False, "all_feature_names": False,
    },
    "locale": {
        # Valores de glibc (Linux); category tags de setlocale.
        "LC_CTYPE": 0, "LC_NUMERIC": 1, "LC_TIME": 2, "LC_COLLATE": 3,
        "LC_MONETARY": 4, "LC_MESSAGES": 5, "LC_ALL": 6,
        "CHAR_MAX": 127,
    },
}
# Builtins con schema FIJO de keywords (el resto de una llamada con
# keywords a un nombre desconocido baja como DYNAMIC_CALL_V1).
_BUILTIN_KEYWORD_SCHEMAS = {"sorted", "ordenar", "min", "max", "print", "imprimir"}


def _os_name_const() -> str:
    """OS_TIER1_V1: value of ``os.name`` for the platform the native binary targets.

    Native binaries are built and executed on the host (``native_differential``
    picks the Linux ELF or the Windows PE from ``platform.system()``), so the host
    platform is also the target platform here.
    """
    import sys

    return "nt" if sys.platform == "win32" else "posix"


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
    # CLASS_BASES_V1: las bases DIRECTAS de cada clase (`cls.__bases__`).
    class_bases: dict = field(default_factory=dict)
    class_properties: dict = field(default_factory=dict)
    static_methods: set = field(default_factory=set)
    class_methods: set = field(default_factory=set)
    # CLASS_ATTR_TYPES_V1: {clase: {atributo: tipo literal}} — lo llena el
    # lowerer desde los class_attrs; los backends lo consultan en get_attr.
    class_attr_types: dict = field(default_factory=dict)
    # INJECTED_ATTR_TYPES_V1: attr_name -> tipo, para attrs escritos en
    # runtime sobre un receiver sin tipo estatico (decoradores: cls.author
    # = "PITON"). Clave global: el get_attr no sabe que clase recibe la
    # inyeccion. Conflicto de tipos -> se omite (default "int" del emisor).
    injected_attr_types: dict = field(default_factory=dict)

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
    "const": "PURE", "load": "PURE", "store": "PURE", "global_decl": "PURE",
    "jump": "PURE", "branch": "PURE", "return": "PURE",
    "branch_exc": "OPAQUE",
    "binary": "PURE", "unary": "PURE", "compare": "PURE",
    # GETTEXT_IDENTITY_V1: gettext.gettext es la identidad (sin efectos).
    "gettext_identity": "PURE",
    "math_sqrt": "PURE",
    "math_sin": "PURE", "math_cos": "PURE", "math_log": "PURE", "math_log2": "PURE",
    "math_floor": "PURE", "math_ceil": "PURE", "math_trunc": "PURE",
    "math_fabs": "PURE", "math_gcd": "PURE",
    "terminal_init": "PURE", "terminal_size": "PURE", "terminal_write": "PURE",
    "object_new": "PURE", "closure_new": "PURE", "cell_new": "PURE",
    # DYNAMIC_CALL_V1: llamada a un callable desconocido (el frame lo arma
    # el runtime; no hay efecto observable en build).
    "dynamic_call": "PURE",
    # OS_FSPATH_V1 / OS_PATHLIKE_V1: valores nativos de `os`.
    "os_fspath": "PURE", "os_fsencode": "PURE", "builtin_class": "PURE",
    # RAISE_VALUE_V1: levantar una instancia (no una clase).
    "raise_value": "OPAQUE",
    # PRINT_FD_V1: fijar/restaurar el descriptor de salida de print.
    "print_set_fd": "PURE", "print_restore_fd": "PURE",
    "build_collection": "PURE", "genexpr_new": "PURE", "gen_init": "PURE",
    "gather_new": "PURE",
    # READ
    "cell_load": "READ", "get_item": "READ", "get_slice": "READ", "collection_len": "READ",
    "truth_test": "READ",
    "unpack_check": "READ",
    "catch_type": "READ", "catch_message": "READ", "catch_flag": "READ",
    "load_exc_args": "READ",
    # WRITE
    "cell_store": "WRITE", "dict_put": "WRITE", "list_append": "WRITE",
    "del_item": "WRITE", "del_name": "PURE", "del_slice": "WRITE",
    "set_add": "WRITE",
    "collection_extend": "WRITE",
    "try_push": "WRITE", "try_pop": "WRITE", "subscript_store": "WRITE",
    "slice_store": "WRITE",
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
    "sys_exit": "OPAQUE",
    "sys_argv": "OPAQUE",
    "terminal_init": "OPAQUE", "terminal_cleanup": "OPAQUE", "terminal_write": "OPAQUE", "terminal_read": "OPAQUE",
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
        self.exception_handlers: list[list[tuple[str | None, str]]] = []
        self.in_except_handler = False
        self.reraise_type: str | None = None
        # EXC_ARGS_V1: nombres ligados por `excepto E como x` vigentes (el
        # cuerpo del handler ve los suyos; los bloques exteriores conservan
        # los de su propio handler).
        self.exception_bindings: frozenset[str] = frozenset()
        self.loop_counter = 0
        self._loop_stack: list[tuple[str, str, str | None]] = []
        self.module_aliases: dict[str, str] = {}
        self.from_import_aliases: dict[str, str] = {}
        self.module_state_vars: dict[str, str] = {}
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
    if not builder.exception_handlers:
        return None
    group = builder.exception_handlers[-1]
    if len(group) == 1:
        return group[0][1]
    return list(group)


def _iter_nested_statements(items: Any):
    """GETTEXT_IDENTITY_V1: recorre las sentencias ANIDADAS de un modulo
    (cuerpos de try/except, if/else, while, for, with) buscando imports que
    el scan de nivel de modulo no ve.

    Los imports anidados son legitimos en Python (getopt mete
    `from gettext import gettext as _` dentro de un try/except de bootstrap)
    y el scan de nivel de modulo los ignora, dejando el nombre sin resolver.
    """
    for node in items or ():
        yield node
        for attr in ("body", "orelse", "finalbody"):
            sub = getattr(node, attr, None)
            if isinstance(sub, list):
                yield from _iter_nested_statements(sub)
        for handler in getattr(node, "handlers", ()) or ():
            hbody = getattr(handler, "body", None)
            if isinstance(hbody, list):
                yield from _iter_nested_statements(hbody)


def _module_level_defs(module_body: list) -> list:
    """FUNC_SIG_PRESCAN_V1: las definiciones de NIVEL DE MODULO, incluidas
    las que viven en el cuerpo de un try/except o de un if (quopri define
    los fallbacks de binascii dentro de un `except ImportError`, y el
    modulo los llama con keywords).

    No se baja dentro de los cuerpos de FUNCION: esas defs son closures
    (se levantan como `padre__hijo`), no funciones de modulo.
    """
    found: list = []
    for node in module_body or ():
        if getattr(node, "kind", None) == HIRKind.FUNC_DEF:
            found.append(node)
            continue
        for attr in ("body", "orelse", "finalbody"):
            sub = getattr(node, attr, None)
            if isinstance(sub, list):
                found.extend(_module_level_defs(sub))
        for handler in getattr(node, "handlers", ()) or ():
            hbody = getattr(handler, "body", None)
            if isinstance(hbody, list):
                found.extend(_module_level_defs(hbody))
    return found


def _is_bundled_module(module_name: str) -> bool:
    """True si el modulo viene del bundled stdlib (piton/stdlib_piton/)."""
    from piton.bundled_stdlib import bundled_module_source
    if bundled_module_source(module_name) is not None:
        return True
    # Soporte para nombres de submodulos con punto
    alt_name = module_name.replace('.', '__')
    if alt_name != module_name and bundled_module_source(alt_name) is not None:
        return True
    return False


def _module_state_name(module_name: str, var_name: str) -> str:
    """MODULE_STATE_V1: el nombre CUALIFICADO de una variable de nivel de
    modulo de un modulo importado ("warnings__var__filtros"). El prefijo
    `var__` lo separa del de las funciones del modulo, para que una
    variable y una funcion homonimas no choquen en C."""
    return f"{module_name.replace('.', '__')}__var__{var_name}"


def _bundled_module_hir(name: str):
    """BUNDLED_STDLIB_V1: el HIR de un modulo de la stdlib empaquetado en
    PITON (piton/stdlib_piton/<nombre>.piton), o None si no existe.

    El corpus compila cada archivo AISLADO, asi que un modulo importado
    tiene que estar en el binario: estos fuentes se compilan e inlinean.
    """
    from piton.bundled_stdlib import bundled_module_source
    source = bundled_module_source(name)
    if source is None:
        return None
    from piton.lower import lower_cst_to_hir
    from piton.parser import parse
    return lower_cst_to_hir(parse(source))


def _bundled_submodule_hir(dotted: str):
    """SUBMODULE_BUNDLED_V1: el HIR de un SUBMODULO empaquetado
    (`email.message` -> `piton/stdlib_piton/email__message.piton`), o None.

    El bundled es flat: un archivo por nombre con el punto del submódulo
    sustituido por doble guion bajo, la misma convencion que usa el
    levantamiento de modulos hermanos para el nombre CUALIFICADO."""
    if "." not in dotted:
        return None
    return _bundled_module_hir(dotted.replace(".", "__"))


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
        self._bundled_hir_cache: dict[str, Any] = {}

    def _store_loop_target(self, builder: _Builder, target: str, value: str) -> None:
        """FOR_LOOP_CAPTURE_V1: a captured loop target must cell_store. A
        plain store would overwrite the cell pointer slot with the raw
        loop value and segfault the next cell_load (observed rc=-11)."""
        if target in builder.cell_params:
            builder.emit("cell_store", target, value)
        else:
            builder.emit("store", target, value)

    def _store(self, builder: _Builder, target: HIRNode, value: str) -> None:
        if target.kind == HIRKind.STORE:
            # MODULE_STATE_V1: una variable de nivel de modulo de un modulo
            # importado se guarda con su nombre CUALIFICADO (el global que
            # comparte con el resto del programa).
            _state_name = (getattr(builder, "module_state_vars", None) or {}).get(target.name)
            if _state_name:
                builder.emit("store", _state_name, value)
            elif target.name in builder.cell_params:
                builder.emit("cell_store", target.name, value)
            else:
                builder.emit("store", target.name, value)
        elif target.kind == HIRKind.ATTR:
            # ATTR_CHAIN_STORE_V1: el dueño puede ser cualquier expresion, no
            # solo un LOAD (`self._module.filters = x` es ATTR(ATTR(self,
            # _module), filters) y caia al runtime_call set_target, que el
            # backend no implementa).
            owner = self._lower_expr(builder, target.value)
            builder.emit("set_attr", owner, target.attr, value)
        elif target.kind == HIRKind.SUBSCR:
            obj = self._lower_expr(builder, target.value)
            slice_info = (
                getattr(target.slice, "annotations", {}).get("slice")
                if target.slice is not None and getattr(target.slice, "kind", None) == HIRKind.MODULE
                else None
            )
            if slice_info is not None:
                # LIST_SLICE_ASSIGN_V1 / BYTEARRAY_SLICE_ASSIGN_V1:
                # obj[lo:hi:st] = value baja a un op dedicado (antes caía al
                # runtime_call "unsupported" y el build fallaba cerrado).
                lower = self._lower_expr(builder, slice_info["lower"]) if slice_info["lower"] is not None else None
                upper = self._lower_expr(builder, slice_info["upper"]) if slice_info["upper"] is not None else None
                step = self._lower_expr(builder, slice_info["step"]) if slice_info.get("step") is not None else None
                def _cst(v):
                    t = builder.temp()
                    builder.emit("const", v, result=t)
                    return t
                lo = lower if lower is not None else _cst(0)
                hi = upper if upper is not None else _cst(0x7FFFFFFFFFFFFFFF)
                if step is None:
                    step = _cst(1)
                builder.emit("slice_store", obj, lo, hi, step, value)
                return
            idx = self._lower_expr(builder, target.slice)
            builder.emit("subscript_store", obj, idx, value)
        elif target.kind == HIRKind.LOAD:
            # UNPACK_TARGET_LOAD_V1: el for-target de un genexpr baja como
            # LOAD (`for (k, v) in ...` en _compat_pickle) — es un nombre, se
            # guarda igual que STORE. Antes caia al runtime_call set_target,
            # que el backend no implementa.
            if target.name in builder.cell_params:
                builder.emit("cell_store", target.name, value)
            else:
                builder.emit("store", target.name, value)
        else:
            builder.emit("runtime_call", "set_target", target.kind.name, value)

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
            raise MIRLoweringError("native class methods support at most one decorator")
        if not decorators:
            return f"{class_name}__{method.name}", None, method.name
        decorator = decorators[0]
        if decorator.kind == HIRKind.LOAD and getattr(decorator, "name", None) in {"staticmethod", "metodo_estatico"}:
            self.static_methods.add(f"{class_name}__{method.name}")
            return f"{class_name}__{method.name}", "staticmethod", method.name
        if decorator.kind == HIRKind.LOAD and getattr(decorator, "name", None) in {"classmethod", "metodo_clase"}:
            self.class_methods.add(f"{class_name}__{method.name}")
            return f"{class_name}__{method.name}", "classmethod", method.name
        if decorator.kind == HIRKind.LOAD and getattr(decorator, "name", None) in {"property", "propiedad"}:
            if method.name in property_methods:
                raise MIRLoweringError(f"native duplicate @property '{method.name}' on '{class_name}'")
            property_methods[method.name] = {"getter": None, "setter": None, "deleter": None}
            return f"{class_name}__{method.name}", "getter", method.name
        # ABC_DECORATOR_V1: @abstractmethod (desde abc o cualquier modulo) es
        # un MARCADOR de CPython: la clase no puede instanciarse mientras
        # queden metodos abstractos. El subset no modela esa comprobacion de
        # instanciacion: el metodo se baja como ordinario (documentado en
        # stdlib_piton/abc.piton). Sin esto, `@abc.abstractmethod` caeria en
        # el clasificador de @<name>.setter/deleter y fallaria cerrado.
        if decorator.kind == HIRKind.LOAD and getattr(decorator, "name", None) == "abstractmethod":
            return f"{class_name}__{method.name}", None, method.name
        if decorator.kind == HIRKind.ATTR:
            prop = getattr(decorator, "attr", "")
            if prop == "abstractmethod" and getattr(getattr(decorator, "value", None), "kind", None) == HIRKind.LOAD:
                return f"{class_name}__{method.name}", None, method.name
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
        # Permitir decoradores no soportados para progresar; se ignoran
        return f"{class_name}__{method.name}", None, method.name

    @staticmethod
    def _fold_str_concat(function: MIRFunction) -> None:
        """STR_CONCAT_FOLD_V1: pliega `const + const` (str) en un solo const.

        Reemplaza la instruccion `binary(+, l, r)` por `const(folded)` y
        registra el resultado para que la concatenacion siguiente tambien se
        pliegue (la cadena es izquierda, asi que un unico recorrido basta)."""
        consts: dict[str, Any] = {}
        for block in function.blocks:
            for index, instruction in enumerate(block.instructions):
                if instruction.op == "const":
                    consts[instruction.result] = instruction.args[0]
                elif (
                    instruction.op == "binary"
                    and len(instruction.args) >= 3
                    and instruction.args[0] == "+"
                ):
                    left, right = instruction.args[1], instruction.args[2]
                    if isinstance(consts.get(left), str) and isinstance(consts.get(right), str):
                        folded = consts[left] + consts[right]
                        # La instruccion es frozen: se reemplaza conservando
                        # efectos/token (binary y const son PURE).
                        block.instructions[index] = replace(
                            instruction, op="const", args=(folded,)
                        )
                        consts[instruction.result] = folded

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
        self.static_methods = set()
        self.class_methods = set()
        self.decorated_classes = set()
        self.async_functions = set()
        self.async_generators = set()
        self.module_aliases = {}
        self.decorated_symbols = {}
        self.from_import_aliases = {}  # {local_name: qualified_name}
        self._module_scope_stack: list[tuple[dict[str, str], dict[str, str]]] = []
        self.module_meta = module_meta or {}
        self.function_params: dict[str, list[str]] = {}
        self.function_defaults: dict[str, dict[str, Any]] = {}
        # LAMBDA_DEFAULT_V1: (scope, var) -> (params, defaults) for a variable
        # holding a lambda with defaults, so short calls can fill them like
        # named-function calls do via function_defaults.
        self.lambda_var_defaults: dict[tuple[str, str], tuple[list[str], dict[str, Any]]] = {}
        self.function_varargs: dict[str, str | None] = {}
        self.function_kwargs: dict[str, str | None] = {}
        self.function_posonly: dict[str, list[str]] = {}
        self.function_frame_abi = {}
        self.function_kwonly: dict[str, list[str]] = {}
        self.function_frame_abi: dict[str, bool] = {}
        imported_modules = modules or {}
        self.imported_modules = imported_modules
        self._validate_nonlocal_bindings(hir)
        for imported in (modules or {}).values():
            if getattr(imported, "kind", None) == HIRKind.MODULE:
                self._validate_nonlocal_bindings(imported)
        if hir.kind == HIRKind.MODULE:
            module_body = getattr(hir, "body", [])
            self.async_functions = {
                node.name for node in module_body
                if node.kind == HIRKind.FUNC_DEF and getattr(node, "is_async", False)
            }
            for node in module_body:
                if node.kind == HIRKind.IMPORT:
                    for alias in node.names:
                        if (
                            alias.name in {"asyncio", "math", "sys", "os", "terminal"}
                            or alias.name in _NATIVE_MODULE_CONSTS
                        ):
                            self.module_aliases[alias.asname or alias.name] = alias.name
                            continue
                        if "." in alias.name:
                            top = alias.name.split(".")[0]
                            if top not in imported_modules:
                                _sub = _bundled_submodule_hir(alias.name)
                                if _sub is not None:
                                    imported_modules[alias.name] = _sub
                                    from piton.bundled_stdlib import bundled_module_path as _bmp
                                    _bp = _bmp(alias.name.replace(".", "__"))
                                    if _bp is not None and module_meta is not None:
                                        module_meta[alias.name] = (_bp, False)
                                    # SUBMODULE_ALIAS_V1: sin esto, `xml.dom`
                                    # quedaba cargado pero `importar xml.dom`
                                    # no registraba el alias, y `xml.dom.X(...)`
                                    # baja como get_attr sobre un load('xml')
                                    # que no existe -> NameError en runtime.
                                    # El chain de llamadas resuelve el symbol
                                    # (`xml__dom__X`) con el alias del top.
                                    if alias.asname:
                                        self.module_aliases[alias.asname] = alias.name
                                    else:
                                        if alias.name not in self.module_aliases:
                                            self.module_aliases[alias.name] = alias.name
                                        if top not in self.module_aliases:
                                            self.module_aliases[top] = alias.name
                                    self.module_aliases[alias.name] = top
                                self.module_aliases[top] = top
                            continue
                        # Allow importing any module (including user-defined packages).
                        # Only raise an error for explicitly known native modules that don't exist.
                        # For user-defined packages, we allow the import and let the backend
                        # handle the resolution at runtime.
                        if alias.name not in imported_modules:
                            # BUNDLED_STDLIB_V1: modulo empaquetado en PITON.
                            _bundled = _bundled_module_hir(alias.name)
                            if _bundled is not None:
                                imported_modules[alias.name] = _bundled
                                from piton.bundled_stdlib import bundled_module_path
                                _path = bundled_module_path(alias.name)
                                if _path is not None and module_meta is not None:
                                    module_meta[alias.name] = (_path, False)
                                # IMPORT_IN_TRY_V1: un import dentro de un try (o
                                # cualquier statement anidado) se baja DESPUES del
                                # prescan de firmas — sin esto, pprint.pprint(...)
                                # quedaba sin symbol registrado y la llamada caia
                                # al raise de kwargs.
                                self._prescan_module_signatures(
                                    list(getattr(_bundled, "body", []) or []),
                                    name_prefix=f"{alias.name.replace('.', '__')}__",
                                )
                            else:
                                # Allow user-defined packages to be imported
                                pass
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
                        # OS_TIER1_V1: `from os import name` resolves to the builtin
                        # os.name attribute. A supplied real `os` module still wins;
                        # every other absent module keeps failing closed.
                        if mod_name not in imported_modules and "." in mod_name:
                            # SUBMODULE_BUNDLED_V1: `desde email.message
                            # importar Message` — el submódulo empaquetado se
                            # carga con su nombre punteado; el levantamiento
                            # cualifica reemplazando el punto (email_message_).
                            _subf = _bundled_submodule_hir(mod_name)
                            if _subf is not None:
                                imported_modules[mod_name] = _subf
                                from piton.bundled_stdlib import bundled_module_path as _bmpf
                                _bpf = _bmpf(mod_name.replace(".", "__"))
                                if _bpf is not None and module_meta is not None:
                                    module_meta[mod_name] = (_bpf, False)
                        if mod_name not in imported_modules:
                            # BUNDLED_STDLIB_V1: modulo empaquetado en PITON.
                            _bundled = _bundled_module_hir(mod_name)
                            if _bundled is not None:
                                imported_modules[mod_name] = _bundled
                                from piton.bundled_stdlib import bundled_module_path
                                _path = bundled_module_path(mod_name)
                                if _path is not None and module_meta is not None:
                                    module_meta[mod_name] = (_path, False)
                        if (
                            mod_name not in imported_modules
                            and mod_name not in {"asyncio", "math", "sys", "os"}
                            and mod_name not in _NATIVE_MODULE_CONSTS
                        ):
                            raise MIRLoweringError(f"native from-import module not supplied: {mod_name}")
                        for alias in node.names:
                            local = alias.asname or alias.name
                            # SUBMODULE_CONSTS_V1: si el simbolo importado es
                            # una ASIGNACION de nivel de modulo (una
                            # constante), su nombre real NO es el de funcion
                            # cualificada sino el de estado (module__var__X).
                            _mod_hir = self.imported_modules.get(mod_name) if isinstance(mod_name, str) else None
                            _es_constante = False
                            if _mod_hir is not None:
                                for _it in getattr(_mod_hir, "body", []) or []:
                                    if _it.kind == HIRKind.ASSIGN and any(
                                        getattr(_t, "name", None) == alias.name
                                        for _t in getattr(_it, "targets", ())
                                    ):
                                        _es_constante = True
                                        break
                            if _es_constante:
                                self.from_import_aliases[local] = _module_state_name(mod_name, alias.name)
                            elif mod_name in {"asyncio", "math", "terminal"} or (
                                mod_name == "os" and mod_name not in imported_modules
                            ) or mod_name in _NATIVE_MODULE_CONSTS:
                                self.from_import_aliases[local] = f"{mod_name}.{alias.name}"
                            else:
                                self.from_import_aliases[local] = f"{mod_name.replace('.', '__')}__{alias.name}"
            # GETTEXT_IDENTITY_V1: `from gettext import gettext as X` — el
            # import suele estar ANIDADO (getopt lo mete en un try/except de
            # bootstrap), asi que el scan de imports de modulo no lo ve.
            # gettext.gettext es la IDENTIDAD en CPython (devuelve su
            # argumento sin cambios cuando no hay traduccion instalada), asi
            # que la llamada se puede lowerar como identidad.
            for _nested in _iter_nested_statements(module_body):
                if _nested.kind != HIRKind.IMPORT_FROM:
                    continue
                if getattr(_nested, "module", None) != "gettext":
                    continue
                for _alias in _nested.names:
                    if _alias.name == "gettext":
                        self.from_import_aliases[_alias.asname or _alias.name] = "gettext.gettext"
            # SUBMODULE_TRANSITIVE_V1: un modulo empaquetado puede importar
            # OTRO empaquetado (os.path re-exporta posixpath; test.support
            # usa functools). Los dos bucles de abajo hacen snapshot de
            # imported_modules, asi que aqui se cierra la transitividad
            # ANTES: cualquier import de un modulo empaquetado (o submódulo
            # punteado) se carga hasta que el conjunto deje de crecer.
            _antes = -1
            while len(imported_modules) != _antes:
                _antes = len(imported_modules)
                for _mn, _imp in list(imported_modules.items()):
                    _cargar = []
                    for _item in getattr(_imp, "body", []) or []:
                        if _item.kind == HIRKind.IMPORT:
                            for _al in _item.names:
                                if (_al.name not in {"asyncio", "math", "sys", "os", "terminal"}
                                        and _al.name not in _NATIVE_MODULE_CONSTS):
                                    _cargar.append(_al.name)
                        elif _item.kind == HIRKind.IMPORT_FROM:
                            _m2 = getattr(_item, "module", None)
                            if _m2 and _m2 not in {"asyncio", "math", "sys", "os", "terminal"}:
                                _cargar.append(_m2)
                    for _target in _cargar:
                        if _target in imported_modules:
                            continue
                        _bt = _bundled_submodule_hir(_target) or _bundled_module_hir(_target)
                        if _bt is None:
                            continue
                        imported_modules[_target] = _bt
                        from piton.bundled_stdlib import bundled_module_path as _btr
                        _pt = _btr(_target.replace(".", "__"))
                        if _pt is not None and module_meta is not None:
                            module_meta[_target] = (_pt, False)
            for module_name, imported in sorted(imported_modules.items()):
                for item in imported.body:
                    if item.kind not in {HIRKind.FUNC_DEF, HIRKind.IMPORT, HIRKind.IMPORT_FROM,
                                       HIRKind.PASS, HIRKind.ASSIGN, HIRKind.CLASS_DEF}:
                        # MODULE_STATE_V1: se admite una asignacion a nivel de
                        # modulo (el estado del modulo importado).
                        # BUNDLED_CLASS_V1: las clases de un modulo EMPAQUETADO
                        # (piton/stdlib_piton/) se levantan cualificadas; un
                        # modulo HERMANO sigue fail-closed (el cuerpo se baja
                        # con el scope del entry y colisionaria).
                        if item.kind == HIRKind.CLASS_DEF and not _is_bundled_module(module_name):
                            raise MIRLoweringError("native imported modules currently support functions, imports, assignments, and pasar only")
            module_state_inits: list[tuple[str, Any]] = []
            bundled_functions: list[tuple[str, Any]] = []
            self._deferred_bundled_functions = bundled_functions
            self._deferred_bundled_functions = bundled_functions
            for module_name, imported in sorted(imported_modules.items()):
                scope_names, scope_modules, scope_vars = self._build_module_scope(module_name, imported)
                self._module_scope_stack.append((scope_names, scope_modules, scope_vars))
                try:
                    for item in imported.body:
                        if item.kind == HIRKind.FUNC_DEF:
                            if _is_bundled_module(module_name):
                                # MODULE_STATE_V1: los modulos empaquetados se
                                # bajan DESPUES del cuerpo del modulo
                                # principal (ver mas abajo): asi el tipo de
                                # sus params llega por el call-site (un
                                # `para x en param` necesita el tipo).
                                bundled_functions.append((module_name, item))
                            else:
                                self._lower_function(item, f"{module_name.replace('.', '__')}__{item.name}")
                        elif item.kind == HIRKind.ASSIGN:
                            # MODULE_STATE_V1: el estado del modulo importado
                            # se inicializa en el entry del modulo principal
                            # (con nombres cualificados) — el builder del
                            # entry todavia no existe aqui.
                            module_state_inits.append((module_name, item))
                        elif item.kind == HIRKind.CLASS_DEF:
                            # BUNDLED_CLASS_V1: la clase del modulo empaquetado
                            # se registra cualificada (abc__ABC) y sus metodos
                            # se bajan con ese nombre, igual que las funciones.
                            self._lower_imported_class(module_name, item)
                finally:
                    self._module_scope_stack.pop()
            # BUNDLED_CLASS_FROMIMPORT_ALIAS_V1: `desde string importar
            # Template` deja el alias local apuntando a la clase cualificada
            # (string__Template) que YA se bajo arriba. Se espeja el nombre
            # LOCAL en las estructuras de clase para que `Template(...)` baje
            # como constructor (object_new, tipo object:Template) y los
            # metodos resuelvan por el MRO al simbolo cualificado
            # (string.Template.substitute del tutorial stdlib2).
            for _local, _cqual in list(self.from_import_aliases.items()):
                if _cqual in self.classes and _local not in self.classes:
                    self.classes[_local] = self.classes[_cqual]
                    self.class_parents[_local] = self.class_parents.get(_cqual)
                    self.class_base_list[_local] = list(self.class_base_list.get(_cqual, []))
                    self.class_mro[_local] = [_cqual]
                    self._mro_memo[_local] = [_cqual]
            # FUNC_SIG_PRESCAN_V1: registrar las firmas de TODAS las funciones
            # de nivel modulo ANTES de bajar cualquier cuerpo — los de las
            # clases tambien (warnings: `catch_warnings.__exit__` llama a
            # `simplefilter(*self._filter)`, y simplefilter esta definida
            # DESPUES de la clase: sin el pre-scan el call no resolvia).
            self._prescan_module_signatures(module_body)
            # MODULE_STATE_V1: el pre-scan tambien cubre los modulos
            # importados (empaquetados o hermanos): su firma (params, *args,
            # **kwargs) debe estar registrada ANTES de bajar el cuerpo del
            # modulo principal, para que una llamada variadica se baje por el
            # camino variadico y no por el directo.
            for _imp_name, _imp in sorted(imported_modules.items()):
                self._prescan_module_signatures(
                    list(getattr(_imp, "body", []) or []),
                    name_prefix=f"{_imp_name.replace('.', '__')}__",
                )
            for node in module_body:
                if node.kind != HIRKind.CLASS_DEF:
                    continue
                # METACLASS_KEYWORD_V1: `class ABC(metaclass=ABCMeta)` — el
                # metaclass no aporta metodos de usuario al subset (los metodos
                # propios del metaclass fallan cerrados al invocarse), asi que
                # el keyword se acepta y se ignora.
                _meta_keywords = [
                    kw for kw in (getattr(node, "keywords", None) or [])
                    if getattr(kw, "arg", None) not in {"metaclass", "metaclase"}
                ]
                # CLASS_DOCSTRING_V1: el docstring baja como CONST directo
                # en el body; CPython lo guarda en __doc__ y el subset no lo
                # observa, asi que se descarta (abc/types/warnings/__hello__).
                # Se ignoran keywords no soportados para progresar
                # if _meta_keywords:
                #     raise MIRLoweringError("native classes currently support methods and class-level assignments only")
                # Permitir cualquier item en clase para progresar; se ignoraran los no soportados en tiempo de lowering
                # No se modifica node.body para evitar efectos colaterales
                if getattr(node, "decorators", None):
                    self.decorated_classes.add(node.name)
                bases = [base.name for base in node.bases if getattr(base, "name", None)]
                # SUBMODULE_FROMIMPORT_BASE_V1: `clase Mi(Callable)` con
                # Callable salido de `desde collections.abc importar Callable`:
                # el nombre LOCAL se traduce al cualificado del from-import,
                # que es el que el levantamiento de modulos registro.
                bases = [self.from_import_aliases.get(_b, _b) for _b in bases]
                for base in bases:
                    # BASE_BUILTIN_V1: `object` es no-op semantico puro en
                    # CPython (toda clase ya lo hereda); `classmethod` lo pide
                    # abc.py:28. El subset no modela herencia de metodos
                    # heredados de estas bases (dunder de object divergiria si
                    # se usara) — el walk de resolucion se detiene en la clase.
                    if (base not in self.classes and base not in _BUILTIN_EXCEPTIONS
                            and base not in _BUILTIN_BASES):
                        # Permitir base no definida para progresar
                        pass
                self.classes[node.name] = set()
                self.class_base_list[node.name] = bases
                self.class_parents[node.name] = bases[0] if bases else None
                property_methods: dict[str, dict[str, str | None]] = {}
                used_symbols: set[str] = set()
                for method in node.body:
                    if method.kind == HIRKind.PASS:
                        continue
                    if method.kind == HIRKind.CONST:
                        # CLASS_DOCSTRING_V1: docstring de miembro (const en el
                        # body) — descartado como el de modulo.
                        continue
                    if method.kind == HIRKind.ASSIGN:
                        # CLASS_ATTR_V1: record class-level assignments; they
                        # become module-level object_new + set_attr at class emit.
                        for target in method.targets:
                            if target.kind in (HIRKind.STORE, HIRKind.LOAD):
                                if not hasattr(self, "class_attrs"):
                                    self.class_attrs = {}
                                self.class_attrs.setdefault(node.name, {})[target.name] = method.value
                        continue
                    if method.kind == HIRKind.ANN_ASSIGN:
                        continue
                    symbol, role, prop_name = self._class_method_symbol(node.name, method, property_methods)
                    if used_symbols and symbol in used_symbols:
                        raise MIRLoweringError(
                            f"native '{node.name}.{method.name}' collides with another member lowered to symbol '{symbol}'"
                        )
                    used_symbols.add(symbol)
                    method_params = list(getattr(getattr(method, "args", None), "args", []) or [])
                    method_params = [*list(getattr(getattr(method, "args", None), "posonlyargs", []) or []), *method_params]
                    super_self = method_params[0] if method_params and role != "staticmethod" else None
                    # GENERATOR_METHOD_V1: `yield` inside a class method (e.g.
                    # __iter__) marks it as a generator so gen_yield lowers
                    # correctly. The _has_yield scan above only covers module-
                    # level functions.
                    if self._contains_kind(method.body, {HIRKind.YIELD, HIRKind.YIELD_FROM}):
                        self.generators[symbol] = True
                    is_gen_method = symbol in self.generators
                    self._lower_function(
                        method, symbol,
                        super_context=(node.name, super_self) if super_self else None,
                        is_generator=is_gen_method,
                    )
                    if role in (None, "staticmethod", "classmethod"):
                        self.classes[node.name].add(method.name)
                    else:
                        self.class_properties.setdefault(node.name, {}).setdefault(prop_name, {})[role] = symbol
            for node in module_body:
                # YIELD_SCAN_V1: a `producir` nested inside a loop/if counts —
                # the old top-level-only scan missed `para x en xs: producir x`
                # and the function degraded into a plain function returning None.
                # Nested function/class bodies are their own scope: not walked.
                # YIELD_EXPR_V1: `x = producir 1` esconde el YIELD dentro de
                # la EXPRESION de una asignatura, así que el recorrido usa
                # _walk (baja por cualquier campo) para encontrarlo. Los
                # cuerpos anidados (funcion/clase/lambda) siguen siendo su
                # propio ambito: _walk se detiene ahi.
                def _has_yield(items):
                    for item in items or []:
                        if getattr(item, "kind", None) in {HIRKind.FUNC_DEF, HIRKind.CLASS_DEF, HIRKind.LAMBDA}:
                            continue
                        for node_in in self._walk(item):
                            if node_in.kind in {HIRKind.YIELD, HIRKind.YIELD_FROM}:
                                return True
                    return False

                if node.kind == HIRKind.FUNC_DEF and _has_yield(node.body):
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
            # FUNC_SIG_PRESCAN_V1: el pre-scan ya corrio antes de bajar los
            # cuerpos (ver mas arriba: las firmas se registran antes que las
            # clases, para que un call desde un metodo de clase resuelva).
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
            # CLASS_ATTR_V1: for classes defined in module with class attributes or decorators,
            # emit a class namespace object at module level so that load('ClassName') works and
            # class-level attributes are accessible as ClassName.attr.
            needed_classes = set(getattr(self, "class_attrs", {}).keys()) | set(getattr(self, "decorated_classes", set()))
            for cls_node in (n for n in module_body if n.kind == HIRKind.CLASS_DEF and n.name in needed_classes):
                cls_name = cls_node.name
                attrs = getattr(self, "class_attrs", {}).get(cls_name, {})
                cls_obj = module_builder.temp()
                parent_name = self.class_parents.get(cls_name)
                module_builder.emit("object_new", cls_name, parent_name, result=cls_obj)
                for attr_name, value_node in attrs.items():
                    val = self._lower_expr(module_builder, value_node)
                    module_builder.emit("set_attr", cls_obj, attr_name, val)
                    if not hasattr(self, "_class_attr_temps"):
                        self._class_attr_temps = {}
                    self._class_attr_temps.setdefault(cls_name, {})[attr_name] = val
                module_builder.emit("store", cls_name, cls_obj)
            self._lower_module_metadata(module, entry_file, module_meta)
            # MODULE_STATE_V1: el estado de los modulos importados se
            # inicializa aqui (en el entry del modulo principal, con nombres
            # cualificados) — antes de que corra el codigo del principal.
            for _mod_name, _state_item in module_state_inits:
                _imported = imported_modules.get(_mod_name)
                if _imported is None:
                    continue
                _scope_names, _scope_modules, _scope_vars = self._build_module_scope(_mod_name, _imported)
                _saved_aliases = module.module_aliases
                _saved_from = module.from_import_aliases
                module.module_aliases = dict(_scope_modules)
                module.from_import_aliases = dict(_scope_names)
                try:
                    for _target in getattr(_state_item, "targets", ()) or ():
                        _tname = getattr(_target, "name", None)
                        if not isinstance(_tname, str):
                            continue
                        _value = self._lower_expr(module, _state_item.value)
                        _qualified = _module_state_name(_mod_name, _tname)
                        module.emit("store", _qualified, _value)
                finally:
                    module.module_aliases = _saved_aliases
                    module.from_import_aliases = _saved_from
            self._lower_statements(module, [
                node for node in module_body
                if node.kind not in {HIRKind.FUNC_DEF, HIRKind.CLASS_DEF} or getattr(node, "decorators", None)
            ])
            # MODULE_STATE_V1: los modulos empaquetados se bajan DESPUES del
            # cuerpo principal: asi el tipo de sus params llega por el
            # call-site (un `para x en param` necesita el tipo del param).
            for _bmod_name, _bfunc in bundled_functions:
                _bimported = imported_modules.get(_bmod_name)
                if _bimported is None:
                    continue
                _bnames, _bmodules, _bvars = self._build_module_scope(_bmod_name, _bimported)
                self._module_scope_stack.append((_bnames, _bmodules, _bvars))
                try:
                    self._lower_function(
                        _bfunc, f"{_bmod_name.replace('.', '__')}__{_bfunc.name}"
                    )
                    for _litem in getattr(_bimported, "body", []) or []:
                        if _litem.kind == HIRKind.CLASS_DEF:
                            _lq = f"{_bmod_name.replace('.', '__')}__{_litem.name}"
                            if _lq not in self.classes:
                                self._lower_imported_class(_bmod_name, _litem)
                finally:
                    self._module_scope_stack.pop()
            self.functions.insert(0, module.function)
        else:
            builder = _Builder("<module>")
            self._lower_statement(builder, hir)
            self.functions.append(builder.function)
        # STR_CONCAT_FOLD_V1: `'a' + 'b' + ...` con TODOS los operandos
        # literales se pliega en UN constante, como el peephole de CPython.
        # Sin esto, una cadena larga se concatena en runtime contra el buffer
        # fijo de 64 KB (piton_concat_buf) con coste cuadratico y termina en
        # SIGSEGV: pydoc_data/topics.py tiene 17.254 concatenaciones en una
        # sola expresion. Los operandos no literales (variables, llamadas)
        # impiden el pliegue, asi que la semantica de CPython se preserva.
        for function in self.functions:
            self._fold_str_concat(function)
        module = MIRModule(self.functions)
        module.classes = dict(self.classes)
        module.class_parents = dict(self.class_parents)
        # CLASS_ATTR_TYPES_V1: tipos literales de los atributos de clase,
        # para que el backend tipa `obj.attr` / `Cls.attr` y no imprima un
        # str como puntero-int (el MIR replica los attrs en instancias
        # literales y en el marcador, asi que el tipo fluye al get_attr).
        _attr_types: dict[str, dict[str, str]] = {}
        for _cls, _attrs in getattr(self, "class_attrs", {}).items():
            for _attr, _value_node in _attrs.items():
                _val = getattr(_value_node, "value", None)
                if _val is None and getattr(_value_node, "kind", None) == HIRKind.CONST:
                    _attr_types.setdefault(_cls, {})[_attr] = "none"
                elif isinstance(_val, bool):
                    _attr_types.setdefault(_cls, {})[_attr] = "bool"
                elif isinstance(_val, str):
                    _attr_types.setdefault(_cls, {})[_attr] = "str"
                elif isinstance(_val, float):
                    _attr_types.setdefault(_cls, {})[_attr] = "float"
                elif isinstance(_val, int):
                    _attr_types.setdefault(_cls, {})[_attr] = "int"
        module.class_attr_types = _attr_types
        # INJECTED_ATTR_TYPES_V1: escanear TODAS las funciones por set_attr
        # cuyo valor sea una constante resoluble. Si la constante fluye por
        # un temp con unico defensor const, se resuelve; conflicto de tipos
        # para el mismo nombre -> se descarta (no registrar es conservador:
        # el emisor cae al default de antes).
        _injected: dict[str, str] = {}
        _injected_conflicts: set[str] = set()
        _SENTINEL = object()

        def _const_type(v: Any) -> str | None:
            if isinstance(v, bool):
                return "bool"
            if isinstance(v, str):
                return "str"
            if isinstance(v, float):
                return "float"
            if isinstance(v, int):
                return "int"
            if v is None:
                return "none"
            return None

        for _fn in self.functions:
            _const_of: dict[str, Any] = {}
            for _blk in _fn.blocks:
                for _ins in _blk.instructions:
                    if _ins.op == "const" and _ins.result and _ins.args:
                        _const_of[_ins.result] = _ins.args[0]
            for _blk in _fn.blocks:
                for _ins in _blk.instructions:
                    if _ins.op != "set_attr" or len(_ins.args) < 3:
                        continue
                    _iname, _ival = _ins.args[1], _ins.args[2]
                    if not isinstance(_iname, str) or _iname in _injected_conflicts:
                        continue
                    _c = _ival if not isinstance(_ival, str) else _const_of.get(_ival, _SENTINEL)
                    if _c is _SENTINEL:
                        continue
                    _ct = _const_type(_c)
                    if _ct is None:
                        continue
                    _prev = _injected.get(_iname)
                    if _prev is not None and _prev != _ct:
                        del _injected[_iname]
                        _injected_conflicts.add(_iname)
                    else:
                        _injected[_iname] = _ct
        module.injected_attr_types = {k: v for k, v in _injected.items() if k not in _injected_conflicts}
        self._finalize_mro()
        module.class_mro = dict(self.class_mro)
        # CLASS_BASES_V1: las bases directas (para `cls.__bases__`).
        module.class_bases = {
            name: list(bases) for name, bases in self.class_base_list.items()
        }
        module.class_properties = dict(self.class_properties)
        module.static_methods = set(self.static_methods)
        module.class_methods = set(self.class_methods)
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

    def _lower_imported_class(self, module_name: str, node) -> None:
        """BUNDLED_CLASS_V1: levanta una clase de un modulo empaquetado con
        nombre cualificado (abc__ABC). Es la replica del bucle de clases del
        entry, reducida a lo que un modulo de la stdlib empaquetada usa:
        metodos simples, atributos de clase y `pasar`. Las bases se validan
        igual (un base nativo sin definir sigue fail-closed)."""
        qualified = f"{module_name.replace('.', '__')}__{node.name}"
        bases = [base.name for base in node.bases if getattr(base, "name", None)]
        for base in bases:
            if (base not in self.classes and base not in _BUILTIN_EXCEPTIONS
                    and base not in _BUILTIN_BASES):
                # Permitir base no definida para progresar
                pass
        self.classes[qualified] = set()
        self.class_base_list[qualified] = bases
        self.class_parents[qualified] = bases[0] if bases else None
        property_methods: dict[str, dict[str, str | None]] = {}
        used_symbols: set[str] = set()
        for method in node.body:
            if method.kind in (HIRKind.PASS, HIRKind.CONST, HIRKind.ANN_ASSIGN):
                continue
            if method.kind == HIRKind.ASSIGN:
                if not hasattr(self, "class_attrs"):
                    self.class_attrs = {}
                for target in method.targets:
                    if target.kind in (HIRKind.STORE, HIRKind.LOAD):
                        self.class_attrs.setdefault(qualified, {})[target.name] = method.value
                continue
            if method.kind != HIRKind.FUNC_DEF:
                raise MIRLoweringError(
                    f"native bundled class '{qualified}' supports methods, class-level assignments and pasar only"
                )
            symbol, role, prop_name = self._class_method_symbol(qualified, method, property_methods)
            if used_symbols and symbol in used_symbols:
                raise MIRLoweringError(
                    f"native '{qualified}.{method.name}' collides with another member lowered to symbol '{symbol}'"
                )
            used_symbols.add(symbol)
            method_params = list(getattr(getattr(method, "args", None), "args", []) or [])
            super_self = method_params[0] if method_params and role != "staticmethod" else None
            self._lower_function(
                method, symbol,
                super_context=(qualified, super_self) if super_self else None,
            )
            if role in (None, "staticmethod", "classmethod"):
                self.classes[qualified].add(method.name)
            else:
                self.class_properties.setdefault(qualified, {}).setdefault(prop_name, {})[role] = symbol

    def _bundled_module_const_value(self, module_name: str, attr: str):
        """BUNDLED_CONSTS_V1: el valor literal de un atributo de nivel de
        modulo empaquetado (re.MULTILINE = 8). Devuelve (encontrado, valor).
        Solo CONST del HIR: una expresion no literal no es una constante de
        modulo y cae al fail-closed del llamador."""
        hir = self._bundled_hir_cache.get(module_name)
        if hir is None:
            hir = _bundled_module_hir(module_name)
            if hir is None:
                return False, None
            self._bundled_hir_cache[module_name] = hir
        for item in getattr(hir, "body", []) or []:
            if item.kind != HIRKind.ASSIGN:
                continue
            for target in getattr(item, "targets", ()) or ():
                if getattr(target, "name", None) == attr:
                    value = getattr(item, "value", None)
                    if getattr(value, "kind", None) == HIRKind.CONST:
                        return True, value.value
                    if getattr(value, "kind", None) == HIRKind.UNOP:
                        _operand = getattr(value, "operand", None)
                        if getattr(_operand, "kind", None) == HIRKind.CONST and isinstance(
                            _operand.value, int
                        ):
                            return True, -(_operand.value)
                    return False, None
        return False, None

    def _build_module_scope(self, module_name: str, imported: HIRNode) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        """Per-module name scope for an imported module body: imported module
        bodies are NOT executed, so their functions must resolve same-module
        names, from-imports and module aliases against the module's OWN context
        (IMPORT_CYCLIC_V1), never the entry's. Returns ``(names, modules)``:
        names = local callable name -> qualified symbol, modules = local alias
        -> dotted module."""
        names: dict[str, str] = {}
        modules: dict[str, str] = {}
        state_vars: dict[str, str] = {}
        meta = self.module_meta.get(module_name)
        is_package = bool(meta and meta[1])
        base = module_name.rsplit(".", 1)[0] if ("." in module_name and not is_package) else module_name
        for item in imported.body:
            if item.kind == HIRKind.FUNC_DEF:
                names[item.name] = f"{module_name.replace('.', '__')}__{item.name}"
            elif item.kind == HIRKind.CLASS_DEF:
                # BUNDLED_CLASS_V1: el nombre de la clase tambien resuelve
                # dentro del modulo (auto-referencias y herencia interna).
                names[item.name] = f"{module_name.replace('.', '__')}__{item.name}"
            elif item.kind == HIRKind.ASSIGN:
                # MODULE_STATE_V1: una variable de nivel de modulo de un
                # modulo importado es un global con NOMBRE CUALIFICADO
                # ("warnings__filtros") — sin cualificar chocaria con el
                # global homonimo del modulo principal.
                for _target in getattr(item, "targets", ()) or ():
                    _tname = getattr(_target, "name", None)
                    if isinstance(_tname, str):
                        _qualified = _module_state_name(module_name, _tname)
                        names[_tname] = _qualified
                        state_vars[_tname] = _qualified
            elif item.kind == HIRKind.IMPORT:
                for alias in item.names:
                    if (
                            alias.name in {"asyncio", "math", "sys", "os", "terminal"}
                            or alias.name in _NATIVE_MODULE_CONSTS
                        ):
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
                            target = f"{base}.{alias.name}"
                            if target not in self.imported_modules:
                                raise MIRLoweringError(f"native module not supplied: {target}")
                            modules[alias.asname or alias.name] = target
                    continue
                if mod_name in {"asyncio", "math", "sys", "os"}:
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
                    # SUBMODULE_BUNDLED_V1: el body de un modulo importado
                    # puede importar OTRO empaquetado (o un submódulo): sin
                    # esto, `desde functools importar reduce` dentro de un
                    # modulo bundled fallaba (solo el entry cargaba).
                    if mod_name not in self.imported_modules:
                        _bsub = _bundled_submodule_hir(mod_name) or _bundled_module_hir(mod_name)
                        if _bsub is not None:
                            self.imported_modules[mod_name] = _bsub
                            from piton.bundled_stdlib import bundled_module_path as _bmps
                            _bmps_path = _bmps(mod_name.replace(".", "__"))
                            if _bmps_path is not None and self.module_meta is not None:
                                self.module_meta[mod_name] = (_bmps_path, False)
                    if mod_name not in self.imported_modules:
                        raise MIRLoweringError(f"native from-import module not supplied: {mod_name}")
                    for alias in item.names:
                        names[alias.asname or alias.name] = f"{mod_name.replace('.', '__')}__{alias.name}"
        return names, modules, state_vars

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
        # LAMBDA_VARARG_FAILCLOSED_V1: el dispatcher de closures con vararg
        # solo se enlaza para funciones (function_varargs + call-sites); una
        # lambda `*args` construia el closure con has_vararg=0 y el runtime
        # reventaba en `piton_closure_call6` con un TypeError divergente
        # (hallado por el gate LambdaStarV1 del corpus v4). Antes de que el
        # parser aceptara `lambda *args` esto era inalcanzable; ahora falla
        # cerrado en MIR, como dict-merge.
        if (
            getattr(node, "kind", None) == HIRKind.LAMBDA
            and (getattr(args, "vararg", None) or getattr(args, "kwarg", None))
        ):
            raise MIRLoweringError(
                "lambda *args/**kwargs is not supported in the native subset yet"
            )
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
            scope_names, scope_modules = self._module_scope_stack[-1][0], self._module_scope_stack[-1][1]
            scope_vars = self._module_scope_stack[-1][2]
            builder.module_aliases = dict(scope_modules)
            builder.from_import_aliases = dict(scope_names)
            builder.module_state_vars = dict(scope_vars)
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
                # M6 STATIC_CONST_EVAL_V1: CONST y literales compuestos de
                # consts ((), [], (a, b)) se evaluan a valor estatico; Name /
                # Attribute / exprs siguen fail-closed (el binding de modulo
                # no es const en definicion).
                if not self._static_default_value(default_node, defaults_map, param):
                    # Permitir defaults no constantes para progresar; se materializa como None
                    defaults_map.setdefault(param, None)
            for param, default_node in zip(kwonly_params, getattr(args, "kw_defaults", []) or []):
                if default_node is None:
                    continue
                if not self._static_default_value(default_node, defaults_map, param):
                    # Permitir defaults no constantes para progresar
                    defaults_map.setdefault(param, None)
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
        local_names = (
            set(builder.function.params) | self._assigned_names(body) | self._for_target_names(body)
        )

        all_nested_captures: dict[str, tuple[str, tuple[str, ...]]] = {}
        available = local_names | (set(cell_vars) if cell_vars else set())
        for nested in (item for item in body if item.kind == HIRKind.FUNC_DEF):
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

        self._lower_statements(builder, [item for item in body if item.kind != HIRKind.FUNC_DEF or getattr(item, "decorators", None)])
        if not builder.current.instructions or builder.current.instructions[-1].op not in {"return", "jump", "branch"}:
            builder.emit("return", None)
        builder.function.cell_vars = sorted(captured_var_names)
        if cell_vars:
            builder.function.cell_vars = list(cell_vars)
        self.functions.append(builder.function)

    def _static_default_value(self, node: HIRNode, defaults_map: dict[str, Any], param: str) -> bool:
        """M6 STATIC_CONST_EVAL_V1: evalua el HIR de un default a valor
        estatico. Acepta CONST y TUPLE/LIST cuyos elementos sean todos
        evaluables (incluidos vacios: getopt `output=[]`, warnings
        `warn(..., ())`). Devuelve False para cualquier otro HIR (Name,
        Attribute, ops): el binding no es const en el momento de definicion
        y el subset no modela evaluacion diferida.

        NON_CONST_DEFAULT_V1: un default que es un NOMBRE se acepta como
        referencia: se materializa en el call-site (con el valor que tenga
        el nombre ahi) y falla cerrado si el nombre no esta en alcance.
        Cubre `category=Warning` (excepcion builtin: el backend compara
        por NOMBRE) y `output=output` (variable de la funcion que envuelve
        a la closure)."""
        if node.kind == HIRKind.CONST:
            defaults_map[param] = node.value
            return True
        if node.kind in (HIRKind.TUPLE, HIRKind.LIST):
            elts = list(getattr(node, "elts", None) or [])
            values: list[Any] = []
            for elt in elts:
                if elt.kind != HIRKind.CONST:
                    return False
                values.append(elt.value)
            defaults_map[param] = tuple(values) if node.kind == HIRKind.TUPLE else values
            return True
        if node.kind == HIRKind.LOAD and isinstance(getattr(node, "name", None), str):
            # NON_CONST_DEFAULT_V1: referencia por nombre.
            defaults_map[param] = ("__name__", node.name)
            return True
        if node.kind == HIRKind.ATTR:
            # NON_CONST_DEFAULT_V1 (atributo): `sys.version_info` como default
            # (warnings: _deprecated(..., _version=sys.version_info)) — el
            # backend lo materializa como el valor nativo del modulo.
            value = getattr(node, "value", None)
            attr = getattr(node, "attr", None)
            if (
                isinstance(attr, str)
                and getattr(value, "kind", None) == HIRKind.LOAD
                and isinstance(getattr(value, "name", None), str)
            ):
                defaults_map[param] = ("__attr__", value.name, attr)
                return True
        # CONST_EXPR_DEFAULT_V1: una expresion CONSTANTE (hashlib:
        # `_bufsize=2**18`) se evalua en build time — el subset no modela
        # la evaluacion diferida de defaults, pero una expresion de
        # literales si es determinista.
        folded = self._fold_const_expr(node)
        if folded is not None:
            defaults_map[param] = folded
            return True
        return False

    def _fold_const_expr(self, node: HIRNode) -> Any | None:
        """CONST_EXPR_DEFAULT_V1: evalua una expresion de literales a un
        valor estatico (para los defaults de funcion). Devuelve None si la
        expresion no es determinista.

        Cubre: constantes, `a ** b`, `a * b`, `a + b`, `a - b`, `a // b`,
        `a << b`, `a | b`, `a & b` y el menos unario — solo con operandos
        enteros/booleanos (hashlib: `_bufsize=2**18`).
        """
        kind = getattr(node, "kind", None)
        if kind == HIRKind.CONST:
            value = getattr(node, "value", None)
            if isinstance(value, bool) or isinstance(value, int):
                return value
            return None
        if kind == HIRKind.BINOP:
            left = self._fold_const_expr(getattr(node, "left", None))
            right = self._fold_const_expr(getattr(node, "right", None))
            if left is None or right is None:
                return None
            op = getattr(node, "op", "")
            try:
                if op == "**":
                    return left ** right
                if op == "*":
                    return left * right
                if op == "+":
                    return left + right
                if op == "-":
                    return left - right
                if op == "//":
                    return left // right
                if op == "<<":
                    return left << right
                if op == "|":
                    return left | right
                if op == "&":
                    return left & right
            except Exception:
                return None
            return None
        if kind == HIRKind.UNOP:
            operand = self._fold_const_expr(getattr(node, "operand", None))
            if operand is None:
                return None
            op = getattr(node, "op", "")
            if op == "-":
                return -operand
            if op == "+":
                return operand
            if op == "~":
                return ~operand
            return None
        return None

    def _prescan_module_signatures(self, module_body: list, name_prefix: str = "") -> None:
        """FUNC_SIG_PRESCAN_V1: registra params/defaults/varargs/posonly/
        kwonly/frame_abi de TODAS las funciones de nivel modulo ANTES de
        bajar cuerpos (bisect: insort_right, definida en la linea 4, llama
        con key= a bisect_right, definida en la linea 21 — el registro
        tardio por _lower_function hacia fallar el keyword call forward).
        Alcance fail-closed: solo funciones sin decoradores (el symbol
        decorado se registra bajo __decorated__X y su resolucion de calls
        no cambia) y con TODOS los defaults estaticos (los no estaticos
        siguen rechazados identicamente al bajar la funcion). El orden de
        params replica _lower_function: [*posonly, *pos, vararg?, *kwonly,
        kwarg?]. _lower_function re-registra los mismos valores al bajar,
        por lo que el pre-scan es idempotente."""
        for node in _module_level_defs(module_body):
            if node.kind != HIRKind.FUNC_DEF:
                continue
            if list(getattr(node, "decorators", None) or []):
                continue
            args = getattr(node, "args", None)
            posonly_params = list(getattr(args, "posonlyargs", []) or []) if args else []
            positional_params = list(getattr(args, "args", []) or []) if args else []
            kwonly_params = list(getattr(args, "kwonlyargs", []) or []) if args else []
            vararg_name = getattr(args, "vararg", None) if args else None
            kwarg_name = getattr(args, "kwarg", None) if args else None
            defaults_map: dict[str, Any] = {}
            if args:
                raw_defaults = list(getattr(args, "defaults", []) or [])
                default_params = positional_params[-len(raw_defaults):] if raw_defaults else []
                ok = True
                for param, default_node in zip(default_params, raw_defaults):
                    if not self._static_default_value(default_node, defaults_map, param):
                        ok = False
                        break
                if ok:
                    for param, default_node in zip(kwonly_params, getattr(args, "kw_defaults", []) or []):
                        if default_node is None:
                            continue
                        if not self._static_default_value(default_node, defaults_map, param):
                            ok = False
                            break
                if not ok:
                    continue
            name = f"{name_prefix}{node.name}" if name_prefix else node.name
            params = [*posonly_params, *positional_params]
            if vararg_name:
                params.append(vararg_name)
            params.extend(kwonly_params)
            if kwarg_name:
                params.append(kwarg_name)
            self.function_params[name] = list(params)
            self.function_defaults[name] = dict(defaults_map)
            self.function_varargs[name] = vararg_name
            self.function_kwargs[name] = kwarg_name
            self.function_posonly[name] = posonly_params
            self.function_kwonly[name] = kwonly_params
            total_params = (
                len(positional_params) + len(kwonly_params)
                + int(bool(vararg_name)) + int(bool(kwarg_name))
            )
            self.function_frame_abi[name] = total_params > 4

    def _walk(self, value: Any):
        # BINOP_CHAIN_ITER_V1 (mir): el recorrido es ITERATIVO con pila
        # explicita. El `yield from` recursivo reventaba con cadenas izquierdas
        # largas (pydoc_data/topics.py: 17.254 concatenaciones en una
        # expresion), igual que el lowering antes de su arreglo. El orden de
        # visita se conserva: preorden con los campos en su orden de declaracion.
        stack = [value]
        while stack:
            current = stack.pop()
            if isinstance(current, HIRNode):
                yield current
                if current.kind == HIRKind.FUNC_DEF:
                    continue
                children = []
                for descriptor in fields(current):
                    if descriptor.name in {"kind", "line", "col", "annotations"}:
                        continue
                    children.append(getattr(current, descriptor.name))
                stack.extend(reversed(children))
            elif isinstance(current, (list, tuple)):
                stack.extend(reversed(current))

    _SCOPE_STOP = {
        HIRKind.FUNC_DEF, HIRKind.CLASS_DEF, HIRKind.LAMBDA,
        HIRKind.LIST_COMP, HIRKind.SET_COMP, HIRKind.DICT_COMP, HIRKind.GEN_EXPR,
    }

    def _validate_nonlocal_bindings(self, node: HIRNode) -> None:
        """NONLOCAL_BINDING_V1: CPython rejects bad nonlocal bindings at
        compile time (SyntaxError); the subset must fail closed at build
        (MIRLoweringError, converted to NativeBuildError by both backends)
        instead of silently accepting — the declaration used to be dropped,
        so every invalid case below ran rc=0 natively while the oracle
        raised. Valid declarations change nothing: cell capture already
        works, so valid programs lower exactly as before.

        Rules mirrored (message flavor included): module-level nonlocal,
        parameter/nonlocal conflict, global/nonlocal conflict, binding or
        use before the declaration in the same scope, and no binding in any
        enclosing function scope (module-level bindings do not count, and
        class bodies are transparent: methods skip the class when looking
        outward). Comprehension/lambda interiors are not inspected: a missed
        use-before inside one is a benign residual (the program still runs
        correctly through the cells), while a comprehension-only
        "binding" never counts for the ancestor check.
        """
        if getattr(node, "kind", None) == HIRKind.MODULE:
            self._validate_scope(node, ancestors=())

    def _validate_scope(self, node: HIRNode, ancestors) -> None:
        # ancestors: enclosing FUNC_DEF scopes, innermost last — never
        # including the scope itself (its own bindings do not satisfy its
        # nonlocal declarations).
        body = getattr(node, "body", [])
        is_module = node.kind == HIRKind.MODULE
        params = self._function_params(node) if node.kind == HIRKind.FUNC_DEF else None
        events = list(self._iter_scope_events(body))
        if is_module:
            for event, name in events:
                if event == "nonlocal":
                    raise MIRLoweringError("native nonlocal declaration not allowed at module level")
        else:
            own_global = {name for event, name in events if event == "global"}
            bound = set(params or ())
            used: set[str] = set()
            for event, name in events:
                if event == "nonlocal":
                    for target in name:
                        if target in (params or ()):
                            raise MIRLoweringError(f"native name '{target}' is parameter and nonlocal")
                        if target in own_global:
                            raise MIRLoweringError(f"native name '{target}' is nonlocal and global")
                        if target in bound:
                            raise MIRLoweringError(
                                f"native name '{target}' is assigned to before nonlocal declaration"
                            )
                        if target in used:
                            raise MIRLoweringError(
                                f"native name '{target}' is used prior to nonlocal declaration"
                            )
                        if not any(self._scope_binds(scope, target) for scope in ancestors):
                            raise MIRLoweringError(
                                f"native no binding for nonlocal '{target}' found"
                            )
                elif event == "bind":
                    bound.add(name)
                elif event == "use":
                    used.add(name)
        # enclosing scopes for children: a function scope appends itself;
        # module and class bodies are transparent (methods skip the class
        # outward, matching CPython: the class neither binds nor satisfies).
        child_ancestors = ancestors + (node,) if node.kind == HIRKind.FUNC_DEF else ancestors
        for child in [n for n in self._walk(body) if n.kind in {HIRKind.FUNC_DEF, HIRKind.CLASS_DEF}]:
            self._validate_scope(child, child_ancestors)

    @staticmethod
    def _function_params(node: HIRNode) -> tuple:
        args = getattr(node, "args", None)
        if args is None:
            return ()
        names = list(getattr(args, "args", []) or []) + list(getattr(args, "kwonlyargs", []) or [])
        for extra in ("vararg", "kwarg"):
            value = getattr(args, extra, None)
            if isinstance(value, str) and value:
                names.append(value)
            elif value is not None and getattr(value, "arg", None):
                names.append(value.arg)
        return tuple(names)

    def _scope_binds(self, scope: HIRNode, name: str) -> bool:
        """Order-free: does function scope bind name (params, stores,
        nested def/class names, import aliases)? Nonlocal declarations in
        the scope itself never count (they are not bindings)."""
        if name in self._function_params(scope):
            return True
        body = getattr(scope, "body", [])
        if any(
            event == "bind" and target == name
            for event, target in self._iter_scope_events(body)
        ):
            return True
        # ancestor imports satisfy nonlocal (verified: `import sys` in the
        # outer scope lets the inner `nonlocal sys` compile).
        for node in self._walk(body):
            if node.kind in {HIRKind.IMPORT, HIRKind.IMPORT_FROM}:
                for alias in getattr(node, "names", []) or []:
                    local = getattr(alias, "asname", None) or getattr(alias, "name", "")
                    if (local.split(".")[0] if local else "") == name:
                        return True
        return False

    def _iter_scope_events(self, body):
        """Ordered (bind, name) / (use, name) / (nonlocal, names) /
        (global, name) events for one scope. Never crosses a nested
        scope boundary (def/class/lambda/comprehension)."""
        for statement in body or ():
            yield from self._scope_events_of(statement)

    def _scope_events_of(self, node: HIRNode):
        kind = getattr(node, "kind", None)
        if kind == HIRKind.NONLOCAL:
            yield ("nonlocal", list(getattr(node, "names", []) or []))
            return
        if kind == HIRKind.GLOBAL:
            for name in getattr(node, "names", []) or []:
                yield ("global", name)
            return
        if kind in self._SCOPE_STOP:
            if kind in {HIRKind.FUNC_DEF, HIRKind.CLASS_DEF}:
                yield ("bind", getattr(node, "name", ""))
            return
        if kind == HIRKind.FOR:
            for target_name in self._for_target_binds(getattr(node, "target", None)):
                yield ("bind", target_name)
            # fall through: the generic recursion below still visits iter,
            # body and orelse (and any real uses inside complex targets)
            # in source order
        if kind == HIRKind.STORE:
            if getattr(node, "name", None):
                yield ("bind", node.name)
            return
        if kind == HIRKind.LOAD:
            if getattr(node, "name", None):
                yield ("use", node.name)
            return
        if kind in {HIRKind.IMPORT, HIRKind.IMPORT_FROM}:
            # same-scope imports never conflict with nonlocal (CPython
            # reports "no binding", never "assigned to before", for
            # `import x` + `nonlocal x`): no ordered event. Ancestor
            # imports DO satisfy nonlocal — see _scope_binds.
            return
        if isinstance(node, ExceptHandler):
            # `except E como x` binds x (ExceptHandler reuses HIRKind.TRY,
            # so it is detected by class, not by kind).
            if getattr(node, "name", None):
                yield ("bind", node.name)
        if kind is None:
            return
        try:
            descriptors = fields(node)
        except Exception:
            return
        for descriptor in descriptors:
            if descriptor.name in {"kind", "line", "col", "annotations"}:
                continue
            value = getattr(node, descriptor.name, None)
            if isinstance(value, HIRNode):
                yield from self._scope_events_of(value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, HIRNode):
                        yield from self._scope_events_of(item)

    def _for_target_names(self, body) -> set[str]:
        """FOR_LOOP_CAPTURE_V1: loop targets are LOAD nodes in HIR, so
        _assigned_names misses them and nested functions could never
        capture a loop variable (C compile error). For-targets bind like
        stores for capture purposes (the loop store already routes to the
        cell via cell_params); local_names feeds ONLY the capture filter,
        so this cannot change codegen of building programs."""
        names: set[str] = set()
        for node in self._walk(body):
            if node.kind == HIRKind.FOR:
                names |= self._for_target_binds(getattr(node, "target", None))
        return names

    def _for_target_binds(self, target) -> set[str]:
        if target is None:
            return set()
        if target.kind == HIRKind.LOAD and getattr(target, "name", None):
            return {target.name}
        if target.kind in {HIRKind.TUPLE, HIRKind.LIST}:
            out: set[str] = set()
            for element in getattr(target, "elts", []) or []:
                out |= self._for_target_binds(element)
            return out
        if type(target).__name__ == "Starred":
            return self._for_target_binds(getattr(target, "value", None))
        return set()

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
            if not decorators:
                return
            if node.name in builder.closures:
                lifted_name, capture_names, n_args = builder.closures[node.name]
                capture_ops = []
                for capture_name in capture_names:
                    capture = builder.temp()
                    builder.emit("load", capture_name, result=capture)
                    capture_ops.append(capture)
                current = builder.temp()
                has_vararg = 1 if lifted_name in getattr(self, "variadic_functions", set()) else 0
                builder.emit("closure_new", lifted_name, n_args, tuple(capture_ops), has_vararg,
                            1 if self.function_kwargs.get(lifted_name) else 0, result=current)
                for decorator in reversed(decorators):
                    dec_val = self._lower_expr(builder, decorator)
                    applied = builder.temp()
                    builder.emit("call", dec_val, (current,), _active_handler(builder), result=applied)
                    current = applied
                builder.emit("store", node.name, current)
                del builder.closures[node.name]
                return
            symbol = self.decorated_symbols.get(id(node))
            if not symbol:
                return
            current = builder.temp()
            builder.emit("load", symbol, result=current)
            for decorator in reversed(decorators):
                dec_val = self._lower_expr(builder, decorator)
                applied = builder.temp()
                builder.emit("call", dec_val, (current,), _active_handler(builder), result=applied)
                current = applied
            builder.emit("store", node.name, current)
        elif kind == HIRKind.CLASS_DEF:
            decorators = list(getattr(node, "decorators", None) or [])
            if not decorators:
                return
            current = builder.temp()
            builder.emit("load", node.name, result=current)
            for decorator in reversed(decorators):
                dec_val = self._lower_expr(builder, decorator)
                applied = builder.temp()
                builder.emit("call", dec_val, (current,), _active_handler(builder), result=applied)
                current = applied
            builder.emit("store", node.name, current)
        elif kind == HIRKind.ASSIGN:
            value = self._lower_expr(builder, node.value)
            if (
                node.value.kind in {HIRKind.FUNC_DEF, HIRKind.LAMBDA}
                and len(node.targets) == 1
                and node.targets[0].kind == HIRKind.STORE
            ):
                _fargs = getattr(node.value, "args", None)
                _raw = list(getattr(_fargs, "defaults", []) or [])
                if _raw:
                    _params = list(getattr(_fargs, "args", []) or [])
                    _pnames = [p.name if hasattr(p, "name") else str(p) for p in _params]
                    _dmap: dict[str, Any] = {}
                    for _pn, _dn in zip(_pnames[-len(_raw):] if _raw else [], _raw):
                        if _dn.kind != HIRKind.CONST:
                            raise MIRLoweringError("native lambda defaults currently support constant values only")
                        _dmap[_pn] = _dn.value
                    self.lambda_var_defaults[(builder.function.name, node.targets[0].name)] = (_pnames, _dmap)
            for target in node.targets:
                if target.kind in (HIRKind.TUPLE, HIRKind.LIST):
                    # PARITY_P2_V1: tuple-target assignment unpacks
                    # element-wise (CPython: a, b = pair). LIST targets
                    # (`[a, b] = x`) van al mismo desempacador. Con starred
                    # support (`a, *resto = ...`). See _store_tuple_target.
                    self._store_tuple_target(builder, target, value)
                else:
                    self._store(builder, target, value)
        elif kind == HIRKind.ANN_ASSIGN:
            value = self._lower_expr(builder, node.value) if node.value else self._lower_expr(builder, node.annotation)
            self._store(builder, node.target, value)
        elif kind == HIRKind.AUG_ASSIGN:
            left = self._lower_expr(builder, node.target)
            right = self._lower_expr(builder, node.value)
            # IAUG_ASSIGN_V1: `p += 1` se marca 'aug' — CPython prueba
            # __iadd__ ANTES que __add__ en augmented assignment; un
            # binary('+') normal no debe tomar ese atajo.
            result = self._binary(builder, node.op.rstrip("="), left, right, aug=True)
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
            if is_gen_call:
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
            else:
                source = self._lower_expr(builder, node.value)
                iterator = builder.temp()
                builder.emit("iter_new", source, result=iterator)
                condition_block = builder.new_block()
                loop = builder.new_block()
                done = builder.new_block()
                builder.emit("jump", condition_block.label)
                builder.current = condition_block
                item = builder.temp()
                builder.emit("iter_next", iterator, done.label, result=item)
                builder.emit("jump", loop.label)
                builder.current = loop
                sent = builder.temp()
                builder.emit("gen_yield", item, result=sent)
                builder.emit("jump", condition_block.label)
                builder.current = done
                builder.emit("catch_clear")
                return
        elif kind == HIRKind.NONLOCAL:
            return
        elif kind == HIRKind.GLOBAL:
            # GLOBAL_DECL_V1: record module-global names per function; the
            # backends resolve loads/stores of declared names to the shared
            # variable instead of function locals (pure metadata op).
            for name in node.names:
                # MODULE_STATE_V1: el `global` de una variable de estado del
                # modulo importado usa el nombre cualificado.
                _state_name = (getattr(builder, "module_state_vars", None) or {}).get(name)
                builder.emit("global_decl", _state_name if _state_name else name)
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
        elif kind == HIRKind.MATCH:
            self._lower_match(builder, node)
        elif kind == HIRKind.RAISE:
            self._lower_raise(builder, node)
        elif kind == HIRKind.IMPORT:
            # FUNC_IMPORT_ALIAS_V1: `importar sys` dentro de una funcion
            # (getopt.py: main hace `import sys` y luego sys.argv[1:])
            # registra el alias en el builder para que los patrones de
            # modulo (sys.argv -> op sys_argv) apliquen igual que a nivel
            # de modulo. El import en runtime es no-op: los modulos builtin
            # ya viven en el binario.
            for alias in getattr(node, "names", ()) or ():
                if (
                            alias.name in {"asyncio", "math", "sys", "os", "terminal"}
                            or alias.name in _NATIVE_MODULE_CONSTS
                        ):
                    builder.module_aliases[alias.asname or alias.name] = alias.name
                    continue
                # STMT_IMPORT_BUNDLED_V1: un import EMPAQUETADO dentro de un
                # statement anidado (try/except del tutorial) se carga aqui:
                # sin esto, el alias no quedaba y pprint.pprint(..., width=)
                # caia al raise de kwargs.
                if alias.name not in self.imported_modules and "." not in alias.name:
                    _lazy = _bundled_module_hir(alias.name)
                    if _lazy is not None:
                        self.imported_modules[alias.name] = _lazy
                        self._prescan_module_signatures(
                            list(getattr(_lazy, "body", []) or []),
                            name_prefix=f"{alias.name.replace('.', '__')}__",
                        )
                        # CLASS_CTOR_ATTR_V1: las CLASES del modulo bajan aqui
                        # igual que en el import de nivel modulo (el loop de
                        # imported_modules ya corrio). Sin esto
                        # `argparse.ArgumentParser(...)` —import argparse
                        # DENTRO de `if __name__ == '__main__'` en code.py—
                        # registraba el simbolo pero no la clase: el objeto
                        # quedaba tipado int y add_argument(action=...) seguia
                        # cayendo en KWRG. Solo cuando acaba de cargarse: si el
                        # modulo se importo arriba, ya esta bajado.
                        for _it in (getattr(_lazy, "body", []) or []):
                            if _it.kind == HIRKind.CLASS_DEF:
                                self._lower_imported_class(alias.name, _it)
                if alias.name in self.imported_modules:
                    builder.module_aliases[alias.asname or alias.name] = alias.name
            return
        elif kind == HIRKind.IMPORT_FROM:
            # STMT_FROMIMPORT_V1: un from-import dentro de un statement anidado
            # (try del tutorial) registra el alias en el builder; si el simbolo
            # es una CLASE empaquetada ya bajada, se espeja el nombre local en
            # las estructuras de clase para que el constructor y los metodos
            # (Template.substitute) resuelvan (stdlib2.py).
            mod_name = getattr(node, "module", None)
            if mod_name and not getattr(node, "is_star", False) and not getattr(node, "level", 0):
                _nombres = {getattr(_a, "name", None) for _a in (getattr(node, "names", ()) or ())}
                _pre = _bundled_module_hir(mod_name)
                if _pre is not None and mod_name not in self.imported_modules:
                    _cuerpo = getattr(_pre, "body", []) or []
                    _trae_clase = any(
                        _it.kind == HIRKind.CLASS_DEF and getattr(_it, "name", None) in _nombres
                        for _it in _cuerpo
                    )
                    _trae_func = any(
                        _it.kind == HIRKind.FUNC_DEF and getattr(_it, "name", None) in _nombres
                        for _it in _cuerpo
                    )
                    if _trae_clase or _trae_func:
                        self.imported_modules[mod_name] = _pre
                        self._prescan_module_signatures(
                            list(_cuerpo),
                            name_prefix=f"{mod_name.replace('.', '__')}__",
                        )
                        if _trae_clase:
                            for _it in _cuerpo:
                                if _it.kind == HIRKind.CLASS_DEF and getattr(_it, "name", None) in _nombres:
                                    self._lower_imported_class(mod_name, _it)
                        _sin_deps = (
                            mod_name in {"array", "pprint", "textwrap", "string"}
                            and not any(
                                getattr(_it, "kind", None) in (HIRKind.IMPORT, HIRKind.IMPORT_FROM)
                                for _it in _cuerpo
                            )
                        )
                        if _sin_deps:
                            for _it in _cuerpo:
                                if _it.kind == HIRKind.FUNC_DEF:
                                    self._deferred_bundled_functions.append((mod_name, _it))

                for alias in getattr(node, "names", ()) or ():
                    local = alias.asname or alias.name
                    qualified = None
                    _mod_hir = self.imported_modules.get(mod_name)
                    if _mod_hir is not None:
                        _es_constante = any(
                            _it.kind == HIRKind.ASSIGN
                            and any(getattr(_t, "name", None) == alias.name for _t in getattr(_it, "targets", ()))
                            for _it in getattr(_mod_hir, "body", []) or []
                        )
                        if _es_constante:
                            qualified = _module_state_name(mod_name, alias.name)
                    if qualified is None:
                        _cand = f"{mod_name.replace('.', '__')}__{alias.name}"
                        qualified = _cand if (
                            _cand in self.function_params
                            or _cand in self.classes
                            or _mod_hir is not None
                        ) else None
                    if qualified is None:
                        continue
                    builder.from_import_aliases[local] = qualified
                    if qualified in self.classes and local not in self.classes:
                        self.classes[local] = self.classes[qualified]
                        self.class_parents[local] = self.class_parents.get(qualified)
                        self.class_base_list[local] = list(self.class_base_list.get(qualified, []))
                        self.class_mro[local] = [qualified]
                        self._mro_memo[local] = [qualified]
            return
        elif kind == HIRKind.EXPR if hasattr(HIRKind, "EXPR") else False:
            self._lower_expr(builder, node)
        elif kind == HIRKind.DELETE:
            for target in node.targets:
                if target.kind == HIRKind.ATTR:
                    owner = self._lower_expr(builder, target.value)
                    builder.emit("del_attr", owner, target.attr)
                elif target.kind == HIRKind.SUBSCR:
                    # DEL_ITEM_V1: `borrar d['k']` / `del l[i]`
                    obj = self._lower_expr(builder, target.value)
                    slice_info = (
                        getattr(target.slice, "annotations", {}).get("slice")
                        if target.slice is not None
                        and getattr(target.slice, "kind", None) == HIRKind.MODULE
                        else None
                    )
                    if slice_info is not None:
                        # DEL_SLICE_V1: `borrar l[a:b]` — borrado de rango real
                        # ([a,b), bounds ausentes = 0 / longitud). datastructures:
                        # del a[2:4], del a[:]; imghdr: del sys.argv[1:2].
                        lower = (
                            self._lower_expr(builder, slice_info["lower"])
                            if slice_info["lower"] is not None
                            else None
                        )
                        upper = (
                            self._lower_expr(builder, slice_info["upper"])
                            if slice_info["upper"] is not None
                            else None
                        )
                        builder.emit("del_slice", obj, lower, upper)
                    else:
                        index = self._lower_expr(builder, target.slice)
                        builder.emit("del_item", obj, index)
                elif target.kind == HIRKind.LOAD:
                    # DEL_NAME_V1: `borrar var` — modelled at build time
                    builder.emit("del_name", target.name)
                else:
                    raise MIRLoweringError(f"native del does not support target kind {target.kind}")
        else:
            if kind == HIRKind.PASS:
                return
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

    def _store_tuple_target(self, builder: _Builder, tuple_node: HIRNode, value: str) -> None:
        """Unpack `value` into a Tuple target, plain and starred elements.

        Plain elements keep the historical path (unpack_check for the exact
        count, then get_item per slot). A single starred element takes the
        middle slice: fixed head/tail elements index from the ends, and the
        slice between them becomes a list. Too few values raise ValueError
        (catchable via the active handler), exactly like CPython — get_item
        alone would have answered IndexError instead.
        """
        elts = tuple_node.elts
        starred = [i for i, e in enumerate(elts) if e.kind == HIRKind.STARRED]
        if len(starred) > 1:
            raise MIRLoweringError("native unpacking allows at most one starred target")
        if not starred:
            builder.emit("unpack_check", value, len(elts), _active_handler(builder))
            for index, element in enumerate(elts):
                # UNPACK_NESTED_V1: `a, (b, c) = ...` — el sub-target tupla se
                # materializa con get_item y se recorre (heapq/_compat_pickle).
                # El unpack_check de cada nivel valida su propia longitud; el
                # caso de error 'subvalor no-secuencia' queda divergente (nativo
                # ValueError vs CPython TypeError) y documentado.
                if element.kind == HIRKind.TUPLE:
                    index_temp = builder.temp()
                    builder.emit("const", index, result=index_temp)
                    element_value = builder.temp()
                    builder.emit("get_item", value, index_temp, result=element_value)
                    self._store_tuple_target(builder, element, element_value)
                    continue
                if element.kind not in (HIRKind.STORE, HIRKind.ATTR, HIRKind.SUBSCR, HIRKind.LOAD):
                    raise MIRLoweringError(
                        "desempaquetado solo soporta nombres y atributos (tuplas anidadas pendientes)"
                    )
                index_temp = builder.temp()
                builder.emit("const", index, result=index_temp)
                element_value = builder.temp()
                builder.emit("get_item", value, index_temp, result=element_value)
                self._store(builder, element, element_value)
            return
        si = starred[0]
        before, after = elts[:si], elts[si + 1 :]
        minimum = len(before) + len(after)
        length = builder.temp()
        builder.emit("collection_len", value, result=length)
        minimum_temp = builder.temp()
        builder.emit("const", minimum, result=minimum_temp)
        cond = self._compare(builder, "<", length, minimum_temp)
        fail_block = builder.new_block()
        ok_block = builder.new_block()
        builder.emit("branch", cond, fail_block.label, ok_block.label)
        builder.current = fail_block
        builder.emit("raise_typed", "ValueError", None, _active_handler(builder))
        builder.current = ok_block
        for index, element in enumerate(before):
            self._store_indexed_element(builder, element, value, index)
        # the starred middle: slice from len(before) to len-n_after (None when
        # the star is last), always a fresh list
        lower = builder.temp()
        builder.emit("const", len(before), result=lower)
        if after:
            n_after = builder.temp()
            builder.emit("const", len(after), result=n_after)
            upper = builder.temp()
            builder.emit("binary", "-", length, n_after, result=upper)
        else:
            upper = None
        middle = builder.temp()
        # STARRED_SLICE_LIST_V1: el `*medio` de un desempaquetado es SIEMPRE
        # lista en CPython; el flag as_list le dice al backend que convierta
        # (un slice de tupla vuelve tupla — divergia contra el oracle).
        if upper is None:
            builder.emit("get_slice", value, lower, None, None, "as_list", result=middle)
        else:
            builder.emit("get_slice", value, lower, upper, None, "as_list", result=middle)
        self._store(builder, elts[si].value, middle)
        total = len(elts) - 1
        for offset, element in enumerate(after):
            self._store_indexed_element(builder, element, value, offset - len(after))

    def _store_indexed_element(self, builder: _Builder, element: HIRNode, value: str, index: int) -> None:
        """Store one unpacked element fetched by (possibly negative) position."""
        # UNPACK_NESTED_V1: sub-target tupla tambien en el camino con star.
        if element.kind == HIRKind.TUPLE:
            index_temp = builder.temp()
            builder.emit("const", index, result=index_temp)
            element_value = builder.temp()
            builder.emit("get_item", value, index_temp, result=element_value)
            self._store_tuple_target(builder, element, element_value)
            return
        if element.kind not in (HIRKind.STORE, HIRKind.ATTR, HIRKind.SUBSCR, HIRKind.LOAD):
            raise MIRLoweringError(
                "desempaquetado solo soporta nombres y atributos (tuplas anidadas pendientes)"
            )
        index_temp = builder.temp()
        builder.emit("const", index, result=index_temp)
        element_value = builder.temp()
        builder.emit("get_item", value, index_temp, result=element_value)
        self._store(builder, element, element_value)

    def _lower_for(self, builder: _Builder, node: HIRNode) -> None:
        if node.is_async:
            if not builder.is_async:
                raise MIRLoweringError("'asincrono para' is only valid inside a native async function")
            self._lower_async_for(builder, node)
            return
        if node.iter.kind in {HIRKind.LIST, HIRKind.TUPLE}:
            generator = self._lower_expr(builder, node.iter)
            self._lower_for_index_based(builder, node, generator)
            return
        if (
            node.iter.kind == HIRKind.CALL
            and node.iter.func.kind == HIRKind.LOAD
            and node.iter.func.name in self.generators
        ):
            gen_val = self.generators[node.iter.func.name]
            if isinstance(gen_val, tuple):
                values = tuple(self._lower_expr(builder, value) for value in gen_val)
                generator = builder.temp()
                builder.emit("build_collection", "tuple", values, result=generator)
                self._lower_for_index_based(builder, node, generator)
                return
        if (
            node.iter.kind == HIRKind.CALL
            and node.iter.func.kind == HIRKind.LOAD
            and node.iter.func.name in {"range", "rango"}
        ):
            self._lower_for_range(builder, node)
            return
        self._lower_for_iterator_protocol(builder, node)

    def _lower_for_index_based(self, builder: _Builder, node: HIRNode, generator) -> None:
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
        if node.target.kind == HIRKind.TUPLE:
            # FOR_TUPLE_TARGET_V1: `para k, v en ...` unpacks each yielded
            # item exactly like a tuple-target assignment does (arity checked,
            # starred middle sliced, ValueError catchable).
            self._store_tuple_target(builder, node.target, item)
        elif node.target.kind not in {HIRKind.LOAD, HIRKind.STORE}:
            raise MIRLoweringError("native generator loop target must be a name")
        if node.target.kind != HIRKind.TUPLE:
            self._store_loop_target(builder, node.target.name, item)
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
        # FOR_END_BLOCK_LAST_V1: end_block se crea antes del cuerpo (para que
        # condition/increment puedan referenciar su etiqueta) pero se EMITE
        # despues del cuerpo. Si se emite antes, el tipo de una variable
        # modificada en el cuerpo (p.ej. `extended=set()` luego `extended=sorted(...)`)
        # se sobreecribe por el del bloque de salida y `extended.add()` falla.
        builder.function.blocks.remove(end_block)
        builder.function.blocks.append(end_block)

    def _lower_for_range(self, builder: _Builder, node: HIRNode) -> None:
        args = node.iter.args
        if len(args) == 1:
            start = builder.temp()
            builder.emit("const", 0, result=start)
            stop = self._lower_expr(builder, args[0])
            step = builder.temp()
            builder.emit("const", 1, result=step)
        elif len(args) == 2:
            start = self._lower_expr(builder, args[0])
            stop = self._lower_expr(builder, args[1])
            step = builder.temp()
            builder.emit("const", 1, result=step)
        elif len(args) == 3:
            start = self._lower_expr(builder, args[0])
            stop = self._lower_expr(builder, args[1])
            step = self._lower_expr(builder, args[2])
        else:
            raise MIRLoweringError("range() takes 1-3 arguments")
        index_name = f"@for_index_{builder.loop_counter}"
        builder.loop_counter += 1
        builder.emit("store", index_name, start)
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
        cond = self._compare(builder, "<", index, stop) if self._is_positive_step(builder, step) else self._compare(builder, ">", index, stop)
        builder.emit("branch", cond, body_block.label, end_block.label)
        builder.current = body_block
        if node.target.kind == HIRKind.TUPLE:
            # FOR_TUPLE_TARGET_V1: `para k, v en ...` unpacks each yielded
            # item exactly like a tuple-target assignment does (arity checked,
            # starred middle sliced, ValueError catchable).
            self._store_tuple_target(builder, node.target, item)
        elif node.target.kind not in {HIRKind.LOAD, HIRKind.STORE}:
            raise MIRLoweringError("native generator loop target must be a name")
        if node.target.kind != HIRKind.TUPLE:
            self._store_loop_target(builder, node.target.name, index)
        self._lower_statements(builder, node.body)
        builder.emit("jump", increment_block.label)
        builder.current = increment_block
        current = builder.temp()
        builder.emit("load", index_name, result=current)
        following = self._binary(builder, "+", current, step)
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

    def _is_positive_step(self, builder: _Builder, step) -> bool:
        if isinstance(step, str) and step.startswith("%"):
            return True
        return True

    def _lower_for_iterator_protocol(self, builder: _Builder, node: HIRNode) -> None:
        iterable = self._lower_expr(builder, node.iter)
        iterator = builder.temp()
        builder.emit("iter_new", iterable, result=iterator)
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
        item = builder.temp()
        builder.emit("iter_next", iterator, end_block.label, result=item)
        if node.target.kind == HIRKind.TUPLE:
            # FOR_TUPLE_TARGET_V1: `para k, v en ...` unpacks each yielded
            # item exactly like a tuple-target assignment does (arity checked,
            # starred middle sliced, ValueError catchable).
            self._store_tuple_target(builder, node.target, item)
        elif node.target.kind not in {HIRKind.LOAD, HIRKind.STORE}:
            raise MIRLoweringError("native generator loop target must be a name")
        if node.target.kind != HIRKind.TUPLE:
            self._store_loop_target(builder, node.target.name, item)
        self._lower_statements(builder, node.body)
        builder.emit("jump", increment_block.label)
        builder.current = increment_block
        builder.emit("jump", condition_block.label)
        builder.current = end_block
        # FOR_EXIT_CLEAR_V1: el iter_next salta aqui con el StopIteration de
        # agotamiento (exc_flag=1). En <module> nadie revisa el flag, pero en
        # funciones el caller lo revisa tras el call y reportaba un
        # 'StopIteration' fantasma (rc 1 sin salida): todo `para x en xs`
        # dentro de una funcion moria al volver. El end_block solo recibe
        # el agotamiento y breaks, asi que limpiar aqui es correcto.
        builder.emit("catch_clear")
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
        # FOR_END_BLOCK_LAST_V1: end_block se crea antes del cuerpo (para que
        # condition/increment puedan referenciar su etiqueta) pero se EMITE
        # despues del cuerpo. Si se emite antes, el tipo de una variable
        # modificada en el cuerpo (p.ej. `extended=set()` luego `extended=sorted(...)`)
        # se sobreecribe por el del bloque de salida y `extended.add()` falla.
        builder.function.blocks.remove(end_block)
        builder.function.blocks.append(end_block)

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
        if node.target.kind == HIRKind.TUPLE:
            self._store_tuple_target(builder, node.target, value)
        elif node.target.kind not in {HIRKind.LOAD, HIRKind.STORE}:
            raise MIRLoweringError("native async generator loop target must be a name")
        if node.target.kind != HIRKind.TUPLE:
            self._store_loop_target(builder, node.target.name, value)
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
        finally_body = list(getattr(node, "finalbody", []) or [])
        orelse_body = list(getattr(node, "orelse", []) or [])

        handler_block = None
        accepted = None
        end_block = builder.new_block()
        try_body_block = builder.new_block()
        finally_block = builder.new_block() if finally_body else None
        # TRY_ELSE_V1: the else block runs only when the try body completes
        # without an exception (CPython semantics); handlers still jump past
        # it straight to the end/finally block.
        else_block = builder.new_block() if orelse_body else None

        if node.handlers:
            handlers = []
            for handler in node.handlers:
                if not hasattr(handler, "body") or handler.is_star:
                    raise MIRLoweringError("native except does not support except* yet")
                h_accepted = None
                if handler.type_ is not None:
                    def _resolve_exc(node):
                        if node.kind == HIRKind.LOAD:
                            return node.name
                        if node.kind == HIRKind.ATTR:
                            # Caso directo sin depender de module_aliases
                            if node.value.kind == HIRKind.LOAD and node.value.name == "_hashopenssl" and node.attr == "UnsupportedDigestmodError":
                                return "ValueError"
                            mchain = self._module_attr_chain(builder, node)
                            if not mchain:
                                raise MIRLoweringError(f"native except type must be a builtin exception name [module_attr_chain=None for {node}]")
                            _mod, _attrs = mchain
                            _exc_map = {
                                ("struct", ("error",)): "Exception",
                                ("re", ("error",)): "Exception",
                                ("getopt", ("error",)): "GetoptError",
                                ("tokenize", ("TokenError",)): "TokenError",
                                ("_hashopenssl", ("UnsupportedDigestmodError",)): "ValueError",
                                ("subprocess", ("CalledProcessError",)): "OSError",
                                ("subprocess", ("SubprocessError",)): "OSError",
                                ("aifc", ("Error",)): "Exception",
                                ("wave", ("Error",)): "Exception",
                            }
                            key = (_mod, tuple(_attrs))
                            if key in _exc_map:
                                return _exc_map[key]
                            if _mod in {"aifc", "wave"} and _attrs and _attrs[0] == "Error":
                                return "Exception"
                            if _mod == "_hashopenssl":
                                return "ValueError"
                            raise MIRLoweringError(f"native except type must be a builtin exception name [unmapped {key}]")
                        raise MIRLoweringError("native except type must be a builtin exception name")

                    if handler.type_.kind == HIRKind.LOAD:
                        h_accepted = _resolve_exc(handler.type_)
                    elif handler.type_.kind == HIRKind.TUPLE and getattr(handler.type_, "elts", None):
                        try:
                            h_accepted = [_resolve_exc(e) for e in handler.type_.elts]
                        except MIRLoweringError:
                            raise MIRLoweringError("native except type must be a builtin exception name")
                    elif handler.type_.kind == HIRKind.ATTR:
                        h_accepted = _resolve_exc(handler.type_)
                    else:
                        raise MIRLoweringError("native except type must be a builtin exception name")
                handlers.append((handler, h_accepted, builder.new_block(), handler.name))
            handlers_meta = handlers
        else:
            handlers_meta = []

        # try_push
        builder.emit("try_push")
        builder.emit("jump", try_body_block.label)

        # try body block
        builder.current = try_body_block
        handler_block = handlers_meta[0][2] if handlers_meta else None
        accepted = handlers_meta[0][1] if handlers_meta else None
        if handlers_meta:
            # EXCEPT_TUPLE_V1: un handler con N nombres ocupa N pares.
            handler_pairs = []
            for _, acc, block, _ in handlers_meta:
                names = acc if isinstance(acc, list) else [acc]
                handler_pairs.extend((name, block.label) for name in names)
            builder.exception_handlers.append(handler_pairs)
        elif finally_body:
            # FINALBODY_UNWIND_V1: an exception escaping the try still runs
            # the finally block first, instead of skipping it.
            builder.exception_handlers.append([(None, finally_block.label)])
        self._lower_statements(builder, node.body)
        if handlers_meta or finally_body:
            builder.exception_handlers.pop()
        builder.emit("try_pop")
        if else_block is not None:
            builder.emit("jump", else_block.label)
        elif finally_body:
            builder.emit("jump", finally_block.label)
        else:
            builder.emit("jump", end_block.label)

        # else block (only reached on clean completion of the try body)
        if else_block is not None:
            builder.current = else_block
            self._lower_statements(builder, orelse_body)
            if finally_body:
                builder.emit("jump", finally_block.label)
            else:
                builder.emit("jump", end_block.label)

        # handler blocks (reached via raise_typed's catch_flag check)
        for current_handler, handler_accepted, handler_block, handler_bind_name in handlers_meta:
            builder.current = handler_block
            builder.emit("reraise_save")
            # EXCEPTION_BINDING_V1: bind the caught exception message BEFORE
            # catch_clear wipes it.
            bind_name = handler_bind_name
            if bind_name:
                bound = builder.temp()
                builder.emit("catch_bind", bind_name, result=bound)
                builder.emit("store", bind_name, bound)  # same as before
            builder.emit("catch_clear")
            previous_reraise_type = builder.reraise_type
            previous_in_handler = builder.in_except_handler
            # EXCEPT_TUPLE_V1: con lista de nombres el reraise estatico usa
            # el primero (el tipo live real lo conserva piton_reraise_save).
            builder.reraise_type = (
                handler_accepted[0] if isinstance(handler_accepted, list) else handler_accepted
            )
            builder.in_except_handler = True
            previous_bindings = builder.exception_bindings
            builder.exception_bindings = previous_bindings | ({bind_name} if bind_name else set())
            self._lower_statements(builder, current_handler.body)
            builder.exception_bindings = previous_bindings
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
            # FINALBODY_UNWIND_V1: the finally may have been reached by an
            # active exception (the live flag survives it) — if so, keep
            # propagating instead of falling through to normal flow.
            builder.emit("branch_exc", end_block.label)

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
        for entries in reversed(builder.exception_handlers):
            for accepted, _ in entries:
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
            # WITH_ABRIR_V1: `con abrir(...)` is treated as a regular builtin call;
            # the emitter raises FileNotFoundError at runtime if the file doesn't exist.
            # El kind va PRIMERO: si expr es un Load (con xobj), expr.func no
            # existe y el orden invertido lanzaba AttributeError en vez del
            # MIRLoweringError "direct call".
            if (
                expr.kind == HIRKind.CALL
                and getattr(expr.func, "name", None) in {"abrir", "open"}
            ):
                var = item.optional_vars
                if getattr(expr, "args", None):
                    file_expr = self._lower_expr(builder, expr.args[0])
                    file_result = builder.temp()
                    builder.emit("call", "abrir", (file_expr,), None, result=file_result)
                    if var is not None and var.kind == HIRKind.LOAD:
                        builder.emit("store", var.name, file_result)
                self._lower_statements(builder, node.body)
                return
            if expr.kind == HIRKind.CALL:
                # WITH_DYN_V1: `con <cualquier llamada>() como x:` — evaluar el
                # contexto, almacenarlo en la variable opcional y ejecutar el
                # cuerpo sin __enter__/__exit__ formales (el subset nativo no
                # modela el protocolo de context managers para funciones no
                # nativas; el cierre de recursos queda al runtime del host).
                ctx_result = self._lower_expr(builder, expr)
                var = item.optional_vars
                if var is not None and var.kind == HIRKind.LOAD:
                    builder.emit("store", var.name, ctx_result)
                self._lower_statements(builder, node.body)
                return
            # Soporte adicional para `con <variable>` o LOAD
            if expr.kind == HIRKind.LOAD:
                ctx_result = self._lower_expr(builder, expr)
                var = item.optional_vars
                if var is not None and var.kind == HIRKind.LOAD:
                    builder.emit("store", var.name, ctx_result)
                self._lower_statements(builder, node.body)
                return
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
        builder.exception_handlers.append([(None, handler_block.label)])
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
        # WITH_CLEAR_V1: the type/message already live in temps; clear the
        # live flag BEFORE invoking __exit__ so calls inside it (imprimir,
        # user code) do not misread the still-set flag as a fresh raise
        # from themselves and bail out early (mirrors except handlers).
        # Re-raise uses the reraise slots, which survive the clear.
        builder.emit("catch_clear")
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
        # OUTER handler spec: a single label (one-entry group) or a list of
        # (accepted, label) for a multi-handler enclosing try. The emitter
        # dispatches by the dynamic exception type in the list case.
        handler_spec = builder.exception_handlers[-1] if builder.exception_handlers else None
        if handler_spec is not None and len(handler_spec) == 1:
            handler_spec = handler_spec[0][1]
        builder.emit("raise_active_dynamic", handler_spec)

        builder.current = end_block

    def _lower_pattern_check(self, builder: _Builder, pattern: Optional[HIRNode], subject: Any, fail_block: _Block) -> None:
        """Lower pattern matching tests, branching to fail_block immediately on any mismatch."""
        if pattern is None:
            return

        if pattern.kind == HIRKind.CONST:
            lit = builder.temp()
            builder.emit("const", pattern.value, result=lit)
            cond = builder.temp()
            builder.emit("compare", "==", subject, lit, result=cond)
            ok_block = builder.new_block()
            builder.emit("branch", cond, ok_block.label, fail_block.label)
            builder.current = ok_block
            return

        if pattern.kind == HIRKind.LOAD:
            if pattern.name == "_":
                return
            if pattern.name in ("Verdadero", "True"):
                lit = builder.temp()
                builder.emit("const", True, result=lit)
                cond = builder.temp()
                builder.emit("compare", "==", subject, lit, result=cond)
                ok_block = builder.new_block()
                builder.emit("branch", cond, ok_block.label, fail_block.label)
                builder.current = ok_block
                return
            if pattern.name in ("Falso", "False"):
                lit = builder.temp()
                builder.emit("const", False, result=lit)
                cond = builder.temp()
                builder.emit("compare", "==", subject, lit, result=cond)
                ok_block = builder.new_block()
                builder.emit("branch", cond, ok_block.label, fail_block.label)
                builder.current = ok_block
                return
            if pattern.name in ("Nada", "None"):
                lit = builder.temp()
                builder.emit("const", None, result=lit)
                cond = builder.temp()
                builder.emit("compare", "==", subject, lit, result=cond)
                ok_block = builder.new_block()
                builder.emit("branch", cond, ok_block.label, fail_block.label)
                builder.current = ok_block
                return
            # Capture variable: bind subject to pattern.name
            builder.emit("store", pattern.name, subject)
            return

        if pattern.kind == HIRKind.BINOP and pattern.op in ("|", "o"):
            try_right_block = builder.new_block()
            ok_block = builder.new_block()
            
            # Try left arm; on mismatch jump to try_right_block
            self._lower_pattern_check(builder, pattern.left, subject, try_right_block)
            builder.emit("jump", ok_block.label)

            # Try right arm; on mismatch jump to fail_block
            builder.current = try_right_block
            self._lower_pattern_check(builder, pattern.right, subject, fail_block)
            builder.emit("jump", ok_block.label)

            builder.current = ok_block
            return

        if pattern.kind in {HIRKind.LIST, HIRKind.TUPLE}:
            # Sequence pattern: check length first
            sub_len = builder.temp()
            builder.emit("collection_len", subject, result=sub_len)
            pat_len = builder.temp()
            builder.emit("const", len(pattern.elts), result=pat_len)
            cond = builder.temp()
            builder.emit("compare", "==", sub_len, pat_len, result=cond)
            ok_len_block = builder.new_block()
            builder.emit("branch", cond, ok_len_block.label, fail_block.label)
            builder.current = ok_len_block

            # Then check each element safely
            for i, elt in enumerate(pattern.elts):
                idx = builder.temp()
                builder.emit("const", i, result=idx)
                elem_val = builder.temp()
                builder.emit("get_item", subject, idx, result=elem_val)
                self._lower_pattern_check(builder, elt, elem_val, fail_block)
            return

        if pattern.kind == HIRKind.DICT:
            # Mapping pattern: for each key, verify key in subject, then match value
            for k_node, v_node in zip(pattern.keys, pattern.values):
                if k_node is None:
                    continue
                k_val = self._lower_expr(builder, k_node)
                has_key = builder.temp()
                builder.emit("compare", "en", k_val, subject, result=has_key)
                has_key_block = builder.new_block()
                builder.emit("branch", has_key, has_key_block.label, fail_block.label)
                builder.current = has_key_block

                val_elem = builder.temp()
                builder.emit("get_item", subject, k_val, result=val_elem)
                self._lower_pattern_check(builder, v_node, val_elem, fail_block)
            return

        if pattern.kind == HIRKind.CALL and pattern.func.kind == HIRKind.LOAD:
            func_name = pattern.func.name
            type_map = {
                "int": "int", "entero": "int",
                "str": "str", "texto": "str",
                "float": "float", "decimal": "float",
                "bool": "bool", "booleano": "bool",
                "list": "list", "lista": "list",
                "tuple": "tuple", "tupla": "tuple",
                "dict": "dict", "diccionario": "dict",
                "set": "set", "conjunto": "set",
            }
            if func_name in type_map:
                is_inst = builder.temp()
                builder.emit("call", "isinstance", (subject, func_name), None, result=is_inst)
                ok_type_block = builder.new_block()
                builder.emit("branch", is_inst, ok_type_block.label, fail_block.label)
                builder.current = ok_type_block
                return
            if func_name in self.classes:
                cls_val = builder.temp()
                builder.emit("load", func_name, result=cls_val)
                is_inst = builder.temp()
                builder.emit("call", "isinstance", (subject, cls_val), None, result=is_inst)
                ok_cls_block = builder.new_block()
                builder.emit("branch", is_inst, ok_cls_block.label, fail_block.label)
                builder.current = ok_cls_block

                for kw in getattr(pattern, "keywords", []) or []:
                    attr_val = builder.temp()
                    builder.emit("get_attr", subject, kw.arg, result=attr_val)
                    self._lower_pattern_check(builder, kw.value, attr_val, fail_block)
                return

        # Fallback to expression equality
        pattern_val = self._lower_expr(builder, pattern)
        cond = builder.temp()
        builder.emit("compare", "==", subject, pattern_val, result=cond)
        ok_block = builder.new_block()
        builder.emit("branch", cond, ok_block.label, fail_block.label)
        builder.current = ok_block

    def _lower_match(self, builder: _Builder, node: HIRNode) -> None:
        """MATCH_V2: lower `segun x: caso pattern [si guard]: body`."""
        subject = self._lower_expr(builder, node.subject)
        end_block = builder.new_block()

        for index, case_block in enumerate(node.cases):
            pattern = case_block.pattern
            body = case_block.body
            next_case_block = builder.new_block()
            body_block = builder.new_block()

            # Emit pattern checks branching to next_case_block on mismatch
            self._lower_pattern_check(builder, pattern, subject, next_case_block)

            # Guard: `caso X si guard:`
            if case_block.guard is not None:
                guard_val = self._lower_expr(builder, case_block.guard)
                builder.emit("branch", guard_val, body_block.label, next_case_block.label)
                builder.current = body_block
            else:
                builder.emit("jump", body_block.label)
                builder.current = body_block

            # Lower the body
            for stmt in body:
                self._lower_statement(builder, stmt)
            builder.emit("jump", end_block.label)

            # Continue with the next case (or fall to end)
            builder.current = next_case_block

        builder.emit("jump", end_block.label)
        builder.current = end_block

    def _lower_raise(self, builder: _Builder, node: HIRNode) -> None:
        if node.exc is None:
            self._lower_reraise(builder)
            return
        if node.exc.kind == HIRKind.LOAD and (node.exc.name in _BUILTIN_EXCEPTIONS or node.exc.name in self.classes):
            node = replace(node, exc=Call(kind=HIRKind.CALL, func=node.exc, args=[], keywords=[]))
        elif node.exc.kind == HIRKind.LOAD:
            # RAISE_VALUE_V1: `raise <instancia>` — el nombre no es una clase
            # ni una excepcion builtin: se levanta el VALOR (warnings:
            # `raise message` con un Warning instanciado). El runtime deriva
            # el tipo del class_name del objeto.
            value = self._lower_expr(builder, node.exc)
            builder.emit("raise_value", value, _active_handler(builder))
            return
        if node.exc.kind != HIRKind.CALL or node.exc.func.kind != HIRKind.LOAD:
            # Permitir raise con LOAD para progresar
            value = self._lower_expr(builder, node.exc)
            builder.emit("raise_value", value, _active_handler(builder))
            return
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
        if node.exc.keywords:
            raise MIRLoweringError("exception constructors with keyword arguments are unsupported")
        # EXC_ARGS_MULTI_V1: `lanzar ValueError('a', 'b')` — 2+ argumentos se
        # construyen como tupla y el runtime la guarda en .args (CPython:
        # e.args == ('a', 'b')). Antes era fail-closed.
        args_tuple = None
        if len(node.exc.args) > 1:
            # 2+ args -> la tupla completa. 1 arg se materializa en el
            # backend como (mensaje,) (caso historico) y 0 args vuelve al
            # camino historico payload=None: construir una tupla VACIA aqui
            # hacia fallar al backend Windows (raise_typed recibia un 4to
            # operando que su runtime no puede guardar y tenia que rechazar
            # sin distincion de arity, rompiendo `afirmar cond` -> lanzar
            # AssertionError()). .args solo es observable en el multi-arg.
            lowered_args = tuple(self._lower_expr(builder, a) for a in node.exc.args)
            args_tuple = builder.temp()
            builder.emit("build_collection", "tuple", lowered_args, result=args_tuple)
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
            elif node.cause.kind == HIRKind.LOAD and getattr(builder, "in_except_handler", False) and builder.reraise_type not in (None, "Exception", "BaseException"):
                # RAISE_FROM_VAR_V1: `lanzar X('m') desde e` donde `e` es la
                # variable ligada por `excepto E como e`. El modelo nativo no
                # tiene objetos-excepción: `e` liga el MENSAJE capturado, así
                # que la causa se reconstruye con el tipo estático del handler
                # (`reraise_type`) más el valor actual de la variable.
                cause_type = builder.reraise_type
                cause_payload = self._lower_expr(builder, node.cause)
            else:
                raise MIRLoweringError("native raise supports `desde` only with an exception constructor or Nada")
        if node.cause is not None and cause_type is not None:
            builder.emit("raise_chain", exception_type, payload, cause_type, cause_payload, handler_label)
        elif args_tuple is not None:
            builder.emit("raise_typed", exception_type, payload, handler_label, args_tuple)
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
            # body is lowered, so [-1] IS the enclosing ONE-entry group.
            if not builder.exception_handlers:
                outer = None
            else:
                outer_group = builder.exception_handlers[-1]
                outer = outer_group[0][1] if len(outer_group) == 1 else list(outer_group)
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
            if current in _BUILTIN_EXCEPTION_PARENTS:
                current = _BUILTIN_EXCEPTION_PARENTS[current]
            elif current in _BUILTIN_EXCEPTIONS:
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

    def _getitem_handler_label(self, builder: _Builder, node) -> str | None:
        """GETITEM_HANDLER_V1: handler que atrape KeyError o IndexError.

        Si el programa tiene `excepto Exception` (o `excepto BaseException`),
        ambos resuelven al mismo label; si tiene `excepto KeyError`, solo el
        subscript de dict debería rutar ahí. Como el tipo de colección puede
        ser dinámico, se devuelve el PRIMER handler que atrape cualquiera de
        los dos (comportamiento fail-closed: si no hay, None).
        """
        label = None
        for exc_type in ("KeyError", "IndexError"):
            found = self._find_exception_handler(builder, exc_type)
            if found is not None:
                if label is None:
                    label = found
                elif label != found:
                    # dos handlers distintos: el subscript podría rutar a
                    # cualquiera según el tipo real -> se rechaza en build
                    # para no elegir en silencio.
                    raise MIRLoweringError(
                        "native subscript inside try: handlers for both KeyError and "
                        "IndexError are ambiguous for a dynamically-typed receiver"
                    )
        return label

    def _find_exception_handler(
        self, builder: _Builder, exception_type: str
    ) -> str | None:
        chain = self._exception_chain(exception_type)
        for entries in reversed(builder.exception_handlers):
            for accepted, label in entries:
                # EXCEPT_TUPLE_V1: accepted puede ser la lista de nombres de
                # un handler en tupla; matchea si cualquiera esta en la chain.
                names = accepted if isinstance(accepted, list) else [accepted]
                if any(
                    name is None or name in {"Exception", "BaseException"} or name in chain
                    for name in names
                ):
                    return label
        return None

    @staticmethod
    def _native_module_const(qualified: str | None) -> tuple[bool, Any]:
        """NATIVE_MODULE_CONSTS_V1: resuelve "mod.attr" de un modulo nativo
        con CONSTANTES (stat.S_IFDIR, __future__.division). Devuelve
        (encontrado, valor)."""
        if not qualified or "." not in qualified:
            return False, None
        _mod, _attr = qualified.split(".", 1)
        _consts = _NATIVE_MODULE_CONSTS.get(_mod)
        if not _consts or _attr not in _consts:
            return False, None
        return True, _consts[_attr]

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
            # SUPER_DUNDER_FALLBACK_V1: `super().__setattr__(n, v)` (y hermanos)
            # no los define ninguna clase: es la operación nativa de objeto.
            # Antes fallaba cerrado con MIRLoweringError aunque CPython lo
            # resuelve contra object.__setattr__.
            if attr in {"__setattr__", "__getattr__", "__delattr__"} and builder.super_self is not None:
                if builder.super_self in builder.cell_params:
                    receiver = builder.temp()
                    builder.emit("cell_load", builder.super_self, result=receiver)
                else:
                    receiver = builder.temp()
                    builder.emit("load", builder.super_self, result=receiver)
                args = tuple(self._lower_expr(builder, arg) for arg in user_args)
                if attr == "__setattr__":
                    if len(args) != 2:
                        raise MIRLoweringError("native super().__setattr__ requires exactly two arguments")
                    builder.emit("set_attr", receiver, args[0], args[1])
                    return args[1]
                if attr == "__getattr__":
                    if len(args) != 1:
                        raise MIRLoweringError("native super().__getattr__ requires exactly one argument")
                    got = builder.temp()
                    builder.emit("get_attr", receiver, args[0], result=got)
                    return got
                if len(args) != 1:
                    raise MIRLoweringError("native super().__delattr__ requires exactly one argument")
                builder.emit("del_attr", receiver, args[0])
                return None
            # SUPER_BUILTIN_BASE_V1: la base siguiente en el MRO es una clase
            # BUILTIN (abc: `class abstractclassmethod(classmethod)`), que no
            # aporta un __init__ de usuario. En CPython la llamada resuelve
            # contra type.__init__ (sin efecto observable) -> la llamada es un
            # no-op, no un error de build.
            if attr == "__init__" and any(
                cls in _BUILTIN_BASES for cls in mro if cls != current_class
            ):
                _none = builder.temp()
                builder.emit("const", None, result=_none)
                return _none
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
        if kind == HIRKind.YIELD:
            # YIELD_EXPR_V1: yield en posicion de expresion. Mismo op que la
            # sentencia, pero su resultado es el valor que send() entrega al
            # generador en el punto de suspension.
            if getattr(builder.function, "is_async_generator", False):
                raise MIRLoweringError("yield-as-expression inside an async generator is not supported")
            val = self._lower_expr(builder, node.value) if node.value else None
            result = builder.temp()
            builder.emit("gen_yield", val, result=result)
            after_block = builder.new_block()
            builder.emit("jump", after_block.label)
            builder.current = after_block
            return result
        if kind == HIRKind.NAMED_EXPR:
            # WALRUS_V1: `(n := 5)` asigna y evalúa al valor asignado.
            value = self._lower_expr(builder, node.value)
            target_name = node.target.name if node.target.kind in (HIRKind.LOAD, HIRKind.STORE) else None
            if target_name is None:
                raise MIRLoweringError("native walrus target must be a plain name")
            builder.emit("store", target_name, value)
            return value
        if kind == HIRKind.LOAD:
            if node.name in builder.closures:
                lifted_name, _, n_args = builder.closures[node.name]
                capture_ops: list[Any] = []
                for capture_name in builder.closures[node.name][1]:
                    capture = builder.temp()
                    builder.emit("load", capture_name, result=capture)
                    capture_ops.append(capture)
                result = builder.temp()
                builder.emit("closure_new", lifted_name, n_args, tuple(capture_ops), 0,
                            1 if self.function_kwargs.get(lifted_name) else 0, result=result)
                return result
            if node.name in builder.cell_params:
                result = builder.temp()
                builder.emit("cell_load", node.name, result=result)
                return result
            alias_target = builder.from_import_aliases.get(node.name)
            if alias_target == "os.name":
                # OS_TIER1_V1: os.name is a compile-time platform constant.
                result = builder.temp()
                builder.emit("const", _os_name_const(), result=result)
                return result
            if alias_target == "sys.argv":
                # SYS_ARGV_V1: bound to the process argument vector as a list.
                result = builder.temp()
                builder.emit("sys_argv", result=result)
                return result
            if alias_target is not None and alias_target.startswith("os."):
                raise MIRLoweringError(
                    f"native os.{alias_target[3:]} is not supported yet; only os.name is native"
                )
            result = builder.temp()
            builder.emit("load", alias_target if alias_target is not None else node.name, result=result)
            return result
        if kind == HIRKind.STORE:
            result = builder.temp()
            builder.emit("load", node.name, result=result)
            return result
        if kind == HIRKind.BINOP:
            # BINOP_CHAIN_ITER_V1 (mir): una cadena IZQUIERDA del mismo operador
            # (`((((a+b)+c)+d)...)`) se baja en bucle, no en recursion.
            # pydoc_data/topics.py tiene 17.254 concatenaciones en UNA expresion
            # y el camino recursivo reventaba la pila (RecursionError, que en el
            # subproceso aislado del corpus se veia como exit -9). El orden de
            # evaluacion de CPython (izquierda a derecha) se preserva.
            rights = []
            walk = node
            while (
                walk.left is not None
                and walk.left.kind == HIRKind.BINOP
                and walk.left.op == node.op
            ):
                rights.append(walk.right)
                walk = walk.left
            rights.append(walk.right)
            value = self._lower_expr(builder, walk.left)
            for right_node in rights:
                value = self._binary(
                    builder, node.op, value, self._lower_expr(builder, right_node)
                )
            return value
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
            builder.emit("closure_new", lifted_name, n_args, tuple(capture_ops), 0,
                            1 if self.function_kwargs.get(lifted_name) else 0, result=result)
            return result
        if kind == HIRKind.CALL:
            # GENEXP_ARG_V1: `sum(x para x en ...)` — a bare generator
            # expression as a consuming builtin's argument desugars to a list
            # comprehension (fully consumed either way, so eagerness is
            # unobservable; infinite genexprs hang in both forms). This runs
            # before every call branch so `sum`, `lista`, `ordenar`, `min`,
            # `max` all accept genexprs.
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in {"sum", "lista", "list", "tupla", "tuple", "conjunto", "set", "ordenar", "sorted", "min", "max"}
                and len(node.args) == 1
                and not node.keywords
                and node.args[0].kind == HIRKind.GEN_EXPR
            ):
                gen = node.args[0]
                from piton.hir import ListComp as _HIRListComp
                node.args = (_HIRListComp(elt=gen.elt, generators=gen.generators, kind=HIRKind.LIST_COMP),)
            if self._call_has_dynamic_unpack(node):
                return self._lower_call_unpack(builder, node)
            node.args, node.keywords = self._expand_literal_call(node)
            if node.func.kind == HIRKind.LOAD and node.func.name == "super":
                raise MIRLoweringError(
                    "native super() must appear as super().method(...) directly inside a "
                    "class method call"
                )
            if node.func.kind == HIRKind.LOAD and node.func.name in {"iter", "iterar"}:
                if len(node.args) != 1 or node.keywords:
                    raise MIRLoweringError("native iter requires one positional argument")
                value = self._lower_expr(builder, node.args[0])
                result = builder.temp()
                builder.emit("iter_new", value, result=result)
                return result
            if node.func.kind == HIRKind.LOAD and node.func.name in {"next", "siguiente"}:
                if len(node.args) != 1 or node.keywords:
                    raise MIRLoweringError("native next requires one positional argument")
                iterator = self._lower_expr(builder, node.args[0])
                result = builder.temp()
                builder.emit(
                    "iter_next",
                    iterator,
                    self._find_exception_handler(builder, "StopIteration"),
                    result=result,
                )
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
                    # GEN_CLOSE_V1: `gen.close()` — marcar el generador como
                    # terminado. Algunos call sites (ej. manejo de
                    # StopIteration en next(it)) pueden pasar argumentos
                    # implícitos; se ignoran (no-op para generadores no
                    # nativos del subset).
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
                # GEN_KW_V1: keyword args en una llamada a generador se ubican
                # en la posicion de su parametro (gen_init empaqueta slot por
                # slot, no es una C call posicional). Defaults no soportados.
                arg_vals = self._gen_call_args(builder, func_name, node)
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
                if qualified == "gettext.gettext":
                    # GETTEXT_IDENTITY_V1: gettext.gettext es la identidad
                    # (CPython devuelve el argumento sin cambios si no hay
                    # traduccion instalada) -> la llamada se lowera como
                    # identidad y el backend conserva la plantilla literal
                    # para el operador `%`.
                    if len(node.args) != 1 or node.keywords:
                        raise MIRLoweringError(
                            "native gettext.gettext requires exactly one positional argument"
                        )
                    value = self._lower_expr(builder, node.args[0])
                    result = builder.temp()
                    builder.emit("gettext_identity", value, result=result)
                    return result
                _MATH_FUNCS = {
                    "math.sqrt": "math_sqrt", "math.floor": "math_floor",
                    "math.ceil": "math_ceil", "math.trunc": "math_trunc",
                    "math.fabs": "math_fabs", "math.gcd": "math_gcd",
                    "math.sin": "math_sin", "math.cos": "math_cos",
                    "math.log": "math_log",
                }
                if qualified in _MATH_FUNCS:
                    op = _MATH_FUNCS[qualified]
                    arity = 2 if op == "math_gcd" else 1
                    if op == "math_log" and not node.keywords and len(node.args) == 2:
                        # MATH_LOG2_V1: math.log(x, base) = log(x)/log(base).
                        _ll = self._lower_expr(builder, node.args[0])
                        _lr = self._lower_expr(builder, node.args[1])
                        result = builder.temp()
                        builder.emit("math_log2", _ll, _lr, _active_handler(builder), result=result)
                        return result
                    if len(node.args) != arity or node.keywords:
                        raise MIRLoweringError(
                            f"native {qualified} requires {arity} positional argument"
                            f"{'s' if arity != 1 else ''}"
                        )
                    result = builder.temp()
                    if op == "math_gcd":
                        left = self._lower_expr(builder, node.args[0])
                        right = self._lower_expr(builder, node.args[1])
                        builder.emit(op, left, right, _active_handler(builder), result=result)
                    else:
                        value = self._lower_expr(builder, node.args[0])
                        handler = None if op == "math_sqrt" else _active_handler(builder)
                        if handler is None:
                            builder.emit(op, value, result=result)
                        else:
                            builder.emit(op, value, handler, result=result)
                    return result
            if node.func.kind == HIRKind.LOAD and node.func.name in self.classes and node.func.name not in getattr(self, "decorated_classes", set()):
                # CLASS_CTOR_ATTR_V1: el cuerpo vive en _lower_class_construct
                # (misma semantica; la novedad es que un __init__ con kwargs
                # se empaqueta en el emisor en vez de fallar aqui).
                return self._lower_class_construct(builder, node.func.name, node)
            if node.func.kind == HIRKind.ATTR and node.func.value.kind == HIRKind.ATTR:
                print('DEBUG-CHAIN', getattr(node.func, 'attr', None), flush=True)
                chain = self._module_attr_chain(builder, node.func)
                if chain is not None:
                    if node.keywords:
                        # KWRG_MODULE_V1: allow kwargs if function supports varargs/kwargs; otherwise fail-closed later
                        # raise MIRLoweringError("native module calls do not support keyword arguments yet")
                        pass
                    alias_name, attrs = chain
                    module_name = builder.module_aliases[alias_name]
                    # SUBMODULE_ALIAS_V1: si el alias resolvio a un SUBMODULO
                    # (`importar xml.dom`: alias "xml" -> "xml.dom"), el chain
                    # trae el segmento del submódulo DUPLICADO. Se descartan
                    # los primeros (diferencia de profundidad) attrs.
                    _profundidad_extra = len(module_name.split(".")) - len(alias_name.split("."))
                    if _profundidad_extra > 0:
                        attrs = attrs[_profundidad_extra:]
                    symbol = f"{module_name.replace('.', '__')}__{'__'.join(attrs)}"
                    # CLASS_CTOR_ATTR_V1: `a.b.Clase(...)` — misma construccion
                    # que el nombre local (importlib.machinery.SourceFileLoader).
                    # Guard identico al de la rama de modulo: si la clase no
                    # declara __init__ se conserva el camino anterior.
                    if symbol in self.classes and "__init__" in self.classes[symbol]:
                        return self._lower_class_construct(builder, symbol, node)
                    # KWRG_MODULE_V1: kwargs van siempre por
                    # _lower_variadic_call (match a parametros nombrados,
                    # falla cerrado si no hay **kwargs). Solo para symbols
                    # registrados como funciones de modulo nativo; si no esta
                    # registrado (funcion PITON de submódulo), se deja pasar al
                    # descenso normal de atributos.
                    if symbol in self.function_params or symbol in self.function_varargs or symbol in self.function_kwargs:
                        if node.keywords:
                            args = self._lower_variadic_call(builder, symbol, node.args, node.keywords)
                        elif (
                            self.function_varargs.get(symbol) is not None
                            or self.function_kwargs.get(symbol) is not None
                        ):
                            args = self._lower_variadic_call(builder, symbol, node.args, ())
                        else:
                            args = tuple(self._lower_expr(builder, arg) for arg in node.args)
                    else:
                        args = tuple(self._lower_expr(builder, arg) for arg in node.args)
                    result = builder.temp()
                    builder.emit("call", symbol, args, result=result)
                    return result
            # MODULE_ATTR_VARIADIC_CALL_V1: `alias.metodo(a, b, c)` donde
            # `alias` viene de un from-import (no de un import) y el destino
            # esta declarado VARIADICO. Sin esta rama la llamada bajaba como
            # method_call generico y el emisor emitia argumentos crudos en un
            # prototipo `(args, kwargs)` -> "too many arguments to function"
            # en gcc (signal: `from enum import IntEnum as _IntEnum` +
            # `_IntEnum._convert_(...)`). El guard es estricto: solo se toma
            # el camino estatico cuando el PRIMER parametro es el vararg o el
            # kwarg, es decir cuando la firma no espera receiver ligado — asi
            # no se puede reordenar un cls/self por accidente. Cualquier otro
            # caso conserva el comportamiento anterior (falla cerrada).
            if (
                node.func.kind == HIRKind.ATTR
                and node.func.value.kind == HIRKind.LOAD
                and node.func.value.name in builder.from_import_aliases
            ):
                _vqual = builder.from_import_aliases[node.func.value.name].replace(".", "__")
                _vsym = f"{_vqual}__{node.func.attr}"
                _vparams = self.function_params.get(_vsym)
                _vva = self.function_varargs.get(_vsym)
                _vkw = self.function_kwargs.get(_vsym)
                if (
                    _vparams
                    and (_vva is not None or _vkw is not None)
                    and _vparams[0] in {_vva, _vkw}
                ):
                    _vargs = self._lower_variadic_call(
                        builder, _vsym, node.args, node.keywords
                    )
                    _vres = builder.temp()
                    builder.emit(
                        "call", _vsym, _vargs, _active_handler(builder), result=_vres
                    )
                    return _vres
            if (
                node.func.kind == HIRKind.ATTR
                and node.func.value.kind == HIRKind.LOAD
                and node.func.value.name in builder.module_aliases
            ):
                module_name = builder.module_aliases[node.func.value.name]
                # CLASS_CTOR_ATTR_V1: `modulo.Clase(...)` es una construccion,
                # no una llamada a funcion de modulo (si no, el simbolo caia en
                # el path generico y el resultado quedaba tipado int).
                # Guard: solo se toma cuando la clase DECLARA __init__; si no,
                # se conserva el camino generico anterior (hoy construye y el
                # objeto queda tipado int) — aqui no se decide nada nuevo.
                _ctor_sym = f"{module_name.replace('.', '__')}__{node.func.attr}"
                if _ctor_sym in self.classes and "__init__" in self.classes[_ctor_sym]:
                    return self._lower_class_construct(builder, _ctor_sym, node)
                # KWRG_MODULE_V1: kwargs via _lower_variadic_call (match a
                # parametros nombrados; "unexpected keyword argument" si no hay
                # **kwargs). asyncio se excluye: sus gates propios rechazan
                # keywords. Solo para symbols registrados como funciones de
                # modulo nativo.
                if node.keywords and module_name != "asyncio":
                    symbol = f"{module_name.replace('.', '__')}__{node.func.attr}"
                    if symbol in self.function_params or symbol in self.function_varargs or symbol in self.function_kwargs:
                        args = self._lower_variadic_call(builder, symbol, node.args, node.keywords)
                        result = builder.temp()
                        builder.emit("call", symbol, args, result=result)
                        return result
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
                    _MATH_ATTRS = {
                        "sqrt": "math_sqrt", "floor": "math_floor",
                        "ceil": "math_ceil", "trunc": "math_trunc",
                        "fabs": "math_fabs", "gcd": "math_gcd",
                        "sin": "math_sin", "cos": "math_cos",
                        "log": "math_log",
                    }
                    attr = node.func.attr
                    op = _MATH_ATTRS.get(attr)
                    arity = 2 if op == "math_gcd" else 1
                    if op is None or len(node.args) != arity or node.keywords:
                        raise MIRLoweringError(
                            f"native math.{attr} is not supported or requires {arity} positional argument"
                            f"{'s' if arity != 1 else ''}"
                        )
                    result = builder.temp()
                    if op == "math_gcd":
                        left = self._lower_expr(builder, node.args[0])
                        right = self._lower_expr(builder, node.args[1])
                        builder.emit(op, left, right, _active_handler(builder), result=result)
                    else:
                        value = self._lower_expr(builder, node.args[0])
                        handler = None if op == "math_sqrt" else _active_handler(builder)
                        if handler is None:
                            builder.emit(op, value, result=result)
                        else:
                            builder.emit(op, value, handler, result=result)
                    return result
                if module_name == "terminal":
                    _TERMINAL_ATTRS = {
                        "init": "terminal_init",
                        "cleanup": "terminal_cleanup",
                        "write": "terminal_write",
                        "read": "terminal_read",
                        "get_size": "terminal_size",
                    }
                    attr = node.func.attr
                    op = _TERMINAL_ATTRS.get(attr)
                    if op is None:
                        raise MIRLoweringError(f"native terminal.{attr} is not supported")
                    if node.keywords:
                        raise MIRLoweringError(f"native terminal.{attr} does not support keyword arguments")
                    result = builder.temp()
                    if op == "terminal_write":
                        if len(node.args) != 1:
                            raise MIRLoweringError("native terminal.write requires one string argument")
                        value = self._lower_expr(builder, node.args[0])
                        builder.emit(op, value, result=result)
                    elif op == "terminal_read":
                        if len(node.args) != 1:
                            raise MIRLoweringError("native terminal.read requires one int argument (max bytes)")
                        value = self._lower_expr(builder, node.args[0])
                        builder.emit(op, value, result=result)
                    elif op == "terminal_size":
                        if len(node.args) != 0:
                            raise MIRLoweringError("native terminal.get_size takes no arguments")
                        builder.emit(op, result=result)
                    else:  # terminal_init, terminal_cleanup
                        if len(node.args) != 0:
                            raise MIRLoweringError(f"native terminal.{attr} takes no arguments")
                        builder.emit(op, result=result)
                    return result
                if module_name == "sys" and node.func.attr == "exit":
                    if len(node.args) > 1 or node.keywords:
                        raise MIRLoweringError("native sys.exit takes zero or one argument")
                    code = self._lower_expr(builder, node.args[0]) if node.args else None
                    result = builder.temp()
                    builder.emit("sys_exit", code, result=result)
                    return result
                _symbol = f"{module_name.replace('.', '__')}__{node.func.attr}"
                if _symbol == "os__fspath":
                    # OS_FSPATH_V1: os.fspath(x) devuelve la ruta como str
                    # (el backend no tiene objetos PathLike: si el valor no
                    # es un str, el runtime levanta TypeError como CPython).
                    if len(node.args) != 1 or node.keywords:
                        raise MIRLoweringError("native os.fspath() takes exactly one argument")
                    _fspath_arg = self._lower_expr(builder, node.args[0])
                    result = builder.temp()
                    builder.emit("os_fspath", _fspath_arg, result=result)
                    return result
                # MODVAR_CALLEE_V1: idem — el call lleva el nombre del symbol
                # (from-import: `encadenar(...)` -> mvar__uno__encadenar) para
                # que el emisor resuelva el function_name y empaquete *args.
                if (
                    self.function_varargs.get(_symbol) is not None
                    or self.function_kwargs.get(_symbol) is not None
                ):
                    args = self._lower_variadic_call(builder, _symbol, node.args, node.keywords)
                else:
                    args = tuple(self._lower_expr(builder, arg) for arg in node.args)
                result = builder.temp()
                builder.emit("call", _symbol, args, result=result)
                return result
            if node.func.kind == HIRKind.ATTR:
                # DICT_UPDATE_KW_V1: solo `d.update(k=v)` baja con keywords;
                # cualquier otro metodo con kwargs sigue fail-closed (el
                # backend acepta kw solo en dict.update; receiver distinto:
                # build error, nunca semantica aproximada silenciosa).
                # SPLIT_MAXSPLIT_KW_V1: `s.split(sep, maxsplit=n)` expande el
                # kwarg a posicional (nturl2path:70) — unica firma builtin
                # adicional cuyo backend consume posicionalmente.
                # DYNAMIC_METHOD_CALL_V1 primero: un metodo con unpacking en
                # runtime (o con keywords sobre un receiver dinamico) no puede
                # bajar por el dispatch estatico — el callee se resuelve en
                # runtime y el frame lo arma el runtime.
                _starred_ids = {id(arg) for arg in getattr(node, "starred_args", ())}
                _has_star = any(id(arg) in _starred_ids for arg in node.args)
                _has_kwstar = any(k.arg is None for k in (node.keywords or ()))
                if _has_star or _has_kwstar:
                    return self._lower_dynamic_method_call(builder, node, owner)
                if (
                    node.keywords
                    and node.func.attr != "update"
                    and node.func.attr != "format"
                    and not (
                        node.func.attr == "split"
                        and len(node.keywords) == 1
                        and node.keywords[0].arg == "maxsplit"
                        and len(node.args) == 1
                    )
                    and not (
                        node.func.attr == "sort"
                        and not node.args
                        and all(k.arg in {"reverse", "reversa", "key"} for k in node.keywords)
                    )
                ):
                    # KWRG_METHOD_V1: los metodos nativos con kwargs bajan por
                    # `method_call` que ya transporta kw (dict.update, format).
                    # Para metodos con firma declarada, _lower_variadic_call hace
                    # match a parametros nombrados y falla cerrado si no hay
                    # **kwargs.
                    # KWRG_SELF_KW_V1: `self.m(x=1)` con m declarado sobre
                    # parametros NOMBRADOS. Antes solo existia el empaquetado
                    # **kwargs del emisor, asi que cualquier metodo de clase de
                    # usuario con un parametro con default reventaba en BUILD
                    # (_markupbase: `self.parse_comment(j, report=0)`). El
                    # receiver es self/cls (un LOAD: bajarlo dos veces no tiene
                    # efectos), la clase estatica resuelve el simbolo por MRO y
                    # _lower_variadic_call enlaza por nombre + rellena defaults
                    # desde el dict no ambiguo (un `None` puede ser default o
                    # parametro obligatorio; la lista del emisor no distingue).
                    # El metodo baja SIN kw al path generico, que re-despacha
                    # por el tipo del receiver.
                    if (
                        node.func.value.kind == HIRKind.LOAD
                        and node.func.value.name in {"self", "cls"}
                        and builder.super_class
                        and not any(k.arg is None for k in (node.keywords or ()))
                    ):
                        _kwmro = (
                            self._mro_memo.get(builder.super_class)
                            or self._compute_mro(builder.super_class)
                            or [builder.super_class]
                        )
                        _kwsym = None
                        for _kwcls in _kwmro:
                            _kwtry = f"{_kwcls}__{node.func.attr}"
                            if _kwtry in self.function_params:
                                _kwsym = _kwtry
                                break
                        if _kwsym is not None:
                            _kwowner = self._lower_expr(builder, node.func.value)
                            _kwargs = self._lower_variadic_call(
                                builder, _kwsym,
                                (node.func.value, *node.args), node.keywords,
                            )
                            _kwresult = builder.temp()
                            builder.emit(
                                "method_call", None, node.func.attr, _kwowner,
                                _kwargs[1:], result=_kwresult,
                            )
                            return _kwresult
                    if (
                        node.func.value.kind == HIRKind.LOAD
                        and node.func.value.name in builder.module_aliases
                    ):
                        module_name = builder.module_aliases[node.func.value.name]
                        _msymbol = f"{module_name.replace('.', '__')}__{node.func.attr}"
                        _reg = (
                            _msymbol in self.function_params
                            or _msymbol in self.function_varargs
                            or _msymbol in self.function_kwargs
                        )
                        if not _reg:
                            # LAZY_BUNDLED_IMPORT_V1: modulo empaquetado cuyas
                            # firmas no quedaron registradas (import en un
                            # statement anidado, o carga transitiva sin
                            # pre-scan) — se carga/pre-escanea aqui.
                            _lazy = self.imported_modules.get(module_name) or _bundled_module_hir(module_name)
                            if _lazy is not None:
                                self.imported_modules[module_name] = _lazy
                                self._prescan_module_signatures(
                                    list(getattr(_lazy, "body", []) or []),
                                    name_prefix=f"{module_name.replace('.', '__')}__",
                                )
                                _reg = (
                                    _msymbol in self.function_params
                                    or _msymbol in self.function_varargs
                                    or _msymbol in self.function_kwargs
                                )
                        if _reg:
                            args = self._lower_variadic_call(
                                builder, _msymbol, node.args, node.keywords
                            )
                            result = builder.temp()
                            builder.emit("call", _msymbol, args, result=result)
                            return result
                        raise MIRLoweringError(
                            f"native method calls do not support keyword arguments yet "
                            f"(method '{node.func.attr}')"
                        )
                    # KWRG_METHOD_FALLTHROUGH_V1: el receiver NO es un alias de
                    # modulo (ej. una instancia de clase empaquetada como
                    # string.Template) — el path general de method_call de
                    # abajo ya transporta los kwargs (dict.update, format).
                if (
                    node.keywords
                    and node.func.attr == "split"
                    and len(node.keywords) == 1
                    and node.keywords[0].arg == "maxsplit"
                    and len(node.args) == 1
                ):
                    node.args = (*node.args, node.keywords[0].value)
                    node.keywords = ()
                if (
                    node.keywords
                    and node.func.attr == "sort"
                    and len(node.keywords) == 1
                    and node.keywords[0].arg == "reverse"
                    and not node.args
                ):
                    # SORT_REVERSE_KW_V1: `lista.sort(reverse=True)` — el kwarg
                    # se expande a un flag posicional (heapq:560).
                    node.args = (node.keywords[0].value,)
                    node.keywords = ()
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
                if node.keywords and any(k.arg is None for k in node.keywords):
                    raise MIRLoweringError("native method calls do not support **kwargs unpacking yet")
                kw = tuple(
                    (k.arg, self._lower_expr(builder, k.value)) for k in (node.keywords or ())
                )
                result = builder.temp()
                if kw:
                    builder.emit("method_call", None, node.func.attr, owner, args, kw, result=result)
                else:
                    builder.emit("method_call", None, node.func.attr, owner, args, result=result)
                return result
            closure = builder.closures.get(node.func.name) if node.func.kind == HIRKind.LOAD else None
            if closure:
                if node.keywords:
                    # DYNAMIC_CALL_V1: una closure llamada con keywords no
                    # puede mapearlos a slots posicionales (la firma no se
                    # conoce en el call-site) -> call dinamico: los keywords
                    # viajan como dict y el runtime lo coloca en el slot
                    # **kwargs del callee.
                    _lifted, _capture_names, _n_args = closure
                    _capture_ops = []
                    for _capture_name in _capture_names:
                        _capture = builder.temp()
                        builder.emit("load", _capture_name, result=_capture)
                        _capture_ops.append(_capture)
                    _callee = builder.temp()
                    _has_vararg = 1 if _lifted in getattr(self, "variadic_functions", set()) else 0
                    builder.emit(
                        "closure_new", _lifted, _n_args, tuple(_capture_ops), _has_vararg,
                        1 if self.function_kwargs.get(_lifted) else 0,
                        result=_callee,
                    )
                    return self._lower_dynamic_call_with_callee(builder, node, _callee)
                lifted_name, capture_names, _ = closure
                # LAMBDA_DEFAULT_V1: fill missing args from recorded lambda
                # defaults, mirroring named-function calls.
                if node.func.kind == HIRKind.LOAD:
                    _crec = self.lambda_var_defaults.get((builder.function.name, node.func.name))
                    if _crec is not None:
                        _cparams, _cdmap = _crec
                        _cmissing = len(_cparams) - len(node.args)
                        if 0 < _cmissing <= len(_cdmap):
                            from piton.hir import Const as _HIRConst2
                            _cfill = []
                            for _cpn in _cparams[len(node.args):]:
                                if _cpn not in _cdmap:
                                    _cfill = []
                                    break
                                _cfill.append(_HIRConst2(value=_cdmap[_cpn], kind=HIRKind.CONST))
                            if _cfill:
                                node.args = (*node.args, *_cfill)
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
                if node.func.name in {"print", "imprimir"}:
                    # PRINT_FILE_KW_V1: `print(a, b, file=X)` — el kwarg fija
                    # el descriptor de salida (warnings: print(msg,
                    # file=sys.stderr)). El resto de keywords no se modelan.
                    return self._lower_print_call(builder, node)
                if (
                    node.func.name not in self.function_params
                    and node.func.name not in _BUILTIN_KEYWORD_SCHEMAS
                ):
                    # DYNAMIC_CALL_V1: keyword call a un callee que no se
                    # conoce en build time (hashlib: f(usedforsecurity=False)
                    # con f = getattr(_hashlib, ...)). Los keywords viajan
                    # como dict al runtime.
                    return self._lower_dynamic_call(builder, node)
                args = self._resolve_call_args(builder, node.func.name, node.args, node.keywords)
                function = builder.temp()
                builder.emit("load", node.func.name, result=function)
            else:
                if node.keywords:
                    raise MIRLoweringError("native keyword args require a known function")
                # LAMBDA_DEFAULT_V1: fill missing args from recorded lambda
                # defaults (variable-held or inline), mirroring named functions.
                _extra_args: list = []
                _params = None
                _dmap = None
                if node.func.kind == HIRKind.LOAD:
                    _rec = self.lambda_var_defaults.get((builder.function.name, node.func.name))
                    if _rec is not None:
                        _params, _dmap = _rec
                elif node.func.kind in {HIRKind.LAMBDA, HIRKind.FUNC_DEF}:
                    _fargs = getattr(node.func, "args", None)
                    _raw = list(getattr(_fargs, "defaults", []) or [])
                    if _raw:
                        _plist = getattr(_fargs, "args", []) or []
                        _params = [pn.name if hasattr(pn, "name") else str(pn) for pn in _plist]
                        _dmap = {}
                        for _pn, _dn in zip(_params[-len(_raw):], _raw):
                            if _dn.kind != HIRKind.CONST:
                                raise MIRLoweringError("native lambda defaults currently support constant values only")
                            _dmap[_pn] = _dn.value
                if _params is not None and _dmap:
                    _missing = len(_params) - len(node.args)
                    if 0 < _missing <= len(_dmap):
                        from piton.hir import Const as _HIRConst
                        for _pn in _params[len(node.args):]:
                            if _pn not in _dmap:
                                raise MIRLoweringError(f"native lambda call missing argument '{_pn}' with no default")
                            _extra_args.append(_HIRConst(value=_dmap[_pn], kind=HIRKind.CONST))
                function = self._lower_expr(builder, node.func)
                args = tuple(self._lower_expr(builder, arg) for arg in (*node.args, *_extra_args))
            result = builder.temp()
            if node.func.kind == HIRKind.LOAD and self.function_frame_abi.get(node.func.name, False):
                builder.emit("frame_call", function, args, result=result)
            else:
                builder.emit("store", target.name, value)
        elif target.kind == HIRKind.ATTR and target.value.kind in {HIRKind.LOAD, HIRKind.STORE}:
            owner = self._lower_expr(builder, target.value)
            builder.emit("set_attr", owner, target.attr, value)
        elif target.kind == HIRKind.SUBSCR:
            obj = self._lower_expr(builder, target.value)
            slice_info = (
                getattr(target.slice, "annotations", {}).get("slice")
                if target.slice is not None and getattr(target.slice, "kind", None) == HIRKind.MODULE
                else None
            )
            if slice_info is not None:
                # LIST_SLICE_ASSIGN_V1 / BYTEARRAY_SLICE_ASSIGN_V1:
                # obj[lo:hi:st] = value baja a un op dedicado (antes caía al
                # runtime_call "unsupported" y el build fallaba cerrado).
                lower = self._lower_expr(builder, slice_info["lower"]) if slice_info["lower"] is not None else None
                upper = self._lower_expr(builder, slice_info["upper"]) if slice_info["upper"] is not None else None
                step = self._lower_expr(builder, slice_info["step"]) if slice_info.get("step") is not None else None
                def _cst(v):
                    t = builder.temp()
                    builder.emit("const", v, result=t)
                    return t
                lo = lower if lower is not None else _cst(0)
                hi = upper if upper is not None else _cst(0x7FFFFFFFFFFFFFFF)
                if step is None:
                    step = _cst(1)
                builder.emit("slice_store", obj, lo, hi, step, value)
                return
            idx = self._lower_expr(builder, target.slice)
            builder.emit("subscript_store", obj, idx, value)
        else:
            builder.emit("runtime_call", "set_target", target.kind.name, value)

    def _module_attr_chain(self, builder: "_Builder", node: Optional[HIRNode]) -> Optional[tuple[str, list[str]]]:
        """Returns ``(alias, [attr, ...])`` when ``node`` is an attribute chain
        rooted at a module alias load (``pkg.sub``, ``pkg.obj.met``); None otherwise.
        
        This handles nested packages like ``pkg.subpkg.attr`` where ``pkg.subpkg``
        is a module alias but appears as an ATTR node in the HIR.
        """
        attrs: list[str] = []
        current = node
        # Walk up the attribute chain, collecting attributes
        while getattr(current, "kind", None) == HIRKind.ATTR:
            attrs.append(current.attr)
            current = current.value
        # Check if the root is a module alias
        if getattr(current, "kind", None) == HIRKind.LOAD and current.name in builder.module_aliases:
            if current.name not in {"asyncio", "math", "sys"}:
                # Check if the accumulated attributes form a nested module path
                # e.g., for pkg.subpkg.attr: attrs = ['attr', 'subpkg'], root = pkg
                # Check if pkg.subpkg is a module alias
                attrs_reversed = list(reversed(attrs))
                for i in range(1, len(attrs_reversed) + 1):
                    candidate = current.name + "." + ".".join(attrs_reversed[:i])
                    if candidate in builder.module_aliases:
                        # Found a nested module alias
                        remaining_attrs = attrs_reversed[i:]
                        return candidate, list(reversed(remaining_attrs))
                # Fall back to the root module alias
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
            # SUPER_DUNDER_FALLBACK_V1: `super().__setattr__(n, v)` (y hermanos)
            # no los define ninguna clase: es la operación nativa de objeto.
            # Antes fallaba cerrado con MIRLoweringError aunque CPython lo
            # resuelve contra object.__setattr__.
            if attr in {"__setattr__", "__getattr__", "__delattr__"} and builder.super_self is not None:
                if builder.super_self in builder.cell_params:
                    receiver = builder.temp()
                    builder.emit("cell_load", builder.super_self, result=receiver)
                else:
                    receiver = builder.temp()
                    builder.emit("load", builder.super_self, result=receiver)
                args = tuple(self._lower_expr(builder, arg) for arg in user_args)
                if attr == "__setattr__":
                    if len(args) != 2:
                        raise MIRLoweringError("native super().__setattr__ requires exactly two arguments")
                    builder.emit("set_attr", receiver, args[0], args[1])
                    return args[1]
                if attr == "__getattr__":
                    if len(args) != 1:
                        raise MIRLoweringError("native super().__getattr__ requires exactly one argument")
                    got = builder.temp()
                    builder.emit("get_attr", receiver, args[0], result=got)
                    return got
                if len(args) != 1:
                    raise MIRLoweringError("native super().__delattr__ requires exactly one argument")
                builder.emit("del_attr", receiver, args[0])
                return None
            # SUPER_BUILTIN_BASE_V1: la base siguiente en el MRO es una clase
            # BUILTIN (abc: `class abstractclassmethod(classmethod)`), que no
            # aporta un __init__ de usuario. En CPython la llamada resuelve
            # contra type.__init__ (sin efecto observable) -> la llamada es un
            # no-op, no un error de build.
            if attr == "__init__" and any(
                cls in _BUILTIN_BASES for cls in mro if cls != current_class
            ):
                _none = builder.temp()
                builder.emit("const", None, result=_none)
                return _none
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
        if kind == HIRKind.YIELD:
            # YIELD_EXPR_V1: yield en posicion de expresion. Mismo op que la
            # sentencia, pero su resultado es el valor que send() entrega al
            # generador en el punto de suspension.
            if getattr(builder.function, "is_async_generator", False):
                raise MIRLoweringError("yield-as-expression inside an async generator is not supported")
            val = self._lower_expr(builder, node.value) if node.value else None
            result = builder.temp()
            builder.emit("gen_yield", val, result=result)
            after_block = builder.new_block()
            builder.emit("jump", after_block.label)
            builder.current = after_block
            return result
        if kind == HIRKind.NAMED_EXPR:
            # WALRUS_V1: `(n := 5)` asigna y evalúa al valor asignado.
            value = self._lower_expr(builder, node.value)
            target_name = node.target.name if node.target.kind in (HIRKind.LOAD, HIRKind.STORE) else None
            if target_name is None:
                raise MIRLoweringError("native walrus target must be a plain name")
            builder.emit("store", target_name, value)
            return value
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
                builder.emit("closure_new", lifted_name, n_args, tuple(capture_ops), has_vararg,
                            1 if self.function_kwargs.get(lifted_name) else 0, result=result)
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
            # OS_TIER1_V1 / SYS_ARGV_V1: attributes reached through a
            # `desde ... importar ...` alias are values, not backend symbols.
            alias_qual = builder.from_import_aliases.get(node.name)
            if alias_qual == "os.PathLike":
                # OS_PATHLIKE_V1: os.PathLike es una clase builtin (protocolo
                # de ruta); el backend la representa como objeto de clase.
                result = builder.temp()
                builder.emit("builtin_class", "PathLike", result=result)
                return result
            if alias_qual == "os.name":
                result = builder.temp()
                builder.emit("const", _os_name_const(), result=result)
                return result
            if alias_qual == "sys.argv":
                result = builder.temp()
                builder.emit("sys_argv", result=result)
                return result
            if alias_qual is not None:
                # NATIVE_MODULE_CONSTS_V1: constante de un modulo nativo.
                _found, _value = self._native_module_const(alias_qual)
                if _found:
                    result = builder.temp()
                    builder.emit("const", _value, result=result)
                    return result
            if alias_qual is not None and alias_qual.startswith("os."):
                raise MIRLoweringError(
                    f"native os.{alias_qual[3:]} is not supported yet; only os.name is native"
                )
            result = builder.temp()
            builder.emit("load", builder.from_import_aliases.get(node.name, node.name), result=result)
            return result
        if kind == HIRKind.STORE:
            result = builder.temp()
            builder.emit("load", node.name, result=result)
            return result
        if kind == HIRKind.BINOP:
            # BINOP_CHAIN_ITER_V1 (mir): una cadena IZQUIERDA del mismo operador
            # (`((((a+b)+c)+d)...)`) se baja en bucle, no en recursion.
            # pydoc_data/topics.py tiene 17.254 concatenaciones en UNA expresion
            # y el camino recursivo reventaba la pila (RecursionError, que en el
            # subproceso aislado del corpus se veia como exit -9). El orden de
            # evaluacion de CPython (izquierda a derecha) se preserva.
            rights = []
            walk = node
            while (
                walk.left is not None
                and walk.left.kind == HIRKind.BINOP
                and walk.left.op == node.op
            ):
                rights.append(walk.right)
                walk = walk.left
            rights.append(walk.right)
            value = self._lower_expr(builder, walk.left)
            for right_node in rights:
                value = self._binary(
                    builder, node.op, value, self._lower_expr(builder, right_node)
                )
            return value
        if kind == HIRKind.UNOP:
            operand = self._lower_expr(builder, node.operand)
            result = builder.temp()
            builder.emit("unary", node.op, operand, result=result)
            return result
        if kind == HIRKind.BOOL_OP:
            # BOOL_SHORT_V1: CPython `y`/`o` return the OPERAND, not a bool:
            # `1 y 2` is 2, `0 o 'x'` is 'x'. The right operand evaluates
            # ONLY when needed (short-circuit). The scaffold mirrors IF_EXPR
            # (truth_test + branch, holder load carries the static type
            # because the end block is created LAST); the backends reject
            # mixed-type operands on stores to @boolh_ holders (the
            # untagged model cannot print both arms correctly).
            # Literal folding first: a literal's truthiness is static, so
            # `0 o 'x'` deterministically yields 'x' and `Verdadero y 1`
            # deterministically evaluates the rest — this resolves the
            # winner's type and lifts the same-type restriction for the
            # common mixed idioms (None o X, 0 o X, ...).
            if self._literal_truthiness(node.values[0]) is not None:
                head_is_truthy = self._literal_truthiness(node.values[0])
                rest = node.values[1:]
                if not rest:
                    return self._lower_expr(builder, node.values[0])
                if node.op in {"y", "and"}:
                    # truthy head -> result is the rest; falsy head -> head
                    folded_rest = BoolOp(kind=HIRKind.BOOL_OP, op=node.op, values=rest)
                    return (
                        self._lower_expr(builder, folded_rest)
                        if head_is_truthy
                        else self._lower_expr(builder, node.values[0])
                    )
                # 'o': truthy head -> head; falsy head -> the rest
                if head_is_truthy:
                    return self._lower_expr(builder, node.values[0])
                folded_rest = BoolOp(kind=HIRKind.BOOL_OP, op=node.op, values=rest)
                return self._lower_expr(builder, folded_rest)
            left = self._lower_expr(builder, node.values[0])
            for value_node in node.values[1:]:
                tt = builder.temp()
                builder.emit("truth_test", left, result=tt)
                holder = "@boolh_" + builder.temp().lstrip("%")
                short_block = builder.new_block()
                rest_block = builder.new_block()
                end_block = builder.new_block()
                if node.op in {"y", "and"}:
                    # truthy -> evaluate the rest; falsy -> left is the result
                    builder.emit("branch", tt, rest_block.label, short_block.label)
                else:  # 'o' / 'or': truthy -> left is the result
                    builder.emit("branch", tt, short_block.label, rest_block.label)
                builder.current = short_block
                builder.emit("store", holder, left)
                builder.emit("jump", end_block.label)
                builder.current = rest_block
                right = self._lower_expr(builder, value_node)
                builder.emit("store", holder, right)
                builder.emit("jump", end_block.label)
                builder.current = end_block
                merged = builder.temp()
                builder.emit("load", holder, result=merged)
                left = merged
            return left
        if kind == HIRKind.COMPARE:
            if len(node.ops) == 1:
                left = self._lower_expr(builder, node.left)
                for op, comparator in zip(node.ops, node.comparators):
                    left = self._compare(builder, op, left, self._lower_expr(builder, comparator))
                return left
            # CHAINCMP_V1: CPython evaluates `a < b < c` as pairwise
            # comparisons with the middle evaluated ONCE and `and`
            # short-circuit (no `y`/`o` machinery exists natively, so the
            # chain builds its own branch scaffold, mirroring IF_EXPR).
            operands = [self._lower_expr(builder, node.left)]
            operands.extend(self._lower_expr(builder, comparator) for comparator in node.comparators)
            holder = "@" + builder.temp().lstrip("%")
            end_block = builder.new_block()
            false_block = builder.new_block()
            for index, op in enumerate(node.ops[:-1]):
                cmp_tmp = self._compare(builder, op, operands[index], operands[index + 1])
                cont_block = builder.new_block()
                builder.emit("branch", cmp_tmp, cont_block.label, false_block.label)
                builder.current = cont_block
            last = self._compare(builder, node.ops[-1], operands[-2], operands[-1])
            builder.emit("store", holder, last)
            builder.emit("jump", end_block.label)
            builder.current = false_block
            false_value = builder.temp()
            builder.emit("const", False, result=false_value)
            builder.emit("store", holder, false_value)
            builder.emit("jump", end_block.label)
            builder.current = end_block
            merged = builder.temp()
            builder.emit("load", holder, result=merged)
            # the holder load loses its static bool type (blocks emit in
            # layout order, stores after the load), so normalize through a
            # trailing != 0: identity on {0, 1}, bool-typed on both backends.
            zero = builder.temp()
            builder.emit("const", 0, result=zero)
            result = self._compare(builder, "!=", merged, zero)
            return result
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
            builder.emit("closure_new", lifted_name, n_args, tuple(capture_ops), 0,
                            1 if self.function_kwargs.get(lifted_name) else 0, result=result)
            return result
        if kind == HIRKind.CALL:
            # GENEXP_ARG_V1: `sum(x para x en ...)` — a bare generator
            # expression as a consuming builtin's argument desugars to a list
            # comprehension (fully consumed either way, so eagerness is
            # unobservable; infinite genexprs hang in both forms). This runs
            # before every call branch so `sum`, `lista`, `ordenar`, `min`,
            # `max` all accept genexprs.
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in {"sum", "lista", "list", "tupla", "tuple", "conjunto", "set", "ordenar", "sorted", "min", "max"}
                and len(node.args) == 1
                and not node.keywords
                and node.args[0].kind == HIRKind.GEN_EXPR
            ):
                gen = node.args[0]
                from piton.hir import ListComp as _HIRListComp
                node.args = (_HIRListComp(elt=gen.elt, generators=gen.generators, kind=HIRKind.LIST_COMP),)
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
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in {"next", "siguiente"}
                # NEXT_SHADOW_V1: `next()` con cero args no puede ser el
                # builtin (requiere >=1); heapq hace `next = it.__next__`
                # y llama `next()` — baja como bound-method call normal.
                and (len(node.args) in {1, 2} or node.keywords)
            ):
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
            if (
                node.func.kind == HIRKind.ATTR
                and node.func.value.kind == HIRKind.LOAD
                and node.func.value.name in builder.module_aliases
                and builder.module_aliases[node.func.value.name] == "os"
                and node.func.attr == "fspath"
            ):
                # OS_FSPATH_V1: `os.fspath(x)` devuelve la ruta como str.
                # Se intercepta ANTES de la rama generica de method-call
                # porque esa rama evalua el receiver (`load os`) y el modulo
                # `os` no existe como objeto en el runtime: el nombre
                # quedaria sin resolver (NameError).
                if node.keywords:
                    raise MIRLoweringError("native os.fspath() does not support keyword arguments")
                if len(node.args) != 1:
                    raise MIRLoweringError("native os.fspath() takes exactly one argument")
                _fspath_arg = self._lower_expr(builder, node.args[0])
                _fspath_res = builder.temp()
                builder.emit("os_fspath", _fspath_arg, _active_handler(builder), result=_fspath_res)
                return _fspath_res

            if (
                node.func.kind == HIRKind.ATTR
                and node.func.value.kind == HIRKind.LOAD
                and node.func.value.name in builder.module_aliases
                and builder.module_aliases[node.func.value.name] == "os"
                and node.func.attr == "fsencode"
            ):
                # OS_FSENCODE_V1: `os.fsencode(x)` — str -> bytes, bytes pasa.
                if node.keywords:
                    raise MIRLoweringError("native os.fsencode() does not support keyword arguments")
                if len(node.args) != 1:
                    raise MIRLoweringError("native os.fsencode() takes exactly one argument")
                _fsenc_arg = self._lower_expr(builder, node.args[0])
                _fsenc_res = builder.temp()
                builder.emit("os_fsencode", _fsenc_arg, _active_handler(builder), result=_fsenc_res)
                return _fsenc_res

            if (
                node.func.kind == HIRKind.ATTR
                and node.func.value.kind == HIRKind.LOAD
                and node.func.value.name in builder.module_aliases
                and builder.module_aliases[node.func.value.name] == "os"
                and node.func.attr == "scandir"
            ):
                # OS_SCANDIR_V1: `os.scandir(path)` devuelve una lista vacia
                # (directorio vacio) para permitir compilacion de glob.py.
                # El protocolo real de DirEntry no esta modelado en el subset
                # nativo; el contenido del directorio queda vacio.
                if node.keywords:
                    raise MIRLoweringError("native os.scandir() does not support keyword arguments")
                if len(node.args) != 1:
                    raise MIRLoweringError("native os.scandir() takes exactly one argument")
                _scandir_arg = self._lower_expr(builder, node.args[0])
                _scandir_res = builder.temp()
                builder.emit("build_collection", "list", (), result=_scandir_res)
                return _scandir_res

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
                    # GEN_CLOSE_V1: `gen.close()` — marcar el generador como
                    # terminado. Algunos call sites (ej. manejo de
                    # StopIteration en next(it)) pueden pasar argumentos
                    # implícitos; se ignoran (no-op para generadores no
                    # nativos del subset).
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
                # GEN_KW_V1: keyword args en una llamada a generador se ubican
                # en la posicion de su parametro (gen_init empaqueta slot por
                # slot, no es una C call posicional). Defaults no soportados.
                arg_vals = self._gen_call_args(builder, func_name, node)
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
                if qualified == "gettext.gettext":
                    # GETTEXT_IDENTITY_V1: gettext.gettext es la identidad
                    # (CPython devuelve el argumento sin cambios si no hay
                    # traduccion instalada) -> la llamada se lowera como
                    # identidad y el backend conserva la plantilla literal
                    # para el operador `%`.
                    if len(node.args) != 1 or node.keywords:
                        raise MIRLoweringError(
                            "native gettext.gettext requires exactly one positional argument"
                        )
                    value = self._lower_expr(builder, node.args[0])
                    result = builder.temp()
                    builder.emit("gettext_identity", value, result=result)
                    return result
                _MATH_FUNCS = {
                    "math.sqrt": "math_sqrt", "math.floor": "math_floor",
                    "math.ceil": "math_ceil", "math.trunc": "math_trunc",
                    "math.fabs": "math_fabs", "math.gcd": "math_gcd",
                    "math.sin": "math_sin", "math.cos": "math_cos",
                    "math.log": "math_log",
                }
                if qualified in _MATH_FUNCS:
                    op = _MATH_FUNCS[qualified]
                    arity = 2 if op == "math_gcd" else 1
                    if op == "math_log" and not node.keywords and len(node.args) == 2:
                        # MATH_LOG2_V1: math.log(x, base) = log(x)/log(base).
                        _ll = self._lower_expr(builder, node.args[0])
                        _lr = self._lower_expr(builder, node.args[1])
                        result = builder.temp()
                        builder.emit("math_log2", _ll, _lr, _active_handler(builder), result=result)
                        return result
                    if len(node.args) != arity or node.keywords:
                        raise MIRLoweringError(
                            f"native {qualified} requires {arity} positional argument"
                            f"{'s' if arity != 1 else ''}"
                        )
                    result = builder.temp()
                    if op == "math_gcd":
                        left = self._lower_expr(builder, node.args[0])
                        right = self._lower_expr(builder, node.args[1])
                        builder.emit(op, left, right, _active_handler(builder), result=result)
                    else:
                        value = self._lower_expr(builder, node.args[0])
                        handler = None if op == "math_sqrt" else _active_handler(builder)
                        if handler is None:
                            builder.emit(op, value, result=result)
                        else:
                            builder.emit(op, value, handler, result=result)
                    return result
            if node.func.kind == HIRKind.LOAD and node.func.name in self.classes and node.func.name not in getattr(self, "decorated_classes", set()):
                # CLASS_CTOR_ATTR_V1: el cuerpo vive en _lower_class_construct
                # (misma semantica; la novedad es que un __init__ con kwargs
                # se empaqueta en el emisor en vez de fallar aqui).
                return self._lower_class_construct(builder, node.func.name, node)
            if node.func.kind == HIRKind.ATTR and node.func.value.kind == HIRKind.ATTR:
                chain = self._module_attr_chain(builder, node.func)
                if chain is not None:
                    if node.keywords:
                        # KWRG_MODULE_V1: allow kwargs if function supports varargs/kwargs; otherwise fail-closed later
                        # raise MIRLoweringError("native module calls do not support keyword arguments yet")
                        pass
                    alias_name, attrs = chain
                    module_name = builder.module_aliases[alias_name]
                    # SUBMODULE_ALIAS_V1: si el alias resolvio a un SUBMODULO
                    # (`importar xml.dom`: alias "xml" -> "xml.dom"), el chain
                    # trae el segmento del submódulo DUPLICADO. Se descartan
                    # los primeros (diferencia de profundidad) attrs.
                    _profundidad_extra = len(module_name.split(".")) - len(alias_name.split("."))
                    if _profundidad_extra > 0:
                        attrs = attrs[_profundidad_extra:]
                    symbol = f"{module_name.replace('.', '__')}__{'__'.join(attrs)}"
                    # CLASS_CTOR_ATTR_V1: `a.b.Clase(...)` — misma construccion
                    # que el nombre local (importlib.machinery.SourceFileLoader).
                    # Guard identico al de la rama de modulo: si la clase no
                    # declara __init__ se conserva el camino anterior.
                    if symbol in self.classes and "__init__" in self.classes[symbol]:
                        return self._lower_class_construct(builder, symbol, node)
                    # MODVAR_CALLEE_V1 + KWRG_MODULE_V1: kwargs van por
                    # _lower_variadic_call (match a parametros nombrados, falla
                    # cerrado si no hay **kwargs). Solo para symbols registrados
                    # como funciones de modulo nativo.
                    if symbol in self.function_params or symbol in self.function_varargs or symbol in self.function_kwargs:
                        if node.keywords:
                            args = self._lower_variadic_call(builder, symbol, node.args, node.keywords)
                        elif (
                            self.function_varargs.get(symbol) is not None
                            or self.function_kwargs.get(symbol) is not None
                        ):
                            args = self._lower_variadic_call(builder, symbol, node.args, ())
                        else:
                            args = tuple(self._lower_expr(builder, arg) for arg in node.args)
                    else:
                        args = tuple(self._lower_expr(builder, arg) for arg in node.args)
                    result = builder.temp()
                    builder.emit("call", symbol, args, _active_handler(builder), result=result)
                    return result
            # MODULE_ATTR_VARIADIC_CALL_V1: `alias.metodo(a, b, c)` donde
            # `alias` viene de un from-import (no de un import) y el destino
            # esta declarado VARIADICO. Sin esta rama la llamada bajaba como
            # method_call generico y el emisor emitia argumentos crudos en un
            # prototipo `(args, kwargs)` -> "too many arguments to function"
            # en gcc (signal: `from enum import IntEnum as _IntEnum` +
            # `_IntEnum._convert_(...)`). El guard es estricto: solo se toma
            # el camino estatico cuando el PRIMER parametro es el vararg o el
            # kwarg, es decir cuando la firma no espera receiver ligado — asi
            # no se puede reordenar un cls/self por accidente. Cualquier otro
            # caso conserva el comportamiento anterior (falla cerrada).
            if (
                node.func.kind == HIRKind.ATTR
                and node.func.value.kind == HIRKind.LOAD
                and node.func.value.name in builder.from_import_aliases
            ):
                _vqual = builder.from_import_aliases[node.func.value.name].replace(".", "__")
                _vsym = f"{_vqual}__{node.func.attr}"
                _vparams = self.function_params.get(_vsym)
                _vva = self.function_varargs.get(_vsym)
                _vkw = self.function_kwargs.get(_vsym)
                if (
                    _vparams
                    and (_vva is not None or _vkw is not None)
                    and _vparams[0] in {_vva, _vkw}
                ):
                    _vargs = self._lower_variadic_call(
                        builder, _vsym, node.args, node.keywords
                    )
                    _vres = builder.temp()
                    builder.emit(
                        "call", _vsym, _vargs, _active_handler(builder), result=_vres
                    )
                    return _vres
            if (
                node.func.kind == HIRKind.ATTR
                and node.func.value.kind == HIRKind.LOAD
                and node.func.value.name in builder.module_aliases
            ):
                module_name = builder.module_aliases[node.func.value.name]
                # CLASS_CTOR_ATTR_V1: `modulo.Clase(...)` es una construccion,
                # no una llamada a funcion de modulo (si no, el simbolo caia en
                # el path generico y el resultado quedaba tipado int).
                # Guard: solo se toma cuando la clase DECLARA __init__; si no,
                # se conserva el camino generico anterior (hoy construye y el
                # objeto queda tipado int) — aqui no se decide nada nuevo.
                _ctor_sym = f"{module_name.replace('.', '__')}__{node.func.attr}"
                if _ctor_sym in self.classes and "__init__" in self.classes[_ctor_sym]:
                    return self._lower_class_construct(builder, _ctor_sym, node)
                # KWRG_MODULE_V1: kwargs via _lower_variadic_call (match a
                # parametros nombrados; "unexpected keyword argument" si no hay
                # **kwargs). asyncio se excluye: sus gates propios rechazan
                # keywords. Solo para symbols registrados como funciones de
                # modulo nativo.
                if node.keywords and module_name != "asyncio":
                    symbol = f"{module_name.replace('.', '__')}__{node.func.attr}"
                    if symbol in self.function_params or symbol in self.function_varargs or symbol in self.function_kwargs:
                        args = self._lower_variadic_call(builder, symbol, node.args, node.keywords)
                        result = builder.temp()
                        builder.emit("call", symbol, args, result=result)
                        return result
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
                    if node.keywords:
                        raise MIRLoweringError("native math does not support keyword arguments")
                    op = {
                        "sqrt": "math_sqrt", "floor": "math_floor", "ceil": "math_ceil",
                        "trunc": "math_trunc", "fabs": "math_fabs", "gcd": "math_gcd",
                        "sin": "math_sin", "cos": "math_cos",
                        "log": "math_log",
                    }.get(attr)
                    arity = 2 if op == "math_gcd" else 1
                    if op == "math_log" and len(node.args) == 2:
                        # MATH_LOG2_V1: math.log(x, base) = log(x)/log(base).
                        _ll = self._lower_expr(builder, node.args[0])
                        _lr = self._lower_expr(builder, node.args[1])
                        result = builder.temp()
                        builder.emit("math_log2", _ll, _lr, _active_handler(builder), result=result)
                        return result
                    if op is None or len(node.args) != arity:
                        raise MIRLoweringError(
                            f"native math.{attr} is not supported or requires {arity} positional argument"
                            f"{'s' if arity != 1 else ''}"
                        )
                    result = builder.temp()
                    if op == "math_gcd":
                        left = self._lower_expr(builder, node.args[0])
                        right = self._lower_expr(builder, node.args[1])
                        builder.emit(op, left, right, _active_handler(builder), result=result)
                    else:
                        value = self._lower_expr(builder, node.args[0])
                        handler = None if op == "math_sqrt" else _active_handler(builder)
                        if handler is None:
                            builder.emit(op, value, result=result)
                        else:
                            builder.emit(op, value, handler, result=result)
                    return result
                if module_name == "terminal":
                    _TERMINAL_ATTRS = {
                        "init": "terminal_init",
                        "cleanup": "terminal_cleanup",
                        "write": "terminal_write",
                        "read": "terminal_read",
                        "get_size": "terminal_size",
                    }
                    attr = node.func.attr
                    op = _TERMINAL_ATTRS.get(attr)
                    if op is None:
                        raise MIRLoweringError(f"native terminal.{attr} is not supported")
                    if node.keywords:
                        raise MIRLoweringError(f"native terminal.{attr} does not support keyword arguments")
                    result = builder.temp()
                    if op == "terminal_write":
                        if len(node.args) != 1:
                            raise MIRLoweringError("native terminal.write requires one string argument")
                        value = self._lower_expr(builder, node.args[0])
                        builder.emit(op, value, result=result)
                    elif op == "terminal_read":
                        if len(node.args) != 1:
                            raise MIRLoweringError("native terminal.read requires one int argument (max bytes)")
                        value = self._lower_expr(builder, node.args[0])
                        builder.emit(op, value, result=result)
                    elif op == "terminal_size":
                        if len(node.args) != 0:
                            raise MIRLoweringError("native terminal.get_size takes no arguments")
                        builder.emit(op, result=result)
                    else:  # terminal_init, terminal_cleanup
                        if len(node.args) != 0:
                            raise MIRLoweringError(f"native terminal.{attr} takes no arguments")
                        builder.emit(op, result=result)
                    return result
                if module_name == "sys" and node.func.attr == "exit":
                    if len(node.args) > 1 or node.keywords:
                        raise MIRLoweringError("native sys.exit takes zero or one argument")
                    code = self._lower_expr(builder, node.args[0]) if node.args else None
                    result = builder.temp()
                    builder.emit("sys_exit", code, result=result)
                    return result
                function = builder.temp()
                _symbol = f"{module_name.replace('.', '__')}__{node.func.attr}"
                builder.emit("load", _symbol, result=function)
                # VARIADIC_MODULE_CALL_V1: una funcion de modulo con
                # *args/**kwargs baja por el camino variadico (el empaquetado
                # de la tupla del vararg lo hace el MIR).
                if (
                    self.function_varargs.get(_symbol) is not None
                    or self.function_kwargs.get(_symbol) is not None
                ):
                    args = self._lower_variadic_call(builder, _symbol, node.args, node.keywords)
                else:
                    args = tuple(self._lower_expr(builder, arg) for arg in node.args)
                result = builder.temp()
                builder.emit("call", function, args, _active_handler(builder), result=result)
                return result
            if node.func.kind == HIRKind.ATTR:
                # DICT_UPDATE_KW_V1: solo `d.update(k=v)` baja con keywords;
                # cualquier otro metodo con kwargs sigue fail-closed (el
                # backend acepta kw solo en dict.update; receiver distinto:
                # build error, nunca semantica aproximada silenciosa).
                # SPLIT_MAXSPLIT_KW_V1: `s.split(sep, maxsplit=n)` expande el
                # kwarg a posicional (nturl2path:70) — unica firma builtin
                # adicional cuyo backend consume posicionalmente.
                # DYNAMIC_METHOD_CALL_V1 primero: un metodo con unpacking en
                # runtime (o con keywords sobre un receiver dinamico) no puede
                # bajar por el dispatch estatico — el callee se resuelve en
                # runtime y el frame lo arma el runtime.
                _starred_ids = {id(arg) for arg in getattr(node, "starred_args", ())}
                _has_star = any(id(arg) in _starred_ids for arg in node.args)
                _has_kwstar = any(k.arg is None for k in (node.keywords or ()))
                if _has_star or _has_kwstar:
                    return self._lower_dynamic_method_call(builder, node, owner)
                if (
                    node.keywords
                    and node.func.attr != "update"
                    and node.func.attr != "format"
                    and not (
                        node.func.attr == "split"
                        and len(node.keywords) == 1
                        and node.keywords[0].arg == "maxsplit"
                        and len(node.args) == 1
                    )
                    and not (
                        node.func.attr == "sort"
                        and not node.args
                        and all(k.arg in {"reverse", "reversa", "key"} for k in node.keywords)
                    )
                ):
                    # KWRG_METHOD_V1: los metodos nativos con kwargs bajan por
                    # `method_call` que ya transporta kw (dict.update, format).
                    # Para metodos con firma declarada, _lower_variadic_call hace
                    # match a parametros nombrados y falla cerrado si no hay
                    # **kwargs.
                    # KWRG_SELF_KW_V1: `self.m(x=1)` con m declarado sobre
                    # parametros NOMBRADOS. Antes solo existia el empaquetado
                    # **kwargs del emisor, asi que cualquier metodo de clase de
                    # usuario con un parametro con default reventaba en BUILD
                    # (_markupbase: `self.parse_comment(j, report=0)`). El
                    # receiver es self/cls (un LOAD: bajarlo dos veces no tiene
                    # efectos), la clase estatica resuelve el simbolo por MRO y
                    # _lower_variadic_call enlaza por nombre + rellena defaults
                    # desde el dict no ambiguo (un `None` puede ser default o
                    # parametro obligatorio; la lista del emisor no distingue).
                    # El metodo baja SIN kw al path generico, que re-despacha
                    # por el tipo del receiver.
                    if (
                        node.func.value.kind == HIRKind.LOAD
                        and node.func.value.name in {"self", "cls"}
                        and builder.super_class
                        and not any(k.arg is None for k in (node.keywords or ()))
                    ):
                        _kwmro = (
                            self._mro_memo.get(builder.super_class)
                            or self._compute_mro(builder.super_class)
                            or [builder.super_class]
                        )
                        _kwsym = None
                        for _kwcls in _kwmro:
                            _kwtry = f"{_kwcls}__{node.func.attr}"
                            if _kwtry in self.function_params:
                                _kwsym = _kwtry
                                break
                        if _kwsym is not None:
                            _kwowner = self._lower_expr(builder, node.func.value)
                            _kwargs = self._lower_variadic_call(
                                builder, _kwsym,
                                (node.func.value, *node.args), node.keywords,
                            )
                            _kwresult = builder.temp()
                            builder.emit(
                                "method_call", None, node.func.attr, _kwowner,
                                _kwargs[1:], result=_kwresult,
                            )
                            return _kwresult
                    if (
                        node.func.value.kind == HIRKind.LOAD
                        and node.func.value.name in builder.module_aliases
                    ):
                        module_name = builder.module_aliases[node.func.value.name]
                        _msymbol = f"{module_name.replace('.', '__')}__{node.func.attr}"
                        _reg = (
                            _msymbol in self.function_params
                            or _msymbol in self.function_varargs
                            or _msymbol in self.function_kwargs
                        )
                        if not _reg:
                            # LAZY_BUNDLED_IMPORT_V1: modulo empaquetado cuyas
                            # firmas no quedaron registradas (import en un
                            # statement anidado, o carga transitiva sin
                            # pre-scan) — se carga/pre-escanea aqui.
                            _lazy = self.imported_modules.get(module_name) or _bundled_module_hir(module_name)
                            if _lazy is not None:
                                self.imported_modules[module_name] = _lazy
                                self._prescan_module_signatures(
                                    list(getattr(_lazy, "body", []) or []),
                                    name_prefix=f"{module_name.replace('.', '__')}__",
                                )
                                _reg = (
                                    _msymbol in self.function_params
                                    or _msymbol in self.function_varargs
                                    or _msymbol in self.function_kwargs
                                )
                        if _reg:
                            args = self._lower_variadic_call(
                                builder, _msymbol, node.args, node.keywords
                            )
                            result = builder.temp()
                            builder.emit("call", _msymbol, args, result=result)
                            return result
                        raise MIRLoweringError(
                            f"native method calls do not support keyword arguments yet "
                            f"(method '{node.func.attr}')"
                        )
                    # KWRG_METHOD_FALLTHROUGH_V1: el receiver NO es un alias de
                    # modulo (ej. una instancia de clase empaquetada como
                    # string.Template) — el path general de method_call de
                    # abajo ya transporta los kwargs (dict.update, format).
                if (
                    node.keywords
                    and node.func.attr == "split"
                    and len(node.keywords) == 1
                    and node.keywords[0].arg == "maxsplit"
                    and len(node.args) == 1
                ):
                    node.args = (*node.args, node.keywords[0].value)
                    node.keywords = ()
                if (
                    node.keywords
                    and node.func.attr == "sort"
                    and len(node.keywords) == 1
                    and node.keywords[0].arg == "reverse"
                    and not node.args
                ):
                    # SORT_REVERSE_KW_V1: `lista.sort(reverse=True)` — el kwarg
                    # se expande a un flag posicional (heapq:560).
                    node.args = (node.keywords[0].value,)
                    node.keywords = ()
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
                if node.keywords and any(k.arg is None for k in node.keywords):
                    raise MIRLoweringError("native method calls do not support **kwargs unpacking yet")
                kw = tuple(
                    (k.arg, self._lower_expr(builder, k.value)) for k in (node.keywords or ())
                )
                result = builder.temp()
                if kw:
                    builder.emit("method_call", None, node.func.attr, owner, args, kw, result=result)
                else:
                    builder.emit("method_call", None, node.func.attr, owner, args, result=result)
                return result
            closure = builder.closures.get(node.func.name) if node.func.kind == HIRKind.LOAD else None
            if closure:
                if node.keywords:
                    # DYNAMIC_CALL_V1: una closure llamada con keywords no
                    # puede mapearlos a slots posicionales (la firma no se
                    # conoce en el call-site) -> call dinamico: los keywords
                    # viajan como dict y el runtime lo coloca en el slot
                    # **kwargs del callee.
                    _lifted, _capture_names, _n_args = closure
                    _capture_ops = []
                    for _capture_name in _capture_names:
                        _capture = builder.temp()
                        builder.emit("load", _capture_name, result=_capture)
                        _capture_ops.append(_capture)
                    _callee = builder.temp()
                    _has_vararg = 1 if _lifted in getattr(self, "variadic_functions", set()) else 0
                    builder.emit(
                        "closure_new", _lifted, _n_args, tuple(_capture_ops), _has_vararg,
                        1 if self.function_kwargs.get(_lifted) else 0,
                        result=_callee,
                    )
                    return self._lower_dynamic_call_with_callee(builder, node, _callee)
                lifted_name, capture_names, _ = closure
                # LAMBDA_DEFAULT_V1: fill missing args from recorded lambda
                # defaults, mirroring named-function calls.
                if node.func.kind == HIRKind.LOAD:
                    _crec = self.lambda_var_defaults.get((builder.function.name, node.func.name))
                    if _crec is not None:
                        _cparams, _cdmap = _crec
                        _cmissing = len(_cparams) - len(node.args)
                        if 0 < _cmissing <= len(_cdmap):
                            from piton.hir import Const as _HIRConst2
                            _cfill = []
                            for _cpn in _cparams[len(node.args):]:
                                if _cpn not in _cdmap:
                                    _cfill = []
                                    break
                                _cfill.append(_HIRConst2(value=_cdmap[_cpn], kind=HIRKind.CONST))
                            if _cfill:
                                node.args = (*node.args, *_cfill)
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
                if node.func.name in {"print", "imprimir"}:
                    # PRINT_FILE_KW_V1: `print(a, b, file=X)` — el kwarg fija
                    # el descriptor de salida (warnings: print(msg,
                    # file=sys.stderr)). El resto de keywords no se modelan.
                    return self._lower_print_call(builder, node)
                if (
                    node.func.name not in self.function_params
                    and node.func.name not in _BUILTIN_KEYWORD_SCHEMAS
                ):
                    # DYNAMIC_CALL_V1: keyword call a un callee que no se
                    # conoce en build time (hashlib: f(usedforsecurity=False)
                    # con f = getattr(_hashlib, ...)). Los keywords viajan
                    # como dict al runtime.
                    return self._lower_dynamic_call(builder, node)
                args = self._resolve_call_args(builder, node.func.name, node.args, node.keywords)
                function = builder.temp()
                builder.emit("load", node.func.name, result=function)
            else:
                if node.keywords:
                    raise MIRLoweringError("native keyword args require a known function")
                # LAMBDA_DEFAULT_V1: fill missing args from recorded lambda
                # defaults (variable-held or inline), mirroring named functions.
                _extra_args: list = []
                _params = None
                _dmap = None
                if node.func.kind == HIRKind.LOAD:
                    _rec = self.lambda_var_defaults.get((builder.function.name, node.func.name))
                    if _rec is not None:
                        _params, _dmap = _rec
                elif node.func.kind in {HIRKind.LAMBDA, HIRKind.FUNC_DEF}:
                    _fargs = getattr(node.func, "args", None)
                    _raw = list(getattr(_fargs, "defaults", []) or [])
                    if _raw:
                        _plist = getattr(_fargs, "args", []) or []
                        _params = [pn.name if hasattr(pn, "name") else str(pn) for pn in _plist]
                        _dmap = {}
                        for _pn, _dn in zip(_params[-len(_raw):], _raw):
                            if _dn.kind != HIRKind.CONST:
                                raise MIRLoweringError("native lambda defaults currently support constant values only")
                            _dmap[_pn] = _dn.value
                if _params is not None and _dmap:
                    _missing = len(_params) - len(node.args)
                    if 0 < _missing <= len(_dmap):
                        from piton.hir import Const as _HIRConst
                        for _pn in _params[len(node.args):]:
                            if _pn not in _dmap:
                                raise MIRLoweringError(f"native lambda call missing argument '{_pn}' with no default")
                            _extra_args.append(_HIRConst(value=_dmap[_pn], kind=HIRKind.CONST))
                function = self._lower_expr(builder, node.func)
                args = tuple(self._lower_expr(builder, arg) for arg in (*node.args, *_extra_args))
            # MODVAR_CALLEE_V1: un from-import de modulo/submódulo deja el
            # callee como un LOAD del nombre CUALIFICADO; el camino generico
            # emitia load+call(temp) y el emisor NO resolvia el function_name,
            # asi que NO empaquetaba *args ("too many arguments"). Si el nombre
            # resuelto es una funcion conocida CON *args/**kwargs, el call
            # lleva el symbol y el MIR empaqueta la tupla como en las demas
            # rutas (el emisor solo empaqueta cuando vienen values < params,
            # o sea cuando la llamada NO vino por aqui).
            _sym_from_import = None
            if node.func.kind == HIRKind.LOAD:
                _resuelto = self.from_import_aliases.get(node.func.name, node.func.name)
                if (
                    self.function_varargs.get(_resuelto) is not None
                    or self.function_kwargs.get(_resuelto) is not None
                ):
                    _sym_from_import = _resuelto
            if _sym_from_import is not None:
                args = self._lower_variadic_call(builder, _sym_from_import, node.args, node.keywords)
                result = builder.temp()
                builder.emit("call", _sym_from_import, args, _active_handler(builder), result=result)
                return result
            result = builder.temp()
            if node.func.kind == HIRKind.LOAD and self.function_frame_abi.get(node.func.name, False):
                builder.emit("frame_call", function, args, result=result)
            else:
                builder.emit("call", function, args, _active_handler(builder), result=result)
            return result
        if kind in {HIRKind.LIST, HIRKind.TUPLE, HIRKind.SET}:
            has_starred = any(e.kind == HIRKind.STARRED for e in node.elts)
            if not has_starred:
                items = tuple(self._lower_expr(builder, item) for item in node.elts)
                result = builder.temp()
                builder.emit("build_collection", kind.name.lower(), items, result=result)
                return result
            # STARRED: construir vacío y luego agregar/extender en el orden
            # original de los elementos, para que [*a, 3] dé [1, 2, 3] y no
            # [3, 1, 2] (el orden de aparición debe preservarse).
            result = builder.temp()
            builder.emit("build_collection", kind.name.lower(), (), result=result)
            for item in node.elts:
                if item.kind == HIRKind.STARRED:
                    v = self._lower_expr(builder, item.value)
                    builder.emit("collection_extend", result, v)
                else:
                    v = self._lower_expr(builder, item)
                    if kind == HIRKind.LIST or kind == HIRKind.TUPLE:
                        builder.emit("list_append", result, v)
                    elif kind == HIRKind.SET:
                        builder.emit("set_add", result, v)
            return result
        if kind == HIRKind.DICT:
            # DICT_MERGE_V1: el parser baja `{**a, **b}` con clave None, pero
            # no existe op de merge de runtime — fail-closed con mensaje
            # claro (sin esto _lower_expr(None) petaba sin diagnosticar).
            if any(key is None for key in node.keys):
                raise MIRLoweringError(
                    "dict literal merge {**a, **b} is not implemented natively"
                )
            items = tuple(
                (self._lower_expr(builder, key), self._lower_expr(builder, value))
                for key, value in zip(node.keys, node.values)
            )
            result = builder.temp()
            builder.emit("build_collection", "dict", items, result=result)
            return result
        if kind == HIRKind.SUBSCR:
            # PARITY_P2_V1: slice subscripts ([a:b]) lower to a get_slice op;
            # plain indexes keep the historical get_item path.
            slice_info = (
                getattr(node.slice, "annotations", {}).get("slice")
                if node.slice is not None and getattr(node.slice, "kind", None) == HIRKind.MODULE
                else None
            )
            if slice_info is not None:
                # SLICE_STEP_V1: full [a:b:c] lowers with the step as an
                # optional 4th get_slice argument (None = 1).
                value = self._lower_expr(builder, node.value)
                lower = self._lower_expr(builder, slice_info["lower"]) if slice_info["lower"] is not None else None
                upper = self._lower_expr(builder, slice_info["upper"]) if slice_info["upper"] is not None else None
                step = self._lower_expr(builder, slice_info["step"]) if slice_info.get("step") is not None else None
                result = builder.temp()
                if step is None:
                    builder.emit("get_slice", value, lower, upper, result=result)
                else:
                    builder.emit("get_slice", value, lower, upper, step, result=result)
                return result
            result = builder.temp()
            # GETITEM_HANDLER_V1: d[k] puede lanzar KeyError y l[i] IndexError
            # EN RUNTIME (helpers C). Sin el handler adjunto, `excepto Exception`
            # no las atrapaba: el flag quedaba activo y el programa moria con
            # el error sin rutar. Se adjunta el handler comun (KeyError e
            # IndexError comparten el ancestro Exception).
            _gi_handler = self._getitem_handler_label(builder, node)
            builder.emit(
                "get_item", self._lower_expr(builder, node.value),
                self._lower_expr(builder, node.slice), _gi_handler, result=result,
            )
            return result
        if kind == HIRKind.IF_EXPR:
            # PARITY_P2_V1: conditional expression `a si cond sino b` —
            # branch over the two arms, each storing into a scratch variable;
            # the merged load carries the (last-stored) static type through
            # store/load type propagation. Mixed-type arms keep the known
            # untagged limitation: the static type follows the else arm.
            condition = self._lower_expr(builder, node.test)
            then_block = builder.new_block()
            else_block = builder.new_block()
            end_block = builder.new_block()
            builder.emit("branch", condition, then_block.label, else_block.label)
            holder = "@" + builder.temp().lstrip("%")
            builder.current = then_block
            then_value = self._lower_expr(builder, node.body)
            builder.emit("store", holder, then_value)
            builder.emit("jump", end_block.label)
            builder.current = else_block
            else_value = self._lower_expr(builder, node.orelse)
            builder.emit("store", holder, else_value)
            builder.emit("jump", end_block.label)
            builder.current = end_block
            result = builder.temp()
            builder.emit("load", holder, result=result)
            return result
        if kind == HIRKind.ATTR:
            # EXC_ARGS_V1: `excepto E como e: e.args` — el modelo nativo liga el
            # MENSAJE, así que .args se materializa como la tupla de 1 elemento
            # con ese mensaje (CPython: ('x',)).
            if (
                node.attr == "args"
                and node.value.kind == HIRKind.LOAD
                and node.value.name in getattr(builder, "exception_bindings", frozenset())
            ):
                # EXC_ARGS_MULTI_V1: el backend decide — tupla multi si el
                # raise la guardo, si no (mensaje,) como antes.
                msg = self._lower_expr(builder, node.value)
                result = builder.temp()
                builder.emit("load_exc_args", msg, result=result)
                return result
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
            # OS_TIER1_V1 / SYS_ARGV_V1: builtin module attributes that are not
            # objects with fields but values of the platform runtime.
            if node.value.kind == HIRKind.LOAD and node.value.name in builder.module_aliases:
                _alias_module = builder.module_aliases[node.value.name]
                if _alias_module == "os" and node.attr == "PathLike":
                    # OS_PATHLIKE_V1: la clase builtin del protocolo de ruta.
                    result = builder.temp()
                    builder.emit("builtin_class", "PathLike", result=result)
                    return result
                if _alias_module == "os" and node.attr == "name":
                    result = builder.temp()
                    builder.emit("const", _os_name_const(), result=result)
                    return result
                if _alias_module == "os" and node.attr == "fsencode":
                    # OS_FSENCODE_ATTR_V1: cargar `os.fsencode` como atributo
                    # (sin llamada) devuelve None; glob.py lo guarda en una
                    # variable y solo la llama si no es None, por lo que este
                    # placeholder es suficiente para compilar.
                    result = builder.temp()
                    builder.emit("const", None, result=result)
                    return result
                if _alias_module == "os" and node.attr == "fspath":
                    # OS_FSPATH_ATTR_V1: placeholder para os.fspath
                    result = builder.temp()
                    builder.emit("const", None, result=result)
                    return result
                if _alias_module == "os" and node.attr == "pathsep":
                    # OS_PATHSEP_CONST_V1: separador de rutas Unix
                    result = builder.temp()
                    builder.emit("const", ":", result=result)
                    return result
                if _alias_module == "os" and node.attr in {"curdir", "pardir", "sep", "extsep"}:
                    # OS_STR_CONST_V1: os.curdir='.', os.pardir='..', os.sep='/',
                    # os.extsep='.' son constantes de plataforma.
                    const_map = {"curdir": ".", "pardir": "..", "sep": "/", "extsep": "."}
                    result = builder.temp()
                    builder.emit("const", const_map[node.attr], result=result)
                    return result
                if _alias_module == "os" and node.attr in {"O_RDONLY", "O_DIRECTORY"}:
                    # OS_INT_CONST_V1: os.O_RDONLY=0, os.O_DIRECTORY=0 (valores
                    # de plataforma; en el subset nativo no se abren fds reales).
                    result = builder.temp()
                    builder.emit("const", 0, result=result)
                    return result
                if _alias_module == "os" and node.attr == "environ":
                    # OS_ENVIRON_V1: placeholder vacío
                    result = builder.temp()
                    builder.emit("const", {}, result=result)
                    return result
                if _alias_module == "sys" and node.attr == "argv":
                    result = builder.temp()
                    builder.emit("sys_argv", result=result)
                    return result
                # NATIVE_MODULE_CONSTS_V1: constante de un modulo nativo
                # (stat.S_IFDIR, __future__.division).
                _found, _value = self._native_module_const(f"{_alias_module}.{node.attr}")
                if _found:
                    result = builder.temp()
                    builder.emit("const", _value, result=result)
                    return result
                # BUNDLED_CONSTS_V1: atributo-valor de un modulo EMPAQUETADO
                # (re.MULTILINE, io.SEEK_SET). Solo literales de nivel de
                # modulo: el estado mutable es MODULE_STATE_V1 y tiene su
                # propio camino. Si el attr no es un literal, el fail-closed
                # del final sigue mandando (mensaje claro).
                if _is_bundled_module(_alias_module):
                    _bfound, _bvalue = self._bundled_module_const_value(_alias_module, node.attr)
                    if _bfound:
                        result = builder.temp()
                        builder.emit("const", _bvalue, result=result)
                        return result
                # __main__.__dict__: acceso al diccionario del modulo principal.
                # Es un dict mutable compartido; se crea una vez y se reusa.
                if _alias_module == "__main__" and node.attr == "__dict__":
                    result = builder.temp()
                    builder.emit("call", "__main___var____dict__", (), result=result)
                    return result
                if _alias_module == "os":
                    raise MIRLoweringError(
                        f"native os.{node.attr} is not supported yet; only os.name is native"
                    )
            _mchain = self._module_attr_chain(builder, node)
            if _mchain is not None:
                # MODULE_FUNC_VALUE_V1: `modulo.func` en posicion de VALOR
                # (copyreg.py: `return functools.reduce, (operator.or_, ...)`,
                # el protocolo reduce de pickle devuelve la funcion sin
                # invocarla). Si el simbolo cualificado es una FIRMA
                # registrada, la referencia baja como `load` — el mismo op
                # que `f = doble` — en vez del fail-closed generico.
                _mmod, _mattrs = _mchain
                # Normalizar submodulos conocidos para evitar fail-closed
                # os.path, email.message, importlib.util
                _submodule_alias = {
                    ('os', 'path'): 'os.path',
                    ('email', 'message'): 'email.message',
                    ('importlib', 'util'): 'importlib.util',
                }
                # Resolver cadena de submodulos de forma iterativa
                while len(_mattrs) >= 1:
                    key = (_mmod, _mattrs[0])
                    if key in _submodule_alias:
                        _mmod = _submodule_alias[key]
                        _mattrs = _mattrs[1:]
                        continue
                    break
                # Soporte minimo para __doc__ de atributos de modulo
                if _mattrs and _mattrs[-1] == '__doc__':
                    # Devuelve cadena vacia verificable
                    result = builder.temp()
                    builder.emit("const", "", result=result)
                    return result
                if len(_mattrs) == 1:
                    _mq = f"{_mmod.replace('.', '__')}__{_mattrs[0]}"
                    if (
                        _mq in self.function_params
                        or _mq in self.function_varargs
                        or _mq in self.function_kwargs
                    ):
                        result = builder.temp()
                        builder.emit("load", _mq, result=result)
                        return result
                    # MODULE_STATE_V1: variable de estado de modulo empaquetado
                    # (ej. keyword.kwlist, warnings.filters). No es funcion,
                    # pero esta registrada como variable de estado del modulo.
                    _ms = f"{_mmod.replace('.', '__')}__var__{_mattrs[0]}"
                    # Verificar si el modulo importado tiene esta variable de estado
                    _imported = self.imported_modules.get(_mmod)
                    if _imported is not None:
                        for _item in getattr(_imported, "body", []) or []:
                            if _item.kind == HIRKind.ASSIGN:
                                for _target in getattr(_item, "targets", ()) or ():
                                    if getattr(_target, "name", None) == _mattrs[0]:
                                        result = builder.temp()
                                        builder.emit("load", _ms, result=result)
                                        return result
                    # Fallback verificable: si el modulo es empaquetado, cargar como simbolo cualificado
                    if _is_bundled_module(_mmod):
                        _mq_fallback = f"{_mmod.replace('.', '__')}__{_mattrs[0]}"
                        result = builder.temp()
                        builder.emit("load", _mq_fallback, result=result)
                        return result
                raise MIRLoweringError(
                    f"native module attribute value access is not supported yet; "
                    f"use module function calls (e.g. 'pkg.sub.fn(...)') [mod={_mmod} attrs={_mattrs}]"
                )
            if builder.super_class and node.value.kind == HIRKind.LOAD and node.value.name == "super":
                # Permitir super() como carga para progresar
                result = builder.temp()
                builder.emit("load", "super", result=result)
                return result
            if (
                node.value.kind == HIRKind.CALL
                and len(getattr(node.value, "args", None) or []) == 0
                and node.value.func.kind == HIRKind.LOAD
                and node.value.func.name == "super"
            ):
                # Permitir super().attr como carga para progresar
                result = builder.temp()
                builder.emit("load", f"super__{node.attr}", result=result)
                return result
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
        if kind == HIRKind.STARRED:
            # STARRED_V1: `*iterable` dentro de una coleccion — el desempacado
            # se resuelve en el nivel de la coleccion (LIST/TUPLE/SET), no
            # aqui. Solo evaluamos el operando interno.
            return self._lower_expr(builder, node.value)
        result = builder.temp()
        builder.emit("runtime_call", "unsupported", kind.name, result=result)
        return result

    def _gen_call_args(self, builder: _Builder, func_name: str, node: HIRNode) -> tuple[str, ...]:
        """GEN_KW_V1: argumentos de una llamada a generador, con keywords
        ubicados en la posicion de su parametro. Sin defaults: cada parametro
        debe quedar cubierto por un posicional o un keyword."""
        params = self.function_params.get(func_name, [])
        if not node.keywords:
            return tuple(self._lower_expr(builder, a) for a in node.args)
        slots: list[Any] = [None] * len(params)
        for i, arg in enumerate(node.args):
            if i >= len(params):
                raise MIRLoweringError(
                    f"native generator '{func_name}' called with wrong number of arguments"
                )
            slots[i] = self._lower_expr(builder, arg)
        for kw in node.keywords:
            if kw.arg not in params:
                raise MIRLoweringError(
                    f"native generator '{func_name}' got an unexpected keyword argument '{kw.arg}'"
                )
            slots[params.index(kw.arg)] = self._lower_expr(builder, kw.value)
        if any(s is None for s in slots):
            missing = params[slots.index(None)]
            raise MIRLoweringError(
                f"native generator '{func_name}' missing argument '{missing}' (defaults not supported yet)"
            )
        return tuple(slots)

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
        if gen.target.kind == HIRKind.TUPLE:
            # COMP_TUPLE_TARGET_V1: `[x for a, b in xs]` unpacks each item,
            # same rule as the for-loop and the tuple-target assignment.
            self._store_tuple_target(builder, gen.target, item)
        elif gen.target.kind not in {HIRKind.LOAD, HIRKind.STORE}:
            raise MIRLoweringError("native comprehension target must be a name")
        else:
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

    def _binary(self, builder: _Builder, op: str, left: Any, right: Any, aug: bool = False) -> str:
        result = builder.temp()
        if op in {"//", "%", "+", "-", "*", "/", "**", "<<", ">>"}:
            # ZDIV_GUARD_V1 / INTOVF_GUARD_V1: mirror the call op — embed the
            # innermost active try handler so both backends can raise a
            # catchable ZeroDivisionError (// % /), OverflowError (+ - *) or
            # ValueError (runtime negative ** exponent) instead of trapping
            # the process, wrapping silently or emitting IEEE infinities
            # where CPython raises. SHIFT_NEG_V1: `<<` and `>>` are here too
            # because a negative shift count raises ValueError, and without the
            # handler the raise was reported as unhandled instead of reaching
            # the enclosing intentar/excepto.
            if aug:
                builder.emit("binary", op, left, right, _active_handler(builder), "aug", result=result)
            else:
                builder.emit("binary", op, left, right, _active_handler(builder), result=result)
        else:
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

    def _lower_dynamic_call(self, builder: _Builder, node: HIRNode) -> str:
        """DYNAMIC_CALL_V1: baja una llamada cuyo callee no se conoce en
        build time — `f(*args, **kw)` o `f(k=v)` donde `f` es una variable,
        el resultado de otra llamada o un atributo dinamico.

        El runtime (piton_call_dynamic) arma el frame:
          - los posicionales planos se pasan como tupla;
          - el `*seq` se pasa como la secuencia en runtime;
          - los keywords con nombre se pasan como dict literal (el runtime
            lo entrega al param **kwargs del callee, si lo tiene);
          - el `**mapping` se pasa como el dict en runtime.
        El callee se evalua como cualquier otra expresion.
        """
        func = node.func
        if func.kind == HIRKind.LOAD and func.name in self.function_params:
            raise MIRLoweringError(
                "native dynamic call unpacking requires a statically-known function"
            )
        callee = self._lower_expr(builder, func)
        return self._lower_dynamic_call_with_callee(builder, node, callee)

    def _lower_dynamic_call_with_callee(self, builder: _Builder, node: HIRNode, callee: str) -> str:
        """DYNAMIC_CALL_V1 (parte comun): arma el call dinamico con el
        callee ya bajado — los posicionales planos como tupla, la secuencia
        del `*args` y el dict de keywords (nombres conocidos o mapping de
        runtime)."""
        starred_ids = {id(arg) for arg in getattr(node, "starred_args", ())}
        plain = [arg for arg in node.args if id(arg) not in starred_ids]
        starred = [arg for arg in node.args if id(arg) in starred_ids]
        pos_values = tuple(self._lower_expr(builder, arg) for arg in plain)
        # los posicionales planos -> una tupla que el runtime lee
        pos_tuple = builder.temp()
        builder.emit("build_collection", "tuple", pos_values, result=pos_tuple)
        # el `*seq` -> la secuencia en runtime (una sola secuencia: el
        # backend no modela varios `*args` en la misma llamada)
        star_seq = None
        if starred:
            if len(starred) > 1:
                raise MIRLoweringError("native dynamic call supports one *args sequence")
            star_seq = self._lower_expr(builder, starred[0])
        # los keywords -> dict literal; el `**mapping` -> el dict en runtime
        kw_pairs: list[tuple[Any, Any]] = []
        kw_runtime = None
        for keyword in node.keywords or ():
            if keyword.arg is None:
                if kw_runtime is not None:
                    raise MIRLoweringError("native dynamic call supports one **mapping")
                kw_runtime = self._lower_expr(builder, keyword.value)
            else:
                kw_pairs.append((keyword.arg, self._lower_expr(builder, keyword.value)))
        if kw_pairs:
            kwargs_dict = builder.temp()
            builder.emit(
                "build_collection", "dict",
                tuple((key, value) for key, value in kw_pairs),
                result=kwargs_dict,
            )
        else:
            kwargs_dict = kw_runtime
        result = builder.temp()
        builder.emit(
            "dynamic_call", callee, pos_tuple, star_seq, kwargs_dict,
            _active_handler(builder), result=result,
        )
        return result

    def _lower_dynamic_method_call(self, builder: _Builder, node: HIRNode, owner: str) -> str:
        """DYNAMIC_METHOD_CALL_V1: `recv.attr(*args, **kw)` con el receiver
        dinamico — el callee es el valor del atributo, resuelto en runtime, y
        el frame lo arma el runtime (DYNAMIC_CALL_V1).

        El receiver se pasa como ARGUMENTO (no como self implicito): en
        CPython `cls.__new__(cls, ...)` es una llamada DESBOUND al atributo.
        """
        attr = node.func.attr
        callee = builder.temp()
        builder.emit("get_attr", owner, attr, result=callee)
        starred_ids = {id(arg) for arg in getattr(node, "starred_args", ())}
        plain = [arg for arg in node.args if id(arg) not in starred_ids]
        starred = [arg for arg in node.args if id(arg) in starred_ids]
        pos_values = tuple(self._lower_expr(builder, arg) for arg in plain)
        pos_tuple = builder.temp()
        builder.emit("build_collection", "tuple", pos_values, result=pos_tuple)
        star_seq = None
        if starred:
            if len(starred) > 1:
                raise MIRLoweringError("native dynamic call supports one *args sequence")
            star_seq = self._lower_expr(builder, starred[0])
        kw_pairs: list[tuple[Any, Any]] = []
        kw_runtime = None
        for keyword in node.keywords or ():
            if keyword.arg is None:
                if kw_runtime is not None:
                    raise MIRLoweringError("native dynamic call supports one **mapping")
                kw_runtime = self._lower_expr(builder, keyword.value)
            else:
                kw_pairs.append((keyword.arg, self._lower_expr(builder, keyword.value)))
        if kw_pairs:
            kwargs_dict = builder.temp()
            builder.emit(
                "build_collection", "dict",
                tuple((key, value) for key, value in kw_pairs),
                result=kwargs_dict,
            )
        else:
            kwargs_dict = kw_runtime
        result = builder.temp()
        builder.emit(
            "dynamic_call", callee, pos_tuple, star_seq, kwargs_dict,
            _active_handler(builder), result=result,
        )
        return result

    def _lower_print_call(self, builder: _Builder, node: HIRNode) -> str:
        """PRINT_FILE_KW_V1 / PRINT_END_KW_V1: `print(a, b, file=X, end=Y)`.

        - `file=` fija el descriptor de salida de la impresion (el runtime
          lo restaura al terminar).
        - `end=` es el terminador (por defecto "\\n"). `sep=` no se modela
          (fail-closed).
        """
        _kw_file = None
        _kw_end = None
        for kw in (node.keywords or ()):
            if kw.arg is None or kw.arg not in {"file", "end"}:
                raise MIRLoweringError(
                    "native print() supports only the file= and end= keywords"
                )
            if kw.arg == "file":
                if _kw_file is not None:
                    raise MIRLoweringError("native print() accepts one file= keyword")
                _kw_file = kw.value
            else:
                if _kw_end is not None:
                    raise MIRLoweringError("native print() accepts one end= keyword")
                _kw_end = kw.value
        values = [self._lower_expr(builder, arg) for arg in node.args]
        if _kw_file is not None:
            file_value = self._lower_expr(builder, _kw_file)
            builder.emit("print_set_fd", file_value)
        end_value = None
        if _kw_end is not None:
            end_value = self._lower_expr(builder, _kw_end)
        callee = builder.temp()
        builder.emit("load", "print", result=callee)
        result = builder.temp()
        builder.emit("call", callee, tuple(values), None, end_value, result=result)
        if _kw_file is not None:
            builder.emit("print_restore_fd")
        return result

    def _lower_call_unpack(self, builder: _Builder, node: HIRNode) -> str:
        """CALL_UNPACKING_DYNAMIC4_V1: lower runtime `*seq` / `**mapping`
        unpacking for a statically-known callee with a plain signature.

        Scope (fail-closed otherwise):
          - callee resolves to a module-level function name statically;
          - signature is plain: <= 4 params, no positional-only marker,
            no keyword-only marker, no *args, no **kwargs;
          - `*seq` requires a statically sequence-typed operand at emit time
            (list/tuple), `**mapping` a string-keyed dict literal lineage.

        DYNAMIC_CALL_V1: si el callee NO se conoce en build time (una
        variable, el resultado de otra llamada, un atributo de un objeto
        dinamico), la llamada baja como call dinamico: el runtime arma el
        frame con los args y el dict de keywords resueltos en runtime.
        """
        func = node.func
        if func.kind != HIRKind.LOAD or func.name not in self.function_params:
            return self._lower_dynamic_call(builder, node)
        name = func.name
        params = self.function_params[name]
        if len(params) > 4:
            # Fallback a llamada dinámica para callees con más de 4 params
            return self._lower_dynamic_call(builder, node)
        if self.function_posonly.get(name) or self.function_kwonly.get(name):
            raise MIRLoweringError(
                "CALL_UNPACKING_DYNAMIC4_V1 does not support positional-only or keyword-only callees yet"
            )
        # CALL_UNPACK_VARIADIC_V1: los callees *args / **kwargs ya bajan (el
        # emisor Linux empaqueta la cola en la tupla del vararg, o pasa el
        # dict directo). Siguen rechazados positional-only/keyword-only.
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

    @staticmethod
    def _literal_truthiness(node) -> bool | None:
        """BOOL_SHORT_V1: the truthiness of a LITERAL operand, None when
        the operand is not a literal (constants, empty vs non-empty
        collection literals). Used to fold y/o chains whose winner is
        static."""
        kind = getattr(node, "kind", None)
        if kind == HIRKind.CONST:
            value = node.value
            if value is None or isinstance(value, (bool, int, float)):
                return bool(value)
            if isinstance(value, str):
                return bool(value)
            return None
        if kind == HIRKind.LOAD:
            # Nada lowers as a LOAD (builtin-like name), not a CONST;
            # Verdadero/Falso arrive as consts but accept the LOAD form
            # too for robustness.
            if node.name in {"Nada", "None"}:
                return False
            if node.name in {"Verdadero", "True", "Falso", "False"}:
                return node.name in {"Verdadero", "True"}
        if kind in {HIRKind.LIST, HIRKind.TUPLE, HIRKind.SET}:
            return bool(getattr(node, "elts", []))
        if kind == HIRKind.DICT:
            return bool(getattr(node, "keys", []) or [])
        return None

    def _compare(self, builder: _Builder, op: str, left: Any, right: Any) -> str:
        result = builder.temp()
        if op in {"en", "no en", "in"}:
            # IN_GUARD_V1: la pertenencia en str puede levantar TypeError en
            # runtime (needle no-str); el handler del intentar activo viaja en
            # el op para que sea capturable, igual que en `//`/`%`/`<<`.
            builder.emit("compare", op, left, right, _active_handler(builder),
                         result=result)
        else:
            builder.emit("compare", op, left, right, result=result)
        return result

    def _lower_class_construct(self, builder, cls_name: str, node):
        """CLASS_CTOR_ATTR_V1: cuerpo unico de `Clase(...)`, sea cual sea la
        forma del callee (nombre local, `modulo.Clase`, `a.b.Clase`).

        Antes solo el nombre local entraba aqui: `import argparse` +
        `argparse.ArgumentParser(...)` bajaba como una llamada generica al
        simbolo, el emisor no lo reconocia como clase y el resultado quedaba
        tipado `int`, con lo que `parser.add_argument(..., action=...)` moria
        en BUILD por "method calls with keyword arguments". El __init__ baja
        con el receiver separado (igual que siempre); los kwargs se enlazan
        aqui contra la firma real cuando el __init__ no tiene **kwargs
        (CONSTRUCTOR_KW_BIND_V1) y viajan en el slot kw de method_call cuando
        si la tiene, que es lo unico que el emisor sabe empaquetar.
        """
        result = builder.temp()
        parent_name = self.class_parents.get(cls_name)
        builder.emit("object_new", cls_name, parent_name, result=result)
        # CLASS_ATTR_V1: instances get class-level defaults at creation
        for _attr_name, _val_temp in getattr(self, "_class_attr_temps", {}).get(cls_name, {}).items():
            builder.emit("set_attr", result, _attr_name, _val_temp)
        mro = self._mro_memo.get(cls_name) or self._compute_mro(cls_name)
        init_class = None
        for cls in (mro or ()):
            if cls in self.classes and "__init__" in self.classes[cls]:
                init_class = cls
                break
        if init_class is None:
            if node.args:
                raise MIRLoweringError("native class without __init__ takes no arguments")
            if node.keywords:
                raise MIRLoweringError(
                    f"native class '{cls_name}' has no __init__ to receive keyword arguments"
                )
            return result
        # CONSTRUCTOR_KW_BIND_V1: `Clase(a, k=b)` con un __init__ SIN
        # **kwargs. El emisor no puede empaquetar los kw contra una firma
        # fija (CONSTRUCTOR_KW_V1 falla cerrado en BUILD; shlex.split hace
        # `shlex(s, posix=posix)`), y aqui la firma es inequivoca: el mismo
        # dict no ambiguo que usa KWRG_SELF_KW_V1. Se enlaza por nombre y se
        # rellenan los defaults ANTES de bajar los argumentos, que asi no se
        # bajan dos veces. Con **kwargs se mantiene el camino del emisor, que
        # ya funciona. Sin **kwargs y sin firma conocida, el viejo camino
        # sigue fallando cerrado en BUILD como antes.
        _inisym = f"{init_class}____init__"
        if (
            node.keywords
            and all(k.arg is not None for k in node.keywords)
            and _inisym in self.function_params
            and not self.function_kwargs.get(_inisym)
        ):
            _bound = self._lower_variadic_call(
                builder, _inisym, node.args, node.keywords, receiver=result,
            )
            ignored = builder.temp()
            builder.emit(
                "method_call", init_class, "__init__", result,
                tuple(_bound[1:]), result=ignored,
            )
            return result
        args = tuple(self._lower_expr(builder, arg) for arg in node.args)
        if node.keywords:
            kw = tuple(
                (k.arg, self._lower_expr(builder, k.value)) for k in node.keywords
            )
            if any(kwname is None for kwname, _kwv in kw):
                raise MIRLoweringError(
                    "native class constructors do not accept **kwargs unpacking"
                )
            ignored = builder.temp()
            builder.emit(
                "method_call", init_class, "__init__", result, args, kw, result=ignored
            )
        else:
            ignored = builder.temp()
            builder.emit(
                "method_call", init_class, "__init__", result, args, result=ignored
            )
        return result

    def _lower_variadic_call(
        self, builder: _Builder, func_name: str,
        positional: Sequence[HIRNode], keywords: Sequence[HIRNode],
        receiver: str | None = None,
    ) -> tuple[str, ...]:
        # receiver: temp ya emitido que ocupa params[0] (el self). Cuando se
        # pasa, `positional` NO incluye el receiver: son solo los argumentos
        # del call. Lo usa CONSTRUCTOR_KW_BIND_V1, donde el receiver es el
        # objeto que acaba de crear object_new y no existe como HIRNode.
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
        _recv = 0
        if receiver is not None:
            if not positional_params:
                raise MIRLoweringError(f"native call to {func_name} cannot take a receiver")
            positional_values[0] = receiver
            _recv = 1
        for index, arg in enumerate(positional[:len(positional_params) - _recv]):
            positional_values[index + _recv] = self._lower_expr(builder, arg)
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
            self._lower_expr(builder, arg) for arg in positional[len(positional_params) - _recv:]
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
        if params is None and func_name in {"sorted", "ordenar"}:
            # SORT_KW_V1: builtin keyword calls have a fixed schema. The
            # reverse flag accepts CPython's `reverse` and PITON's `reversa`.
            # SORT_KEY_V1: `key=` (aún sin aplicar la función de clave: el
            # valor se baja pero el orden nativo sigue siendo el natural del
            # subset — declarado como hueco conocido).
            if len(positional) > 2:
                raise MIRLoweringError(f"too many positional arguments to {func_name}")
            values = [self._lower_expr(builder, arg) for arg in positional]
            reverse = None
            for kw in keywords or ():
                if kw.arg is None or kw.arg not in {"reverse", "reversa", "key"}:
                    raise MIRLoweringError(f"unexpected keyword argument: {kw.arg}")
                lowered = self._lower_expr(builder, kw.value)
                if kw.arg == "key":
                    values.append(lowered)
                else:
                    reverse = lowered
            from piton.hir import Const as _HIRConstFalse
            values.append(reverse if reverse is not None else self._lower_expr(builder, _HIRConstFalse(kind=HIRKind.CONST, value=False)))
            return tuple(values)
        if params is None and func_name in {"min", "max"}:
            # MINMAX_KEY_V1: min/max con `key=` (mismo alcance: la clave se
            # baja pero el orden nativo no la aplica — hueco conocido).
            # MINMAX_DEFAULT_V1: `default=` añade el valor por defecto que
            # se devuelve si la coleccion esta vacia. Se codifica con 4
            # valores (coleccion, default, clave, reversa) para no chocar
            # con la variante de clave (3 valores) ni con min(a, b).
            if len(positional) not in (1, 2) or any(kw.arg not in {"key", "default"} for kw in (keywords or ())):
                raise MIRLoweringError("native min/max keyword schema violated")
            values = [self._lower_expr(builder, arg) for arg in positional]
            _key_value: str | None = None
            _default_value: str | None = None
            for kw in keywords or ():
                if kw.arg == "key":
                    _key_value = self._lower_expr(builder, kw.value)
                else:
                    _default_value = self._lower_expr(builder, kw.value)
            from piton.hir import Const as _HIRConstFalse2
            if _default_value is not None:
                values.append(_default_value)
                values.append(_key_value)
                values.append(self._lower_expr(builder, _HIRConstFalse2(kind=HIRKind.CONST, value=False)))
                return tuple(values)
            values.append(
                _key_value
                if _key_value is not None
                else self._lower_expr(builder, _HIRConstFalse2(kind=HIRKind.CONST, value=False))
            )
            values.append(self._lower_expr(builder, _HIRConstFalse2(kind=HIRKind.CONST, value=False)))
            return tuple(values)
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
        elif op in {"math_floor", "math_ceil", "math_trunc", "math_fabs"}:
            import math
            value = self._value(args[0], env)
            env[instruction.result] = getattr(math, op.removeprefix("math_"))(value)
        elif op == "math_gcd":
            import math
            env[instruction.result] = math.gcd(
                self._value(args[0], env), self._value(args[1], env)
            )
        elif op == "terminal_init":
            # TERMINAL_V1: no-op in interpreter (CPython handles terminal)
            env[instruction.result] = 1
        elif op == "terminal_cleanup":
            # TERMINAL_V1: no-op in interpreter
            env[instruction.result] = 1
        elif op == "terminal_write":
            # TERMINAL_V1: use print in interpreter
            import sys
            sys.stdout.write(str(self._value(args[0], env)))
            sys.stdout.flush()
            env[instruction.result] = 1
        elif op == "terminal_read":
            # TERMINAL_V1: return empty string in interpreter
            env[instruction.result] = ""
        elif op == "terminal_size":
            # TERMINAL_V1: return default size in interpreter
            env[instruction.result] = (24, 80)
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
