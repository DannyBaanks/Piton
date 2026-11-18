import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from native_linux import compile_native_linux
from tools.parity_corpus_sec import python_to_piton


def _run_cpython(py_code: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", py_code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"CPython failed ({proc.returncode}): {proc.stderr}"
    return proc.stdout


def _run_ejecutar(piton_code: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "prog.piton"
        src.write_text(piton_code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "piton", "ejecutar", str(src)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert proc.returncode == 0, (
        f"piton ejecutar failed ({proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc.stdout


def _run_native(piton_code: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / "prog_bin"
        compile_native_linux(piton_code, str(exe))
        proc = subprocess.run(
            [str(exe)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert proc.returncode == 0, (
        f"native failed ({proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc.stdout


def _assert_parity(py_code: str) -> None:
    expected = _run_cpython(py_code)
    pit_code = python_to_piton(py_code)
    assert _run_ejecutar(pit_code) == expected
    assert _run_native(pit_code) == expected


def test_math_isclose_basic_and_tolerances():
    _assert_parity(
        "import math\n"
        "print(math.isclose(0.1 + 0.2, 0.3))\n"
        "print(math.isclose(1.0, 1.001, rel_tol=1e-2))\n"
        "print(math.isclose(1.0, 1.001, rel_tol=1e-4))\n"
        "print(math.isclose(1e-9, 0.0))\n"
        "print(math.isclose(1e-9, 0.0, abs_tol=1e-8))\n"
        "print(math.isclose(1e-9, 0.0, abs_tol=1e-10, rel_tol=0.0))\n"
        "print(math.isclose(5, 5))\n"
        "print(math.isclose(True, 1.0))\n"
        "print(math.isclose(0.0, -0.0))\n"
    )


def test_math_isclose_from_import_and_ieee754():
    _assert_parity(
        "from math import isclose\n"
        "inf = float('inf')\n"
        "ninf = float('-inf')\n"
        "nan = float('nan')\n"
        "print(isclose(inf, inf))\n"
        "print(isclose(ninf, ninf))\n"
        "print(isclose(inf, ninf))\n"
        "print(isclose(inf, 1e308))\n"
        "print(isclose(nan, nan))\n"
        "print(isclose(1.0, nan))\n"
        "print(isclose(1.0, 1.0, rel_tol=nan))\n"
        "print(isclose(1.0, 2.0, rel_tol=nan))\n"
        "print(isclose(-10.0, -10.000000001))\n"
    )


def test_math_isclose_negative_tolerances_value_error():
    _assert_parity(
        "import math\n"
        "try:\n"
        "    math.isclose(1.0, 1.0, rel_tol=-1e-9)\n"
        "    print('no-err')\n"
        "except ValueError:\n"
        "    print('rel-valerr')\n"
        "try:\n"
        "    math.isclose(1.0, 1.0, abs_tol=-0.1)\n"
        "    print('no-err')\n"
        "except ValueError:\n"
        "    print('abs-valerr')\n"
    )
