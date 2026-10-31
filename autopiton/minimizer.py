"""
Failure Minimizer Module for AutoPiton

Structurally reduces a divergent Python program while re-executing both
engines after every candidate reduction. A reduction is accepted only when
the divergence kind is preserved exactly:

    (classification, CPython status, CPython exception type,
     PITON stage, PITON exception type)

The minimizer never edits PITON and never relaxes the comparator: it only
uses whatever ``run_fn`` returns.
"""

from __future__ import annotations

import ast
import copy
import operator
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from .comparator import Classification, ComparisonResult

PRESERVABLE_CLASSES = {
    Classification.DIVERGENT.value,
    Classification.UNSUPPORTED.value,
    Classification.INVALID_ACCEPTED.value,
    Classification.CRASH.value,
    Classification.TIMEOUT.value,
    Classification.NONDETERMINISTIC.value,
}

Kind = Tuple[str, str, str, str, str]

_SAFE_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}


def divergence_kind(comparison: ComparisonResult) -> Kind:
    """Exact category of divergence that a reduction must preserve."""
    cpython = comparison.cpython
    piton = comparison.piton
    if piton is None:
        piton_stage = "missing"
    elif piton.executed:
        piton_stage = "EXECUTE"
    elif piton.failed_stage is not None:
        piton_stage = piton.failed_stage.value
    else:
        piton_stage = "missing"
    return (
        comparison.classification.value,
        cpython.status if cpython else "missing",
        (cpython.exception_type or "") if cpython else "",
        piton_stage,
        (piton.exception_type or "") if piton else "",
    )


@dataclass
class MinimizationResult:
    original_source: str
    minimized_source: str
    original_classification: str
    minimized_classification: str
    preserved_kind: Kind
    attempts: int = 0
    accepted: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)
    stopped_reason: str = ""
    budget_exhausted: bool = False

    @property
    def changed(self) -> bool:
        return self.minimized_source.strip() != self.original_source.strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_source": self.original_source,
            "minimized_source": self.minimized_source,
            "original_classification": self.original_classification,
            "minimized_classification": self.minimized_classification,
            "preserved_kind": list(self.preserved_kind),
            "attempts": self.attempts,
            "accepted": self.accepted,
            "changed": self.changed,
            "history": self.history,
            "stopped_reason": self.stopped_reason,
            "budget_exhausted": self.budget_exhausted,
        }


# --------------------------------------------------------------- AST helpers

_STATEMENT_FIELDS = ("body", "orelse", "finalbody")


def _statement_lists(node: ast.AST, path: Tuple[Any, ...] = ()) -> Iterator[Tuple[Tuple[Any, ...], List[ast.stmt]]]:
    for field_name in _STATEMENT_FIELDS:
        seq = getattr(node, field_name, None)
        if isinstance(seq, list) and seq and all(isinstance(item, ast.stmt) for item in seq):
            yield path + (field_name,), seq
    for field_name in _STATEMENT_FIELDS:
        seq = getattr(node, field_name, None)
        if isinstance(seq, list):
            for index, stmt in enumerate(seq):
                if isinstance(stmt, ast.stmt):
                    yield from _statement_lists(stmt, path + (field_name, index))
    for index, handler in enumerate(getattr(node, "handlers", None) or []):
        yield from _statement_lists(handler, path + ("handlers", index))


def _list_at(tree: ast.AST, path: Tuple[Any, ...]) -> List[ast.stmt]:
    node = tree
    for offset in range(0, len(path) - 1, 2):
        node = getattr(node, path[offset])[path[offset + 1]]
    return getattr(node, path[-1])


def _calls(node: ast.AST) -> Iterator[ast.Call]:
    if isinstance(node, ast.Call):
        yield node
    for child in ast.iter_child_nodes(node):
        yield from _calls(child)


def _constants(node: ast.AST) -> Iterator[ast.Constant]:
    if isinstance(node, ast.Constant):
        yield node
    for child in ast.iter_child_nodes(node):
        yield from _constants(child)


class _DropCallPart(ast.NodeTransformer):
    def __init__(self, target: int, part: str, index: int):
        self.target = target
        self.part = part
        self.index = index
        self.counter = 0

    def visit_Call(self, node: ast.Call) -> ast.AST:
        current = self.counter
        self.counter += 1
        self.generic_visit(node)
        if current == self.target:
            if self.part == "arg":
                del node.args[self.index]
            else:
                del node.keywords[self.index]
        return node


class _StripDecorators(ast.NodeTransformer):
    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        node.decorator_list = []
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        self.generic_visit(node)
        node.decorator_list = []
        return node


class _CollapseIf(ast.NodeTransformer):
    def visit_If(self, node: ast.If) -> Any:
        self.generic_visit(node)
        if isinstance(node.test, ast.Constant) and not isinstance(node.test.value, (str, bytes)):
            return node.body if node.test.value else node.orelse
        return node


class _FoldConstants(ast.NodeTransformer):
    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        if not (isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant)):
            return node
        handler = _SAFE_BINOPS.get(type(node.op))
        if handler is None:
            return node
        left, right = node.left.value, node.right.value
        if not all(isinstance(value, (int, float)) for value in (left, right)):
            return node
        try:
            value = handler(left, right)
        except (ArithmeticError, ValueError):
            return node
        if isinstance(value, (int, float)) and abs(value) < 10**9:
            return ast.Constant(value=value)
        return node


