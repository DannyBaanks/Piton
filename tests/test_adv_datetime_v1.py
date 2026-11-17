"""ADVERSARIAL datetime/time — paridad byte a byte con CPython 3.12.

Sigue el patron de tests/test_adv_funciones.py: traduce Python -> PITON con
python_to_piton, compila a ELF nativo Linux con compile_native_linux, ejecuta
y compara stdout/stderr/exit-code contra CPython 3.12 SIN normalizar
(AGENTS2 §1). Una excepcion en ambos lados cuenta como equivalente.

Caso alcanzables en el backend Linux actual (2026-11-15):
  * date.today(): year/month/day (mismo dia que CPython).
  * date(2003, 12, 2): ctor, str, repr.
  * date + timedelta / date - timedelta (usan date.fromordinal, SÍ alcanzable).
  * timedelta.days / total_seconds.
  * time.localtime()/time.gmtime(): struct_time por indices [0..8] y len 9.
  * time.strftime()/time.asctime()/time.ctime().
  * date.strftime() para %m/%d/%y/%b/%B.

LIMITES DEL RUNTIME (preexistentes, no introducidos por este cambio):
  * Zona horaria: el backend Linux enlaza -nostdlib -static (sin libc), por
    lo time.localtime()==time.gmtime()==UTC. Los casos de localtime/strftime
    se comparan contra CPython con TZ=UTC para que coincidan.
  * date.__sub__ (date - date): el constructor timedelta(...) NO es resoluble
    desde dentro de datetime.piton (bug preexistente del codegen de clases
    bundled: `desde datetime importar timedelta` funciona desde fuera, pero
    `timedelta(...)` dentro de datetime.piton da TypeError). No se fuerza.
  * timedelta(...) acepta tanto `dias=`/`segundos=` como los alias ingleses
    `days=`/`seconds=` (datetime.piton). Sin embargo `(date + timedelta) - td`
    y `date - timedelta` siguen fallando por tipado nativo (ver xfail de
    date_add_sub_timedelta).
  * date.weekday() / date.isoweekday() como METODO de clase bundled: el
    simbolo date__weekday no se enlaza en el backend Linux (T11).
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

from tools.parity_corpus_v4 import python_to_piton  # noqa: E402
from piton.linux_x86 import compile_native_linux  # noqa: E402

TIMEOUT = 60


def run_cpython(codigo_python: str) -> tuple[int, bytes, bytes]:
    # El backend Linux de PITON es UTC (sin libc/TZ). Forzar TZ=UTC en CPython
    # para que localtime/strftime/asctime/ctime coincidan byte a byte.
    env = dict(os.environ)
    env["TZ"] = "UTC"
    proc = subprocess.run(
        [sys.executable, "-c", codigo_python],
        capture_output=True,
        timeout=TIMEOUT,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_piton(codigo_python: str) -> tuple[str, int | None, bytes | None, bytes | None, str]:
    try:
        piton_src = python_to_piton(codigo_python)
    except Exception as exc:
        return "BUILD_FAIL", None, None, None, f"translate: {type(exc).__name__}: {exc}"
    try:
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "prog"
            compile_native_linux(piton_src, exe)
            proc = subprocess.run(
                [str(exe)], capture_output=True, timeout=TIMEOUT
            )
        return "OK", proc.returncode, proc.stdout, proc.stderr, ""
    except Exception as exc:
        return "BUILD_FAIL", None, None, None, f"build: {type(exc).__name__}: {exc}"


def _veredicto_desde(rc_c, out_c, err_c, status, rc_p, out_p, err_p):
    if status == "BUILD_FAIL":
        return "BUILD_FAIL"
    if rc_c == 0:
        if rc_p == 0 and out_p == out_c and err_p == err_c:
            return "EQUIV"
        return "DIVERGENT"
    if rc_p != 0:
        return "EQUIV"
    return "DIVERGENT"


_CASES = [
    ("date_today_year",
     "from datetime import date\nd = date.today()\nprint(d.year)\n"),
    ("date_today_month",
     "from datetime import date\nd = date.today()\nprint(d.month)\n"),
    ("date_today_day",
     "from datetime import date\nd = date.today()\nprint(d.day)\n"),
    ("date_ctor_20031202",
     "from datetime import date\nprint(date(2003, 12, 2))\nprint(repr(date(2003, 12, 2)))\n"),
    ("date_add_sub_timedelta",
     "from datetime import date, timedelta\nda = date(2023, 1, 1)\ntd = timedelta(days=10)\nprint(da + td)\nprint((da + td) - td)\n"),
    ("timedelta_days_total",
     "from datetime import timedelta\ntd = timedelta(days=10, seconds=3600)\nprint(td.days)\nprint(td.total_seconds())\n"),
    ("time_localtime_index",
     "import time\nl = time.localtime(1735689600)\nprint(len(l))\nprint(l[0], l[1], l[2], l[3], l[4], l[5], l[6], l[7], l[8])\n"),
    ("time_gmtime_index",
     "import time\nl = time.gmtime(1735689600)\nprint(len(l))\nprint(l[0], l[1], l[2], l[3], l[4], l[5], l[6], l[7], l[8])\n"),
    ("time_strftime",
     "import time\nl = time.localtime(1735689600)\nprint(time.strftime('%Y-%m-%d', l))\nprint(time.strftime('%A, %d %B %Y', l))\n"),
    ("time_strftime_1arg",
     "import time\nprint(time.strftime('%Y'))\n"),
    ("time_asctime_ctime",
     "import time\nprint(time.asctime(time.localtime(1735689600)))\nprint(time.ctime(1735689600))\n"),
    ("date_strftime_mdy",
     "from datetime import date\nd = date(1964, 7, 31)\nprint(d.strftime('%m-%d-%y'))\nprint(d.strftime('%d %b %Y'))\nprint(d.strftime('%B'))\n"),
]


@pytest.mark.parametrize("nombre,codigo_python", _CASES)
def test_adv_datetime_parity(nombre, codigo_python):
    rc_c, out_c, err_c = run_cpython(codigo_python)
    status, rc_p, out_p, err_p, detail = run_piton(codigo_python)
    veredicto = _veredicto_desde(rc_c, out_c, err_c, status, rc_p, out_p, err_p)
    if veredicto != "EQUIV":
        pytest.fail(
            f"DIVERGENT [{nombre}]\n"
            f"  codigo: {codigo_python!r}\n"
            f"  status: {status} {detail}\n"
            f"  cpython: rc={rc_c} out={out_c!r} err={err_c!r}\n"
            f"  piton:   rc={rc_p} out={out_p!r} err={err_p!r}"
        )
