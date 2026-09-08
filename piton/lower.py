# piton/lower.py
"""Lowering CST → HIR."""
from __future__ import annotations

from typing import List, Optional, Any
from piton.cst import CSTNode, CSTNodeType
from piton.hir import (
    HIRNode, Module, FuncDef, ClassDef, Arguments,
    If, While, For, Return, Yield, YieldFrom, Raise,
    Try, ExceptHandler, With, WithItem, Match, CaseBlock,
    Load, Store, Delete, Global, Nonlocal,
    Assign as HIRAssign, AnnAssign as HIRAnnAssign, AugAssign as HIRAugAssign,
    Const, BinOp, UnOp, Compare, BoolOp, Call, Keyword,
    Attr, Subscr, List, Tuple, Set, Dict,
    ListComp, CompFor, IfExpr, Await,
    Import, ImportFrom, Alias,
    HIRKind,
)
from piton.cst import (
    Module as CSTModule, FuncDef as CSTFuncDef, ClassDef as CSTClassDef,
    Lambda as CSTLambda, IfStmt as CSTIfStmt, WhileStmt as CSTWhileStmt,
    ForStmt as CSTForStmt, ReturnStmt as CSTReturnStmt,
    YieldStmt as CSTYieldStmt, YieldFromStmt as CSTYieldFromStmt,
    RaiseStmt as CSTRaiseStmt, TryStmt as CSTTryStmt,
    ExceptHandler as CSTExceptHandler, WithStmt as CSTWithStmt,
    WithItem as CSTWithItem, MatchStmt as CSTMatchStmt,
    CaseBlock as CSTCaseBlock, ImportStmt as CSTImportStmt,
    ImportFromStmt as CSTImportFromStmt, Alias as CSTAlias,
    GlobalStmt as CSTGlobalStmt, NonlocalStmt as CSTNonlocalStmt,
    Name as CSTName, Constant as CSTConstant, BinOp as CSTBinOp,
    UnaryOp as CSTUnaryOp, Compare as CSTCompare, BoolOp as CSTBoolOp,
    Call as CSTCall, Keyword as CSTKeyword, Attribute as CSTAttribute,
    Subscript as CSTSubscript, Slice as CSTSlice,
    List as CSTList, Tuple as CSTTuple, Set as CSTSet, Dict as CSTDict,
    ListComp as CSTListComp, CompFor as CSTCompFor, IfExpr as CSTIfExpr,
    Await as CWAwait,
    ExprStmt, Assign, AnnAssign, AugAssign,
    Arguments as CSTArguments, Arg as CSTArg,
)


class LoweringError(Exception):
    pass


