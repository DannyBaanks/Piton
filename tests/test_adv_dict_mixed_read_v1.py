"""Lectura de dicts de valores mixtos (str e int) en el backend nativo.

Un `d[k]` cuyo tipo de valor no es conocido en build time (dict que llega de
otra funcion, o leido dentro de una closure) va etiquetado (DICT_GET_SLOT_V1):
el kind real llega a print/format/repr. Una caja que cruza una llamada
dinamica (closure invocada por variable) se desempaqueta a sus bits
(DYN_UNWRAP_BITS_V1), igual que antes de las cajas.

Paridad: la salida nativa debe coincidir con CPython.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.parity_corpus_v4 import python_to_piton  # noqa: E402
from piton.linux_x86 import compile_native_linux  # noqa: E402

import tempfile  # noqa: E402


def _assert_parity(codigo_python: str) -> None:
    piton_src = python_to_piton(codigo_python)
    with tempfile.TemporaryDirectory() as d:
        exe = Path(d) / "prog"
        compile_native_linux(piton_src, exe)
        nat = subprocess.run([str(exe)], capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    cp = subprocess.run(
        [sys.executable, "-I", "-c", codigo_python],
        capture_output=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert (nat.returncode, nat.stdout) == (cp.returncode, cp.stdout), (
        f"\nnativo rc={nat.returncode} stdout={nat.stdout!r}\nstderr={nat.stderr!r}"
        f"\nCPython rc={cp.returncode} stdout={cp.stdout!r}"
    )


def test_dict_de_funcion_mixto_print_y_repr() -> None:
    _assert_parity(
        """\
def g():
    d = {}
    d['s'] = 'x'
    d['n'] = 2
    return d
c = g()
print(c['s'], c['n'])
print(repr(c['s']), repr(c['n']))
print(c['n'] + 1)
"""
    )


def test_closure_devuelve_int_de_dict_mixto() -> None:
    _assert_parity(
        """\
def mk():
    cache = {}
    cache[1] = 9
    def env(k):
        return cache[k]
    return env
f = mk()
print(f(1), f(1))
print(f(1) + 1)
"""
    )


def test_closure_devuelve_str_de_dict() -> None:
    _assert_parity(
        """\
def mk(d):
    def env(k):
        return d[k]
    return env
f = mk({1: 9, 2: 'hi'})
print(f(1))
print(f(2))
"""
    )
