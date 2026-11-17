"""Falsificación adversarial del lenguaje base contra CPython 3.12.3.

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


def test_vars_returns_live_module_globals(tmp_path: Path) -> None:
    """CPython 3.12.3: ``vars()`` en módulo es el diccionario real; mutarlo
    cambia el global observado por una lectura posterior.
    """
    oracle_source = """\
probe = 10
namespace = vars()
print(isinstance(namespace, dict))
namespace["probe"] = 42
print(probe)
print(namespace["probe"])
"""
    piton_source = """\
prueba = 10
espacio = vars()
imprimir(isinstance(espacio, dict))
espacio["prueba"] = 42
imprimir(prueba)
imprimir(espacio["prueba"])
"""
    expected = "True\n42\n42\n"
    _assert_oracle_and_piton(
        tmp_path, "vars_live_globals", oracle_source, piton_source, expected
    )


def test_str_format_mapping_and_indexed_field(tmp_path: Path) -> None:
    """CPython 3.12.3 usa dos formas distintas: ``'{name}'.format(**mapping)``
    para campos nombrados y ``'{0[key]}'.format(mapping)`` para indexar el
    argumento posicional 0.  Combinarlos como ``'{0[key]}'.format(**mapping)``
    es IndexError en el oráculo.
    """
    oracle_source = """\
mapping = {"name": "Ana", "values": [1, 2]}
print("{name}".format(**mapping))
print("{0[name]}".format(mapping))
print("{0[values][1]}".format(mapping))
"""
    piton_source = """\
mapa = {"name": "Ana", "values": [1, 2]}
imprimir("{name}".format(**mapa))
imprimir("{0[name]}".format(mapa))
imprimir("{0[values][1]}".format(mapa))
"""
    expected = "Ana\nAna\n2\n"
    _assert_oracle_and_piton(
        tmp_path, "format_mapping", oracle_source, piton_source, expected
    )


def test_binary_rb_plus_write_and_exact_16_byte_reread(tmp_path: Path) -> None:
    """CPython 3.12.3: un archivo abierto ``rb+`` puede sobrescribir 16 bytes
    y releer exactamente esos 16 bytes tras ``seek(0)``.
    """
    path = tmp_path / "x"
    path.write_bytes(b"0123456789abcdef")

    oracle_source = f"""\
path = {str(path)!r}
with open(path, "rb+") as handle:
    handle.write(b"ABCDEFGHIJKLMNOP")
    handle.seek(0)
    data = handle.read(16)
print(data)
print(len(data))
"""
    piton_source = f"""\
ruta = {str(path)!r}
con abrir(ruta, "rb+") as archivo:
    archivo.write(b"ABCDEFGHIJKLMNOP")
    archivo.seek(0)
    datos = archivo.read(16)
imprimir(datos)
imprimir(len(datos))
"""
    expected = "b'ABCDEFGHIJKLMNOP'\n16\n"
    _assert_oracle_and_piton(
        tmp_path, "binary_rb_plus", oracle_source, piton_source, expected
    )
    assert path.read_bytes() == b"ABCDEFGHIJKLMNOP"


def test_tuple_item_assignment_raises_type_error(tmp_path: Path) -> None:
    """CPython 3.12.3: asignar a ``tuple[0]`` lanza TypeError; PITON no puede
    emularlo con una mutación silenciosa ni con otro tipo de excepción.
    """
    oracle_source = """\
values = (1, 2, 3)
try:
    values[0] = 99
except TypeError:
    print("TypeError")
"""
    piton_source = """\
valores = (1, 2, 3)
intentar:
    valores[0] = 99
excepto TypeError:
    imprimir("TypeError")
"""
    expected = "TypeError\n"
    _assert_oracle_and_piton(
        tmp_path, "tuple_immutable", oracle_source, piton_source, expected
    )


def test_slicing_step_and_zero_step_error(tmp_path: Path) -> None:
    """CPython 3.12.3: slices positivos, negativos y con step; ``[::0]``
    debe lanzar ValueError, no devolver una lista arbitraria.
    """
    oracle_source = """\
values = [0, 1, 2, 3, 4, 5]
print(values[1:6:2])
print(values[::-1])
print(values[-2:])
print(values[::2])
try:
    values[::0]
except ValueError:
    print("ValueError")
"""
    piton_source = """\
valores = [0, 1, 2, 3, 4, 5]
imprimir(valores[1:6:2])
imprimir(valores[::-1])
imprimir(valores[-2:])
imprimir(valores[::2])
intentar:
    valores[::0]
excepto ValueError:
    imprimir("ValueError")
"""
    expected = "[1, 3, 5]\n[5, 4, 3, 2, 1, 0]\n[4, 5]\n[0, 2, 4]\nValueError\n"
    _assert_oracle_and_piton(
        tmp_path, "slicing_step", oracle_source, piton_source, expected
    )


def test_dir_module_returns_list_of_strings(tmp_path: Path) -> None:
    """CPython 3.12.3: ``dir(math)`` es una lista de strings y contiene
    ``sqrt``; una lista de objetos no qualifies no es paridad.
    """
    oracle_source = """\
import math
names = dir(math)
print(isinstance(names, list))
all_strings = True
for name in names:
    if not isinstance(name, str):
        all_strings = False
print(all_strings)
print("sqrt" in names)
"""
    piton_source = """\
importar math
nombres = dir(math)
imprimir(isinstance(nombres, list))
todos_strings = True
para nombre en nombres:
    si not isinstance(nombre, str):
        todos_strings = False
imprimir(todos_strings)
imprimir("sqrt" in nombres)
"""
    expected = "True\nTrue\nTrue\n"
    _assert_oracle_and_piton(
        tmp_path, "dir_module", oracle_source, piton_source, expected
    )
