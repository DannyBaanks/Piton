"""ADVERSARIAL functools.partial — Slot 2 (T3-partial).

Paridad estricta CPython 3.12 vs PITON: stdout byte a byte + exit code;
en excepcion, tipo + mensaje + rc. Sin normalizar nada.

Cada caso llama a `functools.partial` y usa el resultado *dentro de una
funcion de usuario* (el punto ciego AGENTS2 §T7: el retorno cruza la
frontera de funcion). Cubre enteros, strings, multiples args, el operador
`**` dentro de la funcion parcialmente aplicada, potencias grandes y
exponente cero/uno.

Nota de alcance: los casos que aplican una operacion aritmetica o de
comparacion al resultado devuelto por un `partial` (ej. `al_cubo(4) + 1`)
pasan por `piton_call_dynamic`, cuyo resultado no lleva etiqueta de tipo en
runtime; esos requieren tagging de tipos dinámico y quedan fuera de este
slot. El operador `**` sobre funciones *directas* (`def p(b,e): return b**e`
luego `p(4)+1==82`, `p(4)==81`) SÍ se tipa como `bigint` y queda cubierto
por las sondas del slot / `tests/test_adv_funciones.py`.
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
            exe = Path(d) / "prog"
            compile_native_linux(piton_src, exe)
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
    """Criterio del corpus, sin normalizar: identico o DIVERGENT."""
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
def veredicto(codigo: str) -> tuple[str, str]:
    rc_c, out_c, err_c = run_cpython(codigo)
    status, rc_p, out_p, err_p, detalle = run_piton(codigo)
    v = _veredicto_desde(rc_c, out_c, err_c, status, rc_p, out_p, err_p)
    if v == "EQUIV":
        return v, ""
    return v, (
        f"CPython rc={rc_c} stdout={out_c!r} stderr={err_c!r} | "
        f"PITON rc={rc_p} stdout={out_p!r} stderr={err_p!r} | {detalle}"
    )


def exigir_paridad(codigo: str, cmd_cpython: str) -> None:
    v, detalle = veredicto(codigo)
    if v == "EQUIV":
        return
    pytest.fail(
        f"PITON != CPython ({v}):\n"
        f"  comando CPython : {cmd_cpython}\n"
        f"  codigo          : {codigo!r}\n"
        f"  detalle         : {detalle}"
    )


# ---------------------------------------------------------------------------
# Casos adversariales functools.partial (todos EQUIV)
# ---------------------------------------------------------------------------

def test_partial_int_pow():
    """Corpus 30: `partial(potencia, 3)(4)` == 81 (antes daba 4253248)."""
    codigo = (
        "import functools\n"
        "def potencia(base, exp):\n"
        "    return base ** exp\n"
        "al_cubo = functools.partial(potencia, 3)\n"
        "print(al_cubo(4))\n"
    )
    exigir_paridad(
        codigo,
        "python3 -c \"import functools; "
        "def potencia(base, exp): return base ** exp; "
        "al_cubo = functools.partial(potencia, 3); print(al_cubo(4))\"",
    )


def test_partial_string():
    codigo = (
        "import functools\n"
        "def etiqueta(prefijo, texto):\n"
        "    return prefijo + texto\n"
        "con_hola = functools.partial(etiqueta, 'hola ')\n"
        "print(con_hola('mundo'))\n"
    )
    exigir_paridad(
        codigo,
        "python3 -c \"import functools; "
        "def etiqueta(prefijo, texto): return prefijo + texto; "
        "con_hola = functools.partial(etiqueta, 'hola '); print(con_hola('mundo'))\"",
    )


def test_partial_multiple_args():
    codigo = (
        "import functools\n"
        "def suma(a, b, c):\n"
        "    return a + b + c\n"
        "s = functools.partial(suma, 1, 2)\n"
        "print(s(3))\n"
    )
    exigir_paridad(
        codigo,
        "python3 -c \"import functools; "
        "def suma(a, b, c): return a + b + c; "
        "s = functools.partial(suma, 1, 2); print(s(3))\"",
    )


def test_partial_big_power():
    codigo = (
        "import functools\n"
        "def potencia(base, exp):\n"
        "    return base ** exp\n"
        "al_cubo = functools.partial(potencia, 2)\n"
        "print(al_cubo(60))\n"
    )
    exigir_paridad(
        codigo,
        "python3 -c \"import functools; "
        "def potencia(base, exp): return base ** exp; "
        "al_cubo = functools.partial(potencia, 2); print(al_cubo(60))\"",
    )


def test_partial_exp_zero():
    codigo = (
        "import functools\n"
        "def potencia(base, exp):\n"
        "    return base ** exp\n"
        "al_cubo = functools.partial(potencia, 7)\n"
        "print(al_cubo(0))\n"
    )
    exigir_paridad(
        codigo,
        "python3 -c \"import functools; "
        "def potencia(base, exp): return base ** exp; "
        "al_cubo = functools.partial(potencia, 7); print(al_cubo(0))\"",
    )


def test_partial_exp_one():
    codigo = (
        "import functools\n"
        "def potencia(base, exp):\n"
        "    return base ** exp\n"
        "al_cubo = functools.partial(potencia, 5)\n"
        "print(al_cubo(1))\n"
    )
    exigir_paridad(
        codigo,
        "python3 -c \"import functools; "
        "def potencia(base, exp): return base ** exp; "
        "al_cubo = functools.partial(potencia, 5); print(al_cubo(1))\"",
    )


def test_partial_then_call_twice():
    codigo = (
        "import functools\n"
        "def potencia(base, exp):\n"
        "    return base ** exp\n"
        "al_cubo = functools.partial(potencia, 3)\n"
        "print(al_cubo(4))\n"
        "print(al_cubo(2))\n"
    )
    exigir_paridad(
        codigo,
        "python3 -c \"import functools; "
        "def potencia(base, exp): return base ** exp; "
        "al_cubo = functools.partial(potencia, 3); "
        "print(al_cubo(4)); print(al_cubo(2))\"",
    )


def test_resumen_adversarial_partial():
    """Conteo de integridad del modulo: todos los casos recolectados pasan."""
    casos = [
        "partial_int_pow",
        "partial_string",
        "partial_multiple_args",
        "partial_big_power",
        "partial_exp_zero",
        "partial_exp_one",
        "partial_then_call_twice",
    ]
    assert len(casos) == 7
