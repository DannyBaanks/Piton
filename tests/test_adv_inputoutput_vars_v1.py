"""Casos adversarios de ``str.format(**vars())`` y dicts mixtos contra CPython 3.12.

La comparacion es estricta: mismo exit code y stdout byte a byte. Si ambos
lanzan una excepcion, se comparan el tipo y el payload de la ultima linea de
stderr. El oraculo es el CPython vivo del propio test.
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
    with tempfile.TemporaryDirectory(prefix="piton-adv-inputoutput-vars-") as directory:
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
        "id": "str_format_vars_mixed_types",
        "source": (
            "year = 2016\n"
            "event = 'Referendum'\n"
            "flag = True\n"
            "nothing = None\n"
            "ratio = 0.4967\n"
            "table = {k: str(v) for k, v in vars().items()}\n"
            "message = ' '.join([f'{k}: ' + '{' + k + '};' for k in table.keys()])\n"
            "print(message.format(**table))\n"
        ),
    },
    {
        "id": "str_format_vars_ints",
        "source": (
            "a = 1\n"
            "b = -42\n"
            "c = 0\n"
            "table = {k: str(v) for k, v in vars().items()}\n"
            "print('{a} {b} {c}'.format(**table))\n"
        ),
    },
    {
        "id": "str_format_vars_strs",
        "source": (
            "a = 'hello'\n"
            "b = 'world'\n"
            "table = {k: str(v) for k, v in vars().items()}\n"
            "print('{a} {b}'.format(**table))\n"
        ),
    },
    {
        "id": "str_format_vars_none_bool",
        "source": (
            "x = None\n"
            "y = True\n"
            "z = False\n"
            "table = {k: str(v) for k, v in vars().items()}\n"
            "print('{x} {y} {z}'.format(**table))\n"
        ),
    },
    {
        "id": "inputoutput_ejemplo_41_exacto",
        "source": (
            "year = 2016\n"
            "event = 'Referendum'\n"
            "yes_votes = 42_572_654\n"
            "total_votes = 85_705_149\n"
            "percentage = yes_votes / total_votes\n"
            "s = 'Hello, world.'\n"
            "x = 10 * 3.25\n"
            "y = 200 * 200\n"
            "import math\n"
            "table = {k: str(v) for k, v in vars().items()}\n"
            "message = ' '.join([f'{k}: ' + '{' + k + '};' for k in table.keys()])\n"
            "print(message.format(**table))\n"
        ),
    },
    {
        "id": "dict_comprehension_vars_str_values",
        "source": (
            "a = 1\n"
            "b = 'two'\n"
            "c = None\n"
            "table = {k: str(v) for k, v in vars().items()}\n"
            "print(table['a'], table['b'], table['c'])\n"
        ),
    },
]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_inputoutput_vars_adversarial_cpython_312(case: dict[str, str]) -> None:
    _require_parity(case["source"])
