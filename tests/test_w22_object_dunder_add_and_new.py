"""Wave 22 parity coverage for object.__add__ and super().__new__ on builtin bases."""
from __future__ import annotations

import unittest

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    result = compare_native_to_cpython(source)
    testcase.assertEqual(
        result.native.returncode,
        result.oracle.returncode,
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\noracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(
        result.native.stdout,
        result.oracle.stdout,
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}\noracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(result.native.stderr, result.oracle.stderr, "stderr mismatch")


class ObjectDunderAddAndNewParity(unittest.TestCase):
    def test_object_binop_add_with_none_conflict(self):
        """Test __add__ with potential None conflict."""
        _assert_matches(
            self,
            "clase Vec:\n"
            "    funcion __init__(self, x):\n"
            "        self.x = x\n"
            "    funcion __add__(self, otro):\n"
            "        devolver Vec(self.x + otro.x)\n"
            "funcion sumar(a, b):\n"
            "    v = None\n"
            "    si a > 0:\n"
            "        v = Vec(a)\n"
            "    devolver (v + Vec(b)).x\n"
            "imprimir(sumar(3, 4))\n",
        )

    def test_super_new_str_builtin_base_compiles_and_runs(self):
        """Test super().__new__ on str builtin base compiles and runs (original reproducer)."""
        _assert_matches(
            self,
            "clase MiStr(str):\n"
            "    funcion __new__(cls, v):\n"
            "        devolver super().__new__(cls, v)\n"
            "imprimir('compila')\n",
        )

    def test_super_new_int_builtin_base_compiles_and_runs(self):
        """Test super().__new__ on int builtin base compiles and runs."""
        _assert_matches(
            self,
            "clase MiInt(int):\n"
            "    funcion __new__(cls, v):\n"
            "        devolver super().__new__(cls, v)\n"
            "imprimir('compila')\n",
        )

    def test_super_new_float_builtin_base_compiles_and_runs(self):
        """Test super().__new__ on float builtin base compiles and runs."""
        _assert_matches(
            self,
            "clase MiFloat(float):\n"
            "    funcion __new__(cls, v):\n"
            "        devolver super().__new__(cls, v)\n"
            "imprimir('compila')\n",
        )

    def test_super_new_bytes_builtin_base_compiles_and_runs(self):
        """Test super().__new__ on bytes builtin base compiles and runs."""
        _assert_matches(
            self,
            "clase MiBytes(bytes):\n"
            "    funcion __new__(cls, v):\n"
            "        devolver super().__new__(cls, v)\n"
            "imprimir('compila')\n",
        )

    def test_super_new_list_still_fail_closed(self):
        """Test that super().__new__ on list (collection base) still fails closed."""
        source = (
            "clase MiLista(list):\n"
            "    funcion __new__(cls, v):\n"
            "        devolver super().__new__(cls, v)\n"
        )
        with pytest.raises(
            NativeBuildError,
            match=r"native list-subclass overriding '__new__' is not supported",
        ):
            compare_native_to_cpython(source)

    def test_super_new_dict_still_fail_closed(self):
        """Test that super().__new__ on dict (collection base) still fails closed."""
        source = (
            "clase MiDict(dict):\n"
            "    funcion __new__(cls, v):\n"
            "        devolver super().__new__(cls, v)\n"
        )
        with pytest.raises(
            NativeBuildError,
            match=r"native dict-subclass overriding '__new__' is not supported",
        ):
            compare_native_to_cpython(source)

    def test_super_new_tuple_builtin_base_compiles_and_runs(self):
        """Test super().__new__ on tuple builtin base compiles and runs."""
        _assert_matches(
            self,
            "clase MiTuple(tuple):\n"
            "    funcion __new__(cls, v):\n"
            "        devolver super().__new__(cls, v)\n"
            "imprimir('compila')\n",
        )

    def test_super_new_set_still_fail_closed(self):
        """Test that super().__new__ on set still fails closed."""
        source = (
            "clase MiSet(set):\n"
            "    funcion __new__(cls, v):\n"
            "        devolver super().__new__(cls, v)\n"
        )
        with pytest.raises(
            NativeBuildError,
            match=r"native super\(\)\.__new__ with arguments requires a modeled base implementation",
        ):
            compare_native_to_cpython(source)

    def test_super_new_frozenset_still_fail_closed(self):
        """Test that super().__new__ on frozenset still fails closed."""
        source = (
            "clase MiFrozenSet(frozenset):\n"
            "    funcion __new__(cls, v):\n"
            "        devolver super().__new__(cls, v)\n"
        )
        with pytest.raises(
            NativeBuildError,
            match=r"native super\(\)\.__new__ with arguments requires a modeled base implementation",
        ):
            compare_native_to_cpython(source)

    def test_super_new_bytearray_still_fail_closed(self):
        """Test that super().__new__ on bytearray still fails closed."""
        source = (
            "clase MiByteArray(bytearray):\n"
            "    funcion __new__(cls, v):\n"
            "        devolver super().__new__(cls, v)\n"
        )
        with pytest.raises(
            NativeBuildError,
            match=r"native super\(\)\.__new__ with arguments requires a modeled base implementation",
        ):
            compare_native_to_cpython(source)


if __name__ == "__main__":
    unittest.main()