class Lowerer:
    def __init__(self):
        self.errors: List[str] = []

    def lower(self, cst: CSTNode) -> HIRNode:
        method_name = f"_lower_{cst.type.name.lower()}"
        method = getattr(self, method_name, None)
        if method is None:
            compact_name = f"_lower_{cst.type.name.lower().replace('_', '')}"
            method = getattr(self, compact_name, self._lower_generic)
        return method(cst)

    def _lower_generic(self, cst: CSTNode) -> HIRNode:
        # Para nodos que no tienen lowering específico
        self.errors.append(f"No lowering para {cst.type.name}")
        return HIRNode(kind=HIRKind.MODULE)

    def _lower_module(self, cst: CSTModule) -> Module:
        body = [self.lower(stmt) for stmt in cst.body]
        return Module(kind=HIRKind.MODULE, body=body)

    def _lower_funcdef(self, cst: CSTFuncDef) -> FuncDef:
        args = self._lower_arguments(cst.args)
        body = [self.lower(stmt) for stmt in cst.body]
        returns = self.lower(cst.returns) if cst.returns else None
        decorators = [self.lower(d) for d in cst.decorators]
        type_params = self.lower(cst.type_params) if cst.type_params else None
        return FuncDef(
            kind=HIRKind.FUNC_DEF,
            name=cst.name,
            args=args,
            body=body,
            returns=returns,
            decorators=decorators,
            type_params=type_params,
            is_async=cst.is_async,
        )

    def _lower_classdef(self, cst: CSTClassDef) -> ClassDef:
        bases = [self.lower(b) for b in cst.bases]
        keywords = [self.lower(k) for k in cst.keywords]
        body = [self.lower(stmt) for stmt in cst.body]
        decorators = [self.lower(d) for d in cst.decorators]
        type_params = self.lower(cst.type_params) if cst.type_params else None
        return ClassDef(
            kind=HIRKind.CLASS_DEF,
            name=cst.name,
            bases=bases,
            keywords=keywords,
            body=body,
            decorators=decorators,
            type_params=type_params,
        )

    def _lower_lambda(self, cst: CSTLambda):
        args = self._lower_arguments(cst.args)
        body = self.lower(cst.body)
        return FuncDef(
            kind=HIRKind.LAMBDA,
            name="<lambda>",
            args=args,
            body=[body],
            is_async=False,
        )

    def _lower_arguments(self, cst: CSTArguments) -> Arguments:
        return Arguments(
            posonlyargs=[a.arg for a in cst.posonlyargs],
            args=[a.arg for a in cst.args],
            kwonlyargs=[a.arg for a in cst.kwonlyargs],
            kw_defaults=[self.lower(d) if d else None for d in cst.kw_defaults],
            defaults=[self.lower(d) for d in cst.defaults],
            vararg=cst.vararg.arg if cst.vararg else None,
            kwarg=cst.kwarg.arg if cst.kwarg else None,
        )

    # Statements
    def _lower_ifstmt(self, cst: CSTIfStmt):
        test = self.lower(cst.test)
        body = [self.lower(s) for s in cst.body]
        orelse = [self.lower(s) for s in cst.orelse]
        return If(kind=HIRKind.IF, test=test, body=body, orelse=orelse)

    def _lower_whilestmt(self, cst: CSTWhileStmt):
        test = self.lower(cst.test)
        body = [self.lower(s) for s in cst.body]
        orelse = [self.lower(s) for s in cst.orelse]
        return While(kind=HIRKind.WHILE, test=test, body=body, orelse=orelse)

    def _lower_forstmt(self, cst: CSTForStmt):
        target = self.lower(cst.target)
        iter_ = self.lower(cst.iter)
        body = [self.lower(s) for s in cst.body]
        orelse = [self.lower(s) for s in cst.orelse]
        return For(kind=HIRKind.FOR, target=target, iter=iter_, body=body, orelse=orelse)

    def _lower_returnstmt(self, cst: CSTReturnStmt):
        value = self.lower(cst.value) if cst.value else None
        return Return(kind=HIRKind.RETURN, value=value)

    def _lower_yieldstmt(self, cst: CSTYieldStmt):
        value = self.lower(cst.value) if cst.value else None
        return Yield(kind=HIRKind.YIELD, value=value)

    def _lower_yieldfromstmt(self, cst: CSTYieldFromStmt):
        value = self.lower(cst.value)
        return YieldFrom(kind=HIRKind.YIELD_FROM, value=value)

    def _lower_raisestmt(self, cst: CSTRaiseStmt):
        exc = self.lower(cst.exc) if cst.exc else None
        cause = self.lower(cst.cause) if cst.cause else None
        return Raise(kind=HIRKind.RAISE, exc=exc, cause=cause)

    def _lower_trystmt(self, cst: CSTTryStmt):
        body = [self.lower(s) for s in cst.body]
        handlers = [self.lower(h) for h in cst.handlers]
        orelse = [self.lower(s) for s in cst.orelse]
        finalbody = [self.lower(s) for s in cst.finalbody]
        return Try(kind=HIRKind.TRY, body=body, handlers=handlers,
                  orelse=orelse, finalbody=finalbody)

    def _lower_excepthandler(self, cst: CSTExceptHandler):
        type_ = self.lower(cst.type_) if cst.type_ else None
        return ExceptHandler(
            kind=HIRKind.TRY,
            type_=type_,
            name=cst.name,
            body=[self.lower(s) for s in cst.body],
            is_star=cst.is_star,
        )

    def _lower_withstmt(self, cst: CSTWithStmt):
        items = [self.lower(item) for item in cst.items]
        body = [self.lower(s) for s in cst.body]
        return With(kind=HIRKind.WITH, items=items, body=body, is_async=cst.is_async)

    def _lower_withitem(self, cst: CSTWithItem):
        ctx = self.lower(cst.context_expr)
        vars_ = self.lower(cst.optional_vars) if cst.optional_vars else None
        return WithItem(kind=HIRKind.WITH, context_expr=ctx, optional_vars=vars_)

    def _lower_matchstmt(self, cst: CSTMatchStmt):
        subject = self.lower(cst.subject)
        cases = [self.lower(c) for c in cst.cases]
        return Match(kind=HIRKind.MATCH, subject=subject, cases=cases)

    def _lower_caseblock(self, cst: CSTCaseBlock):
        pattern = self.lower(cst.pattern)
        guard = self.lower(cst.guard) if cst.guard else None
        body = [self.lower(s) for s in cst.body]
        return CaseBlock(kind=HIRKind.MATCH, pattern=pattern, guard=guard, body=body)

    def _lower_importstmt(self, cst: CSTImportStmt):
        names = [self.lower(a) for a in cst.names]
        return Import(kind=HIRKind.IMPORT, names=names)

    def _lower_importfromstmt(self, cst: CSTImportFromStmt):
        names = [self.lower(a) for a in cst.names]
        return ImportFrom(
            kind=HIRKind.IMPORT_FROM,
            module=cst.module,
            names=names,
            level=cst.level,
        )

    def _lower_alias(self, cst: CSTAlias):
        return Alias(kind=HIRKind.IMPORT, name=cst.name, asname=cst.asname)

    def _lower_globalstmt(self, cst: CSTGlobalStmt):
        return Global(kind=HIRKind.GLOBAL, names=cst.names)

    def _lower_nonlocalstmt(self, cst: CSTNonlocalStmt):
        return Nonlocal(kind=HIRKind.NONLOCAL, names=cst.names)

    def _lower_exprstmt(self, cst: ExprStmt):
        return self.lower(cst.value)

    def _lower_assign(self, cst: Assign):
        targets = [self.lower(t) for t in cst.targets]
        value = self.lower(cst.value)
        return HIRAssign(kind=HIRKind.ASSIGN, targets=targets, value=value)

    def _lower_annassign(self, cst: AnnAssign):
        target = self.lower(cst.target)
        annotation = self.lower(cst.annotation)
        value = self.lower(cst.value) if cst.value else None
        return HIRAnnAssign(kind=HIRKind.ANN_ASSIGN, target=target,
                            annotation=annotation, value=value)

    def _lower_augassign(self, cst: AugAssign):
        target = self.lower(cst.target)
        value = self.lower(cst.value)
        return HIRAugAssign(kind=HIRKind.AUG_ASSIGN, target=target,
                            op=cst.op, value=value)

    # Expresiones
    def _lower_name(self, cst: CSTName) -> HIRNode:
        if cst.ctx == "Load":
            return Load(kind=HIRKind.LOAD, name=cst.id)
        elif cst.ctx == "Store":
            return Store(kind=HIRKind.STORE, name=cst.id)
        elif cst.ctx == "Del":
            return Delete(kind=HIRKind.DELETE, targets=[Load(kind=HIRKind.LOAD, name=cst.id)])
        return Load(kind=HIRKind.LOAD, name=cst.id)

    def _lower_constant(self, cst: CSTConstant) -> Const:
        return Const(kind=HIRKind.CONST, value=cst.value)

    def _lower_binop(self, cst: CSTBinOp):
        left = self.lower(cst.left)
        right = self.lower(cst.right)
        return BinOp(kind=HIRKind.BINOP, left=left, op=cst.op, right=right)

    def _lower_unaryop(self, cst: CSTUnaryOp):
        operand = self.lower(cst.operand)
        return UnOp(kind=HIRKind.UNOP, op=cst.op, operand=operand)

    def _lower_compare(self, cst: CSTCompare):
        left = self.lower(cst.left)
        comparators = [self.lower(c) for c in cst.comparators]
        return Compare(kind=HIRKind.COMPARE, left=left, ops=cst.ops, comparators=comparators)

    def _lower_boolop(self, cst: CSTBoolOp):
        values = [self.lower(v) for v in cst.values]
        return BoolOp(kind=HIRKind.BOOL_OP, op=cst.op, values=values)

    def _lower_call(self, cst: CSTCall):
        func = self.lower(cst.func)
        args = [self.lower(a) for a in cst.args]
        keywords = [self.lower(k) for k in cst.keywords]
        return Call(kind=HIRKind.CALL, func=func, args=args, keywords=keywords)

    def _lower_keyword(self, cst: CSTKeyword):
        value = self.lower(cst.value)
        return Keyword(kind=HIRKind.CALL, arg=cst.arg, value=value)

    def _lower_attribute(self, cst: CSTAttribute):
        value = self.lower(cst.value)
        return Attr(kind=HIRKind.ATTR, value=value, attr=cst.attr, ctx=cst.ctx)

    def _lower_subscript(self, cst: CSTSubscript):
        value = self.lower(cst.value)
        slice_ = self.lower(cst.slice)
        return Subscr(kind=HIRKind.SUBSCR, value=value, slice=slice_, ctx=cst.ctx)

    def _lower_slice(self, cst: CSTSlice):
        lower = self.lower(cst.lower) if cst.lower else None
        upper = self.lower(cst.upper) if cst.upper else None
        step = self.lower(cst.step) if cst.step else None
        node = HIRNode(kind=HIRKind.MODULE)  # placeholder
        node.annotations["slice"] = {"lower": lower, "upper": upper, "step": step}
        return node

    def _lower_list(self, cst: CSTList):
        elts = [self.lower(e) for e in cst.elts]
        return List(kind=HIRKind.LIST, elts=elts, ctx=cst.ctx)

    def _lower_tuple(self, cst: CSTTuple):
        elts = [self.lower(e) for e in cst.elts]
        return Tuple(kind=HIRKind.TUPLE, elts=elts, ctx=cst.ctx)

    def _lower_set(self, cst: CSTSet):
        elts = [self.lower(e) for e in cst.elts]
        return Set(kind=HIRKind.SET, elts=elts)

    def _lower_dict(self, cst: CSTDict):
        keys = [self.lower(k) if k else None for k in cst.keys]
        values = [self.lower(v) for v in cst.values]
        return Dict(kind=HIRKind.DICT, keys=keys, values=values)

    def _lower_listcomp(self, cst: CSTListComp):
        elt = self.lower(cst.elt)
        generators = [self.lower(g) for g in cst.generators]
        return ListComp(kind=HIRKind.LIST_COMP, elt=elt, generators=generators)

    def _lower_compfor(self, cst: CSTCompFor):
        target = self.lower(cst.target)
        iter_ = self.lower(cst.iter)
        ifs = [self.lower(i) for i in cst.ifs]
        return CompFor(kind=HIRKind.FOR, target=target, iter=iter_, ifs=ifs, is_async=cst.is_async)

    def _lower_ifexpr(self, cst: CSTIfExpr):
        test = self.lower(cst.test)
        body = self.lower(cst.body)
        orelse = self.lower(cst.orelse)
        return IfExpr(kind=HIRKind.IF_EXPR, test=test, body=body, orelse=orelse)

    def _lower_await(self, cst: CWAwait):
        value = self.lower(cst.value)
        return Await(kind=HIRKind.AWAIT, value=value)


def lower_cst_to_hir(cst: CSTNode) -> HIRNode:
    """Función de conveniencia."""
    lowerer = Lowerer()
    return lowerer.lower(cst)
