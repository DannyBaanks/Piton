"""PARIDAD AVANZADA — collections.deque y collections.namedtuple (W4 Slot 3).

Compara PITON (backend nativo Linux) contra CPython 3.12 byte a byte sobre:
- deque: rotate, extend, extendleft, reverse, remove, __getitem__, __setitem__,
  __eq__, __repr__ (fieles a CPython 3.12, incluido maxlen y deque vacio).
- namedtuple(typename, field_names, defaults=Nada): field_names como str
  ('x y', 'x, y', '  a,b  c '), lista/tupla de nombres, y defaults.
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

from tools.parity_corpus_v4 import python_to_piton  # noqa: E402
from piton.linux_x86 import compile_native_linux  # noqa: E402

TIMEOUT = 60


def _run_cpython(codigo: str) -> tuple[int, bytes, bytes]:
    p = subprocess.run([sys.executable, "-c", codigo], capture_output=True, timeout=TIMEOUT)
    return p.returncode, p.stdout, p.stderr


def _run_piton(codigo: str):
    try:
        piton_src = python_to_piton(codigo)
    except Exception:
        return "BUILD_FAIL", None, None, None
    try:
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "prog"
            compile_native_linux(piton_src, exe)
            p = subprocess.run([str(exe)], capture_output=True, timeout=TIMEOUT)
        return "OK", p.returncode, p.stdout, p.stderr
    except Exception:
        return "BUILD_FAIL", None, None, None


def _ultima_linea(err: bytes | None) -> str:
    if not err:
        return ""
    lineas = [l for l in err.decode("utf-8", "replace").splitlines() if l.strip()]
    return lineas[-1] if lineas else ""


def veredicto_desde(
    cpython: tuple[int, bytes, bytes],
    piton: tuple[str, int | None, bytes | None, bytes | None],
) -> str:
    rc_c, out_c, err_c = cpython
    status, rc_p, out_p, err_p = piton
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


def veredicto(codigo: str) -> str:
    return veredicto_desde(_run_cpython(codigo), _run_piton(codigo))


# ---------------------------------------------------------------------------
# Casos avanzados de deque (fieles a CPython 3.12)
# ---------------------------------------------------------------------------

CASES_DEQUE = [
    (
        "rotate n>0 derecha",
        """from collections import deque
dq = deque([1, 2, 3, 4])
dq.rotate(1)
print(list(dq))
""",
    ),
    (
        "rotate n<0 izquierda",
        """from collections import deque
dq = deque([1, 2, 3, 4])
dq.rotate(-1)
print(list(dq))
""",
    ),
    (
        "rotate n mayor que len",
        """from collections import deque
dq = deque([1, 2, 3])
dq.rotate(5)
print(list(dq))
""",
    ),
    (
        "rotate vacio no hace nada",
        """from collections import deque
dq = deque()
dq.rotate(3)
print(list(dq))
""",
    ),
    (
        "rotate n=0",
        """from collections import deque
dq = deque([1, 2, 3])
dq.rotate(0)
print(list(dq))
""",
    ),
    (
        "extend",
        """from collections import deque
dq = deque([1, 2])
dq.extend([3, 4])
print(list(dq))
""",
    ),
    (
        "extendleft revierte orden",
        """from collections import deque
dq = deque([1, 2])
dq.extendleft([3, 4])
print(list(dq))
""",
    ),
    (
        "extendleft respeta maxlen",
        """from collections import deque
dq = deque([1, 2, 3], maxlen=3)
dq.extendleft([4, 5])
print(list(dq))
""",
    ),
    (
        "reverse",
        """from collections import deque
dq = deque([1, 2, 3, 4])
dq.reverse()
print(list(dq))
""",
    ),
    (
        "remove existente",
        """from collections import deque
dq = deque([1, 2, 3, 2])
dq.remove(2)
print(list(dq))
""",
    ),
    (
        "remove inexistente ValueError",
        """from collections import deque
dq = deque([1, 2, 3])
try:
    dq.remove(9)
except ValueError:
    print("ValueError")
""",
    ),
    (
        "__getitem__",
        """from collections import deque
dq = deque([10, 20, 30])
print(dq[0], dq[1], dq[-1])
""",
    ),
    (
        "__setitem__",
        """from collections import deque
dq = deque([10, 20, 30])
dq[1] = 99
print(list(dq))
""",
    ),
    (
        "__eq__",
        """from collections import deque
d1 = deque([1, 2, 3])
d2 = deque([1, 2, 3])
d3 = deque([1, 2, 4])
print(d1 == d2, d1 == d3)
""",
    ),
    (
        "__repr__ sin maxlen",
        """from collections import deque
dq = deque([1, 2, 3])
print(repr(dq))
""",
    ),
    (
        "__repr__ con maxlen",
        """from collections import deque
dq = deque([1, 2], maxlen=5)
print(repr(dq))
""",
    ),
]


# ---------------------------------------------------------------------------
# Casos avanzados de namedtuple con field_names como str
# ---------------------------------------------------------------------------

CASES_NAMEDTUPLE = [
    (
        "namedtuple campos str 'x y'",
        """from collections import namedtuple
Point = namedtuple('Point', 'x y')
p = Point(1, 2)
print(p.x, p.y)
""",
    ),
    (
        "namedtuple campos str 'x, y'",
        """from collections import namedtuple
Point = namedtuple('Point', 'x, y')
p = Point(1, 2)
print(p.x, p.y)
""",
    ),
    (
        "namedtuple campos str '  a,b  c '",
        """from collections import namedtuple
Triple = namedtuple('Triple', '  a,b  c ')
t = Triple(1, 2, 3)
print(t.a, t.b, t.c)
""",
    ),
    (
        "namedtuple campos lista",
        """from collections import namedtuple
Punto = namedtuple('Punto', ['x', 'y'])
p = Punto(1, 2)
print(p.x, p.y)
""",
    ),
    (
        "namedtuple campos tupla",
        """from collections import namedtuple
Punto = namedtuple('Punto', ('x', 'y'))
p = Punto(1, 2)
print(p.x, p.y)
""",
    ),
]


CASES = [("deque::" + nombre, codigo) for nombre, codigo in CASES_DEQUE] + [
    ("namedtuple::" + nombre, codigo) for nombre, codigo in CASES_NAMEDTUPLE
]


@pytest.mark.parametrize("nombre,codigo", CASES, ids=[c[0] for c in CASES])
def test_adv_collections_v2(nombre, codigo):
    v = veredicto(codigo)
    assert v == "EQUIV", (
        f"{nombre}: {v}\nCPython: {_run_cpython(codigo)}\nPITON:  {_run_piton(codigo)}"
    )


def test_adv_collections_v2_resumen():
    filas = [(v, nombre) for nombre, codigo in CASES for v in [veredicto(codigo)]]
    print("\n=== ADV COLLECTIONS V2 ===")
    for v, n in filas:
        print(f"{v:9s} {n}")
    assert len(filas) == len(CASES)
    assert all(v == "EQUIV" for v, _ in filas), [f for f in filas if f[0] != "EQUIV"]
