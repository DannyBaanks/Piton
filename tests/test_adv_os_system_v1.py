"""OS_SYSTEM_V1: os.system real (fork/execve/wait4) en el backend nativo, frente a CPython."""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from native_linux import compile_native_linux, run_native_linux


def _native(source: str, cwd: Path) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory(prefix="piton-ossys-") as directory:
        exe = Path(directory) / "prog"
        compile_native_linux(source, exe)
        return run_native_linux(exe, capture_output=True, text=True, timeout=30, cwd=cwd)


def _oracle(py_source: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-c", py_source], capture_output=True, text=True,
                          timeout=30, cwd=cwd)


def test_exit_status_and_output(tmp_path):
    src = 'importar os\nr = os.system("echo hola; exit 3")\nimprimir(r)\nimprimir(os.system("true"))\n'
    py = 'import os\nprint(os.system("echo hola; exit 3"))\nprint(os.system("true"))\n'
    n, o = _native(src, tmp_path), _oracle(py, tmp_path)
    assert n.returncode == 0, n.stderr
    assert n.stdout == o.stdout == "hola\n768\n0\n"


def test_mkdir_in_cwd(tmp_path):
    src = 'importar os\nimprimir(os.system("mkdir today"))\nimprimir(os.system("mkdir today 2>/dev/null"))\n'
    n = _native(src, tmp_path)
    assert n.returncode == 0, n.stderr
    assert n.stdout == "0\n256\n"
    assert (tmp_path / "today").is_dir()


def test_non_str_raises_type_error(tmp_path):
    src = ('importar os\nintentar:\n    os.system(5)\nexcepto TypeError:\n    imprimir("TypeError")\n')
    py = 'import os\ntry:\n    os.system(5)\nexcept TypeError:\n    print("TypeError")\n'
    n, o = _native(src, tmp_path), _oracle(py, tmp_path)
    assert n.returncode == 0, n.stderr
    assert n.stdout == o.stdout == "TypeError\n"
