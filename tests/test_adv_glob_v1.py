"""Paridad adversarial de glob.glob / glob.iglob / glob.escape / glob.has_magic.

Cada caso se ejecuta en tres sitios sobre el MISMO arbol de directorios:
  1. CPython 3.12 (la referencia),
  2. ``piton ejecutar`` (interprete PITON),
  3. ``compile_native_linux`` (binario nativo -nostdlib).

La salida se compara linea a linea. Los resultados se ordenan en el propio
programa porque el orden de os.scandir depende del sistema de ficheros.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from native_linux import compile_native_linux, run_native_linux  # noqa: E402
from tools.parity_corpus_sec import python_to_piton  # noqa: E402


def _build_tree(base: Path) -> None:
    files = [
        "a.py", "b.txt", "c.PY", ".hidden.py", "data1.csv", "data2.csv",
        "x[1].txt", "ab.py", "é.txt",
        "sub/c.py", "sub/d.txt", "sub/.dot.py",
        "sub/deeper/d.py", "sub/deeper/e.txt", "sub/deeper/.hid/f.py",
        "other/g.py", "emptydir/.keep",
    ]
    # Windows forbids literal ``*`` and ``?`` in file names. Keep the Linux
    # fixture exact (the cases below need those literal names); on Windows the
    # two impossible entries are omitted instead of crashing fixture setup.
    if os.name != "nt":
        files.extend(("star*name.md", "q?.log"))
    for rel in files:
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    (base / "emptydir").mkdir(exist_ok=True)
    (base / "subdir_only").mkdir(exist_ok=True)


def _run_cpython(code: str, cwd: Path) -> str:
    p = subprocess.run([sys.executable, "-c", code], cwd=cwd, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def _run_ejecutar(pit: str, cwd: Path) -> str:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "p.piton"
        src.write_text(pit, encoding="utf-8")
        p = subprocess.run(
            [sys.executable, "-m", "piton", "ejecutar", str(src)],
            cwd=cwd, capture_output=True, text=True, timeout=120,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
    assert p.returncode == 0, p.stderr
    return p.stdout


def _run_native(pit: str, cwd: Path) -> str:
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / "bin"
        compile_native_linux(pit, str(exe))
        p = run_native_linux(exe, cwd=cwd, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return p.stdout


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    _build_tree(tmp_path)
    return tmp_path


def _parity(tree: Path, code: str) -> None:
    expected = _run_cpython(code, tree)
    pit = python_to_piton(code)
    assert _run_ejecutar(pit, tree) == expected
    assert _run_native(pit, tree) == expected


# Los patrones se listan como lineas independientes: cada una imprime su
# resultado ordenado, asi un fallo en un caso no enmascara los demas.
_CASES = [
    "glob.glob('*.py')",
    "glob.glob('*')",
    "glob.glob('.*')",
    "glob.glob('?.py')",
    "glob.glob('[ab].*')",
    "glob.glob('[!a]*.py')",
    "glob.glob('[a-b]*')",
    "glob.glob('data[12].csv')",
    "glob.glob('*.PY')",
    "glob.glob('x[[]1].txt')",
    "glob.glob('*.txt')",
    "glob.glob('sub/*')",
    "glob.glob('sub/*.py')",
    "glob.glob('sub/*/*.py')",
    "glob.glob('*/*.py')",
    "glob.glob('*/')",
    "glob.glob('sub/')",
    "glob.glob('sub/deeper/')",
    "glob.glob('emptydir/')",
    "glob.glob('nope/*.py')",
    "glob.glob('nonexistent')",
    "glob.glob('a.py')",
    "glob.glob('sub/deeper/d.py')",
    "glob.glob('sub/deeper/.hid/f.py')",
    "glob.glob('sub/deeper/.hid/*.py')",
    "glob.glob('**')",
    "glob.glob('**/*.py')",
    "glob.glob('**/*.py', recursive=True)",
    "glob.glob('**/', recursive=True)",
    "glob.glob('sub/**', recursive=True)",
    "glob.glob('**/d.*', recursive=True)",
    "glob.glob('**/.hid/*', recursive=True)",
    "glob.glob('**/*.py', recursive=True, include_hidden=True)",
    "glob.glob('*', include_hidden=True)",
    "glob.glob('.*', include_hidden=False)",
    "glob.glob('*.py', root_dir='sub')",
    "glob.glob('*', root_dir='sub/deeper', recursive=True)",
    "glob.glob('**/*.py', root_dir='sub', recursive=True)",
    "glob.glob('[]x].txt')",
    "glob.glob('*[')",
    "glob.glob('')",
    "glob.glob('subdir_only/')",
    "glob.glob('a.py/')",
    "glob.glob('ab.py')",
    "glob.glob('q?.log')",
    "glob.glob(glob.escape('q?.log'))",
    "glob.glob(glob.escape('x[1].txt'))",
    "glob.glob(glob.escape('star*name.md'))",
    "glob.glob('star*name.md')",
    "glob.glob('é*')",
    "glob.glob('*.md')",
    "glob.glob('*/*/*')",
    "glob.glob('sub/deeper/*/')",
    "glob.iglob('*.py')",
    "glob.escape('a[b]*?')",
    "glob.has_magic('a*')",
    "glob.has_magic('abc')",
    "glob.has_magic('a[b')",
    "glob.has_magic('q?')",
]


@pytest.mark.parametrize("expr", _CASES)
def test_glob_case_matches_cpython(tree: Path, expr: str) -> None:
    code = (
        "import glob\n"
        f"r = {expr}\n"
        "if isinstance(r, list):\n"
        "    print(sorted(r))\n"
        "else:\n"
        "    print(sorted(list(r)) if not isinstance(r, str) else r)\n"
    )
    if expr.startswith("glob.escape") or expr.startswith("glob.has_magic"):
        code = f"import glob\nprint({expr})\n"
    _parity(tree, code)


def test_glob_absolute_pattern(tree: Path) -> None:
    pat = str(tree / "sub" / "*.py")
    _parity(tree, f"import glob\nprint(sorted(glob.glob({pat!r})))\n")


def test_glob_absolute_recursive(tree: Path) -> None:
    pat = str(tree / "**" / "*.py")
    _parity(tree, f"import glob\nprint(sorted(glob.glob({pat!r}, recursive=True)))\n")


def test_glob_iglob_loop_consumes_all(tree: Path) -> None:
    _parity(
        tree,
        "import glob\n"
        "n = 0\n"
        "for p in sorted(glob.iglob('sub/**/*.py', recursive=True)):\n"
        "    print(p)\n"
        "    n = n + 1\n"
        "print(n)\n",
    )


def test_glob_count_and_membership(tree: Path) -> None:
    _parity(
        tree,
        "import glob\n"
        "hits = glob.glob('**/*.py', recursive=True)\n"
        "print(len(hits))\n"
        "print('sub/deeper/d.py' in hits)\n"
        "print('.hidden.py' in hits)\n",
    )


def test_glob_no_matches_is_empty_list(tree: Path) -> None:
    _parity(tree, "import glob\nr = glob.glob('zzz*')\nprint(len(r))\nprint(r == [])\n")


def test_glob_dir_fd_not_supported_fails_closed(tree: Path) -> None:
    pit = python_to_piton("import glob\nprint(glob.glob('*', dir_fd=1))\n")
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / "bin"
        compile_native_linux(pit, str(exe))
        p = run_native_linux(exe, cwd=tree, capture_output=True, text=True, timeout=60)
    assert p.returncode != 0
    assert "dir_fd" in p.stderr


def test_glob_pattern_with_newline_fails_closed(tree: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows filenames cannot contain a newline character")
    (tree / "bad\nname.py").write_text("x", encoding="utf-8")
    pit = python_to_piton("import glob\nprint(glob.glob('*.py'))\n")
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / "bin"
        compile_native_linux(pit, str(exe))
        p = run_native_linux(exe, cwd=tree, capture_output=True, text=True, timeout=60)
    assert p.returncode != 0
    assert "newline" in p.stderr
    assert not re.search(r"Traceback", p.stdout)


def test_glob_large_output_does_not_exhaust_buffers(tree: Path) -> None:
    # Regresion: partir la salida nativa con concatenacion caracter a caracter
    # agotaba el buffer de concatenacion en listados grandes.
    for i in range(12):
        d = tree / "bulk" / f"grp{i:02d}"
        d.mkdir(parents=True, exist_ok=True)
        for j in range(30):
            (d / f"item_{j:03d}.dat").write_text("y", encoding="utf-8")
    _parity(
        tree,
        "import glob\n"
        "r = glob.glob('bulk/**/*.dat', recursive=True)\n"
        "print(len(r))\n"
        "print(sorted(r)[0])\n"
        "print(sorted(r)[-1])\n"
        "print(len(glob.glob('bulk/*')))\n",
    )
