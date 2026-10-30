"""
Program Generator Module for AutoPiton

Generates Python programs using AST and templates.
Focuses on generating small, focused test cases that can reveal divergences.
"""

import ast
import random
import textwrap
from typing import List, Dict, Any, Optional, Callable
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
            family: Specific family to generate, or None for random
            count: Number of programs to generate
            
        Returns:
            List of generated programs
        """
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
        """Generate a random program."""
        families = list(ProgramFamily)
        family = self.rng.choice(families)
        return self._generate_family(family)
    
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
