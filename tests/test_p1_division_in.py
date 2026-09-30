"""TRUEDIV_V1 + CONTAINS_V1: P1 parity gates — real division and membership.

The 155-case parity probe (see test_p0_parity.py) measured these as the
largest NATIVE_GAP cluster:

  - int `/` was pinned fail-closed (test_compilar_reporta_error_nativo used
    `imprimir(7 / 2)` as the out-of-scope sample!)
  - float `/` and `//` raised "float operator not supported yet"
  - `a in b` / `a no en b` raised "binary operator not supported: in"

Now: int `/` converts the i64 operands to double exactly like CPython's
long_true_divide, float `/` and `//` are IEEE divsd/floor, and division by
zero raises a CATCHABLE ZeroDivisionError on both backends. Membership
dispatches per haystack type with value comparison. Float `%` stays
fail-closed: exact fmod needs software code under -nostdlib (follow-up).
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


class TrueDivV1(unittest.TestCase):
    """int / int and float / // — real division, CPython semantics."""

    def test_int_division_basic(self):
        _assert_matches(self, "imprimir(7 / 2, -7 / 2)\n")

    def test_int_division_non_terminating(self):
        _assert_matches(self, "imprimir(1 / 3)\n")

    def test_int_division_constants_fold(self):
        # const/const folds exactly in Python (the oracle); runtime values
        # go through the checked helper with the double conversion.
        _assert_matches(self, "imprimir(9 / 4)\na = 9\nimprimir(a / 4)\n")

    def test_int_division_result_is_a_float(self):
        # the / result must be typed float downstream, not printed as raw bits
        _assert_matches(self, "x = 7 / 2\nimprimir(x)\nimprimir(x * 2)\n")

    def test_int_division_by_zero_is_catchable(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    imprimir(7 / 0)\n"
            "excepto ZeroDivisionError:\n"
            "    imprimir('z')\n",
        )

    def test_int_division_by_zero_uncaught_exits_cleanly(self):
        result = compare_native_to_cpython("imprimir(7 / 0)\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"ZeroDivisionError", result.native.stderr)

    def test_float_division(self):
        _assert_matches(self, "imprimir(7.0 / 2.0, 1.0 / 3.0)\n")

    def test_float_floor_division(self):
        _assert_matches(self, "imprimir(7.0 // 2.0, -7.0 // 2.0, 7.5 // 2.0)\n")

    def test_float_division_by_zero_is_catchable(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    imprimir(1.0 / 0.0)\n"
            "excepto ZeroDivisionError:\n"
            "    imprimir('fz')\n",
        )

    def test_float_floor_division_by_zero_is_catchable(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    imprimir(1.0 // 0.0)\n"
            "excepto ZeroDivisionError:\n"
            "    imprimir('ffz')\n",
        )

    def test_float_modulo(self):
        _assert_matches(self, "imprimir(7.5 % 2.0, -7.5 % 2.0, 7.5 % -2.0)\n")

    def test_float_modulo_signed_zeros(self):
        _assert_matches(self, "imprimir(-0.0 % 1.0, -0.0 % -1.0, 0.0 % -1.0)\n")

    def test_float_modulo_by_zero_is_catchable(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    imprimir(1.0 % 0.0)\n"
            "excepto ZeroDivisionError:\n"
            "    imprimir('mz')\n",
        )

    def test_float_modulo_by_zero_uncaught_exits_cleanly(self):
        result = compare_native_to_cpython("imprimir(1.0 % 0.0)\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"ZeroDivisionError", result.native.stderr)


class ContainsV1(unittest.TestCase):
    """`a in b` / `a no en b` — CPython membership semantics."""

    def test_str_in_str(self):
        _assert_matches(self, 'imprimir("a" in "abc", "x" in "abc")\n')

    def test_str_in_str_empty_needle(self):
        _assert_matches(self, 'imprimir("" in "abc")\n')

    def test_str_not_in(self):
        _assert_matches(self, 'imprimir("x" no en "abc")\n')

    def test_int_in_list(self):
        _assert_matches(self, "imprimir(2 in [1, 2, 3], 9 in [1, 2])\n")

    def test_int_not_in_list(self):
        _assert_matches(self, "imprimir(5 no en [1, 2])\n")

    def test_int_in_tuple(self):
        _assert_matches(self, "imprimir(2 in (1, 2))\n")

    def test_int_in_empty_list(self):
        _assert_matches(self, "imprimir(5 in [])\n")

    def test_bool_in_list(self):
        _assert_matches(self, "imprimir(Verdadero in [Verdadero, Falso])\n")

    def test_int_in_set(self):
        _assert_matches(self, "imprimir(2 in {1, 2, 3}, 9 in {1, 2})\n")

    def test_int_in_dict_keys(self):
        _assert_matches(self, "imprimir(2 in {2: 'x'}, 9 in {2: 'y'})\n")

    def test_mixed_types_never_equal(self):
        # CPython: 2 == 'a' is False, so 2 in ['a', 'b'] is False
        _assert_matches(self, "imprimir(2 in ['a', 'b'])\n")

    @unittest.skipIf(
        sys.platform.startswith("win32"),
        "Windows backend: collection string elements are tagged PitonStr objects while "
        "literal needles are raw pointers (untagged-strings limitation, follow-up)",
    )
    def test_str_in_list(self):
        _assert_matches(self, "imprimir('a' in ['a', 'b'], 'z' in ['a'])\n")

    @unittest.skipIf(
        sys.platform.startswith("win32"),
        "Windows backend: untagged-strings limitation (str dict keys)",
    )
    def test_str_in_dict_keys(self):
        _assert_matches(self, "imprimir('a' in {'a': 1}, 'z' in {'a': 1})\n")

    def test_int_needle_in_str_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir(2 in 'abc')\n")

    def test_float_needle_fails_closed(self):
        # slot bit-equality would be silent approximation for floats
        # (CPython: -0.0 == 0.0); fail closed until the tagged runtime.
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir(1.5 in [1.5])\n")


if __name__ == "__main__":
    unittest.main()
