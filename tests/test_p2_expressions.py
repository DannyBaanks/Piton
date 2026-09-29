"""PARITY_P2_V1: expression-level gaps where the native parser lagged the translator.

13 probe cases (probe2, 155-case battery) were ParseErrors in the native
frontend while the CPython-translated path ran them fine. Four constructs:

  - ternario `a si cond sino b`  — IfExpr existed in CST/HIR but the parser
    never built it and MIR never lowered it
  - desempaquetado `a, b = ...`  — CPython models it as a Tuple store-target
  - slices `s[a:b]` (str/list/tuple) — including the `'literal'[1:3]` case,
    which additionally needed string literals to compose with postfix `[`
  - índice de str `s[i]` — runtime gap: subscription on str was fail-closed

The comprehension `si` filter regression is pinned too: the iterable and
filter slots parse with allow_ternary=False, the same ambiguity CPython
resolves by using or_test in comp_if.
"""
from __future__ import annotations

import sys
import unittest

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.parser import parse
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


class TernaryV1(unittest.TestCase):
    """`a si cond sino b` as a first-class expression."""

    def test_ternary_in_call_args(self):
        _assert_matches(self, "x = 1\nimprimir('si' si x == 1 sino 'no')\n")

    def test_ternary_assigned(self):
        _assert_matches(self, "x = 5\nr = 'alto' si x > 3 sino 'bajo'\nimprimir(r)\n")

    def test_ternary_int_arms(self):
        _assert_matches(self, "x = 5\nimprimir(x si x > 3 sino 0)\n")

    def test_ternary_nested(self):
        _assert_matches(self, "x = 2\nimprimir('a' si x == 1 sino 'b' si x == 2 sino 'c')\n")

    def test_ternary_in_list(self):
        _assert_matches(self, "x = 1\nl = [9 si x == 1 sino 8]\nimprimir(l)\n")

    def test_comprehension_filter_regression(self):
        # the ternary hook must NOT eat the comprehension `si` filter
        # (comprehensions over rango() are a separate, pre-existing gap —
        # this gate pins the filter interaction, so it iterates a list)
        _assert_matches(self, "imprimir([x para x en [0, 1, 2, 3, 4, 5] si x % 2 == 0])\n")

    def test_comprehension_elt_ternary(self):
        _assert_matches(self, "imprimir([x si x > 2 sino 0 para x en [1, 2, 3, 4]])\n")


class UnpackV1(unittest.TestCase):
    """`a, b = ...` — CPython's tuple store-target."""

    def test_unpack_tuple(self):
        _assert_matches(self, "a, b = (1, 2)\nimprimir((a, b))\n")

    def test_unpack_list(self):
        _assert_matches(self, "a, b = [10, 20]\nimprimir((a, b))\n")

    def test_unpack_three(self):
        _assert_matches(self, "a, b, c = (1, 2, 3)\nimprimir((a, b, c))\n")

    def test_unpack_string(self):
        _assert_matches(self, "a, b = 'xy'\nimprimir(a, b)\n")

    @unittest.skipIf(
        sys.platform.startswith("win32"),
        "unpacking a function RETURN VALUE needs return-type inference, and "
        "RETURNTYPE_V1 is implemented in the Linux backend first (Windows follow-up); "
        "unpack itself is covered by the literal cases above",
    )
    def test_unpack_expression_value(self):
        # the value must be evaluated once and indexed per target
        _assert_matches(
            self,
            "funcion par():\n    devolver (7, 8)\na, b = par()\nimprimir((a, b))\n",
        )

    def test_nested_tuple_target_fails_closed(self):
        # parses, but the MIR layer rejects nested tuple targets fail-closed
        parse("a, (b, c) = (1, (2, 3))\n")
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("a, (b, c) = (1, (2, 3))\n")


class SliceV1(unittest.TestCase):
    """`s[a:b]` on str/list/tuple, including on literals."""

    def test_str_slice_on_literal(self):
        _assert_matches(self, "imprimir('hola'[1:3])\n")

    def test_str_slice_open_bounds(self):
        _assert_matches(self, "s = 'hola'\nimprimir(s[2:], s[:2])\n")

    def test_str_slice_negative(self):
        _assert_matches(self, "imprimir('hola'[-3:-1])\n")

    def test_list_slice_on_literal(self):
        _assert_matches(self, "imprimir([1, 2, 3, 4][1:3])\n")

    def test_list_slice_open_bounds(self):
        _assert_matches(self, "l = [1, 2, 3, 4]\nimprimir(l[1:], l[:2], l[:99])\n")

    def test_list_slice_negative(self):
        _assert_matches(self, "imprimir([1, 2, 3, 4][-3:-1])\n")

    def test_tuple_slice(self):
        _assert_matches(self, "imprimir((1, 2, 3, 4)[1:3])\n")

    def test_slice_clamps_to_empty(self):
        _assert_matches(self, "imprimir([1, 2][5:7], 'ab'[9:])\n")

    def test_slice_result_is_collection(self):
        # sliced result keeps its type: printable, iterable, sliceable again
        _assert_matches(self, "l = [1, 2, 3, 4]\ns = l[1:]\nimprimir(s, s[0], s[-1])\n")

    def test_str_index_on_literal(self):
        _assert_matches(self, "imprimir('hola'[1])\n")

    def test_str_index_negative(self):
        _assert_matches(self, "s = 'hola'\nimprimir(s[1], s[-1])\n")

    def test_slice_with_step_fails_closed(self):
        # the step parses and is carried; the MIR layer rejects it fail-closed
        parse("imprimir([1, 2, 3][0:2:1])\n")
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir([1, 2, 3][0:2:1])\n")


if __name__ == "__main__":
    unittest.main()
