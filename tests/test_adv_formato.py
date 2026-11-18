"""ADVERSARIAL formato — corpus `formato` (segundo intento).

Paridad estricta CPython vs PITON: stdout byte a byte + exit code.
No .strip(), no normalización. BUILD_FAIL se reporta como tal.
Los 2 divergentes conocidos deben fallar hoy.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from functools import lru_cache

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.parity_corpus_v4 import python_to_piton  # noqa: E402
from native_linux import compile_native_linux, run_native_linux  # noqa: E402


def run_cpython(codigo: str) -> tuple[int, bytes, bytes]:
    proc = subprocess.run([sys.executable, "-c", codigo], capture_output=True, timeout=60)
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
            proc = run_native_linux(exe, capture_output=True, timeout=60)
        return "OK", proc.returncode, proc.stdout, proc.stderr, ""
    except Exception as exc:
        return "BUILD_FAIL", None, None, None, f"build: {type(exc).__name__}: {exc}"


@lru_cache(maxsize=None)
def veredicto(codigo: str) -> tuple[str, str]:
    rc_c, out_c, err_c = run_cpython(codigo)
    status, rc_p, out_p, err_p, detalle = run_piton(codigo)
    if status == "BUILD_FAIL":
        return "BUILD_FAIL", detalle
    if rc_c == 0:
        if rc_p == 0 and out_p == out_c:
            return "EQUIV", ""
        return "DIVERGENT", f"CPython rc={rc_c} stdout={out_c!r} vs PITON rc={rc_p} stdout={out_p!r}"
    # CPython error
    if rc_p == 0:
        return "DIVERGENT", f"CPython error rc={rc_c} but PITON rc=0"
    # Compare last stderr line
    def ultima_linea(err: bytes | None) -> str:
        if not err:
            return ""
        lines = [l for l in err.decode("utf-8", "replace").splitlines() if l.strip()]
        return lines[-1] if lines else ""
    if ultima_linea(err_p) == ultima_linea(err_c):
        return "EQUIV", ""
    return "DIVERGENT", f"stderr mismatch"


def exigir_paridad(codigo: str, cmd_cpython: str) -> None:
    v, detalle = veredicto(codigo)
    if v == "EQUIV":
        return
    rc_c, out_c, err_c = run_cpython(codigo)
    _status, rc_p, out_p, err_p, detalle_p = run_piton(codigo)
    msg = (
        f"\n  comando CPython : {cmd_cpython}"
        f"\n  codigo          : {codigo!r}"
        f"\n  veredicto       : {v}"
        f"\n  CPython         : rc={rc_c} stdout={out_c!r}"
        f"\n  PITON           : rc={rc_p} stdout={out_p!r}"
        f"\n  detalle         : {detalle_p or detalle}"
    )
    pytest.fail(f"PITON != CPython ({v}):{msg}")


# ---------------------------------------------------------------------------
# 2 fallos conocidos del contexto T1c
# ---------------------------------------------------------------------------

def test_float_1e3():
    # CPython: python3 -c "print(float('1e3'))" -> 1000.0
    # PITON hoy: ValueError: could not convert string to float
    codigo = "print(float('1e3'))\n"
    exigir_paridad(codigo, 'python3 -c "print(float(\'1e3\'))"')


def test_format_hex_hash():
    # CPython: python3 -c "print('{0:#x}'.format(255))" -> 0xff
    # PITON hoy: ff
    codigo = "print('{0:#x}'.format(255))\n"
    exigir_paridad(codigo, 'python3 -c "print(\'{0:#x}\'.format(255))"')


# ---------------------------------------------------------------------------
# Batería nueva de casos límite
# ---------------------------------------------------------------------------

CASOS = [
    {"id": "float_inf", "codigo": "print(float('inf'))\n", "cmd": 'python3 -c "print(float(\'inf\'))"'},
    {"id": "float_nan", "codigo": "print(float('nan'))\n", "cmd": 'python3 -c "print(float(\'nan\'))"'},
    {"id": "float_neg0", "codigo": "print(float('-0.0'))\n", "cmd": 'python3 -c "print(float(\'-0.0\'))"'},
    {"id": "float_spaces", "codigo": "print(float('  1.5  '))\n", "cmd": 'python3 -c "print(float(\'  1.5  \'))"'},
    {"id": "float_underscore", "codigo": "print(float('1_000'))\n", "cmd": 'python3 -c "print(float(\'1_000\'))"'},
    {"id": "format_oct_hash", "codigo": "print('{:#o}'.format(8))\n", "cmd": 'python3 -c "print(\'{:#o}\'.format(8))"'},
    {"id": "format_bin_hash", "codigo": "print('{:#b}'.format(5))\n", "cmd": 'python3 -c "print(\'{:#b}\'.format(5))"'},
    {"id": "format_dec_hash", "codigo": "print('{:#d}'.format(7))\n", "cmd": 'python3 -c "print(\'{:#d}\'.format(7))"'},
    {"id": "format_width_float", "codigo": "print('{:>10.3f}'.format(3.14159))\n", "cmd": 'python3 -c "print(\'{:>10.3f}\'.format(3.14159))"'},
    {"id": "format_sign_align", "codigo": "print('{:=+10d}'.format(42))\n", "cmd": 'python3 -c "print(\'{:=+10d}\'.format(42))"'},
    {"id": "format_repr", "codigo": "print('{!r}'.format('x'))\n", "cmd": 'python3 -c "print(\'{!r}\'.format(\'x\'))"'},
    {"id": "fstring_hex", "codigo": "print(f'{255:#06x}')\n", "cmd": 'python3 -c "print(f\'{255:#06x}\')"' },
    {"id": "round_bankers", "codigo": "print(round(2.675,2))\n", "cmd": 'python3 -c "print(round(2.675,2))"'},
    {"id": "divmod_neg", "codigo": "print(divmod(-7,3))\n", "cmd": 'python3 -c "print(divmod(-7,3))"'},
    {"id": "mod_neg", "codigo": "print((-7)%3)\n", "cmd": 'python3 -c "print((-7)%3)"'},
    {"id": "pow_big", "codigo": "print(2**100)\n", "cmd": 'python3 -c "print(2**100)"'},
    {"id": "int_spaces", "codigo": "print(int('  42  '))\n", "cmd": 'python3 -c "print(int(\'  42  \'))"'},
    {"id": "chr_emoji", "codigo": "print(chr(0x1F600))\n", "cmd": 'python3 -c "print(chr(0x1F600))"'},
    {"id": "ord_emoji", "codigo": "print(ord('😀'))\n", "cmd": 'python3 -c "print(ord(\'😀\'))"'},
]


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
def test_caso_limite_formato(caso):
    exigir_paridad(caso["codigo"], caso["cmd"])


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------

def test_resumen_adversarial_formato():
    casos = [
        ("float_1e3", "print(float('1e3'))\n"),
        ("format_hex_hash", "print('{0:#x}'.format(255))\n"),
    ]
    casos += [(c["id"], c["codigo"]) for c in CASOS]
    filas = []
    for cid, codigo in casos:
        v, _ = veredicto(codigo)
        filas.append((cid, v))
    equiv = [k for k, v in filas if v == "EQUIV"]
    div = [k for k, v in filas if v == "DIVERGENT"]
    bf = [k for k, v in filas if v == "BUILD_FAIL"]
    # imprimir resumen
    print("\n=== ADV FORMATO ===")
    for k, v in filas:
        print(f"{v:9s} {k}")
    print(f"TOTAL={len(filas)} EQUIV={len(equiv)} DIVERGENT={len(div)} BUILD_FAIL={len(bf)}")
    # la aserción solo verifica integridad del conteo
    assert len(filas) == 2 + len(CASOS)
