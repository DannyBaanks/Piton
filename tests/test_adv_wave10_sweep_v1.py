"""Adversarial CPython/PITON differential sweep for language semantics.

Each case runs the same Python source through CPython 3.12, piton ejecutar
and the Linux native compiler. The oracle is live process output and status;
there are no hand-written expected output strings.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from native_linux import compile_native_linux  # noqa: E402
from tools.parity_corpus_sec import python_to_piton  # noqa: E402


@dataclass(frozen=True)
class Result:
    stage: str
    returncode: int | None
    stdout: bytes
    stderr: bytes


def _run(command: list[str], *, cwd: Path = ROOT, timeout: int = 30) -> Result:
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return Result("timeout", None, exc.stdout or b"", exc.stderr or b"")
    return Result("run", proc.returncode, proc.stdout, proc.stderr)


def _run_ejecutar(piton_code: str) -> Result:
    with tempfile.TemporaryDirectory(prefix="piton-wave10-ejecutar-") as td:
        source = Path(td) / "case.piton"
        source.write_text(piton_code, encoding="utf-8")
        return _run([sys.executable, "-m", "piton", "ejecutar", str(source)])


def _run_native(piton_code: str) -> Result:
    with tempfile.TemporaryDirectory(prefix="piton-wave10-native-") as td:
        executable = Path(td) / "case"
        try:
            compile_native_linux(piton_code, executable)
        except Exception as exc:
            return Result("compile", None, b"", f"{type(exc).__name__}: {exc}".encode())
        return _run([str(executable)])


def _assert_parity(py_code: str, case_name: str) -> None:
    # no_local is PITON's spelling; the oracle always receives valid Python.
    piton_code = python_to_piton(py_code).replace("nonlocal", "no_local")
    oracle = _run([sys.executable, "-c", py_code])
    assert oracle.returncode == 0, (
        f"CPython oracle failed ({oracle.returncode}): "
        f"stdout={oracle.stdout!r} stderr={oracle.stderr!r}\nsource:\n{py_code}"
    )

    mismatches = []
    for target, actual in (
        ("piton ejecutar", _run_ejecutar(piton_code)),
        ("compile_native_linux", _run_native(piton_code)),
    ):
        if actual.returncode != oracle.returncode or actual.stdout != oracle.stdout:
            mismatches.append(
                f"{target}: CPython rc={oracle.returncode} stdout={oracle.stdout!r}; "
                f"PITON rc={actual.returncode} stdout={actual.stdout!r} "
                f"stage={actual.stage} stderr={actual.stderr.decode('utf-8', 'replace')}"
            )
    assert not mismatches, (
        f"differential mismatch in {case_name}:\n" + "\n".join(mismatches)
        + f"\nPITON source:\n{piton_code}"
    )


CASES = [
    pytest.param(
        "nested_try_else_finally_order",
        """
events = []
try:
    try:
        events.append('body')
    except KeyError:
        events.append('except')
    else:
        events.append('else')
    finally:
        events.append('inner-finally')
finally:
    events.append('outer-finally')
print(events)
""",
        id="nested-try-else-finally",
    ),
    pytest.param(
        "nested_raise_reraise_finally",
        """
try:
    try:
        raise ValueError('v')
    except ValueError:
        print('inner-caught')
        raise
    finally:
        print('inner-finally')
except ValueError:
    print('outer-caught')
finally:
    print('outer-finally')
""",
        id="raise-reraise-finally",
    ),
    pytest.param(
        "finally_exception_replaces_pending_exception",
        """
try:
    try:
        raise ValueError('first')
    finally:
        raise TypeError('second')
except TypeError:
    print('replacement-caught')
""",
        id="finally-replaces-exception",
    ),
    pytest.param(
        "exception_raised_in_else_reaches_outer_handler",
        """
try:
    try:
        print('body')
    except ValueError:
        print('inner-except')
    else:
        raise RuntimeError('else')
    finally:
        print('inner-finally')
except RuntimeError:
    print('outer-except')
""",
        id="else-exception-scope",
    ),
    pytest.param(
        "nonlocal_integer_cell_mutation",
        """
def make_counter(start):
    value = start
    def next_value():
        nonlocal value
        value += 1
        return value
    return next_value
