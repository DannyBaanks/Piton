"""Tests for W22: str % dynamic dict (mapping) formatting.

This implements runtime mapping lookup for `%` formatting when the RHS is a
dict (static `dict` or dynamic `slot` holding `PK_DICT`), matching CPython 3.12
parity including `KeyError` on missing keys, format specifiers `%s`/`%d`/`%r`,
etc.
"""
from __future__ import annotations

import unittest

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


def _assert_parity(testcase: unittest.TestCase, source: str) -> None:
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


class StrPercentDynamicDictV1(unittest.TestCase):
    """Dynamic dict parameter as RHS of % formatting."""

    def test_dict_param_basic(self):
        # Function parameter used as mapping dict
        _assert_parity(self, (
            "funcion fmt(d):\n"
            "    devolver \"%(nombre)s=%(edad)d\" % d\n"
            "imprimir(fmt({\"nombre\": \"ana\", \"edad\": 30}))\n"
        ))

    def test_dict_param_missing_key_raises_keyerror(self):
        # Missing key raises KeyError at runtime (matching CPython)
        _assert_parity(self, (
            "funcion fmt_bad(d):\n"
            "    intentar:\n"
            "        devolver \"%(x)s\" % d\n"
            "    excepto KeyError:\n"
            "        devolver \"missing\"\n"
            "imprimir(fmt_bad({\"y\": 1}))\n"
        ))

    def test_dict_param_with_format_specifiers(self):
        # Various format specifiers with dynamic dict
        _assert_parity(self, (
            "funcion fmt(d):\n"
            "    devolver \"%(a)s %(b)r %(c)d %(f).2f\" % d\n"
            "imprimir(fmt({\"a\": \"hola\", \"b\": 42, \"c\": -7, \"f\": 3.14159}))\n"
        ))

    def test_dict_param_multiple_calls(self):
        # Same function called with different dicts
        _assert_parity(self, (
            "funcion fmt(d):\n"
            "    devolver \"%(x)s\" % d\n"
            "imprimir(fmt({\"x\": 1}))\n"
            "imprimir(fmt({\"x\": \"dos\"}))\n"
            "imprimir(fmt({\"x\": Verdadero}))\n"
        ))

    def test_nested_dict_access_via_param(self):
        # Dict param with nested values
        _assert_parity(self, (
            "funcion fmt(d):\n"
            "    devolver \"%(k)s\" % d\n"
            "imprimir(fmt({\"k\": {\"inner\": \"value\"}}))\n"
        ))

    def test_dict_param_empty_dict(self):
        # Empty dict with format spec (should raise KeyError)
        _assert_parity(self, (
            "funcion fmt(d):\n"
            "    intentar:\n"
            "        devolver \"%(x)s\" % d\n"
            "    excepto KeyError:\n"
            "        devolver \"empty\"\n"
            "imprimir(fmt({}))\n"
        ))

    def test_static_dict_with_non_string_key(self):
        # Static dict with non-string key goes to runtime, raises KeyError
        _assert_parity(self, "imprimir('%(a)s' % {1: 'x'})\n")

    def test_static_dict_with_variable_string_key(self):
        # Static dict with variable but string key works
        _assert_parity(self, "k = 'a'\nd = {k: 1}\nimprimir('%(a)s' % d)\n")

    def test_dict_param_with_star_width_precision_fails_closed(self):
        # Dynamic width/precision still fails closed (not supported)
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("funcion f(d):\n    devolver '%(a)*s' % d\n")
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("funcion f(d):\n    devolver '%(a).*s' % d\n")


class StrPercentDynamicDictEdgeCasesV1(unittest.TestCase):
    """Edge cases and negative tests."""

    def test_non_dict_argument_fails_closed(self):
        # List as mapping argument fails at build time
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('%(a)s' % [1])\n")

    def test_tuple_argument_with_mapping_fails_closed(self):
        # Tuple with mapping format fails at build time
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('%(a)s' % (1,))\n")

    def test_none_argument_fails_closed(self):
        # None as mapping argument fails at build time
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('%(a)s' % Nada)\n")

    def test_mixed_positional_and_mapping_fails_closed(self):
        # Mixing positional and mapping conversions fails at build time
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('%s %(a)s' % (1,))\n")

    def test_unknown_mapping_key_in_static_dict_fails_closed(self):
        # Static dict with known keys but missing key in format fails at build
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('%(z)s' % {'a': 1})\n")


if __name__ == "__main__":
    unittest.main()