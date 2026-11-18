"""ADVERSARIAL reprlib — paridad byte a byte con CPython 3.12."""
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

from native_linux import compile_native_linux  # noqa: E402
from tools.parity_corpus_v4 import python_to_piton  # noqa: E402

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
    with tempfile.TemporaryDirectory(prefix="piton-adv-reprlib-") as directory:
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
    except Exception as exc:
        return "BUILD_FAIL", f"{type(exc).__name__}: {exc}"

    if cpython.returncode == 0:
        if piton.returncode != 0:
            return (
                "DIVERGENT",
                f"CPython rc=0; PITON rc={piton.returncode}; stderr={piton.stderr!r}",
            )
        if cpython.stdout != piton.stdout:
            return (
                "DIVERGENT",
                f"stdout CPython={cpython.stdout!r} PITON={piton.stdout!r}",
            )
        return "EQUIV", ""

    if piton.returncode == 0:
        return "DIVERGENT", f"CPython rc={cpython.returncode}; PITON rc=0"
    cpython_error = _last_exception_line(cpython.stderr)
    piton_error = _last_exception_line(piton.stderr)
    if cpython.returncode != piton.returncode or cpython_error != piton_error:
        return (
            "DIVERGENT",
            f"CPython rc={cpython.returncode} error={cpython_error!r}; "
            f"PITON rc={piton.returncode} error={piton_error!r}",
        )
    return "EQUIV", ""


CASES = [
    (
        "reprlib_lista",
        "import reprlib\nprint(reprlib.repr([1, 2, 3]))\n",
    ),
    (
        "reprlib_lista_truncada",
        "import reprlib\nprint(reprlib.repr([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]))\n",
    ),
    (
        "reprlib_dict",
        "import reprlib\nprint(reprlib.repr({'a': 1, 'b': 2}))\n",
    ),
    (
        "reprlib_str_corto",
        "import reprlib\nprint(reprlib.repr('abcdefghijklmnopqrstuvwxyz'))\n",
    ),
    (
        "reprlib_str_largo",
        "import reprlib\nprint(reprlib.repr('abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'))\n",
    ),
    (
        "reprlib_set_vacio",
        "import reprlib\nprint(reprlib.repr(set()))\n",
    ),
    (
        "reprlib_set_largo_en_try",
        "try:\n    import reprlib\nexcept BaseException:\n    print('EXC')\n"
        "try:\n    print(reprlib.repr(set('supercalifragilisticexpialidocious')))\nexcept BaseException:\n    print('EXC')\n",
    ),
]


@pytest.mark.parametrize("nombre,codigo", CASES, ids=[c[0] for c in CASES])
def test_adv_reprlib_cpython_312(nombre: str, codigo: str) -> None:
    verdict, detail = _verdict(codigo)
    if verdict != "EQUIV":
        pytest.fail(f"PITON != CPython 3.12 [{nombre}] ({verdict}): {detail}")
