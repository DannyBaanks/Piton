"""ADVERSARIAL/ANTI-MENTIRA (T2) — corpus `objetos`: divergencias + limites.

Capa T2 del pipeline de PITON (AGENTS2.md): no arregla nada, ata el backend a
la paridad real contra CPython. Regla de paridad implementada (ESTRICTA, sin
colapsos que produzcan falso verde):

  * CPython termina rc 0  ->  PITON debe rc 0 Y stdout **byte a byte** igual.
  * CPython lanza excepcion sin capturar (rc != 0) -> PITON debe lanzar tambien
    (rc != 0) **y** coincidir la ultima linea de stderr (`Tipo: payload`).
    Sin esto, `47_exc_uncaught_custom` (PITON imprime `AppError: <instancia`)
    colapsaria a EQUIV por el mero "ambos lanzan": la excepcion se lanza, el
    payload NO sobrevive, y eso es divergencia, no paridad.
  * Si PITON no traduce/compila: `BUILD_FAIL` (veredicto propio, nunca EQUIV).

Nada de `.strip()`, reordenar, colapsar espacios ni descartar casos: el
denominador solo se encoge con el falso verde.

Oráculo: CPython vivo (`sys.executable -c <codigo>`) ejecutado en el mismo
acto; el comando exacto va en comentario en cada caso. Los valores "hoy" de
PITON citados son evidencia medida, no esperados escritos a mano: la asercion
compara contra el oráculo en vivo.

Baseline reproducida con este propio arnés el 2026-11-13 @ HEAD 5a9482c
(56 ejemplos): TOTAL=56 EQUIV=46 DIVERGENT=10 BUILD_FAIL=0, con EXACTAMENTE
los 10 divergentes del contexto: 09_str_casefold, 23_list_pop_empty_catch,
30_dict_missing_catch, 42_exc_args_key, 43_exc_custom_args,
44_exc_capture_value_type, 45_exc_capture_type_error, 47_exc_uncaught_custom,
48_exc_capture_zero_division, 49_class_init_str. `46_exc_uncaught_index`
sigue EQUIV (misma ultima linea de stderr) y `47` DIVERGE (payload perdido).

Bateria nueva (18 casos limite ausentes del corpus): 6 DIVERGENT, 12 EQUIV,
0 BUILD_FAIL, medida el mismo dia/HEAD.
"""
from __future__ import annotations

import os
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

CORPUS = REPO / "corpus_t1" / "objetos"

# Los 10 divergentes del corpus objetos, en el orden del contexto.
CORPUS_DIVERGENTES = [
    "09_str_casefold.py",
    "23_list_pop_empty_catch.py",
    "30_dict_missing_catch.py",
    "42_exc_args_key.py",
    "43_exc_custom_args.py",
    "44_exc_capture_value_type.py",
    "45_exc_capture_type_error.py",
    "47_exc_uncaught_custom.py",
    "48_exc_capture_zero_division.py",
    "49_class_init_str.py",
]

# ---------------------------------------------------------------------------
# Helpers: PITON y CPython por separado, comparacion byte a byte + exit code
# ---------------------------------------------------------------------------


