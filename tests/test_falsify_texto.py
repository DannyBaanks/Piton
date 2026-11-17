"""Falsificación adversarial de textwrap/pprint/reprlib contra CPython 3.12.3.

Expectativas verificadas el 2026-11-13 con ``python3 --version``
(``Python 3.12.3``) y con los comandos ``python3 -c`` citados en cada caso.
No se copió ninguna salida de PITON.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "LC_ALL": "C.UTF-8",
            "LANG": "C.UTF-8",
        }
    )
    return env


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def _assert_oracle_and_piton(
    tmp_path: Path,
    stem: str,
    oracle_source: str,
    piton_source: str,
    expected: str,
) -> None:
    oracle = _run(["python3", "-c", oracle_source])
    assert oracle.returncode == 0, (
        f"el propio oráculo falló para {stem}: rc={oracle.returncode}\n"
        f"stdout={oracle.stdout!r}\nstderr={oracle.stderr!r}"
    )
    assert oracle.stdout == expected, (
        f"cambió la expectativa CPython de {stem}: {oracle.stdout!r} != {expected!r}"
    )

    piton_file = tmp_path / f"{stem}.piton"
    piton_file.write_text(piton_source, encoding="utf-8")
    piton = _run(["python3", "-m", "piton", "ejecutar", str(piton_file)])
    assert piton.returncode == 0, (
        f"PITON no ejecutó {stem}: rc={piton.returncode}\n"
        f"stdout={piton.stdout!r}\nstderr={piton.stderr!r}"
    )
    assert piton.stdout == oracle.stdout, (
        f"PITON diverge de CPython 3.12.3 en {stem}\n"
        f"PITON={piton.stdout!r}\nCPython={oracle.stdout!r}\n"
        f"stderr PITON={piton.stderr!r}"
    )


def test_textwrap_fill_width_40_is_byte_exact(tmp_path: Path) -> None:
    """CPython 3.12.3: ``textwrap.fill(doc, width=40)`` corta exactamente en
    las tres líneas verificadas con ``python3 -c``.
    """
    oracle_source = """\
import textwrap
doc = "La paridad exige que cada resultado sea idéntico byte a byte, incluso en los límites menos visibles del lenguaje."
print(repr(textwrap.fill(doc, width=40)))
"""
    piton_source = """\
importar textwrap
doc = "La paridad exige que cada resultado sea idéntico byte a byte, incluso en los límites menos visibles del lenguaje."
imprimir(repr(textwrap.fill(doc, width=40)))
"""
    expected = (
        "'La paridad exige que cada resultado sea\\n"
        "idéntico byte a byte, incluso en los\\n"
        "límites menos visibles del lenguaje.'\n"
    )
    _assert_oracle_and_piton(
        tmp_path, "fill_width_40", oracle_source, piton_source, expected
    )


def test_textwrap_does_not_split_words_longer_than_width(tmp_path: Path) -> None:
    """CPython 3.12.3 no parte una palabra de 57 X aunque width=40; la deja
    entera y continúa en la línea siguiente.
    """
    oracle_source = """\
import textwrap
doc = "antes " + ("X" * 57) + " despues"
print(repr(textwrap.fill(doc, width=40)))
"""
    piton_source = """\
importar textwrap
doc = "antes " + ("X" * 57) + " despues"
imprimir(repr(textwrap.fill(doc, width=40)))
"""
    expected = (
        "'antes XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX\\n"
        "XXXXXXXXXXXXXXXXXXXXXXX despues'\n"
    )
    _assert_oracle_and_piton(
        tmp_path, "long_word", oracle_source, piton_source, expected
    )


def test_pprint_width_30_indentation_is_exact(tmp_path: Path) -> None:
    """CPython 3.12.3: pprint.pprint(..., width=30) coloca cada clave en una
    línea y conserva la indentación de un espacio.
    """
    oracle_source = """\
import pprint
value = {"alpha": 1, "beta": [2, 3], "gamma": {"nested": True}}
pprint.pprint(value, width=30)
"""
    piton_source = """\
importar pprint
valor = {"alpha": 1, "beta": [2, 3], "gamma": {"nested": True}}
pprint.pprint(valor, width=30)
"""
    expected = (
        "{'alpha': 1,\n"
        " 'beta': [2, 3],\n"
        " 'gamma': {'nested': True}}\n"
    )
    _assert_oracle_and_piton(
        tmp_path, "pprint_indent", oracle_source, piton_source, expected
    )


def test_reprlib_set_limit_is_six_and_uses_ellipsis(tmp_path: Path) -> None:
    """CPython 3.12.3: el ``reprlib.repr`` global ya tiene maxset=6 y produce
    ``{0, 1, 2, 3, 4, 5, ...}``.  API real: ``maxset`` es un parámetro de
    ``reprlib.Repr(...)``; ``reprlib.repr(obj, maxset=6)`` es TypeError.
    """
    oracle_source = """\
import reprlib
print(reprlib.repr(set(range(10))))
print(reprlib.Repr(maxset=6).repr(set(range(10))))
"""
    piton_source = """\
importar reprlib
imprimir(reprlib.repr(set(rango(10))))
imprimir(reprlib.Repr(maxset=6).repr(set(rango(10))))
"""
    expected = "{0, 1, 2, 3, 4, 5, ...}\n{0, 1, 2, 3, 4, 5, ...}\n"
    _assert_oracle_and_piton(
        tmp_path, "reprlib_maxset", oracle_source, piton_source, expected
    )


def test_textwrap_fill_equals_joined_wrap(tmp_path: Path) -> None:
    """CPython 3.12.3: ``fill`` es exactamente ``"\\n".join(wrap)`` para el
    mismo texto y ancho, y ``wrap`` devuelve la lista de tres líneas indicada.
    """
    oracle_source = """\
import textwrap
doc = "La paridad exige que cada resultado sea idéntico byte a byte, incluso en los límites menos visibles del lenguaje."
print(textwrap.wrap(doc, width=40))
print(textwrap.fill(doc, width=40) == "\\n".join(textwrap.wrap(doc, width=40)))
"""
    piton_source = """\
importar textwrap
doc = "La paridad exige que cada resultado sea idéntico byte a byte, incluso en los límites menos visibles del lenguaje."
imprimir(textwrap.wrap(doc, width=40))
imprimir(textwrap.fill(doc, width=40) == "\\n".join(textwrap.wrap(doc, width=40)))
"""
    expected = (
        "['La paridad exige que cada resultado sea', "
        "'idéntico byte a byte, incluso en los', "
        "'límites menos visibles del lenguaje.']\n"
        "True\n"
    )
    _assert_oracle_and_piton(
        tmp_path, "wrap_fill_coherence", oracle_source, piton_source, expected
    )
