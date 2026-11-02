"""
Program Generator Module for AutoPiton

Generates Python programs using AST and templates.
Focuses on generating small, focused test cases that can reveal divergences.
"""

import ast
import random
import textwrap
from typing import List, Dict, Any, Optional, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import string


class ProgramFamily(Enum):
    """Categories of programs to generate."""
    ARITHMETIC = "arithmetic"
    BOOLEAN = "boolean"
    VARIABLES = "variables"
    CONDITIONALS = "conditionals"
    LOOPS = "loops"
    FUNCTIONS = "functions"
    LAMBDAS = "lambdas"
    CLASSES = "classes"
    SUPER = "super"
    EXCEPTIONS = "exceptions"
    COLLECTIONS = "collections"
    UNPACKING = "unpacking"
    DECORATORS = "decorators"
    PROPERTIES = "properties"
    GENERATORS = "generators"
    ASYNC = "async"
    BOUNDED = "bounded"
    SYSTEMATIC = "systematic"
    MODULE = "module"


NON_RANDOM_FAMILIES = (ProgramFamily.SYSTEMATIC, ProgramFamily.MODULE)

FRONTIER_A_SHAPES: Tuple[str, ...] = (
    "dunder_subscript_mapping",
    "dunder_contains_membership",
    "dunder_iterable_protocol",
    "dunder_str_repr",
    "dunder_rich_comparison",
)


@dataclass(frozen=True)
class BoundedConfig:
    """Explicit structural bounds for cross-feature bounded generation."""
    max_depth: int = 2
    max_stmts: int = 3
    max_args: int = 2
    max_iters: int = 4
    types: Tuple[str, ...] = ("int", "float", "bool", "str")
    frontier: Optional[str] = None
    shapes: Optional[Tuple[str, ...]] = None

    def clamped(self) -> "BoundedConfig":
        valid_types = tuple(
            t for t in self.types if t in {"int", "float", "bool", "str"}
        ) or ("int", "float", "bool", "str")
        valid_shapes = tuple(self.shapes) if self.shapes else None
        return BoundedConfig(
            max_depth=max(1, min(4, int(self.max_depth))),
            max_stmts=max(1, min(6, int(self.max_stmts))),
            max_args=max(1, min(4, int(self.max_args))),
            max_iters=max(1, min(6, int(self.max_iters))),
            types=valid_types,
            frontier=self.frontier,
            shapes=valid_shapes,
        )


class SystematicGrid:
    """Deterministic op x kind x surface grid used to probe corpus coverage.

    The rule (which combinations are tried) is the corpus definition; AutoPiton's
    generator emits every cell. Random sampling cannot guarantee coverage, so a
    systematic pass exists as an independent path: no RNG, one program per cell,
    always in the same order.

    Surfaces: ``direct`` prints the expression inline; ``lambda`` returns it
    from a lambda and prints the call. Empirically the value's *shape* is what
    changes under PITON when it crosses a callable boundary — a direct
    ``print(True)`` prints ``True`` but ``f = lambda: True; print(f())`` prints
    ``1`` — so the surface dimension is required for the grid to see those
    divergences at all.

    The ``str`` kinds exist to bound that finding: the boundary defect was
    observed for ``bool`` and ``float``, so ``str`` is the third value shape and
    whether it survives the same crossing is what narrows the defect to a type
    or to the crossing itself. New cells are appended so that a truncated pass
    (``limit``) keeps replaying the same prefix it did before.
    """

    ARITHMETIC_OPS = ("+", "-", "*", "/", "//", "%")
    COMPARISON_OPS = ("==", "!=", "<", "<=", ">", ">=")
    BITWISE_OPS = ("&", "|", "^", "<<", ">>")

    INT_OPERANDS = (7, 3)
    FLOAT_OPERANDS = (7.5, 3.5)
    BOOL_OPERANDS = (True, True)
    SHIFT_OPERANDS = (17, 3)
    STR_OPERANDS = ("ab", "cd")
    STR_REPEAT_OPERANDS = ("ab", 3)

    SURFACES = ("direct", "lambda")

    # Boundary kinds. V0's finding is that a ``bool``/``float`` *result* comes
    # back as an int once it crosses a callable boundary (a lambda's return),
    # while the same expression printed inline is EQUIV. That localises the
    # defect to a crossing, but to one crossing only — the lambda lowering path.
    #
    # Each kind below moves the very same operand expression across a
    # *different* boundary. If a kind's int control stays EQUIV while its
    # float/bool siblings diverge, the corruption follows the value shape and
    # the new signature names the boundary that leaked it. If a kind is
    # uniformly EQUIV, that boundary is clean and the defect is narrower than
    # "callable boundary" — a falsifying result, kept as such.
    BOUNDARY_KINDS = (
        "boundary_func",           # def _f(): return expr
        "boundary_nested",         # def _f(): return _g() -> expr
        "boundary_arg",            # def _f(_x): return _x ; _f(expr)
        "boundary_kwargs",         # def _f(**_k): return _k["x"] ; _f(x=expr)
        "boundary_default",        # def _f(_x=expr): return _x ; _f()
        "boundary_closure",        # value bound in an enclosing scope, read by a nested def
        "boundary_container",      # [expr] then _c[0]
        "boundary_dict",           # {"k": expr} then _c["k"]
        "boundary_comprehension",  # [_x for _x in [expr]][0]
        "boundary_starargs",       # def _f(*_a): return _a[0] ; _f(*[expr])
        "boundary_fstring",        # f"{expr}"
        "boundary_str_format",     # "{0}".format(expr)
        "boundary_percent",        # "%s" % (expr,)
        "boundary_classattr",      # class _C: _a = expr ; _C._a
        "boundary_method",         # class _C: def _m(self): return expr ; _C()._m()
        "boundary_generator",      # def _gen(): yield expr ; next(_gen())
    )

    # Operand shape per boundary. ``arith_int`` is the per-boundary control
    # (an int survives every crossing V0 measured); ``arith_float`` and
    # ``compare_bool`` are the two shapes V0 proved corrupt; ``arith_str`` is
    # the shape that survived. Without the control a boundary that breaks
    # everything looks like a boundary that breaks nothing.
    BOUNDARY_SHAPES = (
        ("arith_int", "+", INT_OPERANDS),
        ("arith_float", "+", FLOAT_OPERANDS),
        ("compare_bool", "==", BOOL_OPERANDS),
        ("arith_str", "+", STR_OPERANDS),
    )

    @classmethod
    def cells(cls):
        cells = []
        for op in cls.ARITHMETIC_OPS:
            cells.append(("arith_int", op, cls.INT_OPERANDS))
            cells.append(("arith_float", op, cls.FLOAT_OPERANDS))
        for op in cls.COMPARISON_OPS:
            cells.append(("compare_int", op, cls.INT_OPERANDS))
            cells.append(("compare_bool", op, cls.BOOL_OPERANDS))
        for op in cls.BITWISE_OPS:
            cells.append(("bitwise_int", op, cls.SHIFT_OPERANDS))
        cells.append(("arith_str", "+", cls.STR_OPERANDS))
        cells.append(("arith_str", "*", cls.STR_REPEAT_OPERANDS))
        for op in cls.COMPARISON_OPS:
            cells.append(("compare_str", op, cls.STR_OPERANDS))
        # Appended, never inserted: the first 74 programs must keep hashing
        # identically or every archived run stops being replayable.
        for kind in cls.BOUNDARY_KINDS:
            for _shape, op, operands in cls.BOUNDARY_SHAPES:
                cells.append((kind, op, operands))
        return cells

    @classmethod
    def render(cls, kind: str, op: str, operands, surface: str) -> str:
        left, right = operands
        expr = f"{left!r} {op} {right!r}"
        if kind.startswith("boundary_"):
            return cls.render_boundary(kind, expr, surface)
        if surface == "lambda":
            return f"_v = lambda: {expr}\nprint(_v())\n"
        return f"print({expr})\n"

    # One prologue per boundary. Each binds the crossed value to ``_b`` so the
    # surface layer can stay uniform. Every wrapper is written in the safe core
    # only: no new names are read before binding, no exception is manufactured.
    BOUNDARY_PROLOGUES = {
        "boundary_func": "def _f():\n    return {e}\n_b = _f()\n",
        "boundary_nested": "def _g():\n    return {e}\ndef _f():\n    return _g()\n_b = _f()\n",
        "boundary_arg": "def _f(_x):\n    return _x\n_b = _f({e})\n",
        "boundary_kwargs": 'def _f(**_k):\n    return _k["x"]\n_b = _f(x={e})\n',
        "boundary_default": "def _f(_x={e}):\n    return _x\n_b = _f()\n",
        "boundary_closure": (
            "def _outer():\n    _cap = {e}\n"
            "    def _inner():\n        return _cap\n    return _inner\n"
            "_b = _outer()()\n"
        ),
        "boundary_container": "_c = [{e}]\n_b = _c[0]\n",
        "boundary_dict": '_c = {"k": {e}}\n_b = _c["k"]\n',
        "boundary_comprehension": "_b = [_x for _x in [{e}]][0]\n",
        "boundary_starargs": "def _f(*_a):\n    return _a[0]\n_b = _f(*[{e}])\n",
        # boundary_fstring is templated separately: the expression goes inside
        # the replacement field, so it cannot share the {e} format pass.
        "boundary_fstring": "",
        "boundary_str_format": '_b = "{0}".format({e})\n',
        "boundary_percent": '_b = "%s" % ({e},)\n',
        "boundary_classattr": "class _C:\n    _a = {e}\n_b = _C._a\n",
        "boundary_method": "class _C:\n    def _m(self):\n        return {e}\n_b = _C()._m()\n",
        "boundary_generator": "def _gen():\n    yield {e}\n_b = next(_gen())\n",
    }

    @classmethod
    def render_boundary(cls, kind: str, expr: str, surface: str) -> str:
        """Move ``expr`` across one boundary, then apply the surface wrapper.

        ``direct`` prints the crossed value as-is; ``lambda`` returns it from a
        second, outer lambda. The outer layer answers whether the defect
        compounds across two crossings or is decided by the first one.
        """
        prologue = cls.BOUNDARY_PROLOGUES[kind]
        if kind == "boundary_fstring":
            # The expression itself goes inside the replacement field, so the
            # template is rendered separately from the other prologues.
            prologue = f'_b = f"{{{expr}}}"\n'
        else:
            # Literal replacement, not str.format: several prologues carry real
            # braces (dict literals, **kwargs reads) that format() would eat.
            prologue = prologue.replace("{e}", expr)
        if surface == "lambda":
            return prologue + "_v = lambda: _b\nprint(_v())\n"
        return prologue + "print(_b)\n"