counter = make_counter(4)
print(counter(), counter(), counter())
""",
        id="nonlocal-cell-mutation",
    ),
    pytest.param(
        "closure_appends_string_argument_to_captured_list",
        """
def make_appender():
    values = []
    def append(value):
        values.append(value)
        print(values)
    return append
append = make_appender()
append('a')
""",
        id="captured-list-string-append",
    ),
    pytest.param(
        "closure_mutates_captured_dict",
        """
def make_counter():
    state = {'count': 0}
    def increment():
        state['count'] += 1
        print(state['count'])
    return increment
increment = make_counter()
increment()
increment()
""",
        id="captured-dict-mutation",
    ),
    pytest.param(
        "recursive_closure_with_capture",
        """
def make_sum_to(limit):
    def visit(n):
        if n <= 0:
            return limit
        return n + visit(n - 1)
    return visit
print(make_sum_to(7)(4))
""",
        id="recursive-closure-capture",
    ),
    pytest.param(
        "closure_cells_are_independent",
        """
def make_counter(start):
    value = start
    def advance():
        nonlocal value
        value += 1
        return value
    return advance
left = make_counter(0)
right = make_counter(10)
print(left(), right(), left(), right())
""",
        id="independent-closure-cells",
    ),
    pytest.param(
        "string_negative_and_out_of_bounds_slices",
        """
text = 'abcdef'
print(text[::-1])
print(text[6:1:-2])
print(text[-20:20:3])
print(text[20:1:-1])
""",
        id="string-negative-slices",
    ),
    pytest.param(
        "list_negative_and_out_of_bounds_slices",
        """
values = [0, 1, 2, 3, 4, 5]
print(values[::-1])
print(values[5:0:-2])
print(values[-30:30:4])
print(values[30:0:-1])
""",
        id="list-negative-slices",
    ),
    pytest.param(
        "tuple_negative_and_out_of_bounds_slices",
        """
values = (0, 1, 2, 3, 4, 5)
print(values[::-1])
print(values[5:0:-2])
print(values[-30:30:4])
print(values[30:0:-1])
""",
        id="tuple-negative-slices",
    ),
    pytest.param(
        "star_unpack_middle_from_list",
        """
first, *middle, last = [10, 20, 30, 40, 50]
print(first, middle, last)
""",
        id="star-unpack-list",
    ),
    pytest.param(
        "star_unpack_empty_middle_and_tuple_input",
        """
first, *middle, last = (10, 20)
print(first, middle, last)
*head, last = [1, 2, 3]
print(head, last)
""",
        id="star-unpack-empty-middle",
    ),
    pytest.param(
        "star_unpack_arity_errors_are_catchable",
        """
for values in [[], [1], [1, 2, 3, 4]]:
    try:
        first, *middle, last = values
        print('ok', first, middle, last)
    except ValueError:
        print('value-error')
""",
        id="star-unpack-arity-errors",
    ),
    pytest.param(
        "list_comprehension_conditions",
        """
print([x * x for x in range(8) if x % 2 == 0 and x > 1])
""",
        id="list-comprehension-condition",
    ),
    pytest.param(
        "nested_list_comprehension_loops_and_conditions",
        """
print([(x, y) for x in [1, 2, 3] for y in [2, 3, 4] if x < y and (x + y) % 2 == 1])
""",
        id="nested-list-comprehension",
    ),
    pytest.param(
        "dict_comprehension_condition_and_nested_loops",
        """
result = {x: y for x in [1, 2, 3] for y in [4, 5] if x + y > 6}
print(result)
""",
        id="nested-dict-comprehension",
    ),
    pytest.param(
        "set_comprehension_condition_nested_loops_sorted",
        """
result = {x % 5 for x in [1, 2, 3, 4] for y in [0, 1] if x + y > 3}
print(sorted(result))
""",
        id="nested-set-comprehension",
    ),
    pytest.param(
        "operator_precedence_unary_power_shift_and_arithmetic",
        """
print(-2 ** 2, (-2) ** 2, 2 ** 3 ** 2)
print(1 + 2 * 3, (1 + 2) * 3, 1 << 2 + 1)
print(1 | 2 & 4 ^ 3)
""",
        id="operator-precedence",
    ),
    pytest.param(
        "chained_comparison_evaluates_middle_once_and_short_circuits",
        """
def value(n):
    print(n)
    return n
