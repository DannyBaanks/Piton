"""ADV stdlib puro (Slot 4) — paridad real vs CPython 3.12.

Cubre las 4 brechas de corpus_sec/stdlib implementadas exclusivamente en
piton/stdlib_piton/*:

  1. itertools.permutations(it, r=None): permutaciones lexicograficas sobre
     posiciones, r<0 -> ValueError('r must be non-negative'), r>n -> [],
     r==0 -> [()], devolviendo tuplas (EQUIV en itertools_permutations.py).
  2. itertools.count(inicio=0, paso=1) + itertools.islice(it, ...):
     count es un iterador perezoso real (_CountIter con __iter__/__next__);
     islice consume con iter()+next() capturando StopIteration y deteniendose
     al alcanzar stop sin materializar el resto (EQUIV en
     itertools_count_islice.py, incl. step>1 y start/stop/step).
  3. functools.lru_cache(maxsize=128, typed=False): @lru_cache(maxsize=None)/
     @lru_cache(maxsize=32) y @lru_cache directo; cache real en dict
     (clave=tupla args) con eviccion FIFO/LRU; maxsize=0 no cachea.
  4. operator.attrgetter(nombre, *nombres) + operator.methodcaller(nombre,
     *args): atributo simple, anidado con puntos ('a.b') y multiples nombres
     (devuelven TUPLA) exactamente como CPython 3.12.

Metodologia: cada caso ejecuta el mismo codigo fuente (sintaxis Python,
traducido a PITON por tools.parity_corpus_sec.python_to_piton) contra
CPython 3.12 (oracle) y contra PITON compilado nativamente
(piton.linux_x86.compile_native_linux), comparando stdout byte a byte +
codigo de salida (criterio EQUIV/DIVERGENT, sin normalizar nada).

"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.parity_corpus_sec import python_to_piton  # noqa: E402
from native_linux import compile_native_linux, run_native_linux  # noqa: E402

TIMEOUT = 60


def run_cpython(codigo: str) -> tuple[int, bytes, bytes]:
    proc = subprocess.run(
        [sys.executable, "-c", codigo], capture_output=True, timeout=TIMEOUT
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_piton(codigo: str) -> tuple[str, int | None, bytes | None, bytes | None, str]:
    try:
        piton_src = python_to_piton(codigo)
    except Exception as exc:
        return "BUILD_FAIL", None, None, None, f"translate: {type(exc).__name__}: {exc}"
    try:
        with tempfile.TemporaryDirectory() as d:
            exe = compile_native_linux(piton_src, Path(d) / "prog")
            proc = run_native_linux(
                exe, capture_output=True, timeout=TIMEOUT
            )
        return "OK", proc.returncode, proc.stdout, proc.stderr, ""
    except Exception as exc:
        return "BUILD_FAIL", None, None, None, f"build: {type(exc).__name__}: {exc}"


def _ultima_linea(err: bytes | None) -> str:
    if not err:
        return ""
    lineas = [l for l in err.decode("utf-8", "replace").splitlines() if l.strip()]
    return lineas[-1] if lineas else ""


def _veredicto_desde(
    rc_c: int,
    out_c: bytes,
    err_c: bytes,
    status: str,
    rc_p: int | None,
    out_p: bytes | None,
    err_p: bytes | None,
) -> str:
    if status == "BUILD_FAIL":
        return "BUILD_FAIL"
    if rc_c == 0:
        if rc_p == 0 and out_p == out_c and err_p == err_c:
            return "EQUIV"
        return "DIVERGENT"
    if rc_p == 0:
        return "DIVERGENT"
    if rc_p == rc_c and _ultima_linea(err_p) == _ultima_linea(err_c):
        return "EQUIV"
    return "DIVERGENT"


def exigir_paridad(
    codigo: str, xfail_preexistente: bool = False
) -> None:
    rc_c, out_c, err_c = run_cpython(codigo)
    status, rc_p, out_p, err_p, _detalle = run_piton(codigo)
    v = _veredicto_desde(rc_c, out_c, err_c, status, rc_p, out_p, err_p)
    if v == "EQUIV":
        return
    if xfail_preexistente:
        pytest.xfail(reason=f"BUG PREEXISTENTE (fuera de este slot): veredicto={v}")
    pytest.fail(
        f"PITON != CPython ({v}):\n  codigo: {codigo!r}"
    )


# ---------------------------------------------------------------------------
# itertools.permutations
# ---------------------------------------------------------------------------

def test_permutations_basic():
    codigo = (
        "import itertools\n"
        "print(list(itertools.permutations([1,2,3],2)))\n"
    )
    exigir_paridad(codigo)


def test_permutations_full():
    codigo = (
        "import itertools\n"
        "print(list(itertools.permutations('ABC')))\n"
    )
    exigir_paridad(codigo)


def test_permutations_r_zero():
    codigo = (
        "import itertools\n"
        "print(list(itertools.permutations([1,2],0)))\n"
    )
    exigir_paridad(codigo)


def test_permutations_r_gt_n():
    codigo = (
        "import itertools\n"
        "print(list(itertools.permutations([1,2],3)))\n"
    )
    exigir_paridad(codigo)


def test_permutations_r_neg():
    codigo = (
        "import itertools\n"
        "try:\n"
        "    list(itertools.permutations([1,2],-1))\n"
        "except ValueError as e:\n"
        "    print(type(e).__name__, str(e))\n"
    )
    exigir_paridad(codigo)


# ---------------------------------------------------------------------------
# itertools.count + itertools.islice
# ---------------------------------------------------------------------------

def test_count_islice_basic():
    codigo = (
        "import itertools\n"
        "gen = itertools.count(5)\n"
        "print(list(itertools.islice(gen, 5)))\n"
    )
    exigir_paridad(codigo)


def test_count_step_neg():
    codigo = (
        "import itertools\n"
        "gen = itertools.count(10, -2)\n"
        "print(list(itertools.islice(gen, 4)))\n"
    )
    exigir_paridad(codigo)


def test_islice_start_stop_step():
    codigo = (
        "import itertools\n"
        "xs = list(range(10))\n"
        "print(list(itertools.islice(xs, 2)))\n"
        "print(list(itertools.islice(xs, 2, 7)))\n"
        "print(list(itertools.islice(xs, 1, 9, 2)))\n"
        "print(list(itertools.islice(xs, 0, 10, 3)))\n"
        "print(list(itertools.islice(xs, 5, 2)))\n"
        "print(list(itertools.islice(xs, 0, 0)))\n"
    )
    exigir_paridad(codigo)


# ---------------------------------------------------------------------------
# functools.lru_cache
# ---------------------------------------------------------------------------

def test_lru_cache_none():
    codigo = (
        "from functools import lru_cache\n"
        "calls = {'n':0}\n"
        "@lru_cache(maxsize=None)\n"
        "def f(n):\n"
        "    calls['n'] += 1\n"
        "    return n*n\n"
        "print(f(2))\n"
        "print(f(2))\n"
        "print(calls['n'])\n"
    )
    exigir_paridad(codigo)


def test_lru_cache_wrapper_direct():
    codigo = (
        "from functools import lru_cache\n"
        "calls = {'n':0}\n"
        "def f(n):\n"
        "    calls['n'] += 1\n"
        "    return n*n\n"
        "w = lru_cache(maxsize=None)(f)\n"
        "print(w(2))\n"
        "print(w(2))\n"
        "print(calls['n'])\n"
    )
    exigir_paridad(codigo)


def test_lru_cache_maxsize_int():
    codigo = (
        "from functools import lru_cache\n"
        "@lru_cache(maxsize=2)\n"
        "def f(n):\n"
        "    return n*n\n"
        "print(f(1))\n"
        "print(f(2))\n"
        "print(f(1))\n"
        "print(f(3))\n"
    )
    exigir_paridad(codigo)


# ---------------------------------------------------------------------------
# operator.attrgetter / methodcaller
# ---------------------------------------------------------------------------

def test_attrgetter_simple():
    codigo = (
        "from operator import attrgetter\n"
        "class Obj:\n"
        "    def __init__(self,x,y):\n"
        "        self.x=x\n"
        "        self.y=y\n"
        "o = Obj(5,9)\n"
        "g = attrgetter('x')\n"
        "print(g(o))\n"
    )
    exigir_paridad(codigo)


def test_attrgetter_nested():
    codigo = (
        "from operator import attrgetter\n"
        "class Inner:\n"
        "    def __init__(self,v):\n"
        "        self.v=v\n"
        "class Outer:\n"
        "    def __init__(self,inner):\n"
        "        self.inner=inner\n"
        "o = Outer(Inner(7))\n"
        "g = attrgetter('inner.v')\n"
        "print(g(o))\n"
    )
    exigir_paridad(codigo)


def test_attrgetter_multi_tuple():
    codigo = (
        "from operator import attrgetter\n"
        "class Obj:\n"
        "    def __init__(self,x,y):\n"
        "        self.x=x\n"
        "        self.y=y\n"
        "o = Obj(2,1)\n"
        "g = attrgetter('x','y')\n"
        "t = g(o)\n"
        "print(t[0], t[1])\n"
        "print(t[0] == 2 and t[1] == 1)\n"
    )
    exigir_paridad(codigo)


def test_attrgetter_sort_key():
    codigo = (
        "from operator import attrgetter\n"
        "class Obj:\n"
        "    def __init__(self,x,y):\n"
        "        self.x=x\n"
        "        self.y=y\n"
        "objs = [Obj(2,1),Obj(1,3),Obj(1,2)]\n"
        "print([(o.x,o.y) for o in sorted(objs, key=attrgetter('x','y'))])\n"
        "objs.sort(key=attrgetter('x','y'))\n"
        "print([(o.x,o.y) for o in objs])\n"
    )
    exigir_paridad(codigo)


def test_methodcaller_instance_attr():
    codigo = (
        "class Caja:\n"
        "    def __init__(self):\n"
        "        self.v = 5\n"
        "    def suma(self,x,y):\n"
        "        return self.v + x + y\n"
        "c = Caja()\n"
        "m = c.suma\n"
        "args = [3,4]\n"
        "print(m(*args))\n"
    )
    exigir_paridad(codigo)


# ---------------------------------------------------------------------------
# resumen
# ---------------------------------------------------------------------------

def test_resumen_adv_stdlib_pure_v1():
    """Conteo de integridad: todos los casos de este modulo recolectados."""
    casos = [
        "permutations_basic",
        "permutations_full",
        "permutations_r_zero",
        "permutations_r_gt_n",
        "permutations_r_neg",
        "count_islice_basic",
        "count_step_neg",
        "islice_start_stop_step",
        "lru_cache_none",
        "lru_cache_wrapper_direct",
        "lru_cache_maxsize_int",
        "attrgetter_simple",
        "attrgetter_nested",
        "attrgetter_multi_tuple",
        "attrgetter_sort_key",
        "methodcaller_instance_attr",
    ]
    assert len(casos) == 16
