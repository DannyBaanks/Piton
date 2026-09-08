# piton/backend_python.py
"""Backend Python: genera código Python desde HIR (o CST)."""
from __future__ import annotations

from typing import List, Optional
from piton.hir import (
    HIRNode, Module, FuncDef, ClassDef, Arguments,
    If, While, For, Return, Yield, YieldFrom, Raise,
    Try, ExceptHandler, With, WithItem, Match, CaseBlock,
    Load, Store, Delete, Global, Nonlocal,
    Const, BinOp, UnOp, Compare, BoolOp, Call, Keyword,
    Attr, Subscr, List, Tuple, Set, Dict,
    ListComp, CompFor, IfExpr, Await,
    Import, ImportFrom, Alias,
    HIRKind,
)


class PythonBackend:
    """Genera código Python 3.12+ desde HIR."""

    def __init__(self):
        self.indent = 0
        self.lines: List[str] = []

    def generate(self, node: HIRNode) -> str:
        self.indent = 0
        self.lines = []
        self._visit(node)
        return "\n".join(self.lines)

    def _emit(self, line: str = ""):
        self.lines.append("    " * self.indent + line)

    def _visit(self, node: HIRNode):
        method_name = f"_gen_{node.kind.name.lower()}"
        method = getattr(self, method_name, self._gen_generic)
        method(node)

    def _gen_generic(self, node: HIRNode):
        self._emit(f"# TODO: {node.kind.name}")

    def _gen_module(self, node: Module):
        for stmt in node.body:
            self._visit(stmt)

    def _gen_funcdef(self, node: FuncDef):
        decorators = "".join(f"@{self._expr_to_str(d)}\n" for d in node.decorators)
        async_kw = "async " if node.is_async else ""
        args = self._format_args(node.args)
        returns = f" -> {self._expr_to_str(node.returns)}" if node.returns else ""
        self._emit(f"{decorators}{async_kw}def {node.name}({args}){returns}:")
        self.indent += 1
        if not node.body:
            self._emit("pass")
        else:
            for stmt in node.body:
                self._visit(stmt)
        self.indent -= 1

    def _gen_classdef(self, node: ClassDef):
        decorators = "".join(f"@{self._expr_to_str(d)}\n" for d in node.decorators)
        bases = ", ".join(self._expr_to_str(b) for b in node.bases) if node.bases else ""
        class_line = f"class {node.name}"
        if bases:
            class_line += f"({bases})"
        class_line += ":"
        self._emit(f"{decorators}{class_line}")
        self.indent += 1
        if not node.body:
            self._emit("pass")
        else:
            for stmt in node.body:
                self._visit(stmt)
        self.indent -= 1

    def _format_args(self, args: Arguments) -> str:
        parts = []
        for a in args.args:
            parts.append(a)
        if args.vararg:
            parts.append(f"*{args.vararg}")
        for a in args.kwonlyargs:
            parts.append(a)
        if args.kwarg:
            parts.append(f"**{args.kwarg}")
        return ", ".join(parts)

    def _gen_if(self, node: If):
        self._emit(f"if {self._expr_to_str(node.test)}:")
        self.indent += 1
        for s in node.body:
            self._visit(s)
        self.indent -= 1
        if node.orelse:
            self._emit("else:")
            self.indent += 1
            for s in node.orelse:
                self._visit(s)
            self.indent -= 1

    def _gen_while(self, node: While):
        self._emit(f"while {self._expr_to_str(node.test)}:")
        self.indent += 1
        for s in node.body:
            self._visit(s)
        self.indent -= 1
        if node.orelse:
            self._emit("else:")
            self.indent += 1
            for s in node.orelse:
                self._visit(s)
            self.indent -= 1

    def _gen_for(self, node: For):
        async_kw = "async " if node.is_async else ""
        self._emit(f"{async_kw}for {self._expr_to_str(node.target)} in {self._expr_to_str(node.iter)}:")
        self.indent += 1
        for s in node.body:
            self._visit(s)
        self.indent -= 1
        if node.orelse:
            self._emit("else:")
            self.indent += 1
            for s in node.orelse:
                self._visit(s)
            self.indent -= 1

    def _gen_return(self, node: Return):
        if node.value:
            self._emit(f"return {self._expr_to_str(node.value)}")
        else:
            self._emit("return")

    def _gen_yield(self, node: Yield):
        if node.value:
            self._emit(f"yield {self._expr_to_str(node.value)}")
        else:
            self._emit("yield")

    def _gen_yieldfrom(self, node: YieldFrom):
        self._emit(f"yield from {self._expr_to_str(node.value)}")

    def _gen_raise(self, node: Raise):
        if node.exc:
            if node.cause:
                self._emit(f"raise {self._expr_to_str(node.exc)} from {self._expr_to_str(node.cause)}")
            else:
                self._emit(f"raise {self._expr_to_str(node.exc)}")
        else:
            self._emit("raise")

    def _gen_try(self, node: Try):
        self._emit("try:")
        self.indent += 1
        for s in node.body:
            self._visit(s)
        self.indent -= 1
        for h in node.handlers:
            self._visit(h)
        if node.orelse:
            self._emit("else:")
            self.indent += 1
            for s in node.orelse:
                self._visit(s)
            self.indent -= 1
        if node.finalbody:
            self._emit("finally:")
            self.indent += 1
            for s in node.finalbody:
                self._visit(s)
            self.indent -= 1

    def _gen_excephandler(self, node: ExceptHandler):
        if node.is_star:
            self._emit("except*:")
        elif node.type_:
            if node.name:
                self._emit(f"except {self._expr_to_str(node.type_)} as {node.name}:")
            else:
                self._emit(f"except {self._expr_to_str(node.type_)}:")
        else:
            self._emit("except:")
        self.indent += 1
        for s in node.body:
            self._visit(s)
        self.indent -= 1

    def _gen_with(self, node: With):
        async_kw = "async " if node.is_async else ""
        items = ", ".join(self._visit_with_item(i) for i in node.items)
        self._emit(f"{async_kw}with {items}:")
        self.indent += 1
        for s in node.body:
            self._visit(s)
        self.indent -= 1

    def _visit_with_item(self, item: WithItem) -> str:
        ctx = self._expr_to_str(item.context_expr)
        if item.optional_vars:
            return f"{ctx} as {self._expr_to_str(item.optional_vars)}"
        return ctx

    def _gen_match(self, node: Match):
        self._emit(f"match {self._expr_to_str(node.subject)}:")
        self.indent += 1
        for c in node.cases:
            self._visit(c)
        self.indent -= 1

    def _gen_caseblock(self, node: CaseBlock):
        pattern = self._pattern_to_str(node.pattern)
        guard = f" if {self._expr_to_str(node.guard)}" if node.guard else ""
        self._emit(f"case {pattern}{guard}:")
        self.indent += 1
        for s in node.body:
            self._visit(s)
        self.indent -= 1

    def _gen_load(self, node: Load) -> str:
        return node.name

    def _gen_store(self, node: Store) -> str:
        return node.name

    def _gen_delete(self, node: Delete):
        targets = ", ".join(self._expr_to_str(t) for t in node.targets)
        self._emit(f"del {targets}")

    def _gen_global(self, node: Global):
        self._emit(f"global {', '.join(node.names)}")

    def _gen_nonlocal(self, node: Nonlocal):
        self._emit(f"nonlocal {', '.join(node.names)}")

    def _gen_const(self, node: Const) -> str:
        if node.value is None:
            return "None"
        if isinstance(node.value, bool):
            return "True" if node.value else "False"
        if isinstance(node.value, str):
            return repr(node.value)
        return str(node.value)

    def _gen_binop(self, node: BinOp) -> str:
        return f"({self._expr_to_str(node.left)} {node.op} {self._expr_to_str(node.right)})"

    def _gen_unop(self, node: UnOp) -> str:
        return f"({node.op}{self._expr_to_str(node.operand)})"

    def _gen_compare(self, node: Compare) -> str:
        parts = [self._expr_to_str(node.left)]
        for op, comp in zip(node.ops, node.comparators):
            parts.append(op)
            parts.append(self._expr_to_str(comp))
        return " ".join(parts)

    def _gen_boolop(self, node: BoolOp) -> str:
        return f"({f' {node.op} '.join(self._expr_to_str(v) for v in node.values)})"

    def _gen_call(self, node: Call) -> str:
        func = self._expr_to_str(node.func)
        args = [self._expr_to_str(a) for a in node.args]
        kw = [f"{k.arg}={self._expr_to_str(k.value)}" for k in node.keywords if k.arg]
        for k in node.keywords:
            if k.arg is None:
                args.append(f"**{self._expr_to_str(k.value)}")
            elif k.arg == "":
                args.append(f"*{self._expr_to_str(k.value)}")
        return f"{func}({', '.join(args + kw)})"

    def _gen_attr(self, node: Attr) -> str:
        return f"{self._expr_to_str(node.value)}.{node.attr}"

    def _gen_subscr(self, node: Subscr) -> str:
        return f"{self._expr_to_str(node.value)}[{self._expr_to_str(node.slice)}]"

    def _gen_list(self, node: List) -> str:
        elts = ", ".join(self._expr_to_str(e) for e in node.elts)
        return f"[{elts}]"

    def _gen_tuple(self, node: Tuple) -> str:
        elts = ", ".join(self._expr_to_str(e) for e in node.elts)
        if len(node.elts) == 1:
            return f"({elts},)"
        return f"({elts})"

    def _gen_set(self, node: Set) -> str:
        elts = ", ".join(self._expr_to_str(e) for e in node.elts)
        return f"{{{elts}}}"

    def _gen_dict(self, node: Dict) -> str:
        pairs = []
        for k, v in zip(node.keys, node.values):
            if k is None:
                pairs.append(f"**{self._expr_to_str(v)}")
            else:
                pairs.append(f"{self._expr_to_str(k)}: {self._expr_to_str(v)}")
        return f"{{{', '.join(pairs)}}}"

    def _gen_listcomp(self, node: ListComp) -> str:
        elt = self._expr_to_str(node.elt)
        gens = " ".join(self._compfor_to_str(g) for g in node.generators)
        return f"[{elt} {gens}]"

    def _compfor_to_str(self, node: CompFor) -> str:
        async_kw = "async " if node.is_async else ""
        target = self._expr_to_str(node.target)
        iter_ = self._expr_to_str(node.iter)
        ifs = " ".join(f"if {self._expr_to_str(i)}" for i in node.ifs)
        return f"{async_kw}for {target} in {iter_} {ifs}"

    def _gen_ifexpr(self, node: IfExpr) -> str:
        return f"{self._expr_to_str(node.body)} if {self._expr_to_str(node.test)} else {self._expr_to_str(node.orelse)}"

    def _gen_await(self, node: Await) -> str:
        return f"await {self._expr_to_str(node.value)}"

    def _gen_import(self, node: Import):
        names = ", ".join(self._alias_to_str(a) for a in node.names)
        self._emit(f"import {names}")

    def _gen_importfrom(self, node: ImportFrom):
        level = "." * node.level
        module = f"{level}{node.module}" if node.module else level
        names = ", ".join(self._alias_to_str(a) for a in node.names)
        self._emit(f"from {module} import {names}")

    def _alias_to_str(self, a: Alias) -> str:
        return f"{a.name} as {a.asname}" if a.asname else a.name

    def _expr_to_str(self, node: Optional[HIRNode]) -> str:
        if node is None:
            return ""
        method_name = f"_gen_{node.kind.name.lower()}"
        method = getattr(self, method_name, None)
        if method:
            return method(node)
        return f"<{node.kind.name}>"

    def _pattern_to_str(self, node: HIRNode) -> str:
        # Simplificado
        return self._expr_to_str(node)


