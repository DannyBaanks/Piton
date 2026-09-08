# piton/analysis.py
"""Análisis de scopes y bindings para Pitón."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Any
from piton.cst import (
    CSTNode, Module, FuncDef, ClassDef, Lambda, Name,
    Assign, AnnAssign, AugAssign, ForStmt, WithItem,
    ImportStmt, ImportFromStmt, GlobalStmt, NonlocalStmt,
    ExceptHandler, Arguments, Arg, CompFor,
    MatchStmt, CaseBlock,
)
from piton.cst import CSTNodeType


@dataclass
class Scope:
    """Un scope anidado (módulo, función, clase, comprensión)."""
    name: str
    parent: Optional["Scope"] = None
    children: List["Scope"] = field(default_factory=list)
    bindings: Dict[str, "Binding"] = field(default_factory=dict)
    read_before_write: Set[str] = field(default_factory=set)
    is_function: bool = False
    is_class: bool = False
    is_comprehension: bool = False

    def __post_init__(self):
        if self.parent:
            self.parent.children.append(self)

    def resolve(self, name: str) -> Optional["Binding"]:
        """Buscar binding en este scope o ancestros."""
        if name in self.bindings:
            return self.bindings[name]
        if self.parent:
            return self.parent.resolve(name)
        return None

    def define(self, name: str, kind: str, node: CSTNode) -> "Binding":
        """Definir un nombre en este scope."""
        if name in self.bindings:
            # Re-definición en mismo scope (shadowing)
            pass
        binding = Binding(name=name, kind=kind, scope=self, node=node)
        self.bindings[name] = binding
        return binding

    def add_read(self, name: str, node: CSTNode):
        """Registrar lectura de un nombre."""
        binding = self.resolve(name)
        if binding:
            binding.reads.append(node)
        else:
            # Nombre no definido en scopes visibles
            self.read_before_write.add(name)


@dataclass
class Binding:
    name: str
    kind: str  # "param", "local", "global", "nonlocal", "import", "class", "function"
    scope: Scope
    node: CSTNode
    reads: List[CSTNode] = field(default_factory=list)
    writes: List[CSTNode] = field(default_factory=list)

    def is_local(self) -> bool:
        return self.kind in ("param", "local")

    def is_global(self) -> bool:
        return self.kind == "global"

    def is_nonlocal(self) -> bool:
        return self.kind == "nonlocal"


@dataclass
class AnalysisResult:
    module_scope: Scope
    all_scopes: List[Scope]
    unbound_names: Set[str]
    errors: List[str]


class ScopeAnalyzer:
    """Analiza scopes y bindings en el CST."""

    def __init__(self):
        self.current_scope: Optional[Scope] = None
        self.all_scopes: List[Scope] = []
        self.errors: List[str] = []

    def analyze(self, module: Module) -> AnalysisResult:
        self.current_scope = Scope(name="<module>", parent=None)
        self.all_scopes = [self.current_scope]
        self.errors = []

        self._visit(module)

        # Recopilar nombres no resueltos
        unbound = set()
        for scope in self.all_scopes:
            for name in scope.read_before_write:
                if not any(name in s.bindings for s in self._ancestors(scope)):
                    unbound.add(name)

        return AnalysisResult(
            module_scope=self.current_scope,
            all_scopes=self.all_scopes,
            unbound_names=unbound,
            errors=self.errors,
        )

    def _ancestors(self, scope: Scope) -> List[Scope]:
        result = []
        while scope.parent:
            result.append(scope.parent)
            scope = scope.parent
        return result

    def _enter_scope(self, name: str, is_function: bool = False,
                     is_class: bool = False, is_comprehension: bool = False) -> Scope:
        new_scope = Scope(
            name=name,
            parent=self.current_scope,
            is_function=is_function,
            is_class=is_class,
            is_comprehension=is_comprehension,
        )
        self.all_scopes.append(new_scope)
        old = self.current_scope
        self.current_scope = new_scope
        return old

    def _exit_scope(self, old_scope: Scope):
        self.current_scope = old_scope

    def _visit(self, node: CSTNode):
        method_name = f"_visit_{node.type.name.lower()}"
        method = getattr(self, method_name, None)
        if method is None:
            compact_name = f"_visit_{node.type.name.lower().replace('_', '')}"
            method = getattr(self, compact_name, self._visit_generic)
        method(node)

    def _visit_generic(self, node: CSTNode):
        for attr in dir(node):
            if attr.startswith("_"):
                continue
            val = getattr(node, attr)
            if isinstance(val, CSTNode):
                self._visit(val)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, CSTNode):
                        self._visit(item)

    def _visit_module(self, node: Module):
        for stmt in node.body:
            self._visit(stmt)

    def _visit_funcdef(self, node: FuncDef):
        # La función se define en el scope actual
        self.current_scope.define(node.name, "function", node)

        # Nuevo scope para la función
        old = self._enter_scope(node.name, is_function=True)

        # Parámetros son locales
        for arg in node.args.args:
            self.current_scope.define(arg.arg, "param", arg)
        if node.args.vararg:
            self.current_scope.define(node.args.vararg.arg, "param", node.args.vararg)
        if node.args.kwarg:
            self.current_scope.define(node.args.kwarg.arg, "param", node.args.kwarg)

        # Body
        for stmt in node.body:
            self._visit(stmt)

        self._exit_scope(old)

    def _visit_classdef(self, node: ClassDef):
        # La clase se define en el scope actual
        self.current_scope.define(node.name, "class", node)

        # Nuevo scope para la clase (no crea scope para métodos en Python)
        old = self._enter_scope(node.name, is_class=True)

        for stmt in node.body:
            self._visit(stmt)

        self._exit_scope(old)

    def _visit_lambda(self, node: Lambda):
        old = self._enter_scope("<lambda>", is_function=True)
        for arg in node.args.args:
            self.current_scope.define(arg.arg, "param", arg)
        self._visit(node.body)
        self._exit_scope(old)

    def _visit_assign(self, node: Assign):
        self._visit(node.value)
        for target in node.targets:
            self._visit_target(target, "local")

    def _visit_annassign(self, node: AnnAssign):
        if node.annotation:
            self._visit(node.annotation)
        if node.value:
            self._visit(node.value)
        self._visit_target(node.target, "local")

    def _visit_augassign(self, node: AugAssign):
        self._visit(node.value)
        self._visit_target(node.target, "local")

    def _visit_target(self, node: CSTNode, kind: str):
        if isinstance(node, Name):
            if node.ctx == "Store":
                self.current_scope.define(node.id, kind, node)
            elif node.ctx == "Del":
                pass  # delete
        elif isinstance(node, (Tuple, List)):
            for elt in node.elts:
                self._visit_target(elt, kind)
        elif isinstance(node, Attribute):
            pass  # attribute assignment
        elif isinstance(node, Subscript):
            pass  # subscript assignment

    def _visit_forstmt(self, node: ForStmt):
        self._visit(node.iter)
        self._visit_target(node.target, "local")
        for stmt in node.body:
            self._visit(stmt)
        for stmt in node.orelse:
            self._visit(stmt)

    def _visit_withstmt(self, node: WithStmt):
        for item in node.items:
            self._visit(item.context_expr)
            if item.optional_vars:
                self._visit_target(item.optional_vars, "local")
        for stmt in node.body:
            self._visit(stmt)

    def _visit_importstmt(self, node: ImportStmt):
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            self.current_scope.define(name, "import", node)

    def _visit_importfromstmt(self, node: ImportFromStmt):
        for alias in node.names:
            name = alias.asname or alias.name
            self.current_scope.define(name, "import", node)

    def _visit_globalstmt(self, node: GlobalStmt):
        for name in node.names:
            # Marcar en scope de módulo
            module_scope = self.current_scope
            while module_scope.parent:
                module_scope = module_scope.parent
            if name in module_scope.bindings:
                module_scope.bindings[name].kind = "global"
            else:
                module_scope.define(name, "global", node)

    def _visit_nonlocalstmt(self, node: NonlocalStmt):
        for name in node.names:
            # Buscar en scopes padre (no módulo)
            parent = self.current_scope.parent
            found = False
            while parent and not parent.is_function:
                parent = parent.parent
            if parent:
                if name in parent.bindings:
                    parent.bindings[name].kind = "nonlocal"
                else:
                    parent.define(name, "nonlocal", node)
            else:
                self.errors.append(f"nonlocal '{name}' no encontrado en scope envolvente")

    def _visit_excepthandler(self, node: ExceptHandler):
        if node.name:
            self.current_scope.define(node.name, "local", node)
        for stmt in node.body:
            self._visit(stmt)

    def _visit_name(self, node: Name):
        if node.ctx == "Load":
            self.current_scope.add_read(node.id, node)
        elif node.ctx == "Store":
            self.current_scope.define(node.id, "local", node)

    def _visit_comprehension(self, node: CompFor):
        old = self._enter_scope(f"<comp:{node.line}>", is_comprehension=True)
        self._visit_target(node.target, "local")
        self._visit(node.iter)
        for if_ in node.ifs:
            self._visit(if_)
        self._exit_scope(old)


def analyze_scopes(module: Module) -> AnalysisResult:
    """Función de conveniencia."""
    analyzer = ScopeAnalyzer()
    return analyzer.analyze(module)
