"""PARIDAD AVANZADA — collections.namedtuple genérico.

Compara PITON (backend nativo Linux) contra CPython 3.12 byte a byte sobre
tipos y campos ARBITRARIOS (no solo Punto), incluyendo errores esperados
(TypeError / AttributeError).

Nota de alcance: el backend nativo de PITON aún no resuelve llamadas con
argumentos por nombre sobre valores de cierre (T11/T1 del arnés), por lo que
la instanciación por keyword, ``_replace(**kwargs)`` y ``defaults=...`` no
se ejercitan aquí; se prueban la instanciación posicional, el acceso por
atributo/índice, ``len``, iteración, ``==``/``!=`` contra tuplas,
``_asdict()``, ``_fields`` y ``__repr__``/``__str__``, además de los errores
esperados.
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
from native_linux import compile_native_linux, run_native_linux  # noqa: E402

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
            p = run_native_linux(exe, capture_output=True, timeout=TIMEOUT)
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
# Casos avanzados sobre namedtuple genérico (campos y nombres arbitrarios)
# ---------------------------------------------------------------------------

CASES = [
    # ---- instanciación posicional, atributo, índice, len, iteración ----
    (
        "inst posicional campos arbitrarios",
        """from collections import namedtuple
Rect = namedtuple("Rect", ["ancho", "alto"])
r = Rect(3, 4)
print(r.ancho, r.alto, r[0], r[1], len(r), list(r))
""",
    ),
    (
        "inst posicional campos string-tupla",
        """from collections import namedtuple
Persona = namedtuple("Persona", ("nombre", "edad"))
p = Persona("Ana", 30)
print(p.nombre, p.edad, len(p), list(p))
""",
    ),
    # ---- igualdad frente a tuplas ----
    (
        "igualdad con tupla",
        """from collections import namedtuple
Punto = namedtuple("Punto", ["x", "y"])
p = Punto(1, 2)
print(p == (1, 2), p == (1, 3), p != (1, 2))
""",
    ),
    # ---- _asdict ----
    (
        "_asdict",
        """from collections import namedtuple
Punto = namedtuple("Punto", ["x", "y"])
p = Punto(1, 2)
print(p._asdict())
""",
    ),
    # ---- _fields ----
    (
        "_fields",
        """from collections import namedtuple
Punto = namedtuple("Punto", ("x", "y"))
p = Punto(1, 2)
print(p._fields)
""",
    ),
    # ---- repr / str exactos ----
    (
        "repr exacto",
        """from collections import namedtuple
Rect = namedtuple("Rect", ["ancho", "alto"])
r = Rect(3, 4)
print(repr(r))
""",
    ),
    (
        "str exacto",
        """from collections import namedtuple
Rect = namedtuple("Rect", ["ancho", "alto"])
r = Rect(3, 4)
print(str(r))
""",
    ),
    # ---- errores esperados ----
    (
        "atributo inexistente",
        """from collections import namedtuple
Punto = namedtuple("Punto", ["x", "y"])
p = Punto(1, 2)
print(p.z)
""",
    ),
    (
        "arity por faltar args (TypeError capturado)",
        """from collections import namedtuple
Punto = namedtuple("Punto", ["x", "y"])
try:
    Punto()
except TypeError:
    print("TypeError")
""",
    ),
    (
        "arity por sobrar args (TypeError capturado)",
        """from collections import namedtuple
Punto = namedtuple("Punto", ["x", "y"])
try:
    Punto(1, 2, 3)
except TypeError:
    print("TypeError")
""",
    ),
]


@pytest.mark.parametrize("nombre,codigo", CASES, ids=[c[0] for c in CASES])
def test_adv_namedtuple(nombre, codigo):
    v = veredicto(codigo)
    assert v == "EQUIV", (
        f"{nombre}: {v}\nCPython: {_run_cpython(codigo)}\nPITON:  {_run_piton(codigo)}"
    )


def test_adv_namedtuple_resumen():
    filas = [(v, nombre) for nombre, codigo in CASES for v in [veredicto(codigo)]]
    print("\n=== ADV NAMEDTUPLE ===")
    for v, n in filas:
        print(f"{v:9s} {n}")
    assert len(filas) == len(CASES)
    assert all(v == "EQUIV" for v, _ in filas), [f for f in filas if f[0] != "EQUIV"]
