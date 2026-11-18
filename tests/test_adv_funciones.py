"""ADVERSARIAL funciones — retorno de funcion de libreria (corpus funciones).

Punto ciego AGENTS2 §T7: sentencia de modulo vs valor de retorno que cruza la
frontera de una funcion. Paridad estricta CPython 3.12 vs PITON: stdout byte a
byte + exit code; en excepcion, tipo + mensaje + rc. Sin normalizar nada.

Cada caso de esta bateria FALLA contra el PITON de antes del fix que lo cerro:
- `combinar_shadow_*`: el alias dialectal de zip se comia una funcion del
  usuario llamada `combinar` (corpus_t1/funciones/34 moria en BUILD con
  "print of a iterator:zip").
- `product_*`: itertools.product no existia (NotImplementedError) y, con la
  implementacion ingenua, las tuplas mixtas salian con punteros crudos.
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
# ZIP_ALIAS_SHADOW_V1 — `combinar` del usuario no es el builtin zip
# ---------------------------------------------------------------------------

def test_combinar_shadow_math_comb():
    # Antes: BUILD_FAIL "native print of a iterator:zip value is not supported"
    codigo = (
        "import math\n"
        "def combinar(n, k):\n"
        "    return math.comb(n, k)\n"
        "print(combinar(5, 2))\n"
    )
    exigir_paridad(codigo, "python3 -c \"import math; ...; print(10)\"")


def test_combinar_shadow_int_plano():
    codigo = (
        "def combinar(n, k):\n"
        "    return n + k\n"
        "print(combinar(5, 2))\n"
    )
    exigir_paridad(codigo, 'python3 -c "print(7)"')


def test_combinar_shadow_no_rompe_zip():
    # El alias dialectal de zip sigue vivo cuando NO hay def que lo sombree.
    codigo = "print(list(zip([1, 2], [3, 4])))\n"
    exigir_paridad(codigo, 'python3 -c "print(list(zip([1, 2], [3, 4])))"')


# ---------------------------------------------------------------------------
# PRODUCT_V1 — itertools.product dentro de una funcion
# ---------------------------------------------------------------------------

def test_product_dos_iterables_mixtos():
    # Antes: NotImplementedError; con lista(param) salian punteros crudos.
    codigo = (
        "import itertools\n"
        "def combinar(a, b):\n"
        "    return list(itertools.product(a, b))\n"
        "print(combinar([1, 2], ['a', 'b']))\n"
    )
    exigir_paridad(codigo, "python3 -c \"...\" print(combinar([1, 2], ['a', 'b']))")


def test_product_un_iterable():
    codigo = (
        "import itertools\n"
        "def combinar(a):\n"
        "    return list(itertools.product(a))\n"
        "print(combinar([1, 2]))\n"
    )
    exigir_paridad(codigo, "python3 -c \"...\" print(combinar([1, 2]))")


def test_product_tres_iterables():
    codigo = (
        "import itertools\n"
        "def combinar(a, b, c):\n"
        "    return list(itertools.product(a, b, c))\n"
        "print(combinar([1, 2], [3, 4], ['a']))\n"
    )
    exigir_paridad(codigo, "python3 -c \"...\" print(combinar([1, 2], [3, 4], ['a']))")


def test_product_solo_enteros():
    codigo = (
        "import itertools\n"
        "def combinar(a, b):\n"
        "    return list(itertools.product(a, b))\n"
        "print(combinar([1, 2], [3, 4]))\n"
    )
    exigir_paridad(codigo, "python3 -c \"...\" print(combinar([1, 2], [3, 4]))")


# ---------------------------------------------------------------------------
# Cierre: math.* devuelto por una funcion de usuario
# ---------------------------------------------------------------------------

CASOS = [
    {
        "id": "math_comb_funcion",
        "codigo": "import math\ndef f(n, k):\n    return math.comb(n, k)\nprint(f(6, 2))\n",
        "cmd": "python3 -c \"import math; print(math.comb(6, 2))\"",
    },
    {
        "id": "math_perm_funcion",
        "codigo": "import math\ndef f(n, k):\n    return math.perm(n, k)\nprint(f(5, 2))\n",
        "cmd": "python3 -c \"import math; print(math.perm(5, 2))\"",
    },
    {
        "id": "math_factorial_funcion",
        "codigo": "import math\ndef f(n):\n    return math.factorial(n)\nprint(f(6))\n",
        "cmd": "python3 -c \"import math; print(math.factorial(6))\"",
    },
    {
        "id": "math_gcd_funcion",
        "codigo": "import math\ndef f(a, b):\n    return math.gcd(a, b)\nprint(f(48, 18))\n",
        "cmd": "python3 -c \"import math; print(math.gcd(48, 18))\"",
    },
    {
        "id": "math_sqrt_funcion",
        "codigo": "import math\ndef f(n):\n    return math.sqrt(n)\nprint(f(16))\n",
        "cmd": "python3 -c \"import math; print(math.sqrt(16))\"",
    },
    {
        "id": "math_isqrt_negativo",
        "codigo": "import math\ndef f(n):\n    return math.isqrt(n)\nprint(f(-1))\n",
        "cmd": "python3 -c \"import math; print(math.isqrt(-1))\"",
    },
]


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
def test_caso_funciones(caso):
    exigir_paridad(caso["codigo"], caso["cmd"])


# ---------------------------------------------------------------------------
# Resumen (integridad del conteo; el verde real es cada test de arriba)
# ---------------------------------------------------------------------------

def test_resumen_adversarial_funciones():
    fijos = [
        ("combinar_shadow_math_comb", "import math\ndef combinar(n, k):\n    return math.comb(n, k)\nprint(combinar(5, 2))\n"),
        ("product_dos_iterables_mixtos", "import itertools\ndef combinar(a, b):\n    return list(itertools.product(a, b))\nprint(combinar([1, 2], ['a', 'b']))\n"),
    ]
    casos = fijos + [(c["id"], c["codigo"]) for c in CASOS]
    filas = []
    for cid, codigo in casos:
        v, _ = veredicto(codigo)
        filas.append((cid, v))
    print("\n=== ADV FUNCIONES ===")
    for k, v in filas:
        print(f"{v:9s} {k}")
    equiv = sum(1 for _, v in filas if v == "EQUIV")
    print(f"TOTAL={len(filas)} EQUIV={equiv}")
    assert len(filas) == 2 + len(CASOS)


# ---------------------------------------------------------------------------
# DEQUE_ITER_V1 / DEQUE_MAXLEN_V1 / deque.count
# Antes: `list(q)` moria con "cannot convert to a sequence", `deque.count`
# no existia (el linker fallaba en `deque__count`), y maxlen se ignoraba.
# ---------------------------------------------------------------------------

DEQUE_CASOS = [
    {
        "id": "deque_append_extremos",
        "codigo": (
            "from collections import deque\n"
            "def operar(datos):\n"
            "    q = deque(datos)\n"
            "    q.append(4)\n"
            "    q.appendleft(0)\n"
            "    return list(q)\n"
            "print(operar([1, 2, 3]))\n"
        ),
        "cmd": "python3 -c \"from collections import deque; ...\"",
    },
    {
        "id": "deque_pops",
        "codigo": (
            "from collections import deque\n"
            "def operar(datos):\n"
            "    q = deque(datos)\n"
            "    ultimo = q.pop()\n"
            "    primero = q.popleft()\n"
            "    return list(q)\n"
            "print(operar([1, 2, 3, 4]))\n"
        ),
        "cmd": "python3 -c \"from collections import deque; ...\"",
    },
    {
        "id": "deque_maxlen",
        "codigo": (
            "from collections import deque\n"
            "def acotar(datos, n):\n"
            "    q = deque(datos, n)\n"
            "    return list(q)\n"
            "print(acotar([1, 2, 3, 4], 2))\n"
        ),
        "cmd": "python3 -c \"from collections import deque; ...\"",
    },
    {
        "id": "deque_count",
        "codigo": (
            "from collections import deque\n"
            "def contar(datos, valor):\n"
            "    q = deque(datos)\n"
            "    return q.count(valor)\n"
            "print(contar([1, 2, 2, 3, 2], 2))\n"
        ),
        "cmd": "python3 -c \"from collections import deque; ...\"",
    },
]


@pytest.mark.parametrize("caso", DEQUE_CASOS, ids=[c["id"] for c in DEQUE_CASOS])
def test_caso_deque(caso):
    exigir_paridad(caso["codigo"], caso["cmd"])


# ---------------------------------------------------------------------------
# ISLICE_ARGS_V1 — islice(it, stop) NO es islice(it, start)
# ---------------------------------------------------------------------------

ISLICE_CASOS = [
    {
        "id": "islice_stop_only",
        "codigo": (
            "import itertools\n"
            "def cortar(datos, n):\n"
            "    return list(itertools.islice(datos, n))\n"
            "print(cortar(['a', 'b', 'c', 'd'], 3))\n"
        ),
        "cmd": "python3 -c \"import itertools; print(list(itertools.islice('abcd', 3)))\"",
    },
    {
        "id": "islice_start_stop",
        "codigo": (
            "import itertools\n"
            "def cortar(datos, a, b):\n"
            "    return list(itertools.islice(datos, a, b))\n"
            "print(cortar([1, 2, 3, 4, 5, 6], 2, 5))\n"
        ),
        "cmd": "python3 -c \"import itertools; print(list(itertools.islice(range(1, 7), 2, 5)))\"",
    },
    {
        "id": "islice_paso",
        "codigo": (
            "import itertools\n"
            "print(list(itertools.islice([1, 2, 3, 4, 5], 0, 5, 2)))\n"
        ),
        "cmd": "python3 -c \"import itertools; print(list(itertools.islice([1,2,3,4,5], 0, 5, 2)))\"",
    },
    {
        "id": "islice_stop_mayor_que_largo",
        "codigo": "import itertools\nprint(list(itertools.islice([1, 2, 3], 10)))\n",
        "cmd": "python3 -c \"import itertools; print(list(itertools.islice([1,2,3], 10)))\"",
    },
]


@pytest.mark.parametrize("caso", ISLICE_CASOS, ids=[c["id"] for c in ISLICE_CASOS])
def test_caso_islice(caso):
    exigir_paridad(caso["codigo"], caso["cmd"])


# ---------------------------------------------------------------------------
# REPR_LIST_V1 / STR_OF_SLOT_V1
# Antes: reprlib.repr(lista) no truncaba, y un elemento 0 se imprimia "None"
# (str() perdia la kind del slot y piton_str_of_value(0) es "None").
# ---------------------------------------------------------------------------

def test_reprlib_lista_truncada():
    codigo = (
        "import reprlib\n"
        "def mostrar(dato):\n"
        "    return reprlib.repr(dato)\n"
        "print(mostrar([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]))\n"
    )
    exigir_paridad(codigo, "python3 -c \"import reprlib; print(reprlib.repr(list(range(10))))\"")


def test_reprlib_lista_corta_sin_elipsis():
    codigo = (
        "import reprlib\n"
        "def mostrar(dato):\n"
        "    return reprlib.repr(dato)\n"
        "print(mostrar([1, 2, 3]))\n"
    )
    exigir_paridad(codigo, "python3 -c \"import reprlib; print(reprlib.repr([1, 2, 3]))\"")


def test_str_de_elemento_cero():
    # Antes: [5, 0] -> str(x[1]) imprimia "None".
    codigo = (
        "def f(x):\n"
        "    return str(x[0]) + '/' + str(x[1])\n"
        "print(f([5, 0]))\n"
    )
    exigir_paridad(codigo, "python3 -c \"print(str(5) + '/' + str(0))\"")


# ---------------------------------------------------------------------------
# CAPWORDS_CAPITALIZE_V1 — CPython usa str.capitalize(), no upper()+resto
# ---------------------------------------------------------------------------

def test_capwords_baja_el_resto():
    # Antes: "ABC def GHI" -> "ABC Def GHI" (solo subia la inicial).
    codigo = (
        "import string\n"
        "def titular(frase):\n"
        "    return string.capwords(frase)\n"
        "print(titular('ABC def GHI'))\n"
    )
    exigir_paridad(codigo, "python3 -c \"import string; print(string.capwords('ABC def GHI'))\"")


def test_capwords_con_separador():
    codigo = (
        "import string\n"
        "def titular(frase, sep):\n"
        "    return string.capwords(frase, sep)\n"
        "print(titular('a-b-c', '-'))\n"
    )
    exigir_paridad(codigo, "python3 -c \"import string; print(string.capwords('a-b-c', '-'))\"")


# ---------------------------------------------------------------------------
# DEDENT_V1 — textwrap.dedent real (antes era un no-op que devolvia el texto)
# ---------------------------------------------------------------------------

DEDENT_CASOS = [
    {
        "id": "dedent_basico",
        "codigo": (
            "import textwrap\n"
            "def limpiar(texto):\n"
            "    return textwrap.dedent(texto)\n"
            "print(limpiar('    hola\\n    mundo'))\n"
        ),
        "cmd": "python3 -c \"import textwrap; print(textwrap.dedent('    hola\\\\n    mundo'))\"",
    },
    {
        "id": "dedent_sangria_desigual",
        "codigo": (
            "import textwrap\n"
            "def limpiar(texto):\n"
            "    return textwrap.dedent(texto)\n"
            "print(limpiar('  hola\\n    mundo\\n'))\n"
        ),
        "cmd": "python3 -c \"import textwrap; print(textwrap.dedent('  hola\\\\n    mundo\\\\n'))\"",
    },
    {
        "id": "dedent_linea_en_blanco",
        "codigo": (
            "import textwrap\n"
            "def limpiar(texto):\n"
            "    return textwrap.dedent(texto)\n"
            "print(limpiar('a\\n  \\nb'))\n"
        ),
        "cmd": "python3 -c \"import textwrap; print(textwrap.dedent('a\\\\n  \\\\nb'))\"",
    },
    {
        "id": "dedent_sin_sangria",
        "codigo": (
            "import textwrap\n"
            "def limpiar(texto):\n"
            "    return textwrap.dedent(texto)\n"
            "print(limpiar('hola\\nmundo'))\n"
        ),
        "cmd": "python3 -c \"import textwrap; print(textwrap.dedent('hola\\\\nmundo'))\"",
    },
    {
        "id": "dedent_linea_inicial_vacia",
        "codigo": (
            "import textwrap\n"
            "def limpiar(texto):\n"
            "    return textwrap.dedent(texto)\n"
            "print(limpiar('\\n  a\\n  b\\n'))\n"
        ),
        "cmd": "python3 -c \"import textwrap; print(textwrap.dedent('\\\\n  a\\\\n  b\\\\n'))\"",
    },
]


@pytest.mark.parametrize("caso", DEDENT_CASOS, ids=[c["id"] for c in DEDENT_CASOS])
def test_caso_dedent(caso):
    exigir_paridad(caso["codigo"], caso["cmd"])


# ---------------------------------------------------------------------------
# SHORTEN_V1 — colapso de espacios + marcador ' [...]' como CPython
# ---------------------------------------------------------------------------

SHORTEN_CASOS = [
    {
        "id": "shorten_cabe_entero",
        "codigo": (
            "import textwrap\n"
            "def recortar(texto, ancho):\n"
            "    return textwrap.shorten(texto, ancho)\n"
            "print(recortar('  hola   mundo  ', 20))\n"
        ),
        "cmd": "python3 -c \"import textwrap; print(textwrap.shorten('  hola   mundo  ', 20))\"",
    },
    {
        "id": "shorten_conserva_palabras",
        "codigo": (
            "import textwrap\n"
            "def recortar(texto, ancho):\n"
            "    return textwrap.shorten(texto, ancho)\n"
            "print(recortar('hola mundo entero', 12))\n"
        ),
        "cmd": "python3 -c \"import textwrap; print(textwrap.shorten('hola mundo entero', 12))\"",
    },
    {
        "id": "shorten_solo_marcador",
        "codigo": (
            "import textwrap\n"
            "def recortar(texto, ancho):\n"
            "    return textwrap.shorten(texto, ancho)\n"
            "print(recortar('hola mundo', 5))\n"
        ),
        "cmd": "python3 -c \"import textwrap; print(textwrap.shorten('hola mundo', 5))\"",
    },
    {
        "id": "shorten_exacto",
        "codigo": (
            "import textwrap\n"
            "def recortar(texto, ancho):\n"
            "    return textwrap.shorten(texto, ancho)\n"
            "print(recortar('hola mundo', 10))\n"
        ),
        "cmd": "python3 -c \"import textwrap; print(textwrap.shorten('hola mundo', 10))\"",
    },
]


@pytest.mark.parametrize("caso", SHORTEN_CASOS, ids=[c["id"] for c in SHORTEN_CASOS])
def test_caso_shorten(caso):
    exigir_paridad(caso["codigo"], caso["cmd"])


def test_shorten_tercer_posicional_no_existe():
    # CPython 3.12: shorten(text, width, **kwargs) -> TypeError exacto.
    codigo = (
        "import textwrap\n"
        "def recortar(texto, ancho, marca):\n"
        "    return textwrap.shorten(texto, ancho, marca)\n"
        "print(recortar('hola mundo entero', 5, '...'))\n"
    )
    exigir_paridad(codigo, "python3 -c \"import textwrap; textwrap.shorten('x', 5, '...')\"")


# ---------------------------------------------------------------------------
# DICT_KEY_SLOT_EXC_V1 / DICT_KEY_GUESS_V1
# ---------------------------------------------------------------------------

def test_iterar_dict_desde_atributo():
    # Antes: el bucle sobre `d = self._d` corria una vuelta de mas con la key
    # basura y el StopIteration escapaba ("StopIteration:").
    codigo = (
        "class Caja:\n"
        "    def __init__(self):\n"
        "        self._d = {'a': 1, 'b': 2}\n"
        "    def total(self):\n"
        "        d = self._d\n"
        "        n = 0\n"
        "        for k in d:\n"
        "            n = n + 1\n"
        "        return n\n"
        "print(Caja().total())\n"
    )
    exigir_paridad(codigo, "python3 -c \"print(2)\"")


def test_subscriptar_dict_con_clave_de_tipo_desconocido():
    # Antes: la clave se etiquetaba PK_INT aunque fuese un str -> KeyError.
    codigo = (
        "class Caja:\n"
        "    def __init__(self):\n"
        "        self._d = {'a': 2, 'b': 3}\n"
        "        self._orden = ['a', 'b']\n"
        "    def valores(self):\n"
        "        d = self._d\n"
        "        out = []\n"
        "        for k in self._orden:\n"
        "            out.append(d[k])\n"
        "        return out\n"
        "print(Caja().valores())\n"
    )
    exigir_paridad(codigo, "python3 -c \"print([2, 3])\"")


def test_dict_asignar_con_clave_de_tipo_desconocido():
    codigo = (
        "class Caja:\n"
        "    def __init__(self):\n"
        "        self._d = {}\n"
        "        self._orden = ['a']\n"
        "    def meter(self):\n"
        "        d = self._d\n"
        "        for k in self._orden:\n"
        "            d[k] = 7\n"
        "        return d['a']\n"
        "print(Caja().meter())\n"
    )
    exigir_paridad(codigo, "python3 -c \"print(7)\"")


# ---------------------------------------------------------------------------
# GROUPBY_V1 / COMBINATIONS_V1
# ---------------------------------------------------------------------------

def test_groupby_claves_y_grupos():
    codigo = (
        "import itertools\n"
        "def contar(datos):\n"
        "    salida = []\n"
        "    for par in itertools.groupby(datos):\n"
        "        salida.append(par[0])\n"
        "        salida.append(len(list(par[1])))\n"
        "    return salida\n"
        "print(contar([1, 1, 2, 3, 3]))\n"
    )
    exigir_paridad(codigo, "python3 -c \"import itertools; print([1, 2, 2, 1, 3, 2])\"")


def test_groupby_de_un_solo_grupo():
    codigo = (
        "import itertools\n"
        "def claves(datos):\n"
        "    out = []\n"
        "    for par in itertools.groupby(datos):\n"
        "        out.append(par[0])\n"
        "    return out\n"
        "print(claves(['x', 'x', 'x']))\n"
    )
    exigir_paridad(codigo, "python3 -c \"import itertools; print(['x'])\"")


COMBINATIONS_CASOS = [
    {
        "id": "combinations_2_de_3",
        "codigo": (
            "import itertools\n"
            "def pares(datos, k):\n"
            "    return list(itertools.combinations(datos, k))\n"
            "print(pares([1, 2, 3], 2))\n"
        ),
        "cmd": "python3 -c \"import itertools; print(list(itertools.combinations([1,2,3], 2)))\"",
    },
    {
        "id": "combinations_3_de_4",
        "codigo": (
            "import itertools\n"
            "def trios(datos, k):\n"
            "    return list(itertools.combinations(datos, k))\n"
            "print(trios([1, 2, 3, 4], 3))\n"
        ),
        "cmd": "python3 -c \"import itertools; print(list(itertools.combinations([1,2,3,4], 3)))\"",
    },
    {
        "id": "combinations_r_mayor_que_n",
        "codigo": (
            "import itertools\n"
            "def nada(datos, k):\n"
            "    return list(itertools.combinations(datos, k))\n"
            "print(nada([1, 2], 3))\n"
        ),
        "cmd": "python3 -c \"import itertools; print(list(itertools.combinations([1,2], 3)))\"",
    },
]


@pytest.mark.parametrize("caso", COMBINATIONS_CASOS, ids=[c["id"] for c in COMBINATIONS_CASOS])
def test_caso_combinations(caso):
    exigir_paridad(caso["codigo"], caso["cmd"])


# ---------------------------------------------------------------------------
# COUNTER_V1
# ---------------------------------------------------------------------------

def test_counter_most_common_con_n():
    codigo = (
        "from collections import Counter\n"
        "def frecuentes(datos, n):\n"
        "    return Counter(datos).most_common(n)\n"
        "print(frecuentes(['a', 'b', 'a', 'a', 'c'], 2))\n"
    )
    exigir_paridad(codigo, "python3 -c \"from collections import Counter; print(Counter(['a','b','a','a','c']).most_common(2))\"")


def test_counter_most_common_estable():
    # Empate a 1: CPython conserva el orden de insercion.
    codigo = (
        "from collections import Counter\n"
        "def frecuentes(datos):\n"
        "    return Counter(datos).most_common()\n"
        "print(frecuentes(['b', 'a', 'c', 'a']))\n"
    )
    exigir_paridad(codigo, "python3 -c \"from collections import Counter; print(Counter(['b','a','c','a']).most_common())\"")
