"""Differential coverage for unbound builtin type methods on Linux."""
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
class UnboundBuiltinMethodsV1(unittest.TestCase):
    def test_direct_unbound_calls_preserve_results_and_mutation(self):
        _assert_matches(
            self,
            "imprimir(float.fromhex('0.1'), float.fromhex('0x1.8p+1'))\n"
            "imprimir(float.fromhex('0x1.00000000000008p0'))\n"
            "imprimir(float.fromhex('0x619aa15d2.98cf2277p+79'))\n"
            "imprimir(float.fromhex('0x1p-1075'), float.fromhex('0x3p-1075'))\n"
            "imprimir(int.bit_length(0), int.bit_length(-17), int.bit_length(128))\n"
            "imprimir(int.bit_length(2 ** 130), int.bit_length(-(2 ** 130)))\n"
            "values = []\n"
            "result = list.append(values, 'added')\n"
            "large = 2 ** 130\n"
            "list.append(values, large)\n"
            "imprimir(str.upper('piton'), result, values)\n",
        )

    def test_unbound_methods_work_as_values_for_calls_and_map(self):
        _assert_matches(
            self,
            "uppercase = str.upper\n"
            "imprimir(uppercase('callback'))\n"
            "imprimir(list(map(str.upper, ['one', 'TWO', 'straße', 'é'])))\n"
            "imprimir(list(map(int.bit_length, [0, 7, 16, 2 ** 130])))\n"
            "imprimir(list(map(float.fromhex, ['0.1', '0x1p+2'])))\n",
        )

    def test_all_supported_descriptors_can_be_saved_and_called(self):
        _assert_matches(
            self,
            "values = []\n"
            "upper = str.upper\n"
            "bit_length = int.bit_length\n"
            "fromhex = float.fromhex\n"
            "append = list.append\n"
            "instancecheck = type.__instancecheck__\n"
            "append(values, 'saved')\n"
            "imprimir(upper('value'), bit_length(17), fromhex('0x1p-1'))\n"
            "imprimir(instancecheck(int, Verdadero), values)\n",
        )

    def test_float_fromhex_overflow_raises_overflowerror(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    float.fromhex('0x1p+1024')\n"
            "excepto OverflowError:\n"
            "    imprimir('overflow')\n"
            "intentar:\n"
            "    float.fromhex('0x.p0')\n"
            "excepto ValueError:\n"
            "    imprimir('invalid')\n"
            "imprimir(float.fromhex('-0x0p0'))\n",
        )

    def test_type_instancecheck_supports_builtin_and_user_classes(self):
        _assert_matches(
            self,
            "clase Base:\n"
            "    pasar\n"
            "clase Child(Base):\n"
            "    pasar\n"
            "value = Child()\n"
            "imprimir(type.__instancecheck__(int, Verdadero))\n"
            "imprimir(type.__instancecheck__(int, 'text'))\n"
            "imprimir(type.__instancecheck__(str, 'text'))\n"
            "imprimir(type.__instancecheck__(type, int))\n"
            "imprimir(type.__instancecheck__(Base, value))\n",
        )

    def test_wrong_receivers_raise_descriptor_typeerror(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    str.upper(3)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n"
            "intentar:\n"
            "    int.bit_length('x')\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n"
            "intentar:\n"
            "    str.upper(1.0)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n"
            "intentar:\n"
            "    str.upper(str)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n"
            "intentar:\n"
            "    int.bit_length(1.0)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n"
            "intentar:\n"
            "    list.append(list, 1)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n"
            "intentar:\n"
            "    list.append((), 1)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n"
            "intentar:\n"
            "    type.__instancecheck__(3, 1)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n"
            "intentar:\n"
            "    float.fromhex(1)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n",
        )

    def test_unsupported_unbound_methods_and_non_unary_map_callbacks_fail_closed(self):
        with pytest.raises(NativeBuildError, match=r"unbound int.to_bytes\(\) is not supported"):
            compare_native_to_cpython("imprimir(int.to_bytes(5, 2))\n")
        with pytest.raises(NativeBuildError, match="requires 2 positional arguments"):
            compare_native_to_cpython("imprimir(list(map(list.append, [[]])))\n")


if __name__ == "__main__":
    unittest.main()
