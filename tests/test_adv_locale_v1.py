"""ADVERSARIAL locale (C/POSIX) — paridad byte a byte con CPython 3.12.

Sigue el patron de tests/test_adv_datetime_v1.py: traduce Python -> PITON con
python_to_piton, compila a ELF nativo Linux con compile_native_linux, ejecuta
y compara stdout y exit-code contra CPython 3.12 SIN normalizar (AGENTS2 §1).
Una excepcion en ambos lados cuenta como equivalente (se imprime 'EXC').

Alcance cubierto (piton/stdlib_piton/locale.piton):
  * setlocale(categoria, nombre): "C", "POSIX", "C.UTF-8" y categorias 0..6.
    Nombres no C ('English_United States.1252', 'xx_YY') y categorias
    invalidas lanzan locale.Error('unsupported locale setting').
  * localeconv(): las 18 claves del locale C; valores enteros (127, ...).
  * format_string(): %d %i %u %s %f %F %x %X %o %%, banderas, ancho, precision,
    '*' (ancho y precision), tuplas, valores sueltos, %.127f (frac_digits).

Limites conocidos (xfail estricto, no son paridad):
  * %e %E %g %G %c %r %a: NotImplementedError en el backend nativo.
  * Enteros (%d %x ...) por encima de 2^53: NotImplementedError (no exactos).

Entorno: setlocale('') y setlocale(LC_ALL) (consulta) dependen del entorno
de CPython (LANG/LC_*), asi que no se comparan con CPython. Tampoco los
locales instalados en el host (p. ej. es_ES.UTF-8 aqui): CPython los acepta
y PITON solo conoce la familia C, asi que los lanza como Error.
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
from native_linux import compile_native_linux  # noqa: E402

TIMEOUT = 300


def run_cpython(codigo_python: str) -> tuple[int, bytes, bytes]:
    proc = subprocess.run(
        [sys.executable, "-I", "-c", codigo_python],
        capture_output=True,
        timeout=TIMEOUT,
        stdin=subprocess.DEVNULL,
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
                [str(exe)], capture_output=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL
            )
        return "OK", proc.returncode, proc.stdout, proc.stderr, ""
    except Exception as exc:
        return "BUILD_FAIL", None, None, None, f"build: {type(exc).__name__}: {exc}"


def assert_parity(codigo_python: str) -> None:
    status, rc_p, out_p, err_p, motivo = run_piton(codigo_python)
    assert status == "OK", f"no compila en nativo: {motivo}"
    rc_c, out_c, err_c = run_cpython(codigo_python)
    assert (rc_p, out_p) == (rc_c, out_c), (
        f"\nnativo rc={rc_p} stdout={out_p!r}\nstderr={err_p!r}"
        f"\ncpython rc={rc_c} stdout={out_c!r}\nstderr={err_c!r}"
    )


# --- setlocale / errores -----------------------------------------------------

SETLOCALE_C = """\
import locale
print(locale.setlocale(locale.LC_ALL, 'C'))
print(locale.setlocale(locale.LC_ALL, 'POSIX'))
print(locale.setlocale(locale.LC_NUMERIC, 'C'))
print(locale.setlocale(locale.LC_ALL, 'C.UTF-8'))
print(locale.setlocale(locale.LC_TIME, 'C.utf8'))
print(locale.setlocale(locale.LC_ALL, 'C'))
"""


def test_setlocale_c_y_posix_devuelven_el_nombre_pedido() -> None:
    assert_parity(SETLOCALE_C)


SETLOCALE_ERRORES = """\
import locale
try:
    locale.setlocale(locale.LC_ALL, 'English_United States.1252')
    print('NO ERROR')
except locale.Error:
    print('Error 1')
try:
    locale.setlocale(locale.LC_ALL, 'xx_YY.UTF-8')
    print('NO ERROR')
except locale.Error:
    print('Error 2')
try:
    locale.setlocale(99, 'C')
    print('NO ERROR')
except locale.Error:
    print('Error 3')
