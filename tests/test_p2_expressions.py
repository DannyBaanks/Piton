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


class UnpackArityV1(unittest.TestCase):
    """UNPACK_ARITY_V1: unpacking verifies the element count.

    `a, b = (1, 2, 3)` used to slice silently (rc=0) while CPython raises
    ValueError. Now MIR emits an unpack_check before the per-index
    get_items: list/tuple/str verify their length at runtime (str counts
    bytes, like str get_item), anything else fails closed at build, and
    the check rides the intentar handler so ValueError routes.
    """

    def test_too_many_raises(self):
        result = compare_native_to_cpython("a, b = (1, 2, 3)\nimprimir(a, b)\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"ValueError", result.native.stderr)

    def test_too_few_raises(self):
        result = compare_native_to_cpython("a, b = (1,)\nimprimir(a, b)\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"ValueError", result.native.stderr)

    def test_too_many_caught(self):
        _assert_matches(
            self,
            "intentar:\n    a, b = (1, 2, 3)\nexcepto ValueError:\n    imprimir('muchos')\n",
        )

    def test_too_few_caught(self):
        _assert_matches(
            self,
            "intentar:\n    a, b = (1,)\nexcepto ValueError:\n    imprimir('pocos')\n",
        )

    def test_str_arity(self):
        result = compare_native_to_cpython("a, b = 'xyz'\nimprimir(a, b)\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"ValueError", result.native.stderr)

    def test_unpack_non_sequence_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("a, b = 5\nimprimir(a, b)\n")

    def test_unpack_dict_fails_closed(self):
        # dict unpacking needs key iteration (separate item); today it
        # dies at get_item with KeyError, so reject statically instead.
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("a, b = {'x': 1, 'y': 2}\nimprimir(a, b)\n")

    def test_unpack_variable(self):
        _assert_matches(self, "t = (1, 2)\na, b = t\nimprimir(a, b)\n")

    def test_unpack_call_result_list(self):
        _assert_matches(
            self, "funcion par():\n    devolver [7, 8, 9]\na, b, c = par()\nimprimir(a, b, c)\n"
        )


class ChainCmpV1(unittest.TestCase):
    """CHAINCMP_V1: chained comparisons evaluate pairwise with single
    evaluation and short-circuit.

    The Pratt parser used to nest right (`1 < (2 < 3)`) and MIR folded
    left over the bool result (`(1 < 2) < 3`), so `1 < 2 < 3` printed
    False... now True. All comparison ops share one precedence level
    (CPython) and chains merge by identity — parenthesized compares
    still nest (`(2 < 1) < 3` compares True < 3). The chain evaluates
    each operand once, in order, and skips the rest after the first
    False (observable via side effects).
    """

    def test_basic_chain(self):
        _assert_matches(self, "imprimir(1 < 2 < 3)\n")

    def test_descending_chain(self):
        _assert_matches(self, "imprimir(3 > 2 > 1)\n")

    def test_mixed_direction(self):
        _assert_matches(self, "imprimir(1 < 2 > 3)\n")

    def test_equality_chain(self):
        _assert_matches(self, "imprimir(2 == 2 == 2)\n")

    def test_failing_link(self):
        _assert_matches(self, "imprimir(2 < 1 < 3)\n")

    def test_chain_with_bool(self):
        _assert_matches(self, "imprimir(1 < 2 == Verdadero)\n")

    def test_parens_still_nest(self):
        _assert_matches(self, "imprimir((2 < 1) < 3)\n")

    def test_chain_over_expression(self):
        _assert_matches(self, "imprimir(1 < 2 + 1 < 4)\n")

    def test_long_chain(self):
        _assert_matches(self, "imprimir(1 < 2 < 3 < 4)\n")

    def test_chain_over_variable(self):
        _assert_matches(self, "x = 2\nimprimir(1 < x < 3)\n")

    def test_chain_over_strings(self):
        _assert_matches(self, "imprimir('a' < 'b' < 'c')\n")

    def test_single_evaluation_and_short_circuit(self):
        _assert_matches(
            self,
            "funcion f():\n    imprimir('lado')\n    devolver 2\n"
            "imprimir(0 < f() < 3)\nimprimir(5 < f() < 3)\n",
        )

    def test_chained_le_ge_ne(self):
        _assert_matches(self, "imprimir(1 <= 1 <= 2)\n")
        _assert_matches(self, "imprimir(3 >= 3 >= 4)\n")
        _assert_matches(self, "imprimir(1 != 2 != 1)\n")


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

class StepSliceV1(unittest.TestCase):
    """SLICE_STEP_V1: full [a:b:c] with direction-aware bound defaults."""

    def test_reverse_list(self):
        _assert_matches(self, "imprimir([1, 2, 3, 4][::-1])\n")

    def test_reverse_str(self):
        _assert_matches(self, "imprimir('hola'[::-1])\n")

    def test_reverse_tuple(self):
        _assert_matches(self, "imprimir((1, 2, 3, 4)[::-1])\n")

    def test_positive_step(self):
        _assert_matches(self, "imprimir([1, 2, 3, 4][::2])\n")

    def test_negative_step_bounds(self):
        _assert_matches(self, "imprimir([1, 2, 3, 4, 5][3:0:-1])\n")

    def test_negative_step_stride(self):
        _assert_matches(self, "imprimir([1, 2, 3, 4, 5][4:1:-2])\n")

    def test_negative_step_str(self):
        _assert_matches(self, "imprimir('hola'[3:0:-1])\n")

    def test_runtime_step(self):
        _assert_matches(self, "n = 2\nimprimir([1, 2, 3, 4][::n])\n")

    def test_step_zero_fails_at_runtime(self):
        result = compare_native_to_cpython("imprimir([1, 2][::0])\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"ValueError", result.native.stderr)


if __name__ == "__main__":
    unittest.main()