def generate_python_from_hir(hir: HIRNode) -> str:
    backend = PythonBackend()
    return backend.generate(hir)


# También un backend directo CST -> Python para testing rápido
class CSTPythonBackend:
    """Genera Python directamente desde CST (para testing)."""

    def __init__(self):
        self.indent = 0
        self.lines: List[str] = []

    def generate(self, node) -> str:
        self.indent = 0
        self.lines = []
        self._visit(node)
        return "\n".join(self.lines)

    def _emit(self, line: str = ""):
        self.lines.append("    " * self.indent + line)

    def _visit(self, node):
        if node is None:
            return
        method_name = f"_visit_{node.type.name.lower()}"
        method = getattr(self, method_name, self._visit_generic)
        return method(node)

    def _visit_generic(self, node):
        for attr in dir(node):
            if attr.startswith("_"):
                continue
            val = getattr(node, attr)
            if hasattr(val, "type"):  # CSTNode
                self._visit(val)
            elif isinstance(val, list):
                for item in val:
                    if hasattr(item, "type"):
                        self._visit(item)

    def _visit_module(self, node):
        for stmt in node.body:
            self._visit(stmt)

    def _visit_funcdef(self, node):
        dec = "".join(f"@{self._visit(d)}\n" for d in node.decorators)
        async_kw = "async " if node.is_async else ""
        args = self._format_args(node.args)
        ret = f" -> {self._visit(node.returns)}" if node.returns else ""
        self._emit(f"{dec}{async_kw}def {node.name}({args}){ret}:")
        self.indent += 1
        if not node.body:
            self._emit("pass")
        else:
            for s in node.body:
                self._visit(s)
        self.indent -= 1

    def _format_args(self, args):
        parts = []
        for a in args.args:
            ann = f": {self._visit(a.annotation)}" if a.annotation else ""
            parts.append(f"{a.arg}{ann}")
        if args.vararg:
            parts.append(f"*{args.vararg.arg}")
        for a in args.kwonlyargs:
            ann = f": {self._visit(a.annotation)}" if a.annotation else ""
            parts.append(f"{a.arg}{ann}")
        if args.kwarg:
            parts.append(f"**{args.kwarg.arg}")
        return ", ".join(parts)

    def _visit_classdef(self, node):
        dec = "".join(f"@{self._visit(d)}\n" for d in node.decorators)
        bases = ", ".join(self._visit(b) for b in node.bases) if node.bases else ""
        line = f"class {node.name}"
        if bases:
            line += f"({bases})"
        line += ":"
        self._emit(f"{dec}{line}")
        self.indent += 1
        if not node.body:
            self._emit("pass")
        else:
            for s in node.body:
                self._visit(s)
        self.indent -= 1

    def _visit_ifstmt(self, node):
        self._emit(f"if {self._visit(node.test)}:")
        self.indent += 1
        for s in node.body:
            self._visit(s)
        self.indent -= 1
        if node.orelse:
            self._emit("else:")
            self.indent += 1
            for s in node.orelse:
                self._visit(s)
            self.indent -= 1

    def _visit_whilestmt(self, node):
        self._emit(f"while {self._visit(node.test)}:")
        self.indent += 1
        for s in node.body:
            self._visit(s)
        self.indent -= 1
        if node.orelse:
            self._emit("else:")
            self.indent += 1
            for s in node.orelse:
                self._visit(s)
            self.indent -= 1

    def _visit_forstmt(self, node):
        async_kw = "async " if node.is_async else ""
        self._emit(f"{async_kw}for {self._visit(node.target)} in {self._visit(node.iter)}:")
        self.indent += 1
        for s in node.body:
            self._visit(s)
        self.indent -= 1
        if node.orelse:
            self._emit("else:")
            self.indent += 1
            for s in node.orelse:
                self._visit(s)
            self.indent -= 1

    def _visit_returnstmt(self, node):
        if node.value:
            self._emit(f"return {self._visit(node.value)}")
        else:
            self._emit("return")

    def _visit_assign(self, node):
        targets = ", ".join(self._visit(t) for t in node.targets)
        self._emit(f"{targets} = {self._visit(node.value)}")

    def _visit_exprstmt(self, node):
        self._emit(self._visit(node.value))

    def _visit_name(self, node):
        return node.id

    def _visit_constant(self, node):
        if node.value is None:
            return "None"
        if isinstance(node.value, bool):
            return "True" if node.value else "False"
        if isinstance(node.value, str):
            return repr(node.value)
        return str(node.value)

    def _visit_binop(self, node):
        return f"({self._visit(node.left)} {node.op} {self._visit(node.right)})"

    def _visit_compare(self, node):
        parts = [self._visit(node.left)]
        for op, comp in zip(node.ops, node.comparators):
            parts.append(op)
            parts.append(self._visit(comp))
        return " ".join(parts)

    def _visit_call(self, node):
        func = self._visit(node.func)
        args = [self._visit(a) for a in node.args]
        kw = [f"{k.arg}={self._visit(k.value)}" for k in node.keywords if k.arg]
        for k in node.keywords:
            if k.arg is None:
                args.append(f"**{self._visit(k.value)}")
        return f"{func}({', '.join(args + kw)})"

    def _visit_attribute(self, node):
        return f"{self._visit(node.value)}.{node.attr}"

    def _visit_subscript(self, node):
        return f"{self._visit(node.value)}[{self._visit(node.slice)}]"

    def _visit_list(self, node):
        elts = ", ".join(self._visit(e) for e in node.elts)
        return f"[{elts}]"

    def _visit_tuple(self, node):
        elts = ", ".join(self._visit(e) for e in node.elts)
        if len(node.elts) == 1:
            return f"({elts},)"
        return f"({elts})"

    def _visit_set(self, node):
        elts = ", ".join(self._visit(e) for e in node.elts)
        return f"{{{elts}}}"

    def _visit_dict(self, node):
        pairs = []
        for k, v in zip(node.keys, node.values):
            if k is None:
                pairs.append(f"**{self._visit(v)}")
            else:
                pairs.append(f"{self._visit(k)}: {self._visit(v)}")
        return f"{{{', '.join(pairs)}}}"

    def _visit_importstmt(self, node):
        names = ", ".join(self._visit(a) for a in node.names)
        self._emit(f"import {names}")

    def _visit_importfromstmt(self, node):
        level = "." * node.level
        module = f"{level}{node.module}" if node.module else level
        names = ", ".join(self._visit(a) for a in node.names)
        self._emit(f"from {module} import {names}")

    def _visit_alias(self, node):
        return f"{node.name} as {node.asname}" if node.asname else node.name

    def _visit_globalstmt(self, node):
        self._emit(f"global {', '.join(node.names)}")

    def _visit_nonlocalstmt(self, node):
        self._emit(f"nonlocal {', '.join(node.names)}")

    def _visit_raisestmt(self, node):
        if node.value:
            self._emit(f"raise {self._visit(node.value)}")
        else:
            self._emit("raise")

    def _visit_trystmt(self, node):
        self._emit("try:")
        self.indent += 1
        for s in node.body:
            self._visit(s)
        self.indent -= 1
        for h in node.handlers:
            self._visit(h)
        if node.orelse:
            self._emit("else:")
            self.indent += 1
            for s in node.orelse:
                self._visit(s)
            self.indent -= 1
        if node.finalbody:
            self._emit("finally:")
            self.indent += 1
            for s in node.finalbody:
                self._visit(s)
            self.indent -= 1

    def _visit_excephandler(self, node):
        if node.is_star:
            self._emit("except*:")
        elif node.type_:
            if node.name:
                self._emit(f"except {self._visit(node.type_)} as {node.name}:")
            else:
                self._emit(f"except {self._visit(node.type_)}:")
        else:
            self._emit("except:")
        self.indent += 1
        for s in node.body:
            self._visit(s)
        self.indent -= 1

    def _visit_withstmt(self, node):
        async_kw = "async " if node.is_async else ""
        items = ", ".join(self._visit(i) for i in node.items)
        self._emit(f"{async_kw}with {items}:")
        self.indent += 1
        for s in node.body:
            self._visit(s)
        self.indent -= 1

    def _visit_withitem(self, node):
        ctx = self._visit(node.context_expr)
        if node.optional_vars:
            return f"{ctx} as {self._visit(node.optional_vars)}"
        return ctx

    def _visit_alias(self, node):
        return f"{node.name} as {node.asname}" if node.asname else node.name


def generate_python_from_cst(cst) -> str:
    """Función de conveniencia."""
    backend = CSTPythonBackend()
    return backend.generate(cst)