print(locale.setlocale(locale.LC_ALL, 'C'))
"""


def test_setlocale_nombre_invalido_lanza_locale_error() -> None:
    assert_parity(SETLOCALE_ERRORES)


# --- localeconv -----------------------------------------------------------------

LOCALECONV_CLAVES = """\
import locale
conv = locale.localeconv()
print(len(conv))
print(conv['frac_digits'], conv['int_frac_digits'], conv['p_cs_precedes'])
print(conv['n_sign_posn'], conv['grouping'], conv['mon_grouping'])
print(conv['decimal_point'] == '.', conv['thousands_sep'] == '')
"""


def test_localeconv_claves_y_valores_del_locale_c() -> None:
    assert_parity(LOCALECONV_CLAVES)


# --- format_string ---------------------------------------------------------------

# Cada caso: (formato, valor-en-texto-python). Todos son paridad exacta hoy.
FORMAT_CASOS_OK = [
    ("'%d'", "1234567.8"),
    ("'%d'", "-7.9"),
    ("'%5d'", "42"),
    ("'%05d'", "-42"),
    ("'%+d'", "42"),
    ("'%.3d'", "7"),
    ("'%s'", "'abc'"),
    ("'%s'", "7"),
    ("'%s'", "2.5"),
    ("'%.2s'", "'abcdef'"),
    ("'%f'", "2.5"),
    ("'%.2f'", "3.14159"),
    ("'%10.3f'", "3.14159"),
    ("'%-10.3f'", "3.14159"),
    ("'%010.2f'", "-3.14159"),
    ("'%+.1f'", "2.25"),
    ("'%.0f'", "2.5"),
    ("'%5.2f'", "2.5"),
    ("'%x'", "255"),
    ("'%#x'", "255"),
    ("'%X'", "255"),
    ("'%o'", "8"),
    ("'%%'", "''"),
    ("'%d %s'", "(7, 'ab')"),
    ("'%.127f'", "1234567.8"),
    ("'%.*f'", "(2, 2.5)"),
    ("'%s%.*f'", "('$', 2, 1234567.8)"),
    ("'%d'", "(7, 8)"),
]


def _matriz_format() -> str:
    lineas = ["import locale", "def caso(fmt, val):", "    try:",
              "        print(repr(locale.format_string(fmt, val)))",
              "    except Exception:", "        print('EXC')"]
    for fmt, val in FORMAT_CASOS_OK:
        lineas.append(f"caso({fmt}, {val})")
    return "\n".join(lineas) + "\n"


def test_format_string_matriz_de_casos() -> None:
    assert_parity(_matriz_format())


FORMAT_EJEMPLO_11 = """\
import locale
x = 1234567.8
print(locale.format_string("%d", x, grouping=True))
"""


def test_format_string_ejemplo_corpus_d_con_grouping() -> None:
    assert_parity(FORMAT_EJEMPLO_11)


FORMAT_EJEMPLO_14 = """\
import locale
conv = locale.localeconv()
x = 1234567.8
print(locale.format_string("%s%.*f", (conv['currency_symbol'], conv['frac_digits'], x), grouping=True))
"""


def test_format_string_ejemplo_corpus_moneda_con_conv() -> None:
    assert_parity(FORMAT_EJEMPLO_14)


@pytest.mark.xfail(strict=True, reason="%e no implementado en el backend nativo (NotImplementedError)")
def test_format_string_exponencial_no_implementado() -> None:
    assert_parity("import locale\nprint(locale.format_string('%e', 2.5))\n")


def test_format_string_mapping_entero() -> None:
    assert_parity("import locale\nprint(locale.format_string('%(a)d', {'a': 1}))\n")


def test_format_string_mapping_varias_claves() -> None:
    assert_parity(
        "import locale\n"
        "print(locale.format_string('%(n)s=%(v)5.2f|%(n)-4s|%%', {'n': 'x', 'v': 3.14159}))\n"
    )


@pytest.mark.xfail(strict=True, reason="enteros >= 2^53 no exactos en el backend nativo (NotImplementedError)")
def test_format_string_entero_grande_no_implementado() -> None:
    assert_parity("import locale\nprint(locale.format_string('%d', 1e20))\n")
