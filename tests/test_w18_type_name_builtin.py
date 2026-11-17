"""Differential coverage for builtin type class objects."""
from __future__ import annotations

import unittest

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


CASES = {
    "type_name_int": "imprimir(type(3).__name__)\n",
    "type_name_many": "imprimir([type(v).__name__ para v en [1, 'a', 2.5, None, [], (), {}, Verdadero, b'x']])\n",
    "type_is": "imprimir(type('s') es str, type(1) == int, type(None))\n",
    "type_qualname_user": "clase A:\n    pasar\nimprimir(type(A()).__name__, type(A()).__qualname__, type(A))\n",
}


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    result = compare_native_to_cpython(source)
    testcase.assertEqual(
        result.native.returncode,
        result.oracle.returncode,
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}; "
        f"oracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(
        result.native.stdout,
        result.oracle.stdout,
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}; "
        f"oracle stderr: {result.oracle.stderr!r}",
    )


class BuiltinTypeNameParity(unittest.TestCase):
    def test_type_name_int(self):
        _assert_matches(self, CASES["type_name_int"])

    def test_type_name_many_builtin_values(self):
        _assert_matches(self, CASES["type_name_many"])

    def test_type_identity_and_repr(self):
        _assert_matches(self, CASES["type_is"])

    def test_user_class_type_metadata_is_unchanged(self):
        _assert_matches(self, CASES["type_qualname_user"])

    def test_builtin_type_metadata_and_identity(self):
        _assert_matches(
            self,
            "imprimir(type(3), type(3).__name__, type(3).__qualname__, type(3).__module__)\n"
            "imprimir(type(None).__name__, type(None).__module__)\n"
            "imprimir(type(b'x').__name__, type(b'x').__module__)\n"
            "imprimir(type(1) es int, type(1) == int, type(1) es type(2))\n"
            "imprimir(type(b'x') es bytes, type(b'x') == bytes)\n"
            "values = [1, 'a', 2.5, None, [], (), {}, Verdadero, b'x']\n"
            "imprimir([type(v).__qualname__ para v en values])\n"
            "imprimir([type(v).__module__ para v en values])\n"
            "imprimir([type(v) para v en values])\n"
            "imprimir([type(v) es int para v en values])\n",
        )

    def test_zero_argument_type_still_fails_closed(self):
        with pytest.raises(NativeBuildError, match="Linux type requires one argument"):
            compare_native_to_cpython("imprimir(type())\n")


if __name__ == "__main__":
    unittest.main()
