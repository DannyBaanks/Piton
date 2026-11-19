"""Differential coverage for unbound builtin collection ``.copy`` values (Linux).

Covers ``list.copy``, ``dict.copy`` and ``set.copy`` used as first-class
unbound builtin method values (``f = list.copy; f(xs)``), with CPython 3.12
parity for the copied result, mutation independence and the ``TypeError`` raised
on a wrong receiver type. Wrong arity still fails closed at build time.
"""
from __future__ import annotations

import sys
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


@unittest.skipIf(sys.platform.startswith("win32"), "Linux backend feature")
class UnboundBuiltinCopyAttrV1(unittest.TestCase):
    def test_list_copy_unbound_value_is_independent_copy(self):
        _assert_matches(
            self,
            "f = list.copy\n"
            "xs = [1, 2, 3]\n"
            "ys = f(xs)\n"
            "ys.append(4)\n"
            "imprimir(xs, ys)\n",
        )

    def test_dict_copy_unbound_value_is_independent_copy(self):
        _assert_matches(
            self,
            "g = dict.copy\n"
            "d = {'a': 1}\n"
            "d2 = g(d)\n"
            "d2['b'] = 2\n"
            "imprimir(d, d2)\n",
        )

    def test_set_copy_unbound_value_is_independent_copy(self):
        # Los sets no son imprimibles byte a byte (PYTHONHASHSEED), asi que se
        # comparan longitudes y membresia en lugar de imprimir el set.
        _assert_matches(
            self,
            "h = set.copy\n"
            "s = {1, 2, 3}\n"
            "s2 = h(s)\n"
            "s2.add(4)\n"
            "imprimir(len(s), len(s2), 4 in s, 4 in s2)\n",
        )

    def test_copy_wrong_receiver_raises_typeerror(self):
        _assert_matches(
            self,
            "f = list.copy\n"
            "intentar:\n"
            "    f(5)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n"
            "g = dict.copy\n"
            "intentar:\n"
            "    g('nope')\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n"
            "h = set.copy\n"
            "intentar:\n"
            "    h(1.5)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n",
        )

    def test_copy_wrong_arity_fails_closed_at_build(self):
        # copy() toma exactamente el receptor; cero o dos argumentos es un
        # error de compilacion cerrado, nunca un resultado silencioso.
        with pytest.raises(NativeBuildError, match="requires exactly 1 positional argument"):
            compare_native_to_cpython("f = list.copy\nimprimir(f())\n")
        with pytest.raises(NativeBuildError, match="requires exactly 1 positional argument"):
            compare_native_to_cpython("g = dict.copy\nimprimir(g({'a': 1}, 2))\n")
        with pytest.raises(NativeBuildError, match="requires exactly 1 positional argument"):
            compare_native_to_cpython("h = set.copy\nimprimir(h())\n")


if __name__ == "__main__":
    unittest.main()
