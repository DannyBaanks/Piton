"""SLOT_RETURN_PROPAGATE_V1: PK_BOOL must survive function return.

A value read from a dict parameter / **kwargs and returned from a function
must keep its kind (True prints as True, not 1) through print(), %, the
equality operator and call chains. Each program is checked against CPython,
the piton interpreter (`piton ejecutar`) and the native Linux build.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from native_linux import compile_native_linux, run_native_linux
from tools.parity_corpus_sec import python_to_piton


def _run_cpython(py_code: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", py_code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"CPython failed ({proc.returncode}): {proc.stderr}"
    return proc.stdout


def _run_ejecutar(piton_code: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "prog.piton"
        src.write_text(piton_code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "piton", "ejecutar", str(src)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert proc.returncode == 0, (
        f"piton ejecutar failed ({proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc.stdout


def _run_native(piton_code: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / "prog_bin"
        compile_native_linux(piton_code, str(exe))
        proc = run_native_linux(
            exe,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert proc.returncode == 0, (
        f"native failed ({proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc.stdout


def _assert_parity(py_code: str) -> None:
    piton_code = python_to_piton(py_code)
    cp_out = _run_cpython(py_code)
    ej_out = _run_ejecutar(piton_code)
    assert ej_out == cp_out, f"ejecutar mismatch:\nCPython:\n{cp_out}\nejecutar:\n{ej_out}"
    na_out = _run_native(piton_code)
    assert na_out == cp_out, f"native mismatch:\nCPython:\n{cp_out}\nnative:\n{na_out}"


def test_kwargs_return_bool_prints_true():
    _assert_parity(
        """
def fk(**kw):
    return kw['b']
print(fk(b=True))
"""
    )


def test_kwargs_return_bool_equality():
    _assert_parity(
        """
def fk(**kw):
    return kw['b']
print(fk(b=True) == True, fk(b=False) == False)
"""
    )


def test_kwargs_return_bool_percent_format():
    _assert_parity(
        """
def fk(**kw):
    return kw['b']
print('%s' % fk(b=True))
"""
    )


def test_kwargs_return_int_stays_int():
    _assert_parity(
        """
def fk(**kw):
    return kw['b']
print(fk(b=1))
"""
    )


def test_param_dict_return_bool_keeps_kind():
    _assert_parity(
        """
def fp(d):
    return d['b']
print(fp({'b': True}))
print(fp({'b': False}))
print(fp({'b': True}) == True)
"""
    )


def test_return_bool_assigned_then_negated():
    _assert_parity(
        """
def fk(**kw):
    return kw['b']
x = fk(b=False)
print(x)
print(not x)
"""
    )


def test_return_bool_through_kwargs_call_chain():
    _assert_parity(
        """
def fk(**kw):
    return kw['b']
def g(**kw):
    return fk(**kw)
print(g(b=True))
"""
    )


def test_return_bool_through_dict_unpack_call():
    _assert_parity(
        """
def fk(**kw):
    return kw['b']
print(fk(**dict(b=True)))
"""
    )


def test_return_bool_in_list_literal():
    _assert_parity(
        """
def fk(**kw):
    return kw['b']
print([fk(b=True)])
"""
    )


def test_return_bool_in_tuple_percent_format():
    _assert_parity(
        """
def fp(d):
    return d['b']
print('v=%s' % (fp({'b': True}),))
print('%s and %s' % (fp({'b': True}), fp({'b': False})))
"""
    )


def test_tuple_percent_format_mixed_kinds():
    _assert_parity(
        """
print('%s|%s|%s|%s' % (True, 'ab', 3, [1, 2]))
print('%s' % (None,))
print('%d-%s' % (7, 'x'))
"""
    )
