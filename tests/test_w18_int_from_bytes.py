"""Differential parity tests for int.from_bytes and byte round trips."""
from __future__ import annotations

import unittest

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


_REQUIRED_REPRODUCERS = {
    "from_bytes_bytes": (
        "imprimir(int.from_bytes(b'\\x01\\x00', 'big'), "
        "int.from_bytes(b'\\x01\\x00', 'little'))\n"
    ),
    "from_bytes_signed": "imprimir(int.from_bytes(b'\\xff\\xfe', 'big', signed=Verdadero))\n",
    "from_bytes_list": "imprimir(int.from_bytes([1, 2, 3], 'big'))\n",
    "from_bytes_bytearray_default": "imprimir(int.from_bytes(bytearray(b'\\x00\\x10')))\n",
    "to_bytes_roundtrip": (
        "n = 1027\n"
        "b = n.to_bytes(2, 'little')\n"
        "imprimir(b, int.from_bytes(b, 'little'))\n"
    ),
    "from_bytes_bad_order": (
        "intentar:\n"
        "    int.from_bytes(b'\\x01', 'medio')\n"
        "excepto ValueError como e:\n"
        "    imprimir('VE', e)\n"
    ),
}


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


@pytest.mark.parametrize("case,source", _REQUIRED_REPRODUCERS.items(), ids=_REQUIRED_REPRODUCERS.keys())
def test_required_reproducers(case: str, source: str) -> None:
    _assert_matches(unittest.TestCase(), source)


class IntFromBytes(unittest.TestCase):
    def test_lists_tuples_ranges_and_generator_expressions(self):
        _assert_matches(
            self,
            "imprimir(int.from_bytes([1, 2, 3], 'big'))\n"
            "imprimir(int.from_bytes((1, 2, 3), 'little'))\n"
            "imprimir(int.from_bytes(range(1, 4), 'big'))\n"
            "imprimir(int.from_bytes((x para x en [1, 2, 3]), 'little'))\n",
        )

    def test_signed_keyword_default_order_and_both_byteorders(self):
        _assert_matches(
            self,
            "imprimir(int.from_bytes(b'\\xff\\xfe', signed=Verdadero))\n"
            "imprimir(int.from_bytes(b'\\xff\\xfe', byteorder='little', signed=Verdadero))\n"
            "imprimir(int.from_bytes([255, 254], 'big', signed=Falso))\n",
        )

    def test_arbitrary_precision_and_empty_iterables(self):
        _assert_matches(
            self,
            "imprimir(int.from_bytes(b'\\x01\\x02\\x03\\x04\\x05\\x06\\x07\\x08\\x09'))\n"
            "imprimir(int.from_bytes(b'\\xff\\xff\\xff\\xff\\xff\\xff\\xff\\xff\\xff', signed=Verdadero))\n"
            "imprimir(int.from_bytes([1]) + 1, int.from_bytes([0]) == 0)\n"
            "imprimir(int.from_bytes([]), int.from_bytes(b''))\n",
        )

    def test_bad_iterable_values_raise_python_exceptions(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    int.from_bytes([1, 256], 'big')\n"
            "excepto ValueError como e:\n"
            "    imprimir(type(e).__name__, e)\n"
            "intentar:\n"
            "    int.from_bytes([1, -1], 'big')\n"
            "excepto ValueError como e:\n"
            "    imprimir(type(e).__name__, e)\n"
            "intentar:\n"
            "    int.from_bytes([1, 1.5], 'big')\n"
            "excepto TypeError como e:\n"
            "    imprimir(type(e).__name__, e)\n",
        )

    def test_non_iterable_raises_type_error(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    int.from_bytes(7)\n"
            "excepto TypeError como e:\n"
            "    imprimir(type(e).__name__, e)\n",
        )

    def test_to_bytes_signed_and_default_arguments(self):
        _assert_matches(
            self,
            "imprimir((1027).to_bytes(2))\n"
            "imprimir((-2).to_bytes(1, 'big', signed=Verdadero))\n"
            "imprimir(int.from_bytes((-2).to_bytes(1, 'big', signed=Verdadero), signed=Verdadero))\n",
        )

    def test_custom_iterable_stays_fail_closed(self):
        source = (
            "clase MiIterable:\n"
            "    funcion __iter__(self):\n"
            "        devolver iter([1, 2])\n"
            "int.from_bytes(MiIterable())\n"
        )
        with self.assertRaisesRegex(NativeBuildError, "custom iterables"):
            compare_native_to_cpython(source)


if __name__ == "__main__":
    unittest.main()
