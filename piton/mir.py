"""MIR canónico mínimo para pruebas y trazas, no una VM de producción."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
import json
import operator
from typing import Any, Dict, List, Optional, Sequence

from piton.hir import Attr, BoolOp, Call, ExceptHandler, HIRKind, HIRNode, Keyword, Load, Module, With


_ZERO_ARG_SUPER = "__piton_zero_arg_super__"


def _zero_arg_super_targets(
    class_mro: dict[str, list[str]],
    classes: dict[str, set[str]],
    current_class: str,
    method: str,
) -> list[tuple[str, str]]:
    """Map each known runtime class to super()'s next C3 method owner."""
    targets: list[tuple[str, str]] = []
    for runtime_class, mro in class_mro.items():
        try:
            anchor_index = mro.index(current_class)
        except ValueError:
            continue
        target = next(
            (
                candidate
                for candidate in mro[anchor_index + 1:]
                if method in classes.get(candidate, set())
            ),
            None,
        )
        if target is not None:
            targets.append((runtime_class, target))
    return targets


_BUILTIN_EXCEPTIONS = {"Exception", "BaseException", "ValueError", "TypeError", "RuntimeError", "StopIteration", "AssertionError", "KeyError", "IndexError", "AttributeError", "NameError", "ZeroDivisionError", "OverflowError", "NotImplementedError", "ModuleNotFoundError"}
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
    "SystemExit", "KeyboardInterrupt", "StopAsyncIteration", "GeneratorExit", "UserWarning",
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
    "GeneratorExit": "BaseException",
}
_BUILTIN_EXCEPTIONS |= {"ExceptionGroup", "BaseExceptionGroup"}
# BASE_BUILTIN_V1: bases builtin sin definicion previa (ver uso en class loop).
# BASE_BUILTIN_V1: bases builtin sin definicion previa (ver uso en class loop).
# `staticmethod` (abc) y `property` son del mismo tipo: no aportan metodos
# de usuario, asi que un super() hacia ellas es un no-op.
_BUILTIN_BASES = {
    "object", "classmethod", "staticmethod", "property",
    "dict", "list", "tuple", "set", "frozenset",
    "str", "int", "float", "bytes", "bytearray",
    "Exception", "BaseException",
}
# NATIVE_MODULE_CONSTS_V1: modulos de la stdlib que el backend provee con
# sus CONSTANTES (no con codigo): `stat.S_IFDIR`, `__future__.division`...
# Los valores son los POSIX/estandar, no los del host. OJO: en CPython los
# `stat.S_IS*` son FUNCIONES (S_ISDIR(mode)) y los `__future__.*` son
# objetos _Feature — no son constantes, asi que no se proveen aqui (el
# backend falla cerrado al usarlos).
_NATIVE_MODULE_CONSTS: dict[str, dict[str, Any]] = {
    "math": {
        "pi": 3.141592653589793,
        "e": 2.718281828459045,
        "tau": 6.283185307179586,
    },
    "sys": {
        "maxsize": 9223372036854775807,
    },
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
    # "locale" NO va aqui: sus constantes (LC_*, CHAR_MAX) viven en
    # piton/stdlib_piton/locale.piton. Un modulo listado aqui nunca carga su
    # cuerpo empaquetado, y sus funciones quedan sin definir (NameError).
    "errno": {
        "EPERM": 1, "ENOENT": 2, "ESRCH": 3, "EINTR": 4, "EIO": 5, "ENXIO": 6,
        "E2BIG": 7, "ENOEXEC": 8, "EBADF": 9, "ECHILD": 10, "EAGAIN": 11,
        "EWOULDBLOCK": 11, "ENOMEM": 12, "EACCES": 13, "EFAULT": 14, "ENOTBLK": 15,
        "EBUSY": 16, "EEXIST": 17, "EXDEV": 18, "ENODEV": 19, "ENOTDIR": 20,
        "EISDIR": 21, "EINVAL": 22, "ENFILE": 23, "EMFILE": 24, "ENOTTY": 25,
        "ETXTBSY": 26, "EFBIG": 27, "ENOSPC": 28, "ESPIPE": 29, "EROFS": 30,
        "EMLINK": 31, "EPIPE": 32, "EDOM": 33, "ERANGE": 34, "EDEADLK": 35,
        "EDEADLOCK": 35, "ENAMETOOLONG": 36, "ENOLCK": 37, "ENOSYS": 38,
        "ENOTEMPTY": 39, "ELOOP": 40, "EOVERFLOW": 75, "EAFNOSUPPORT": 97,
        "EADDRINUSE": 98, "EADDRNOTAVAIL": 99, "ENETDOWN": 100, "ENETUNREACH": 101,
        "ECONNABORTED": 103, "ECONNRESET": 104, "ENOBUFS": 105, "EISCONN": 106,
        "ENOTCONN": 107, "ETIMEDOUT": 110, "ECONNREFUSED": 111, "EHOSTUNREACH": 113,
        "EALREADY": 114, "EINPROGRESS": 115, "ECANCELED": 125,
    },
}
# Builtins con schema FIJO de keywords (el resto de una llamada con
# keywords a un nombre desconocido baja como DYNAMIC_CALL_V1).
_BUILTIN_KEYWORD_SCHEMAS = {"sorted", "ordenar", "min", "max", "print", "imprimir", "open", "abrir"}


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
    default_params: List[str] = field(default_factory=list)
    posonly_params: List[str] = field(default_factory=list)
    kwonly_params: List[str] = field(default_factory=list)
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
            "posonly_params": list(self.posonly_params),
            "kwonly_params": list(self.kwonly_params),
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
    # METACLASS_CONFLICT_V1: metaclase efectiva por clase ("type" por defecto);
    # `type(A)` devuelve este objeto de clase.
    class_metaclasses: dict = field(default_factory=dict)
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
    function_aliases: dict = field(default_factory=dict)

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
    "json_dumps": "OPAQUE",
    "json_loads": "OPAQUE",
    "json_alias_set": "WRITE",
    "heapq_op": "WRITE",
    "native_call": "OPAQUE",
    "json_load_file": "OPAQUE",
    "json_value_used": "WRITE",
    "json_alias_check": "OPAQUE",
    "math_sin": "PURE", "math_cos": "PURE", "math_log": "PURE", "math_log2": "PURE",
    "math_floor": "PURE", "math_ceil": "PURE", "math_trunc": "PURE",
    "math_fabs": "PURE", "math_gcd": "PURE",
    "math_isinf": "PURE", "math_isnan": "PURE", "math_radians": "PURE",
    "math_isfinite": "PURE", "math_copysign": "PURE", "math_modf": "PURE", "math_frexp": "PURE",
    "math_isqrt": "PURE", "math_ldexp": "PURE", "math_degrees": "PURE", "math_atan2": "PURE", "math_isclose": "PURE",
    "math_factorial": "PURE", "math_comb": "PURE", "math_perm": "PURE",
    "terminal_init": "PURE", "terminal_size": "PURE", "terminal_write": "PURE",
    "object_new": "PURE", "closure_new": "PURE", "cell_new": "PURE",
    "type_new": "OPAQUE", "type_call": "OPAQUE",
    # DYNAMIC_CALL_V1: llamada a un callable desconocido (el frame lo arma
    # el runtime; no hay efecto observable en build).
    "dynamic_call": "PURE",
    # FMT_NS_V1: str.format(**mapping) resuelto en runtime (levanta KeyError
    # como mucho; no escribe estado observable).
    "str_format_ns": "PURE",
    # OS_FSPATH_V1 / OS_PATHLIKE_V1: valores nativos de `os`.
    "os_fspath": "PURE", "os_fsencode": "PURE", "builtin_class": "PURE",
    "os_stat_check": "READ", "os_makedirs": "OPAQUE", "tempfile_mkdtemp": "OPAQUE", "fs_glob": "OPAQUE",
    # RAISE_VALUE_V1: levantar una instancia (no una clase).
    "raise_value": "OPAQUE",
    # PRINT_FD_V1: fijar/restaurar el descriptor de salida de print.
    "print_set_fd": "PURE", "print_restore_fd": "PURE",
    "build_collection": "PURE", "genexpr_new": "PURE", "slot_box": "PURE", "gen_init": "PURE",
    "gather_new": "PURE",
    # READ
    "cell_load": "READ", "get_item": "READ", "get_slice": "READ", "collection_len": "READ",
    "truth_test": "READ",
    "unpack_check": "READ",
    "catch_type": "READ", "catch_message": "READ", "catch_flag": "READ",
    # EXCEPT_CLASS_CHECK_V1: filtra el handler por clase (no escribe estado).
    "catch_class_check": "READ",
    "load_exc_args": "READ",
    # WRITE
    "cell_store": "WRITE", "dict_put": "WRITE", "list_append": "WRITE",
    "del_item": "WRITE", "del_name": "PURE", "del_slice": "WRITE",
    "set_add": "WRITE",
    "collection_extend": "WRITE",
    "try_push": "WRITE", "try_pop": "WRITE", "subscript_store": "WRITE",
    "slice_store": "WRITE",
    "catch_bind": "WRITE", "catch_clear": "WRITE", "reraise_save": "WRITE", "reraise_clear": "WRITE",
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
        self._finally_stack: list[tuple[Any, int]] = []
        self.value_classes: dict[str, str] = {}
        self.name_classes: dict[str, str] = {}
        self.module_aliases: dict[str, str] = {}
        self.from_import_aliases: dict[str, str] = {}
        self.module_state_vars: dict[str, str] = {}
        # LOCAL_SHADOWS_FUNCTION_V1: keep lexical bindings separate from
        # module-level function symbols. A same-named local must be loaded
        # from its stack/cell slot, while an explicit global binding may use
        # the module's mutable function-value slot.
        self.local_names: set[str] = set()
        self.nonlocal_names: set[str] = set()
        self.global_names: set[str] = set()
        self.module_function_bindings: dict[str, str] = {}
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

    No se baja dentro de los cuerpos de FUNCION ni de CLASE: esas defs son
    closures o metodos, no funciones de modulo.
    """
    found: list = []
    for node in module_body or ():
        if getattr(node, "kind", None) == HIRKind.CLASS_DEF:
            continue
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


def _iter_hir(root):
    """Recorre todos los nodos HIR (dataclasses) del arbol, en orden."""
    if isinstance(root, (list, tuple)):
        for item in root:
            yield from _iter_hir(item)
        return
    if not is_dataclass(root):
        return
    yield root
    for f in fields(root):
        yield from _iter_hir(getattr(root, f.name, None))


def _json_binding_counts(root) -> dict[str, int]:
    """JSON_VALUE_ALIAS_V1: cuantas veces se liga cada nombre en TODO el programa
    (asignaciones, def, clase, parametros, for/with/except). Un alias a json.X solo
    es seguro si el nombre se liga exactamente una vez."""
    counts: dict[str, int] = {}

    def bump(name):
        if isinstance(name, str):
            counts[name] = counts.get(name, 0) + 1

    def walk(node):
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if not is_dataclass(node):
            return
        kind = getattr(node, "kind", None)
        if kind == HIRKind.STORE:
            bump(getattr(node, "name", None))
        if kind in {HIRKind.FUNC_DEF, HIRKind.CLASS_DEF}:
            bump(getattr(node, "name", None))
        if type(node).__name__ == "Arguments":
            for attr in ("posonlyargs", "args", "kwonlyargs"):
                for a in getattr(node, attr, []) or []:
                    bump(a)
            bump(getattr(node, "vararg", None))
            bump(getattr(node, "kwarg", None))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(root)
    return counts


class MIRLowerer:
    def __init__(self):
        self.functions: List[MIRFunction] = []
        self.generators: dict[str, tuple[HIRNode, ...]] = {}
        self.classes: dict[str, set[str]] = {}
        self.class_parents: dict[str, str | None] = {}
        self.class_base_list: dict[str, list[str]] = {}
        # METACLASS_CONFLICT_V1: metaclase efectiva por clase (nombre de
        # clase o "type"); sirve para fallar cerrado ante bases incompatibles.
        self.class_metaclasses: dict[str, str] = {}
        self.class_mro: dict[str, list[str]] = {}
        self._mro_memo: dict[str, list[str]] = {}
        self.class_properties: dict[str, dict[str, dict[str, str | None]]] = {}
        self.async_functions: set[str] = set()
        self.async_generators: set[str] = set()
        self.module_aliases: dict[str, str] = {}
        self._identity_decorator_names: set[str] = set()
        self._module_func_nodes: dict[str, HIRNode] = {}
        self._module_statement_positions: dict[int, int] = {}
        self._module_binding_positions: dict[str, int] = {}
        self._current_class_position: int | None = None
        self.decorated_class_methods: dict[tuple[str, str], str] = {}
        self.decorated_class_method_metadata: dict[str, dict[str, Any]] = {}
        self._decorated_method_names: set[str] = set()
        self.decorated_symbols: dict[int, str] = {}
        self.function_aliases: dict[str, str] = {}
        self._bundled_hir_cache: dict[str, Any] = {}
        self._bundled_modules_loaded: set[str] = set()
        self._local_classes: set[str] = set()
        # ZIP_ALIAS_SHADOW_V1: nombres de funciones de nivel modulo del HIR
        # que se esta bajando. El alias dialectal de zip no debe comerse una
        # funcion del usuario con el mismo nombre.
        self._module_function_names: set[str] = set()
        self._module_function_bindings: dict[str, str] = {}

    def _mod_prefix(self, module_name: str) -> str:
        norm = module_name.replace('.', '__')
        if module_name in self._local_classes or norm in self._local_classes:
            return f"_mod__{norm}__"
        return f"{norm}__"

    def _mod_func_symbol(self, module_name: str, func_name: str) -> str:
        return f"{self._mod_prefix(module_name)}{func_name}"

    def _module_function_binding(self, builder: "_Builder", name: str) -> str:
        """Resolve mutable module-function bindings after lexical scopes."""
        if name in builder.local_names or name in builder.nonlocal_names:
            return name
        return builder.module_function_bindings.get(name, name)

    def _mirror_class_import_alias(self, local_name: str, qualified_name: str) -> None:
        """Mirror a bundled class alias and route its methods to real symbols.

        Class aliases participate in dynamic receiver dispatch under both the
        qualified name and the imported local name. Without method aliases,
        the latter branch emitted an undefined C reference such as
        ``WeakSet__add`` even though ``_weakrefset__WeakSet__add`` existed.
        """
        if qualified_name not in self.classes or local_name in self.classes:
            return
        methods = set(self.classes[qualified_name])
        self.classes[local_name] = methods
        self.class_parents[local_name] = self.class_parents.get(qualified_name)
        self.class_base_list[local_name] = list(self.class_base_list.get(qualified_name, []))
        self.class_mro[local_name] = [qualified_name]
        self._mro_memo[local_name] = [qualified_name]

        defined = {function.name for function in self.functions}
        for method_name in methods:
            alias_symbol = f"{local_name}__{method_name}"
            real_symbol = f"{qualified_name}__{method_name}"
            if alias_symbol == real_symbol:
                continue
            if real_symbol not in defined:
                raise MIRLoweringError(
                    f"native imported class method alias '{alias_symbol}' has no lowered implementation '{real_symbol}'"
                )
            existing = self.function_aliases.get(alias_symbol)
            if (
                alias_symbol in defined
                or alias_symbol in self._module_function_names
                or (existing is not None and existing != real_symbol)
            ):
                raise MIRLoweringError(
                    f"native imported class method alias '{alias_symbol}' collides with another function"
                )
            self.function_aliases[alias_symbol] = real_symbol

    def _name_shadows_builtin(self, name: str, builder: "_Builder") -> bool:
        """ZIP_ALIAS_SHADOW_V1: True si `name` esta ligado por el usuario
        (funcion de modulo, local, param, closure, clase o alias de import)
        en este punto del bajado, en cuyo caso el alias dialectal del builtin
        (zip/combinar) NO debe aplicarse."""
        if name in self._module_function_names:
            return True
        if name in self.function_params or name in self.from_import_aliases:
            return True
        if name in self.classes:
            return True
        func = getattr(builder, "function", None)
        if func is not None and name in getattr(func, "params", ()):  # type: ignore[arg-type]
            return True
        if name in getattr(builder, "cell_params", ()):  # type: ignore[operator]
            return True
        if name in getattr(builder, "closures", {}):
            return True
        return False

    def _store_loop_target(self, builder: _Builder, target: str, value: str) -> None:
        """FOR_LOOP_CAPTURE_V1: a captured loop target must cell_store. A
        plain store would overwrite the cell pointer slot with the raw
        loop value and segfault the next cell_load (observed rc=-11)."""
        if target in builder.cell_params:
            builder.emit("cell_store", target, value)
        elif self._module_function_binding(builder, target) != target:
            builder.emit(
                "store", self._module_function_binding(builder, target), value,
                "internal",
            )
        else:
            builder.emit("store", target, value)

    def _store(self, builder: _Builder, target: HIRNode, value: str) -> None:
        if target.kind == HIRKind.STORE:
            _binding_name = self._module_function_binding(builder, target.name)
            _class_name = builder.value_classes.get(value)
            if _class_name is None:
                builder.name_classes.pop(target.name, None)
            else:
                builder.name_classes[target.name] = _class_name
            # MODULE_STATE_V1: una variable de nivel de modulo de un modulo
            # importado se guarda con su nombre CUALIFICADO (el global que
            # comparte con el resto del programa).
            _state_name = (getattr(builder, "module_state_vars", None) or {}).get(target.name)
            if _state_name:
                builder.emit("store", _state_name, value)
            elif target.name in builder.cell_params:
                builder.emit("cell_store", target.name, value)
            elif _binding_name != target.name:
                builder.emit("store", _binding_name, value, "internal")
            else:
                builder.emit("store", target.name, value)
        elif target.kind == HIRKind.ATTR:
            # ATTR_CHAIN_STORE_V1: el dueño puede ser cualquier expresion, no
            # solo un LOAD (`self._module.filters = x` es ATTR(ATTR(self,
            # _module), filters) y caia al runtime_call set_target, que el
            # backend no implementa).
            owner = self._lower_expr(builder, target.value)
            builder.emit("set_attr", owner, target.attr, value, _active_handler(builder))
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
                # Preserve absent bounds so native backends can apply the
                # correct defaults for negative steps as well as positive ones.
                builder.emit("slice_store", obj, lower, upper, step, value, _active_handler(builder))
                return
            idx = self._lower_expr(builder, target.slice)
            builder.emit("subscript_store", obj, idx, value, _active_handler(builder))
        elif target.kind == HIRKind.LOAD:
            # UNPACK_TARGET_LOAD_V1: el for-target de un genexpr baja como
            # LOAD (`for (k, v) in ...` en _compat_pickle) — es un nombre, se
            # guarda igual que STORE. Antes caia al runtime_call set_target,
            # que el backend no implementa.
            if target.name in builder.cell_params:
                builder.emit("cell_store", target.name, value)
            elif self._module_function_binding(builder, target.name) != target.name:
                builder.emit(
                    "store", self._module_function_binding(builder, target.name),
                    value, "internal",
                )
            else:
                builder.emit("store", target.name, value)
        else:
            builder.emit("runtime_call", "set_target", target.kind.name, value)

    @staticmethod
    def _direct_function_returns(node: HIRNode) -> list[HIRNode]:
        """Return expressions owned by a function, excluding nested scopes."""
        found: list[HIRNode] = []

        def visit(items) -> None:
            for item in items or ():
                kind = getattr(item, "kind", None)
                if kind in {HIRKind.FUNC_DEF, HIRKind.CLASS_DEF, HIRKind.LAMBDA}:
                    continue
                if kind == HIRKind.RETURN:
                    value = getattr(item, "value", None)
                    if value is not None:
                        found.append(value)
                    continue
                for field in ("body", "orelse", "finalbody"):
                    child = getattr(item, field, None)
                    if isinstance(child, list):
                        visit(child)
                for handler in getattr(item, "handlers", ()) or ():
                    visit(getattr(handler, "body", None))

        visit(getattr(node, "body", None))
        return found

    def _direct_function_free_loads(self, node: HIRNode) -> set[str]:
        """Names a function reads from an enclosing scope in its own body."""
        loaded: set[str] = set()
        bound: set[str] = set()
        global_names: set[str] = set()

        def visit(value: Any) -> None:
            if isinstance(value, (list, tuple)):
                for item in value:
                    visit(item)
                return
            kind = getattr(value, "kind", None)
            if kind == HIRKind.LOAD:
                name = getattr(value, "name", None)
                if isinstance(name, str):
                    loaded.add(name)
                return
            if kind == HIRKind.STORE:
                name = getattr(value, "name", None)
                if isinstance(name, str):
                    bound.add(name)
                return
            if kind == HIRKind.GLOBAL:
                global_names.update(getattr(value, "names", []) or [])
                return
            if kind in {HIRKind.FUNC_DEF, HIRKind.CLASS_DEF}:
                name = getattr(value, "name", None)
                if isinstance(name, str):
                    bound.add(name)
                # Decorators, bases, annotations, and defaults execute while
                # this function is defining the nested object. Its body does
                # not execute until later, so do not treat those loads as
                # current-scope closure reads.
                for field_name in ("decorators", "bases", "keywords", "returns", "args"):
                    visit(getattr(value, field_name, None))
                return
            if kind == HIRKind.LAMBDA:
                return
            try:
                descriptors = fields(value)
            except (TypeError, AttributeError):
                return
            for descriptor in descriptors:
                if descriptor.name in {"kind", "line", "col", "annotations"}:
                    continue
                visit(getattr(value, descriptor.name, None))

        visit(getattr(node, "body", None))
        args = getattr(node, "args", None)
        if args is not None:
            for arg in (
                *list(getattr(args, "posonlyargs", []) or []),
                *list(getattr(args, "args", []) or []),
                *list(getattr(args, "kwonlyargs", []) or []),
            ):
                name = getattr(arg, "name", arg if isinstance(arg, str) else None)
                if isinstance(name, str):
                    bound.add(name)
            for arg in (getattr(args, "vararg", None), getattr(args, "kwarg", None)):
                name = getattr(arg, "name", arg if isinstance(arg, str) else None)
                if isinstance(name, str):
                    bound.add(name)
        return loaded - bound - global_names

    def _factory_decorator_uses_captured_values(
        self, factory: HIRNode, expression: HIRNode,
    ) -> bool:
        """Detect factory parameters used while applying its returned decorator."""
        parameter_nodes = [
            *list(getattr(getattr(factory, "args", None), "posonlyargs", []) or []),
            *list(getattr(getattr(factory, "args", None), "args", []) or []),
            *list(getattr(getattr(factory, "args", None), "kwonlyargs", []) or []),
        ]
        parameter_names = [getattr(arg, "name", arg if isinstance(arg, str) else None) for arg in parameter_nodes]
        parameters = {name for name in parameter_names if isinstance(name, str)}
        args = getattr(factory, "args", None)
        for arg in (getattr(args, "vararg", None), getattr(args, "kwarg", None)):
            name = getattr(arg, "name", arg if isinstance(arg, str) else None)
            if isinstance(name, str):
                parameters.add(name)
        if not parameters:
            return False
        values: dict[str, HIRNode] = {}
        positional_values = list(getattr(expression, "args", []) or [])
        has_dynamic_positional = bool(getattr(expression, "starred_args", ()) or ())
        has_dynamic_keywords = False
        for index, value in enumerate(positional_values):
            if index < len(parameter_names) and isinstance(parameter_names[index], str):
                values[parameter_names[index]] = value
        for keyword in getattr(expression, "keywords", []) or []:
            name = getattr(keyword, "arg", None)
            if isinstance(name, str):
                values[name] = getattr(keyword, "value", None)
            else:
                has_dynamic_keywords = True
        nested = {
            getattr(item, "name", None): item
            for item in getattr(factory, "body", []) or []
            if getattr(item, "kind", None) == HIRKind.FUNC_DEF
        }
        for value in self._direct_function_returns(factory):
            target = nested.get(getattr(value, "name", None))
            if target is None:
                continue
            for captured in self._direct_function_free_loads(target) & parameters:
                if has_dynamic_positional or has_dynamic_keywords:
                    return True
                argument = values.get(captured)
                if (getattr(argument, "kind", None) != HIRKind.CONST
                        or type(getattr(argument, "value", None)) not in {int, float, bool, type(None)}):
                    return True
        return False

    def _function_returns_callable(self, node: HIRNode) -> bool:
        """Prove a decorator returns its input function or a native closure."""
        if (getattr(node, "kind", None) != HIRKind.FUNC_DEF
                or getattr(node, "decorators", None)
                or getattr(node, "is_async", False)):
            return False
        args = getattr(node, "args", None)
        positional = [
            *list(getattr(args, "posonlyargs", []) or []),
            *list(getattr(args, "args", []) or []),
        ]
        input_function = getattr(positional[0], "name", None) if positional else None
        nested = {
            getattr(item, "name", None): item
            for item in getattr(node, "body", []) or ()
            if getattr(item, "kind", None) == HIRKind.FUNC_DEF
        }
        returns = self._direct_function_returns(node)
        if not returns:
            return False
        for value in returns:
            if getattr(value, "kind", None) != HIRKind.LOAD:
                return False
            name = getattr(value, "name", None)
            if name == input_function:
                continue
            returned_function = nested.get(name)
            if returned_function is None:
                return False
            if (getattr(returned_function, "decorators", None)
                    and not self._has_functools_wraps(returned_function)):
                return False
        return True

    def _factory_returns_callable_decorator(self, node: HIRNode) -> bool:
        """Prove a decorator factory returns decorators with native output."""
        if (getattr(node, "kind", None) != HIRKind.FUNC_DEF
                or getattr(node, "decorators", None)
                or getattr(node, "is_async", False)):
            return False
        nested = {
            getattr(item, "name", None): item
            for item in getattr(node, "body", []) or ()
            if getattr(item, "kind", None) == HIRKind.FUNC_DEF
        }
        returns = self._direct_function_returns(node)
        if not returns:
            return False
        targets = [
            nested.get(getattr(value, "name", None))
            for value in returns
            if getattr(value, "kind", None) == HIRKind.LOAD
        ]
        return (
            len(targets) == len(returns)
            and all(target is not None and self._function_returns_callable(target) for target in targets)
        )

    def _has_functools_wraps(self, node: HIRNode) -> bool:
        for expression in getattr(node, "decorators", []) or ():
            target = getattr(expression, "func", None) if getattr(expression, "kind", None) == HIRKind.CALL else expression
            if getattr(target, "kind", None) == HIRKind.LOAD:
                qualified = self.from_import_aliases.get(getattr(target, "name", ""), "")
                if qualified.replace(".", "__") == "functools__wraps":
                    return True
            elif getattr(target, "kind", None) == HIRKind.ATTR and getattr(target, "attr", None) == "wraps":
                value = getattr(target, "value", None)
                if getattr(value, "kind", None) == HIRKind.LOAD:
                    module = self.module_aliases.get(getattr(value, "name", ""), getattr(value, "name", ""))
                    if module.replace(".", "__") == "functools":
                        return True
        return False

    def _function_preserves_wrapper_metadata(self, node: HIRNode) -> bool:
        """Prove a decorator returns its input or a functools.wraps wrapper."""
        if getattr(node, "kind", None) != HIRKind.FUNC_DEF:
            return False
        args = getattr(node, "args", None)
        positional = [
            *list(getattr(args, "posonlyargs", []) or []),
            *list(getattr(args, "args", []) or []),
        ]
        input_function = getattr(positional[0], "name", None) if positional else None
        nested = {
            getattr(item, "name", None): item
            for item in getattr(node, "body", []) or ()
            if getattr(item, "kind", None) == HIRKind.FUNC_DEF
        }
        returns = self._direct_function_returns(node)
        if not returns:
            return False
        for value in returns:
            if getattr(value, "kind", None) != HIRKind.LOAD:
                return False
            name = getattr(value, "name", None)
            if name == input_function:
                continue
            returned_function = nested.get(name)
            if returned_function is None or not self._has_functools_wraps(returned_function):
                return False
        return True

    def _method_decorators_preserve_metadata(self, expressions: list[HIRNode]) -> bool:
        for expression in expressions:
            is_factory = getattr(expression, "kind", None) == HIRKind.CALL
            callee = getattr(expression, "func", None) if is_factory else expression
            if getattr(callee, "kind", None) != HIRKind.LOAD:
                return False
            function = self._module_func_nodes.get(getattr(callee, "name", None))
            if function is None:
                return False
            if is_factory:
                nested = {
                    getattr(item, "name", None): item
                    for item in getattr(function, "body", []) or ()
                    if getattr(item, "kind", None) == HIRKind.FUNC_DEF
                }
                targets = [
                    nested.get(getattr(value, "name", None))
                    for value in self._direct_function_returns(function)
                    if getattr(value, "kind", None) == HIRKind.LOAD
                ]
                if not targets or len(targets) != len(self._direct_function_returns(function)):
                    return False
                if not all(target is not None and self._function_preserves_wrapper_metadata(target) for target in targets):
                    return False
            elif not self._function_preserves_wrapper_metadata(function):
                return False
        return bool(expressions)

    def _supported_method_decorator(
        self,
        expression: HIRNode,
        class_local_names: set[str],
    ) -> bool:
        """Prove an earlier module decorator evaluates to a native callable."""
        if self._current_class_position is None:
            return False
        is_factory = getattr(expression, "kind", None) == HIRKind.CALL
        callee = getattr(expression, "func", None) if is_factory else expression
        if getattr(callee, "kind", None) != HIRKind.LOAD:
            return False
        name = getattr(callee, "name", None)
        if not isinstance(name, str) or name in class_local_names:
            return False
        function_node = self._module_func_nodes.get(name)
        position = self._module_binding_positions.get(name)
        if (function_node is None or position is None
                or position >= self._current_class_position):
            return False

        # Decorator expressions execute in the class body. A class-local or
        # forward global reference cannot be replaced with a zero-valued C
        # global without changing CPython's NameError behavior.
        for part in self._walk(expression):
            if getattr(part, "kind", None) != HIRKind.LOAD:
                continue
            loaded_name = getattr(part, "name", None)
            if loaded_name in class_local_names:
                return False
            bound_at = self._module_binding_positions.get(loaded_name)
            if bound_at is None or bound_at >= self._current_class_position:
                return False

        if is_factory:
            return (
                self._factory_returns_callable_decorator(function_node)
                and not self._factory_decorator_uses_captured_values(function_node, expression)
            )
        return self._function_returns_callable(function_node)

    def _class_method_symbol(
        self,
        class_name: str,
        method: HIRNode,
        property_methods: dict[str, dict[str, str | None]],
        *,
        class_local_names: set[str] | None = None,
        allow_user_identity: bool = True,
    ) -> tuple[str, str | None, str]:
        """Classify a method decorator, returning its native method symbol."""
        decorators = list(getattr(method, "decorators", None) or [])
        if not decorators:
            return f"{class_name}__{method.name}", None, method.name
        unsupported = (
            "native class method decorators currently support only "
            "@property, @staticmethod, and @classmethod"
        )
        class_local_names = class_local_names or set()

        # User-defined, provably identity decorators have no observable effect
        # and remain safe to elide.
        if (allow_user_identity and len(decorators) == 1
                and decorators[0].kind == HIRKind.LOAD
                and getattr(decorators[0], "name", None) in self._module_func_nodes
                and getattr(decorators[0], "name", None) in self._identity_decorator_names
                and getattr(decorators[0], "name", None) not in class_local_names):
            return f"{class_name}__{method.name}", None, method.name

        # Descriptor decorators are special only as a single decorator.
        # Stacking an ordinary wrapper around one needs descriptor behavior the
        # native class model does not currently expose.
        if len(decorators) == 1:
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
            if decorator.kind == HIRKind.ATTR:
                prop = getattr(decorator, "attr", "")
                if prop == "cached_property":
                    raise MIRLoweringError(
                        "native @functools.cached_property is not supported: cached descriptor storage is unavailable"
                    )
                if (getattr(getattr(decorator, "value", None), "kind", None) == HIRKind.LOAD
                        and getattr(decorator.value, "name", None) == method.name
                        and prop in {"setter", "deleter"}):
                    existing = property_methods.get(method.name)
                    if existing is None:
                        raise MIRLoweringError(
                            f"native @{method.name}.{prop} requires the property getter '@property def {method.name}' first in class '{class_name}'"
                        )
                    if existing[prop] is not None:
                        raise MIRLoweringError(f"native duplicate @{method.name}.{prop} on '{class_name}'")
                    return f"{class_name}__{method.name}__{prop}", prop, method.name

        unsupported_results = {
            "abstractmethod", "contextmanager", "_sslcopydoc", "lru_cache",
            "cache", "overload", "cached_property",
        }
        for dec in decorators:
            name = getattr(dec, "name", None) or getattr(dec, "attr", None)
            if name in unsupported_results and name not in self._module_func_nodes:
                raise MIRLoweringError(
                    f"native class method decorator '@{name}' result cannot be represented natively"
                )
            if getattr(dec, "kind", None) == HIRKind.CALL:
                callee = getattr(dec, "func", None)
                factory = self._module_func_nodes.get(getattr(callee, "name", None))
                if (factory is not None
                        and self._factory_decorator_uses_captured_values(factory, dec)):
                    raise MIRLoweringError(
                        "native method decorator factories that read captured values while "
                        "applying the decorator are not supported"
                    )

        if all(self._supported_method_decorator(dec, class_local_names) for dec in decorators):
            if method.name.startswith("__") and method.name.endswith("__"):
                raise MIRLoweringError(
                    f"native decorated special method '{method.name}' is not supported by runtime dispatch"
                )
            original_symbol = f"__piton_original__{class_name}__{method.name}"
            decorated_value = f"__piton_decorated__{class_name}__{method.name}"
            self.decorated_class_methods[(class_name, method.name)] = decorated_value
            if self._method_decorators_preserve_metadata(decorators):
                body = getattr(method, "body", []) or []
                doc = (
                    body[0].value
                    if body and getattr(body[0], "kind", None) == HIRKind.CONST
                    and isinstance(getattr(body[0], "value", None), str)
                    else None
                )
                self.decorated_class_method_metadata[decorated_value] = {
                    "__name__": method.name,
                    "__doc__": doc,
                }
            self._decorated_method_names.add(method.name)
            return original_symbol, "decorated", method.name

        raise MIRLoweringError(unsupported)

    @staticmethod
    def _is_identity_decorator_definition(node: HIRNode) -> bool:
        """True only for an undecorated one-argument `return parameter` def."""
        if (getattr(node, "kind", None) != HIRKind.FUNC_DEF
                or getattr(node, "decorators", None)
                or getattr(node, "is_async", False)
                or getattr(node, "returns", None)
                or getattr(node, "type_params", None)):
            return False
        args = getattr(node, "args", None)
        if args is None:
            return False
        parameters = [
            *list(getattr(args, "posonlyargs", []) or []),
            *list(getattr(args, "args", []) or []),
        ]
        if (len(parameters) != 1
                or getattr(args, "kwonlyargs", None)
                or getattr(args, "vararg", None)
                or getattr(args, "kwarg", None)
                or getattr(args, "defaults", None)
                or any(value is not None for value in (getattr(args, "kw_defaults", []) or []))):
            return False
        body = list(getattr(node, "body", []) or [])
        if len(body) != 1 or getattr(body[0], "kind", None) != HIRKind.RETURN:
            return False
        value = getattr(body[0], "value", None)
        return (getattr(value, "kind", None) == HIRKind.LOAD
                and getattr(value, "name", None) == parameters[0])

    def _module_identity_decorator_names(self, module_body: list[HIRNode]) -> set[str]:
        """Find unambiguous direct module defs that are provably identity functions.

        Bindings introduced by other module-scope statements invalidate a
        candidate, including assignments nested in module control-flow. This
        intentionally rejects indirect, conditionally defined, or shadowed
        decorator names instead of guessing which callable CPython resolves.
        """
        bindings: dict[str, int] = {}
        candidates: dict[str, HIRNode] = {}
        has_star_import = False

        def bind(name: str | None) -> None:
            if isinstance(name, str):
                bindings[name] = bindings.get(name, 0) + 1

        def visit_module_scope(value: Any) -> None:
            nonlocal has_star_import
            if isinstance(value, (list, tuple)):
                for item in value:
                    visit_module_scope(item)
                return
            if not isinstance(value, HIRNode):
                return
            kind = getattr(value, "kind", None)
            if kind == HIRKind.FUNC_DEF:
                bind(getattr(value, "name", None))
                return
            if kind == HIRKind.CLASS_DEF:
                bind(getattr(value, "name", None))
                return
            if kind == HIRKind.STORE:
                bind(getattr(value, "name", None))
            if kind == HIRKind.DELETE:
                for target in getattr(value, "targets", []) or []:
                    bind(getattr(target, "name", None))
            if kind == HIRKind.IMPORT and hasattr(value, "names"):
                for alias in getattr(value, "names", []) or []:
                    name = getattr(alias, "asname", None) or getattr(alias, "name", "")
                    bind(name.split(".", 1)[0] if name else None)
            elif kind == HIRKind.IMPORT_FROM:
                if getattr(value, "is_star", False):
                    has_star_import = True
                else:
                    for alias in getattr(value, "names", []) or []:
                        bind(getattr(alias, "asname", None) or getattr(alias, "name", None))
            for descriptor in fields(value):
                if descriptor.name in {"kind", "line", "col", "annotations"}:
                    continue
                visit_module_scope(getattr(value, descriptor.name, None))

        for item in module_body or []:
            if getattr(item, "kind", None) == HIRKind.FUNC_DEF:
                bind(item.name)
                if self._is_identity_decorator_definition(item):
                    candidates[item.name] = item
            elif getattr(item, "kind", None) == HIRKind.CLASS_DEF:
                bind(item.name)
            else:
                visit_module_scope(item)
        if has_star_import:
            return set()
        return {name for name in candidates if bindings.get(name) == 1}

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

    def _collect_json_aliases(self, hir) -> None:
        """JSON_VALUE_ALIAS_V2: `f = json.X` (o `f = X` con X de `desde json importar`)
        con el nombre ligado una sola vez en el programa. La asignacion fija una
        bandera de ligado en runtime y cada llamada la comprueba (NameError si aun
        no se ejecuto), igual que CPython, tambien desde funciones y bucles."""
        json_mod: set[str] = set()
        json_from: dict[str, str] = {}
        for node in _iter_hir(hir):
            if node.kind == HIRKind.IMPORT:
                for alias in getattr(node, "names", []) or []:
                    if alias.name == "json":
                        json_mod.add(alias.asname or alias.name)
            elif node.kind == HIRKind.IMPORT_FROM and getattr(node, "module", None) == "json":
                for alias in getattr(node, "names", []) or []:
                    json_from[alias.asname or alias.name] = alias.name
        for node in _iter_hir(hir):
            if node.kind != HIRKind.ASSIGN or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if target.kind != HIRKind.STORE or self._json_binding_counts.get(target.name, 0) != 1:
                continue
            value = node.value
            attr = None
            if value is not None and value.kind == HIRKind.ATTR and value.value is not None \
                    and value.value.kind == HIRKind.LOAD and value.value.name in json_mod:
                attr = value.attr
            elif value is not None and value.kind == HIRKind.LOAD and value.name in json_from:
                attr = json_from[value.name]
            if attr in {"loads", "load", "dumps", "dump"}:
                self._json_value_alias[target.name] = attr
                self._json_alias_assign_ids.add(id(node))

    def lower(
        self, hir: HIRNode, modules: dict[str, HIRNode] | None = None,
        from_imports: dict | None = None,
        entry_file: str | None = None,
        module_meta: dict[str, tuple[str, bool]] | None = None,
    ) -> MIRModule:
        self._json_binding_counts = _json_binding_counts(hir)
        self._json_value_alias = {}
        self._json_alias_assign_ids = set()
        self._collect_json_aliases(hir)
        self.functions = []
        self.generators = {}
        self.classes = {}
        self.class_parents = {}
        self.class_base_list = {}
        self.class_mro = {}
        self._mro_memo = {}
        self.abstract_methods: dict[str, set[str]] = {}
        self.class_properties = {}
        self.static_methods = set()
        self.class_methods = set()
        self.decorated_classes = set()
        self.async_functions = set()
        self.async_generators = set()
        self.module_aliases = {}
        self._identity_decorator_names = set()
        self._module_func_nodes = {}
        self._module_statement_positions = {}
        self._module_binding_positions = {}
        self._current_class_position = None
        self.decorated_class_methods = {}
        self.decorated_class_method_metadata = {}
        self._decorated_method_names = set()
        self._module_function_names = set()
        self._module_function_bindings = {}
        self.decorated_symbols = {}
        self.function_aliases = {}
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
            self._identity_decorator_names = self._module_identity_decorator_names(module_body)
            self._module_statement_positions = {
                id(item): index for index, item in enumerate(module_body)
            }
            self._module_func_nodes = {
                item.name: item for item in module_body
                if getattr(item, "kind", None) == HIRKind.FUNC_DEF
            }
            self._module_binding_positions = {}
            for index, item in enumerate(module_body):
                if getattr(item, "kind", None) in {HIRKind.FUNC_DEF, HIRKind.CLASS_DEF}:
                    self._module_binding_positions[item.name] = index
                elif getattr(item, "kind", None) == HIRKind.IMPORT:
                    for alias in getattr(item, "names", []) or []:
                        bound_name = alias.asname or alias.name.split(".", 1)[0]
                        self._module_binding_positions[bound_name] = index
                elif getattr(item, "kind", None) == HIRKind.IMPORT_FROM:
                    for alias in getattr(item, "names", []) or []:
                        self._module_binding_positions[alias.asname or alias.name] = index
                elif getattr(item, "kind", None) == HIRKind.ASSIGN:
                    for target in getattr(item, "targets", []) or []:
                        if getattr(target, "kind", None) in {HIRKind.LOAD, HIRKind.STORE}:
                            self._module_binding_positions[target.name] = index
                elif getattr(item, "kind", None) == HIRKind.ANN_ASSIGN:
                    target = getattr(item, "target", None)
                    if getattr(target, "kind", None) in {HIRKind.LOAD, HIRKind.STORE}:
                        self._module_binding_positions[target.name] = index
            # ZIP_ALIAS_SHADOW_V1: `combinar` es el alias dialectal de zip.
            # Si el usuario DEFINE una funcion con ese nombre, el def gana al
            # builtin (CPython). Sin este registro, `def combinar(n, k):
            # return math.comb(n, k)` bajaba a iter_init("zip") y el print
            # moria con "native print of a iterator:zip value is not
            # supported" (corpus_t1/funciones/34 y /23).
            self._module_function_names = {
                node.name for node in module_body if node.kind == HIRKind.FUNC_DEF
            }
            # GLOBAL_FUNCTION_BINDING_V1: Python stores functions in the
            # module namespace, so a `global escape; escape = value` must be
            # able to replace the function binding. Materialize a distinct
            # slot only for module functions named by a global declaration.
            _global_function_names: set[str] = set()
            for _function in _module_level_defs(module_body):
                for _scope_node in self._walk(getattr(_function, "body", ())):
                    if _scope_node.kind == HIRKind.GLOBAL:
                        _global_function_names.update(
                            name for name in (getattr(_scope_node, "names", ()) or ())
                            if name in self._module_function_names
                        )
            _used_binding_names = {
                getattr(_node, "name", None)
                for _node in _iter_hir(hir)
                if _node.kind in {HIRKind.LOAD, HIRKind.STORE, HIRKind.FUNC_DEF, HIRKind.CLASS_DEF}
            }
            self._module_function_bindings = {}
            for _name in sorted(_global_function_names):
                _binding = f"__piton_module_binding__{_name}"
                while _binding in _used_binding_names:
                    _binding = f"_{_binding}"
                self._module_function_bindings[_name] = _binding
                _used_binding_names.add(_binding)
            self.async_functions = {
                node.name for node in module_body
                if node.kind == HIRKind.FUNC_DEF and getattr(node, "is_async", False)
            }
            for node in module_body:
                if node.kind == HIRKind.IMPORT:
                    for alias in node.names:
                        if (
                            alias.name in {"asyncio", "math", "sys", "os", "terminal", "builtins"}
                            or alias.name in _NATIVE_MODULE_CONSTS
                        ):
                            self.module_aliases[alias.asname or alias.name] = alias.name
                            if alias.name == "os" and "os.path" not in imported_modules:
                                _sub_osp = _bundled_submodule_hir("os.path")
                                if _sub_osp is not None:
                                    imported_modules["os.path"] = _sub_osp
                                    from piton.bundled_stdlib import bundled_module_path as _bmp_osp
                                    _bp_osp = _bmp_osp("os__path")
                                    if _bp_osp is not None and module_meta is not None:
                                        module_meta["os.path"] = (_bp_osp, False)
                                    self._prescan_module_signatures(
                                        list(getattr(_sub_osp, "body", []) or []),
                                        name_prefix="os__path__",
                                    )
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
                        if alias.name in imported_modules or alias.name in {"asyncio", "math", "sys", "os", "terminal", "builtins"} or alias.name in _NATIVE_MODULE_CONSTS:
                            self.module_aliases[alias.asname or alias.name] = alias.name
                elif node.kind == HIRKind.IMPORT_FROM:
                    mod_name = getattr(node, "module", None)
                    if getattr(node, "is_star", False):
                        if mod_name in {"asyncio", "math", "sys"}:
                            raise MIRLoweringError("native star imports from builtin modules are not supported")
                        if not mod_name or getattr(node, "level", 0) or 0 > 0:
                            raise MIRLoweringError("native relative star imports are not supported")
                        if mod_name not in imported_modules:
                            # Relajar star import sin modulo: no-op
                            continue
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
                            if mod_name == "os" and alias.name == "PathLike":
                                # OS_PATHLIKE_V1: PathLike es un protocolo
                                # builtin del backend aunque os.piton provea
                                # el resto del modulo empaquetado.
                                self.from_import_aliases[local] = "os.PathLike"
                            elif mod_name == "os" and alias.name == "name":
                                self.from_import_aliases[local] = "os.name"
                            elif _es_constante:
                                self.from_import_aliases[local] = _module_state_name(mod_name, alias.name)
                            elif mod_name in {"asyncio", "math", "terminal", "json", "heapq", "zlib", "statistics"} or (
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
                if module_name in self._bundled_modules_loaded:
                    # Ya cargado completamente dentro de _lower_stmt
                    # (intentar/funcion): clases, estado y funciones ya estan
                    # bajadas; evitar redefiniciones.
                    continue
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
                self._mirror_class_import_alias(_local, _cqual)
            def _find_classes(nodes):
                res = []
                for item in nodes:
                    if getattr(item, "kind", None) == HIRKind.CLASS_DEF:
                        res.append(item)
                    for attr in ("body", "orelse", "finalbody"):
                        sub = getattr(item, attr, None)
                        if isinstance(sub, list):
                            res.extend(_find_classes(sub))
                return res

            self._local_classes = {node.name for node in _find_classes(module_body)}

            # CLASS_BASE_ALIAS_V1: recognize the narrow, unambiguous
            # `class A; Alias = A; class B(Alias)` sequence. Requiring the
            # alias assignment to be adjacent to both class statements avoids
            # folding over intervening calls or rebinding, where the runtime
            # base expression could have changed. Other dynamic bases remain
            # unresolved and super() fails closed for them.
            self._class_base_aliases_by_class_id: dict[int, dict[str, str]] = {}
            for _index in range(1, len(module_body) - 1):
                _alias_stmt = module_body[_index]
                _base_class_stmt = module_body[_index - 1]
                _derived_class_stmt = module_body[_index + 1]
                if (
                    _alias_stmt.kind != HIRKind.ASSIGN
                    or _base_class_stmt.kind != HIRKind.CLASS_DEF
                    or _derived_class_stmt.kind != HIRKind.CLASS_DEF
                ):
                    continue
                _targets = [
                    target.name for target in getattr(_alias_stmt, "targets", []) or []
                    if getattr(target, "kind", None) == HIRKind.STORE
                ]
                _value = getattr(_alias_stmt, "value", None)
                if len(_targets) != 1 or getattr(_value, "kind", None) != HIRKind.LOAD:
                    continue
                _alias, _source = _targets[0], _value.name
                if _base_class_stmt.name != _source:
                    continue
                _derived_bases = {
                    getattr(getattr(base, "value", base), "name", None)
                    for base in getattr(_derived_class_stmt, "bases", []) or []
                }
                if _alias not in _derived_bases:
                    continue
                self._class_base_aliases_by_class_id[id(_derived_class_stmt)] = {
                    _alias: _source,
                }

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
                    name_prefix=self._mod_prefix(_imp_name),
                )

            for node in _find_classes(module_body):
                self._current_class_position = self._module_statement_positions.get(id(node))
                # Metaclass keywords are recorded in class_metaclasses below;
                # their runtime hooks are emitted with module class creation.
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
                def _extract_base_name(_b):
                    if getattr(_b, "kind", None) == HIRKind.SUBSCR:
                        _b = _b.value
                    if getattr(_b, "name", None):
                        return _b.name
                    if getattr(_b, "kind", None) == HIRKind.ATTR:
                        val = _b.value
                        if getattr(val, "name", None):
                            mod = self.module_aliases.get(val.name, val.name)
                            return f"{mod.replace('.', '__')}__{_b.attr}"
                    return None
                bases = [_name for base in node.bases for _name in [_extract_base_name(base)] if _name]
                # SUBMODULE_FROMIMPORT_BASE_V1: `clase Mi(Callable)` con
                # Callable salido de `desde collections.abc importar Callable`:
                # el nombre LOCAL se traduce al cualificado del from-import,
                # que es el que el levantamiento de modulos registro.
                bases = [self.from_import_aliases.get(_b, _b) for _b in bases]
                _base_aliases = self._class_base_aliases_by_class_id.get(id(node), {})
                bases = [_base_aliases.get(_b, _b) for _b in bases]
                for dec in (getattr(node, "decorators", []) or []):
                    if getattr(dec, "kind", None) == HIRKind.CALL:
                        func_name = getattr(getattr(dec, "func", None), "name", None)
                        if func_name in ("_simple_enum", "simple_enum") and getattr(dec, "args", None):
                            first_arg = getattr(dec.args[0], "name", None)
                            if first_arg:
                                bases.append(self.from_import_aliases.get(first_arg, first_arg))
                # METACLASS_CONFLICT_V1: `clase C(A, B)` cuyas bases tienen
                # metaclases incompatibles debe fallar cerrado (CPython lanza
                # TypeError "metaclass conflict"); antes la clase se creaba en
                # silencio (fail-open de la cadena relax).
                _explicit_meta = None
                for _kw in (getattr(node, "keywords", None) or []):
                    if getattr(_kw, "arg", None) in {"metaclass", "metaclase"}:
                        _raw_meta = getattr(getattr(_kw, "value", None), "name", None)
                        if _raw_meta:
                            _explicit_meta = self.from_import_aliases.get(_raw_meta, _raw_meta)
                _meta_winner = _explicit_meta or "type"
                for _base in bases:
                    _base_meta = self.class_metaclasses.get(_base, "type")
                    if _base_meta == "type" or _base_meta == _meta_winner:
                        continue
                    if self._metaclass_is_subclass(_base_meta, _meta_winner):
                        _meta_winner = _base_meta
                    elif not self._metaclass_is_subclass(_meta_winner, _base_meta):
                        raise MIRLoweringError(
                            "metaclass conflict: the metaclasses of the bases are "
                            f"incompatible ('{_meta_winner}' and '{_base_meta}')"
                        )
                self.class_metaclasses[node.name] = _meta_winner
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
                class_local_names: set[str] = set()
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
                                class_local_names.add(target.name)
                                if not hasattr(self, "class_attrs"):
                                    self.class_attrs = {}
                                self.class_attrs.setdefault(node.name, {})[target.name] = method.value
                        continue
                    if method.kind == HIRKind.ANN_ASSIGN:
                        target = getattr(method, "target", None)
                        if getattr(target, "kind", None) in (HIRKind.STORE, HIRKind.LOAD):
                            class_local_names.add(target.name)
                        continue
                    if method.kind != HIRKind.FUNC_DEF:
                        # Skip non-method statements in class body (e.g., if, for)
                        continue
                    symbol, role, prop_name = self._class_method_symbol(
                        node.name, method, property_methods,
                        class_local_names=class_local_names,
                    )
                    if self._class_method_is_abstract(method):
                        self.abstract_methods.setdefault(node.name, set()).add(method.name)
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
                    if role in (None, "staticmethod", "classmethod", "decorated"):
                        self.classes[node.name].add(method.name)
                    else:
                        self.class_properties.setdefault(node.name, {}).setdefault(prop_name, {})[role] = symbol
                    class_local_names.add(method.name)
            self._current_class_position = None
            if hasattr(self, "class_attrs"):
                for _cname, _attrs in self.class_attrs.items():
                    for _aname, _aval in _attrs.items():
                        _vname = getattr(_aval, "name", None)
                        if isinstance(_vname, str) and _vname in self.classes.get(_cname, set()):
                            self.classes[_cname].add(_aname)
                            if not hasattr(self, "function_aliases"):
                                self.function_aliases = {}
                            self.function_aliases[f"{_cname}__{_aname}"] = f"{_cname}__{_vname}"
            self._finalize_mro()
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
            self._func_def_nodes = {
                _n.name: _n for _n in module_body
                if _n.kind == HIRKind.FUNC_DEF
                and not (getattr(_n, "decorators", None) or [])
                and not getattr(_n, "is_async", False)
                and _n.name not in self.generators
            }
            self._json_specs = set()
            self._json_spec_alias_for = {}
            for node in module_body:
                if node.kind == HIRKind.FUNC_DEF:
                    is_gen = node.name in self.generators
                    decorators = list(getattr(node, "decorators", None) or [])
                    if decorators:
                        if is_gen or getattr(node, "is_async", False):
                            if all((getattr(d, "name", None) or getattr(d, "attr", None)) in {"contextmanager", "gestor_contexto"} for d in decorators):
                                self._lower_function(node, is_generator=is_gen)
                                continue
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
            module_builder.module_function_bindings = dict(self._module_function_bindings)
            for _function_name, _binding_name in sorted(self._module_function_bindings.items()):
                _function_value = module_builder.temp()
                module_builder.emit("load", _function_name, result=_function_value)
                module_builder.emit("store", _binding_name, _function_value, "internal")
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
            all_classes_with_attrs = set(getattr(self, "class_attrs", {}).keys())
            for cls_name in list(self.classes.keys()):
                for anc in self.class_mro.get(cls_name, []):
                    if anc in all_classes_with_attrs:
                        all_classes_with_attrs.add(cls_name)
                        break
            _classes_with_metaclass = {
                _cls for _cls, _meta in self.class_metaclasses.items()
                if _meta != "type"
            }
            _metaclass_objects = {
                _meta for _meta in self.class_metaclasses.values()
                if _meta in self.classes
            }
            needed_classes = (
                all_classes_with_attrs
                | set(getattr(self, "decorated_classes", set()))
                | _classes_with_metaclass
                | _metaclass_objects
            )
            for cls_node in (n for n in module_body if n.kind == HIRKind.CLASS_DEF and n.name in needed_classes):
                cls_name = cls_node.name
                merged_attrs = {}
                for anc in reversed(self.class_mro.get(cls_name, [cls_name])):
                    merged_attrs.update(getattr(self, "class_attrs", {}).get(anc, {}))
                if not hasattr(self, "_class_attr_nodes"):
                    self._class_attr_nodes = {}
                self._class_attr_nodes[cls_name] = merged_attrs
                cls_obj = module_builder.temp()
                parent_name = self.class_parents.get(cls_name)
                _meta_name = self.class_metaclasses.get(cls_name, "type")
                _meta_mro = self.class_mro.get(_meta_name, [_meta_name])
                _meta_new = next((
                    f"{_candidate}____new__"
                    for _candidate in _meta_mro
                    if "__new__" in self.classes.get(_candidate, set())
                    and f"{_candidate}____new__" in self.function_params
                ), None) if _meta_name != "type" else None
                _meta_init = next((
                    f"{_candidate}____init__"
                    for _candidate in _meta_mro
                    if "__init__" in self.classes.get(_candidate, set())
                    and f"{_candidate}____init__" in self.function_params
                ), None) if _meta_name != "type" else None
                if _meta_new or _meta_init:
                    # Build the namespace once and pass the same object to the
                    # metaclass hooks, matching class-statement argument
                    # identity and allowing __new__ to add class attributes.
                    _ns_items = []
                    for _attr_name, _value_node in merged_attrs.items():
                        _key = module_builder.temp()
                        module_builder.emit("const", _attr_name, result=_key)
                        _value = self._lower_expr(module_builder, _value_node)
                        _ns_items.append((_key, _value))
                        if not hasattr(self, "_class_attr_temps"):
                            self._class_attr_temps = {}
                        self._class_attr_temps.setdefault(cls_name, {})[_attr_name] = _value
                    _namespace = module_builder.temp()
                    module_builder.emit("build_collection", "dict", tuple(_ns_items), result=_namespace)
                    _class_name_value = module_builder.temp()
                    module_builder.emit("const", cls_name, result=_class_name_value)
                    _base_values = []
                    for _base in self.class_base_list.get(cls_name, []):
                        _base_value = module_builder.temp()
                        module_builder.emit("load", _base, result=_base_value)
                        _base_values.append(_base_value)
                    _bases = module_builder.temp()
                    module_builder.emit("build_collection", "tuple", tuple(_base_values), result=_bases)
                    if _meta_new:
                        _meta_value = module_builder.temp()
                        module_builder.emit("load", _meta_name, result=_meta_value)
                        module_builder.emit(
                            "call", _meta_new,
                            (_meta_value, _class_name_value, _bases, _namespace),
                            None, result=cls_obj,
                        )
                    else:
                        module_builder.emit("object_new", cls_name, parent_name, result=cls_obj)
                        for _index, _attr_name in enumerate(merged_attrs):
                            module_builder.emit(
                                "set_attr", cls_obj, _attr_name, _ns_items[_index][1]
                            )
                    if _meta_init:
                        _ignored = module_builder.temp()
                        module_builder.emit(
                            "call", _meta_init,
                            (cls_obj, _class_name_value, _bases, _namespace),
                            None, result=_ignored,
                        )
                else:
                    module_builder.emit("object_new", cls_name, parent_name, result=cls_obj)
                    for attr_name, value_node in merged_attrs.items():
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
                if node.kind not in {HIRKind.FUNC_DEF, HIRKind.CLASS_DEF}
                or getattr(node, "decorators", None)
                or (
                    node.kind == HIRKind.CLASS_DEF
                    and any(
                        (node.name, getattr(item, "name", None)) in self.decorated_class_methods
                        for item in (getattr(node, "body", []) or [])
                    )
                )
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
                        _bfunc, self._mod_func_symbol(_bmod_name, _bfunc.name)
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
        for _cls in getattr(self, "classes", {}):
            for _anc in reversed(self.class_mro.get(_cls, [_cls])):
                for _attr, _t in _attr_types.get(_anc, {}).items():
                    _attr_types.setdefault(_cls, {})[_attr] = _t
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
        module.class_metaclasses = dict(self.class_metaclasses)
        module.class_properties = dict(self.class_properties)
        module.static_methods = set(self.static_methods)
        module.class_methods = set(self.class_methods)
        module.function_aliases = dict(getattr(self, "function_aliases", {}))
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
        def _extract_imported_base(_b):
            if getattr(_b, "kind", None) == HIRKind.SUBSCR:
                _b = _b.value
            if getattr(_b, "name", None):
                n = _b.name
                if self._module_scope_stack:
                    _scope_names = self._module_scope_stack[-1][0]
                    _resolved = _scope_names.get(n)
                    if _resolved in self.classes:
                        return _resolved
                _cand = f"{module_name.replace('.', '__')}__{n}"
                if _cand in self.classes or n not in self.classes:
                    return _cand
                return n
            if getattr(_b, "kind", None) == HIRKind.ATTR:
                val = _b.value
                if getattr(val, "name", None):
                    mod = self.module_aliases.get(val.name, val.name)
                    return f"{mod.replace('.', '__')}__{_b.attr}"
            return None
        bases = [_name for base in node.bases for _name in [_extract_imported_base(base)] if _name]
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
            symbol, role, prop_name = self._class_method_symbol(
                qualified, method, property_methods, allow_user_identity=False,
            )
            if self._class_method_is_abstract(method):
                self.abstract_methods.setdefault(qualified, set()).add(method.name)
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
                        # Relajar star import sin modulo: no-op
                        continue
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

    def _ensure_submodule_attr(
        self, dotted_name: str, builder: "_Builder"
    ) -> None:
        """Crea el objeto de modulo para ``dotted_name`` (p. ej. ``os.path``)
        y lo establece como atributo del modulo padre, igual que hace
        _lower_module_metadata a nivel de modulo. Un paquete padre que solo
        se haya importado implicitamente por un import punteado tambien debe
        quedar ligado al nombre superior, como ocurre en CPython."""
        parts = dotted_name.split(".")
        if len(parts) < 2:
            return

        parent_name = parts[0]

        # Un import empaquetado registra HIR y simbolos para compilar el
        # modulo, pero no siempre emite un objeto runtime para el paquete
        # padre (por ejemplo `importar xml.dom`, donde `xml` no tiene un
        # archivo propio). Si no hay una asignacion anterior, crear el objeto
        # implicito de paquete antes de establecer su submodulo.
        _const_values = {}
        _module_values = {}
        _module_bindings = {}
        for _block in builder.function.blocks:
            for _instruction in _block.instructions:
                if _instruction.op == "const" and _instruction.result:
                    _const_values[_instruction.result] = _instruction.args[0]
                elif (
                    _instruction.op == "object_new"
                    and _instruction.args
                    and _instruction.args[0] == "module"
                    and _instruction.result
                ):
                    _module_values[_instruction.result] = None
                elif (
                    _instruction.op == "set_attr"
                    and len(_instruction.args) >= 3
                    and _instruction.args[1] == "__name__"
                    and _instruction.args[0] in _module_values
                ):
                    _module_values[_instruction.args[0]] = _const_values.get(
                        _instruction.args[2]
                    )
                elif (
                    _instruction.op == "store"
                    and len(_instruction.args) >= 2
                ):
                    _module_bindings[_instruction.args[0]] = _instruction.args[1]
        parent_obj = _module_bindings.get(parent_name)
        parent_is_module = _module_values.get(parent_obj) == parent_name
        if not parent_is_module:
            parent_temp = builder.temp()
            builder.emit("object_new", "module", None, result=parent_temp)
            _parent_n = builder.temp()
            builder.emit("const", parent_name, result=_parent_n)
            builder.emit("set_attr", parent_temp, "__name__", _parent_n)
            _parent_p = builder.temp()
            builder.emit("const", "", result=_parent_p)
            builder.emit("set_attr", parent_temp, "__package__", _parent_p)
            builder.emit("store", parent_name, parent_temp)

        # Reuse the object already registered in sys.modules when the module
        # was present in the initial metadata scan. Creating a fresh
        # placeholder in that case discarded nested attributes (pkg.sub.deep).
        parent_temp = builder.temp()
        builder.emit("load", parts[0], result=parent_temp)
        for part in parts[1:-1]:
            next_parent = builder.temp()
            builder.emit("get_attr", parent_temp, part, result=next_parent)
            parent_temp = next_parent

        if dotted_name in getattr(self, "_module_registry_names", set()):
            sys_temp = builder.temp()
            builder.emit("load", "sys", result=sys_temp)
            modules_temp = builder.temp()
            builder.emit("get_attr", sys_temp, "modules", result=modules_temp)
            name_temp = builder.temp()
            builder.emit("const", dotted_name, result=name_temp)
            child_temp = builder.temp()
            builder.emit("get_item", modules_temp, name_temp, result=child_temp)
        else:
            # Late-loaded built-in submodules such as os.path were not in the
            # metadata prologue. Give them a small module object here.
            child_temp = builder.temp()
            builder.emit("object_new", "module", None, result=child_temp)
            name_temp = builder.temp()
            builder.emit("const", dotted_name, result=name_temp)
            builder.emit("set_attr", child_temp, "__name__", name_temp)
            package_temp = builder.temp()
            builder.emit("const", ".".join(parts[:-1]), result=package_temp)
            builder.emit("set_attr", child_temp, "__package__", package_temp)
        builder.emit("set_attr", parent_temp, parts[-1], child_temp)

    def _load_bundled_module_fully(
        self, module_name: str, builder: "_Builder"
    ) -> bool:
        """Carga completamente un modulo empaquetado dentro de _lower_stmt.

        Replica lo que el nivel modulo hace para cada modulo importado
        (HIR, imports transitivos, estado de modulo, clases y funciones de
        nivel de modulo) para que un modulo cargado perezosamente dentro de
        un `intentar` o una función tenga todos sus simbolos disponibles,
        igual que si se hubiera importado a nivel de modulo.

        Evita duplicados si el modulo ya estaba cargado (nivel modulo o
        previo dentro de _lower_stmt). Devuelve True si el modulo se cargo
        (o ya estaba cargado), False si no existe como modulo empaquetado.
        """
        if module_name in self.imported_modules:
            return True

        # Para nombres punteados (p. ej. "urllib.request"), asegurar que los
        # modulos padre existan en imported_modules aunque no tengan HIR
        # propio (p. ej. no existe urllib.piton). Esto evita que
        # _build_module_scope falle al encontrar `importar urllib` dentro
        # del cuerpo del submodulo.
        if "." in module_name:
            _parts = module_name.split(".")
            for _i in range(1, len(_parts)):
                _parent = ".".join(_parts[:_i])
                if _parent not in self.imported_modules:
                    self.imported_modules[_parent] = Module(
                        body=[], kind=HIRKind.MODULE
                    )

        hir = _bundled_submodule_hir(module_name) or _bundled_module_hir(module_name)
        if hir is None:
            return False

        self.imported_modules[module_name] = hir
        self._prescan_module_signatures(
            list(getattr(hir, "body", []) or []),
            name_prefix=f"{module_name.replace('.', '__')}__",
        )

        # Imports transitivos del modulo empaquetado (p. ej. `importar time`
        # dentro de datetime.piton). Se cargan recursivamente.
        _cargar = []
        for _it in getattr(hir, "body", []) or []:
            if _it.kind == HIRKind.IMPORT:
                for _al in _it.names:
                    if (
                        _al.name not in {"asyncio", "math", "sys", "os", "terminal"}
                        and _al.name not in _NATIVE_MODULE_CONSTS
                    ):
                        _cargar.append(_al.name)
            elif _it.kind == HIRKind.IMPORT_FROM:
                _m2 = getattr(_it, "module", None)
                if _m2 and _m2 not in {"asyncio", "math", "sys", "os", "terminal"}:
                    _cargar.append(_m2)
        for _target in _cargar:
            if _target not in self.imported_modules:
                _target_builtin = (
                    _target in {"asyncio", "math", "sys", "os", "terminal", "time", "builtins"}
                    or _target in _NATIVE_MODULE_CONSTS
                )
                if (
                    _target_builtin
                    and getattr(builder, "function", None)
                    and builder.function.name == "<module>"
                ):
                    # Modulo nativo/builtin importado de forma transitoria por
                    # un modulo empaquetado (p. ej. `importar time` en
                    # datetime.piton): crear el objeto modulo y almacenarlo
                    # como global, igual que hace el is_builtin de nivel
                    # modulo.
                    _tmod = builder.temp()
                    builder.emit("object_new", "module", None, result=_tmod)
                    _tn = builder.temp()
                    builder.emit("const", _target, result=_tn)
                    builder.emit("set_attr", _tmod, "__name__", _tn)
                    _tp = builder.temp()
                    builder.emit("const", "", result=_tp)
                    builder.emit("set_attr", _tmod, "__package__", _tp)
                    builder.emit("store", _target, _tmod)
                    builder.module_aliases[_target] = _target
                    self.imported_modules[_target] = None  # marcador nativo
                else:
                    self._load_bundled_module_fully(_target, builder)

        # Clases, estado y funciones del modulo empaquetado, con el
        # ambito del modulo empujado (igual que a nivel de modulo en
        # el bucle de imported_modules) para que referencias como
        # MINYEAR dentro de date__init__ se resuelvan cualificados.
        _scope_names, _scope_modules, _scope_vars = self._build_module_scope(
            module_name, hir
        )
        self._module_scope_stack.append((_scope_names, _scope_modules, _scope_vars))
        try:
            # Clases del modulo empaquetado.
            for _it in getattr(hir, "body", []) or []:
                if _it.kind == HIRKind.CLASS_DEF:
                    self._lower_imported_class(module_name, _it)

            # Estado de modulo: inicializar variables/constantes de
            # nivel de modulo como globals cualificados.
            _saved_aliases = builder.module_aliases
            _saved_from = builder.from_import_aliases
            builder.module_aliases = dict(_scope_modules)
            builder.from_import_aliases = dict(_scope_names)
            try:
                for _it in getattr(hir, "body", []) or []:
                    if _it.kind == HIRKind.ASSIGN:
                        for _target in getattr(_it, "targets", ()) or ():
                            _tname = getattr(_target, "name", None)
                            if not isinstance(_tname, str):
                                continue
                            _value = self._lower_expr(builder, _it.value)
                            _qualified = _module_state_name(module_name, _tname)
                            builder.emit("store", _qualified, _value)
            finally:
                builder.module_aliases = _saved_aliases
                builder.from_import_aliases = _saved_from

            # Funciones de nivel de modulo del paquete empaquetado.
            for _it in getattr(hir, "body", []) or []:
                if _it.kind == HIRKind.FUNC_DEF:
                    self._lower_function(
                        _it, self._mod_func_symbol(module_name, _it.name)
                    )
        finally:
            self._module_scope_stack.pop()

        self._bundled_modules_loaded.add(module_name)
        return True

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
        self._module_registry_names = set(module_meta or {})

        def emit_const(value: Any) -> str:
            result = builder.temp()
            builder.emit("const", value, result=result)
            return result

        def emit_store(name: str, value: str, *, internal: bool = False) -> None:
            if internal:
                # Runtime support modules are available to generated code but
                # are not bindings in the user's module unless imported.
                builder.emit("store", name, value, "internal")
            else:
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
        emit_store("__doc__", emit_const(None))
        emit_store("__package__", emit_const(None))
        # __loader__: CPython reporta el cargador del modulo (__main__). Piton no
        # tiene importlib, pero str(format) necesita el texto <class ...>.
        # Almacenamos la representacion de clase como cadena para que
        # str.format(**vars()) coincida byte-a-byte con CPython.
        emit_store("__loader__", emit_const("<class '_frozen_importlib.BuiltinImporter'>"))
        emit_store("__spec__", emit_const(None))
        if entry_file:
            emit_store("__file__", emit_const(entry_file))
        # __annotations__ = {} (empty dict, como CPython en un modulo sin anotaciones)
        _anns = builder.temp()
        builder.emit("build_collection", "dict", (), result=_anns)
        emit_store("__annotations__", _anns)
        sys_obj = module_object("sys", None, "")
        builtins_obj = module_object("builtins", None, "")
        emit_store("sys", sys_obj, internal=True)
        emit_store("builtins", builtins_obj, internal=True)
        entries: list[tuple[str, str]] = [
            ("sys", sys_obj),
            ("builtins", builtins_obj),
            ("__main__", module_object("__main__", entry_file, None)),
        ]
        module_objects: dict[str, str] = {"sys": sys_obj, "builtins": builtins_obj}
        for name in sorted(module_meta or {}):
            file_path, is_package = module_meta[name]
            package = name.rsplit(".", 1)[0] if "." in name else (name if is_package else "")
            module_obj = module_object(name, file_path, package)
            entries.append((name, module_obj))
            module_objects[name] = module_obj
            # Keep imported module names available to native emitters. The
            # catalog above models sys.modules; this binding models `import a`.
            binding = name.split(".", 1)[0]
            if binding.isidentifier() and binding not in {item[0] for item in entries[:-1]}:
                emit_store(binding, module_obj)
        modules_dict = builder.temp()
        builder.emit("build_collection", "dict", tuple(entries), result=modules_dict)
        builder.emit("set_attr", sys_obj, "modules", modules_dict)
        builder.emit("store", "__builtins__", builtins_obj)
        # IMPORT_BINDS_SUBMODULE_V1: CPython's import machinery sets each
        # imported submodule as an attribute of its parent package
        # (`importar pkg.sub` makes `pkg.sub` reachable). Without the runtime
        # edge, any `pkg.sub.fn()` chain that falls back to a runtime
        # get_attr dies with AttributeError.
        for name, module_obj in module_objects.items():
            parent_name, _, leaf = name.rpartition(".")
            parent_obj = module_objects.get(parent_name)
            if parent_obj is not None:
                builder.emit("set_attr", parent_obj, leaf, module_obj)

    def _lower_function(
        self, node: HIRNode, qualified_name: str | None = None,
        captures: tuple[str, ...] = (), cell_vars: list[str] | None = None,
        super_context: tuple[str, str] | None = None,
        frame_abi: bool = False, is_generator: bool = False,
    ) -> None:
        args = getattr(node, "args", None)
        # LAMBDA_STAR_CLOSURE_V1: lambda varargs/kwargs conservan sus slots de
        # firma para que el closure dispatcher recoja los argumentos extra.

        posonly_params = list(getattr(args, "posonlyargs", []) or []) if args else []
        params = list(getattr(args, "args", []) or []) if args else []
        kwonly_params = list(getattr(args, "kwonlyargs", []) or []) if args else []
        positional_params = [*posonly_params, *params]
        vararg_name = getattr(args, "vararg", None) if args else None
        kwarg_name = getattr(args, "kwarg", None) if args else None
        body = list(getattr(node, "body", []))
        builder = _Builder(qualified_name or node.name, [*captures, *positional_params])
        # JSON_SPECIALIZE_V1: en una especializacion, el parametro que recibia json.dumps/dump es un alias.
        builder.json_param_alias = dict(getattr(self, "_json_spec_alias_for", {}).get(qualified_name or node.name, {}))
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
        if not self._module_scope_stack:
            builder.module_function_bindings = dict(self._module_function_bindings)
        if cell_vars:
            builder.cell_params = set(cell_vars)
            if qualified_name and qualified_name != node.name:
                nested_args = getattr(node, "args", None)
                nested_n_args = len(
                    list(getattr(nested_args, "posonlyargs", []) or []) + list(getattr(nested_args, "args", []) or [])
                )
                builder.closures[node.name] = (qualified_name, tuple(sorted(cell_vars)), nested_n_args)
        defaults_map: dict[str, Any] = {}
        runtime_operator_dunder = (
            node.name in {"__floordiv__", "__rfloordiv__", "__pow__", "__rpow__"}
            and qualified_name is not None
            and qualified_name.endswith(f"__{node.name}")
        )
        if args:
            raw_defaults = list(getattr(args, "defaults", []) or [])
            default_params = positional_params[-len(raw_defaults):] if raw_defaults else []
            for param, default_node in zip(default_params, raw_defaults):
                # M6 STATIC_CONST_EVAL_V1: CONST y literales compuestos de
                # consts ((), [], (a, b)) se evaluan a valor estatico; Name /
                # Attribute / exprs siguen fail-closed (el binding de modulo
                # no es const en definicion).
                if not self._static_default_value(default_node, defaults_map, param):
                    if runtime_operator_dunder:
                        raise MIRLoweringError(
                            f"native {node.name} default for '{param}' must be a static value"
                        )
                    # Permitir defaults no constantes para progresar; se materializa como None
                    defaults_map.setdefault(param, None)
            for param, default_node in zip(kwonly_params, getattr(args, "kw_defaults", []) or []):
                if default_node is None:
                    continue
                if not self._static_default_value(default_node, defaults_map, param):
                    if runtime_operator_dunder:
                        raise MIRLoweringError(
                            f"native {node.name} default for '{param}' must be a static value"
                        )
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
        builder.function.posonly_params = list(posonly_params)
        builder.function.kwonly_params = list(kwonly_params)
        builder.function.defaults = [defaults_map.get(p) for p in builder.function.params]
        builder.function.default_params = [p for p in builder.function.params if p in defaults_map]
        self.function_params[qualified_name or node.name] = list(builder.function.params)
        self.function_defaults[qualified_name or node.name] = defaults_map
        self.function_varargs[qualified_name or node.name] = vararg_name
        self.function_kwargs[qualified_name or node.name] = kwarg_name
        self.function_posonly[qualified_name or node.name] = posonly_params
        self.function_kwonly[qualified_name or node.name] = kwonly_params
        self.function_frame_abi[qualified_name or node.name] = use_frame_abi
        global_names = {
            name
            for scope_node in self._walk(body)
            if scope_node.kind == HIRKind.GLOBAL
            for name in (getattr(scope_node, "names", ()) or ())
        }
        nonlocal_names = {
            name
            for scope_node in self._walk(body)
            if scope_node.kind == HIRKind.NONLOCAL
            for name in (getattr(scope_node, "names", ()) or ())
        }
        local_names = (
            set(builder.function.params)
            | self._assigned_names(body)
            | self._for_target_names(body)
            | {item.name for item in body if getattr(item, "kind", None) in {HIRKind.FUNC_DEF, HIRKind.CLASS_DEF}}
        ) - global_names - nonlocal_names
        builder.local_names = local_names
        builder.global_names = global_names
        builder.nonlocal_names = nonlocal_names
        self._validate_local_shadow_initialization(
            body, local_names, set(builder.function.params)
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
            nested_args = getattr(nested, "args", None)
            nested_positional = [
                *list(getattr(nested_args, "posonlyargs", []) or []),
                *list(getattr(nested_args, "args", []) or []),
            ] if nested_args else []
            nested_super_context = (
                (builder.super_class, nested_positional[0])
                if (
                    builder.super_class
                    and nested_positional
                    and self._contains_zero_arg_super(nested.body)
                ) else None
            )
            self._lower_function(
                nested, lifted_name, nested_captures,
                cell_vars=inner_cell_vars, super_context=nested_super_context,
                frame_abi=True,
            )

        lambda_counter = 0
        for lambda_node in (item for item in self._walk(body) if item.kind == HIRKind.LAMBDA):
            lambda_captures = tuple(name for name in sorted(self._nested_free_loads(lambda_node)) if name in available)
            lambda_name = f"<lambda_{lambda_counter}>"
            lifted_name = f"{builder.function.name}__lambda_{lambda_counter}"
            lambda_counter += 1
            inner_cell_vars = [c for c in lambda_captures if c in captured_var_names]
            lambda_args = getattr(lambda_node, "args", None)
            lambda_positional = [
                *list(getattr(lambda_args, "posonlyargs", []) or []),
                *list(getattr(lambda_args, "args", []) or []),
            ] if lambda_args else []
            lambda_super_context = (
                (builder.super_class, lambda_positional[0])
                if (
                    builder.super_class
                    and lambda_positional
                    and self._contains_zero_arg_super(lambda_node.body)
                ) else None
            )
            self._lower_function(
                lambda_node, lifted_name, lambda_captures,
                cell_vars=inner_cell_vars, super_context=lambda_super_context,
                frame_abi=True,
            )

        self._lower_statements(
            builder,
            [
                item for item in body
                if item.kind != HIRKind.FUNC_DEF
                or getattr(item, "decorators", None)
                or item.name in captured_var_names
            ],
        )
        if not builder.current.instructions:
            _referenced_labels: set[str] = set()
            for _b in builder.function.blocks:
                for _i in _b.instructions:
                    for _a in _i.args:
                        if isinstance(_a, str):
                            _referenced_labels.add(_a)
            if builder.current.label != "entry" and builder.current.label not in _referenced_labels:
                builder.function.blocks.remove(builder.current)
            else:
                builder.emit("return", None)
        elif builder.current.instructions[-1].op not in {"return", "jump", "branch"}:
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
        ok, folded = self._try_fold_const_expr(node)
        if ok:
            defaults_map[param] = folded
            return True
        return False

    def _fold_const_expr(self, node: HIRNode) -> Any | None:
        ok, folded = self._try_fold_const_expr(node)
        return folded if ok else None

    def _try_fold_const_expr(self, node: HIRNode) -> tuple[bool, Any]:
        """CONST_EXPR_DEFAULT_V1: evalua una expresion de literales a un
        valor estatico (para los defaults de funcion). Devuelve (False, None)
        si la expresion no es determinista o no evaluable en build time.

        Cubre: constantes, `a ** b`, `a * b`, `a + b`, `a - b`, `a // b`,
        `a << b`, `a | b`, `a & b`, comparaciones (`==`, `!=`, `<`, `<=`, `>`, `>=`,
        `is`, `is not`, `in`, `not in` y alias en español) y unarios (`-`, `+`, `~`, `not`).
        """
        _FAIL = object()

        def _eval(n: Any) -> Any:
            if n is None:
                return _FAIL
            k = getattr(n, "kind", None)
            if k == HIRKind.CONST:
                val = getattr(n, "value", None)
                if isinstance(val, (bool, int, float, str)) or val is None:
                    return val
                return _FAIL
            if k == HIRKind.COMPARE:
                left_val = _eval(getattr(n, "left", None))
                if left_val is _FAIL:
                    return _FAIL
                comparators = list(getattr(n, "comparators", None) or [])
                ops = list(getattr(n, "ops", None) or [])
                if not ops or len(ops) != len(comparators):
                    return _FAIL
                curr_left = left_val
                for op, comp_node in zip(ops, comparators):
                    right_val = _eval(comp_node)
                    if right_val is _FAIL:
                        return _FAIL
                    try:
                        if op in ("==", "es", "is"):
                            cond = (curr_left == right_val)
                        elif op in ("!=", "no es", "is not"):
                            cond = (curr_left != right_val)
                        elif op == "<":
                            cond = (curr_left < right_val)
                        elif op == "<=":
                            cond = (curr_left <= right_val)
                        elif op == ">":
                            cond = (curr_left > right_val)
                        elif op == ">=":
                            cond = (curr_left >= right_val)
                        elif op in ("in", "en"):
                            cond = (curr_left in right_val)
                        elif op in ("not in", "no en"):
                            cond = (curr_left not in right_val)
                        else:
                            return _FAIL
                    except Exception:
                        return _FAIL
                    if not cond:
                        return False
                    curr_left = right_val
                return True
            if k == HIRKind.BINOP:
                left_val = _eval(getattr(n, "left", None))
                right_val = _eval(getattr(n, "right", None))
                if left_val is _FAIL or right_val is _FAIL:
                    return _FAIL
                op = getattr(n, "op", "")
                try:
                    if op == "**":
                        return left_val ** right_val
                    if op == "*":
                        return left_val * right_val
                    if op == "+":
                        return left_val + right_val
                    if op == "-":
                        return left_val - right_val
                    if op == "//":
                        return left_val // right_val
                    if op == "/":
                        return left_val / right_val
                    if op == "%":
                        return left_val % right_val
                    if op == "<<":
                        return left_val << right_val
                    if op == ">>":
                        return left_val >> right_val
                    if op == "|":
                        return left_val | right_val
                    if op == "&":
                        return left_val & right_val
                    if op == "^":
                        return left_val ^ right_val
                except Exception:
                    return _FAIL
                return _FAIL
            if k == HIRKind.UNOP:
                operand = _eval(getattr(n, "operand", None))
                if operand is _FAIL:
                    return _FAIL
                op = getattr(n, "op", "")
                try:
                    if op == "-":
                        return -operand
                    if op == "+":
                        return operand
                    if op == "~":
                        return ~operand
                    if op in ("not", "no"):
                        return not operand
                except Exception:
                    return _FAIL
                return _FAIL
            if k == HIRKind.BOOL_OP:
                op = getattr(n, "op", "")
                values = list(getattr(n, "values", None) or [])
                if not values:
                    return _FAIL
                res = _eval(values[0])
                if res is _FAIL:
                    return _FAIL
                for v_node in values[1:]:
                    v = _eval(v_node)
                    if v is _FAIL:
                        return _FAIL
                    if op in ("and", "y"):
                        res = res and v
                    elif op in ("or", "o"):
                        res = res or v
                    else:
                        return _FAIL
                return res
            return _FAIL

        res = _eval(node)
        if res is _FAIL:
            return False, None
        return True, res

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

    def _contains_zero_arg_super(self, value: Any) -> bool:
        """Whether this scope contains a call to the supported ``super()`` form."""
        return any(
            item.kind == HIRKind.CALL
            and getattr(getattr(item, "func", None), "kind", None) == HIRKind.LOAD
            and getattr(item.func, "name", None) == "super"
            and not getattr(item, "args", None)
            and not getattr(item, "keywords", None)
            for item in self._walk(value)
        )

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
        global_here = {
            name
            for statement in self._walk(getattr(node, "body", ()) or ())
            if statement.kind == HIRKind.GLOBAL
            for name in (getattr(statement, "names", ()) or ())
        }
        locals_here = params_here | self._assigned_names(node.body)
        free = self._loaded_names(node.body) - locals_here - global_here
        free |= {
            name
            for statement in node.body
            if getattr(statement, "kind", None) == HIRKind.NONLOCAL
            for name in getattr(statement, "names", [])
        }
        for nested in (item for item in node.body if item.kind == HIRKind.FUNC_DEF):
            free |= {name for name in self._nested_free_loads(nested) if name not in locals_here}
        return free

    def _validate_local_shadow_initialization(
        self, body: Sequence[HIRNode], local_names: set[str], initialized: set[str]
    ) -> None:
        """Fail closed on a local shadow read before its first binding.

        The native C backend does not track Python's unbound-local sentinel.
        Without this check, `escape` could read as zero before a later local
        assignment instead of raising UnboundLocalError. Track definite
        bindings across branches so an assignment in every continuing branch
        is recognized, while a branch that returns or raises need not bind.
        """
        candidates = (local_names & self._module_function_names) - initialized
        if not candidates:
            return

        def children(node: HIRNode):
            for descriptor in fields(node):
                if descriptor.name in {"kind", "line", "col", "annotations"}:
                    continue
                value = getattr(node, descriptor.name, None)
                if isinstance(value, HIRNode):
                    yield value
                elif isinstance(value, (list, tuple)):
                    yield from (item for item in value if isinstance(item, HIRNode))

        def check_expr(node: Optional[HIRNode], bound: set[str]) -> None:
            if node is None:
                return
            stack = [node]
            while stack:
                current = stack.pop()
                kind = getattr(current, "kind", None)
                if kind == HIRKind.LOAD:
                    name = getattr(current, "name", None)
                    if name in candidates and name not in bound:
                        raise MIRLoweringError(
                            f"native local '{name}' shadowing a module function may be read before assignment"
                        )
                    continue
                # A nested scope reads captured cells when called, not when
                # its definition is evaluated. Its own lowering checks its
                # local loads independently.
                if kind in self._SCOPE_STOP:
                    continue
                stack.extend(reversed(tuple(children(current))))

        def target_names(target: Optional[HIRNode]) -> set[str]:
            if target is None:
                return set()
            kind = getattr(target, "kind", None)
            if kind == HIRKind.STORE:
                name = getattr(target, "name", None)
                return {name} if name else set()
            if kind in {HIRKind.TUPLE, HIRKind.LIST, HIRKind.STARRED}:
                names: set[str] = set()
                for child in children(target):
                    names.update(target_names(child))
                return names
            return set()

        def check_target_address(target: Optional[HIRNode], bound: set[str]) -> None:
            if target is None:
                return
            kind = getattr(target, "kind", None)
            if kind in {HIRKind.TUPLE, HIRKind.LIST, HIRKind.STARRED}:
                for child in children(target):
                    check_target_address(child, bound)
            elif kind == HIRKind.ATTR:
                check_expr(getattr(target, "value", None), bound)
            elif kind == HIRKind.SUBSCR:
                check_expr(getattr(target, "value", None), bound)
                check_expr(getattr(target, "slice", None), bound)

        def merge_fallthrough(paths: list[tuple[set[str], bool]], fallback: set[str]):
            normal = [state for state, falls in paths if falls]
            return (set.intersection(*normal) if normal else fallback), bool(normal)

        def sequence(statements, incoming: set[str]) -> tuple[set[str], bool]:
            bound = set(incoming)
            falls = True
            for statement in statements or ():
                if not falls:
                    break
                bound, falls = statement_flow(statement, bound)
            return bound, falls

        def statement_flow(statement: HIRNode, incoming: set[str]) -> tuple[set[str], bool]:
            kind = getattr(statement, "kind", None)
            bound = set(incoming)
            if kind == HIRKind.ASSIGN:
                check_expr(getattr(statement, "value", None), bound)
                targets = getattr(statement, "targets", ()) or ()
                for target in targets:
                    check_target_address(target, bound)
                for target in targets:
                    bound.update(target_names(target))
                return bound, True
            if kind == HIRKind.ANN_ASSIGN:
                target = getattr(statement, "target", None)
                check_expr(getattr(statement, "value", None), bound)
                check_target_address(target, bound)
                if getattr(statement, "value", None) is not None:
                    bound.update(target_names(target))
                return bound, True
            if kind == HIRKind.AUG_ASSIGN:
                target = getattr(statement, "target", None)
                # Augmented assignment reads a name target before writing it.
                if getattr(target, "kind", None) == HIRKind.STORE:
                    name = getattr(target, "name", None)
                    if name in candidates and name not in bound:
                        raise MIRLoweringError(
                            f"native local '{name}' shadowing a module function may be read before assignment"
                        )
                else:
                    check_target_address(target, bound)
                check_expr(getattr(statement, "value", None), bound)
                bound.update(target_names(target))
                return bound, True
            if kind == HIRKind.IF:
                check_expr(getattr(statement, "test", None), bound)
                body_state = sequence(getattr(statement, "body", ()), bound)
                else_body = getattr(statement, "orelse", ()) or ()
                else_state = sequence(else_body, bound) if else_body else (set(bound), True)
                return merge_fallthrough([body_state, else_state], bound)
            if kind in {HIRKind.RETURN, HIRKind.RAISE}:
                for child in children(statement):
                    check_expr(child, bound)
                return bound, False
            if kind in {HIRKind.WHILE, HIRKind.FOR}:
                if kind == HIRKind.WHILE:
                    check_expr(getattr(statement, "test", None), bound)
                    body_in = set(bound)
                else:
                    check_expr(getattr(statement, "iter", None), bound)
                    target = getattr(statement, "target", None)
                    check_target_address(target, bound)
                    body_in = set(bound) | target_names(target)
                sequence(getattr(statement, "body", ()), body_in)
                # A loop can execute zero times; only its else suite can add
                # bindings guaranteed on every normal exit.
                else_state = sequence(getattr(statement, "orelse", ()), bound)
                return set(bound) & else_state[0], True
            if kind == HIRKind.WITH:
                for item in getattr(statement, "items", ()) or ():
                    check_expr(getattr(item, "context_expr", None), bound)
                    target = getattr(item, "optional_vars", None)
                    check_target_address(target, bound)
                    bound.update(target_names(target))
                return sequence(getattr(statement, "body", ()), bound)
            if kind == HIRKind.TRY:
                body_state = sequence(getattr(statement, "body", ()), bound)
                paths = [body_state]
                for handler in getattr(statement, "handlers", ()) or ():
                    check_expr(getattr(handler, "type_", None), bound)
                    handler_in = set(bound)
                    handler_name = getattr(handler, "name", None)
                    if handler_name:
                        handler_in.add(handler_name)
                    paths.append(sequence(getattr(handler, "body", ()), handler_in))
                before_final, falls = merge_fallthrough(paths, bound)
                if not falls:
                    return before_final, False
                else_state = sequence(getattr(statement, "orelse", ()), before_final)
                if not else_state[1]:
                    before_final, falls = else_state
                else:
                    before_final = else_state[0]
                final_state = sequence(getattr(statement, "finalbody", ()), before_final)
                return final_state
            if kind in {HIRKind.IMPORT, HIRKind.IMPORT_FROM}:
                for alias in getattr(statement, "names", ()) or ():
                    name = getattr(alias, "asname", None) or getattr(alias, "name", "")
                    if statement.kind == HIRKind.IMPORT and not getattr(alias, "asname", None):
                        name = name.split(".")[0]
                    if name:
                        bound.add(name)
                return bound, True
            if kind in {HIRKind.BREAK, HIRKind.CONTINUE}:
                return bound, False
            # For other statement forms, consume the ordered scope events.
            # The common assignment and control-flow forms above are handled
            # explicitly because HIR lists assignment targets before values.
            for event, name in self._scope_events_of(statement):
                if event == "use" and name in candidates and name not in bound:
                    raise MIRLoweringError(
                        f"native local '{name}' shadowing a module function may be read before assignment"
                    )
                if event == "bind":
                    bound.add(name)
            return bound, True

        sequence(body, initialized)

    def _loaded_names(self, body: Sequence[HIRNode]) -> set[str]:
        return {
            node.name for node in self._walk(body)
            if node.kind == HIRKind.LOAD and getattr(node, "name", None)
        }

    def _contains_kind(self, body: Sequence[HIRNode], kinds: set[HIRKind]) -> bool:
        return any(node.kind in kinds for node in self._walk(body))

    def _lower_finalizer(self, builder: _Builder, finalizer: Any) -> None:
        """Lower a source ``finally`` body or a registered cleanup callback."""
        if callable(finalizer):
            finalizer(builder)
        else:
            self._lower_statements(builder, finalizer)

    def _lower_statements(self, builder: _Builder, statements: Sequence[HIRNode]) -> None:
        for statement in statements:
            self._lower_statement(builder, statement)

    def _lower_statement(self, builder: _Builder, node: HIRNode) -> None:
        kind = node.kind
        if kind == HIRKind.FUNC_DEF:
            decorators = list(getattr(node, "decorators", None) or [])
            # A nested function captured by another nested function must be
            # materialized at its definition site.  The captured local is a
            # cell; previously the cell stayed empty because undecorated defs
            # were always skipped here, so calls through the sibling closure
            # saw a non-callable value.
            if not decorators and node.name not in builder.cell_params:
                return
            if node.name in builder.closures:
                lifted_name, capture_names, n_args = builder.closures[node.name]
                capture_ops = []
                for capture_name in capture_names:
                    capture = builder.temp()
                    builder.emit("load", capture_name, result=capture)
                    capture_ops.append(capture)
                current = builder.temp()
                has_vararg = 1 if self.function_varargs.get(lifted_name) else 0
                builder.emit("closure_new", lifted_name, n_args, tuple(capture_ops), has_vararg,
                            1 if self.function_kwargs.get(lifted_name) else 0, result=current)
                for decorator in reversed(decorators):
                    dec_val = self._lower_expr(builder, decorator)
                    applied = builder.temp()
                    builder.emit("call", dec_val, (current,), _active_handler(builder), result=applied)
                    current = applied
                if node.name in builder.cell_params:
                    builder.emit("cell_store", node.name, current)
                else:
                    builder.emit("store", node.name, current)
                if decorators:
                    del builder.closures[node.name]
                return
            if not decorators:
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
            cls_name = node.name
            parent_name = self.class_parents.get(cls_name)
            cls_obj = builder.temp()
            builder.emit("object_new", cls_name, parent_name, result=cls_obj)
            for item in getattr(node, "body", []) or []:
                if item.kind == HIRKind.ASSIGN:
                    for target in getattr(item, "targets", ()) or ():
                        if getattr(target, "kind", None) in (HIRKind.STORE, HIRKind.LOAD):
                            attr_val = self._lower_expr(builder, item.value)
                            builder.emit("set_attr", cls_obj, target.name, attr_val)
                elif item.kind == HIRKind.FUNC_DEF:
                    decorated_value = self.decorated_class_methods.get((cls_name, item.name))
                    if decorated_value is None:
                        continue
                    # CPython evaluates decorator expressions top-to-bottom,
                    # then applies the resulting callables bottom-to-top.
                    decorator_values = [
                        self._lower_expr(builder, decorator)
                        for decorator in (getattr(item, "decorators", None) or [])
                    ]
                    current = builder.temp()
                    original_symbol = f"__piton_original__{cls_name}__{item.name}"
                    builder.emit("load", original_symbol, _active_handler(builder), result=current)
                    for decorator_value in reversed(decorator_values):
                        applied = builder.temp()
                        builder.emit(
                            "call", decorator_value, (current,),
                            _active_handler(builder), result=applied,
                        )
                        current = applied
                    # The class namespace owns the decorated object in Python;
                    # this set_attr also lets class decorators observe it.
                    builder.emit("set_attr", cls_obj, item.name, current)
                    builder.emit("store", decorated_value, current)
            decorators = list(getattr(node, "decorators", None) or [])
            current = cls_obj
            for decorator in reversed(decorators):
                dec_val = self._lower_expr(builder, decorator)
                applied = builder.temp()
                builder.emit("call", dec_val, (current,), _active_handler(builder), result=applied)
                current = applied
            builder.emit("store", cls_name, current)
        elif kind == HIRKind.ASSIGN:
            if id(node) in getattr(self, "_json_alias_assign_ids", ()):
                # JSON_VALUE_ALIAS_V2: el alias no se evalua; solo marca "ligado" en runtime.
                builder.emit("json_alias_set", node.targets[0].name)
                return
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
            ret_val = self._lower_expr(builder, node.value) if node.value else None
            if getattr(builder, "_finally_stack", None):
                saved_stack = list(builder._finally_stack)
                for idx in range(len(saved_stack) - 1, -1, -1):
                    fb, _ = saved_stack[idx]
                    builder._finally_stack = saved_stack[:idx]
                    self._lower_finalizer(builder, fb)
                builder._finally_stack = saved_stack
            if builder.in_except_handler:
                builder.emit("reraise_clear")
            builder.emit("return", ret_val)
            dead_block = builder.new_block()
            builder.current = dead_block
            return
        elif kind == HIRKind.BREAK:
            if not builder._loop_stack:
                raise MIRLoweringError("'romper' outside loop")
            break_label, _, flag_name = builder._loop_stack[-1]
            current_loop_depth = len(builder._loop_stack)
            if getattr(builder, "_finally_stack", None):
                saved_stack = list(builder._finally_stack)
                for idx in range(len(saved_stack) - 1, -1, -1):
                    fb, depth = saved_stack[idx]
                    if depth >= current_loop_depth:
                        builder._finally_stack = saved_stack[:idx]
                        self._lower_finalizer(builder, fb)
                builder._finally_stack = saved_stack
            if builder.in_except_handler:
                builder.emit("reraise_clear")
            if flag_name:
                one = builder.temp()
                builder.emit("const", 1, result=one)
                builder.emit("store", flag_name, one)
            builder.emit("jump", break_label)
            dead_block = builder.new_block()
            builder.current = dead_block
            return
        elif kind == HIRKind.CONTINUE:
            if not builder._loop_stack:
                raise MIRLoweringError("'continuar' outside loop")
            _, continue_label, _ = builder._loop_stack[-1]
            current_loop_depth = len(builder._loop_stack)
            if getattr(builder, "_finally_stack", None):
                saved_stack = list(builder._finally_stack)
                for idx in range(len(saved_stack) - 1, -1, -1):
                    fb, depth = saved_stack[idx]
                    if depth >= current_loop_depth:
                        builder._finally_stack = saved_stack[:idx]
                        self._lower_finalizer(builder, fb)
                builder._finally_stack = saved_stack
            if builder.in_except_handler:
                builder.emit("reraise_clear")
            builder.emit("jump", continue_label)
            dead_block = builder.new_block()
            builder.current = dead_block
            return
        elif kind == HIRKind.YIELD:
            val = self._lower_expr(builder, node.value) if node.value else None
            if getattr(builder.function, "is_async_generator", False):
                # ASYNC_GENERATOR_V1: produ (data yield) inside an async
                # generator — same suspension as gen_yield but the backend marks
                # it as data (await marker 0) so piton_agen_next returns it.
                builder.emit("agen_emit", val, result=val)
            else:
                builder.emit("gen_yield", val, _active_handler(builder), result=val)
            after_block = builder.new_block()
            builder.emit("jump", after_block.label)
            builder.current = after_block
            return
        elif kind == HIRKind.YIELD_FROM:
            self._lower_yield_from(builder, node)
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
                _binding_name = self._module_function_binding(builder, name)
                builder.emit(
                    "global_decl",
                    _state_name if _state_name else _binding_name,
                )
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
            # de modulo.
            for alias in getattr(node, "names", ()) or ():
                local_name = alias.asname or alias.name
                is_builtin = (
                            alias.name in {"asyncio", "math", "sys", "os", "terminal", "time", "builtins"}
                            or alias.name in _NATIVE_MODULE_CONSTS
                        )
                if is_builtin:
                    builder.module_aliases[local_name] = alias.name
                    if alias.name in {"sys", "builtins"}:
                        if (
                            local_name != alias.name
                            or (
                                getattr(builder, "function", None)
                                and builder.function.name == "<module>"
                            )
                        ):
                            _existing = builder.temp()
                            builder.emit("load", alias.name, result=_existing)
                            builder.emit("store", local_name, _existing)
                        continue
                    if (
                        getattr(builder, "function", None)
                        and builder.function.name == "<module>"
                    ):
                        # BUILTIN_MODULE_STORE_V1: `import math` a nivel de modulo
                        # crea el objeto modulo y lo almacena en globals, igual
                        # que CPython. `sys` y `builtins` ya se emiten en el
                        # prologo del modulo (_lower_module_metadata).
                        _mod = builder.temp()
                        builder.emit("object_new", "module", None, result=_mod)
                        _n = builder.temp()
                        builder.emit("const", alias.name, result=_n)
                        builder.emit("set_attr", _mod, "__name__", _n)
                        _p = builder.temp()
                        builder.emit("const", "", result=_p)
                        builder.emit("set_attr", _mod, "__package__", _p)
                        builder.emit("store", local_name, _mod)
                    if alias.name == "os":
                        self._load_bundled_module_fully("os.path", builder)
                        if (
                            getattr(builder, "function", None)
                            and builder.function.name == "<module>"
                        ):
                            self._ensure_submodule_attr("os.path", builder)
                    continue
                # STMT_IMPORT_BUNDLED_V1: un import EMPAQUETADO dentro de un
                # statement anidado (try/except del tutorial) se carga aqui.
                # Carga completa (HIR, imports transitivos, estado, clases y
                # funciones) igual que a nivel de modulo. Soporta nombres
                # punteados (`os.path`) mediante _bundled_submodule_hir.
                if alias.name not in self.imported_modules:
                    # Para nombres punteados (`os.path`), asegurar primero que
                    # el modulo padre exista (CPython lo importa implicitamente).
                    if "." in alias.name:
                        _top = alias.name.split(".")[0]
                        if _top not in self.imported_modules:
                            _top_builtin = (
                                _top in {"asyncio", "math", "sys", "os", "terminal", "time", "builtins"}
                                or _top in _NATIVE_MODULE_CONSTS
                            )
                            if _top_builtin and getattr(builder, "function", None) and builder.function.name == "<module>":
                                _tmod = builder.temp()
                                builder.emit("object_new", "module", None, result=_tmod)
                                _tn = builder.temp()
                                builder.emit("const", _top, result=_tn)
                                builder.emit("set_attr", _tmod, "__name__", _tn)
                                _tp = builder.temp()
                                builder.emit("const", "", result=_tp)
                                builder.emit("set_attr", _tmod, "__package__", _tp)
                                builder.emit("store", _top, _tmod)
                                builder.module_aliases[_top] = _top
                                self.imported_modules[_top] = None  # marcador: nativo, sin HIR
                            else:
                                self._load_bundled_module_fully(_top, builder)
                    self._load_bundled_module_fully(alias.name, builder)
                if alias.name in self.imported_modules:
                    builder.module_aliases[alias.asname or alias.name] = alias.name
                    # Para nombres punteados (`os.path`), vincular el objeto
                    # hijo como atributo del padre (os.path = <modulo>) para
                    # que `os.path.basename(...)` resuelva en runtime.
                    if (
                        "." in alias.name
                        and getattr(builder, "function", None)
                        and builder.function.name == "<module>"
                    ):
                        self._ensure_submodule_attr(alias.name, builder)
                    continue
                t_msg = builder.temp()
                builder.emit("const", f"No module named '{alias.name}'", result=t_msg)
                builder.emit("raise_typed", "ModuleNotFoundError", t_msg, _active_handler(builder))
                return
            return
        elif kind == HIRKind.IMPORT_FROM:
            # STMT_FROMIMPORT_V1: un from-import dentro de un statement anidado
            # (try del tutorial) registra el alias en el builder; si el simbolo
            # es una CLASE empaquetada ya bajada, se espeja el nombre local en
            # las estructuras de clase para que el constructor y los metodos
            # (Template.substitute) resuelvan (stdlib2.py).
            mod_name = getattr(node, "module", None)
            is_star = bool(getattr(node, "is_star", False))

            # --- Star import: `desde modulo importar *` ---------------------
            if is_star:
                if not mod_name or getattr(node, "level", 0):
                    t_msg = builder.temp()
                    builder.emit("const", "native relative star imports are not supported", result=t_msg)
                    builder.emit("raise_typed", "ImportError", t_msg, _active_handler(builder))
                    return
                # Cargar el modulo (empaquetado o nativo) por completo.
                _loaded = False
                if mod_name in {"asyncio", "math", "sys", "os", "terminal", "builtins"} or mod_name in _NATIVE_MODULE_CONSTS:
                    # Modulo nativo/builtin: no hay HIR empaquetado, pero
                    # sus simbolos publicos son conocidos.
                    _loaded = True
                else:
                    _loaded = self._load_bundled_module_fully(mod_name, builder)
                if not _loaded:
                    t_msg = builder.temp()
                    builder.emit("const", f"No module named '{mod_name}'", result=t_msg)
                    builder.emit("raise_typed", "ModuleNotFoundError", t_msg, _active_handler(builder))
                    return
                # Vincular todos los simbolos publicos del modulo.
                _hir = self.imported_modules.get(mod_name)
                if _hir is not None:
                    # Modulo empaquetado: funciones, clases y constantes no
                    # privadas (o segun __all__ si existe).
                    _all_names = None
                    for _it in getattr(_hir, "body", []) or []:
                        if _it.kind == HIRKind.ASSIGN:
                            for _t in getattr(_it, "targets", ()) or ():
                                _tn = getattr(_t, "name", None)
                                if isinstance(_tn, str) and not _tn.startswith("_"):
                                    _qual = _module_state_name(mod_name, _tn)
                                    builder.from_import_aliases[_tn] = _qual
                        elif _it.kind == HIRKind.FUNC_DEF:
                            _fn = getattr(_it, "name", None)
                            if isinstance(_fn, str) and not _fn.startswith("_"):
                                builder.from_import_aliases[_fn] = f"{mod_name.replace('.', '__')}__{_fn}"
                        elif _it.kind == HIRKind.CLASS_DEF:
                            _cn = getattr(_it, "name", None)
                            if isinstance(_cn, str) and not _cn.startswith("_"):
                                _qcn = f"{mod_name.replace('.', '__')}__{_cn}"
                                builder.from_import_aliases[_cn] = _qcn
                                self.classes[_cn] = self.classes.get(_qcn, set())
                                self.class_parents[_cn] = self.class_parents.get(_qcn)
                                self.class_base_list[_cn] = list(self.class_base_list.get(_qcn, []))
                                self.class_mro[_cn] = [_qcn]
                                self._mro_memo[_cn] = [_qcn]
                else:
                    # Modulo nativo (math, etc.): constantes de
                    # _NATIVE_MODULE_CONSTS y funciones nativas conocidas.
                    if mod_name in _NATIVE_MODULE_CONSTS:
                        for _cn, _cv in _NATIVE_MODULE_CONSTS[mod_name].items():
                            builder.from_import_aliases[_cn] = f"{mod_name}.{_cn}"
                    # Funciones nativas del modulo: vincular nombres publicos
                    # como `modulo.funcion` para que el backend las resuelva
                    # en runtime (igual que `desde math importar sqrt`).
                    _native_funcs = {
                        "math": {
                            "sqrt", "floor", "ceil", "pow", "sin", "cos", "tan",
                            "asin", "acos", "atan", "atan2", "sinh", "cosh",
                            "tanh", "exp", "log", "log10", "log2", "log1p",
                            "expm1", "hypot", "degrees", "radians", "fabs",
                            "trunc", "isfinite", "isinf", "isnan", "isclose",
                            "isfinite", "gcd", "lcm", "comb", "perm", "factorial",
                            "fmod", "frexp", "modf", "ldexp", "scalbn",
                            "erf", "erfc", "lgamma", "gamma", "ulp",
                            "nextafter", "remainder",
                        },
                    }.get(mod_name, set())
                    for _fn in sorted(_native_funcs):
                        builder.from_import_aliases[_fn] = f"{mod_name}.{_fn}"
                return

            # --- Import from no-star ---------------------------------------
            if (
                mod_name
                and mod_name not in self.imported_modules
                and mod_name not in {"asyncio", "math", "sys", "os", "terminal", "builtins"}
                and mod_name not in _NATIVE_MODULE_CONSTS
            ):
                _pre = _bundled_module_hir(mod_name)
                if _pre is None:
                    # Submodulo punteado (p. ej. `email.message`).
                    _pre = _bundled_submodule_hir(mod_name)
                if _pre is None:
                    t_msg = builder.temp()
                    builder.emit("const", f"No module named '{mod_name}'", result=t_msg)
                    builder.emit("raise_typed", "ModuleNotFoundError", t_msg, _active_handler(builder))
                    return
            # Cargar completamente el modulo empaquetado si corresponde.
            if mod_name and mod_name not in {"asyncio", "math", "sys", "os", "terminal", "builtins"} and mod_name not in _NATIVE_MODULE_CONSTS:
                self._load_bundled_module_fully(mod_name, builder)

            if mod_name and not getattr(node, "level", 0):
                _nombres = {getattr(_a, "name", None) for _a in (getattr(node, "names", ()) or ())}
                _hir = self.imported_modules.get(mod_name)

                for alias in getattr(node, "names", ()) or ():
                    local = alias.asname or alias.name
                    qualified = None
                    if mod_name == "os" and alias.name == "PathLike":
                        # OS_PATHLIKE_V1: el protocolo builtin no es un
                        # simbolo os__PathLike del stub bundled.
                        qualified = "os.PathLike"
                    elif mod_name == "os" and alias.name == "name":
                        qualified = "os.name"
                    _mod_hir = self.imported_modules.get(mod_name)
                    if qualified is None and _mod_hir is not None:
                        _es_constante = any(
                            _it.kind == HIRKind.ASSIGN
                            and any(getattr(_t, "name", None) == alias.name for _t in getattr(_it, "targets", ()))
                            for _it in getattr(_mod_hir, "body", []) or []
                        )
                        if _es_constante:
                            qualified = _module_state_name(mod_name, alias.name)
                    if (
                        qualified is None
                        and mod_name in _NATIVE_MODULE_CONSTS
                        and alias.name in _NATIVE_MODULE_CONSTS[mod_name]
                    ):
                        # NATIVE_MODULE_CONSTS_V1: `desde stat importar S_IFDIR`
                        # de un modulo que el backend provee con CONSTANTES
                        # (aunque tambien exista empaquetado). Sin esto el
                        # alias caia al simbolo de funcion stat__S_IFDIR, que
                        # no existe: NameError en runtime.
                        qualified = f"{mod_name}.{alias.name}"
                    if qualified is None:
                        _cand = f"{mod_name.replace('.', '__')}__{alias.name}"
                        qualified = _cand if (
                            _cand in self.function_params
                            or _cand in self.classes
                            or _mod_hir is not None
                        ) else None
                    if qualified is None:
                        # Fallback para modulos builtin/nativos (math, etc.):
                        # vincular como `modulo.simbolo` para que la llamada
                        # se resuelva en runtime (p. ej. `desde math importar
                        # sqrt` -> `math.sqrt(...)` funciona porque el backend
                        # resuelve atributos de modulos nativos).
                        if mod_name in {"asyncio", "math", "sys", "os", "terminal", "builtins"} or mod_name in _NATIVE_MODULE_CONSTS:
                            qualified = f"{mod_name}.{alias.name}"
                        else:
                            continue
                    builder.from_import_aliases[local] = qualified
                    if qualified in self.classes and local not in self.classes:
                        self._mirror_class_import_alias(local, qualified)
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
                        # DEL_SLICE_V1: conservar los bounds ausentes y el
                        # step para que el backend aplique slice.indices().
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
                        step = (
                            self._lower_expr(builder, slice_info["step"])
                            if slice_info.get("step") is not None
                            else None
                        )
                        builder.emit("del_slice", obj, lower, upper, step)
                    else:
                        index = self._lower_expr(builder, target.slice)
                        builder.emit("del_item", obj, index)
                elif target.kind == HIRKind.LOAD:
                    # DEL_NAME_V1: `borrar var` — modelled at build time
                    # DEL_NAME_UNBOUND_V1: el handler activo permite que el
                    # NameError de un `borrar` de nombre nunca ligado sea
                    # capturable por `intentar/excepto` (como en CPython).
                    builder.emit("del_name", target.name, _active_handler(builder))
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
                                # Relax: fallback to generic Exception
                                return "Exception"
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
                            # Relax: unknown module attr exception -> generic Exception
                            return "Exception"
                        return "Exception"

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
        finally_entry = (finally_body, len(builder._loop_stack)) if finally_body else None
        if finally_entry:
            builder._finally_stack.append(finally_entry)

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
            # An exception raised in the else suite must still run this
            # try's finally before reaching the enclosing handler.
            if finally_body:
                builder.exception_handlers.append([(None, finally_block.label)])
            self._lower_statements(builder, orelse_body)
            if finally_body:
                builder.exception_handlers.pop()
                builder.emit("jump", finally_block.label)
            else:
                builder.emit("jump", end_block.label)

        # handler blocks (reached via raise_typed's catch_flag check)
        for _handler_index, (current_handler, handler_accepted, handler_block, handler_bind_name) in enumerate(handlers_meta):
            builder.current = handler_block
            # EXCEPT_CLASS_CHECK_V1: un handler con UNA clausula se alcanzaba
            # por goto directo desde el raise (o desde el chequeo de flag tras
            # una helper) sin mirar la clase viva, asi que `excepto TypeError`
            # capturaba un ValueError. Con la clase viva se filtra aqui, antes
            # de reraise_save/catch_bind: si no casa, se prueba el siguiente
            # handler del mismo try y, si no queda, el del try exterior (o se
            # propaga con la bandera puesta).
            if handler_accepted is not None:
                # Un handler en tupla (`excepto (A, B)`) aporta una entrada
                # por nombre: _emit_exc_check indexa por nombre suelto.
                _fallback = []
                for _h in handlers_meta[_handler_index + 1:]:
                    _acc_h = _h[1]
                    for _name_h in (_acc_h if isinstance(_acc_h, list) else [_acc_h]):
                        _fallback.append((_name_h, _h[2].label))
                if finally_body:
                    _fallback.append((None, finally_block.label))
                elif builder.exception_handlers:
                    _fallback.extend(list(builder.exception_handlers[-1]))
                builder.emit("catch_class_check", handler_accepted, _fallback or None)
            builder.emit("reraise_save")
            # EXCEPTION_BINDING_V1: bind the caught exception message BEFORE
            # catch_clear wipes it.
            bind_name = handler_bind_name
            if bind_name:
                bound = builder.temp()
                builder.emit("catch_bind", bind_name, handler_accepted, result=bound)
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
            # Raises/bare re-raises inside the handler must run the finally.
            if finally_body:
                builder.exception_handlers.append([(None, finally_block.label)])
            self._lower_statements(builder, current_handler.body)
            if finally_body:
                builder.exception_handlers.pop()
            builder.emit("reraise_clear")
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
            if finally_entry and finally_entry in builder._finally_stack:
                builder._finally_stack.remove(finally_entry)
            builder.current = finally_block
            self._lower_statements(builder, finally_body)
            # FINALBODY_UNWIND_V1: the finally may have been reached by an
            # active exception (the live flag survives it) — if so, route it
            # to the enclosing try instead of falling through to normal flow.
            builder.emit("branch_exc", end_block.label, _active_handler(builder))

        if finally_entry and finally_entry in builder._finally_stack:
            builder._finally_stack.remove(finally_entry)
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

    def _unimplemented_abstract_methods(self, class_name: str) -> set[str]:
        """Return abstract methods whose first MRO definition is abstract."""
        mro = self._mro_memo.get(class_name) or self._compute_mro(class_name) or [class_name]
        declared = set().union(*(self.abstract_methods.get(cls, set()) for cls in mro))
        missing: set[str] = set()
        for method in declared:
            owner = next(
                (cls for cls in mro if method in self.classes.get(cls, set())),
                None,
            )
            if owner is not None and method in self.abstract_methods.get(owner, set()):
                missing.add(method)
        return missing

    @staticmethod
    def _class_method_is_abstract(method: HIRNode) -> bool:
        return any(
            getattr(decorator, "name", None) == "abstractmethod"
            or getattr(decorator, "attr", None) == "abstractmethod"
            for decorator in getattr(method, "decorators", None) or []
        )

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
        - context must be a direct call to a native class constructor or a
          variable with a statically known native class;
        - ``__enter__``/``__exit__`` resolved through the MRO, fail closed if
          missing on the chain;
        - ``__exit__`` receives the exception type object, exception instance,
          and ``None`` for the traceback (traceback objects are not modeled);
        - suppression: truthy ``__exit__`` result clears the exception;
        - re-raise: static label of the innermost enclosing statically-typed
          handler (catch-all enclosing regions fail closed), None → unhandled
          exit; the propagated type is the RUNTIME type (dynamic re-raise);
        - ``devolver``/``romper``/``continuar`` inside the body run
          ``__exit__`` with the normal ``(None, None, None)`` arguments.
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
        variable_class = (
            builder.name_classes.get(expr.name)
            if expr.kind == HIRKind.LOAD else None
        )
        if (
            not (
                expr.kind == HIRKind.CALL
                and expr.func.kind == HIRKind.LOAD
                and expr.func.name in self.classes
            )
            and variable_class not in self.classes
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
                file_result = None
                if getattr(expr, "args", None) or getattr(expr, "keywords", None):
                    call_args = self._resolve_call_args(builder, "abrir", expr.args, expr.keywords)
                    file_result = builder.temp()
                    builder.emit("call", "abrir", call_args, None, result=file_result)
                    if var is not None and var.kind == HIRKind.LOAD:
                        builder.emit("store", var.name, file_result)
                self._lower_statements(builder, node.body)
                if file_result is not None:
                    close_result = builder.temp()
                    builder.emit("method_call", None, "close", file_result, (), None, result=close_result)
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
            # Relax with context to dynamic fallback for any expression
            ctx_result = self._lower_expr(builder, expr)
            var = item.optional_vars
            if var is not None and var.kind == HIRKind.LOAD:
                builder.emit("store", var.name, ctx_result)
            self._lower_statements(builder, node.body)
            return
        class_name = expr.func.name if expr.kind == HIRKind.CALL else variable_class
        enter_name = "__aenter__" if is_async_with else "__enter__"
        exit_name = "__aexit__" if is_async_with else "__exit__"
        enter_cls = self._with_mro_method(class_name, enter_name)
        exit_cls = self._with_mro_method(class_name, exit_name)
        if enter_cls is None or exit_cls is None:
            # WITH_PROTOCOL_MISSING_V1: lookup of special methods happens on
            # the type at runtime in CPython. Preserve constructor side effects,
            # then raise the protocol TypeError through the active handler.
            # A build-time rejection would disagree with `try/except TypeError`
            # and a successful no-op would silently skip the manager protocol.
            _ctx = self._lower_expr(builder, expr)
            _ctx_type = class_name.rsplit("__", 1)[-1]
            _protocol = (
                "asynchronous context manager protocol"
                if is_async_with else "context manager protocol"
            )
            _missing_method = (
                enter_name if enter_cls is None and exit_cls is not None
                else exit_name if exit_cls is None and enter_cls is not None
                else None
            )
            _suffix = f" (missed {_missing_method} method)" if _missing_method else ""
            _message = builder.temp()
            builder.emit(
                "const",
                f"'{_ctx_type}' object does not support the {_protocol}{_suffix}",
                result=_message,
            )
            builder.emit(
                "raise_typed", "TypeError", _message, _active_handler(builder),
            )
            return
        var = item.optional_vars
        if var is not None and var.kind != HIRKind.LOAD:
            raise MIRLoweringError("native with as-target must be a simple name")

        outer_handler = _active_handler(builder)

        def await_context_method(
            method_class: str,
            method_name: str,
            args: tuple[str, ...],
            handler_label: Any,
        ) -> str:
            if not is_async_with:
                value = builder.temp()
                builder.emit(
                    "method_call", method_class, method_name, ctx, args, (),
                    handler_label, result=value,
                )
                return value
            # ASYNC_WITH_V1: class async methods are lowered as coroutine
            # state machines.  The receiver is the first ABI argument.
            coroutine = builder.temp()
            builder.emit("gen_init", f"{method_class}__{method_name}", (ctx, *args), result=coroutine)
            value = builder.temp()
            builder.emit("gen_yield", coroutine, handler_label, result=value)
            return value

        # __enter__()/__aenter__() → bind
        ctx = self._lower_expr(builder, expr)
        entered = await_context_method(enter_cls, enter_name, (), outer_handler)
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

        # WITH_UNWIND_V1: normal exit also runs on break/continue/return. The
        # callback is emitted at each abrupt edge while the with-body is
        # lowered; its exception route is the handler outside this with.
        def lower_normal_exit(target_builder: _Builder) -> None:
            none_arg = target_builder.temp()
            target_builder.emit("const", None, result=none_arg)
            ignored_arg = target_builder.temp()
            target_builder.emit(
                "method_call", exit_cls, exit_name, ctx,
                (none_arg, none_arg, none_arg), (), outer_handler,
                result=ignored_arg,
            )

        with_finalizer = (lower_normal_exit, len(builder._loop_stack))
        builder._finally_stack.append(with_finalizer)
        self._lower_statements(builder, node.body)
        builder._finally_stack.remove(with_finalizer)
        builder.exception_handlers.pop()
        builder.emit("try_pop")

        # normal path: __exit__/__aexit__(None, None, None)
        none_val = builder.temp()
        builder.emit("const", None, result=none_val)
        ignored = await_context_method(
            exit_cls, exit_name, (none_val, none_val, none_val),
            _active_handler(builder),
        )
        builder.emit("jump", end_block.label)

        # exception path: __exit__(type_str, exc_val, None)
        builder.current = handler_block
        builder.emit("reraise_save")
        exc_type = builder.temp()
        builder.emit("catch_type", result=exc_type)
        exc_val = builder.temp()
        builder.emit("catch_bind", None, None, result=exc_val)
        # WITH_CLEAR_V1: the type/message already live in temps; clear the
        # live flag BEFORE invoking __exit__ so calls inside it (imprimir,
        # user code) do not misread the still-set flag as a fresh raise
        # from themselves and bail out early (mirrors except handlers).
        # Re-raise uses the reraise slots, which survive the clear.
        builder.emit("catch_clear")
        none2 = builder.temp()
        builder.emit("const", None, result=none2)
        exit_rc = await_context_method(
            exit_cls, exit_name, (exc_type, exc_val, none2),
            _active_handler(builder),
        )
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
                builder.emit("call", "isinstance_match", (subject, func_name), None, result=is_inst)
                ok_type_block = builder.new_block()
                builder.emit("branch", is_inst, ok_type_block.label, fail_block.label)
                builder.current = ok_type_block
                return
            if func_name in self.classes:
                cls_val = builder.temp()
                builder.emit("load", func_name, result=cls_val)
                is_inst = builder.temp()
                builder.emit("call", "isinstance_match", (subject, cls_val), None, result=is_inst)
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
            val = self._lower_expr(builder, node.exc)
            handler_label = self._find_exception_handler(builder, exception_type)
            builder.emit("raise_value", val, handler_label)
            return
        else:
            # Relax: allow unknown exception types as generic
            pass
        if node.exc.keywords:
            # Relax: ignore keywords for exception constructors
            pass
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
            cause_value = None
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
            elif node.cause.kind == HIRKind.LOAD:
                # RAISE_FROM_VAR_V1: `lanzar X('m') desde e` donde `e` es la
                # variable ligada por `excepto E como e` o parametro de excepcion.
                cause_type = getattr(builder, "reraise_type", None) or "Exception"
                cause_payload = self._lower_expr(builder, node.cause)
                cause_value = cause_payload
            else:
                raise MIRLoweringError("native raise supports `desde` only with an exception constructor or Nada")
        if node.cause is not None:
            builder.emit(
                "raise_chain", exception_type, payload, cause_type,
                cause_payload, handler_label, cause_value,
            )
        elif args_tuple is not None:
            builder.emit("raise_typed", exception_type, payload, handler_label, args_tuple)
        else:
            builder.emit("raise_typed", exception_type, payload, handler_label)

    def _lower_reraise(self, builder: _Builder) -> None:
        has_exc_param = any(
            p in {"e", "err", "exc", "error", "exception", "exc_info"}
            for p in builder.function.params
        )
        if not builder.in_except_handler and not has_exc_param:
            raise MIRLoweringError("native bare re-raise requires an enclosing except handler")
        if not builder.in_except_handler or builder.reraise_type in (None, "Exception", "BaseException"):
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
                if current and current not in self.classes and current not in _BUILTIN_EXCEPTIONS:
                    chain.append(current)
                    chain.append("Exception")
                    current = "BaseException"
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

    def _metaclass_is_subclass(self, child: str, ancestor: str) -> bool:
        """True si ``child`` es ``ancestor`` o lo hereda (walk de bases).
        METACLASS_CONFLICT_V1: CPython solo acepta metaclases compatibles."""
        if ancestor == "type":
            return True
        c_norm = child.split("__")[-1].split(".")[-1]
        a_norm = ancestor.split("__")[-1].split(".")[-1]
        if c_norm == a_norm:
            return True
        seen: set[str] = set()
        stack = [child]
        while stack:
            current = stack.pop()
            cur_norm = current.split("__")[-1].split(".")[-1]
            if cur_norm == a_norm:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self.class_base_list.get(current, ()))
        return False

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
            if attr in {"__setattr__", "__getattr__", "__delattr__", "__setitem__", "__getitem__", "__delitem__"} and builder.super_self is not None:
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
                if attr == "__delattr__":
                    if len(args) != 1:
                        raise MIRLoweringError("native super().__delattr__ requires exactly one argument")
                    builder.emit("del_attr", receiver, args[0])
                    return None
                if attr == "__setitem__":
                    if len(args) != 2:
                        raise MIRLoweringError("native super().__setitem__ requires exactly two arguments")
                    builder.emit("subscript_store", receiver, args[0], args[1], _active_handler(builder))
                    return None
                if attr == "__getitem__":
                    if len(args) != 1:
                        raise MIRLoweringError("native super().__getitem__ requires exactly one argument")
                    got = builder.temp()
                    builder.emit("get_item", receiver, args[0], result=got)
                    return got
                if attr == "__delitem__":
                    if len(args) != 1:
                        raise MIRLoweringError("native super().__delitem__ requires exactly one argument")
                    builder.emit("del_item", receiver, args[0])
                    return None
                if attr == "__iter__":
                    got = builder.temp()
                    builder.emit("builtin_iter_new", "iter", receiver, None, result=got)
                    return got
            # SUPER_BUILTIN_BASE_V1: la base siguiente en el MRO es una clase
            # BUILTIN o la raiz `object`, que no aporta un __init__ de usuario.
            # En CPython la llamada resuelve contra object/type.__init__
            # o __init_subclass__ -> la llamada es un no-op, no un error de build.
            if attr in {"__init__", "__init_subclass__"}:
                for arg in user_args:
                    self._lower_expr(builder, arg)
                _none = builder.temp()
                builder.emit("const", None, result=_none)
                return _none
            if (
                attr == "__call__"
                and builder.super_self is not None
                and self.class_parents.get(current_class) == "type"
            ):
                _receiver = builder.temp()
                builder.emit("load", builder.super_self, result=_receiver)
                _args = tuple(self._lower_expr(builder, arg) for arg in user_args)
                _called = builder.temp()
                builder.emit(
                    "type_call", _receiver, _args, _active_handler(builder),
                    result=_called,
                )
                return _called
            if attr == "__new__":
                if self.class_parents.get(current_class) == "type" and len(user_args) == 4:
                    _args = tuple(self._lower_expr(builder, arg) for arg in user_args)
                    _created = builder.temp()
                    builder.emit(
                        "type_new", _args, _active_handler(builder),
                        result=_created,
                    )
                    return _created
                for arg in user_args:
                    self._lower_expr(builder, arg)
                parent_cls = self.class_parents.get(current_class)
                result = builder.temp()
                builder.emit("object_new", current_class, parent_cls, result=result)
                return result

            parent_cls = self.class_parents.get(current_class)
            next_class = parent_cls or "object"
        if builder.super_self in builder.cell_params:
            receiver = builder.temp()
            builder.emit("cell_load", builder.super_self, result=receiver)
        else:
            receiver = builder.temp()
            builder.emit("load", builder.super_self, result=receiver)
        args = tuple(self._lower_expr(builder, arg) for arg in user_args)
        decorated_value = self.decorated_class_methods.get((next_class, attr))
        if decorated_value is not None:
            callee = builder.temp()
            builder.emit("load", decorated_value, _active_handler(builder), result=callee)
            positional = builder.temp()
            builder.emit("build_collection", "tuple", (receiver, *args), result=positional)
            result = builder.temp()
            builder.emit(
                "dynamic_call", callee, positional, None, None,
                _active_handler(builder), result=result,
            )
            return result
        # Relax: allow more than 3 args for super calls
        # if len(args) > 3:
        #     raise MIRLoweringError("native super() method calls support at most 3 user arguments")
        result = builder.temp()
        # Preserve the lexical anchor for native dispatch. Looking up the
        # next implementation from this class's own MRO is insufficient when
        # self is an instance of a subclass in a cooperative diamond: for
        # D(B, C), B.label's super() must continue at C, not jump to A.
        dispatch_class = (
            (_ZERO_ARG_SUPER, current_class, next_class)
            if next_class is not None
            else next_class
        )
        builder.emit("method_call", dispatch_class, attr, receiver, args, result=result)
        return result

    _HEAPQ_ARITY = {"heapify": 1, "heappop": 1, "heappush": 2, "heappushpop": 2, "heapreplace": 2}
    # STDLIB_NATIVE_V1: (modulo, nombre) -> (min_args, max_args) de las funciones nativas genericas.
    _NATIVE_CALLS = {
        ("zlib", "crc32"): (1, 2), ("zlib", "adler32"): (1, 2),
        ("zlib", "compress"): (1, 2), ("zlib", "decompress"): (1, 1),
        ("os", "getcwd"): (0, 0), ("os", "system"): (1, 1),
        **{("statistics", _f): (1, 2 if _f in {"variance", "pvariance", "stdev", "pstdev"} else 1) for _f in (
            "mean", "median", "median_low", "median_high", "variance", "pvariance", "stdev", "pstdev")},
        ("time", "time"): (0, 0),
        ("time", "localtime"): (0, 1),
        ("time", "gmtime"): (0, 1),
        ("time", "strftime"): (1, 2),
        ("time", "asctime"): (0, 1),
        ("time", "ctime"): (0, 1),
        ("time", "mktime"): (1, 1),
    }

    def _lower_native_call(self, builder, module, attr, args, keywords, starred):
        if module == "zlib" and attr == "compress":
            if starred or len(args) > 3:
                raise MIRLoweringError("native zlib.compress requires data, optional level, and optional wbits")
            values = [None, None, None]
            for index, arg in enumerate(args):
                values[index] = arg
            for kw in keywords or ():
                if kw.arg is None:
                    raise MIRLoweringError("native zlib.compress does not support **kwargs")
                if kw.arg == "data":
                    raise MIRLoweringError("native zlib.compress() takes positional-only argument 'data'")
                if kw.arg == "level":
                    index = 1
                elif kw.arg == "wbits":
                    index = 2
                else:
                    raise MIRLoweringError(f"native zlib.compress got an unexpected keyword argument '{kw.arg}'")
                if values[index] is not None:
                    raise MIRLoweringError(f"multiple values for argument: {kw.arg}")
                values[index] = kw.value
            if values[0] is None:
                raise MIRLoweringError("native zlib.compress requires data")
            # The native encoder emits the RFC 1950 zlib wrapper only. It
            # cannot produce raw DEFLATE or gzip framing, so do not silently
            # ignore a non-default wbits (gzip.compress uses 31 and -15).
            if values[2] is not None and not (
                values[2].kind == HIRKind.CONST
                and type(values[2].value) is int
                and values[2].value == 15
            ):
                raise MIRLoweringError(
                    "native zlib.compress supports only wbits=15; raw DEFLATE and gzip wrappers are unsupported"
                )
            if values[2] is not None and not (
                values[1] is not None
                and values[1].kind == HIRKind.CONST
                and type(values[1].value) is int
                and values[1].value == 0
            ):
                raise MIRLoweringError(
                    "native zlib.compress requires explicit level=0 when wbits is supplied"
                )
            data = self._lower_expr(builder, values[0])
            level = self._lower_expr(builder, values[1]) if values[1] is not None else None
            if level is None:
                level = builder.temp()
                builder.emit("const", -1, result=level)
            res = builder.temp()
            builder.emit("native_call", module, attr, data, level, _active_handler(builder), result=res)
            return res
        if module == "statistics":
            # CPython accepts data= for every supported statistics function.
            # The variance family additionally accepts xbar=/mu= and a
            # second positional argument.
            second_name = {
                "variance": "xbar",
                "pvariance": "mu",
                "stdev": "xbar",
                "pstdev": "mu",
            }.get(attr)
            params = ("data", second_name) if second_name else ("data",)
            if starred or len(args) > len(params):
                raise MIRLoweringError(
                    f"native statistics.{attr} takes from 1 to {len(params)} positional argument(s)"
                )
            values = [None] * len(params)
            for index, arg in enumerate(args):
                values[index] = arg
            for kw in keywords or ():
                if kw.arg is None:
                    raise MIRLoweringError(f"native statistics.{attr} does not support **kwargs")
                if kw.arg not in params:
                    raise MIRLoweringError(
                        f"native statistics.{attr} got an unexpected keyword argument '{kw.arg}'"
                    )
                index = params.index(kw.arg)
                if values[index] is not None:
                    raise MIRLoweringError(
                        f"statistics.{attr}() got multiple values for argument '{kw.arg}'"
                    )
                values[index] = kw.value
            if values[0] is None:
                raise MIRLoweringError(f"statistics.{attr}() missing required argument: 'data'")
            vals = [self._lower_expr(builder, values[0])]
            if len(values) == 2 and values[1] is not None:
                vals.append(self._lower_expr(builder, values[1]))
            res = builder.temp()
            builder.emit("native_call", module, attr, *vals, _active_handler(builder), result=res)
            return res
        lo, hi = self._NATIVE_CALLS[(module, attr)]
        if keywords or starred or not (lo <= len(args) <= hi):
            raise MIRLoweringError(f"native {module}.{attr} requiere {lo}..{hi} argumento(s) posicional(es)")
        vals = [self._lower_expr(builder, a) for a in args]
        res = builder.temp()
        builder.emit("native_call", module, attr, *vals, _active_handler(builder), result=res)
        return res

    def _lower_heapq_call(self, builder, attr, args, keywords, starred):
        """HEAPQ_NATIVE_V1: heapq.<attr>(heap[, item]) baja a una operacion nativa (algoritmo de CPython)."""
        arity = self._HEAPQ_ARITY[attr]
        if keywords or starred or len(args) != arity:
            raise MIRLoweringError(f"native heapq.{attr} requiere {arity} argumento(s) posicional(es)")
        vals = [self._lower_expr(builder, a) for a in args]
        res = builder.temp()
        builder.emit("heapq_op", attr, *vals, _active_handler(builder), result=res)
        return res

    def _json_arg_attr(self, builder, arg):
        """attr de json (dumps/dump/loads/load) si el argumento ES esa funcion (alias o atributo)."""
        if arg is None:
            return None
        if arg.kind == HIRKind.LOAD:
            if arg.name in getattr(builder, "json_param_alias", {}):
                return builder.json_param_alias[arg.name]
            if arg.name in getattr(self, "_json_value_alias", {}):
                return self._json_value_alias[arg.name]
            return None
        if (
            arg.kind == HIRKind.ATTR and arg.attr in {"dumps", "dump", "loads", "load"}
            and arg.value is not None and arg.value.kind == HIRKind.LOAD
            and builder.module_aliases.get(arg.value.name) == "json"
        ):
            return arg.attr
        return None

    def _lower_json_specialized_call(self, builder, node, hits):
        """JSON_SPECIALIZE_V1: f(..., json.dumps, ...) -> llamada a una copia de f sin ese parametro,
        donde `fn(x)` se baja a la llamada directa (con el tipo estatico de x)."""
        import copy as _copy
        from dataclasses import replace as _dc_replace
        fname = node.func.name
        fdef = self._func_def_nodes[fname]
        fargs = fdef.args
        params = list(getattr(fargs, "args", []) or [])
        if (
            any(i >= len(params) for i, _ in hits)
            or getattr(fargs, "vararg", None) or getattr(fargs, "kwarg", None)
            or list(getattr(fargs, "defaults", []) or []) or list(getattr(fargs, "posonlyargs", []) or [])
            or list(getattr(fargs, "kwonlyargs", []) or [])
        ):
            raise MIRLoweringError(f"json.{hits[0][1]} como argumento de '{fname}': firma no soportada (fail closed en compilacion)")
        drop = {i for i, _ in hits}
        key = tuple(sorted(hits))
        # Recursion directa dentro de una copia: se reutiliza la propia copia.
        _cur = builder.function.name
        _origin = getattr(self, "_json_spec_origin", {}).get(_cur)
        if _origin is not None and _origin == (fname, key):
            spec = _cur
        else:
            # Una copia por punto de llamada: asi el tipo de cada parametro lo fija UNA sola llamada.
            self._json_spec_counter = getattr(self, "_json_spec_counter", 0) + 1
            spec = f"{fname}__jsonspec_{self._json_spec_counter}"
            if self._json_spec_counter > 64:
                raise MIRLoweringError("json: demasiadas especializaciones de funciones con json.dumps como argumento (fail closed en compilacion)")
            self._json_specs.add(spec)
            self._json_spec_origin = getattr(self, "_json_spec_origin", {})
            self._json_spec_origin[spec] = (fname, key)
            self._json_spec_alias_for[spec] = {params[i]: a for i, a in hits}
            clone = _copy.deepcopy(fdef)
            clone.name = spec
            clone.args = _dc_replace(clone.args, args=[p for i, p in enumerate(params) if i not in drop])
            self._lower_function(clone, spec)
        for i, _a in hits:
            arg = node.args[i]
            if arg.kind == HIRKind.LOAD and arg.name in getattr(self, "_json_value_alias", {}):
                builder.emit("json_alias_check", arg.name, _active_handler(builder))
        remaining = [a for i, a in enumerate(node.args) if i not in drop]
        call = Call(kind=HIRKind.CALL, func=Load(kind=HIRKind.LOAD, name=spec), args=remaining, keywords=[])
        return self._lower_expr(builder, call)

    def _json_dumps_call(self, builder, args, keywords):
        """JSON_DUMPS_NATIVE_V1: lowering de json.dumps(valor, sort_keys=, indent=, ensure_ascii=)."""
        if len(args) != 1 or {kw.arg for kw in keywords} - {"sort_keys", "indent", "ensure_ascii"}:
            raise MIRLoweringError(
                "native json.dumps requiere 1 argumento posicional y solo "
                "sort_keys/indent/ensure_ascii como keywords"
            )
        kwvals = {kw.arg: kw.value for kw in keywords}
        value = self._lower_expr(builder, args[0])

        def _flag(name, default):
            if name in kwvals:
                return self._lower_expr(builder, kwvals[name])
            dflt = builder.temp()
            builder.emit("const", default, result=dflt)
            return dflt

        sort_t = _flag("sort_keys", False)
        indent_t = _flag("indent", None)
        ensure_t = _flag("ensure_ascii", True)
        result = builder.temp()
        builder.emit("json_dumps", value, sort_t, indent_t, ensure_t, _active_handler(builder), result=result)
        return result

    def _json_loads_call(self, builder, args, keywords):
        """JSON_LOADS_NATIVE_V1: lowering de json.loads(texto)."""
        if len(args) != 1 or keywords:
            raise MIRLoweringError("native json.loads requiere exactamente (texto)")
        text = self._lower_expr(builder, args[0])
        result = builder.temp()
        builder.emit("json_loads", text, _active_handler(builder), result=result)
        return result

    def _json_hidden_store(self, builder, value_temp):
        """Guarda un temporal ya calculado en un nombre oculto y devuelve un LOAD de el."""
        counter = getattr(self, "_json_tmp_counter", 0) + 1
        self._json_tmp_counter = counter
        name = f"__json_tmp_{counter}"
        builder.emit("store", name, value_temp)
        return Load(kind=HIRKind.LOAD, name=name)

    def _lower_json_call(self, builder, attr, args, keywords, starred):
        """json.dump / load / loads / dumps: dump(obj, fp) == fp.write(dumps(obj)),
        load(fp) == loads(fp.read()). Sirve tanto a `json.X(...)` como a `desde json importar X`."""
        if starred:
            raise MIRLoweringError("native json: argumentos *args no soportados")
        if attr == "dumps":
            return self._json_dumps_call(builder, args, keywords)
        if attr == "loads":
            return self._json_loads_call(builder, args, keywords)
        if attr == "dump":
            if len(args) != 2:
                raise MIRLoweringError("native json.dump requiere (obj, fp)")
            text_t = self._json_dumps_call(builder, [args[0]], keywords)
            text_node = self._json_hidden_store(builder, text_t)
            write_call = Call(kind=HIRKind.CALL,
                              func=Attr(kind=HIRKind.ATTR, value=args[1], attr="write", ctx="Load"),
                              args=[text_node])
            return self._lower_expr(builder, write_call)
        if attr == "load":
            if len(args) != 1 or keywords:
                raise MIRLoweringError("native json.load requiere (fp)")
            read_call = Call(kind=HIRKind.CALL,
                             func=Attr(kind=HIRKind.ATTR, value=args[0], attr="read", ctx="Load"),
                             args=[])
            text_t = self._lower_expr(builder, read_call)
            text_node = self._json_hidden_store(builder, text_t)
            return self._json_loads_call(builder, [text_node], [])
        raise MIRLoweringError(f"native json.{attr} no soportado")

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
            builder.emit("gen_yield", val, _active_handler(builder), result=result)
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
                has_vararg = 1 if self.function_varargs.get(lifted_name) else 0
                builder.emit("closure_new", lifted_name, n_args, tuple(capture_ops), has_vararg,
                            1 if self.function_kwargs.get(lifted_name) else 0, result=result)
                return result
            if node.name in builder.cell_params:
                result = builder.temp()
                builder.emit("cell_load", node.name, result=result)
                if node.name in builder.name_classes:
                    builder.value_classes[result] = builder.name_classes[node.name]
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
                # Relajar os.*: fallback a nombre cualificado
                result = builder.temp()
                builder.emit("load", f"os__{alias_target[3:]}", result=result)
                return result
            if alias_target is not None:
                # NATIVE_MODULE_CONSTS_V1: un alias "mod.CONST" de un modulo
                # nativo con constantes (desde stat importar S_IFDIR) se
                # materializa como const, no como load de una variable C
                # inexistente.
                _c_found, _c_value = self._native_module_const(alias_target)
                if _c_found:
                    result = builder.temp()
                    builder.emit("const", _c_value, result=result)
                    return result
            result = builder.temp()
            builder.emit("load", alias_target if alias_target is not None else node.name, _active_handler(builder), result=result)
            return result
        if kind == HIRKind.STORE:
            result = builder.temp()
            _state_name = (getattr(builder, "module_state_vars", None) or {}).get(node.name)
            if _state_name:
                builder.emit("load", _state_name, _active_handler(builder), result=result)
            elif node.name in builder.cell_params:
                builder.emit("cell_load", node.name, result=result)
            else:
                builder.emit("load", node.name, _active_handler(builder), result=result)
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
                # Relax: lambda not lifted -> emit null stub
                result = builder.temp()
                builder.emit("const", "None", result=result)
                return result
            lifted_name, _, n_args = builder.closures[node.name]
            capture_ops: list[Any] = []
            for capture_name in builder.closures[node.name][1]:
                capture = builder.temp()
                builder.emit("load", capture_name, result=capture)
                cell = builder.temp()
                builder.emit("cell_new", capture, result=cell)
                capture_ops.append(cell)
            result = builder.temp()
            has_vararg = 1 if self.function_varargs.get(lifted_name) else 0
            builder.emit("closure_new", lifted_name, n_args, tuple(capture_ops), has_vararg,
                            1 if self.function_kwargs.get(lifted_name) else 0, result=result)
            return result
        if kind == HIRKind.CALL:
            # JSON_INTERNAL_V2: _json_native_loads(texto) es la operacion nativa usada por el stub json.loads
            # (valor de primera clase). Baja a json_loads con el tipo estatico del argumento.
            if node.func.kind == HIRKind.LOAD and node.func.name == "_json_native_loads":
                return self._json_loads_call(builder, node.args, node.keywords)
            # STDLIB_NATIVE_V1: `desde zlib importar crc32` baja a la operacion nativa.
            if node.func.kind == HIRKind.LOAD and node.func.name in builder.from_import_aliases:
                _na = builder.from_import_aliases[node.func.name]
                for _sep in (".", "__"):
                    if _sep in _na:
                        _nm, _nn = _na.split(_sep, 1)
                        if (_nm, _nn) in self._NATIVE_CALLS:
                            return self._lower_native_call(builder, _nm, _nn, node.args, node.keywords, node.starred_args)
            # HEAPQ_FROM_IMPORT_V1: `desde heapq importar heappush` baja a la operacion nativa.
            if node.func.kind == HIRKind.LOAD and node.func.name in builder.from_import_aliases:
                _hq = builder.from_import_aliases[node.func.name]
                _hq = _hq.replace("heapq__", "").replace("heapq.", "") if _hq.startswith(("heapq.", "heapq__")) else None
                if _hq in self._HEAPQ_ARITY:
                    return self._lower_heapq_call(builder, _hq, node.args, node.keywords, node.starred_args)
            # JSON_INTERNAL_V3: _json_native_load_file(fp) lo usa el stub json.load (valor de primera clase).
            if node.func.kind == HIRKind.LOAD and node.func.name == "_json_native_load_file":
                _lf = self._lower_expr(builder, node.args[0])
                _lres = builder.temp()
                builder.emit("json_load_file", _lf, _active_handler(builder), result=_lres)
                return _lres
            # JSON_INTERNAL_V2: _json_native_loads(texto) lo usa el stub json.loads (valor de primera clase).
            if node.func.kind == HIRKind.LOAD and node.func.name == "_json_native_loads":
                return self._json_loads_call(builder, node.args, node.keywords)
            # JSON_SPECIALIZE_V1: `fn(x)` donde fn es un parametro especializado a json.X.
            if node.func.kind == HIRKind.LOAD and node.func.name in getattr(builder, "json_param_alias", {}):
                return self._lower_json_call(builder, builder.json_param_alias[node.func.name], node.args, node.keywords, node.starred_args)
            # JSON_SPECIALIZE_V1: f(..., json.dumps, ...) con f funcion de usuario -> copia especializada.
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in getattr(self, "_func_def_nodes", {})
                and not node.keywords and not node.starred_args
            ):
                _jhits = [
                    (_i, _a) for _i, _arg in enumerate(node.args)
                    for _a in [self._json_arg_attr(builder, _arg)] if _a in {"dumps", "dump"}
                ]
                if _jhits:
                    return self._lower_json_specialized_call(builder, node, _jhits)
            # JSON_VALUE_ALIAS_V1: `f = json.loads; f(x)` -> json.loads(x) (nombre ligado una vez).
            if node.func.kind == HIRKind.LOAD and node.func.name in getattr(self, "_json_value_alias", {}):
                builder.emit("json_alias_check", node.func.name, _active_handler(builder))
                return self._lower_json_call(builder, self._json_value_alias[node.func.name], node.args, node.keywords, node.starred_args)
            # JSON_FROM_IMPORT_V1: `desde json importar loads/dumps/dump/load`
            # se baja al mismo lowering nativo que `json.X(...)`.
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in builder.from_import_aliases
                and builder.from_import_aliases[node.func.name] in {"json.dump", "json.load", "json.loads", "json.dumps", "json__dump", "json__load", "json__loads", "json__dumps"}
            ):
                return self._lower_json_call(
                    builder, builder.from_import_aliases[node.func.name].replace("json__", "").replace("json.", ""),
                    node.args, node.keywords, node.starred_args,
                )
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
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in {"dict", "diccionario"}
                and len(node.args) == 0
                and node.keywords
                and all(kw.arg is not None for kw in node.keywords)
            ):
                from piton.hir import Const as _HIRConst
                items = tuple(
                    (self._lower_expr(builder, _HIRConst(kind=HIRKind.CONST, value=kw.arg)),
                     self._lower_expr(builder, kw.value))
                    for kw in node.keywords
                )
                result = builder.temp()
                builder.emit("build_collection", "dict", items, result=result)
                return result
            if node.func.kind == HIRKind.ATTR:
                decorated_target = self._decorated_method_target(
                    builder, node.func.value, node.func.attr,
                )
                if decorated_target is not None:
                    return self._lower_decorated_method_call(
                        builder, node, decorated_target[0], decorated_target[1],
                    )
            if self._call_has_dynamic_unpack(node):
                return self._lower_call_unpack(builder, node)
            node.args, node.keywords = self._expand_literal_call(node)
            if node.func.kind == HIRKind.LOAD and node.func.name == "super":
                # Relajar super(): fallback a llamada dinámica
                return self._lower_dynamic_call(builder, node)
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in {"__piton_mkdtemp", "__piton_stat_kind", "__piton_glob"}
            ):
                return self._lower_builtin_fs_op(builder, node)
            if node.func.kind == HIRKind.LOAD and node.func.name in {"iter", "iterar"}:
                if len(node.args) != 1 or node.keywords:
                    raise MIRLoweringError("native iter requires one positional argument")
                value = self._lower_expr(builder, node.args[0])
                result = builder.temp()
                builder.emit("iter_new", value, _active_handler(builder), result=result)
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
                if attr_name in {"throw", "arrojar"}:
                    owner = self._lower_expr(builder, node.func.value)
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
                    owner = self._lower_expr(builder, node.func.value)
                    # GEN_CLOSE_V1: `gen.close()` — marcar el generador como
                    # terminado. Algunos call sites (ej. manejo de
                    # StopIteration en next(it)) pueden pasar argumentos
                    # implícitos; se ignoran (no-op para generadores no
                    # nativos del subset).
                    result = builder.temp()
                    builder.emit("gen_close", owner, result=result)
                    return result
                if attr_name in {"send", "enviar"}:
                    owner = self._lower_expr(builder, node.func.value)
                    if len(node.args) != 1 or node.keywords:
                        raise MIRLoweringError("native generator send requires one argument")
                    value = self._lower_expr(builder, node.args[0])
                    result = builder.temp()
                    builder.emit("gen_send", owner, value, self._find_exception_handler(builder, "TypeError"), result=result)
                    return result
            if node.func.kind == HIRKind.LOAD and node.func.name in {"enumerate", "enumerar"}:
                start = None
                if len(node.args) == 2:
                    start = self._lower_expr(builder, node.args[1])
                elif node.keywords:
                    for kw in node.keywords:
                        if kw.arg in {"start", "inicio"}:
                            start = self._lower_expr(builder, kw.value)
                        else:
                            raise MIRLoweringError(f"native enumerate does not support keyword argument '{kw.arg}'")
                elif len(node.args) != 1:
                    raise MIRLoweringError("native enumerate requires one iterable and optional start")
                source = self._lower_expr(builder, node.args[0])
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
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in {"zip", "combinar"}
                and not self._name_shadows_builtin(node.func.name, builder)
            ):
                strict = None
                seen_strict = False
                for kw in node.keywords or ():
                    if kw.arg not in {"strict", "estricto"}:
                        raise MIRLoweringError(f"native zip does not support keyword argument '{kw.arg}'")
                    if seen_strict:
                        raise MIRLoweringError("native zip got multiple values for keyword argument 'strict'")
                    seen_strict = True
                    strict = self._lower_expr(builder, kw.value)
                if len(node.args) == 0:
                    result = builder.temp()
                    builder.emit("builtin_iter_new", "zip", (), strict, result=result)
                    return result
                sources = tuple(self._lower_expr(builder, arg) for arg in node.args)
                result = builder.temp()
                builder.emit("builtin_iter_new", "zip", sources, strict, result=result)
                return result
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in {"map", "filtrar", "filter"}
                and node.func.name not in builder.closures
                and node.func.name not in builder.function.params
                and node.func.name not in builder.cell_params
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
                # COROUTINE_OBJECT_V1: an async call creates a real
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
                if qualified in {"json.dump", "json.load", "json.loads", "json.dumps"}:
                    return self._lower_json_call(builder, qualified[5:], node.args, node.keywords, node.starred_args)
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
                if qualified == "math.isclose":
                    return self._lower_math_isclose(builder, node)
                if qualified == "os.makedirs":
                    return self._lower_os_makedirs(builder, node)
                _MATH_FUNCS = {
                    "math.sqrt": "math_sqrt", "math.floor": "math_floor",
                    "math.ceil": "math_ceil", "math.trunc": "math_trunc",
                    "math.fabs": "math_fabs", "math.gcd": "math_gcd",
                    "math.sin": "math_sin", "math.cos": "math_cos",
                    "math.log": "math_log",
                    "math.isinf": "math_isinf", "math.isnan": "math_isnan",
                    "math.isfinite": "math_isfinite",
                    "math.copysign": "math_copysign",
                    "math.modf": "math_modf", "math.frexp": "math_frexp",
                    "math.isqrt": "math_isqrt", "math.ldexp": "math_ldexp",
                    "math.radians": "math_radians", "math.degrees": "math_degrees",
                    "math.atan2": "math_atan2",
                }
                if qualified in _MATH_FUNCS:
                    op = _MATH_FUNCS[qualified]
                    arity = 2 if op in {"math_gcd", "math_copysign", "math_ldexp", "math_atan2", "math_comb"} else 1
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
                    if op in {"math_gcd", "math_copysign", "math_ldexp", "math_atan2", "math_comb"} or op == "math_perm":
                        left = self._lower_expr(builder, node.args[0])
                        right = self._lower_expr(builder, node.args[1]) if len(node.args) > 1 else left
                        builder.emit(op, left, right, _active_handler(builder), result=result)
                    else:
                        value = self._lower_expr(builder, node.args[0])
                        handler = None if op in {"math_sqrt", "math_isfinite", "math_modf", "math_frexp", "math_isqrt"} else _active_handler(builder)
                        if handler is None:
                            builder.emit(op, value, result=result)
                        else:
                            builder.emit(op, value, handler, result=result)
                    return result
            _ctor_cls = builder.from_import_aliases.get(node.func.name, node.func.name) if node.func.kind == HIRKind.LOAD else None
            if (
                node.func.kind == HIRKind.LOAD
                and (_ctor_cls in self.classes or node.func.name in self.classes)
                and node.func.name not in getattr(self, "decorated_classes", set())
            ):
                # CLASS_CTOR_ATTR_V1: el cuerpo vive en _lower_class_construct
                # (misma semantica; la novedad es que un __init__ con kwargs
                # se empaqueta en el emisor en vez de fallar aqui).
                _target_cls = _ctor_cls if _ctor_cls in self.classes else node.func.name
                return self._lower_class_construct(builder, _target_cls, node)
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
                if (module_name, node.func.attr) in self._NATIVE_CALLS:
                    return self._lower_native_call(builder, module_name, node.func.attr, node.args, node.keywords, node.starred_args)
                if module_name == "heapq" and node.func.attr in self._HEAPQ_ARITY:
                    return self._lower_heapq_call(builder, node.func.attr, node.args, node.keywords, node.starred_args)
                if module_name == "json" and node.func.attr in {"dump", "load", "loads", "dumps"}:
                    # JSON_NATIVE_DISPATCH_V1: lowering unico (ver _lower_json_call).
                    return self._lower_json_call(builder, node.func.attr, node.args, node.keywords, node.starred_args)
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
                    symbol = self._mod_func_symbol(module_name, node.func.attr)
                    if symbol in self.function_params or symbol in self.function_varargs or symbol in self.function_kwargs:
                        args = self._lower_variadic_call(builder, symbol, node.args, node.keywords)
                        result = builder.temp()
                        builder.emit("call", symbol, args, _active_handler(builder), result=result)
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
                    if attr == "isclose":
                        return self._lower_math_isclose(builder, node)
                    _MATH_ATTRS = {
                        "sqrt": "math_sqrt", "floor": "math_floor",
                        "ceil": "math_ceil", "trunc": "math_trunc",
                        "fabs": "math_fabs", "gcd": "math_gcd",
                        "sin": "math_sin", "cos": "math_cos",
                        "log": "math_log",
                        "isinf": "math_isinf", "isnan": "math_isnan",
                        "isfinite": "math_isfinite",
                        "copysign": "math_copysign",
                        "modf": "math_modf", "frexp": "math_frexp",
                        "isqrt": "math_isqrt", "ldexp": "math_ldexp",
                        "radians": "math_radians", "degrees": "math_degrees",
                        "atan2": "math_atan2",
                        "factorial": "math_factorial", "comb": "math_comb",
                        "perm": "math_perm",
                    }
                    op = _MATH_ATTRS.get(attr)
                    arity = 2 if op in {"math_gcd", "math_copysign", "math_ldexp", "math_atan2", "math_comb"} else 1
                    if op is None or (len(node.args) != arity and not (op == "math_perm" and len(node.args) in (1, 2))) or node.keywords:
                        raise MIRLoweringError(
                            f"native math.{attr} is not supported or requires {arity} positional argument"
                            f"{'s' if arity != 1 else ''}"
                        )
                    result = builder.temp()
                    if op in {"math_gcd", "math_copysign", "math_ldexp", "math_atan2", "math_comb"} or op == "math_perm":
                        left = self._lower_expr(builder, node.args[0])
                        right = self._lower_expr(builder, node.args[1]) if len(node.args) > 1 else left
                        builder.emit(op, left, right, _active_handler(builder), result=result)
                    else:
                        value = self._lower_expr(builder, node.args[0])
                        handler = None if op in {"math_sqrt", "math_isfinite", "math_modf", "math_frexp", "math_isqrt"} else _active_handler(builder)
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
                if _symbol == "os__makedirs":
                    return self._lower_os_makedirs(builder, node)
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
                elif node.keywords and _symbol in self.function_params:
                    args = self._resolve_call_args(builder, _symbol, node.args, node.keywords)
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
                if _has_star or (_has_kwstar and node.func.attr != "format"):
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
                        _msymbol = self._mod_func_symbol(module_name, node.func.attr)
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
                                    name_prefix=self._mod_prefix(module_name),
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
                            builder.emit("call", _msymbol, args, _active_handler(builder), result=result)
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
                    if node.func.attr != "format":
                        raise MIRLoweringError("native method calls do not support **kwargs unpacking yet")
                kw = tuple(
                    (k.arg, self._lower_expr(builder, k.value)) for k in (node.keywords or ())
                )
                result = builder.temp()
                if kw:
                    builder.emit("method_call", None, node.func.attr, owner, args, kw, _active_handler(builder), result=result)
                else:
                    builder.emit("method_call", None, node.func.attr, owner, args, (), _active_handler(builder), result=result)
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
                    _has_vararg = 1 if self.function_varargs.get(_lifted) else 0
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
                _kw_alias = builder.from_import_aliases.get(node.func.name)
                if (
                    _kw_alias is not None
                    and node.func.name not in self.function_params
                    and _kw_alias in self.function_params
                    and self.function_varargs.get(_kw_alias) is None
                    and self.function_kwargs.get(_kw_alias) is None
                    and all(_k.arg is not None for _k in node.keywords)
                ):
                    # FROM_IMPORT_KW_V1: `from m import f; f(k=v)` liga los
                    # keywords a los params de la funcion bundled conocida
                    # (antes caia al call dinamico, que ignora kwargs en
                    # funciones code-address: lru_cache(maxsize=None) -> 0).
                    args = self._resolve_call_args(builder, _kw_alias, node.args, node.keywords)
                    function = builder.temp()
                    builder.emit("load", _kw_alias, result=function)
                    result = builder.temp()
                    builder.emit("call", function, args, _active_handler(builder), result=result)
                    return result
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
                    return self._lower_dynamic_call(builder, node)
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
            builder.emit("set_attr", owner, target.attr, value, _active_handler(builder))
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
                builder.emit("slice_store", obj, lo, hi, step, value, _active_handler(builder))
                return
            idx = self._lower_expr(builder, target.slice)
            builder.emit("subscript_store", obj, idx, value, _active_handler(builder))
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
        current_index = mro.index(current_class)
        tail_mro = mro[current_index + 1:]
        unresolved_bases = [
            cls for cls in tail_mro
            if cls not in self.classes
            and cls not in _BUILTIN_BASES
            and cls not in _BUILTIN_EXCEPTIONS
        ]
        if unresolved_bases:
            raise MIRLoweringError(
                "native super() cannot resolve dynamically evaluated base "
                f"'{unresolved_bases[0]}'"
            )
        if next_class is None:
            # SUPER_DUNDER_FALLBACK_V1: `super().__setattr__(n, v)` (y hermanos)
            # no los define ninguna clase: es la operación nativa de objeto.
            # Antes fallaba cerrado con MIRLoweringError aunque CPython lo
            # resuelve contra object.__setattr__.
            if attr in {"__setattr__", "__getattr__", "__delattr__", "__setitem__", "__getitem__", "__delitem__"} and builder.super_self is not None:
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
                if attr == "__delattr__":
                    if len(args) != 1:
                        raise MIRLoweringError("native super().__delattr__ requires exactly one argument")
                    builder.emit("del_attr", receiver, args[0])
                    return None
                if attr == "__setitem__":
                    if len(args) != 2:
                        raise MIRLoweringError("native super().__setitem__ requires exactly two arguments")
                    builder.emit("subscript_store", receiver, args[0], args[1], _active_handler(builder))
                    return None
                if attr == "__getitem__":
                    if len(args) != 1:
                        raise MIRLoweringError("native super().__getitem__ requires exactly one argument")
                    got = builder.temp()
                    builder.emit("get_item", receiver, args[0], result=got)
                    return got
                if attr == "__delitem__":
                    if len(args) != 1:
                        raise MIRLoweringError("native super().__delitem__ requires exactly one argument")
                    builder.emit("del_item", receiver, args[0])
                    return None
                if attr == "__iter__":
                    got = builder.temp()
                    builder.emit("builtin_iter_new", "iter", receiver, None, result=got)
                    return got
            if (
                attr == "__init__"
                and builder.super_self is not None
                and any(
                    cls in _BUILTIN_EXCEPTIONS
                    for cls in tail_mro
                )
            ):
                # Builtin BaseException.__init__ stores its positional args.
                # Preserve that behavior for ``super().__init__(...)` in a
                # user-defined exception subclass.
                if builder.super_self in builder.cell_params:
                    receiver = builder.temp()
                    builder.emit("cell_load", builder.super_self, result=receiver)
                else:
                    receiver = builder.temp()
                    builder.emit("load", builder.super_self, result=receiver)
                args = tuple(self._lower_expr(builder, arg) for arg in user_args)
                args_tuple = builder.temp()
                builder.emit("build_collection", "tuple", args, result=args_tuple)
                builder.emit("set_attr", receiver, "args", args_tuple, _active_handler(builder))
                none_value = builder.temp()
                builder.emit("const", None, result=none_value)
                return none_value
            # SUPER_BUILTIN_BASE_V1: la base siguiente en el MRO es una clase
            # BUILTIN o la raiz `object`, que no aporta un __init__ de usuario.
            # En CPython la llamada resuelve contra object/type.__init__
            # o __init_subclass__ -> la llamada es un no-op, no un error de build.
            if attr in {"__init__", "__init_subclass__"}:
                if user_args:
                    if (
                        attr == "__init__"
                        and "classmethod" in tail_mro
                        and len(user_args) == 2
                    ):
                        # classmethod.__init__ accepts one positional argument
                        # after its bound receiver. Keep the exception at call
                        # time so an unused method body remains compilable, and
                        # preserve CPython's message if that path is executed.
                        _message = builder.temp()
                        builder.emit(
                            "const", "classmethod expected 1 argument, got 2",
                            result=_message,
                        )
                        builder.emit(
                            "raise_typed", "TypeError", _message,
                            _active_handler(builder),
                        )
                        _none = builder.temp()
                        builder.emit("const", None, result=_none)
                        return _none
                    raise MIRLoweringError(
                        f"native super().{attr} with arguments requires a modeled base implementation"
                    )
                for arg in user_args:
                    self._lower_expr(builder, arg)
                _none = builder.temp()
                builder.emit("const", None, result=_none)
                return _none
            if (
                attr == "__call__"
                and builder.super_self is not None
                and self.class_parents.get(current_class) == "type"
            ):
                _receiver = builder.temp()
                builder.emit("load", builder.super_self, result=_receiver)
                _args = tuple(self._lower_expr(builder, arg) for arg in user_args)
                _called = builder.temp()
                builder.emit(
                    "type_call", _receiver, _args, _active_handler(builder),
                    result=_called,
                )
                return _called
            if attr == "__new__":
                if self.class_parents.get(current_class) == "type" and len(user_args) == 4:
                    _args = tuple(self._lower_expr(builder, arg) for arg in user_args)
                    _created = builder.temp()
                    builder.emit(
                        "type_new", _args, _active_handler(builder),
                        result=_created,
                    )
                    return _created
                if user_args:
                    raise MIRLoweringError(
                        "native super().__new__ with arguments requires a modeled base implementation"
                    )
                for arg in user_args:
                    self._lower_expr(builder, arg)
                parent_cls = self.class_parents.get(current_class)
                result = builder.temp()
                builder.emit("object_new", current_class, parent_cls, result=result)
                return result

            builtin_base = next(
                (cls for cls in tail_mro if cls in _BUILTIN_BASES | _BUILTIN_EXCEPTIONS),
                None,
            )
            if builtin_base is not None:
                raise MIRLoweringError(
                    f"native super().{attr} for builtin base '{builtin_base}' "
                    "has no modeled native implementation"
                )
            if tail_mro:
                raise MIRLoweringError(
                    f"native super().{attr} has no base class in MRO of '{current_class}' "
                    "that defines the attribute"
                )
            raise MIRLoweringError(
                f"native super().{attr} has no base class in MRO of '{current_class}'"
            )
        if builder.super_self in builder.cell_params:
            receiver = builder.temp()
            builder.emit("cell_load", builder.super_self, result=receiver)
        else:
            receiver = builder.temp()
            builder.emit("load", builder.super_self, result=receiver)
        args = tuple(self._lower_expr(builder, arg) for arg in user_args)
        # Relax: allow more than 3 args for super calls
        # if len(args) > 3:
        #     raise MIRLoweringError("native super() method calls support at most 3 user arguments")
        result = builder.temp()
        # Keep the defining class as the lexical anchor. The native backends
        # use the receiver's runtime class to continue through its C3 MRO.
        dispatch_class = (
            (_ZERO_ARG_SUPER, current_class, next_class)
            if next_class is not None
            else next_class
        )
        builder.emit("method_call", dispatch_class, attr, receiver, args, result=result)
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
            builder.emit("gen_yield", val, _active_handler(builder), result=result)
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
            _binding_name = self._module_function_binding(builder, target_name)
            if _binding_name != target_name:
                builder.emit("store", _binding_name, value, "internal")
            else:
                builder.emit("store", target_name, value)
            return value
        if kind == HIRKind.LOAD and node.name in getattr(builder, "json_param_alias", {}):
            raise MIRLoweringError(f"json.{builder.json_param_alias[node.name]} usado como valor dentro de una funcion especializada: solo se admite llamarlo (fail closed en compilacion)")
        if kind == HIRKind.LOAD and node.name in getattr(self, "_json_value_alias", {}):
            # JSON_VALUE_FIRST_CLASS_V1: loads como valor = la funcion json__loads (stub Piton
            # que llama a la operacion nativa). El resto (dumps/dump/load) falla en compilacion.
            _jattr = self._json_value_alias[node.name]
            if _jattr not in {"loads", "load"}:
                raise MIRLoweringError(f"json.{_jattr} como valor de primera clase no soportado: necesita el tipo estatico del argumento (fail closed en compilacion)")
            # JSON_FIRST_CLASS_V1: loads como valor = la funcion json__loads; el programa pasa a
            # tratar los resultados de llamadas por valor de funcion como valores dinamicos.
            _jres = builder.temp()
            builder.emit("json_alias_check", node.name, _active_handler(builder))
            builder.emit("json_value_used")
            builder.emit("load", f"json__{_jattr}", _active_handler(builder), result=_jres)
            return _jres
        if kind == HIRKind.LOAD:
            _lexical_local = (
                node.name in builder.local_names
                or node.name in builder.nonlocal_names
            )
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
                if node.name in builder.name_classes:
                    builder.value_classes[result] = builder.name_classes[node.name]
                return result
            # M8: aliases de módulo creados por `desde .. importar X` dentro
            # de un paquete son marcadores estáticos. Si el nombre del alias
            # resuelve a un módulo importado, emitimos un placeholder (None) en
            # vez de un load que no existe en el backend.
            alias_target = None if _lexical_local else builder.module_aliases.get(node.name)
            if alias_target and alias_target != node.name and alias_target in self.imported_modules:
                result = builder.temp()
                builder.emit("const", None, result=result)
                return result
            # OS_TIER1_V1 / SYS_ARGV_V1: attributes reached through a
            # `desde ... importar ...` alias are values, not backend symbols.
            alias_qual = None if _lexical_local else builder.from_import_aliases.get(node.name)
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
            if alias_qual in {"json.dump", "json.dumps", "json__dump", "json__dumps", "json.load", "json__load", "json.loads", "json__loads"}:
                # JSON_VALUE_FAIL_CLOSED_V1: dumps como VALOR necesita el tipo estatico
                # del argumento; sin el, la operacion nativa seria incorrecta.
                raise MIRLoweringError(f"json.{alias_qual.split('.')[-1].split('__')[-1]} como valor no soportado (fail closed en compilacion)")
            if alias_qual is not None:
                # NATIVE_MODULE_CONSTS_V1: constante de un modulo nativo.
                _found, _value = self._native_module_const(alias_qual)
                if _found:
                    result = builder.temp()
                    builder.emit("const", _value, result=result)
                    return result
            if alias_qual is not None and alias_qual.startswith("os."):
                # Relajar os.*: fallback a nombre cualificado
                result = builder.temp()
                builder.emit("load", f"os__{alias_qual[3:]}", _active_handler(builder), result=result)
                return result
            result = builder.temp()
            _binding_name = self._module_function_binding(builder, node.name)
            if node.name in builder.module_function_bindings and not _lexical_local:
                _load_name = _binding_name
            else:
                _load_name = builder.from_import_aliases.get(node.name, _binding_name)
            if _lexical_local:
                _load_name = node.name
            builder.emit("load", _load_name, _active_handler(builder), result=result)
            if node.name in builder.name_classes:
                builder.value_classes[result] = builder.name_classes[node.name]
            return result
        if kind == HIRKind.STORE:
            result = builder.temp()
            _state_name = (getattr(builder, "module_state_vars", None) or {}).get(node.name)
            if _state_name:
                builder.emit("load", _state_name, _active_handler(builder), result=result)
            elif node.name in builder.cell_params:
                builder.emit("cell_load", node.name, result=result)
            else:
                builder.emit(
                    "load", self._module_function_binding(builder, node.name),
                    _active_handler(builder), result=result,
                )
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
            # CHAINCMP_V1: CPython evaluates `a < b < c` left-to-right,
            # evaluating each intermediate comparator ONCE and short-circuiting
            # before evaluating later comparators if an earlier pair is False.
            holder = "@" + builder.temp().lstrip("%")
            end_block = builder.new_block()
            false_block = builder.new_block()
            prev_val = self._lower_expr(builder, node.left)
            for op, comparator in zip(node.ops[:-1], node.comparators[:-1]):
                next_val = self._lower_expr(builder, comparator)
                cmp_tmp = self._compare(builder, op, prev_val, next_val)
                cont_block = builder.new_block()
                builder.emit("branch", cmp_tmp, cont_block.label, false_block.label)
                builder.current = cont_block
                prev_val = next_val
            last_right = self._lower_expr(builder, node.comparators[-1])
            last = self._compare(builder, node.ops[-1], prev_val, last_right)
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
                # Relax: lambda not lifted -> emit null stub
                result = builder.temp()
                builder.emit("const", "None", result=result)
                return result
            lifted_name, _, n_args = builder.closures[node.name]
            capture_ops: list[Any] = []
            for capture_name in builder.closures[node.name][1]:
                capture = builder.temp()
                builder.emit("load", capture_name, result=capture)
                cell = builder.temp()
                builder.emit("cell_new", capture, result=cell)
                capture_ops.append(cell)
            result = builder.temp()
            has_vararg = 1 if self.function_varargs.get(lifted_name) else 0
            builder.emit("closure_new", lifted_name, n_args, tuple(capture_ops), has_vararg,
                            1 if self.function_kwargs.get(lifted_name) else 0, result=result)
            return result
        if kind == HIRKind.CALL:
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in self._module_function_names
                and (
                    node.func.name in builder.local_names
                    or node.func.name in builder.nonlocal_names
                    or node.func.name in builder.module_function_bindings
                )
            ):
                # LOCAL_SHADOWS_FUNCTION_V1: direct calls are valid only when
                # LEGB resolves the name to its unshadowed module function.
                return self._lower_dynamic_call(builder, node)
            # JSON_INTERNAL_V2: _json_native_loads(texto) es la operacion nativa usada por el stub json.loads
            # (valor de primera clase). Baja a json_loads con el tipo estatico del argumento.
            if node.func.kind == HIRKind.LOAD and node.func.name == "_json_native_loads":
                return self._json_loads_call(builder, node.args, node.keywords)
            # STDLIB_NATIVE_V1: `desde zlib importar crc32` baja a la operacion nativa.
            if node.func.kind == HIRKind.LOAD and node.func.name in builder.from_import_aliases:
                _na = builder.from_import_aliases[node.func.name]
                for _sep in (".", "__"):
                    if _sep in _na:
                        _nm, _nn = _na.split(_sep, 1)
                        if (_nm, _nn) in self._NATIVE_CALLS:
                            return self._lower_native_call(builder, _nm, _nn, node.args, node.keywords, node.starred_args)
            # HEAPQ_FROM_IMPORT_V1: `desde heapq importar heappush` baja a la operacion nativa.
            if node.func.kind == HIRKind.LOAD and node.func.name in builder.from_import_aliases:
                _hq = builder.from_import_aliases[node.func.name]
                _hq = _hq.replace("heapq__", "").replace("heapq.", "") if _hq.startswith(("heapq.", "heapq__")) else None
                if _hq in self._HEAPQ_ARITY:
                    return self._lower_heapq_call(builder, _hq, node.args, node.keywords, node.starred_args)
            # JSON_INTERNAL_V3: _json_native_load_file(fp) lo usa el stub json.load (valor de primera clase).
            if node.func.kind == HIRKind.LOAD and node.func.name == "_json_native_load_file":
                _lf = self._lower_expr(builder, node.args[0])
                _lres = builder.temp()
                builder.emit("json_load_file", _lf, _active_handler(builder), result=_lres)
                return _lres
            # JSON_INTERNAL_V2: _json_native_loads(texto) lo usa el stub json.loads (valor de primera clase).
            if node.func.kind == HIRKind.LOAD and node.func.name == "_json_native_loads":
                return self._json_loads_call(builder, node.args, node.keywords)
            # JSON_SPECIALIZE_V1: `fn(x)` donde fn es un parametro especializado a json.X.
            if node.func.kind == HIRKind.LOAD and node.func.name in getattr(builder, "json_param_alias", {}):
                return self._lower_json_call(builder, builder.json_param_alias[node.func.name], node.args, node.keywords, node.starred_args)
            # JSON_SPECIALIZE_V1: f(..., json.dumps, ...) con f funcion de usuario -> copia especializada.
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in getattr(self, "_func_def_nodes", {})
                and not node.keywords and not node.starred_args
            ):
                _jhits = [
                    (_i, _a) for _i, _arg in enumerate(node.args)
                    for _a in [self._json_arg_attr(builder, _arg)] if _a in {"dumps", "dump"}
                ]
                if _jhits:
                    return self._lower_json_specialized_call(builder, node, _jhits)
            # JSON_VALUE_ALIAS_V1: `f = json.loads; f(x)` -> json.loads(x) (nombre ligado una vez).
            if node.func.kind == HIRKind.LOAD and node.func.name in getattr(self, "_json_value_alias", {}):
                builder.emit("json_alias_check", node.func.name, _active_handler(builder))
                return self._lower_json_call(builder, self._json_value_alias[node.func.name], node.args, node.keywords, node.starred_args)
            # JSON_FROM_IMPORT_V1: `desde json importar loads/dumps/dump/load`
            # se baja al mismo lowering nativo que `json.X(...)`.
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in builder.from_import_aliases
                and builder.from_import_aliases[node.func.name] in {"json.dump", "json.load", "json.loads", "json.dumps", "json__dump", "json__load", "json__loads", "json__dumps"}
            ):
                return self._lower_json_call(
                    builder, builder.from_import_aliases[node.func.name].replace("json__", "").replace("json.", ""),
                    node.args, node.keywords, node.starred_args,
                )
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in {"any", "all"}
                and not self._name_shadows_builtin(node.func.name, builder)
                and len(node.args) == 1
                and not node.keywords
                and node.args[0].kind == HIRKind.GEN_EXPR
            ):
                return self._lower_any_all_genexpr(
                    builder, node.args[0], want_any=node.func.name == "any"
                )
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
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in {"dict", "diccionario"}
                and len(node.args) == 0
                and node.keywords
                and all(kw.arg is not None for kw in node.keywords)
            ):
                from piton.hir import Const as _HIRConst
                items = tuple(
                    (self._lower_expr(builder, _HIRConst(kind=HIRKind.CONST, value=kw.arg)),
                     self._lower_expr(builder, kw.value))
                    for kw in node.keywords
                )
                result = builder.temp()
                builder.emit("build_collection", "dict", items, result=result)
                return result
            if node.func.kind == HIRKind.ATTR:
                decorated_target = self._decorated_method_target(
                    builder, node.func.value, node.func.attr,
                )
                if decorated_target is not None:
                    return self._lower_decorated_method_call(
                        builder, node, decorated_target[0], decorated_target[1],
                    )
            if self._call_has_dynamic_unpack(node):
                return self._lower_call_unpack(builder, node)
            node.args, node.keywords = self._expand_literal_call(node)
            if node.func.kind == HIRKind.LOAD and node.func.name == "super":
                # Relajar super(): fallback a llamada dinámica
                return self._lower_dynamic_call(builder, node)
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in {"__piton_mkdtemp", "__piton_stat_kind", "__piton_glob"}
            ):
                return self._lower_builtin_fs_op(builder, node)
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
                builder.emit("iter_new", value, _active_handler(builder), result=result)
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
                builder.emit("iter_next", iterator, handler.label, True, result=result)
                builder.emit("try_pop")
                builder.emit("jump", end.label)
                builder.current = handler
                builder.emit("catch_clear")
                builder.emit("try_pop")
                boxed_default = builder.temp()
                builder.emit("slot_box", default_val, result=boxed_default)
                builder.emit("store", result, boxed_default)
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
                if attr_name in {"throw", "arrojar"}:
                    owner = self._lower_expr(builder, node.func.value)
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
                    owner = self._lower_expr(builder, node.func.value)
                    # GEN_CLOSE_V1: `gen.close()` — marcar el generador como
                    # terminado. Algunos call sites (ej. manejo de
                    # StopIteration en next(it)) pueden pasar argumentos
                    # implícitos; se ignoran (no-op para generadores no
                    # nativos del subset).
                    result = builder.temp()
                    builder.emit("gen_close", owner, result=result)
                    return result
                if attr_name in {"send", "enviar"}:
                    owner = self._lower_expr(builder, node.func.value)
                    if len(node.args) != 1 or node.keywords:
                        raise MIRLoweringError("native generator send requires one argument")
                    value = self._lower_expr(builder, node.args[0])
                    result = builder.temp()
                    builder.emit("gen_send", owner, value, self._find_exception_handler(builder, "TypeError"), result=result)
                    return result
            if node.func.kind == HIRKind.LOAD and node.func.name in {"enumerate", "enumerar"}:
                start = None
                if len(node.args) == 2:
                    start = self._lower_expr(builder, node.args[1])
                elif node.keywords:
                    for kw in node.keywords:
                        if kw.arg in {"start", "inicio"}:
                            start = self._lower_expr(builder, kw.value)
                        else:
                            raise MIRLoweringError(f"native enumerate does not support keyword argument '{kw.arg}'")
                elif len(node.args) != 1:
                    raise MIRLoweringError("native enumerate requires one iterable and optional start")
                source = self._lower_expr(builder, node.args[0])
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
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in {"zip", "combinar"}
                and not self._name_shadows_builtin(node.func.name, builder)
            ):
                strict = None
                seen_strict = False
                for kw in node.keywords or ():
                    if kw.arg not in {"strict", "estricto"}:
                        raise MIRLoweringError(f"native zip does not support keyword argument '{kw.arg}'")
                    if seen_strict:
                        raise MIRLoweringError("native zip got multiple values for keyword argument 'strict'")
                    seen_strict = True
                    strict = self._lower_expr(builder, kw.value)
                if len(node.args) == 0:
                    result = builder.temp()
                    builder.emit("builtin_iter_new", "zip", (), strict, result=result)
                    return result
                sources = tuple(self._lower_expr(builder, arg) for arg in node.args)
                result = builder.temp()
                builder.emit("builtin_iter_new", "zip", sources, strict, result=result)
                return result
            if (
                node.func.kind == HIRKind.LOAD
                and node.func.name in {"map", "filtrar", "filter"}
                and node.func.name not in builder.closures
                and node.func.name not in builder.function.params
                and node.func.name not in builder.cell_params
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
                # COROUTINE_OBJECT_V1: an async call creates a real
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
                if qualified in {"json.dump", "json.load", "json.loads", "json.dumps"}:
                    return self._lower_json_call(builder, qualified[5:], node.args, node.keywords, node.starred_args)
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
                if qualified == "math.isclose":
                    return self._lower_math_isclose(builder, node)
                if qualified == "os.makedirs":
                    return self._lower_os_makedirs(builder, node)
                _MATH_FUNCS = {
                    "math.sqrt": "math_sqrt", "math.floor": "math_floor",
                    "math.ceil": "math_ceil", "math.trunc": "math_trunc",
                    "math.fabs": "math_fabs", "math.gcd": "math_gcd",
                    "math.sin": "math_sin", "math.cos": "math_cos",
                    "math.log": "math_log",
                    "math.isinf": "math_isinf", "math.isnan": "math_isnan",
                    "math.isfinite": "math_isfinite",
                    "math.copysign": "math_copysign",
                    "math.modf": "math_modf", "math.frexp": "math_frexp",
                    "math.isqrt": "math_isqrt", "math.ldexp": "math_ldexp",
                    "math.radians": "math_radians", "math.degrees": "math_degrees",
                    "math.atan2": "math_atan2",
                }
                if qualified in _MATH_FUNCS:
                    op = _MATH_FUNCS[qualified]
                    arity = 2 if op in {"math_gcd", "math_copysign", "math_ldexp", "math_atan2", "math_comb"} else 1
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
                    if op in {"math_gcd", "math_copysign", "math_ldexp", "math_atan2", "math_comb"} or op == "math_perm":
                        left = self._lower_expr(builder, node.args[0])
                        right = self._lower_expr(builder, node.args[1]) if len(node.args) > 1 else left
                        builder.emit(op, left, right, _active_handler(builder), result=result)
                    else:
                        value = self._lower_expr(builder, node.args[0])
                        handler = None if op in {"math_sqrt", "math_isfinite", "math_modf", "math_frexp", "math_isqrt"} else _active_handler(builder)
                        if handler is None:
                            builder.emit(op, value, result=result)
                        else:
                            builder.emit(op, value, handler, result=result)
                    return result
            _ctor_cls = builder.from_import_aliases.get(node.func.name, node.func.name) if node.func.kind == HIRKind.LOAD else None
            if (
                node.func.kind == HIRKind.LOAD
                and (_ctor_cls in self.classes or node.func.name in self.classes)
                and node.func.name not in getattr(self, "decorated_classes", set())
            ):
                # CLASS_CTOR_ATTR_V1: el cuerpo vive en _lower_class_construct
                # (misma semantica; la novedad es que un __init__ con kwargs
                # se empaqueta en el emisor en vez de fallar aqui).
                _target_cls = _ctor_cls if _ctor_cls in self.classes else node.func.name
                return self._lower_class_construct(builder, _target_cls, node)
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
                if (module_name, node.func.attr) in self._NATIVE_CALLS:
                    return self._lower_native_call(builder, module_name, node.func.attr, node.args, node.keywords, node.starred_args)
                if module_name == "heapq" and node.func.attr in self._HEAPQ_ARITY:
                    return self._lower_heapq_call(builder, node.func.attr, node.args, node.keywords, node.starred_args)
                if module_name == "json" and node.func.attr in {"dump", "load", "loads", "dumps"}:
                    # JSON_NATIVE_DISPATCH_V1: lowering unico (ver _lower_json_call).
                    return self._lower_json_call(builder, node.func.attr, node.args, node.keywords, node.starred_args)
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
                    symbol = self._mod_func_symbol(module_name, node.func.attr)
                    if symbol in self.function_params or symbol in self.function_varargs or symbol in self.function_kwargs:
                        args = self._lower_variadic_call(builder, symbol, node.args, node.keywords)
                        result = builder.temp()
                        builder.emit("call", symbol, args, _active_handler(builder), result=result)
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
                    if attr == "isclose":
                        return self._lower_math_isclose(builder, node)
                    if node.keywords:
                        raise MIRLoweringError("native math does not support keyword arguments")
                    op = {
                        "sqrt": "math_sqrt", "floor": "math_floor", "ceil": "math_ceil",
                        "trunc": "math_trunc", "fabs": "math_fabs", "gcd": "math_gcd",
                        "sin": "math_sin", "cos": "math_cos",
                        "log": "math_log",
                        "isinf": "math_isinf", "isnan": "math_isnan",
                        "isfinite": "math_isfinite",
                        "copysign": "math_copysign",
                        "modf": "math_modf", "frexp": "math_frexp",
                        "isqrt": "math_isqrt", "ldexp": "math_ldexp",
                        "radians": "math_radians", "degrees": "math_degrees",
                        "atan2": "math_atan2",
                        "factorial": "math_factorial", "comb": "math_comb",
                        "perm": "math_perm",
                    }.get(attr)
                    arity = 2 if op in {"math_gcd", "math_copysign", "math_ldexp", "math_atan2", "math_comb"} else 1
                    if op == "math_log" and len(node.args) == 2:
                        # MATH_LOG2_V1: math.log(x, base) = log(x)/log(base).
                        _ll = self._lower_expr(builder, node.args[0])
                        _lr = self._lower_expr(builder, node.args[1])
                        result = builder.temp()
                        builder.emit("math_log2", _ll, _lr, _active_handler(builder), result=result)
                        return result
                    if op is None or (len(node.args) != arity and not (op == "math_perm" and len(node.args) in (1, 2))):
                        raise MIRLoweringError(
                            f"native math.{attr} is not supported or requires {arity} positional argument"
                            f"{'s' if arity != 1 else ''}"
                        )
                    result = builder.temp()
                    if op in {"math_gcd", "math_copysign", "math_ldexp", "math_atan2", "math_comb"} or op == "math_perm":
                        left = self._lower_expr(builder, node.args[0])
                        right = self._lower_expr(builder, node.args[1]) if len(node.args) > 1 else left
                        builder.emit(op, left, right, _active_handler(builder), result=result)
                    else:
                        value = self._lower_expr(builder, node.args[0])
                        handler = None if op in {"math_sqrt", "math_isfinite", "math_modf", "math_frexp", "math_isqrt"} else _active_handler(builder)
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
                if module_name == "os" and node.func.attr == "makedirs":
                    return self._lower_os_makedirs(builder, node)
                function = builder.temp()
                _symbol = f"{module_name.replace('.', '__')}__{node.func.attr}"
                builder.emit("load", _symbol, _active_handler(builder), result=function)
                # VARIADIC_MODULE_CALL_V1: una funcion de modulo con
                # *args/**kwargs baja por el camino variadico (el empaquetado
                # de la tupla del vararg lo hace el MIR).
                if (
                    self.function_varargs.get(_symbol) is not None
                    or self.function_kwargs.get(_symbol) is not None
                ):
                    args = self._lower_variadic_call(builder, _symbol, node.args, node.keywords)
                elif node.keywords and _symbol in self.function_params:
                    args = self._resolve_call_args(builder, _symbol, node.args, node.keywords)
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
                if _has_star or (_has_kwstar and node.func.attr != "format"):
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
                        _msymbol = self._mod_func_symbol(module_name, node.func.attr)
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
                                    name_prefix=self._mod_prefix(module_name),
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
                            builder.emit("call", _msymbol, args, _active_handler(builder), result=result)
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
                    if node.func.attr != "format":
                        raise MIRLoweringError("native method calls do not support **kwargs unpacking yet")
                kw = tuple(
                    (k.arg, self._lower_expr(builder, k.value)) for k in (node.keywords or ())
                )
                result = builder.temp()
                if kw:
                    builder.emit("method_call", None, node.func.attr, owner, args, kw, _active_handler(builder), result=result)
                else:
                    builder.emit("method_call", None, node.func.attr, owner, args, (), _active_handler(builder), result=result)
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
                    _has_vararg = 1 if self.function_varargs.get(_lifted) else 0
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
                _kw_alias = builder.from_import_aliases.get(node.func.name)
                if (
                    _kw_alias is not None
                    and node.func.name not in self.function_params
                    and _kw_alias in self.function_params
                    and self.function_varargs.get(_kw_alias) is None
                    and self.function_kwargs.get(_kw_alias) is None
                    and all(_k.arg is not None for _k in node.keywords)
                ):
                    # FROM_IMPORT_KW_V1: `from m import f; f(k=v)` liga los
                    # keywords a los params de la funcion bundled conocida
                    # (antes caia al call dinamico, que ignora kwargs en
                    # funciones code-address: lru_cache(maxsize=None) -> 0).
                    args = self._resolve_call_args(builder, _kw_alias, node.args, node.keywords)
                    function = builder.temp()
                    builder.emit("load", _kw_alias, result=function)
                    result = builder.temp()
                    builder.emit("call", function, args, _active_handler(builder), result=result)
                    return result
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
                    return self._lower_dynamic_call(builder, node)
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
            if any(key is None for key in node.keys):
                # DICT_MERGE_V2: evaluate keys and values in left-to-right
                # appearance order (`{**a, 'k': v, **b}`). Starred entries
                # (`key is None`) merge a mapping; regular entries set `k: v`,
                # overwriting any prior key while preserving first-seen key order.
                result = builder.temp()
                builder.emit("build_collection", "dict", (), result=result)
                for key, value in zip(node.keys, node.values):
                    if key is None:
                        v = self._lower_expr(builder, value)
                        builder.emit("collection_extend", result, v, _active_handler(builder))
                    else:
                        k = self._lower_expr(builder, key)
                        v = self._lower_expr(builder, value)
                        builder.emit("subscript_store", result, k, v, _active_handler(builder))
                return result
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
            # JSON_VALUE_FAIL_CLOSED_V1: json.X usado como valor (no llamado)
            # falla en compilacion: el valor de retorno de una funcion Piton no
            # conserva la etiqueta de un slot nativo.
            if (
                node.attr in {"dump", "dumps", "load", "loads"}
                and node.value.kind == HIRKind.LOAD
                and builder.module_aliases.get(node.value.name) == "json"
            ):
                if node.attr in {"loads", "load"}:
                    # JSON_FIRST_CLASS_V1: json.loads / json.load como valor = la funcion del stub.
                    _jres = builder.temp()
                    builder.emit("json_value_used")
                    builder.emit("load", f"json__{node.attr}", _active_handler(builder), result=_jres)
                    return _jres
                raise MIRLoweringError(f"json.{node.attr} como valor de primera clase no soportado: necesita el tipo estatico del argumento (fail closed en compilacion)")
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
                    # Relajar os.*: fallback a load dinámico
                    result = builder.temp()
                    builder.emit("load", f"os__{node.attr}", result=result)
                    return result
            _mchain = self._module_attr_chain(builder, node)
            if _mchain is not None:
                # MODULE_FUNC_VALUE_V1: `modulo.func` en posicion de VALOR
                # (copyreg.py: `return functools.reduce, (operator.or_, ...)`,
                # el protocolo reduce de pickle devuelve la funcion sin
                # invocarla). Si el simbolo cualificado es una FIRMA
                # registrada, la referencia baja como `load` — el mismo op
                # que `f = doble` — en vez del fail-closed generico.
                _mmod, _mattrs = _mchain
                _mmod = builder.module_aliases.get(_mmod, _mmod)
                # Normalizar submodulos conocidos para evitar fail-closed
                _submodule_alias = {
                    ('os', 'path'): 'os.path',
                    ('email', 'message'): 'email.message',
                    ('importlib', 'util'): 'importlib.util',
                    ('importlib', 'machinery'): 'importlib.machinery',
                }
                while len(_mattrs) >= 1:
                    key = (_mmod, _mattrs[0])
                    cand = f"{_mmod}.{_mattrs[0]}"
                    if key in _submodule_alias:
                        _mmod = _submodule_alias[key]
                        _mattrs = _mattrs[1:]
                        continue
                    if len(_mattrs) > 1 and (cand in builder.module_aliases or cand in self.imported_modules or _is_bundled_module(cand)):
                        _mmod = cand
                        _mattrs = _mattrs[1:]
                        continue
                    break
                if not _mattrs:
                    result = builder.temp()
                    builder.emit("load", _mmod.replace('.', '__'), result=result)
                    return result
                # Soporte minimo para __doc__ de atributos de modulo
                if _mattrs and _mattrs[-1] == '__doc__':
                    # Devuelve cadena vacia verificable
                    result = builder.temp()
                    builder.emit("const", "", result=result)
                    return result
                if len(_mattrs) == 1 and _mmod in _NATIVE_MODULE_CONSTS and _mattrs[0] in _NATIVE_MODULE_CONSTS[_mmod]:
                    result = builder.temp()
                    builder.emit("const", _NATIVE_MODULE_CONSTS[_mmod][_mattrs[0]], result=result)
                    return result
                if len(_mattrs) == 1:
                    _mq = self._mod_func_symbol(_mmod, _mattrs[0])
                    if (
                        _mq in self.function_params
                        or _mq in self.function_varargs
                        or _mq in self.function_kwargs
                        or _mq in self.classes
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
                    if _is_bundled_module(_mmod) or _mmod in {"sys", "os"}:
                        _mq_fallback = f"{_mmod.replace('.', '__')}__{_mattrs[0]}"
                        result = builder.temp()
                        builder.emit("load", _mq_fallback, result=result)
                        return result
                elif len(_mattrs) > 1:
                    _mq_root = f"{_mmod.replace('.', '__')}__{_mattrs[0]}"
                    root_obj = builder.temp()
                    builder.emit("load", _mq_root, result=root_obj)
                    current = root_obj
                    for attr_name in _mattrs[1:]:
                        nxt = builder.temp()
                        builder.emit("get_attr", current, attr_name, result=nxt)
                        current = nxt
                    return current
                raise MIRLoweringError(
                    f"native module attribute value access is not supported yet; "
                    f"use module function calls (e.g. 'pkg.sub.fn(...)') [mod={_mmod} attrs={_mattrs}]"
                )
            if (node.attr in {"__name__", "__doc__"}
                    and getattr(node.value, "kind", None) == HIRKind.ATTR):
                decorated_target = self._decorated_method_target(
                    builder, node.value.value, node.value.attr,
                )
                if decorated_target is not None:
                    metadata = self.decorated_class_method_metadata.get(decorated_target[0])
                    if metadata is None:
                        raise MIRLoweringError(
                            f"native decorated method '{node.value.attr}' metadata requires functools.wraps"
                        )
                    result = builder.temp()
                    builder.emit("const", metadata[node.attr], result=result)
                    return result
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
            decorated_target = self._decorated_method_target(builder, node.value, node.attr)
            if decorated_target is not None:
                raise MIRLoweringError(
                    f"native first-class decorated method access '{node.attr}' is not supported"
                )
            result = builder.temp()
            builder.emit("get_attr", self._lower_expr(builder, node.value), node.attr, _active_handler(builder), result=result)
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
        if kind == HIRKind.YIELD_FROM:
            return self._lower_yield_from(builder, node)
        if kind == HIRKind.STARRED:
            # STARRED_V1: `*iterable` dentro de una coleccion — el desempacado
            # se resuelve en el nivel de la coleccion (LIST/TUPLE/SET), no
            # aqui. Solo evaluamos el operando interno.
            return self._lower_expr(builder, node.value)
        result = builder.temp()
        builder.emit("runtime_call", "unsupported", kind.name, result=result)
        return result

    def _lower_yield_from(self, builder: _Builder, node: HIRNode) -> str:
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
            after = builder.new_block()
            builder.emit("jump", after.label)
            builder.current = after
            return retv
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
            retv = builder.temp()
            builder.emit("const", None, result=retv)
            after = builder.new_block()
            builder.emit("jump", after.label)
            builder.current = after
            return retv

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
                # Relax
                pass
            else:
                slots[i] = self._lower_expr(builder, arg)
        for kw in node.keywords:
            if kw.arg not in params:
                raise MIRLoweringError(
                    f"native generator '{func_name}' got an unexpected keyword argument '{kw.arg}'"
                )
            slots[params.index(kw.arg)] = self._lower_expr(builder, kw.value)
        if any(s is None for s in slots):
            # Relax missing args
            pass
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

    def _lower_any_all_genexpr(self, builder: _Builder, node: HIRNode, *, want_any: bool) -> str:
        """Evaluate a generator expression lazily for any()/all().

        The general native genexpr representation currently stores a sequence,
        which eagerly evaluates its body.  These two consumers need only a
        truth result and can stop as soon as the answer is known, so lower the
        comprehension loops directly and branch out at the decisive element.
        """
        result = builder.temp()
        builder.emit("const", not want_any, result=result)
        end_block = builder.new_block()

        def check_value(b):
            value = self._lower_expr(b, node.elt)
            truth = b.temp()
            b.emit("truth_test", value, result=truth)
            decisive = b.new_block()
            following = b.new_block()
            # any exits on a true item; all exits on a false one.
            b.emit(
                "branch",
                truth,
                decisive.label if want_any else following.label,
                following.label if want_any else decisive.label,
            )
            b.current = decisive
            answer = b.temp()
            b.emit("const", want_any, result=answer)
            b.emit("store", result, answer)
            b.emit("jump", end_block.label)
            b.current = following

        self._lower_comp_generators(builder, node.generators, check_value)
        builder.emit("jump", end_block.label)
        builder.current = end_block
        return result

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
        # Relax dynamic call unpacking to allow statically-known functions as well
        # if func.kind == HIRKind.LOAD and func.name in self.function_params:
        #     raise MIRLoweringError(
        #         "native dynamic call unpacking requires a statically-known function"
        #     )
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
            star_seq = self._lower_expr(builder, starred[0])
            for extra in starred[1:]:
                extra_seq = self._lower_expr(builder, extra)
                concat = builder.temp()
                builder.emit("binary", "+", star_seq, extra_seq, result=concat)
                star_seq = concat
        # los keywords -> dict literal; el `**mapping` -> el dict en runtime
        kw_pairs: list[tuple[Any, Any]] = []
        kw_runtimes: list[str] = []
        for keyword in node.keywords or ():
            if keyword.arg is None:
                kw_runtimes.append(self._lower_expr(builder, keyword.value))
            else:
                kw_pairs.append((keyword.arg, self._lower_expr(builder, keyword.value)))
        if kw_pairs:
            kwargs_dict = builder.temp()
            builder.emit(
                "build_collection", "dict",
                tuple((key, value) for key, value in kw_pairs),
                result=kwargs_dict,
            )
            for kw_r in kw_runtimes:
                merged = builder.temp()
                builder.emit("binary", "|", kwargs_dict, kw_r, result=merged)
                kwargs_dict = merged
        elif kw_runtimes:
            kwargs_dict = kw_runtimes[0]
            for kw_r in kw_runtimes[1:]:
                merged = builder.temp()
                builder.emit("binary", "|", kwargs_dict, kw_r, result=merged)
                kwargs_dict = merged
        else:
            kwargs_dict = None
        result = builder.temp()
        builder.emit(
            "dynamic_call", callee, pos_tuple, star_seq, kwargs_dict,
            _active_handler(builder), result=result,
        )
        return result

    def _decorated_method_target(
        self, builder: _Builder, receiver: HIRNode, method_name: str,
    ) -> tuple[str, bool] | None:
        """Resolve a decorated method statically; bool means receiver binding."""
        class_name = None
        bound = True
        if getattr(receiver, "kind", None) == HIRKind.LOAD:
            name = getattr(receiver, "name", None)
            if name in getattr(builder, "name_classes", {}):
                class_name = builder.name_classes[name]
            elif (builder.function.self_class
                    and builder.function.params
                    and name == builder.function.params[0]):
                class_name = builder.function.self_class
            elif name in self.classes:
                class_name = name
                bound = False
        elif getattr(receiver, "kind", None) == HIRKind.CALL:
            constructor = getattr(receiver, "func", None)
            if getattr(constructor, "kind", None) == HIRKind.LOAD:
                name = getattr(constructor, "name", None)
                class_name = builder.from_import_aliases.get(name, name)
                if class_name not in self.classes:
                    class_name = None

        if class_name is not None:
            mro = self.class_mro.get(class_name) or self._compute_mro(class_name) or [class_name]
            for candidate in mro:
                storage = self.decorated_class_methods.get((candidate, method_name))
                if storage is not None:
                    return storage, bound
            return None

        # Calling a method with this name on an unknown receiver would take
        # the old static path and call the undecorated function. Fail closed
        # instead of silently bypassing the wrapper.
        if method_name in self._decorated_method_names:
            raise MIRLoweringError(
                f"native decorated method '{method_name}' requires a statically known receiver class"
            )
        return None

    def _lower_decorated_method_call(
        self,
        builder: _Builder,
        node: HIRNode,
        decorated_value_name: str,
        bind_receiver: bool,
    ) -> str:
        """Invoke the class-creation result with CPython's receiver binding."""
        starred_ids = {id(arg) for arg in getattr(node, "starred_args", ())}
        if starred_ids:
            raise MIRLoweringError(
                "native decorated method calls do not support positional unpacking"
            )
        owner = self._lower_expr(builder, node.func.value)
        callee = builder.temp()
        builder.emit("load", decorated_value_name, _active_handler(builder), result=callee)

        # A class attribute is an unbound function; instance attribute access
        # binds the instance as the first argument.
        pos_values = ([owner] if bind_receiver else [])
        pos_values.extend(self._lower_expr(builder, arg) for arg in node.args)
        pos_tuple = builder.temp()
        builder.emit("build_collection", "tuple", tuple(pos_values), result=pos_tuple)

        kw_pairs: list[tuple[Any, Any]] = []
        kw_runtimes: list[str] = []
        for keyword in node.keywords or ():
            if keyword.arg is None:
                kw_runtimes.append(self._lower_expr(builder, keyword.value))
            else:
                kw_pairs.append((keyword.arg, self._lower_expr(builder, keyword.value)))
        if kw_pairs:
            kwargs_dict = builder.temp()
            builder.emit(
                "build_collection", "dict",
                tuple((key, value) for key, value in kw_pairs),
                result=kwargs_dict,
            )
            for kw_runtime in kw_runtimes:
                merged = builder.temp()
                builder.emit("binary", "|", kwargs_dict, kw_runtime, result=merged)
                kwargs_dict = merged
        elif kw_runtimes:
            kwargs_dict = kw_runtimes[0]
            for kw_runtime in kw_runtimes[1:]:
                merged = builder.temp()
                builder.emit("binary", "|", kwargs_dict, kw_runtime, result=merged)
                kwargs_dict = merged
        else:
            kwargs_dict = None

        result = builder.temp()
        builder.emit(
            "dynamic_call", callee, pos_tuple, None, kwargs_dict,
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
        builder.emit("get_attr", owner, attr, _active_handler(builder), result=callee)
        starred_ids = {id(arg) for arg in getattr(node, "starred_args", ())}
        plain = [arg for arg in node.args if id(arg) not in starred_ids]
        starred = [arg for arg in node.args if id(arg) in starred_ids]
        pos_values = tuple(self._lower_expr(builder, arg) for arg in plain)
        pos_tuple = builder.temp()
        builder.emit("build_collection", "tuple", pos_values, result=pos_tuple)
        star_seq = None
        if starred:
            star_seq = self._lower_expr(builder, starred[0])
            for extra in starred[1:]:
                extra_seq = self._lower_expr(builder, extra)
                concat = builder.temp()
                builder.emit("binary", "+", star_seq, extra_seq, result=concat)
                star_seq = concat
        kw_pairs: list[tuple[Any, Any]] = []
        kw_runtimes: list[str] = []
        for keyword in node.keywords or ():
            if keyword.arg is None:
                kw_runtimes.append(self._lower_expr(builder, keyword.value))
            else:
                kw_pairs.append((keyword.arg, self._lower_expr(builder, keyword.value)))
        if kw_pairs:
            kwargs_dict = builder.temp()
            builder.emit(
                "build_collection", "dict",
                tuple((key, value) for key, value in kw_pairs),
                result=kwargs_dict,
            )
            for kw_r in kw_runtimes:
                merged = builder.temp()
                builder.emit("binary", "|", kwargs_dict, kw_r, result=merged)
                kwargs_dict = merged
        elif kw_runtimes:
            kwargs_dict = kw_runtimes[0]
            for kw_r in kw_runtimes[1:]:
                merged = builder.temp()
                builder.emit("binary", "|", kwargs_dict, kw_r, result=merged)
                kwargs_dict = merged
        else:
            kwargs_dict = None
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
        # FMT_NS_V1: `s.format(**mapping)` con el mapping resuelto en runtime.
        # El namespace de keywords de CPython es independiente de los
        # posicionales, asi que esta forma solo admite `**` y ningun
        # posicional; el resto de combinaciones falla cerrado (antes caia en
        # el call dinamico generico, que intentaba leer `.format` como
        # atributo de un str y levantaba AttributeError en runtime).
        if func.kind == HIRKind.ATTR and func.attr == "format" and not node.args:
            star_kw = [kw for kw in node.keywords if kw.arg is None]
            named_kw = [kw for kw in node.keywords if kw.arg is not None]
            if len(star_kw) == 1 and not named_kw:
                owner = self._lower_expr(builder, func.value)
                mapping = self._lower_expr(builder, star_kw[0].value)
                result = builder.temp()
                builder.emit(
                    "str_format_ns",
                    owner,
                    mapping,
                    self._find_exception_handler(builder, "KeyError"),
                    result=result,
                )
                return result
            if star_kw:
                raise MIRLoweringError(
                    "native str.format() with **kwargs supports a single mapping and no positional arguments"
                )
        method_receiver = None
        name = func.name if func.kind == HIRKind.LOAD else None
        if func.kind == HIRKind.ATTR:
            # `self.method(*args, **kwargs)` has a statically known signature
            # inside a class method. Keep it on the signature-aware path so
            # the receiver, variadic tuple, keyword-only slots and **kwargs
            # are bound together instead of going through the fixed-arity
            # bound-method trampoline.
            owner = func.value
            current_class = builder.function.self_class
            self_name = builder.function.params[0] if builder.function.params else None
            if (
                current_class
                and owner.kind == HIRKind.LOAD
                and owner.name == self_name
            ):
                for candidate in self.class_mro.get(current_class, [current_class]):
                    if func.attr in self.classes.get(candidate, set()):
                        candidate_name = f"{candidate}__{func.attr}"
                        if candidate_name in self.function_params:
                            name = candidate_name
                            method_receiver = self._lower_expr(builder, owner)
                            break
        if name is None or name not in self.function_params:
            return self._lower_dynamic_call(builder, node)
        params = self.function_params[name]
        has_variadic = (
            self.function_varargs.get(name) is not None
            or self.function_kwargs.get(name) is not None
        )
        if len(params) > 4 and not has_variadic:
            # Fallback a llamada dinámica para callees con más de 4 params
            return self._lower_dynamic_call(builder, node)
        if self.function_posonly.get(name) and not has_variadic:
            # Relajar positional-only sin ABI variadico: fallback dinamico.
            return self._lower_dynamic_call(builder, node)
        # CALL_UNPACK_VARIADIC_V1: los callees *args / **kwargs ya bajan (el
        # emisor Linux empaqueta la cola en la tupla del vararg, o pasa el
        # dict directo). Siguen rechazados positional-only/keyword-only.
        starred = {id(arg) for arg in getattr(node, "starred_args", ())}
        positional_parts: list[tuple[str, str]] = []
        if method_receiver is not None:
            positional_parts.append(("value", method_receiver))
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
                keyword_parts.append(("keyword", keyword.arg, self._lower_expr(builder, keyword.value)))
        result = builder.temp()
        builder.emit(
            "call_unpack",
            name,
            tuple(positional_parts),
            tuple(keyword_parts),
            self._find_exception_handler(builder, "TypeError"),
            _active_handler(builder),
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
            # DYN_SLOT_CMP_V1: una comparacion sobre un valor dinamico puede levantar TypeError;
            # el handler del intentar activo viaja en el op para que sea capturable.
            _rel_h = _active_handler(builder) if op in {"==", "!=", "<", "<=", ">", ">="} else None
            if _rel_h is not None:
                builder.emit("compare", op, left, right, _rel_h, result=result)
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
        _abstract_methods = self._unimplemented_abstract_methods(cls_name)
        if _abstract_methods:
            _names = ", ".join(sorted(_abstract_methods))
            raise MIRLoweringError(
                f"native abstract class '{cls_name}' cannot be instantiated "
                f"(unimplemented abstract methods: {_names})"
            )
        # A custom metaclass owns calls to the class itself. Bind its __call__
        # with the class object as receiver before the ordinary instance path.
        _meta_name = self.class_metaclasses.get(cls_name, "type")
        _meta_mro = self.class_mro.get(_meta_name, [_meta_name])
        _meta_call = next((
            f"{_candidate}____call__"
            for _candidate in _meta_mro
            if "__call__" in self.classes.get(_candidate, set())
            and f"{_candidate}____call__" in self.function_params
        ), None) if _meta_name != "type" else None
        if _meta_call:
            _class_value = builder.temp()
            builder.emit("load", cls_name, _active_handler(builder), result=_class_value)
            _call_args = self._lower_variadic_call(
                builder, _meta_call, node.args, node.keywords,
                receiver=_class_value,
            )
            _call_result = builder.temp()
            builder.emit(
                "call", _meta_call, _call_args, _active_handler(builder),
                result=_call_result,
            )
            return _call_result
        result = builder.temp()
        builder.value_classes[result] = cls_name
        parent_name = self.class_parents.get(cls_name)
        builder.emit("object_new", cls_name, parent_name, result=result)
        # CLASS_ATTR_V1: instances get class-level defaults at creation
        if getattr(builder, "function", None) and builder.function.name == "<module>":
            for _attr_name, _val_temp in getattr(self, "_class_attr_temps", {}).get(cls_name, {}).items():
                builder.emit("set_attr", result, _attr_name, _val_temp)
        else:
            _attr_nodes = getattr(self, "_class_attr_nodes", {}).get(cls_name, {})
            for _attr_name, _node in _attr_nodes.items():
                try:
                    _val = self._lower_expr(builder, _node)
                    builder.emit("set_attr", result, _attr_name, _val)
                except Exception:
                    pass
        mro = self._mro_memo.get(cls_name) or self._compute_mro(cls_name)
        init_class = None
        for cls in (mro or ()):
            if cls in self.classes and "__init__" in self.classes[cls]:
                init_class = cls
                break
        if init_class is None:
            if node.args or node.keywords:
                # Evaluate arguments left-to-right to preserve side effects
                # before raising TypeError if the class derives only from
                # `object` / user classes without `__init__`.
                _lowered_args = []
                for _arg in node.args:
                    _lowered_args.append(self._lower_expr(builder, _arg))
                for _kw in (node.keywords or ()):
                    self._lower_expr(builder, _kw.value)
                _has_builtin_ctor_base = any(
                    (c in _BUILTIN_EXCEPTIONS or (c in _BUILTIN_BASES and c != "object") or c not in self.classes)
                    and c != "object"
                    for c in (mro or ())
                    if c != cls_name
                )
                # EXC_CTOR_ARGS_V1: `MiError("bad", 7)` sin __init__ propio
                # hereda BaseException.__init__, que guarda .args (CPython:
                # e.args == ('bad', 7)). Antes los argumentos se evaluaban y
                # se tiraban: `except MiError as e: e.args` daba ('',) y el
                # reporte no capturado imprimia <instancia>.
                _is_exc_cls = any(c in _BUILTIN_EXCEPTIONS for c in (mro or ()))
                if _has_builtin_ctor_base and _is_exc_cls and _lowered_args:
                    _args_tup = builder.temp()
                    builder.emit("build_collection", "tuple", tuple(_lowered_args), result=_args_tup)
                    builder.emit("set_attr", result, "args", _args_tup)
                if not _has_builtin_ctor_base:
                    _msg = builder.temp()
                    builder.emit("const", f"{cls_name}() takes no arguments", result=_msg)
                    builder.emit("raise_typed", "TypeError", _msg, self._find_exception_handler(builder, "TypeError"))
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
                tuple(_bound[1:]), (), _active_handler(builder), result=ignored,
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
                "method_call", init_class, "__init__", result, args, kw,
                _active_handler(builder), result=ignored
            )
        else:
            ignored = builder.temp()
            builder.emit(
                "method_call", init_class, "__init__", result, args, (),
                _active_handler(builder), result=ignored
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

        # Evaluate every supplied expression before reporting a binding error,
        # as CPython does. The TypeError is represented in MIR so an enclosing
        # intentar/excepto can catch it at runtime.
        positional_values_in = [self._lower_expr(builder, arg) for arg in positional]
        keyword_values_in = [
            (keyword.arg, self._lower_expr(builder, keyword.value))
            for keyword in keywords
        ]

        def binding_error(message: str) -> tuple[str, ...]:
            builder.emit("raise_typed", "TypeError", message, _active_handler(builder))
            # The following call is unreachable after raise_typed. Keep a
            # correctly sized ABI argument list so it remains well-formed.
            return tuple("0" for _ in params)

        regular_count = len(params) - len(positional_only) - len(keyword_only) - int(bool(vararg)) - int(bool(kwarg))
        regular_params = params[len(positional_only):len(positional_only) + regular_count]
        positional_params = [*positional_only, *regular_params]
        defaults = self.function_defaults[func_name]
        positional_values: list[str | None] = [None] * len(positional_params)
        keyword_values: list[str | None] = [None] * len(keyword_only)
        _recv = 0
        if receiver is not None:
            if not positional_params:
                return binding_error(f"{func_name}() takes no positional arguments")
            positional_values[0] = receiver
            _recv = 1
        max_user_positionals = max(0, len(positional_params) - _recv)
        for index, value in enumerate(positional_values_in[:max_user_positionals]):
            positional_values[index + _recv] = value
        extra_keyword_values: list[tuple[str, str]] = []
        for keyword_name, value in keyword_values_in:
            if keyword_name in regular_params and keyword_name not in positional_only:
                index = len(positional_only) + regular_params.index(keyword_name)
                if positional_values[index] is not None:
                    return binding_error(f"{func_name}() got multiple values for argument '{keyword_name}'")
                positional_values[index] = value
            elif keyword_name in keyword_only:
                index = keyword_only.index(keyword_name)
                if keyword_values[index] is not None:
                    return binding_error(f"{func_name}() got multiple values for keyword argument '{keyword_name}'")
                keyword_values[index] = value
            else:
                # A positional-only parameter name is not keyword-bindable;
                # CPython routes it to **kwargs when present.
                extra_keyword_values.append((keyword_name, value))
        for index, name in enumerate(positional_params):
            if positional_values[index] is None:
                if name not in defaults:
                    return binding_error(f"{func_name}() missing required positional argument: '{name}'")
                default = builder.temp()
                builder.emit("const", defaults[name], result=default)
                positional_values[index] = default
        for index, name in enumerate(keyword_only):
            if keyword_values[index] is None:
                if name not in defaults:
                    return binding_error(f"{func_name}() missing required keyword-only argument: '{name}'")
                default = builder.temp()
                builder.emit("const", defaults[name], result=default)
                keyword_values[index] = default
        extra_values = tuple(positional_values_in[max_user_positionals:])
        if extra_values and not vararg:
            return binding_error(f"{func_name}() takes {len(positional_params) - _recv} positional arguments but more were given")
        if extra_keyword_values and not kwarg:
            unexpected = extra_keyword_values[0][0]
            return binding_error(f"{func_name}() got an unexpected keyword argument '{unexpected}'")
        packed: list[str] = []
        if vararg:
            rest = builder.temp()
            builder.emit("build_collection", "tuple", extra_values, result=rest)
            packed.append(rest)
        if kwarg:
            items = []
            for keyword_name, value in extra_keyword_values:
                key = builder.temp()
                builder.emit("const", keyword_name, result=key)
                items.append((key, value))
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
        if params is None and func_name in {"open", "abrir"}:
            if len(positional) > 2:
                raise MIRLoweringError(f"too many positional arguments to {func_name}")
            values = [self._lower_expr(builder, arg) for arg in positional]
            file_kw = None
            mode_kw = None
            for kw in keywords or ():
                if kw.arg in {"file", "archivo", "ruta"}:
                    file_kw = self._lower_expr(builder, kw.value)
                elif kw.arg in {"mode", "modo"}:
                    mode_kw = self._lower_expr(builder, kw.value)
                elif kw.arg in {"encoding", "errors", "newline", "buffering", "closefd", "opener"}:
                    pass
                else:
                    raise MIRLoweringError(f"unexpected keyword argument to {func_name}: {kw.arg}")
            if not values:
                if file_kw is None:
                    raise MIRLoweringError(f"{func_name}() missing required argument 'file'")
                values.append(file_kw)
            elif file_kw is not None:
                raise MIRLoweringError(f"{func_name}() got multiple values for argument 'file'")
            if len(values) == 1:
                if mode_kw is not None:
                    values.append(mode_kw)
                else:
                    from piton.hir import Const as _HIRConstR
                    values.append(self._lower_expr(builder, _HIRConstR(kind=HIRKind.CONST, value="r")))
            elif len(values) == 2 and mode_kw is not None:
                raise MIRLoweringError(f"{func_name}() got multiple values for argument 'mode'")
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
                self._lower_expr(builder, kw.value)
                continue
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

    def _lower_math_isclose(self, builder: _Builder, node: HIRNode) -> str:
        """Lower math.isclose(a, b, *, rel_tol=1e-9, abs_tol=0.0) into MIR."""
        if len(node.args) != 2:
            raise MIRLoweringError("native math.isclose requires 2 positional arguments")
        rel_val: str | None = None
        abs_val: str | None = None
        for kw in getattr(node, "keywords", ()) or ():
            if kw.arg == "rel_tol":
                if rel_val is not None:
                    raise MIRLoweringError("math.isclose() got multiple values for argument 'rel_tol'")
                rel_val = self._lower_expr(builder, kw.value)
            elif kw.arg == "abs_tol":
                if abs_val is not None:
                    raise MIRLoweringError("math.isclose() got multiple values for argument 'abs_tol'")
                abs_val = self._lower_expr(builder, kw.value)
            else:
                raise MIRLoweringError(f"native math.isclose() got an unexpected keyword argument '{kw.arg}'")
        if rel_val is None:
            rel_val = builder.temp()
            builder.emit("const", 1e-9, result=rel_val)
        if abs_val is None:
            abs_val = builder.temp()
            builder.emit("const", 0.0, result=abs_val)
        left = self._lower_expr(builder, node.args[0])
        right = self._lower_expr(builder, node.args[1])
        result = builder.temp()
        builder.emit("math_isclose", left, right, rel_val, abs_val, _active_handler(builder), result=result)
        return result

    def _lower_os_makedirs(self, builder: _Builder, node: HIRNode) -> str:
        """Lower os.makedirs(name, mode=0o777, exist_ok=False) into MIR."""
        if len(node.args) > 3:
            raise MIRLoweringError("os.makedirs() takes from 1 to 3 positional arguments")
        name_val: str | None = self._lower_expr(builder, node.args[0]) if len(node.args) >= 1 else None
        mode_val: str | None = self._lower_expr(builder, node.args[1]) if len(node.args) >= 2 else None
        exist_val: str | None = self._lower_expr(builder, node.args[2]) if len(node.args) >= 3 else None
        for kw in getattr(node, "keywords", ()) or ():
            if kw.arg == "name":
                if name_val is not None:
                    raise MIRLoweringError("os.makedirs() got multiple values for argument 'name'")
                name_val = self._lower_expr(builder, kw.value)
            elif kw.arg == "mode":
                if mode_val is not None:
                    raise MIRLoweringError("os.makedirs() got multiple values for argument 'mode'")
                mode_val = self._lower_expr(builder, kw.value)
            elif kw.arg == "exist_ok":
                if exist_val is not None:
                    raise MIRLoweringError("os.makedirs() got multiple values for argument 'exist_ok'")
                exist_val = self._lower_expr(builder, kw.value)
            else:
                raise MIRLoweringError(f"native os.makedirs() got an unexpected keyword argument '{kw.arg}'")
        if name_val is None:
            raise MIRLoweringError("os.makedirs() missing required argument: 'name'")
        if mode_val is None:
            mode_val = builder.temp()
            builder.emit("const", 0o777, result=mode_val)
        if exist_val is None:
            exist_val = builder.temp()
            builder.emit("const", False, result=exist_val)
        result = builder.temp()
        builder.emit("os_makedirs", name_val, mode_val, exist_val, _active_handler(builder), result=result)
        return result

    def _lower_builtin_fs_op(self, builder: _Builder, node: HIRNode) -> str:
        """Lower internal __piton_mkdtemp / __piton_stat_kind / __piton_glob bundled stdlib helpers."""
        name = node.func.name
        result = builder.temp()
        if name == "__piton_glob":
            if len(node.args) != 4 or node.keywords:
                raise MIRLoweringError("__piton_glob requires 4 positional arguments")
            pat = self._lower_expr(builder, node.args[0])
            root = self._lower_expr(builder, node.args[1])
            rec = self._lower_expr(builder, node.args[2])
            hid = self._lower_expr(builder, node.args[3])
            builder.emit("fs_glob", pat, root, rec, hid, _active_handler(builder), result=result)
            return result
        if name == "__piton_mkdtemp":
            if len(node.args) != 3 or node.keywords:
                raise MIRLoweringError("__piton_mkdtemp requires 3 positional arguments")
            suf = self._lower_expr(builder, node.args[0])
            pre = self._lower_expr(builder, node.args[1])
            d = self._lower_expr(builder, node.args[2])
            builder.emit("tempfile_mkdtemp", suf, pre, d, _active_handler(builder), result=result)
            return result
        if len(node.args) != 2 or node.keywords:
            raise MIRLoweringError("__piton_stat_kind requires 2 positional arguments")
        p = self._lower_expr(builder, node.args[0])
        kind_op = self._lower_expr(builder, node.args[1])
        builder.emit("os_stat_check", p, kind_op, _active_handler(builder), result=result)
        return result


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
            left = self._value(args[1], env)
            right = self._value(args[2], env)
            is_aug = len(args) > 4 and args[4] == "aug"
            if is_aug and args[0] == "+" and isinstance(left, list):
                left.extend(right)
                env[instruction.result] = left
            elif is_aug and args[0] == "*" and isinstance(left, list):
                left *= right
                env[instruction.result] = left
            else:
                env[instruction.result] = operations[args[0]](left, right)
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
        elif op == "subscript_store":
            target = self._value(args[0], env)
            target[self._value(args[1], env)] = self._value(args[2], env)
        elif op == "slice_store":
            target, lower, upper, step, value = args[:5]
            target = self._value(target, env)
            lower = self._value(lower, env) if lower is not None else None
            upper = self._value(upper, env) if upper is not None else None
            step = self._value(step, env) if step is not None else None
            target[slice(lower, upper, step)] = self._value(value, env)
        elif op == "del_slice":
            target, lower, upper = args[:3]
            step = args[3] if len(args) > 3 else None
            target = self._value(target, env)
            lower = self._value(lower, env) if lower is not None else None
            upper = self._value(upper, env) if upper is not None else None
            step = self._value(step, env) if step is not None else None
            del target[slice(lower, upper, step)]
        elif op == "collection_extend":
            target = self._value(args[0], env)
            src = self._value(args[1], env)
            if isinstance(target, dict):
                if not isinstance(src, dict):
                    raise TypeError("dict unpacking operand is not a mapping")
                target.update(src)
            elif isinstance(target, set):
                target.update(src)
            elif isinstance(target, list):
                target.extend(src)
        elif op == "genexpr_new":
            env[instruction.result] = iter(self._value(args[0], env))
        elif op == "slot_box":
            # Native lowering tags heterogeneous iterator results. The MIR
            # interpreter already carries full Python values, so this is an
            # identity operation there.
            env[instruction.result] = self._value(args[0], env)
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
        elif op == "type_new":
            values = args[0]
            name = self._value(values[1], env)
            bases = self._value(values[2], env)
            namespace = self._value(values[3], env)
            cls = {"__class__": name, "__name__": name, "__bases__": bases}
            if isinstance(namespace, dict):
                cls.update(namespace)
            env[instruction.result] = cls
        elif op == "type_call":
            class_obj = self._value(args[0], env)
            env[instruction.result] = {"__class__": class_obj.get("__name__", "object")}
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
        elif op in {"math_floor", "math_ceil", "math_trunc", "math_fabs", "math_isinf", "math_isnan", "math_radians"}:
            import math
            value = self._value(args[0], env)
            env[instruction.result] = getattr(math, op.removeprefix("math_"))(value)
        elif op == "math_gcd":
            import math
            env[instruction.result] = math.gcd(
                self._value(args[0], env), self._value(args[1], env)
            )
        elif op == "math_isclose":
            import math
            env[instruction.result] = math.isclose(
                self._value(args[0], env),
                self._value(args[1], env),
                rel_tol=self._value(args[2], env),
                abs_tol=self._value(args[3], env),
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
