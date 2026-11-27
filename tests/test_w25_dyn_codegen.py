"""W25 — codegen / dynamic dispatch: 6 Tier A BUILD_FAIL de /usr/lib/python3.12.

Ola 25 (piton/linux_x86.py + stdlib/mir segun ownership). Cada test fija una
causa raiz con su sonda minima y la verifica contra el oraculo CPython 3.12
byte a byte (AGENTS2 §1: no hay falso verde — si no es EQUIV, el test falla).

Casos cubiertos (archivo stdlib -> causa raiz):
  * pathlib.py    — UnboundLocalError '_all_values' en la rama de funcion
                    nativa de modulo (os.path.ismount): los args se calculaban
                    DESPUES de usarse. Fix: hoist de _complete_call_args.
                    Ademas os.path.isjunction/samestat/realpath no existian en
                    el bundled os__path/posixpath (isjunction = Falso en POSIX,
                    CPython 3.12 posixpath).
  * _aix_support.py — `piton_slot_box_new(<long>)` en _wrap_result_box: el RHS
                    crudo (p.ej. piton_call_dynamic) se pasaba directo a una
                    funcion que recibe PitonSlot (struct) -> error de tipo en
                    C. Fix: piton_dyn_unbox antes de boxear.
  * ast.py        — llamada directa a `__init__(cls, *args)` de metaclase con
                    MAS argumentos que params: VARARG_PACK_V1 solo empaquetaba
                    con menos args -> gcc "too many arguments". Fix: empaquetar
                    siempre que el slot vararg no lleve ya la tupla.
  * gettext.py    — method_call sobre receptor None-conflict de tipo
                    desconocido ('add_fallback'): despacho dinamico por
                    class_name en runtime (WIP concurrente DYN dispatch).
  * plistlib.py   — subscript_store sobre receptor None-conflict de tipo
                    desconocido: despacho en runtime por kind (DYN_SUBSCRIPT_
                    STORE_V1).
  * imaplib.py    — datetime.datetime.fromtimestamp: clase datetime real en
                    datetime.piton (WIP concurrente) en vez de fail-closed.

Verificacion Tier A: los 6 archivos pasan el embudo adapt->parse->mir->build
de tools/parity_corpus_v4 (estatico, sin ejecutar).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.parity_corpus_v4 import STDLIB_DIR, _stage_impl, python_to_piton  # noqa: E402
from native_linux import compile_native_linux, require_linux_native, run_native_linux  # noqa: E402

TIMEOUT = 120

# Los 6 archivos Tier A de la ola 25.
W25_TIER_A = [
    "pathlib.py",
    "_aix_support.py",
    "ast.py",
    "gettext.py",
    "plistlib.py",
    "imaplib.py",
]

# Sonda minima por causa raiz: (nombre, codigo Python). Cada una imprime el
# valor de retorno y se compara byte a byte contra CPython con TZ=UTC (el
# backend Linux nativo es UTC: sin libc/TZ).
_CASES = [
    ("vararg_mas_args_que_params",
     "def f(a, *resto):\n    print(a)\n    print(len(resto))\nf(1, 2, 3)\n"),
    ("vararg_metaclase_init_ast",
     "class Meta(type):\n    def __init__(cls, *args):\n        cls.tag = len(args)\n"
     "class Num(metaclass=Meta):\n    pass\nprint(Num.tag)\n"),
    ("os_path_ismount_isjunction_pathlib",
     "import os\nprint(os.path.ismount('/'))\nprint(os.path.isjunction('/'))\n"),
    ("dyn_subscript_store_plistlib",
     "def f(x):\n    result = None\n    if x:\n        result = x\n    result['k'] = 1\n"
     "    return result\nprint(f({'a': 1}))\n"),
    ("dyn_method_call_gettext",
     "class T:\n    def __init__(self):\n        self.n = 0\n"
     "    def add_fallback(self, o):\n        self.n = self.n + 1\n"
     "def f(cls):\n    result = None\n    result = cls()\n    result.add_fallback(1)\n"
     "    return result.n\nprint(f(T))\n"),
    ("datetime_fromtimestamp_utc_imaplib",
     "from datetime import datetime, timezone\n"
     "d = datetime.fromtimestamp(0, timezone.utc)\n"
     "print(d.year, d.month, d.day, d.hour, d.minute, d.second)\n"),
    ("datetime_fromtimestamp_naive",
     "from datetime import datetime\nd = datetime.fromtimestamp(1735689600)\n"
     "print(d.year, d.month, d.day, d.hour, d.minute, d.second)\nprint(d)\n"),
]


def _cpython_env() -> dict[str, str]:
    env = dict(os.environ)
    env["TZ"] = "UTC"  # backend Linux nativo == UTC (sin libc/TZ)
    return env


def run_cpython(codigo: str) -> tuple[int, bytes, bytes]:
    proc = subprocess.run(
        [sys.executable, "-c", codigo],
        capture_output=True, timeout=TIMEOUT, env=_cpython_env(),
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_piton(codigo: str) -> tuple[str, int | None, bytes | None, str]:
    try:
        piton_src = python_to_piton(codigo)
    except Exception as exc:
        return "BUILD_FAIL", None, None, f"translate: {type(exc).__name__}: {exc}"
    try:
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "prog"
            compile_native_linux(piton_src, exe)
            proc = run_native_linux(exe, capture_output=True, timeout=TIMEOUT,
                                    env=_cpython_env())
        return "OK", proc.returncode, proc.stdout, ""
    except Exception as exc:
        return "BUILD_FAIL", None, None, f"build: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Tier A: los 6 archivos stdlib llegan a `build` en el embudo estatico v4
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("nombre", W25_TIER_A)
def test_w25_tier_a_build(nombre):
    require_linux_native()
    record, _ = _stage_impl(str(STDLIB_DIR / nombre))
    assert record.get("stage_fail") is None, (
        f"{nombre}: stage_fail={record.get('stage_fail')} "
        f"reached={record.get('reached')} detail={record.get('detail')}"
    )
    assert record.get("reached") == "build", (
        f"{nombre}: reached={record.get('reached')} detail={record.get('detail')}"
    )


# ---------------------------------------------------------------------------
# Paridad diferencial de la sonda minima por causa raiz
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("nombre,codigo", _CASES)
def test_w25_sonda_paridad(nombre, codigo):
    require_linux_native()
    rc_c, out_c, _err_c = run_cpython(codigo)
    status, rc_p, out_p, detail = run_piton(codigo)
    assert status == "OK", f"[{nombre}] BUILD_FAIL: {detail}"
    assert rc_p == rc_c and out_p == out_c, (
        f"[{nombre}] DIVERGENT\n  codigo: {codigo!r}\n"
        f"  cpython: rc={rc_c} out={out_c!r}\n  piton:   rc={rc_p} out={out_p!r}"
    )


# ---------------------------------------------------------------------------
# Unidad: _wrap_result_box no puede pasar un long crudo a piton_slot_box_new
# (piton_slot_box_new recibe PitonSlot, struct — error de tipo en C)
# ---------------------------------------------------------------------------
def test_w25_wrap_result_box_long_crudo():
    from piton.linux_x86 import LinuxCEmitter

    em = LinuxCEmitter()
    # RHS long crudo (retorno de piton_call_dynamic): hay que desempaquetar
    # con piton_dyn_unbox antes de boxear.
    line = "    piton_usr__20=piton_call_dynamic(a,b,0,0);"
    out = em._wrap_result_box(line, "piton_usr__20")
    assert "piton_slot_box_new(piton_dyn_unbox(piton_call_dynamic(a,b,0,0)))" in out, out
    # RHS literal 0 (int): tambien long crudo.
    out0 = em._wrap_result_box("    piton_usr__20=0;", "piton_usr__20")
    assert "piton_slot_box_new(piton_dyn_unbox(0))" in out0, out0
    # RHS ya boxeado: no se toca.
    boxed = "    piton_usr__20=(long)piton_slot_box_new(piton_slot(x,PK_STR));"
    assert em._wrap_result_box(boxed, "piton_usr__20") == boxed
    # RHS que YA es una expresion PitonSlot (struct): box directo, sin
    # piton_dyn_unbox (un struct no convierte a long).
    struct = "    piton_usr__20=piton_slot_guess(x);"
    outs = em._wrap_result_box(struct, "piton_usr__20")
    assert "piton_slot_box_new(piton_slot_guess(x))" in outs and "piton_dyn_unbox" not in outs, outs
    # OJO: un RHS que solo CONTIENE piton_slot( como subexpresion (p.ej. el
    # empaquetado de args dentro de piton_call_dynamic) sigue siendo long.
    nested = "    piton_usr__20=piton_call_dynamic(m,(long)(piton_slot(v,PK_INT)),0,0);"
    outn = em._wrap_result_box(nested, "piton_usr__20")
    assert "piton_dyn_unbox(piton_call_dynamic" in outn, outn
