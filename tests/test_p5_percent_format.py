"""PCT_FORMAT_V1: "%s-%d" % args — printf-style formatting.

The probe case `imprimir("%s-%d" % ("x", 3))` died with "string binary
operator not supported: %". Now: the template (always a literal) is
translated to a {}-template at BUILD time and lowered through the
piton_str_format engine from STR_METHODS_V1, with per-spec conversions:

  %s (str() conversion), %d/%i (int conversion, truncating floats),
  %r (repr conversion, CPython quote preference), %c (int codepoint or
  1-character str), %% (literal %).

Flags -, 0, static widths/precisions, %x/%X/%o/%u and %(name)s
mappings over inline dict literals are supported (P14); '+', ' ', '#'
flags, length modifiers, float conversions, dynamic (*) widths and
unknown specs still fail closed at build (the template is const, so
every rejection is static). Placeholder/argument mismatches and
non-literal templates also fail closed at build — CPython raises
TypeError at runtime for the former; the static subset rejects instead.
Tuple arguments need statically known elements; mapping arguments need
inline dict literals with literal str keys; anything else fails closed.
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
        result = compare_native_to_cpython("imprimir('%s %s' % (1,))\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError", result.native.stderr)

    def test_width_now_supported(self):
        # P14 lifted the V1 width restriction.
        _assert_matches(self, "imprimir('%5d' % 3)\n")

    def test_float_spec(self):
        # P36: %f/%e/%g/%G supported via the decimal rounding helpers.
        for source in (
            "imprimir('%f' % 1.5)\n",
            "imprimir('%.2f' % 1.567)\n",
            "imprimir('%e' % 1.5)\n",
            "imprimir('%g' % 1.5)\n",
            "imprimir('%08.3f' % 1.5)\n",
        ):
            _assert_matches(self, source)

    def test_mapping_now_supported(self):
        # P14 lifted the V1 mapping restriction (inline literal dicts).
        _assert_matches(self, "imprimir('%(a)s' % {'a': 1})\n")

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
        self.assertIn(b"OverflowError", result.native.stderr)


class PercentSpecsP14(unittest.TestCase):
    """P14: width/precision/flags, %x/%X/%o/%u and %(name)s mappings.

    Every case is a byte-exact differential vs CPython (returncode +
    stdout). Width/precision apply through piton_str_pad (sign-aware zero
    padding, numeric precision, char-truncation for strings including
    multibyte). Mappings resolve inline dict literals with literal str
    keys at build time; element types resolve at use time so a variable
    reassigned after the tuple/dict is built still converts by its
    current type. Anything dynamic (parameters, computed widths, unknown
    keys) fails closed at build.
    """

    def test_width_and_alignment(self):
        _assert_matches(self, "imprimir('%5d' % 42)\n")
        _assert_matches(self, "imprimir('%-5d|x' % 42)\n")
        _assert_matches(self, "imprimir('%5d' % -42)\n")
        _assert_matches(self, "imprimir('%-5d' % -42)\n")

    def test_zero_flag(self):
        _assert_matches(self, "imprimir('%05d' % 42)\n")
        _assert_matches(self, "imprimir('%05d' % -42)\n")
        _assert_matches(self, "imprimir('%-05d' % 42)\n")
        _assert_matches(self, "imprimir('%04x' % 255)\n")

    def test_numeric_precision(self):
        _assert_matches(self, "imprimir('%5.3d' % 7)\n")
        _assert_matches(self, "imprimir('%.0d' % 0)\n")
        _assert_matches(self, "imprimir('%5.0d' % 0)\n")
        _assert_matches(self, "imprimir('%.3d' % -7)\n")

    def test_string_precision(self):
        _assert_matches(self, "imprimir('%.3s' % 'abcdef')\n")
        _assert_matches(self, "imprimir('%8.3s' % 'abcdef')\n")
        _assert_matches(self, "imprimir('%-8.3s' % 'abcdef')\n")

    def test_hex_octal_unsigned(self):
        _assert_matches(self, "imprimir('%x' % 255)\n")
        _assert_matches(self, "imprimir('%X' % 255)\n")
        _assert_matches(self, "imprimir('%o' % 8)\n")
        _assert_matches(self, "imprimir('%x' % -1)\n")
        _assert_matches(self, "imprimir('%5x' % 255)\n")
        _assert_matches(self, "imprimir('%-6X|' % 255)\n")
        _assert_matches(self, "imprimir('%u' % -5)\n")
        _assert_matches(self, "imprimir('%i' % 7)\n")

    def test_char_width(self):
        _assert_matches(self, "imprimir('%5c' % 65)\n")
        _assert_matches(self, "imprimir('%-5c|' % 65)\n")

    def test_bool_none_conversions(self):
        _assert_matches(self, "imprimir('%r' % Verdadero)\n")
        _assert_matches(self, "imprimir('%d' % Verdadero)\n")
        _assert_matches(self, "imprimir('%s' % Nada)\n")

    def test_repr_width_precision(self):
        _assert_matches(self, "imprimir('%10r' % 'ab')\n")
        _assert_matches(self, "imprimir('%.3r' % 'abcdef')\n")

    def test_mapping_inline(self):
        _assert_matches(self, "imprimir('%(a)s-%(b)d' % {'a': 'x', 'b': 3})\n")
        _assert_matches(self, "imprimir('%(n)05d' % {'n': 42})\n")
        _assert_matches(self, "imprimir('%(k)x' % {'k': 255})\n")
        _assert_matches(self, "imprimir('%(a).2s' % {'a': 'abcdef'})\n")

    def test_mapping_variable(self):
        _assert_matches(self, "d = {'a': 1}\nimprimir('%(a)s' % d)\n")

    def test_use_time_types(self):
        # the tuple/dict captures the VALUE at build; the conversion reads
        # the CURRENT static type, so reassignment after building converts
        # by the type the slot actually holds at use.
        _assert_matches(self, "x = 1\nt = (x,)\nx = 's'\nimprimir('%s' % t)\n")
        _assert_matches(self, "x = 1\nd = {'a': x}\nx = 's'\nimprimir('%(a)s' % d)\n")

    def test_handler_routing(self):
        _assert_matches(
            self,
            "intentar:\n    imprimir('%c' % 1114112)\nexcepto OverflowError:\n    imprimir('caught')\n",
        )
        _assert_matches(
            self,
            "intentar:\n    imprimir('%c' % 'ab')\nexcepto TypeError:\n    imprimir('caught')\n",
        )
        # a successful formatting inside intentar keeps its value (the
        # Windows backend once stored the catch flag over the result).
        _assert_matches(
            self,
            "intentar:\n    imprimir('%05d' % 7)\nexcepto ValueError:\n    imprimir('caught')\n",
        )

    def test_still_closed(self):
        for source in (
            "imprimir('%+d' % 5)\n",
            "imprimir('% d' % 5)\n",
            "imprimir('%#x' % 5)\n",
            "imprimir('%*d' % (5, 42))\n",
            "imprimir('%.*s' % (3, 'abcdef'))\n",
            "imprimir('%ld' % 5)\n",
            "imprimir('%s %(a)s' % (1,))\n",
            "imprimir('%(z)s' % {'a': 1})\n",
            "imprimir('%(a)s' % [1])\n",
            "imprimir('%(a)s' % {1: 'x'})\n",
            "imprimir('%5%' % ())\n",
            "imprimir('%.3c' % 65)\n",
            "imprimir('%x' % 3.5)\n",
            "imprimir('%d' % 'a')\n",
            "funcion f(x):\n    imprimir('%s' % (x,))\nf(1)\n",
            "k = 'a'\nd = {k: 1}\nimprimir('%(a)s' % d)\n",
        ):
            try:
                compare_native_to_cpython(source)
            except NativeBuildError:
                pass
            else:
                self.fail(f"not closed: {source}")


if __name__ == "__main__":
    unittest.main()