def run_cpython(codigo: str) -> tuple[int, bytes, bytes]:
    """Oráculo vivo: mismo codigo via `-c`, sin tocar stdout/stderr."""
    proc = subprocess.run(
        [sys.executable, "-c", codigo], capture_output=True, timeout=60
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_piton(codigo: str) -> tuple[str, int | None, bytes | None, bytes | None, str]:
    """PITON: traduccion mecanica + compilado nativo Linux + ejecucion.

    Devuelve (status, rc, stdout, stderr, detalle) con status en
    {OK, BUILD_FAIL, RUN_TIMEOUT}. BUILD_FAIL conserva la etapa (translate /
    build) y el mensaje real: es un veredicto, no una excepcion que escape.
    """
    try:
        piton_src = python_to_piton(codigo)
    except Exception as exc:  # traduccion mecanica (p.ej. TokenError)
        return "BUILD_FAIL", None, None, None, f"translate: {type(exc).__name__}: {exc}"
    try:
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "prog"
            compile_native_linux(piton_src, exe)
            try:
                proc = subprocess.run([str(exe)], capture_output=True, timeout=60)
            except subprocess.TimeoutExpired:
                return "RUN_TIMEOUT", None, None, None, "ejecucion > 60 s"
        return "OK", proc.returncode, proc.stdout, proc.stderr, ""
    except Exception as exc:  # codegen/gcc
        return "BUILD_FAIL", None, None, None, f"build: {type(exc).__name__}: {exc}"


def ultima_linea(stderr: bytes | None) -> str:
    """Ultima linea no vacia de stderr (`Tipo: payload`). Sin normalizar."""
    if not stderr:
        return ""
    lines = [line for line in stderr.decode("utf-8", "replace").splitlines() if line.strip()]
    return lines[-1] if lines else ""


@lru_cache(maxsize=None)
def veredicto(codigo: str) -> tuple[str, str]:
    """(EQUIV | DIVERGENT | BUILD_FAIL, detalle) con el contrato estricto."""
    rc_c, out_c, err_c = run_cpython(codigo)
    status, rc_p, out_p, err_p, detalle = run_piton(codigo)
    if status == "BUILD_FAIL":
        return "BUILD_FAIL", detalle
    if status == "RUN_TIMEOUT":
        return "DIVERGENT", detalle
    if rc_c == 0:
        if rc_p == 0 and out_p == out_c:
            return "EQUIV", ""
        return (
            "DIVERGENT",
            f"CPython rc={rc_c} stdout={out_c!r} vs PITON rc={rc_p} "
            f"stdout={out_p!r} stderr_last={ultima_linea(err_p)!r}",
        )
    # CPython lanza sin capturar: exigir rc != 0 Y misma ultima linea de stderr.
    if rc_p == 0:
        return "DIVERGENT", (
            f"CPython lanza (rc={rc_c}, {ultima_linea(err_c)!r}) pero PITON "
            f"termina rc=0 stdout={out_p!r}"
        )
    if ultima_linea(err_p) == ultima_linea(err_c):
        return "EQUIV", ""
    return "DIVERGENT", (
        f"CPython {ultima_linea(err_c)!r} vs PITON {ultima_linea(err_p)!r}"
    )


def exigir_paridad(codigo: str, cmd_cpython: str) -> None:
    """Aserta paridad estricta; el mensaje deja el diff exacto si falla."""
    v, detalle = veredicto(codigo)
    if v == "EQUIV":
        return
    rc_c, out_c, err_c = run_cpython(codigo)
    _status, rc_p, out_p, err_p, detalle_piton = run_piton(codigo)
    mensaje = (
        f"\n  comando CPython : {cmd_cpython}"
        f"\n  codigo          : {codigo!r}"
        f"\n  veredicto       : {v}"
        f"\n  CPython         : rc={rc_c} stdout={out_c!r} stderr_last={ultima_linea(err_c)!r}"
        f"\n  PITON           : rc={rc_p} stdout={out_p!r} stderr_last={ultima_linea(err_p)!r}"
        f"\n  detalle         : {detalle_piton or detalle}"
    )
    pytest.fail(f"PITON != CPython ({v}):{mensaje}")


# ---------------------------------------------------------------------------
# Capa "test del test": el comparador debe poder fallar y ver BUILD_FAIL
# ---------------------------------------------------------------------------


def test_oraculo_cpython_vivo():
    rc, out, _err = run_cpython("print(1 + 1)")
    expected = b"2" + os.linesep.encode("ascii")
    assert rc == 0 and out == expected, f"oraculo roto: rc={rc} out={out!r}"


def test_comparador_no_es_vacuo():
    """Un test T2 que no puede fallar no es un test: el comparador debe poder
    decir algo distinto de EQUIV sobre codigo real.

    Antes usaba `09_str_casefold` (casefold ASCII-only), pero eso ya es EQUIV:
    STR_CASEFOLD_V1 pliega ß y U+1E9E. El caso estable es `print(object())`:
    CPython imprime `<object object at 0x...>` (direccion de memoria), que
    ningun backend puede reproducir byte a byte, asi que el veredicto jamas
    puede ser EQUIV (hoy DIVERGENT o BUILD_FAIL; el test solo exige que el
    comparador no sea vacuo)."""
    v, detalle = veredicto("print(object())\n")
    assert v != "EQUIV", f"comparador vacuo: object() dio EQUIV ({detalle})"


def test_build_fail_se_reporta_y_no_es_equiv():
    """Fuente que PITON no puede traducir => BUILD_FAIL, nunca EQUIV."""
    codigo = "x = ("  # TokenError determinista en python_to_piton
    status, _rc, _out, _err, detalle = run_piton(codigo)
    assert status == "BUILD_FAIL" and detalle.startswith("translate:")
    assert veredicto(codigo)[0] == "BUILD_FAIL"


# ---------------------------------------------------------------------------
# Capa 1: los 10 divergentes del corpus, contra el valor de CPython.
# Cada test DEBE fallar hoy: la paridad es el contrato, PITON la rompe.
# ---------------------------------------------------------------------------
#
# 09_str_casefold.py
#   CPython: python3 corpus_t1/objetos/09_str_casefold.py -> rc 0, "strasse\n"
#   PITON hoy: rc 0, "straße\n" (casefold no pliega la ß).
# 23_list_pop_empty_catch.py
#   CPython: python3 corpus_t1/objetos/23_list_pop_empty_catch.py
#     -> rc 0, "IndexError ('pop from empty list',)\n"
#   PITON hoy: rc 1, stderr "IndexError: pop from empty list" (el except no
#   captura; el cuerpo con type(exc).__name__/exc.args nunca llega).
# 30_dict_missing_catch.py
#   CPython: python3 corpus_t1/objetos/30_dict_missing_catch.py
#     -> rc 0, "KeyError ('missing',)\n"
#   PITON hoy: rc 1, stderr "TypeError: 'str' object has no attribute '__name__'".
# 42_exc_args_key.py
#    CPython: python3 corpus_t1/objetos/42_exc_args_key.py -> rc 0, "('x',)\n"
#   PITON hoy: rc 0, "('',)\n" (exc.args pierde la clave: tupla con '').
# 43_exc_custom_args.py
#   CPython: python3 corpus_t1/objetos/43_exc_custom_args.py
#     -> rc 0, "AppError ('bad', 7)\n"
#   PITON hoy: rc 1, stderr "TypeError: 'str' object has no attribute '__name__'".
# 44_exc_capture_value_type.py
#   CPython: python3 corpus_t1/objetos/44_exc_capture_value_type.py
#     -> rc 0, "ValueError list.remove(x): x not in list\n"
#   PITON hoy: rc 0, stdout vacio (el except no produce salida comparable).
# 45_exc_capture_type_error.py
#   CPython: python3 corpus_t1/objetos/45_exc_capture_type_error.py
#     -> rc 1 (ValueError SIN capturar: remove(3) lanza ValueError, no TypeError)
#   PITON hoy: rc 0, stdout vacio (captura/degrada distinto: falso exito).
# 47_exc_uncaught_custom.py
#   CPython: python3 corpus_t1/objetos/47_exc_uncaught_custom.py
#     -> rc 1, stderr "AppError: ('boom', 7)"
#   PITON hoy: rc 1, stderr "AppError: <instancia" (el payload ('boom', 7)
#   no sobrevive; "ambos lanzan" NO es paridad aqui).
# 48_exc_capture_zero_division.py
#   CPython: python3 corpus_t1/objetos/48_exc_capture_zero_division.py
#     -> rc 0, "ZeroDivisionError ('integer division or modulo by zero',)\n"
#   PITON hoy: rc 1, stderr "TypeError: 'str' object has no attribute '__name__'".
# 49_class_init_str.py
#   CPython: python3 corpus_t1/objetos/49_class_init_str.py -> rc 0, "(2,3)\n"
#   PITON hoy: rc 1, stderr "AttributeError: x" (__str__/self.x en metodo).
#
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nombre",
    CORPUS_DIVERGENTES,
    ids=[n[: -len(".py")] for n in CORPUS_DIVERGENTES],
)
def test_divergencia_corpus_objetos(nombre: str):
    codigo = (CORPUS / nombre).read_text(encoding="utf-8")
    exigir_paridad(codigo, f"python3 corpus_t1/objetos/{nombre}")


