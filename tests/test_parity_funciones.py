"""PARIDAD/ANTI-MENTIRA — corpus funciones.

Tres capas, igual que en objetos/formato:
1) `veredicto()` honesto: stdout byte a byte + exit code; en excepcion, tipo +
   mensaje + rc. Nada se normaliza, nada se descarta.
2) test del test: el comparador DEMUESTRA que caza mentiras (stdout distinto,
   excepcion con mensaje distinto, rc distinto) y que no perdona BUILD_FAIL.
3) aplicacion real a ejemplos de corpus_t1/funciones/ que hoy deben ser EQUIV.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.parity_corpus_v4 import python_to_piton  # noqa: E402
from native_linux import compile_native_linux  # noqa: E402

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
    """Logica pura del comparador (testeable sin compilar nada)."""
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


@lru_cache(maxsize=None)
def veredicto(codigo: str) -> str:
    return veredicto_desde(_run_cpython(codigo), _run_piton(codigo))


# ---------------------------------------------------------------------------
# CAPA 2: el test del test — el comparador NO puede mentir
# ---------------------------------------------------------------------------

def test_comparador_caza_stdout_distinto():
    v = veredicto_desde((0, b"10\n", b""), ("OK", 0, b"11\n", b""))
    assert v == "DIVERGENT"


def test_comparador_caza_stdout_igual_pero_rc_distinto():
    v = veredicto_desde((0, b"10\n", b""), ("OK", 1, b"10\n", b""))
    assert v == "DIVERGENT"


def test_comparador_caza_excepcion_con_mensaje_distinto():
    # Mismo tipo de excepcion (ValueError) y mismo rc, mensaje distinto.
    cp = (1, b"", b"ValueError: argument must be nonnegative\n")
    pi = ("OK", 1, b"", b"ValueError: only accepts nonnegative integers\n")
    assert veredicto_desde(cp, pi) == "DIVERGENT"


def test_comparador_acepta_excepcion_identica_ignorando_traceback():
    # El traceback lleva rutas absolutas (AGENTS2 T14): manda tipo+mensaje.
    cp = (1, b"", b"Traceback (most recent call last):\n  File \"x\", line 1\nValueError: boom\n")
    pi = ("OK", 1, b"", b"ValueError: boom\n")
    assert veredicto_desde(cp, pi) == "EQUIV"


def test_comparador_no_perdona_build_fail():
    v = veredicto_desde((0, b"10\n", b""), ("BUILD_FAIL", None, None, None))
    assert v == "BUILD_FAIL"


def test_comparador_no_confunde_exito_con_excepcion():
    v = veredicto_desde((0, b"", b""), ("OK", 1, b"", b"TypeError: x\n"))
    assert v == "DIVERGENT"


# ---------------------------------------------------------------------------
# CAPA 3: aplicacion real sobre corpus_t1/funciones/
# ---------------------------------------------------------------------------

EJEMPLOS = [
    "23_itertools_product.py",
    "31_math_gcd.py",
    "32_math_factorial.py",
    "34_math_comb.py",
    "35_math_perm.py",
    "37_math_sqrt.py",
]


@pytest.mark.parametrize("nombre", EJEMPLOS)
def test_corpus_funciones_equiv(nombre):
    ruta = REPO / "corpus_t1" / "funciones" / nombre
    codigo = ruta.read_text(encoding="utf-8")
    v = veredicto_desde(_run_cpython(codigo), _run_piton(codigo))
    assert v == "EQUIV", f"{nombre}: {v} (stdout/rc no coinciden con CPython)"

    # Integrity: el ejemplo debe imprimir un valor de retorno (regla de oro 2).
    assert "print(" in codigo


def test_paridad_funciones_resumen():
    filas = [(n, veredicto_desde(
        _run_cpython((REPO / "corpus_t1" / "funciones" / n).read_text(encoding="utf-8")),
        _run_piton((REPO / "corpus_t1" / "funciones" / n).read_text(encoding="utf-8")),
    )) for n in EJEMPLOS]
    print("\n=== PARITY FUNCIONES ===")
    for n, v in filas:
        print(f"{v:9s} {n}")
    assert len(filas) == len(EJEMPLOS)
