"""ADVERSARIAL weakref.WeakValueDictionary (backend nativo Linux).

Sin recolector ni referencias debiles, el diccionario no puede retener nada:
  * construir funciona (como CPython);
  * d[k] siempre levanta KeyError (CPython tambien, mientras nada se guardo);
  * d[k] = v levanta NotImplementedError (fail-closed, no se finge una ref fuerte);
  * len == 0, `in` == False.

Limite conocido (no de weakref): `del a` de un global sin enlazar no levanta
cuando `a` se asigna luego con un tipo especializado (stdlib2 #32).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.parity_corpus_v4 import python_to_piton  # noqa: E402
from piton.linux_x86 import compile_native_linux  # noqa: E402

TIMEOUT = 60


def _piton(code: str) -> str:
    with tempfile.TemporaryDirectory() as d:
        exe = Path(d) / "prog"
        compile_native_linux(python_to_piton(code), exe)
        proc = subprocess.run([str(exe)], capture_output=True, timeout=TIMEOUT)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.decode()


def _cpython(code: str) -> str:
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, timeout=TIMEOUT, check=True
    ).stdout.decode()


_PRE = "import weakref\nd = weakref.WeakValueDictionary()\n"


def test_getitem_missing_is_keyerror_like_cpython():
    code = _PRE + "try:\n    d['primary']\nexcept BaseException:\n    print('EXC')\n"
    assert _piton(code) == _cpython(code) == "EXC\n"


def test_construct_prints_nothing_like_cpython():
    code = _PRE + "print('ok')\n"
    assert _piton(code) == _cpython(code) == "ok\n"


def test_setitem_fails_closed():
    code = _PRE + "try:\n    d['k'] = [1]\nexcept BaseException:\n    print('EXC')\n"
    assert _piton(code) == "EXC\n"


def test_empty_len_and_contains():
    code = _PRE + "print(len(d))\nprint('k' in d)\n"
    assert _piton(code) == _cpython(code) == "0\nFalse\n"

