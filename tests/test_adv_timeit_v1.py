"""timeit v1: Timer(stmt, setup) y timeit(stmt, setup, number).

Backend nativo: valida argumentos como CPython (ValueError para stmt/setup no
str, TypeError para number no int) y falla cerrado con NotImplementedError al
medir, porque no hay eval() en runtime ni reloj de alta resolucion expuesto.

Backend ejecutar: usa el modulo timeit real de CPython, que mide de verdad y
devuelve un float no negativo.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from piton.linux_x86 import compile_native_linux


def _run_ejecutar(piton_code: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "prog.piton"
        src.write_text(piton_code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "piton", "ejecutar", str(src)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
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


def _probe(call: str, excepto: str) -> str:
    """Bloque intentar/excepto que imprime 'OK' si la llamada no lanza, o el tag."""
    return (
        f"intentar:\n    {call}\n    imprimir ('OK')\n"
        f"excepto {excepto}:\n    imprimir ('{excepto}')\n"
    )


def test_native_timer_construction_and_defaults():
    code = (
        "desde timeit importar Timer\n"
        "t = Timer()\n"
        "imprimir (t.stmt, t.setup)\n"
        "t2 = Timer('a=1', 'a=0')\n"
        "imprimir (t2.stmt, t2.setup)\n"
    )
    assert _run_native(code) == "pass pass\na=1 a=0\n"


def test_native_timer_rejects_non_string_stmt_and_setup_like_cpython():
    code = (
        "desde timeit importar Timer\n"
        + _probe("Timer(3)", "ValueError")
        + _probe("Timer('pass', 2.5)", "ValueError")
        + _probe("Timer(Nada)", "ValueError")
    )
    assert _run_native(code) == "ValueError\nValueError\nValueError\n"


def test_native_timeit_number_must_be_int():
    code = (
        "desde timeit importar Timer\n"
        + _probe("Timer('pass').timeit('muchos')", "TypeError")
        + _probe("Timer('pass').timeit(2.0)", "TypeError")
    )
    assert _run_native(code) == "TypeError\nTypeError\n"


def test_native_measurement_fails_closed_not_a_fake_float():
    code = (
        "desde timeit importar Timer\n"
        "importar timeit\n"
        + _probe("Timer('a=1').timeit()", "NotImplementedError")
        + _probe("Timer('a=1').timeit(number=10)", "NotImplementedError")
        + _probe("timeit.timeit('x', 'y', 100)", "NotImplementedError")
        + _probe("timeit.timeit()", "NotImplementedError")
    )
    assert _run_native(code) == "NotImplementedError\n" * 4


def test_native_module_timeit_validates_before_failing_closed():
    code = (
        "importar timeit\n"
        + _probe("timeit.timeit(3)", "ValueError")
        + _probe("timeit.timeit('x', 'y', 'n')", "TypeError")
        + _probe("timeit.timeit('x', 'y', 5)", "NotImplementedError")
    )
    assert _run_native(code) == "ValueError\nTypeError\nNotImplementedError\n"


def test_ejecutar_timer_returns_non_negative_float():
    code = (
        "desde timeit importar Timer\n"
        "t = Timer('a=1', 'a=0').timeit(1000)\n"
        "imprimir (type(t) == float and t >= 0)\n"
        "importar timeit\n"
        "u = timeit.timeit('a=1', 'a=0', number=1000)\n"
        "imprimir (type(u) == float and u >= 0)\n"
    )
    assert _run_ejecutar(code) == "True\nTrue\n"