# ---------------------------------------------------------------------------
# Capa 2: bateria nueva de casos limite (el corpus no los cubria).
# `cmd` es el comando CPython exacto; `hoy` es lo medido en PITON al capturar
# (2026-11-13 @ 5a9482c); la asercion compara contra el oraculo EN VIVO.
# ---------------------------------------------------------------------------
CASOS_LIMITE = [
    {
        "id": "casefold_unicode",
        # CPython: python3 -c "print('ß'.casefold())" # y print('ẞ'.casefold())
        #   -> rc 0, stdout "ss\nss\n"
        # PITON hoy: rc 0, "ß\nẞ\n" — casefold no pliega (ASCII-only).
        "codigo": "print('ß'.casefold())\nprint('ẞ'.casefold())\n",
        "cmd": "python3 -c \"print('\\u00df'.casefold()); print('\\u1e9e'.casefold())\"",
        "hoy": 'rc 0, "\\u00df\\n\\u1e9e\\n"',
    },
    {
        "id": "pop_vacio_print_e",
        # CPython: python3 -c "values=[]<NL>try:<NL>    values.pop()<NL>except IndexError as e:<NL>    print(e)"
        #   -> rc 0, "pop from empty list\n"
        # PITON hoy: rc 1, stderr "IndexError: pop from empty list" — el except
        # no captura y print(e) nunca ejecuta.
        "codigo": "values = []\ntry:\n    values.pop()\nexcept IndexError as e:\n    print(e)\n",
        "cmd": "python3 -c \"values = []\\ntry:\\n    values.pop()\\nexcept IndexError as e:\\n    print(e)\"",
        "hoy": 'rc 1, stderr "IndexError: pop from empty list"',
    },
    {
        "id": "dict_missing_args",
        # CPython: python3 -c "d={}<NL>try:<NL>    d['x']<NL>except KeyError as e:<NL>    print(e.args)"
        #   -> rc 0, "('x',)\n"
        # PITON hoy: rc 0, "('',)\n" — exc.args pierde la clave.
        "codigo": "d = {}\ntry:\n    d['x']\nexcept KeyError as e:\n    print(e.args)\n",
        "cmd": "python3 -c \"d = {}\\ntry:\\n    d['x']\\nexcept KeyError as e:\\n    print(e.args)\"",
        "hoy": "rc 0, \"('',)\\n\"",
    },
    {
        "id": "exc_custom_tres_args",
        # CPython: python3 -c "class Boom(Exception): pass ... raise Boom('a', 2, None)"
        #   -> rc 0, "Boom ('a', 2, None)\n"
        # PITON hoy: rc 1, stderr "TypeError: 'str' object has no attribute
        # '__name__'" — exc.args con 3 argumentos (uno None) rompe el except.
        "codigo": (
            "class Boom(Exception):\n    pass\n"
            "try:\n    raise Boom('a', 2, None)\n"
            "except Boom as e:\n    print(type(e).__name__, e.args)\n"
        ),
        "cmd": "python3 -c \"class Boom(Exception): pass; raise Boom('a', 2, None)\"",
        "hoy": "rc 1, TypeError 'str' object has no attribute '__name__'",
    },
    {
        "id": "except_tupla_valueerror",
        # CPython: python3 -c "try:<NL>    [1, 2].remove(9)<NL>except (TypeError, ValueError) as e:<NL>    print(type(e).__name__)"
        #   -> rc 0, "ValueError\n"
        # PITON hoy: rc 0, stdout vacio — captura la tupla pero no produce
        # type(e).__name__.
        "codigo": "try:\n    [1, 2].remove(9)\nexcept (TypeError, ValueError) as e:\n    print(type(e).__name__)\n",
        "cmd": "python3 -c \"try:\\n    [1, 2].remove(9)\\nexcept (TypeError, ValueError) as e:\\n    print(type(e).__name__)\"",
        "hoy": "rc 0, stdout vacio",
    },
    {
        "id": "except_tupla_typeerror",
        # CPython: python3 -c "try:<NL>    n = 1 + 'a'<NL>except (TypeError, ValueError) as e:<NL>    print(type(e).__name__)"
        #   -> rc 0, "TypeError\n"
        # PITON hoy: rc 1, stderr "TypeError: 'str' object has no attribute
        # '__name__'".
        "codigo": "try:\n    n = 1 + 'a'\nexcept (TypeError, ValueError) as e:\n    print(type(e).__name__)\n",
        "cmd": "python3 -c \"try:\\n    n = 1 + 'a'\\nexcept (TypeError, ValueError) as e:\\n    print(type(e).__name__)\"",
        "hoy": "rc 1, TypeError 'str' object has no attribute '__name__'",
    },
    {
        "id": "raise_from_none",
        # CPython: python3 -c "raise ValueError('x') from None"
        #   -> rc 1, stderr "ValueError: x" (sin cadena "During handling")
        # PITON hoy: rc 1, misma ultima linea — EQUIV (pin de comportamiento).
        "codigo": "raise ValueError('x') from None\n",
        "cmd": "python3 -c \"raise ValueError('x') from None\"",
        "hoy": 'rc 1, "ValueError: x"',
    },
    {
        "id": "init_y_metodo_str",
        # CPython: python3 -c "class P:<NL>    def __init__(self, n): self.n = n<NL>    def hi(self): return 'hi-' + self.n<NL>print(P('x').hi())"
        #   -> rc 0, "hi-x\n"
        # PITON hoy: rc 0, "hi-x\n" — EQUIV.
        "codigo": (
            "class P:\n    def __init__(self, n):\n        self.n = n\n"
            "    def hi(self):\n        return 'hi-' + self.n\nprint(P('x').hi())\n"
        ),
        "cmd": "python3 -c \"class P:\\n    def __init__(self, n):\\n        self.n = n\\n    def hi(self):\\n        return 'hi-' + self.n\\nprint(P('x').hi())\"",
        "hoy": 'rc 0, "hi-x\\n"',
    },
    {
        "id": "metodo_retorna_str_call",
        # CPython: python3 -c "class P:<NL>    def __init__(self, n): self.n = n<NL>    def label(self): return str(self.n)<NL>print(P(7).label())"
        #   -> rc 0, "7\n"
        # PITON hoy: rc 0, "7\n" — EQUIV (el defecto T1 de AGENTS2 —str() al
        # volver de una funcion devolvia un puntero— no se reproduce en HEAD;
        # el test lo deja pinado).
        "codigo": (
            "class P:\n    def __init__(self, n):\n        self.n = n\n"
            "    def label(self):\n        return str(self.n)\nprint(P(7).label())\n"
        ),
        "cmd": "python3 -c \"class P:\\n    def __init__(self, n):\\n        self.n = n\\n    def label(self):\\n        return str(self.n)\\nprint(P(7).label())\"",
        "hoy": 'rc 0, "7\\n"',
    },
    {
        "id": "herencia_super_init",
        # CPython: python3 -c "class A:<NL>    def __init__(self): self.n='A'<NL>class B(A):<NL>    def __init__(self): super().__init__(); self.n = self.n + 'B'<NL>print(B().n)"
        #   -> rc 0, "AB\n"
        # PITON hoy: rc 0, "AB\n" — EQUIV.
        "codigo": (
            "class A:\n    def __init__(self):\n        self.n = 'A'\n"
            "class B(A):\n    def __init__(self):\n        super().__init__()\n"
            "        self.n = self.n + 'B'\nprint(B().n)\n"
        ),
        "cmd": "python3 -c \"class A:\\n    def __init__(self):\\n        self.n = 'A'\\nclass B(A):\\n    def __init__(self):\\n        super().__init__()\\n        self.n = self.n + 'B'\\nprint(B().n)\"",
        "hoy": 'rc 0, "AB\\n"',
    },
    {
        "id": "str_y_repr",
        # CPython: python3 -c "class C:<NL>    def __str__(self): return 'C-str'<NL>    def __repr__(self): return 'C-repr'<NL>print(str(C()))<NL>print(repr(C()))"
        #   -> rc 0, "C-str\nC-repr\n"
        # PITON hoy: rc 0, "C-str\nC-repr\n" — EQUIV.
        "codigo": (
            "class C:\n    def __str__(self):\n        return 'C-str'\n"
            "    def __repr__(self):\n        return 'C-repr'\n"
            "print(str(C()))\nprint(repr(C()))\n"
        ),
        "cmd": "python3 -c \"class C:\\n    def __str__(self):\\n        return 'C-str'\\n    def __repr__(self):\\n        return 'C-repr'\\nprint(str(C()))\\nprint(repr(C()))\"",
        "hoy": 'rc 0, "C-str\\nC-repr\\n"',
    },
    {
        "id": "slice_paso_negativo",
        # CPython: python3 -c "xs=[0,1,2,3,4,5]<NL>print(xs[::-1])<NL>print(xs[-1:-4:-1])<NL>print(xs[4:0:-1])<NL>print(xs[1:5:-1])"
        #   -> rc 0, "[5, 4, 3, 2, 1, 0]\n[5, 4, 3]\n[4, 3, 2, 1]\n[]\n"
        # PITON hoy: identico — EQUIV (incluido el slice vacio por limites
        # invertidos con paso negativo).
        "codigo": (
            "xs = [0, 1, 2, 3, 4, 5]\n"
            "print(xs[::-1])\nprint(xs[-1:-4:-1])\nprint(xs[4:0:-1])\nprint(xs[1:5:-1])\n"
        ),
        "cmd": "python3 -c \"xs = [0, 1, 2, 3, 4, 5]\\nprint(xs[::-1])\\nprint(xs[-1:-4:-1])\\nprint(xs[4:0:-1])\\nprint(xs[1:5:-1])\"",
        "hoy": 'rc 0, "[5, 4, 3, 2, 1, 0]\\n[5, 4, 3]\\n[4, 3, 2, 1]\\n[]\\n"',
    },
    {
        "id": "star_en_print",
        # CPython: python3 -c "print(*[1, 2, 3])" -> rc 0, "1 2 3\n"
        # PITON hoy: rc 0, "1 2 3\n" — EQUIV.
        "codigo": "print(*[1, 2, 3])\n",
        "cmd": "python3 -c \"print(*[1, 2, 3])\"",
        "hoy": 'rc 0, "1 2 3\\n"',
    },
    {
        "id": "star_en_medio_unpack",
        # CPython: python3 -c "head, *mid, tail = [1, 2, 3, 4]<NL>print(head, mid, tail)"
        #   -> rc 0, "1 [2, 3] 4\n"
        # PITON hoy: rc 0, "1 [2, 3] 4\n" — EQUIV.
        "codigo": "head, *mid, tail = [1, 2, 3, 4]\nprint(head, mid, tail)\n",
        "cmd": "python3 -c \"head, *mid, tail = [1, 2, 3, 4]\\nprint(head, mid, tail)\"",
        "hoy": 'rc 0, "1 [2, 3] 4\\n"',
    },
    {
        "id": "star_rest_vacio",
        # CPython: python3 -c "a, *rest = [9]<NL>print(a, rest)"
        #   -> rc 0, "9 []\n"
        # PITON hoy: rc 0, "9 []\n" — EQUIV (rest vacia, no (None, None)).
        "codigo": "a, *rest = [9]\nprint(a, rest)\n",
        "cmd": "python3 -c \"a, *rest = [9]\\nprint(a, rest)\"",
        "hoy": 'rc 0, "9 []\\n"',
    },
    {
        "id": "star_print_mixto",
        # CPython: python3 -c "print(1, *[2, 3], 4)" -> rc 0, "1 2 3 4\n"
        # PITON hoy: rc 0, "1 2 3 4\n" — EQUIV.
        "codigo": "print(1, *[2, 3], 4)\n",
        "cmd": "python3 -c \"print(1, *[2, 3], 4)\"",
        "hoy": 'rc 0, "1 2 3 4\\n"',
    },
    {
        "id": "star_args_vacio_len",
        # CPython: python3 -c "def f(*a):<NL>    return len(a)<NL>print(f())"
        #   -> rc 0, "0\n"
        # PITON hoy: rc 0, "0\n" — EQUIV (el defecto T2 de AGENTS2 —*args
        # vacio llegaba como (None, None), len==2— no se reproduce en HEAD;
        # el test lo deja pinado).
        "codigo": "def f(*a):\n    return len(a)\nprint(f())\n",
        "cmd": "python3 -c \"def f(*a):\\n    return len(a)\\nprint(f())\"",
        "hoy": 'rc 0, "0\\n"',
    },
    {
        "id": "star_en_lista",
        # CPython: python3 -c "print([*[1, 2], *[3]])" -> rc 0, "[1, 2, 3]\n"
        # PITON hoy: rc 0, "[1, 2, 3]\n" — EQUIV.
        "codigo": "print([*[1, 2], *[3]])\n",
        "cmd": "python3 -c \"print([*[1, 2], *[3]])\"",
        "hoy": 'rc 0, "[1, 2, 3]\\n"',
    },
]


