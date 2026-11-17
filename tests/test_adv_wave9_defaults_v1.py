"""Wave 9 adversarial: parametros con default tipado (str/int) llamados con
argumentos de tipo conflictivo.

Bug (Wave 6): `funcion f(x='pass'): imprimir(x)` seguido de `f(3)` compilaba
y el binario hacia SIGSEGV (el param se tipa str y el long se desreferencia
como char*). Lo mismo con `x.upper()`. El backend nativo debe fallar cerrado
en compilacion (NativeBuildError), como SEQ_PARAM_DEFAULT_V1, o bien
comportarse como CPython. Nunca un segfault.

Los casos que CPython acepta y que el subset no puede tipar siguen siendo
fail-closed; los casos que CPython rechaza en runtime deben lanzar la misma
excepcion (rc=1 + TypeError en stderr), no crashear.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from piton.linux_x86 import compile_native_linux  # noqa: E402
from piton.x86 import NativeBuildError  # noqa: E402


def _build_and_run(piton_code: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / "prog_bin"
        compile_native_linux(piton_code, str(exe))
        return subprocess.run(
            [str(exe)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )


def _assert_rejected(piton_code: str) -> None:
    """El compilador debe rechazar el programa (fail closed), no emitir binario."""
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / "prog_bin"
        with pytest.raises(NativeBuildError):
            compile_native_linux(piton_code, str(exe))
        assert not exe.exists(), "fail-closed debe dejar el binario sin generar"


# --- Caso del reporte: literal int contra default str -----------------------

def test_str_default_rejects_literal_int_instead_of_segfault():
    _assert_rejected(
        "funcion f(x='pass'):\n"
        "    imprimir (x)\n"
        "f(3)\n"
    )


def test_str_default_used_as_str_rejects_literal_int():
    _assert_rejected(
        "funcion f(x='pass'):\n"
        "    imprimir (x.upper())\n"
        "f(3)\n"
    )


@pytest.mark.xfail(
    strict=True,
    reason="KNOWN GAP: el tipo estatico 'int' es ambiguo (int real o no tipado). "
    "Rechazarlo para params str rompe deque/Counter/re/attrgetter/getopt "
    "(38 regresiones medidas). Solo se rechazan literales escalares.",
)
def test_str_default_rejects_int_through_variable():
    # `n` es un int conocido en el call-site pero no un literal.
    _assert_rejected(
        "funcion f(x='pass'):\n"
        "    imprimir (x)\n"
        "n = 3\n"
        "f(n)\n"
    )


@pytest.mark.xfail(
    strict=True,
    reason="KNOWN GAP: mismo caso que la variable; 'int' no literal no se rechaza.",
)
def test_str_default_rejects_int_call_result():
    _assert_rejected(
        "funcion f(x='pass'):\n"
        "    imprimir (x)\n"
        "f(len('abc'))\n"
    )


def test_str_default_rejects_bool_and_float_literals():
    _assert_rejected(
        "funcion f(x='pass'):\n"
        "    imprimir (x)\n"
        "f(Verdadero)\n"
    )
    _assert_rejected(
        "funcion f(x='pass'):\n"
        "    imprimir (x)\n"
        "f(2.5)\n"
    )


# --- Lo que debe seguir funcionando ----------------------------------------

def test_str_default_with_str_arguments_and_omitted_default_still_run():
    proc = _build_and_run(
        "funcion f(x='pass', n=3):\n"
        "    imprimir (x.upper(), n)\n"
        "f()\n"
        "f('ab')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "PASS 3\nAB 3\n"


def test_int_default_with_int_arguments_still_runs():
    proc = _build_and_run(
        "funcion f(n=3):\n"
        "    devolver n + 1\n"
        "imprimir (f())\n"
        "imprimir (f(7))\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "4\n8\n"


def test_str_repetition_with_int_default_matches_cpython():
    proc = _build_and_run(
        "funcion g(x=0):\n"
        "    devolver x * 2\n"
        "imprimir (g('ab'))\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "abab\n"


# --- Conflicto en el otro sentido: int default, str argumento ---------------

def test_int_default_with_str_argument_raises_type_error_like_cpython():
    # CPython: TypeError en runtime ('a' + 1). No debe segfaultar ni compilar
    # a un resultado silencioso.
    proc = _build_and_run(
        "funcion f(x=3):\n"
        "    imprimir (x + 1)\n"
        "f('a')\n"
    )
    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    assert "TypeError" in proc.stderr
    assert proc.stdout == ""


def test_timer_rejects_int_stmt_like_cpython():
    proc = _build_and_run(
        "desde timeit importar Timer\n"
        "intentar:\n"
        "    t = Timer(3)\n"
        "    imprimir ('no error')\n"
        "excepto ValueError:\n"
        "    imprimir ('ValueError')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "ValueError\n"
