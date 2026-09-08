# piton/hir.py
"""HIR (High-level IR) para Pitón — representación de intención semántica."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Dict, Any, Union
from piton.cst import CSTNode


class HIRKind(Enum):
    # Módulo
    MODULE = auto()

    # Funciones y clases
    FUNC_DEF = auto()
    CLASS_DEF = auto()
    LAMBDA = auto()

    # Control flow
    IF = auto()
    WHILE = auto()
    FOR = auto()
    RETURN = auto()
    YIELD = auto()
    YIELD_FROM = auto()
    RAISE = auto()
    TRY = auto()
    WITH = auto()
    MATCH = auto()

    # Variables
    LOAD = auto()
    STORE = auto()
    DELETE = auto()
    GLOBAL = auto()
    NONLOCAL = auto()
    ASSIGN = auto()
    ANN_ASSIGN = auto()
    AUG_ASSIGN = auto()

    # Expresiones
    CONST = auto()
    BINOP = auto()
    UNOP = auto()
    COMPARE = auto()
    BOOL_OP = auto()
    CALL = auto()
    ATTR = auto()
    SUBSCR = auto()
    LIST = auto()
    TUPLE = auto()
    SET = auto()
    DICT = auto()
    LIST_COMP = auto()
    SET_COMP = auto()
    DICT_COMP = auto()
    GEN_EXPR = auto()
    IF_EXPR = auto()
    AWAIT = auto()

    # Imports
    IMPORT = auto()
    IMPORT_FROM = auto()

    # Patterns
    MATCH_VALUE = auto()
    MATCH_SEQUENCE = auto()
    MATCH_MAPPING = auto()
    MATCH_CLASS = auto()
    MATCH_STAR = auto()
    MATCH_AS = auto()
    MATCH_OR = auto()


@dataclass(slots=True)
class HIRNode:
    kind: HIRKind = HIRKind.MODULE  # default, se sobrescribe en subclases
    # Para source maps
    line: int = 0
    col: int = 0
    # Metadata
    annotations: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Module(HIRNode):
    body: List[HIRNode] = field(default_factory=list)
    kind: HIRKind = HIRKind.MODULE


@dataclass(slots=True)
class FuncDef(HIRNode):
    name: str = ""
    args: Optional["Arguments"] = None
    body: List[HIRNode] = field(default_factory=list)
    returns: Optional[HIRNode] = None
    decorators: List[HIRNode] = field(default_factory=list)
    type_params: Optional[HIRNode] = None
    is_async: bool = False
    kind: HIRKind = HIRKind.FUNC_DEF


@dataclass(slots=True)
class ClassDef(HIRNode):
    name: str = ""
    bases: List[HIRNode] = field(default_factory=list)
    keywords: List[HIRNode] = field(default_factory=list)
    body: List[HIRNode] = field(default_factory=list)
    decorators: List[HIRNode] = field(default_factory=list)
    type_params: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.CLASS_DEF


@dataclass(slots=True)
class Arguments(HIRNode):
    posonlyargs: List[str] = field(default_factory=list)
    args: List[str] = field(default_factory=list)
    kwonlyargs: List[str] = field(default_factory=list)
    kw_defaults: List[Optional[HIRNode]] = field(default_factory=list)
    defaults: List[HIRNode] = field(default_factory=list)
    vararg: Optional[str] = None
    kwarg: Optional[str] = None
    kind: HIRKind = HIRKind.MODULE  # reutilizado


@dataclass(slots=True)
class If(HIRNode):
    test: Optional[HIRNode] = None
    body: List[HIRNode] = field(default_factory=list)
    orelse: List[HIRNode] = field(default_factory=list)
    kind: HIRKind = HIRKind.IF


@dataclass(slots=True)
class While(HIRNode):
    test: Optional[HIRNode] = None
    body: List[HIRNode] = field(default_factory=list)
    orelse: List[HIRNode] = field(default_factory=list)
    kind: HIRKind = HIRKind.WHILE


@dataclass(slots=True)
class For(HIRNode):
    target: Optional[HIRNode] = None
    iter: Optional[HIRNode] = None
    body: List[HIRNode] = field(default_factory=list)
    orelse: List[HIRNode] = field(default_factory=list)
    is_async: bool = False
    kind: HIRKind = HIRKind.FOR


@dataclass(slots=True)
class Return(HIRNode):
    value: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.RETURN


@dataclass(slots=True)
class Yield(HIRNode):
    value: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.YIELD


@dataclass(slots=True)
class YieldFrom(HIRNode):
    value: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.YIELD_FROM


@dataclass(slots=True)
class Raise(HIRNode):
    exc: Optional[HIRNode] = None
    cause: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.RAISE


@dataclass(slots=True)
class Try(HIRNode):
    body: List[HIRNode] = field(default_factory=list)
    handlers: List["ExceptHandler"] = field(default_factory=list)
    orelse: List[HIRNode] = field(default_factory=list)
    finalbody: List[HIRNode] = field(default_factory=list)
    kind: HIRKind = HIRKind.TRY


@dataclass(slots=True)
class ExceptHandler(HIRNode):
    type_: Optional[HIRNode] = None
    name: Optional[str] = None
    body: List[HIRNode] = field(default_factory=list)
    is_star: bool = False
    kind: HIRKind = HIRKind.TRY


@dataclass(slots=True)
class With(HIRNode):
    items: List["WithItem"] = field(default_factory=list)
    body: List[HIRNode] = field(default_factory=list)
    is_async: bool = False
    kind: HIRKind = HIRKind.WITH


@dataclass(slots=True)
class WithItem(HIRNode):
    context_expr: Optional[HIRNode] = None
    optional_vars: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.WITH


@dataclass(slots=True)
class Match(HIRNode):
    subject: Optional[HIRNode] = None
    cases: List["CaseBlock"] = field(default_factory=list)
    kind: HIRKind = HIRKind.MATCH


@dataclass(slots=True)
class CaseBlock(HIRNode):
    pattern: Optional[HIRNode] = None
    guard: Optional[HIRNode] = None
    body: List[HIRNode] = field(default_factory=list)
    kind: HIRKind = HIRKind.MATCH


@dataclass(slots=True)
class Load(HIRNode):
    name: str = ""
    kind: HIRKind = HIRKind.LOAD


@dataclass(slots=True)
class Store(HIRNode):
    name: str = ""
    kind: HIRKind = HIRKind.STORE


@dataclass(slots=True)
class Delete(HIRNode):
    targets: List[HIRNode] = field(default_factory=list)
    kind: HIRKind = HIRKind.DELETE


@dataclass(slots=True)
class Global(HIRNode):
    names: List[str] = field(default_factory=list)
    kind: HIRKind = HIRKind.GLOBAL


@dataclass(slots=True)
class Nonlocal(HIRNode):
    names: List[str] = field(default_factory=list)
    kind: HIRKind = HIRKind.NONLOCAL


@dataclass(slots=True)
class Assign(HIRNode):
    targets: List[HIRNode] = field(default_factory=list)
    value: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.ASSIGN


@dataclass(slots=True)
class AnnAssign(HIRNode):
    target: Optional[HIRNode] = None
    annotation: Optional[HIRNode] = None
    value: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.ANN_ASSIGN


@dataclass(slots=True)
class AugAssign(HIRNode):
    target: Optional[HIRNode] = None
    op: str = ""
    value: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.AUG_ASSIGN


@dataclass(slots=True)
class Const(HIRNode):
    value: Any = None
    kind: HIRKind = HIRKind.CONST


@dataclass(slots=True)
class BinOp(HIRNode):
    left: Optional[HIRNode] = None
    op: str = ""
    right: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.BINOP


@dataclass(slots=True)
class UnOp(HIRNode):
    op: str = ""
    operand: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.UNOP


@dataclass(slots=True)
class Compare(HIRNode):
    left: Optional[HIRNode] = None
    ops: List[str] = field(default_factory=list)
    comparators: List[HIRNode] = field(default_factory=list)
    kind: HIRKind = HIRKind.COMPARE


@dataclass(slots=True)
class BoolOp(HIRNode):
    op: str = ""
    values: List[HIRNode] = field(default_factory=list)
    kind: HIRKind = HIRKind.BOOL_OP


@dataclass(slots=True)
class Call(HIRNode):
    func: Optional[HIRNode] = None
    args: List[HIRNode] = field(default_factory=list)
    keywords: List["Keyword"] = field(default_factory=list)
    kind: HIRKind = HIRKind.CALL


@dataclass(slots=True)
class Keyword(HIRNode):
    arg: Optional[str] = None
    value: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.CALL


@dataclass(slots=True)
class Attr(HIRNode):
    value: Optional[HIRNode] = None
    attr: str = ""
    ctx: str = "Load"
    kind: HIRKind = HIRKind.ATTR


@dataclass(slots=True)
class Subscr(HIRNode):
    value: Optional[HIRNode] = None
    slice: Optional[HIRNode] = None
    ctx: str = "Load"
    kind: HIRKind = HIRKind.SUBSCR


@dataclass(slots=True)
class List(HIRNode):
    elts: List[HIRNode] = field(default_factory=list)
    ctx: str = "Load"
    kind: HIRKind = HIRKind.LIST


@dataclass(slots=True)
class Tuple(HIRNode):
    elts: List[HIRNode] = field(default_factory=list)
    ctx: str = "Load"
    kind: HIRKind = HIRKind.TUPLE


@dataclass(slots=True)
class Set(HIRNode):
    elts: List[HIRNode] = field(default_factory=list)
    kind: HIRKind = HIRKind.SET


@dataclass(slots=True)
class Dict(HIRNode):
    keys: List[Optional[HIRNode]] = field(default_factory=list)
    values: List[HIRNode] = field(default_factory=list)
    kind: HIRKind = HIRKind.DICT


@dataclass(slots=True)
class ListComp(HIRNode):
    elt: Optional[HIRNode] = None
    generators: List["CompFor"] = field(default_factory=list)
    kind: HIRKind = HIRKind.LIST_COMP


@dataclass(slots=True)
class CompFor(HIRNode):
    target: Optional[HIRNode] = None
    iter: Optional[HIRNode] = None
    ifs: List[HIRNode] = field(default_factory=list)
    is_async: bool = False
    kind: HIRKind = HIRKind.FOR


@dataclass(slots=True)
class IfExpr(HIRNode):
    test: Optional[HIRNode] = None
    body: Optional[HIRNode] = None
    orelse: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.IF_EXPR


@dataclass(slots=True)
class Await(HIRNode):
    value: Optional[HIRNode] = None
    kind: HIRKind = HIRKind.AWAIT


@dataclass(slots=True)
class Import(HIRNode):
    names: List["Alias"] = field(default_factory=list)
    kind: HIRKind = HIRKind.IMPORT


@dataclass(slots=True)
class ImportFrom(HIRNode):
    module: Optional[str] = None
    names: List["Alias"] = field(default_factory=list)
    level: int = 0
    kind: HIRKind = HIRKind.IMPORT_FROM


@dataclass(slots=True)
class Alias(HIRNode):
    name: str = ""
    asname: Optional[str] = None
    kind: HIRKind = HIRKind.IMPORT


# MIR forward reference
class MIRNode:
    pass


# Funciones de utilidad para construir HIR
def hir_module(body: List[HIRNode]) -> Module:
    return Module(body=body)

def hir_func(name: str, args: Arguments, body: List[HIRNode], **kw) -> FuncDef:
    return FuncDef(kind=HIRKind.FUNC_DEF, name=name, args=args, body=body, **kw)

def hir_const(value: Any) -> Const:
    return Const(kind=HIRKind.CONST, value=value)

def hir_load(name: str) -> Load:
    return Load(kind=HIRKind.LOAD, name=name)

def hir_store(name: str) -> Store:
    return Store(kind=HIRKind.STORE, name=name)

def hir_binop(left: HIRNode, op: str, right: HIRNode) -> BinOp:
    return BinOp(kind=HIRKind.BINOP, left=left, op=op, right=right)

def hir_call(func: HIRNode, args: List[HIRNode], keywords: List[Keyword]) -> Call:
    return Call(kind=HIRKind.CALL, func=func, args=args, keywords=keywords)
