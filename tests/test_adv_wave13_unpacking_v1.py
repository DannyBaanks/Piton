"""Adversarial CPython/PITON differential sweep for unpacking and calls.

Each case runs the same Python source through CPython, ``piton ejecutar`` and
the Linux native compiler. The oracle is live process output and status.
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
        proc = subprocess.run(command, cwd=cwd, capture_output=True, timeout=timeout)
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
        "starred_assignment_middle",
        """
first, *middle, last = [10, 20, 30, 40, 50]
print(first, middle, last)
""",
        id="starred-assignment-middle",
    ),
    pytest.param(
        "starred_assignment_leading",
        """
*head, last = (1, 2, 3, 4)
print(head, last)
""",
        id="starred-assignment-leading",
    ),
    pytest.param(
        "starred_assignment_empty_capture",
        """
first, *middle = [1]
*head, last = (2,)
print(first, middle, head, last)
""",
        id="starred-assignment-empty-capture",
    ),
    pytest.param(
        "nested_tuple_list_assignment",
        """
(outer, (inner_a, inner_b)) = [1, (2, 3)]
print(outer, inner_a, inner_b)
""",
        id="nested-tuple-list-assignment",
    ),
    pytest.param(
        "nested_unpacking_in_for_target",
        """
rows = [(1, (2, 3)), (4, (5, 6))]
for outer, (left, right) in rows:
    print(outer, left, right)
""",
        id="nested-unpacking-for-target",
    ),
    pytest.param(
        "args_kwargs_forwarding_function",
        """
def collect(first, second=0, *rest, label='default', **extras):
    print(first, second, rest, label, extras)
def forward(*args, **kwargs):
    return collect(*args, **kwargs)
values = [20, 30]
options = {'label': 'sent', 'flag': True}
print(forward(10, *values, **options))
""",
        id="args-kwargs-function-forwarding",
    ),
    pytest.param(
        "args_kwargs_forwarding_closure",
        """
def make_forwarder(prefix):
    def target(*args, **kwargs):
        print(prefix, args, kwargs)
    def forward(*args, **kwargs):
        return target(*args, **kwargs)
    return forward
forward = make_forwarder('closed')
print(forward(1, 2, marker='yes'))
""",
        id="args-kwargs-closure-forwarding",
    ),
    pytest.param(
        "args_kwargs_forwarding_method",
        """
class Receiver:
    def __init__(self, name):
        self.name = name
    def receive(self, *args, separator=':', **kwargs):
        print(self.name, args, separator, kwargs)
    def forward(self, *args, **kwargs):
        return self.receive(*args, **kwargs)
print(Receiver('method').forward(4, 5, separator='/', flag=6))
""",
        id="args-kwargs-method-forwarding",
    ),
    pytest.param(
        "mutable_default_persists_across_calls",
        """
def append_value(value, acc=[]):
    acc.append(value)
    return acc
print(append_value('a'))
print(append_value('b'))
print(append_value('isolated', []))
print(append_value('c'))
""",
        id="mutable-default-persistence",
    ),
    pytest.param(
        "keyword_only_and_mixed_calls",
        """
def format_values(first, second=2, *, required, suffix='!'):
    print(first, second, required, suffix)
print(format_values(1, required='x'))
print(format_values(3, 4, suffix='?', required='y'))
""",
        id="keyword-only-mixed-calls",
    ),
    pytest.param(
        "wrong_call_arity_typeerrors_are_catchable",
        """
def require_values(first, second, *, keyword):
    pass
try:
    require_values(1, keyword=3)
except TypeError:
    print('missing-positional')
try:
    require_values(1, 2, 3, keyword=4)
except TypeError:
    print('too-many-positional')
try:
    require_values(1, 2)
except TypeError:
    print('missing-keyword-only')
try:
    require_values(1, 2, keyword=3, extra=4)
except TypeError:
    print('unexpected-keyword')
try:
    require_values(1, 2, keyword=3, first=4)
except TypeError:
    print('duplicate-value')
""",
        id="wrong-arity-typeerror-catchable",
    ),
    pytest.param(
        "dict_unpacking_later_mapping_overrides",
        """
left = {'same': 'left', 'left': 1}
right = {'same': 'right', 'right': 2}
combined = {**left, **right}
print(combined)
""",
        id="dict-unpacking-override-order",
    ),
    pytest.param(
        "starred_list_and_tuple_displays",
        """
left = [1, 2]
right = (4, 5)
print([*left, 0, *right])
print((*left, 0, *right))
""",
        id="starred-list-tuple-displays",
    ),
]


@pytest.mark.parametrize("case_name,py_code", CASES)
def test_wave13_unpacking_differential_sweep(case_name: str, py_code: str) -> None:
    _assert_parity(py_code, case_name)
