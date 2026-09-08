# piton/cst.py
"""CST (Concrete Syntax Tree) para Pitón — árbol con toda la información sintáctica."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union, List, Any
from enum import Enum


class CSTNodeType(Enum):
    # Programa
    MODULE = "module"

    # Statements
    EXPR_STMT = "expr_stmt"
    ASSIGN = "assign"
    AUG_ASSIGN = "aug_assign"
    ANN_ASSIGN = "ann_assign"  # tipo Nombre = expr
    IF_STMT = "if_stmt"
    WHILE_STMT = "while_stmt"
    FOR_STMT = "for_stmt"
    FUNC_DEF = "func_def"
    CLASS_DEF = "class_def"
    RETURN_STMT = "return_stmt"
    YIELD_STMT = "yield_stmt"
    YIELD_FROM_STMT = "yield_from_stmt"
    RAISE_STMT = "raise_stmt"
    TRY_STMT = "try_stmt"
    WITH_STMT = "with_stmt"
    MATCH_STMT = "match_stmt"  # segun
    CASE_BLOCK = "case_block"  # caso
    IMPORT_STMT = "import_stmt"
    IMPORT_FROM_STMT = "import_from_stmt"
    GLOBAL_STMT = "global_stmt"
    NONLOCAL_STMT = "nonlocal_stmt"
    ASSERT_STMT = "assert_stmt"
    DEL_STMT = "del_stmt"
    PASS_STMT = "pass_stmt"
    BREAK_STMT = "break_stmt"
    CONTINUE_STMT = "continue_stmt"

    # Async variants
    ASYNC_FUNC_DEF = "async_func_def"
    ASYNC_FOR_STMT = "async_for_stmt"
    ASYNC_WITH_STMT = "async_with_stmt"

    # Expresiones
    NAME = "name"
    CONSTANT = "constant"
    BIN_OP = "bin_op"
    UNARY_OP = "unary_op"
    COMPARE = "compare"
    BOOL_OP = "bool_op"
    CALL = "call"
    ATTRIBUTE = "attribute"
    SUBSCRIPT = "subscript"
    SLICE = "slice"
    LIST = "list"
    TUPLE = "tuple"
    SET = "set"
    DICT = "dict"
    LIST_COMP = "list_comp"
    SET_COMP = "set_comp"
    DICT_COMP = "dict_comp"
    GEN_EXPR = "gen_expr"
    IF_EXPR = "if_expr"  # a si cond sino b
    LAMBDA = "lambda"
    AWAIT = "await"
    FSTRING = "fstring"
    FSTRING_PART = "fstring_part"
    FSTRING_EXPR = "fstring_expr"

    # Pattern matching
    MATCH_VALUE = "match_value"
    MATCH_SINGLETON = "match_singleton"
    MATCH_SEQUENCE = "match_sequence"
    MATCH_MAPPING = "match_mapping"
    MATCH_CLASS = "match_class"
    MATCH_STAR = "match_star"
    MATCH_AS = "match_as"
    MATCH_OR = "match_or"

    # Type params (Python 3.12)
    TYPE_PARAMS = "type_params"
    TYPE_VAR = "type_var"
    PARAM_SPEC = "param_spec"
    TYPE_VAR_TUPLE = "type_var_tuple"

    # Comprehension clauses
    COMP_FOR = "comp_for"
    COMP_IF = "comp_if"

    # With items
    WITH_ITEM = "with_item"

    # Except handlers
    EXCEPT_HANDLER = "except_handler"

    # Arguments
    ARGUMENTS = "arguments"
    ARG = "arg"
    KEYWORD = "keyword"

    # Decorators
    DECORATOR = "decorator"


@dataclass(slots=True)
class CSTNode:
    # Posiciones para source map
    line: int = 0
    col: int = 0
    end_line: int = 0
    end_col: int = 0
    start_byte: int = 0
    end_byte: int = 0

    def set_pos(self, token: Any) -> CSTNode:
        if hasattr(token, "line"):
            self.line = token.line
            self.col = token.col
            self.start_byte = getattr(token, "start", 0)
        if hasattr(token, "end_line"):
            self.end_line = token.end_line
            self.end_col = token.end_col
            self.end_byte = getattr(token, "end", 0)
        elif hasattr(token, "line"):
            self.end_line = token.line
            self.end_col = token.col + len(getattr(token, "value", ""))
            self.end_byte = getattr(token, "end", 0)
        return self


@dataclass(slots=True)
class Module(CSTNode):
    body: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.MODULE


@dataclass(slots=True)
class ExprStmt(CSTNode):
    value: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.EXPR_STMT


@dataclass(slots=True)
class Assign(CSTNode):
    targets: List[CSTNode] = field(default_factory=list)
    value: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.ASSIGN


@dataclass(slots=True)
class AnnAssign(CSTNode):
    target: Optional[CSTNode] = None
    annotation: Optional[CSTNode] = None
    value: Optional[CSTNode] = None
    simple: int = 1  # 1 si target es Name simple
    type: CSTNodeType = CSTNodeType.ANN_ASSIGN


@dataclass(slots=True)
class AugAssign(CSTNode):
    target: Optional[CSTNode] = None
    op: str = ""  # +=, -=, etc.
    value: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.AUG_ASSIGN


@dataclass(slots=True)
class IfStmt(CSTNode):
    test: Optional[CSTNode] = None
    body: List[CSTNode] = field(default_factory=list)
    orelse: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.IF_STMT


@dataclass(slots=True)
class WhileStmt(CSTNode):
    test: Optional[CSTNode] = None
    body: List[CSTNode] = field(default_factory=list)
    orelse: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.WHILE_STMT


@dataclass(slots=True)
class ForStmt(CSTNode):
    target: Optional[CSTNode] = None
    iter: Optional[CSTNode] = None
    body: List[CSTNode] = field(default_factory=list)
    orelse: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.FOR_STMT


@dataclass(slots=True)
class FuncDef(CSTNode):
    name: str = ""
    args: Optional["Arguments"] = None
    body: List[CSTNode] = field(default_factory=list)
    returns: Optional[CSTNode] = None
    decorators: List[CSTNode] = field(default_factory=list)
    type_params: Optional[CSTNode] = None
    is_async: bool = False
    type: CSTNodeType = CSTNodeType.FUNC_DEF


@dataclass(slots=True)
class ClassDef(CSTNode):
    name: str = ""
    bases: List[CSTNode] = field(default_factory=list)
    keywords: List[CSTNode] = field(default_factory=list)
    body: List[CSTNode] = field(default_factory=list)
    decorators: List[CSTNode] = field(default_factory=list)
    type_params: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.CLASS_DEF


@dataclass(slots=True)
class ReturnStmt(CSTNode):
    value: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.RETURN_STMT


@dataclass(slots=True)
class YieldStmt(CSTNode):
    value: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.YIELD_STMT


@dataclass(slots=True)
class YieldFromStmt(CSTNode):
    value: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.YIELD_FROM_STMT


@dataclass(slots=True)
class RaiseStmt(CSTNode):
    exc: Optional[CSTNode] = None
    cause: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.RAISE_STMT


@dataclass(slots=True)
class TryStmt(CSTNode):
    body: List[CSTNode] = field(default_factory=list)
    handlers: List["ExceptHandler"] = field(default_factory=list)
    orelse: List[CSTNode] = field(default_factory=list)
    finalbody: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.TRY_STMT


@dataclass(slots=True)
class ExceptHandler(CSTNode):
    type: CSTNodeType = CSTNodeType.EXCEPT_HANDLER
    type_: Optional[CSTNode] = None  # excepción a capturar
    name: Optional[str] = None  # as nombre
    body: List[CSTNode] = field(default_factory=list)
    is_star: bool = False  # excepto*


@dataclass(slots=True)
class WithStmt(CSTNode):
    items: List["WithItem"] = field(default_factory=list)
    body: List[CSTNode] = field(default_factory=list)
    is_async: bool = False
    type: CSTNodeType = CSTNodeType.WITH_STMT


@dataclass(slots=True)
class WithItem(CSTNode):
    context_expr: Optional[CSTNode] = None
    optional_vars: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.WITH_ITEM


@dataclass(slots=True)
class MatchStmt(CSTNode):
    subject: Optional[CSTNode] = None
    cases: List["CaseBlock"] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.MATCH_STMT


@dataclass(slots=True)
class CaseBlock(CSTNode):
    pattern: Optional[CSTNode] = None
    guard: Optional[CSTNode] = None
    body: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.CASE_BLOCK


@dataclass(slots=True)
class ImportStmt(CSTNode):
    names: List["Alias"] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.IMPORT_STMT


@dataclass(slots=True)
class ImportFromStmt(CSTNode):
    module: Optional[str] = None
    names: List["Alias"] = field(default_factory=list)
    level: int = 0
    type: CSTNodeType = CSTNodeType.IMPORT_FROM_STMT


@dataclass(slots=True)
class Alias(CSTNode):
    name: str = ""
    asname: Optional[str] = None
    type: CSTNodeType = CSTNodeType.IMPORT_STMT


@dataclass(slots=True)
class GlobalStmt(CSTNode):
    names: List[str] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.GLOBAL_STMT


@dataclass(slots=True)
class NonlocalStmt(CSTNode):
    names: List[str] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.NONLOCAL_STMT


@dataclass(slots=True)
class AssertStmt(CSTNode):
    test: Optional[CSTNode] = None
    msg: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.ASSERT_STMT


@dataclass(slots=True)
class DelStmt(CSTNode):
    targets: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.DEL_STMT


@dataclass(slots=True)
class PassStmt(CSTNode):
    type: CSTNodeType = CSTNodeType.PASS_STMT


@dataclass(slots=True)
class BreakStmt(CSTNode):
    type: CSTNodeType = CSTNodeType.BREAK_STMT


@dataclass(slots=True)
class ContinueStmt(CSTNode):
    type: CSTNodeType = CSTNodeType.CONTINUE_STMT


# Expresiones
@dataclass(slots=True)
class Name(CSTNode):
    id: str = ""
    ctx: str = "Load"  # Load, Store, Del
    type: CSTNodeType = CSTNodeType.NAME


@dataclass(slots=True)
class Constant(CSTNode):
    value: Any = None  # int, float, str, bool, None
    type: CSTNodeType = CSTNodeType.CONSTANT


@dataclass(slots=True)
class BinOp(CSTNode):
    left: Optional[CSTNode] = None
    op: str = ""  # +, -, *, /, //, %, **, @, <<, >>, &, |, ^
    right: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.BIN_OP


@dataclass(slots=True)
class UnaryOp(CSTNode):
    op: str = ""  # +, -, ~, not
    operand: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.UNARY_OP


@dataclass(slots=True)
class Compare(CSTNode):
    left: Optional[CSTNode] = None
    ops: List[str] = field(default_factory=list)  # ==, !=, <, <=, >, >=, is, is not, in, not in
    comparators: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.COMPARE


@dataclass(slots=True)
class BoolOp(CSTNode):
    op: str = ""  # and, or
    values: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.BOOL_OP


@dataclass(slots=True)
class Call(CSTNode):
    func: Optional[CSTNode] = None
    args: List[CSTNode] = field(default_factory=list)
    keywords: List["Keyword"] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.CALL


@dataclass(slots=True)
class Keyword(CSTNode):
    arg: Optional[str] = None  # None para **kwargs
    value: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.KEYWORD


@dataclass(slots=True)
class Attribute(CSTNode):
    value: Optional[CSTNode] = None
    attr: str = ""
    ctx: str = "Load"
    type: CSTNodeType = CSTNodeType.ATTRIBUTE


@dataclass(slots=True)
class Subscript(CSTNode):
    value: Optional[CSTNode] = None
    slice: Optional[CSTNode] = None
    ctx: str = "Load"
    type: CSTNodeType = CSTNodeType.SUBSCRIPT


@dataclass(slots=True)
class Slice(CSTNode):
    lower: Optional[CSTNode] = None
    upper: Optional[CSTNode] = None
    step: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.SLICE


@dataclass(slots=True)
class List(CSTNode):
    elts: List[CSTNode] = field(default_factory=list)
    ctx: str = "Load"
    type: CSTNodeType = CSTNodeType.LIST


@dataclass(slots=True)
class Tuple(CSTNode):
    elts: List[CSTNode] = field(default_factory=list)
    ctx: str = "Load"
    type: CSTNodeType = CSTNodeType.TUPLE


@dataclass(slots=True)
class Set(CSTNode):
    elts: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.SET


@dataclass(slots=True)
class Dict(CSTNode):
    keys: List[Optional[CSTNode]] = field(default_factory=list)  # None para **
    values: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.DICT


@dataclass(slots=True)
class ListComp(CSTNode):
    elt: Optional[CSTNode] = None
    generators: List["CompFor"] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.LIST_COMP


@dataclass(slots=True)
class SetComp(CSTNode):
    elt: Optional[CSTNode] = None
    generators: List["CompFor"] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.SET_COMP


@dataclass(slots=True)
class DictComp(CSTNode):
    key: Optional[CSTNode] = None
    value: Optional[CSTNode] = None
    generators: List["CompFor"] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.DICT_COMP


@dataclass(slots=True)
class GenExpr(CSTNode):
    elt: Optional[CSTNode] = None
    generators: List["CompFor"] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.GEN_EXPR


@dataclass(slots=True)
class CompFor(CSTNode):
    target: Optional[CSTNode] = None
    iter: Optional[CSTNode] = None
    ifs: List[CSTNode] = field(default_factory=list)
    is_async: bool = False
    type: CSTNodeType = CSTNodeType.COMP_FOR


@dataclass(slots=True)
class CompIf(CSTNode):
    test: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.COMP_IF


@dataclass(slots=True)
class IfExpr(CSTNode):
    test: Optional[CSTNode] = None
    body: Optional[CSTNode] = None
    orelse: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.IF_EXPR


@dataclass(slots=True)
class Lambda(CSTNode):
    args: Optional["Arguments"] = None
    body: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.LAMBDA


@dataclass(slots=True)
class Await(CSTNode):
    value: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.AWAIT


@dataclass(slots=True)
class FString(CSTNode):
    parts: List[CSTNode] = field(default_factory=list)  # FStringPart y FStringExpr
    type: CSTNodeType = CSTNodeType.FSTRING


@dataclass(slots=True)
class FStringPart(CSTNode):
    value: str = ""
    type: CSTNodeType = CSTNodeType.FSTRING_PART


@dataclass(slots=True)
class FStringExpr(CSTNode):
    value: Optional[CSTNode] = None
    format_spec: Optional[CSTNode] = None
    conversion: int = -1  # -1=none, ord('s'), ord('r'), ord('a')
    type: CSTNodeType = CSTNodeType.FSTRING_EXPR


# Pattern matching nodes
@dataclass(slots=True)
class MatchValue(CSTNode):
    value: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.MATCH_VALUE


@dataclass(slots=True)
class MatchSingleton(CSTNode):
    value: Any = None  # True, False, None
    type: CSTNodeType = CSTNodeType.MATCH_SINGLETON


@dataclass(slots=True)
class MatchSequence(CSTNode):
    patterns: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.MATCH_SEQUENCE


@dataclass(slots=True)
class MatchMapping(CSTNode):
    keys: List[CSTNode] = field(default_factory=list)
    patterns: List[CSTNode] = field(default_factory=list)
    rest: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.MATCH_MAPPING


@dataclass(slots=True)
class MatchClass(CSTNode):
    cls: Optional[CSTNode] = None
    patterns: List[CSTNode] = field(default_factory=list)
    kwd_attrs: List[str] = field(default_factory=list)
    kwd_patterns: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.MATCH_CLASS


@dataclass(slots=True)
class MatchStar(CSTNode):
    name: Optional[str] = None
    type: CSTNodeType = CSTNodeType.MATCH_STAR


@dataclass(slots=True)
class MatchOr(CSTNode):
    patterns: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.MATCH_OR


@dataclass(slots=True)
class MatchAs(CSTNode):
    pattern: Optional[CSTNode] = None
    name: Optional[str] = None
    type: CSTNodeType = CSTNodeType.MATCH_AS


# Type params
@dataclass(slots=True)
class TypeParams(CSTNode):
    params: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.TYPE_PARAMS


@dataclass(slots=True)
class TypeVar(CSTNode):
    name: str = ""
    bound: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.TYPE_VAR


@dataclass(slots=True)
class ParamSpec(CSTNode):
    name: str = ""
    type: CSTNodeType = CSTNodeType.PARAM_SPEC


@dataclass(slots=True)
class TypeVarTuple(CSTNode):
    name: str = ""
    type: CSTNodeType = CSTNodeType.TYPE_VAR_TUPLE


# Arguments
@dataclass(slots=True)
class Arguments(CSTNode):
    posonlyargs: List["Arg"] = field(default_factory=list)
    args: List["Arg"] = field(default_factory=list)
    kwonlyargs: List["Arg"] = field(default_factory=list)
    kw_defaults: List[Optional[CSTNode]] = field(default_factory=list)
    defaults: List[CSTNode] = field(default_factory=list)
    vararg: Optional["Arg"] = None
    kwarg: Optional["Arg"] = None
    type: CSTNodeType = CSTNodeType.ARGUMENTS
    kwonlyargs: List["Arg"] = field(default_factory=list)
    kw_defaults: List[Optional[CSTNode]] = field(default_factory=list)
    defaults: List[CSTNode] = field(default_factory=list)
    vararg: Optional["Arg"] = None
    kwarg: Optional["Arg"] = None
    type: CSTNodeType = CSTNodeType.ARGUMENTS


@dataclass(slots=True)
class Arg(CSTNode):
    arg: str = ""
    annotation: Optional[CSTNode] = None
    type: CSTNodeType = CSTNodeType.ARG


# Decorators
@dataclass(slots=True)
class Decorator(CSTNode):
    func: Optional[CSTNode] = None
    decorators: List[CSTNode] = field(default_factory=list)
    type: CSTNodeType = CSTNodeType.DECORATOR


# Union type para type hints
CSTExpr = Union[
    Name, Constant, BinOp, UnaryOp, Compare, BoolOp, Call,
    Attribute, Subscript, Slice, List, Tuple, Set, Dict,
    ListComp, SetComp, DictComp, GenExpr, IfExpr, Lambda,
    Await, FString, FStringPart, FStringExpr,
    MatchValue, MatchSingleton, MatchSequence, MatchMapping,
    MatchClass, MatchStar, MatchAs, MatchOr,
]

CSTStmt = Union[
    ExprStmt, Assign, AnnAssign, AugAssign, IfStmt, WhileStmt, ForStmt,
    FuncDef, ClassDef, ReturnStmt, YieldStmt, YieldFromStmt, RaiseStmt,
    TryStmt, WithStmt, MatchStmt, ImportStmt, ImportFromStmt,
    GlobalStmt, NonlocalStmt, AssertStmt, DelStmt,
    PassStmt, BreakStmt, ContinueStmt,
]

CSTNodeUnion = Union[CSTExpr, CSTStmt, Module, Arguments, Arg, Keyword,
                     Alias, WithItem, ExceptHandler, CaseBlock,
                     TypeParams, TypeVar, ParamSpec, TypeVarTuple,
                     CompFor, Decorator]