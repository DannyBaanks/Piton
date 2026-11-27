"""W23: zip() sobre itertools.count(), sorted(zip(..)), splitroot y modulo en atributo de clase.

Paridad (AGENTS2 §1) contra CPython 3.12 con compare_native_to_cpython.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from piton.native_differential import compare_native_to_cpython


def _assert_parity(source: str) -> None:
    result = compare_native_to_cpython(source)
    assert result.native.returncode == result.oracle.returncode, (result.native, result.oracle)
    assert result.native.stdout == result.oracle.stdout, (result.native, result.oracle)


# --- zip / sorted(zip) con itertools.count() --------------------------------

def test_zip_with_count_sorted_reverse():
    _assert_parity(
        "desde itertools importar count\n"
        "imprimir(sorted(zip(['b', 'a', 'c'], count()), reverse=Verdadero))\n"
        "imprimir(sorted(zip(['b', 'a', 'c'], count())))\n"
    )


def test_zip_count_start_step_and_empty_edge():
    _assert_parity(
        "desde itertools importar count\n"
        "imprimir(list(zip(count(5, 2), [1, 2, 3])))\n"
        "imprimir(list(zip([], count())))\n"
        "imprimir(list(zip(count(-3), 'ab')))\n"
    )


def test_sorted_zip_of_plain_lists():
    _assert_parity(
        "imprimir(sorted(zip([3, 1, 2], ['c', 'a', 'b'])))\n"
        "imprimir(sorted(zip([], [])))\n"
    )


# --- splitroot ----------------------------------------------------------------

def test_posixpath_splitroot_str_and_bytes_edges():
    _assert_parity(
        "importar posixpath\n"
        "para p en ['//a/b', '///a', '////x', '/', '//', '///', '', 'a/b', '/usr/bin']:\n"
        "    imprimir(posixpath.splitroot(p))\n"
        "para p en [b'//x', b'/x', b'///x', b'', b'x']:\n"
        "    imprimir(posixpath.splitroot(p))\n"
    )


def test_os_path_splitroot():
    _assert_parity(
        "importar os.path\n"
        "imprimir(os.path.splitroot('/usr/bin'))\n"
        "imprimir(os.path.splitroot('////x'))\n"
        "imprimir(os.path.splitroot('rel'))\n"
    )


def test_splitroot_non_path_raises_typeerror_in_both():
    _assert_parity(
        "importar posixpath\n"
        "intentar:\n"
        "    posixpath.splitroot(5)\n"
        "excepto TypeError como e:\n"
        "    imprimir(e)\n"
    )


# --- modulo guardado como atributo de clase (pathlib: cls._flavour = os.path) --

def test_class_attribute_holding_module_method_call():
    _assert_parity(
        "importar os\n"
        "clase P:\n"
        "    _f = os.path\n"
        "    @classmethod\n"
        "    funcion g(cls, p):\n"
        "        devolver cls._f.splitroot(p)\n"
        "    funcion h(este, p):\n"
        "        devolver este._f.splitroot(p)\n"
        "imprimir(P.g('//a/b'))\n"
        "imprimir(P().h('/a'))\n"
    )


# --- datetime.fromtimestamp ---------------------------------------------------

def test_datetime_datetime_fromtimestamp_matches_cpython(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    result = compare_native_to_cpython(
        "desde datetime importar datetime\n"
        "imprimir(datetime.fromtimestamp(1.5))\n"
    )
    assert result.equivalent, (result.native, result.oracle)


# --- etapa de build de la stdlib ------------------------------------------------

def _stage(name: str) -> dict:
    from parity_corpus_v4 import stage_file
    return stage_file(f"/usr/lib/python3.12/{name}.py")


def test_statistics_reaches_build():
    rec = _stage("statistics")
    assert rec["reached"] == "build" and not rec.get("stage_fail"), rec


def test_pathlib_no_longer_blocked_on_splitroot():
    rec = _stage("pathlib")
    assert "splitroot" not in (rec.get("detail") or ""), rec
