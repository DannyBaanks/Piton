"""PCT_FORMAT_V1: "%s-%d" % args — printf-style formatting.

The probe case `imprimir("%s-%d" % ("x", 3))` died with "string binary
operator not supported: %". Now: the template (always a literal) is
translated to a {}-template at BUILD time and lowered through the
piton_str_format engine from STR_METHODS_V1, with per-spec conversions:

  %s (str() conversion), %d/%i (int conversion, truncating floats),
  %r (repr conversion, CPython quote preference), %c (int codepoint or
  1-character str), %% (literal %).

Flags, width, precision, length modifiers, mappings and unknown specs
fail closed at build (the template is const, so every rejection is
static). Placeholder/argument mismatches and non-literal templates also
fail closed at build — CPython raises TypeError at runtime for the
former; the static subset rejects instead. Tuple arguments need a
statically known length (tuples are immutable, so the recorded length
is sound); anything else fails closed.
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


class PercentFormatV1(unittest.TestCase):
    def test_basic_str_int(self):
        _assert_matches(self, "imprimir('%s-%d' % ('x', 3))\n")

    def test_single_str(self):
        _assert_matches(self, "imprimir('%s' % 'ab')\n")

    def test_single_int(self):
        _assert_matches(self, "imprimir('%d' % 42, '%i' % -7)\n")

    def test_float_truncates_to_int(self):
        _assert_matches(self, "imprimir('%d' % 3.7, '%d' % -3.7)\n")

    def test_bool_none(self):
        _assert_matches(self, "imprimir('%s %d' % (Verdadero, Falso), '%s' % Nada)\n")

    def test_repr(self):
        _assert_matches(self, "imprimir('%r' % 'a', '%r' % 7)\n")

    def test_repr_quote_preference(self):
        _assert_matches(self, "imprimir('%r' % \"a'b\")\n")

    def test_char_int_and_str(self):
        _assert_matches(self, "imprimir('%c' % 65, '%c' % 'z')\n")

    def test_percent_escape(self):
        _assert_matches(self, "imprimir('100%%' % ())\n")

    def test_braces_pass_through(self):
        _assert_matches(self, "imprimir('{%s}' % 1)\n")

    def test_tuple_variable(self):
        _assert_matches(self, "t = (1, 'a')\nimprimir('%d-%s' % t)\n")

    def test_count_mismatch_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('%s %s' % (1,))\n")

    def test_width_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('%5d' % 3)\n")

    def test_float_spec_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('%f' % 1.5)\n")

    def test_mapping_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('%(a)s' % {'a': 1})\n")

    def test_str_for_d_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('%d' % 'x')\n")

    def test_non_literal_template_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("f = '%s'\nimprimir(f % 1)\n")

    def test_unknown_length_tuple_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("funcion f(t):\n    devolver '%s' % t\nimprimir(f((1,)))\n")

    def test_char_multi_char_str_fails_at_runtime(self):
        result = compare_native_to_cpython("imprimir('%c' % 'ab')\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError", result.native.stderr)

    def test_char_out_of_range_fails_at_runtime(self):
        result = compare_native_to_cpython("imprimir('%c' % 99999999)\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"ValueError", result.native.stderr)


if __name__ == "__main__":
    unittest.main()
