"""Casos adversarios de ``textwrap`` contra CPython 3.12.

La comparacion es estricta: mismo exit code y stdout byte a byte. Si ambos
lanzan una excepcion, se comparan ademas el tipo y el payload de la ultima
linea de stderr. No se fijan salidas copiadas de PITON ni se normalizan
espacios: el oriculo es el CPython vivo del propio test.
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
from piton.linux_x86 import compile_native_linux  # noqa: E402


TIMEOUT = 60


def _run_cpython(source: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        timeout=TIMEOUT,
        check=False,
    )


def _run_piton(source: str) -> subprocess.CompletedProcess[bytes]:
    translated = python_to_piton(source)
    with tempfile.TemporaryDirectory(prefix="piton-adv-textwrap-") as directory:
        executable = compile_native_linux(translated, Path(directory) / "program")
        return subprocess.run(
            [str(executable)],
            capture_output=True,
            timeout=TIMEOUT,
            check=False,
        )


def _last_exception_line(stderr: bytes) -> str:
    lines = [
        line
        for line in stderr.decode("utf-8", "replace").splitlines()
        if line.strip()
    ]
    return lines[-1] if lines else ""


@lru_cache(maxsize=None)
def _verdict(source: str) -> tuple[str, str]:
    cpython = _run_cpython(source)
    try:
        piton = _run_piton(source)
    except Exception as exc:  # pragma: no cover - el fallo se muestra como diff
        return "BUILD_FAIL", f"{type(exc).__name__}: {exc}"

    if cpython.returncode == 0:
        if piton.returncode != 0:
            return (
                "DIVERGENT",
                f"CPython rc=0; PITON rc={piton.returncode}; "
                f"stderr={piton.stderr!r}",
            )
        if cpython.stdout != piton.stdout:
            return (
                "DIVERGENT",
                f"stdout CPython={cpython.stdout!r} PITON={piton.stdout!r}",
            )
        return "EQUIV", ""

    if piton.returncode == 0:
        return (
            "DIVERGENT",
            f"CPython rc={cpython.returncode}; PITON rc=0",
        )
    cpython_error = _last_exception_line(cpython.stderr)
    piton_error = _last_exception_line(piton.stderr)
    if cpython.returncode != piton.returncode or cpython_error != piton_error:
        return (
            "DIVERGENT",
            f"CPython rc={cpython.returncode} error={cpython_error!r}; "
            f"PITON rc={piton.returncode} error={piton_error!r}",
        )
    return "EQUIV", ""


def _require_parity(source: str) -> None:
    verdict, detail = _verdict(source)
    if verdict == "EQUIV":
        return
    pytest.fail(
        f"PITON != CPython 3.12 ({verdict})\n"
        f"  source={source!r}\n"
        f"  detail={detail}"
    )


CASES = [
    {
        "id": "wrap_devuelve_lista_con_espacio_final",
        "source": (
            "import textwrap\n"
            "def partir(texto, ancho):\n"
            "    return textwrap.wrap(texto, ancho)\n"
            "print(partir('aaaa bb cccc', 3))\n"
        ),
    },
    {
        "id": "fill_conserva_espacios_internos_del_tutorial",
        "source": (
            "import textwrap\n"
            "doc = '''The wrap() method is just like fill() except that it returns\n"
            "    a list of strings instead of one big string with newlines to separate\n"
            "    the wrapped lines.'''\n"
            "print(textwrap.fill(doc, width=40))\n"
        ),
    },
    {
        "id": "fill_acepta_text_y_width_por_keyword",
        "source": (
            "import textwrap\n"
            "print(textwrap.fill(text='one     two three', width=12))\n"
        ),
    },
    {
        "id": "wrap_acepta_text_y_width_por_keyword",
        "source": (
            "import textwrap\n"
            "print(textwrap.wrap(text='one     two three', width=12))\n"
        ),
    },
    {
        "id": "wrap_preserva_espacios_multiples",
        "source": (
            "import textwrap\n"
            "print(textwrap.wrap('one     two three', 12))\n"
        ),
    },
    {
        "id": "wrap_parte_palabra_larga",
        "source": (
            "import textwrap\n"
            "print(textwrap.wrap('abcdefghijkl', 5))\n"
        ),
    },
    {
        "id": "wrap_reparte_guiones_compuestos",
        "source": (
            "import textwrap\n"
            "print(textwrap.wrap('well-known-word and foo--bar', 12))\n"
        ),
    },
    {
        "id": "fill_normaliza_tabs_nuevas_lineas_y_espacios",
        "source": (
            "import textwrap\n"
            "print(repr(textwrap.fill('a\\tb\\n\\n  c   d', width=8)))\n"
        ),
    },
    {
        "id": "wrap_conserva_espacio_inicial_y_descarta_final",
        "source": (
            "import textwrap\n"
            "print(textwrap.wrap('  a  ', 3))\n"
        ),
    },
    {
        "id": "width_cero_lanza_valueerror",
        "source": (
            "import textwrap\n"
            "textwrap.fill('x', width=0)\n"
        ),
    },
]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_textwrap_adversarial_cpython_312(case: dict[str, str]) -> None:
    _require_parity(case["source"])
