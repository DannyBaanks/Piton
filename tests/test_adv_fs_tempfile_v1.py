import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from piton.linux_x86 import compile_native_linux
from tools.parity_corpus_sec import python_to_piton

_TMP = re.compile(r"/tmp/tmp[a-z0-9_]{8}")


def _cpython(code):
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return _TMP.sub("<TMP>", p.stdout)


def _native(pit):
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / "bin"
        compile_native_linux(pit, str(exe))
        p = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return _TMP.sub("<TMP>", p.stdout)


def _ejecutar(pit):
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "p.piton"
        src.write_text(pit, encoding="utf-8")
        p = subprocess.run([sys.executable, "-m", "piton", "ejecutar", str(src)], cwd=ROOT,
                           capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return _TMP.sub("<TMP>", p.stdout)


def _parity(code, ejecutar=False):
    expected = _cpython(code)
    pit = python_to_piton(code)
    assert _native(pit) == expected
    if ejecutar:
        assert _ejecutar(pit) == expected


def test_corpus_style_basename_dirname_exists():
    _parity(
        "import os, tempfile\n"
        "tmpdir = tempfile.mkdtemp()\n"
        "path = os.path.join(tmpdir, 'sub', 'file.txt')\n"
        "os.makedirs(os.path.dirname(path), exist_ok=True)\n"
        "with open(path,'w') as f:\n"
        "    f.write('x')\n"
        "print(os.path.basename(path), os.path.dirname(path), os.path.exists(path))\n"
    )


def test_exists_isfile_isdir():
    _parity(
        "import os, tempfile\n"
        "d = tempfile.mkdtemp()\n"
        "p = os.path.join(d, 'a.txt')\n"
        "print(os.path.exists(p))\n"
        "with open(p, 'w') as f:\n"
        "    f.write('z')\n"
        "print(os.path.exists(p), os.path.isfile(p), os.path.isdir(p))\n"
        "print(os.path.isdir(d), os.path.isfile(d))\n"
    )


def test_makedirs_nested_and_exist_ok_error():
    _parity(
        "import os, tempfile\n"
        "d = tempfile.mkdtemp()\n"
        "p = os.path.join(d, 'x', 'y', 'z')\n"
        "os.makedirs(p)\n"
        "print(os.path.isdir(p))\n"
        "os.makedirs(p, exist_ok=True)\n"
        "try:\n"
        "    os.makedirs(p)\n"
        "    print('no-error')\n"
        "except FileExistsError:\n"
        "    print('FileExistsError')\n"
    )


def test_basename_dirname_edge_cases():
    _parity(
        "import os\n"
        "for s in ['', '/', '///', '/abc', '//abc', '/a/b/c', '/a/b/', '/a/b//c', 'abc', 'a/b']:\n"
        "    print(repr(os.path.basename(s)), repr(os.path.dirname(s)))\n"
    )


def test_mkdtemp_prefix_suffix_dir_and_unique():
    _parity(
        "import os, tempfile\n"
        "a = tempfile.mkdtemp()\n"
        "b = tempfile.mkdtemp()\n"
        "print(a != b, os.path.isdir(a), os.path.isdir(b))\n"
        "c = tempfile.mkdtemp(prefix='pre_', suffix='_suf', dir=a)\n"
        "print(os.path.dirname(c) == a)\n"
        "n = os.path.basename(c)\n"
        "print(n.startswith('pre_'), n.endswith('_suf'), len(n))\n"
    )


def test_utf8_roundtrip_with_statement():
    _parity(
        "import os, tempfile\n"
        "d = tempfile.mkdtemp()\n"
        "p = os.path.join(d, 'acc.txt')\n"
        "with open(p, 'w', encoding='utf-8') as f:\n"
        "    f.write('áéíóú ñ')\n"
        "with open(p, 'r', encoding='utf-8') as f:\n"
        "    print(f.read())\n"
    )


def test_lru_cache_none_decorated_counts_one_call():
    code = (
        "from functools import lru_cache\n"
        "calls = {'n':0}\n"
        "@lru_cache(maxsize=None)\n"
        "def f(n):\n"
        "    calls['n'] += 1\n"
        "    return n*n\n"
        "print(f(2))\n"
        "print(f(2))\n"
        "print(f(3))\n"
        "print(calls['n'])\n"
    )
    _parity(code, ejecutar=True)