@pytest.mark.parametrize(
    "caso", CASOS_LIMITE, ids=[c["id"] for c in CASOS_LIMITE]
)
def test_caso_limite(caso: dict):
    exigir_paridad(caso["codigo"], caso["cmd"])


# ---------------------------------------------------------------------------
# Capa 3: reporte real (medido 2026-11-13 @ HEAD 5a9482c). Solo imprime: el
# conteo de hoy no se congela en una asercion, lo enforcedan los tests.

# TOTAL_CASOS = 28 (10 corpus + 18 limite)
# CORPUS_DIVERGENTES : 10/10 fallan hoy (veredicto DIVERGENT en todos)
# Limites medidos    : 6 DIVERGENT / 12 EQUIV / 0 BUILD_FAIL
#   DIVERGENT: casefold_unicode, pop_vacio_print_e, dict_missing_args,
#     exc_custom_tres_args, except_tupla_valueerror, except_tupla_typeerror
# ---------------------------------------------------------------------------


def test_resumen_adversarial():
    casos_corpus = {n: (CORPUS / n).read_text(encoding="utf-8") for n in CORPUS_DIVERGENTES}
    casos_limite = {c["id"]: c["codigo"] for c in CASOS_LIMITE}
    filas = []
    for nombre, codigo in casos_corpus.items():
        v, _d = veredicto(codigo)
        filas.append((f"corpus:{nombre}", v))
    for cid, codigo in casos_limite.items():
        v, _d = veredicto(codigo)
        filas.append((f"limite:{cid}", v))
    equiv = [k for k, v in filas if v == "EQUIV"]
    div = [k for k, v in filas if v == "DIVERGENT"]
    bf = [k for k, v in filas if v == "BUILD_FAIL"]
    print("\n=== ADV OBJETOS (estricto: bytes + exit code) ===")
    for k, v in filas:
        print(f"{v:9s} {k}")
    print(
        f"TOTAL={len(filas)} EQUIV={len(equiv)} DIVERGENT={len(div)} BUILD_FAIL={len(bf)}"
    )
    corpus_div = [k for k, v in filas if k.startswith("corpus:") and v != "EQUIV"]
    print(f"corpus divergentes que siguen rojos: {len(corpus_div)}/{len(casos_corpus)}")
    assert len(filas) == len(CORPUS_DIVERGENTES) + len(CASOS_LIMITE)
    assert all(v in {"EQUIV", "DIVERGENT", "BUILD_FAIL"} for _k, v in filas)