print(value(1) < value(2) < value(3))
print(value(3) < value(2) < value(1))
""",
        id="chained-comparison-side-effects",
    ),
    pytest.param(
        "and_or_return_operands_and_skip_side_effects",
        """
events = []
def mark(name, value):
    events.append(name)
    return value
print(mark('zero', 0) and mark('skip-and', 8))
print(mark('empty', '') or mark('fallback', 'ok'))
print(mark('truthy', [1]) or mark('skip-or', 9))
print(events)
""",
        id="short-circuit-values-and-effects",
    ),
    pytest.param(
        "inheritance_c3_mro_method_resolution",
        """
class A:
    def label(self):
        return 'A'
class B(A):
    def label(self):
        return 'B'
class C(A):
    def label(self):
        return 'C'
class D(B, C):
    pass
print(D().label())
""",
        id="inheritance-c3-mro",
    ),
    pytest.param(
        "linear_super_method_chain",
        """
class A:
    def label(self):
        return 'A'
class B(A):
    def label(self):
        return 'B' + super().label()
class C(B):
    def label(self):
        return 'C' + super().label()
print(C().label())
""",
        id="super-linear-chain",
    ),
    pytest.param(
        "cooperative_diamond_super_mro",
        """
class A:
    def label(self):
        return 'A'
class B(A):
    def label(self):
        return 'B' + super().label()
class C(A):
    def label(self):
        return 'C' + super().label()
class D(B, C):
    def label(self):
        return 'D' + super().label()
print(D().label())
""",
        id="super-cooperative-diamond",
    ),
    pytest.param(
        "property_getter_setter_deleter_and_inheritance",
        """
class Base:
    def __init__(self, value):
        self._value = value
    @property
    def value(self):
        return self._value
    @value.setter
    def value(self, new_value):
        self._value = new_value
    @value.deleter
    def value(self):
        self._value = -1
class Child(Base):
    pass
item = Child(3)
print(item.value)
item.value = 8
print(item.value)
del item.value
print(item.value)
""",
        id="property-get-set-delete-inherited",
    ),
    pytest.param(
        "classmethod_and_staticmethod_binding",
        """
class Base:
    def __init__(self, value):
        self.value = value
    @classmethod
    def build(cls, value):
        return cls(value)
class Child(Base):
    def __init__(self, value):
        self.value = value * 2
    @staticmethod
    def combine(left, right):
        return left + right
item = Child.build(12)
print(item.value)
print(Child.combine(4, 5))
""",
        id="classmethod-staticmethod",
    ),
    pytest.param(
        "custom_eq_and_lt_methods",
        """
class Score:
    def __init__(self, value):
        self.value = value
    def __eq__(self, other):
        return self.value == other.value
    def __lt__(self, other):
        return self.value < other.value
left = Score(2)
right = Score(5)
print(left == Score(2), left == right)
print(left < right, right < left)
""",
        id="custom-eq-lt",
    ),
    pytest.param(
        "custom_len_method_controls_truth",
        """
class Sized:
    def __init__(self, size):
        self.size = size
    def __len__(self):
        return self.size
for item in [Sized(0), Sized(2)]:
    if item:
        print('truthy')
    else:
        print('falsey')
""",
        id="custom-len-truth",
    ),
    pytest.param(
        "custom_bool_method_controls_truth",
        """
class Explicit:
    def __init__(self, flag):
        self.flag = flag
    def __bool__(self):
        return self.flag
for item in [Explicit(False), Explicit(True)]:
    if item:
        print('truthy')
    else:
        print('falsey')
""",
        id="custom-bool-truth",
    ),
    pytest.param(
        "custom_len_and_bool_dispatch_per_class",
        """
class Sized:
    def __init__(self, size):
        self.size = size
    def __len__(self):
        return self.size
class Explicit:
    def __init__(self, flag):
        self.flag = flag
    def __len__(self):
        return 1
    def __bool__(self):
        return self.flag
for item in [Sized(0), Sized(2), Explicit(False), Explicit(True)]:
    if item:
        print('truthy')
    else:
        print('falsey')
""",
        id="custom-len-bool-cross-class-dispatch",
    ),
]


@pytest.mark.parametrize("case_name,py_code", CASES)
def test_wave10_adversarial_differential_sweep(case_name: str, py_code: str) -> None:
    _assert_parity(py_code, case_name)
