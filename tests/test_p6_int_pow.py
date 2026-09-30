"""INT_POW_V1: exact `int ** int` for non-negative exponents.

The last MISMATCH of the parity probe: `9223372036854775807 + 1` is fixed,
but `2 ** 70` wrapped silently. Now:

  - constant operands fold EXACTLY in Python (bignum arithmetic IS the
    oracle): small results stay plain ints (better downstream: indexing,
    int arithmetic, no bigint prelude needed), large results promote to
    bigint literals, negative exponents yield the exact float;
  - runtime operands use square-and-multiply over the existing bigint
    helpers (Linux piton_bigint_pow_small, Windows native_runtime.c twin);
  - a runtime negative exponent cannot produce a bigint in the untagged
    model, so it raises a CATCHABLE ValueError instead of corrupting
    (folded literals like `2 ** -1` still yield the exact 0.5).

Along the way this fixed a pre-existing printer hole: bigint zeros shaped
{sign:0, count:0} (from sub/mul to zero) printed empty — the print check
only recognized {0, 1, [0]}. Now any all-zero limb shape prints "0".
"""
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


class IntPowV1(unittest.TestCase):
    def test_fold_small_stays_int(self):
        _assert_matches(self, "imprimir(2 ** 10)\n")

    def test_fold_big_promotes(self):
        _assert_matches(self, "imprimir(10 ** 30)\n")

    def test_fold_negative_exponent_yields_float(self):
        _assert_matches(self, "imprimir(2 ** -1, 10 ** -2)\n")

    def test_fold_zero_base(self):
        _assert_matches(self, "imprimir(0 ** 0, 0 ** 5)\n")

    def test_fold_negative_base(self):
        _assert_matches(self, "imprimir((-2) ** 3, (-2) ** 4)\n")

    def test_runtime_pow(self):
        _assert_matches(self, "x = 3\nz = x ** 4\nimprimir(z)\n")

    def test_runtime_pow_big(self):
        _assert_matches(self, "x = 10\nimprimir(x ** 25)\n")

    def test_runtime_pow_zero(self):
        _assert_matches(self, "x = 0\nimprimir(x ** 5)\n")

    def test_fold_small_result_flows_as_int(self):
        _assert_matches(self, "x = 2 ** 3\nimprimir(x + 1)\n")

    def test_bigint_result_flows_downstream(self):
        _assert_matches(self, "x = 10 ** 30\nimprimir(x + 1)\n")

    def test_runtime_negative_exponent_is_catchable(self):
        # designed divergence: CPython returns a float; the untagged model
        # cannot union bigint|float, so it raises a catchable ValueError.
        result = compare_native_to_cpython(
            "intentar:\n"
            "    x = 2\n"
            "    e = 0 - 1\n"
            "    imprimir(x ** e)\n"
            "excepto ValueError:\n"
            "    imprimir('v')\n"
        )
        self.assertEqual(result.native.returncode, 0)
        self.assertEqual(result.native.stdout.replace(b"\r\n", b"\n"), b"v\n")

    def test_zero_to_negative_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir(0 ** -1)\n")

    def test_bigint_base_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir((10 ** 30) ** 2)\n")


class BigintZeroPrintV1(unittest.TestCase):
    """bigint zeros print "0" whatever their limb shape."""

    def test_sub_to_zero(self):
        _assert_matches(self, "imprimir(10 ** 30 - 10 ** 30)\n")

    def test_mul_to_zero(self):
        _assert_matches(self, "imprimir(10 ** 30 * 0)\n")


if __name__ == "__main__":
    unittest.main()