@dataclass
class GeneratedProgram:
    """A generated Python program."""
    
    # Source code
    code: str
    
    # Metadata
    family: ProgramFamily
    seed: int
    complexity: int  # 1-10
    
    # Description
    description: str = ""
    
    # Expected validity
    is_valid: bool = True
    expected_exception: Optional[str] = None
    
    # Hash
    code_hash: str = ""
    
    def __post_init__(self):
        if not self.code_hash:
            self.code_hash = hashlib.sha256(self.code.encode()).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "family": self.family.value,
            "seed": self.seed,
            "complexity": self.complexity,
            "description": self.description,
            "is_valid": self.is_valid,
            "expected_exception": self.expected_exception,
            "code_hash": self.code_hash,
        }


class ASTGenerator:
    """Generate Python code using AST."""
    
    def __init__(self, seed: Optional[int] = None):
        self.rng = random.Random(seed)
        self._var_counter = 0
        self._func_counter = 0
        self._class_counter = 0
        self.scope: List[str] = []

    def reset(self) -> None:
        """Reset counters and binding scope for one generated program."""
        self._var_counter = 0
        self._func_counter = 0
        self._class_counter = 0
        self.scope = []

    def bind(self, *names: str) -> None:
        """Record names that are legally in scope for later expressions."""
        for name in names:
            if name not in self.scope:
                self.scope.append(name)
    
    def new_var(self) -> str:
        """Generate a new variable name."""
        self._var_counter += 1
        return f"v{self._var_counter}"
    
    def new_func(self) -> str:
        """Generate a new function name."""
        self._func_counter += 1
        return f"f{self._func_counter}"
    
    def new_class(self) -> str:
        """Generate a new class name."""
        self._class_counter += 1
        return f"C{self._class_counter}"
    
    def random_expr(self, max_depth: int = 3, allow_names: bool = True) -> ast.AST:
        """Generate an expression built only from literals and bound names."""
        if max_depth <= 0:
            return self.random_literal()

        choices = [self.random_literal]
        if allow_names and self.scope:
            choices.append(self.random_var)
        choices += [
            lambda: self._safe_binop(max_depth - 1, allow_names),
            lambda: ast.UnaryOp(
                op=ast.USub(),
                operand=self.random_expr(max_depth - 1, allow_names),
            ),
            lambda: self.random_comparison(max_depth - 1, allow_names),
        ]
        return self.rng.choice(choices)()

    def _safe_binop(self, max_depth: int, allow_names: bool) -> ast.AST:
        op = self.random_operator()
        if isinstance(op, (ast.LShift, ast.RShift, ast.BitXor, ast.BitOr, ast.BitAnd)):
            left = ast.Constant(value=self.rng.randint(0, 100))
            upper = 8 if isinstance(op, (ast.LShift, ast.RShift)) else 100
            right = ast.Constant(value=self.rng.randint(0, upper))
            return ast.BinOp(left=left, op=op, right=right)
        left = self.random_expr(max_depth, allow_names)
        if isinstance(op, (ast.Div, ast.Mod, ast.FloorDiv)):
            right = ast.Constant(value=self.rng.randint(1, 9))
        else:
            right = self.random_expr(max_depth, allow_names)
        return ast.BinOp(left=left, op=op, right=right)

    
    def random_literal(self) -> ast.AST:
        """Generate a numeric literal (the generated expression language is numeric)."""
        choices = [
            lambda: ast.Constant(value=self.rng.randint(-10, 10)),
            lambda: ast.Constant(value=round(self.rng.random() * 10, 2)),
            lambda: ast.Constant(value=self.rng.choice([True, False])),
        ]
        return self.rng.choice(choices)()
    
    def random_var(self) -> ast.AST:
        """Reference a name that is actually bound in the current scope."""
        if not self.scope:
            return self.random_literal()
        return ast.Name(id=self.rng.choice(self.scope), ctx=ast.Load())
    
    def random_operator(self) -> ast.operator:
        """Generate a random operator."""
        ops = [
            ast.Add(), ast.Sub(), ast.Mult(), ast.Div(),
            ast.Mod(),
            ast.BitXor(), ast.BitOr(), ast.BitAnd(),
            ast.LShift(), ast.RShift(),
            ast.FloorDiv(),
        ]
        return self.rng.choice(ops)
    
    def random_comparison(self, max_depth: int = 2, allow_names: bool = True) -> ast.AST:
        """Generate a comparison whose operands are already safe expressions."""
        ops = [
            ast.Eq(), ast.NotEq(), ast.Lt(), ast.LtE(),
            ast.Gt(), ast.GtE(),
        ]
        return ast.Compare(
            left=self.random_expr(max_depth, allow_names),
            ops=[self.rng.choice(ops)],
            comparators=[self.random_expr(max_depth, allow_names)],
        )

    def random_boolean(self, max_depth: int = 2) -> ast.AST:
        """Generate a boolean expression with bounded recursion."""
        if max_depth <= 0:
            return ast.Constant(value=self.rng.choice([True, False]))
        choices = [
            lambda: ast.Constant(value=self.rng.choice([True, False])),
            lambda: ast.UnaryOp(op=ast.Not(), operand=self.random_boolean(max_depth - 1)),
            lambda: ast.BoolOp(op=ast.And(), values=[
                self.random_boolean(max_depth - 1),
                self.random_boolean(max_depth - 1),
            ]),
            lambda: ast.BoolOp(op=ast.Or(), values=[
                self.random_boolean(max_depth - 1),
                self.random_boolean(max_depth - 1),
            ]),
            lambda: self.random_comparison(1),
        ]
        return self.rng.choice(choices)()
    
    def to_source(self, node: ast.AST) -> str:
        """Convert AST to source code."""
        try:
            return ast.unparse(node).strip()
        except Exception:
            return ast.dump(node, indent=2)

    def bounded_literal_source(self, kind: str) -> str:
        """Return a deterministic, safe literal source string of *kind*."""
        if kind == "bool":
            return repr(self.rng.choice([True, False]))
        if kind == "float":
            val = round(self.rng.choice([1.5, 2.25, 3.5, 4.75, 6.5, 7.5, 9.25]), 2)
            return repr(val)
        if kind == "str":
            return repr(self.rng.choice(["ab", "cd", "xy", "pq", "hi", "ok"]))
        return repr(self.rng.randint(-8, 15))

    def bounded_expr_source(
        self,
        kind: str,
        depth: int = 2,
        typed_vars: Optional[Dict[str, List[str]]] = None,
        allowed_types: Tuple[str, ...] = ("int", "float", "bool", "str"),
    ) -> str:
        """Generate a type-safe expression of *kind* bounded by *depth*.

        Unlike ``random_expr``, operands are constrained by static type so
        generated expressions never fail with accidental ``str + int`` or
        ``ZeroDivisionError`` before reaching the compiler stages under test.
        """
        vars_for_kind = (typed_vars or {}).get(kind) or []
        if depth <= 0:
            if vars_for_kind and self.rng.random() < 0.5:
                return self.rng.choice(vars_for_kind)
            return self.bounded_literal_source(kind)

        if kind == "int":
            op_kind = self.rng.choice(["bin", "shift", "bit", "unop", "leaf"])
            if op_kind == "leaf":
                return self.bounded_expr_source("int", 0, typed_vars, allowed_types)
            if op_kind == "unop":
                uop = self.rng.choice(["-", "~"])
                inner = self.bounded_expr_source("int", depth - 1, typed_vars, allowed_types)
                return f"({uop}({inner}))"
            if op_kind == "shift":
                op = self.rng.choice(["<<", ">>"])
                left = repr(self.rng.randint(1, 32))
                right = repr(self.rng.randint(0, 4))
                return f"({left} {op} {right})"
            if op_kind == "bit":
                op = self.rng.choice(["&", "|", "^"])
                left = self.bounded_expr_source("int", depth - 1, typed_vars, allowed_types)
                right = repr(self.rng.randint(1, 31))
                return f"({left} {op} {right})"
            op = self.rng.choice(["+", "-", "*", "//", "%"])
            left = self.bounded_expr_source("int", depth - 1, typed_vars, allowed_types)
            if op in ("//", "%"):
                right = repr(self.rng.randint(1, 7))
            else:
                right = self.bounded_expr_source("int", depth - 1, typed_vars, allowed_types)
            return f"({left} {op} {right})"

        if kind == "float":
            op_kind = self.rng.choice(["arith", "div_int", "unop", "leaf"])
            if op_kind == "leaf":
                return self.bounded_expr_source("float", 0, typed_vars, allowed_types)
            if op_kind == "unop":
                inner = self.bounded_expr_source("float", depth - 1, typed_vars, allowed_types)
                return f"(-({inner}))"
            if op_kind == "div_int":
                num = self.rng.randint(1, 25)
                den = self.rng.choice([2, 4, 5, 8])
                return f"({num} / {den})"
            op = self.rng.choice(["+", "-", "*", "/", "//"])
            left = self.bounded_expr_source("float", depth - 1, typed_vars, allowed_types)
            right = repr(self.rng.choice([1.5, 2.0, 2.5, 3.5, 4.0]))
            return f"({left} {op} {right})"

        if kind == "str":
            op_kind = self.rng.choice(["concat", "repeat", "leaf"])
            if op_kind == "leaf":
                return self.bounded_expr_source("str", 0, typed_vars, allowed_types)
            if op_kind == "repeat":
                left = self.bounded_expr_source("str", depth - 1, typed_vars, allowed_types)
                count = self.rng.randint(1, 3)
                return f"({left} * {count})"
            left = self.bounded_expr_source("str", depth - 1, typed_vars, allowed_types)
            right = self.bounded_literal_source("str")
            return f"({left} + {right})"

        # kind == "bool"
        op_kind = self.rng.choice(["compare", "boolop", "not", "leaf"])
        if op_kind == "leaf":
            return self.bounded_expr_source("bool", 0, typed_vars, allowed_types)
        if op_kind == "not":
            inner = self.bounded_expr_source("bool", depth - 1, typed_vars, allowed_types)
            return f"(not ({inner}))"
        if op_kind == "boolop":
            bop = self.rng.choice(["and", "or"])
            left = self.bounded_expr_source("bool", depth - 1, typed_vars, allowed_types)
            right = self.bounded_expr_source("bool", depth - 1, typed_vars, allowed_types)
            return f"({left} {bop} {right})"
        cmp_type = self.rng.choice(list(allowed_types))
        if cmp_type == "bool":
            cmp_op = self.rng.choice(["==", "!="])
        else:
            cmp_op = self.rng.choice(["==", "!=", "<", "<=", ">", ">="])
        left = self.bounded_expr_source(cmp_type, depth - 1, typed_vars, allowed_types)
        right = self.bounded_expr_source(cmp_type, depth - 1, typed_vars, allowed_types)
        return f"({left} {cmp_op} {right})"


