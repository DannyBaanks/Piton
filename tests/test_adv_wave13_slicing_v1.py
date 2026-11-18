"""Adversarial CPython/PITON differential sweep for slicing and operators.

Each case runs the same Python source through CPython 3.12, ``piton
ejecutar`` and the Linux native compiler.  The oracle is live process
output and status; there are no hand-written expected output strings.
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
        "string_reverse_and_negative_bounds",
        """
text = 'abcdef'
print(text[::-1])
print(text[-4:-1])
print(text[5:0:-2])
""",
        id="string-reverse-negative-bounds",
    ),
    pytest.param(
        "string_step_and_out_of_bounds",
        """
text = 'abcdef'
print(text[::2])
print(text[-20:20:3])
print(text[20:1:-1])
print(text[100:200])
print(text[-100:-200])
""",
        id="string-step-out-of-bounds",
    ),
    pytest.param(
        "list_reverse_and_step",
        """
values = [0, 1, 2, 3, 4, 5]
print(values[::-1])
print(values[::2])
print(values[5:0:-2])
print(values[-4:-1])
""",
        id="list-reverse-step",
    ),
    pytest.param(
        "tuple_negative_and_step",
        """
values = (0, 1, 2, 3, 4, 5)
print(values[-4:-1])
print(values[::-1])
print(values[::2])
print(values[-20:20:3])
""",
        id="tuple-negative-step",
    ),
    pytest.param(
        "bytes_negative_and_step",
        """
value = b'abcdef'
print(value[::-1])
print(value[::2])
print(value[-4:-1])
print(value[-20:20:3])
""",
        id="bytes-negative-step",
    ),
    pytest.param(
        "out_of_bounds_slices_on_all_sequence_types",
        """
print('abc'[-100:100])
print([1, 2, 3][-100:100])
print((1, 2, 3)[-100:100])
print(b'abc'[-100:100])
print('abc'[100:-100])
print([1, 2, 3][100:-100])
print((1, 2, 3)[100:-100])
print(b'abc'[100:-100])
""",
        id="out-of-bounds-all-sequences",
    ),
    pytest.param(
        "list_slice_assignment_can_resize",
        """
xs = [0, 1, 2, 3, 4]
alias = xs
xs[1:3] = [9]
print(xs, alias)
xs[1:2] = [8, 7, 6]
print(xs, alias)
""",
        id="list-slice-assignment-resize",
    ),
    pytest.param(
        "list_extended_slice_assignment",
        """
xs = [0, 1, 2, 3, 4]
xs[::2] = [9, 7, 5]
print(xs)
""",
        id="list-extended-slice-assignment",
    ),
    pytest.param(
        "list_slice_deletion",
        """
xs = [0, 1, 2, 3, 4]
alias = xs
del xs[1:3]
print(xs, alias)
""",
        id="list-slice-deletion",
    ),
    pytest.param(
        "list_extended_slice_deletion",
        """
xs = [0, 1, 2, 3, 4, 5]
del xs[::2]
print(xs)
""",
        id="list-extended-slice-deletion",
    ),
    pytest.param(
        "list_in_place_add_preserves_aliases",
        """
xs = [1, 2]
alias = xs
xs += [3, 4]
print(xs, alias)
""",
        id="list-in-place-add-alias",
    ),
    pytest.param(
        "list_in_place_add_accepts_tuple_iterable",
        """
xs = [1, 2]
alias = xs
xs += (3,)
print(xs, alias)
""",
        id="list-in-place-add-tuple",
    ),
    pytest.param(
        "list_in_place_multiply_preserves_aliases",
        """
xs = [1, 2]
alias = xs
xs *= 2
print(xs, alias)
xs *= 0
print(xs, alias)
""",
        id="list-in-place-multiply-alias",
    ),
    pytest.param(
        "integer_in_place_operators_rebind",
        """
number = 5
alias = number
number += 2
number *= 3
print(number, alias)
""",
        id="integer-in-place-operators",
    ),
    pytest.param(
        "string_in_place_operators_rebind",
        """
text = 'ab'
alias = text
text += 'c'
text *= 2
print(text, alias)
""",
        id="string-in-place-operators",
    ),
    pytest.param(
        "chained_comparisons_evaluate_once_and_short_circuit",
        """
events = []
def value(number):
    events.append(number)
    return number
print(value(1) < value(2) < value(3))
print(events)
print(value(3) < value(2) < value(1))
print(events)
print(value(1) <= value(2) == value(2) < value(3))
print(events)
""",
        id="chained-comparison-side-effects",
    ),
]


@pytest.mark.parametrize("case_name,py_code", CASES)
def test_wave13_adversarial_differential_sweep(case_name: str, py_code: str) -> None:
    _assert_parity(py_code, case_name)