class _ZeroNthConstant(ast.NodeTransformer):
    def __init__(self, target: int):
        self.target = target
        self.counter = 0

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        current = self.counter
        self.counter += 1
        if current == self.target and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return ast.Constant(value=0)
        return node


def _unparse(tree: ast.AST) -> Optional[str]:
    try:
        text = ast.unparse(tree).strip()
    except (ValueError, TypeError):
        return None
    return text + "\n" if text else None


def iter_candidates(source: str) -> Iterator[Tuple[str, str]]:
    """Yield (description, candidate source) in a deterministic order."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return

    normalized = _unparse(tree)
    if normalized and normalized.strip() != source.strip():
        yield "normalize formatting", normalized

    for path, seq in list(_statement_lists(tree)):
        if len(seq) <= 1:
            continue
        for index in range(len(seq)):
            new_tree = copy.deepcopy(tree)
            del _list_at(new_tree, path)[index]
            text = _unparse(new_tree)
            if text:
                yield f"remove {type(seq[index]).__name__} statement", text

    calls = list(_calls(tree))
    for call_index, call in enumerate(calls):
        for arg_index in range(len(call.args)):
            new_tree = copy.deepcopy(tree)
            _DropCallPart(call_index, "arg", arg_index).visit(new_tree)
            text = _unparse(new_tree)
            if text:
                yield f"drop argument {arg_index} of call {call_index}", text
        for kw_index in range(len(call.keywords)):
            new_tree = copy.deepcopy(tree)
            _DropCallPart(call_index, "keyword", kw_index).visit(new_tree)
            text = _unparse(new_tree)
            if text:
                yield f"drop keyword {kw_index} of call {call_index}", text

    if any(getattr(node, "decorator_list", None) for node in ast.walk(tree)):
        new_tree = copy.deepcopy(tree)
        _StripDecorators().visit(new_tree)
        text = _unparse(new_tree)
        if text:
            yield "strip decorators", text

    if any(isinstance(node, ast.If) for node in ast.walk(tree)):
        new_tree = copy.deepcopy(tree)
        _CollapseIf().visit(new_tree)
        text = _unparse(new_tree)
        if text:
            yield "collapse constant if", text

    if any(isinstance(node, ast.BinOp) for node in ast.walk(tree)):
        new_tree = copy.deepcopy(tree)
        _FoldConstants().visit(new_tree)
        text = _unparse(new_tree)
        if text:
            yield "fold constant arithmetic", text

    constants = [
        node for node in _constants(tree)
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool) and node.value != 0
    ]
    for target in range(len(constants)):
        new_tree = copy.deepcopy(tree)
        _ZeroNthConstant(target).visit(new_tree)
        text = _unparse(new_tree)
        if text:
            yield f"reduce constant {target} to 0", text


# ----------------------------------------------------------------- minimizer

class FailureMinimizer:
    """Greedy structural reducer driven by re-execution on both engines."""

    def __init__(
        self,
        run_fn: Callable[[str], ComparisonResult],
        max_evaluations: int = 120,
        max_rounds: int = 12,
    ):
        self.run_fn = run_fn
        self.max_evaluations = max_evaluations
        self.max_rounds = max_rounds

    def minimize(
        self,
        source: str,
        progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> MinimizationResult:
        baseline = self.run_fn(source)
        target = divergence_kind(baseline)
        result = MinimizationResult(
            original_source=source,
            minimized_source=source,
            original_classification=baseline.classification.value,
            minimized_classification=baseline.classification.value,
            preserved_kind=target,
            stopped_reason="no reduction preserved the divergence",
        )
        if baseline.classification.value not in PRESERVABLE_CLASSES:
            result.stopped_reason = (
                f"not a preservable divergence: {baseline.classification.value}"
            )
            return result

        current = source
        for _ in range(self.max_rounds):
            if result.attempts >= self.max_evaluations:
                result.budget_exhausted = True
                result.stopped_reason = "evaluation budget exhausted"
                break
            accepted_source: Optional[str] = None
            seen: set[str] = set()
            for description, candidate in iter_candidates(current):
                if candidate.strip() == current.strip() or candidate in seen:
                    continue
                seen.add(candidate)
                if result.attempts >= self.max_evaluations:
                    result.budget_exhausted = True
                    result.stopped_reason = "evaluation budget exhausted"
                    break
                try:
                    compile(candidate, "<autopiton-min-candidate>", "exec")
                except SyntaxError:
                    continue
                result.attempts += 1
                candidate_result = self.run_fn(candidate)
                kind = divergence_kind(candidate_result)
                accepted = kind == target
                entry = {
                    "attempt": result.attempts,
                    "reduction": description,
                    "classification": candidate_result.classification.value,
                    "kind": list(kind),
                    "accepted": accepted,
                }
                if accepted:
                    entry["source"] = candidate
                result.history.append(entry)
                if progress is not None:
                    progress(entry)
                if accepted:
                    accepted_source = candidate
                    result.accepted += 1
                    result.minimized_source = candidate
                    result.minimized_classification = candidate_result.classification.value
                    break
            if accepted_source is None:
                break
            current = accepted_source

        return result
