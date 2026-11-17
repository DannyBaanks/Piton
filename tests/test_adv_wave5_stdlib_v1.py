import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from piton.linux_x86 import compile_native_linux
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
    piton_code = python_to_piton(py_code)
    cp_out = _run_cpython(py_code)
    ej_out = _run_ejecutar(piton_code)
    na_out = _run_native(piton_code)
    assert ej_out == cp_out, f"ejecutar mismatch:\nCPython:\n{cp_out}\nejecutar:\n{ej_out}"
    assert na_out == cp_out, f"native mismatch:\nCPython:\n{cp_out}\nnative:\n{na_out}"


def test_random_seeded_mt19937_parity():
    _assert_parity(
        """
import random
random.seed(42)
print(random.choice(['apple', 'pear', 'banana']))
print(round(random.random(), 12))
print(random.randrange(6))
print(random.randrange(10, 50, 5))
print(random.randint(1, 100))
xs = [10, 20, 30, 40, 50]
random.shuffle(xs)
print(xs)
"""
    )


def test_datetime_date_subtraction_and_timedelta_days():
    _assert_parity(
        """
from datetime import date
d1 = date(2003, 12, 2)
birthday = date(1964, 7, 31)
age = d1 - birthday
print(age.days)
d2 = date(2024, 3, 1) - date(2024, 2, 28)
print(d2.days)
d3 = date(2023, 3, 1) - date(2023, 2, 28)
print(d3.days)
"""
    )


def test_string_template_substitute_and_safe_substitute():
    _assert_parity(
        """
from string import Template
t = Template('${village}folk send $$10 to $cause.')
print(t.substitute(village='Nottingham', cause='the ditch fund'))
t2 = Template('Return the $item to $owner.')
d = dict(item='unladen swallow')
try:
    print(t2.substitute(d))
except KeyError:
    print('KEY_ERR_OK')
print(t2.safe_substitute(d))
t3 = Template('Val: $x and ${y}!')
print(t3.substitute({'x': 42, 'y': 'ok'}))
"""
    )


def test_init_exception_propagates_to_try_except():
    _assert_parity(
        """
class BadInit:
    def __init__(self, x):
        if x < 0:
            raise ValueError('negativo')
        self.x = x

try:
    b = BadInit(-1)
except ValueError:
    print('CAUGHT_INIT_ERR')
b2 = BadInit(5)
print(b2.x)
"""
    )
