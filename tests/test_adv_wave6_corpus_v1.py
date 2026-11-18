import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from native_linux import compile_native_linux, run_native_linux
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
        proc = run_native_linux(
            exe,
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
    assert ej_out == cp_out, f"ejecutar mismatch:\nCPython:\n{cp_out}\nejecutar:\n{ej_out}"
    na_out = _run_native(piton_code)
    assert na_out == cp_out, f"native mismatch:\nCPython:\n{cp_out}\nnative:\n{na_out}"


def test_template_malformed_placeholder_valueerror():
    _assert_parity(
        """
from string import Template
for s in ['cost: $', 'bad $1abc', 'open ${abc', 'x ${} y']:
    try:
        print(Template(s).substitute(a=1))
    except ValueError:
        print('VALUE_ERR_OK')
print(Template('cost: $').safe_substitute(a=1))
"""
    )


def test_template_consecutive_dollar_escapes_and_nonstring_values():
    _assert_parity(
        """
from string import Template
print(Template('$$$$').substitute())
print(Template('$$$$$a').substitute(a='X'))
print(Template('$$a $a $$${a}').substitute(a='v'))
print(Template('n=$n f=$f z=$z').substitute(n=42, f=1.5, z=0))
print(Template('${n}${n}').substitute(n=-7))
"""
    )


def test_template_bool_value_native_gap():
    _assert_parity(
        """
from string import Template
print(Template('b=$b').substitute(b=True))
"""
    )


def test_kwargs_dict_bool_value_keeps_kind():
    _assert_parity(
        """
def fk(**kw):
    return '%s' % kw['b']
print(fk(b=True), fk(b=False))
def g(**kw):
    print(kw['b'])
g(b=True)
"""
    )


def test_date_leap_year_arithmetic():
    _assert_parity(
        """
from datetime import date
print((date(2024, 3, 1) - date(2024, 2, 28)).days)
print((date(2023, 3, 1) - date(2023, 2, 28)).days)
print((date(2000, 3, 1) - date(2000, 2, 28)).days)
print((date(1900, 3, 1) - date(1900, 2, 28)).days)
print((date(2025, 1, 1) - date(2024, 1, 1)).days)
print((date(2028, 12, 31) - date(2024, 2, 29)).days)
"""
    )


def test_date_negative_timedelta_days():
    _assert_parity(
        """
from datetime import date
a = date(2024, 2, 29)
b = date(2024, 3, 10)
print((a - b).days)
print((b - a).days)
print((a - a).days)
print((date(1999, 12, 31) - date(2000, 12, 31)).days)
"""
    )


def test_random_seed_determinism():
    _assert_parity(
        """
import random
for n in [0, 1, 7, 12345]:
    random.seed(n)
    a = [random.randint(1, 1000) for _ in range(5)]
    b = [random.randrange(0, 100, 3) for _ in range(5)]
    c = [random.choice(['a', 'b', 'c', 'd']) for _ in range(5)]
    xs = list(range(8))
    random.shuffle(xs)
    print(n, a, b, c, xs)
    random.seed(n)
    print([random.randint(1, 1000) for _ in range(5)] == a)
"""
    )


def test_init_exception_in_nested_try_except():
    _assert_parity(
        """
class Bad:
    def __init__(self, x):
        if x == 0:
            raise ValueError('zero')
        if x < 0:
            raise KeyError('neg')
        self.x = x

for v in [0, -1, 3]:
    try:
        o = Bad(v)
        print('ok', o.x)
    except ValueError:
        print('VE')
    except KeyError:
        print('KE')

def make(v):
    try:
        return Bad(v)
    except ValueError:
        return None

print(make(0) is None)
print(make(2).x)
"""
    )


def test_init_exception_through_finally_to_outer_except():
    _assert_parity(
        """
class Bad:
    def __init__(self, x):
        if x == 0:
            raise ValueError('zero')
        self.x = x

try:
    try:
        Bad(0)
    finally:
        print('finally')
except ValueError:
    print('outer')
"""
    )


def test_lru_cache_negative_args():
    _assert_parity(
        """
from functools import lru_cache

@lru_cache(maxsize=None)
def f(n):
    return n * n

print(f(-3), f(-3), f(3), f(-1), f(0))
print(f(-3) + f(-3))
"""
    )


def test_lru_cache_cache_info():
    _assert_parity(
        """
from functools import lru_cache

@lru_cache(maxsize=None)
def f(n):
    return n * n

print(f(-3), f(-3), f(3), f(-1), f(0))
info = f.cache_info()
print(info.hits, info.misses, info.currsize)
f.cache_clear()
print(f.cache_info().currsize)
"""
    )
