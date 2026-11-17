"""ADVERSARIAL try:/if/def imports — paridad byte a byte con CPython 3.12.

Cubre imports de modulos empaquetados y nativos dentro de bloques `intentar`
(try) y funciones, que antes fallaban en _lower_stmt (piton/mir.py):
  1. `importar time, os.path` dentro de `intentar` (dotted import empaquetado).
  2. `desde datetime importar date` dentro de `intentar` (carga pereza con
     estado de modulo, imports transitivos y funciones de nivel de modulo).
  3. `desde modulo importar *` dentro de `intentar` (star import, incluido
     modulo builtin math).

Sigue el patron de tests/test_adv_datetime_v1.py: traduce Python -> PITON con
python_to_piton, compila a ELF nativo Linux con compile_native_linux, ejecuta
y compara stdout/stderr/exit-code contra CPython 3.12 SIN normalizar
(AGENTS2 §1). Una excepcion en ambos lados cuenta como equivalente.

LIMITES PREEXISTENTES (no introducidos por este cambio):
  * `date - date` (resta de fechas) devuelve null object en el backend nativo
    tanto a nivel de modulo como dentro de intentar (bug preexistente).
  * `collections.Counter('abracadabra')` falla con TypeError tanto a nivel de
    modulo como dentro de intentar (bug preexistente en collections.piton).
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
    # 1. from datetime import date dentro de intentar
    (
        "datetime_date_in_try",
        """
try:
    from datetime import date
except BaseException:
    print('EXC')
try:
    now = date.today()
    print(now)
    print(now.strftime("%m-%d-%y"))
    birthday = date(1964, 7, 31)
    print(birthday)
except BaseException as e:
    print('EXC', type(e).__name__, e)
""",
    ),
    # 2. import time, os.path dentro de intentar
    (
        "import_time_os_path_in_try",
        """
try:
    import time, os.path
except BaseException:
    print('EXC')
try:
    print(os.path.basename("/a/b.txt"))
    print(os.path.join("a", "b"))
    print(time.strftime("%Y"))
except BaseException as e:
    print('EXC', type(e).__name__, e)
""",
    ),
    # 3. from collections import * dentro de intentar (deque + namedtuple)
    (
        "collections_star_in_try",
        """
try:
    from collections import *
except BaseException:
    print('EXC')
try:
    Point = namedtuple('Point', ['x', 'y'])
    p = Point(1, 2)
    print(p.x, p.y)
    d = deque(["task1", "task2"])
    d.append("task3")
    print(d.popleft())
    print(d.popleft())
except BaseException as e:
    print('EXC', type(e).__name__, e)
""",
    ),
    # 4. from math import * dentro de intentar
    (
        "math_star_in_try",
        """
try:
    from math import *
except BaseException:
    print('EXC')
try:
    print(sqrt(16))
    print(pi)
    print(floor(3.7))
except BaseException as e:
    print('EXC', type(e).__name__, e)
""",
    ),
    # 5. import os.path as p dentro de intentar (alias con punto)
    (
        "import_os_path_as_p_in_try",
        """
try:
    import os.path as p
except BaseException:
    print('EXC')
try:
    print(p.basename("/a/b.txt"))
    print(p.join("a", "b"))
except BaseException as e:
    print('EXC', type(e).__name__, e)
""",
    ),
    (
        "datetime_date_sub_in_try",
        """
try:
    from datetime import date
except BaseException:
    print('EXC')
try:
    now = date.today()
    birthday = date(1964, 7, 31)
    age = now - birthday
    print(age.days)
except BaseException as e:
    print('EXC', type(e).__name__, e)
""",
    ),
]


@pytest.mark.parametrize("nombre,codigo_python", _CASES)
def test_adv_try_import_parity(nombre, codigo_python):
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