class ProgramGenerator:
    """
    Generates Python programs for testing.
    
    Uses AST generation and templates to create diverse test cases.
    """
    
    def __init__(self, seed: Optional[int] = None):
        self.rng = random.Random(seed)
        self.ast_gen = ASTGenerator(seed)
        self.generated: List[GeneratedProgram] = []
    
    def seed(self, seed: int) -> None:
        """Set the random seed."""
        self.rng.seed(seed)
        self.ast_gen = ASTGenerator(seed)
    
    def generate(self, family: Optional[ProgramFamily] = None, count: int = 1) -> List[GeneratedProgram]:
        """
        Generate programs.

        Args:
            family: Specific family to generate, or None for random.
                SYSTEMATIC bypasses the RNG and enumerates the grid instead.
            count: Number of programs to generate

        Returns:
            List of generated programs
        """
        if family is ProgramFamily.SYSTEMATIC:
            return self.systematic_corpus(limit=count)
        programs = []
        for _ in range(count):
            if family:
                program = self._generate_family(family)
            else:
                program = self._generate_random()
            self._validate(program)
            programs.append(program)
        return programs

    @staticmethod
    def _validate(program: GeneratedProgram) -> None:
        """Compile-check with CPython; a broken template is labelled, not hidden."""
        try:
            compile(program.code, "<autopiton-generated>", "exec")
        except SyntaxError as exc:
            program.is_valid = False
            program.expected_exception = "SyntaxError"
            program.description = f"{program.description} [auto-invalid: {exc.msg}]"
    
    def _generate_random(self) -> GeneratedProgram:
        """Generate a random program from the sampled families."""
        families = [f for f in ProgramFamily if f not in NON_RANDOM_FAMILIES]
        family = self.rng.choice(families)
        return self._generate_family(family)

    def systematic_corpus(self, limit: Optional[int] = None) -> List[GeneratedProgram]:
        """Enumerate every cell x surface of the systematic grid."""
        cells = [
            (kind, op, operands, surface)
            for (kind, op, operands) in SystematicGrid.cells()
            for surface in SystematicGrid.SURFACES
        ]
        if limit is not None:
            cells = cells[:limit]
        programs: List[GeneratedProgram] = []
        for kind, op, operands, surface in cells:
            code = SystematicGrid.render(kind, op, operands, surface)
            left, right = operands
            program = GeneratedProgram(
                code=code,
                family=ProgramFamily.SYSTEMATIC,
                seed=0,
                complexity=1 if surface == "direct" else 2,
                description=f"systematic[{kind}/{surface}] {left!r} {op} {right!r}",
            )
            self._validate(program)
            programs.append(program)
        return programs
    
    def _generate_family(self, family: ProgramFamily) -> GeneratedProgram:
        """Generate a program of a specific family."""
        self.ast_gen.reset()
        generators = {
            ProgramFamily.ARITHMETIC: self._gen_arithmetic,
            ProgramFamily.BOOLEAN: self._gen_boolean,
            ProgramFamily.VARIABLES: self._gen_variables,
            ProgramFamily.CONDITIONALS: self._gen_conditionals,
            ProgramFamily.LOOPS: self._gen_loops,
            ProgramFamily.FUNCTIONS: self._gen_functions,
            ProgramFamily.LAMBDAS: self._gen_lambdas,
            ProgramFamily.CLASSES: self._gen_classes,
            ProgramFamily.SUPER: self._gen_super,
            ProgramFamily.EXCEPTIONS: self._gen_exceptions,
            ProgramFamily.COLLECTIONS: self._gen_collections,
            ProgramFamily.UNPACKING: self._gen_unpacking,
            ProgramFamily.DECORATORS: self._gen_decorators,
            ProgramFamily.PROPERTIES: self._gen_properties,
            ProgramFamily.GENERATORS: self._gen_generators,
            ProgramFamily.ASYNC: self._gen_async,
            ProgramFamily.BOUNDED: self._gen_bounded,
        }
        
        gen_func = generators.get(family)
        if gen_func:
            return gen_func()
        else:
            return self._gen_arithmetic()
    
    def _gen_arithmetic(self) -> GeneratedProgram:
        """Generate arithmetic program."""
        node = ast.Module(
            body=[
                ast.Expr(value=self.ast_gen.random_expr(3)),
            ],
            type_ignores=[],
        )
        code = f"print({self.ast_gen.to_source(node.body[0].value)})\n"
        
        return GeneratedProgram(
            code=code,
            family=ProgramFamily.ARITHMETIC,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(1, 3),
            description="Arithmetic expression",
            is_valid=True,
        )
    
    def _gen_boolean(self) -> GeneratedProgram:
        """Generate boolean program."""
        node = self.ast_gen.random_boolean()
        code = f"print({self.ast_gen.to_source(node)})\n"
        
        return GeneratedProgram(
            code=code,
            family=ProgramFamily.BOOLEAN,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(1, 3),
            description="Boolean expression",
            is_valid=True,
        )
    
    def _gen_variables(self) -> GeneratedProgram:
        """Generate program with variables."""
        v1 = self.ast_gen.new_var()
        expr1 = self.ast_gen.to_source(self.ast_gen.random_expr(2, allow_names=False))
        self.ast_gen.bind(v1)
        v2 = self.ast_gen.new_var()
        expr2 = self.ast_gen.to_source(self.ast_gen.random_expr(2))
        self.ast_gen.bind(v2)
        
        code = f"""
{v1} = {expr1}
{v2} = {expr2}
print({v1}, {v2})
"""
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.VARIABLES,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(2, 4),
            description="Variable assignment",
            is_valid=True,
        )
    
    def _gen_conditionals(self) -> GeneratedProgram:
        """Generate program with conditionals."""
        v = self.ast_gen.new_var()
        
        code = f"""
if {self.ast_gen.to_source(self.ast_gen.random_boolean())}:
    {v} = 1
else:
    {v} = 2
print({v})
"""
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.CONDITIONALS,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(2, 4),
            description="Conditional statement",
            is_valid=True,
        )
    
    def _gen_loops(self) -> GeneratedProgram:
        """Generate program with loops."""
        v = self.ast_gen.new_var()
        
        code = f"""
{v} = 0
for i in range({self.rng.randint(1, 5)}):
    {v} += {self.rng.randint(1, 3)}
print({v})
"""
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.LOOPS,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(3, 5),
            description="For loop",
            is_valid=True,
        )
    
    def _gen_functions(self) -> GeneratedProgram:
        """Generate program with functions."""
        f = self.ast_gen.new_func()
        
        num_args = self.rng.randint(0, 3)
        args = [self.ast_gen.new_var() for _ in range(num_args)]
        args_str = ", ".join(args) if args else ""
        
        call_args = [
            self.ast_gen.to_source(self.ast_gen.random_expr(1, allow_names=False))
            for _ in range(num_args)
        ]
        call_args_str = ", ".join(call_args) if call_args else ""
        
        self.ast_gen.bind(*args)
        body = self.ast_gen.to_source(self.ast_gen.random_expr(2))
        
        code = f"""
def {f}({args_str}):
    return {body}

print({f}({call_args_str}))
"""
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.FUNCTIONS,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(3, 5),
            description="Function definition and call",
            is_valid=True,
        )
    
    def _gen_lambdas(self) -> GeneratedProgram:
        """Generate program with lambdas."""
        f = self.ast_gen.new_var()
        arg = self.ast_gen.new_var()
        self.ast_gen.bind(arg)
        
        body = self.ast_gen.to_source(self.ast_gen.random_expr(2))
        val = self.ast_gen.to_source(self.ast_gen.random_expr(1, allow_names=False))
        self.ast_gen.bind(f)
        
        code = f"""
{f} = lambda {arg}: {body}
print({f}({val}))
"""
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.LAMBDAS,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(3, 5),
            description="Lambda function",
            is_valid=True,
        )
    
    def _gen_classes(self) -> GeneratedProgram:
        """Generate program with classes."""
        c = self.ast_gen.new_class()
        m = self.ast_gen.new_func()
        x = self.ast_gen.new_var()
        self.ast_gen.bind(x)
        body = self.ast_gen.to_source(self.ast_gen.random_expr(2))
        
        code = f"""
class {c}:
    def {m}(self, {x}):
        return {body}

obj = {c}()
print(obj.{m}({self.rng.randint(1, 10)}))
"""
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.CLASSES,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(4, 6),
            description="Class with method",
            is_valid=True,
        )
    
    def _gen_super(self) -> GeneratedProgram:
        """Generate program with super()."""
        base = self.ast_gen.new_class()
        derived = self.ast_gen.new_class()
        m = self.ast_gen.new_func()
        x = self.ast_gen.new_var()
        
        code = f"""
class {base}:
    def {m}(self, {x}):
        return {x}

class {derived}({base}):
    def {m}(self, {x}):
        return super().{m}({x}) + 1

obj = {derived}()
print(obj.{m}({self.rng.randint(1, 10)}))
"""
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.SUPER,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(4, 6),
            description="Inheritance with super()",
            is_valid=True,
        )
    
    def _gen_exceptions(self) -> GeneratedProgram:
        """Generate program with exceptions."""
        choices = [
            # Valid exception
            """
try:
    raise ValueError("test error")
except ValueError as e:
    print("caught", str(e))
""",
            # Exception with cause
            """
try:
    raise ValueError("inner")
except ValueError:
    raise TypeError("outer") from None
""",
            # Multiple except
            """
try:
    x = 1 / 0
except ZeroDivisionError:
    print("div by zero")
except Exception:
    print("other")
""",
        ]
        
        code = self.rng.choice(choices)
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.EXCEPTIONS,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(4, 6),
            description="Exception handling",
            is_valid=True,
        )
    
    def _gen_collections(self) -> GeneratedProgram:
        """Generate program with collections."""
        choices = [
            """
lst = [1, 2, 3]
lst.append(4)
print(lst)
""",
            """
d = {"a": 1, "b": 2}
d["c"] = 3
print(d)
""",
            """
s = {1, 2, 3}
s.add(4)
print(s)
""",
            """
t = (1, 2, 3)
print(t[1])
""",
        ]
        
        code = self.rng.choice(choices)
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.COLLECTIONS,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(2, 4),
            description="Collection operations",
            is_valid=True,
        )
    
    def _gen_unpacking(self) -> GeneratedProgram:
        """Generate program with unpacking."""
        choices = [
            # Star unpacking in call
            """
def f(*args):
    return sum(args)

print(f(*[1, 2, 3]))
""",
            # Multiple star unpacking
            """
def f(*args):
    return sum(args)

print(f(*[1, 2], *[3, 4]))
""",
            # Dict unpacking
            """
a = {"x": 1}
b = {"y": 2}
print({**a, **b})
""",
        ]
        
        code = self.rng.choice(choices)
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.UNPACKING,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(3, 5),
            description="Unpacking operations",
            is_valid=True,
        )
    
    def _gen_decorators(self) -> GeneratedProgram:
        """Generate program with decorators."""
        choices = [
            # Simple decorator
            """
def decorator(f):
    return f

@decorator
def func():
    return 42

print(func())
""",
            # Class method
            """
class C:
    @staticmethod
    def method():
        return 100

print(C.method())
""",
            # Property
            """
class C:
    @property
    def prop(self):
        return 50

print(C().prop)
""",
        ]
        
        code = self.rng.choice(choices)
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.DECORATORS,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(4, 6),
            description="Decorator usage",
            is_valid=True,
        )
    
    def _gen_properties(self) -> GeneratedProgram:
        """Generate program with properties."""
        choices = [
            # Property getter
            """
class C:
    @property
    def x(self):
        return 10

print(C().x)
""",
            # Property setter
            """
class C:
    def __init__(self):
        self._x = 0
    
    @property
    def x(self):
        return self._x
    
    @x.setter
    def x(self, value):
        self._x = value

obj = C()
obj.x = 42
print(obj.x)
""",
        ]
        
        code = self.rng.choice(choices)
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.PROPERTIES,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(4, 6),
            description="Property usage",
            is_valid=True,
        )
    
    def _gen_generators(self) -> GeneratedProgram:
        """Generate program with generators."""
        choices = [
            # Simple generator
            """
def gen():
    yield 1
    yield 2
    yield 3

print(list(gen()))
""",
            # Generator expression
            """
print(list(i * 2 for i in range(5)))
""",
        ]
        
        code = self.rng.choice(choices)
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.GENERATORS,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(3, 5),
            description="Generator usage",
            is_valid=True,
        )
    
    def _gen_async(self) -> GeneratedProgram:
        """Generate program with async."""
        choices = [
            # Async function
            """
import asyncio

async def foo():
    return 42

async def main():
    print(await foo())

asyncio.run(main())
""",
        ]
        
        code = self.rng.choice(choices)
        return GeneratedProgram(
            code=textwrap.dedent(code).strip(),
            family=ProgramFamily.ASYNC,
            seed=self.rng.randint(0, 2**32),
            complexity=self.rng.randint(5, 7),
            description="Async function",
            is_valid=True,
        )

    def generate_bounded(
        self,
        count: int = 10,
        config: Optional[BoundedConfig] = None,
    ) -> List[GeneratedProgram]:
        """Generate *count* bounded cross-feature programs under *config*."""
        cfg = (config or BoundedConfig()).clamped()
        programs: List[GeneratedProgram] = []
        for _ in range(count):
            self.ast_gen.reset()
            program = self._gen_bounded(cfg)
            self._validate(program)
            programs.append(program)
        return programs

    def _gen_bounded(self, config: Optional[BoundedConfig] = None) -> GeneratedProgram:
        """Generate a bounded cross-feature program combining 2+ mechanisms."""
        cfg = (config or BoundedConfig()).clamped()
        depth = cfg.max_depth
        allowed = cfg.types
        target_type = self.rng.choice(list(allowed))
        if cfg.shapes:
            candidate_shapes = list(cfg.shapes)
        elif cfg.frontier and str(cfg.frontier).lower() in ("a", "frontier_a", "dunders"):
            candidate_shapes = list(FRONTIER_A_SHAPES)
        else:
            candidate_shapes = [
                "default_expr_fn",
                "closure_capture_fn",
                "class_method_default",
                "super_inheritance_expr",
                "loop_conditional_call",
                "try_except_else_call",
                "comprehension_filter_call",
                "property_computed_class",
                "mutable_container_unpack",
                "generator_yield_try",
                "dunder_operator_class",
                "nonlocal_mutable_closure",
                "try_finally_return_flow",
                *FRONTIER_A_SHAPES,
            ]
        shape = self.rng.choice(candidate_shapes)

        if shape == "default_expr_fn":
            f = self.ast_gen.new_func()
            n_args = self.rng.randint(1, cfg.max_args)
            param_names = [self.ast_gen.new_var() for _ in range(n_args)]
            param_types = [self.rng.choice(list(allowed)) for _ in range(n_args)]
            defaults = [
                self.ast_gen.bounded_expr_source(pt, max(1, depth - 1), None, allowed)
                for pt in param_types
            ]
            sig_parts = [f"{p}={d}" for p, d in zip(param_names, defaults)]
            typed_vars: Dict[str, List[str]] = {}
            for p, pt in zip(param_names, param_types):
                typed_vars.setdefault(pt, []).append(p)
            ret_type = self.rng.choice(param_types)
            body_expr = self.ast_gen.bounded_expr_source(ret_type, depth, typed_vars, allowed)
            call_arg = self.ast_gen.bounded_expr_source(param_types[0], max(0, depth - 1), None, allowed)
            code = (
                f"def {f}({', '.join(sig_parts)}):\n"
                f"    return {body_expr}\n\n"
                f"print({f}())\n"
                f"print({f}({call_arg}))\n"
            )
            desc = f"bounded[default_expr_fn/{ret_type}] depth={depth} args={n_args}"

        elif shape == "closure_capture_fn":
            outer_f = self.ast_gen.new_func()
            inner_f = self.ast_gen.new_func()
            v_outer = self.ast_gen.new_var()
            v_inner = self.ast_gen.new_var()
            typed_vars = {target_type: [v_outer, v_inner]}
            default_expr = self.ast_gen.bounded_expr_source(target_type, max(1, depth - 1), None, allowed)
            inner_expr = self.ast_gen.bounded_expr_source(target_type, depth, typed_vars, allowed)
            call_val = self.ast_gen.bounded_expr_source(target_type, 0, None, allowed)
            code = (
                f"def {outer_f}({v_outer}={default_expr}):\n"
                f"    {v_inner} = {call_val}\n"
                f"    def {inner_f}():\n"
                f"        return {inner_expr}\n"
                f"    return {inner_f}()\n\n"
                f"print({outer_f}())\n"
            )
            desc = f"bounded[closure_capture_fn/{target_type}] depth={depth}"

        elif shape == "class_method_default":
            cls_name = self.ast_gen.new_class()
            m_name = self.ast_gen.new_func()
            p1 = self.ast_gen.new_var()
            p2 = self.ast_gen.new_var()
            d1 = self.ast_gen.bounded_expr_source(target_type, max(1, depth - 1), None, allowed)
            d2 = self.ast_gen.bounded_expr_source(target_type, max(1, depth - 1), None, allowed)
            typed_vars = {target_type: ["self.val", p2]}
            body_expr = self.ast_gen.bounded_expr_source(target_type, depth, typed_vars, allowed)
            code = (
                f"class {cls_name}:\n"
                f"    def __init__(self, {p1}={d1}):\n"
                f"        self.val = {p1}\n"
                f"    def {m_name}(self, {p2}={d2}):\n"
                f"        return {body_expr}\n\n"
                f"obj = {cls_name}()\n"
                f"print(obj.{m_name}())\n"
            )
            desc = f"bounded[class_method_default/{target_type}] depth={depth}"

        elif shape == "super_inheritance_expr":
            base_cls = self.ast_gen.new_class()
            sub_cls = self.ast_gen.new_class()
            m_name = self.ast_gen.new_func()
            p1 = self.ast_gen.new_var()
            base_expr = self.ast_gen.bounded_expr_source(target_type, max(1, depth - 1), {target_type: [p1]}, allowed)
            arg_val = self.ast_gen.bounded_expr_source(target_type, 0, None, allowed)
            if target_type in ("int", "float", "str"):
                tail = self.ast_gen.bounded_literal_source(target_type)
                combine = f"super().{m_name}({p1}) + {tail}"
            else:
                combine = f"not super().{m_name}({p1})"
            code = (
                f"class {base_cls}:\n"
                f"    def {m_name}(self, {p1}):\n"
                f"        return {base_expr}\n\n"
                f"class {sub_cls}({base_cls}):\n"
                f"    def {m_name}(self, {p1}):\n"
                f"        return {combine}\n\n"
                f"obj = {sub_cls}()\n"
                f"print(obj.{m_name}({arg_val}))\n"
            )
            desc = f"bounded[super_inheritance_expr/{target_type}] depth={depth}"

        elif shape == "loop_conditional_call":
            f = self.ast_gen.new_func()
            p = self.ast_gen.new_var()
            acc = self.ast_gen.new_var()
            iters = self.rng.randint(2, cfg.max_iters)
            cond_expr = self.ast_gen.bounded_expr_source("bool", max(1, depth - 1), {"int": [p]}, allowed)
            branch_a = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), {"int": [p]}, allowed)
            branch_b = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), {"int": [p]}, allowed)
            code = (
                f"def {f}({p}):\n"
                f"    if {cond_expr}:\n"
                f"        return {branch_a}\n"
                f"    return {branch_b}\n\n"
                f"{acc} = 0\n"
                f"for i in range({iters}):\n"
                f"    {acc} = {acc} + {f}(i)\n"
                f"print({acc})\n"
            )
            desc = f"bounded[loop_conditional_call/int] depth={depth} iters={iters}"

        elif shape == "try_except_else_call":
            f = self.ast_gen.new_func()
            p = self.ast_gen.new_var()
            res_var = self.ast_gen.new_var()
            cond_expr = self.ast_gen.bounded_expr_source("bool", max(1, depth - 1), None, allowed)
            ok_expr = self.ast_gen.bounded_expr_source(target_type, depth, {target_type: [p]}, allowed)
            fallback_expr = self.ast_gen.bounded_expr_source(target_type, 0, None, allowed)
            arg_val = self.ast_gen.bounded_expr_source(target_type, 0, None, allowed)
            code = (
                f"def {f}({p}):\n"
                f"    if {cond_expr}:\n"
                f"        raise ValueError('bounded')\n"
                f"    return {ok_expr}\n\n"
                f"try:\n"
                f"    {res_var} = {f}({arg_val})\n"
                f"except ValueError:\n"
                f"    {res_var} = {fallback_expr}\n"
                f"print({res_var})\n"
            )
            desc = f"bounded[try_except_else_call/{target_type}] depth={depth}"

        elif shape == "comprehension_filter_call":
            f = self.ast_gen.new_func()
            p = self.ast_gen.new_var()
            iters = self.rng.randint(2, cfg.max_iters)
            body_expr = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), {"int": [p]}, allowed)
            pred_expr = self.ast_gen.bounded_expr_source("bool", 1, {"int": ["i"]}, ("int", "bool"))
            code = (
                f"def {f}({p}):\n"
                f"    return {body_expr}\n\n"
                f"vals = [{f}(i) for i in range({iters}) if {pred_expr}]\n"
                f"print(vals)\n"
            )
            desc = f"bounded[comprehension_filter_call/int] depth={depth} iters={iters}"

        elif shape == "mutable_container_unpack":
            f = self.ast_gen.new_func()
            v1 = self.ast_gen.new_var()
            v2 = self.ast_gen.new_var()
            e1 = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), None, ("int", "bool"))
            e2 = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), None, ("int", "bool"))
            e3 = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), None, ("int", "bool"))
            code = (
                f"def {f}({v1}):\n"
                f"    {v2} = {v1}\n"
                f"    {v2}.append({e3})\n"
                f"    return {v1}[::-1]\n\n"
                f"base = [{e1}, {e2}]\n"
                f"rev = {f}(base)\n"
                f"d1 = {{'a': base[0], 'b': rev[0]}}\n"
                f"d2 = {{'b': rev[-1], 'c': len(base)}}\n"
                f"merged = {{**d1, **d2}}\n"
                f"print(base, rev, merged['a'], merged['b'], merged['c'])\n"
            )
            desc = f"bounded[mutable_container_unpack/int] depth={depth}"

        elif shape == "generator_yield_try":
            g = self.ast_gen.new_func()
            p = self.ast_gen.new_var()
            iters = self.rng.randint(2, cfg.max_iters)
            step_expr = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), {"int": ["i"]}, ("int", "bool"))
            fallback = self.rng.randint(10, 50)
            code = (
                f"def {g}({p}):\n"
                f"    for i in range({p}):\n"
                f"        try:\n"
                f"            if i == {iters - 1}:\n"
                f"                raise ValueError('gen_stop')\n"
                f"            yield {step_expr}\n"
                f"        except ValueError:\n"
                f"            yield {fallback}\n\n"
                f"print(list(zip(range({iters}), {g}({iters}), range(10, 10 + {iters}))))\n"
            )
            desc = f"bounded[generator_yield_try/int] depth={depth} iters={iters}"

        elif shape == "dunder_operator_class":
            cls_name = self.ast_gen.new_class()
            p = self.ast_gen.new_var()
            other = self.ast_gen.new_var()
            v_a = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), None, ("int", "bool"))
            v_b = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), None, ("int", "bool"))
            code = (
                f"class {cls_name}:\n"
                f"    def __init__(self, {p}):\n"
                f"        self.v = {p}\n"
                f"    def __len__(self):\n"
                f"        return abs(self.v)\n"
                f"    def __add__(self, {other}):\n"
                f"        return self.v + {other}\n\n"
                f"inst = {cls_name}({v_a})\n"
                f"print(len(inst), inst + {v_b})\n"
            )
            desc = f"bounded[dunder_operator_class/int] depth={depth}"

        elif shape == "nonlocal_mutable_closure":
            outer_f = self.ast_gen.new_func()
            step_f = self.ast_gen.new_func()
            acc = self.ast_gen.new_var()
            delta = self.ast_gen.new_var()
            iters = self.rng.randint(2, cfg.max_iters)
            init_expr = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), None, ("int", "bool"))
            code = (
                f"def {outer_f}():\n"
                f"    {acc} = {init_expr}\n"
                f"    def {step_f}({delta}):\n"
                f"        nonlocal {acc}\n"
                f"        {acc} = {acc} + {delta}\n"
                f"        return {acc}\n"
                f"    out = []\n"
                f"    for i in range({iters}):\n"
                f"        out.append({step_f}(i + 1))\n"
                f"    return out\n\n"
                f"print({outer_f}())\n"
            )
            desc = f"bounded[nonlocal_mutable_closure/int] depth={depth} iters={iters}"

        elif shape == "try_finally_return_flow":
            f = self.ast_gen.new_func()
            p = self.ast_gen.new_var()
            log = self.ast_gen.new_var()
            e_ok = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), {"int": [p]}, ("int", "bool"))
            e_err = self.ast_gen.bounded_expr_source("int", 0, None, ("int",))
            code = (
                f"{log} = []\n"
                f"def {f}({p}):\n"
                f"    try:\n"
                f"        if {p} < 0:\n"
                f"            raise RuntimeError('neg')\n"
                f"        {log}.append({e_ok})\n"
                f"    except RuntimeError:\n"
                f"        {log}.append({e_err})\n"
                f"    finally:\n"
                f"        {log}.append(99)\n"
                f"    return len({log})\n\n"
                f"r1 = {f}(2)\n"
                f"r2 = {f}(-1)\n"
                f"print(r1, r2, {log})\n"
            )
            desc = f"bounded[try_finally_return_flow/int] depth={depth}"

        elif shape == "dunder_subscript_mapping":
            cls_name = self.ast_gen.new_class()
            v1 = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), None, ("int", "bool"))
            v2 = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), None, ("int", "bool"))
            v3 = self.ast_gen.bounded_expr_source("int", max(1, depth - 1), None, ("int", "bool"))
            code = (
                f"class {cls_name}:\n"
                f"    def __init__(self):\n"
                f"        self._d = {{'k1': {v1}}}\n"
                f"    def __getitem__(self, key):\n"
                f"        return self._d[key]\n"
                f"    def __setitem__(self, key, val):\n"
                f"        self._d[key] = val\n"
                f"    def __len__(self):\n"
                f"        return len(self._d)\n\n"
                f"inst = {cls_name}()\n"
                f"v_read = inst['k1']\n"
                f"inst['k2'] = {v2}\n"
                f"inst['k3'] = {v3}\n"
                f"print(v_read, inst['k2'], inst['k3'], len(inst))\n"
            )
            desc = f"bounded[dunder_subscript_mapping/int] depth={depth}"

        elif shape == "dunder_contains_membership":
            cls_name = self.ast_gen.new_class()
            t = self.ast_gen.bounded_expr_source("int", 0, None, ("int",))
            other = repr(int(t) + 7)
            code = (
                f"class {cls_name}:\n"
                f"    def __init__(self, target):\n"
                f"        self.target = target\n"
                f"    def __contains__(self, item):\n"
                f"        return item == self.target\n\n"
                f"box = {cls_name}({t})\n"
                f"print({t} in box, {other} in box, {other} not in box)\n"
            )
            desc = f"bounded[dunder_contains_membership/bool] depth={depth}"

        elif shape == "dunder_iterable_protocol":
            cls_name = self.ast_gen.new_class()
            iters = self.rng.randint(2, cfg.max_iters)
            step_v = self.ast_gen.bounded_expr_source("int", 0, None, ("int",))
            code = (
                f"class {cls_name}:\n"
                f"    def __init__(self, limit):\n"
                f"        self.limit = limit\n"
                f"    def __iter__(self):\n"
                f"        for i in range(self.limit):\n"
                f"            yield i + {step_v}\n\n"
                f"seq = {cls_name}({iters})\n"
                f"out = []\n"
                f"for elem in seq:\n"
                f"    out.append(elem)\n"
                f"print(out, list(seq))\n"
            )
            desc = f"bounded[dunder_iterable_protocol/int] depth={depth} iters={iters}"

        elif shape == "dunder_str_repr":
            cls_name = self.ast_gen.new_class()
            tag = self.rng.choice(["val", "item", "node", "elem"])
            val = self.ast_gen.bounded_expr_source("int", 0, None, ("int",))
            code = (
                f"class {cls_name}:\n"
                f"    def __init__(self, val):\n"
                f"        self.val = val\n"
                f"    def __str__(self):\n"
                f"        return '{tag}:' + str(self.val)\n"
                f"    def __repr__(self):\n"
                f"        return '<' + '{tag}' + ' ' + str(self.val) + '>'\n\n"
                f"obj = {cls_name}({val})\n"
                f"print(str(obj), repr(obj))\n"
            )
            desc = f"bounded[dunder_str_repr/str] depth={depth}"

        elif shape == "dunder_rich_comparison":
            cls_name = self.ast_gen.new_class()
            v1 = self.ast_gen.bounded_expr_source("int", 0, None, ("int",))
            v2 = self.ast_gen.bounded_expr_source("int", 0, None, ("int",))
            code = (
                f"class {cls_name}:\n"
                f"    def __init__(self, p, tag):\n"
                f"        self.p = p\n"
                f"        self.tag = tag\n"
                f"    def __eq__(self, other):\n"
                f"        return self.p == other.p\n"
                f"    def __lt__(self, other):\n"
                f"        return self.p < other.p\n"
                f"    def __le__(self, other):\n"
                f"        return self.p <= other.p\n\n"
                f"x = {cls_name}({v1}, 'x')\n"
                f"y = {cls_name}({v2}, 'y')\n"
                f"print(x == y, x != y, x < y, x <= y)\n"
            )
            desc = f"bounded[dunder_rich_comparison/bool] depth={depth}"

        else:  # property_computed_class
            cls_name = self.ast_gen.new_class()
            p = self.ast_gen.new_var()
            init_val = self.ast_gen.bounded_expr_source(target_type, max(1, depth - 1), None, allowed)
            prop_expr = self.ast_gen.bounded_expr_source(
                target_type, depth, {target_type: ["self._v"]}, allowed
            )
            code = (
                f"class {cls_name}:\n"
                f"    def __init__(self, {p}={init_val}):\n"
                f"        self._v = {p}\n"
                f"    @property\n"
                f"    def val(self):\n"
                f"        return {prop_expr}\n\n"
                f"obj = {cls_name}()\n"
                f"print(obj.val)\n"
            )
            desc = f"bounded[property_computed_class/{target_type}] depth={depth}"

        return GeneratedProgram(
            code=code.strip() + "\n",
            family=ProgramFamily.BOUNDED,
            seed=self.rng.randint(0, 2**32),
            complexity=min(10, depth + cfg.max_stmts),
            description=desc,
            is_valid=True,
        )
    
    def generate_canary_cases(self) -> List[GeneratedProgram]:
        """Generate the canary test cases from the specification."""
        return [
            GeneratedProgram(
                code="f = lambda x: x + 1\nprint(f(5))",
                family=ProgramFamily.LAMBDAS,
                seed=0,
                complexity=1,
                description="Canary A: Lambda real",
                is_valid=True,
            ),
            GeneratedProgram(
                code="def f(a):\n    return a\n\nprint(f(a=1, b=2))",
                family=ProgramFamily.FUNCTIONS,
                seed=1,
                complexity=2,
                description="Canary B: Unexpected keyword argument",
                is_valid=False,
                expected_exception="TypeError",
            ),
            GeneratedProgram(
                code="class A:\n    def f(self, *args):\n        return len(args)\n\nclass B(A):\n    def g(self):\n        return super().f(1, 2, 3, 4)\n\nprint(B().g())",
                family=ProgramFamily.SUPER,
                seed=2,
                complexity=4,
                description="Canary C: super() with multiple args",
                is_valid=True,
            ),
            GeneratedProgram(
                code="print(list(zip([1, 2], [3, 4], [5, 6])))",
                family=ProgramFamily.COLLECTIONS,
                seed=3,
                complexity=2,
                description="Canary D: zip with three iterables",
                is_valid=True,
            ),
            GeneratedProgram(
                code="raise",
                family=ProgramFamily.EXCEPTIONS,
                seed=4,
                complexity=1,
                description="Canary E: bare re-raise",
                is_valid=False,
                expected_exception="RuntimeError",
            ),
            GeneratedProgram(
                code="class A:\n    pass\n\nA(1, 2)",
                family=ProgramFamily.CLASSES,
                seed=5,
                complexity=2,
                description="Canary F: Constructor with invalid args",
                is_valid=False,
                expected_exception="TypeError",
            ),
            GeneratedProgram(
                code="class C:\n    def __enter__(self):\n        print('enter')\n        return self\n    def __exit__(self, kind, value, traceback):\n        print('exit')\n\nwith C():\n    print('body')",
                family=ProgramFamily.CLASSES,
                seed=6,
                complexity=3,
                description="Canary G: Context manager",
                is_valid=True,
            ),
            GeneratedProgram(
                code="a = {'x': 1}\nb = {'x': 2, 'y': 3}\nprint({**a, **b})",
                family=ProgramFamily.COLLECTIONS,
                seed=7,
                complexity=2,
                description="Canary H: Dict merge",
                is_valid=True,
            ),
            GeneratedProgram(
                code="def f(*args):\n    print(sum(args))\n\nf(*[1, 2], *[3, 4])",
                family=ProgramFamily.UNPACKING,
                seed=8,
                complexity=3,
                description="Canary I: Multiple starred args",
                is_valid=True,
            ),
            GeneratedProgram(
                code="class E:\n    pass\n\nraise E()",
                family=ProgramFamily.EXCEPTIONS,
                seed=9,
                complexity=2,
                description="Canary J: Invalid exception base",
                is_valid=False,
                expected_exception="TypeError",
            ),
        ]
