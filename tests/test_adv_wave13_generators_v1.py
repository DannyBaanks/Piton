"""Adversarial CPython/PITON differential sweep for generator semantics.

Each case runs the same Python source through CPython, ``piton ejecutar`` and
the Linux native compiler. The oracle is live process output and status; there
are no hand-written expected output strings.
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


def _run(command: list[str], *, cwd: Path = ROOT, timeout: int = 60) -> Result:
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
    with tempfile.TemporaryDirectory(prefix="piton-wave13-ejecutar-") as td:
        source = Path(td) / "case.piton"
        source.write_text(piton_code, encoding="utf-8")
        return _run([sys.executable, "-m", "piton", "ejecutar", str(source)])


def _run_native(piton_code: str) -> Result:
    with tempfile.TemporaryDirectory(prefix="piton-wave13-native-") as td:
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
        "generator_multiple_yields_and_early_return",
        """
def values():
    yield 'first'
    yield 'second'
    return 'finished'

g = values()
print(next(g))
print(next(g))
try:
    next(g)
except StopIteration:
    print('returned')
print(next(g, 'default'))
""",
        id="multiple-yields-early-return",
    ),
    pytest.param(
        "generator_instances_keep_independent_suspended_state",
        """
def values(start):
    current = start
    yield current
    current += 10
    yield current

left = values(1)
right = values(5)
print(next(left), next(right), next(left), next(right))
""",
        id="independent-generator-state",
    ),
    pytest.param(
        "yield_from_subgenerator_return_value_and_following_state",
        """
def child():
    yield 2
    return 40

def parent():
    result = yield from child()
    yield result
    yield 3

print(list(parent()))
""",
        id="yield-from-return-value",
    ),
    pytest.param(
        "yield_from_mixed_iterables_and_generator",
        """
def child():
    yield 'g1'
    yield 'g2'

def combined():
    yield 'start'
    yield from child()
    yield from [10, 20]
    yield from (30, 40)
    yield 'end'

print(list(combined()))
""",
        id="yield-from-mixed-iterables",
    ),
    pytest.param(
        "filtered_generator_expression_sum",
        """
print(sum(x * x for x in range(10) if x % 2 == 1 and x > 3))
""",
        id="filtered-genexpr-sum",
    ),
    pytest.param(
        "generator_expression_list_nested_loops_and_filters",
        """
print(list((x, y, x * y) for x in [1, 2, 3] for y in [2, 3, 4] if x < y and (x + y) % 2 == 0))
""",
        id="genexpr-list-nested-loops",
    ),
    pytest.param(
        "any_and_all_genexpr_short_circuit_side_effects",
        """
seen_any = []
def mark_any(value):
    seen_any.append(value)
    return value

seen_all = []
def mark_all(value):
    seen_all.append(value)
    return value

print(any(mark_any(x) for x in [0, 0, 5, 9]))
print(seen_any)
print(all(mark_all(x) for x in [1, 2, 0, 4]))
print(seen_all)
""",
        id="any-all-genexpr-short-circuit",
    ),
    pytest.param(
        "nested_generator_expressions",
        """
print(sum(sum(x + y for y in [1, 2, 3]) for x in [10, 20]))
""",
        id="nested-genexprs",
    ),
    pytest.param(
        "next_default_and_stop_iteration_exception",
        """
def one():
    yield 7

g = one()
print(next(g, 'unused'))
print(next(g, 'fallback'))
try:
    next(g)
except StopIteration:
    print('stop')
""",
        id="next-default-and-stop-iteration",
    ),
    pytest.param(
        "generator_send_value_is_received_at_suspension",
        """
def exchange():
    incoming = yield 'ready'
    yield incoming * 2
    incoming = yield 'again'
    yield incoming + 1

g = exchange()
print(next(g))
print(g.send(6))
print(next(g))
print(g.send(10))
""",
        id="send-resumes-at-yield-expression",
    ),
    pytest.param(
        "closing_generator_runs_finally_once",
        """
events = []
def guarded():
    try:
        yield 'ready'
        yield 'later'
    finally:
        events.append('cleanup')

g = guarded()
print(next(g))
g.close()
g.close()
print(events)
print(next(g, 'closed'))
""",
        id="close-runs-finally-once",
    ),
    pytest.param(
        "generator_finally_runs_on_normal_exhaustion",
        """
events = []
def guarded():
    try:
        yield 1
        yield 2
    finally:
        events.append('finally')

g = guarded()
print(list(g))
print(events)
""",
        id="finally-on-normal-exhaustion",
    ),
    pytest.param(
        "exhausted_generator_stays_empty_when_reiterated",
        """
g = (x * 3 for x in [1, 2, 3])
print(list(g))
print(list(g))
print(list(iter(g)))
""",
        id="reiterate-exhausted-genexpr",
    ),
    pytest.param(
        "itertools_chain_consumes_generator_and_genexpr",
        """
import itertools

def prefix():
    yield 1
    yield 2

print(list(itertools.chain(prefix(), (x * x for x in [3, 4]), [25])))
""",
        id="itertools-chain-generators",
    ),
    pytest.param(
        "itertools_chain_from_iterable_preserves_generator_order",
        """
import itertools

def groups():
    yield (x for x in [1, 2])
    yield [3]
    yield (x for x in [4, 5] if x > 4)

print(list(itertools.chain.from_iterable(groups())))
""",
        id="chain-from-iterable-generators",
    ),
]


@pytest.mark.parametrize("case_name,py_code", CASES)
def test_wave13_generator_adversarial_differential(case_name: str, py_code: str) -> None:
    _assert_parity(py_code, case_name)
