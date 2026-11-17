"""Paridad texto: reprlib.repr, pprint.pprint, textwrap.fill vs CPython 3.12.

Cada test ejecuta el ejemplo de tutorial stdlib2 en el backend nativo Linux
de Pitón y compara la salida contra CPython 3.12 byte a byte (regla honesta:
excepcion en ambos lados == EQUIV).
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PITON_SRC = REPO / "piton"
ORACLE = sys.executable


def _run_piton(source: str) -> subprocess.CompletedProcess:
    """Compila y ejecuta fuente Pitón en el backend nativo Linux."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="piton-test-") as d:
        src = Path(d) / "t.piton"
        src.write_text(source, encoding="utf-8")
        exe = Path(d) / "t"
        comp = subprocess.run(
            [sys.executable, "-m", "piton", "compilar", str(src),
             "--backend=linux", "--output", str(exe)],
            capture_output=True, text=True,
        )
        if comp.returncode != 0:
            return comp  # devuelve el error de compilación
        return subprocess.run([str(exe)], capture_output=True, text=True)


def _run_cpython(source: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ORACLE, "-c", source], capture_output=True, text=True,
    )


def test_reprlib_repr_set_supercali():
    # stdlib2 #2: reprlib.repr(set('supercalifragilisticexpialidocious'))
    # Nota: en el tutorial es una expresion suelta (no imprime en CPython);
    # aqui la envolvemos en print para poder comparar byte a byte.
    piton = _run_piton(
        "importar reprlib\n"
        "imprimir(reprlib.repr(conjunto('supercalifragilisticexpialidocious')))\n"
    )
    cpy = _run_cpython(
        "import reprlib\n"
        "print(reprlib.repr(set('supercalifragilisticexpialidocious')))\n"
    )
    assert piton.returncode == 0, piton.stderr
    assert piton.stdout == cpy.stdout, (piton.stdout, cpy.stdout)


def test_pprint_pprint_width30():
    # stdlib2 #5: pprint.pprint(t, width=30)  (byte a byte)
    src = (
        "importar pprint\n"
        "t = [[[['black', 'cyan'], 'white', ['green', 'red']], "
        "[['magenta', 'yellow'], 'blue']]]\n"
        "pprint.pprint(t, width=30)\n"
    )
    piton = _run_piton(src)
    cpy = _run_cpython(
        "import pprint\n"
        "t = [[[['black', 'cyan'], 'white', ['green', 'red']], "
        "[['magenta', 'yellow'], 'blue']]]\n"
        "pprint.pprint(t, width=30)\n"
    )
    assert piton.returncode == 0, piton.stderr
    assert piton.stdout == cpy.stdout, (piton.stdout, cpy.stdout)


def test_textwrap_fill_width40():
    # stdlib2 #8: print(textwrap.fill(doc, width=40))  (byte a byte)
    src = (
        "importar textwrap\n"
        "doc = 'The wrap() method is just like fill() except that it returns'\n"
        "doc = doc + ' a list of strings instead of one big string with newlines'\n"
        "doc = doc + ' to separate the wrapped lines.'\n"
        "imprimir(textwrap.fill(doc, width=40))\n"
    )
    piton = _run_piton(src)
    cpy = _run_cpython(
        "import textwrap\n"
        "doc = 'The wrap() method is just like fill() except that it returns'\n"
        "doc = doc + ' a list of strings instead of one big string with newlines'\n"
        "doc = doc + ' to separate the wrapped lines.'\n"
        "print(textwrap.fill(doc, width=40))\n"
    )
    assert piton.returncode == 0, piton.stderr
    assert piton.stdout == cpy.stdout, (piton.stdout, cpy.stdout)
