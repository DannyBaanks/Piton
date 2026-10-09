"""STR_METHODS_V1: builtin str methods bound by static dispatch.

The last ParseError cluster of the P2 probe round (method calls on string
literals never parsed; method calls on str variables parsed but died in the
backend with "method receiver class is not statically known"). Supported,
both backends, differentials vs CPython:

  upper/lower (ASCII; non-ASCII fails closed at runtime), find,
  startswith/endswith, replace (including the empty-old edge),
  split (sep and no-arg whitespace mode), strip/lstrip/rstrip,
  join (list/tuple of str), format ({} and {N} positional, {{ }} escapes).

Everything else — arity, static arg types, unknown method names, format
specs/keywords, float % — fails closed at BUILD time. Valid-Python but
out-of-subset inputs (non-ASCII case, non-str join element, bad format
field) exit cleanly with the exception name, the uncatchable-error
convention of the other runtime helpers.
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


class CaseV1(unittest.TestCase):
    def test_upper_lower(self):
        _assert_matches(self, "imprimir('abc'.upper(), 'ABC'.lower())\n")

    def test_upper_on_variable(self):
        _assert_matches(self, "s = 'aBc'\nimprimir(s.upper())\n")

    def test_upper_with_wrong_arity_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('a'.upper('x'))\n")

    def test_non_ascii_case_fails_closed_at_runtime(self):
        result = compare_native_to_cpython("imprimir('á'.upper())\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"ValueError", result.native.stderr)


class FindV1(unittest.TestCase):
    def test_find_found_and_missing(self):
        _assert_matches(self, "imprimir('abc'.find('c'), 'abc'.find('x'))\n")

    def test_find_empty_needle(self):
        _assert_matches(self, "imprimir('abc'.find(''))\n")

    def test_find_with_start_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('a'.find('a', 1))\n")

    def test_startswith_endswith(self):
        _assert_matches(
            self,
            "imprimir('abc'.startswith('ab'), 'abc'.startswith('c'), "
            "'abc'.endswith('bc'), 'abc'.endswith('a'))\n",
        )


class ReplaceV1(unittest.TestCase):
    def test_replace(self):
        _assert_matches(self, "imprimir('aaa'.replace('a', 'b'))\n")

    def test_replace_empty_old(self):
        _assert_matches(self, "imprimir('ab'.replace('', '-'))\n")

    def test_replace_wrong_arity_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('a'.replace('a'))\n")


class SplitV1(unittest.TestCase):
    def test_split_lengths_both_platforms(self):
        # full list printing works on Linux; on Windows collection string
        # elements are untagged raw pointers (known limitation), so the
        # cross-platform gate observes through longitud.
        _assert_matches(self, "imprimir(longitud('a,b,c'.split(',')))\n")
        _assert_matches(self, "imprimir(longitud('  a  b c '.split()))\n")

    @unittest.skipIf(
        sys.platform.startswith("win32"),
        "Windows backend: split results hold untagged raw string pointers "
        "(same limitation as literal str elements in collections)",
    )
    def test_split_sep_full(self):
        _assert_matches(self, "imprimir('a,b,c'.split(','))\n")

    @unittest.skipIf(
        sys.platform.startswith("win32"),
        "Windows backend: split results hold untagged raw string pointers",
    )
    def test_split_whitespace_full(self):
        _assert_matches(self, "imprimir('  a  b c '.split())\n")

    @unittest.skipIf(
        sys.platform.startswith("win32"),
        "Windows backend: split results hold untagged raw string pointers",
    )
    def test_split_edges(self):
        _assert_matches(self, "imprimir(''.split(','), ''.split(), 'a,'.split(','))\n")

    def test_split_wrong_arity_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('a'.split(',', 1))\n")


class StripV1(unittest.TestCase):
    def test_strip_lstrip_rstrip(self):
        _assert_matches(self, "imprimir('  x  '.strip(), '  y'.lstrip(), 'z  '.rstrip())\n")

    def test_strip_with_chars(self):
        # P36: strip/lstrip/rstrip aceptan el conjunto de caracteres (CPython).
        _assert_matches(self, "imprimir('xx'.strip('x'))\n")
        _assert_matches(self, "imprimir('xxay'.lstrip('x'))\n")
        _assert_matches(self, "imprimir('ayxx'.rstrip('x'))\n")


class JoinV1(unittest.TestCase):
    def test_join_list(self):
        # STR_BOXED_V1 (Windows P43): los literales str se boxean, asi
        # que el join ya no distingue backends.
        _assert_matches(self, "imprimir(','.join(['a', 'b']), '-'.join([]))\n")

    def test_join_tuple(self):
        _assert_matches(self, "imprimir('+'.join(('a', 'b')))\n")

    def test_join_non_str_element_fails_at_runtime(self):
        result = compare_native_to_cpython("imprimir(','.join([1]))\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError", result.native.stderr)

    def test_join_non_collection_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir(','.join('ab'))\n")


class FormatV1(unittest.TestCase):
    def test_format_auto(self):
        _assert_matches(self, "imprimir('{}-{}'.format(1, 2))\n")

    def test_format_indexed(self):
        _assert_matches(self, "imprimir('{1} {0}'.format('a', 'b'))\n")

    def test_format_mixed_types(self):
        _assert_matches(self, "imprimir('n={} f={} b={} s={}'.format(3, 1.5, Verdadero, Nada))\n")

    def test_format_escaped_braces(self):
        _assert_matches(self, "imprimir('{{}} {}'.format(7))\n")

    def test_format_no_args(self):
        _assert_matches(self, "imprimir('plain'.format())\n")

    def test_format_bad_field_fails_at_build(self):
        # FMT_SPEC_V1: named placeholders now reject at BUILD (the template
        # is always a literal, so every rejection is static — an upgrade
        # from the old runtime ValueError).
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('{x}'.format(1))\n")

    def test_format_bad_index_fails_at_build(self):
        # FMT_SPEC_V1: out-of-range field indices reject at BUILD (an
        # upgrade from the old runtime IndexError).
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('{5}'.format(1))\n")

    def test_unknown_method_fails_closed(self):
        # title (P23), partition (P36), removeprefix (P38) implemented; casefold still outside
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('a'.casefold())\n")


class StrMethodsChainedV1(unittest.TestCase):
    def test_chained_str_calls(self):
        _assert_matches(self, "imprimir('  a,b '.strip().upper().replace(',', ';'))\n")


class FormatSpecV1(unittest.TestCase):
    """FMT_SPEC_V1: str.format() format specs on literal templates.

    `'{:>5}'.format('a')` used to die with "ValueError: single '{' in
    format string" — the engine only knew {} and {N}. Now the backends
    parse the template statically: each field converts to its type's
    string (d/x/X/o/b) and applies the presentation part
    ([[fill]align][sign][#][0][width][,][.prec]) through
    piton_str_apply_spec. Explicit field indices reorder correctly
    ({1} {0}) and a field may repeat ({0} {0}). Float presentations
    (f/e/g/%) fail closed: repr output diverged from CPython ({:.2f}
    printed "3.") and {:.2g} even crashed (rc=-11); unknown specs fail
    closed at build because a NULL template argument crashed the engine.
    """

    def test_align_and_fill(self):
        _assert_matches(self, "imprimir('{:>5}'.format('a'))\n")
        _assert_matches(self, "imprimir('{:<5}'.format('a'))\n")
        _assert_matches(self, "imprimir('{:^5}'.format('a'))\n")
        _assert_matches(self, "imprimir('{:_>5}'.format('a'))\n")
        _assert_matches(self, "imprimir('{:*^5}'.format('a'))\n")
        _assert_matches(self, "imprimir('{:->8}'.format('a'))\n")

    def test_numeric_pads(self):
        _assert_matches(self, "imprimir('{:05d}'.format(42))\n")
        _assert_matches(self, "imprimir('{:+d}'.format(5))\n")
        _assert_matches(self, "imprimir('{: d}'.format(5))\n")

    def test_integer_types(self):
        _assert_matches(self, "imprimir('{:x}'.format(255))\n")
        _assert_matches(self, "imprimir('{:X}'.format(255))\n")
        _assert_matches(self, "imprimir('{:o}'.format(8))\n")
        _assert_matches(self, "imprimir('{:b}'.format(5))\n")
        _assert_matches(self, "imprimir('{:x}'.format(-255))\n")

    def test_thousands_separator(self):
        _assert_matches(self, "imprimir('{:,}'.format(1234567))\n")
        _assert_matches(self, "imprimir('{:,}'.format(-1234567))\n")
        _assert_matches(self, "imprimir('{:,}'.format(0))\n")
        _assert_matches(self, "imprimir('{:,}'.format(999))\n")
        _assert_matches(self, "imprimir('{:,}'.format(1234.5))\n")

    def test_string_truncation_and_width(self):
        _assert_matches(self, "imprimir('{:5}'.format('abcdef'))\n")
        _assert_matches(self, "imprimir('{:.3}'.format('abcdef'))\n")

    def test_explicit_indices(self):
        _assert_matches(self, "imprimir('{1} {0}'.format('a', 'b'))\n")
        _assert_matches(self, "imprimir('{1:>5}|{0}'.format('a', 'b'))\n")
        _assert_matches(self, "imprimir('{0} {0}'.format('a'))\n")

    def test_escaped_braces(self):
        _assert_matches(self, "imprimir('{{}}'.format())\n")
        _assert_matches(self, "imprimir('{{}} {}'.format(1))\n")

    def test_custom_fill_percent(self):
        # % is a legal FILL character when followed by an align op
        _assert_matches(self, "imprimir('{:%>5}'.format(1))\n")

    def test_runtime_template_keeps_old_path(self):
        # a non-literal template cannot carry specs (the historical runtime
        # path handles {} / {N}; a spec inside it errors loudly there)
        _assert_matches(self, "s = 'plain'\nimprimir(s.format(1))\n")

    def test_float_presentations(self):
        # P36 FMT_FLOAT_V1: f/e/g/% with real decimal rounding.
        for source in (
            "imprimir('{:.2f}'.format(3.14))\n",
            "imprimir('{:.1%}'.format(0.5))\n",
            "imprimir('{:.2e}'.format(1234.5))\n",
            "imprimir('{:.2g}'.format(0.5))\n",
        ):
            _assert_matches(self, source)

    def test_unknown_spec_fails_closed(self):
        for source in (
            "imprimir('{:q}'.format(1))\n",
            "imprimir('{:.}'.format('a'))\n",
        ):
            try:
                compare_native_to_cpython(source)
            except NativeBuildError:
                pass
            else:
                self.fail(f"not closed: {source}")

    def test_mixing_numbering_fails_closed(self):
        try:
            compare_native_to_cpython("imprimir('{} {0}'.format(1, 2))\n")
        except NativeBuildError:
            pass
        else:
            self.fail("not closed: mixing automatic and manual numbering")


class StrMethodsP23(unittest.TestCase):
    """STR_PREDS_V1 + STR_TRANSFORMS_V1 + STR_PADS_V1: the second str
    methods wave (mirror of the Linux inline helpers on Windows).

    Predicates are the ASCII subset; empty strings are False for all of
    them, and isupper/islower/istitle need at least one cased character
    ('123'.isupper() is False). center follows CPython's
    left = marg//2 + (marg & width & 1) (the extra pad goes left only
    when BOTH marg and width are odd). index/rindex report an uncatchable
    ValueError (exit 1, name on stderr) when the substring is absent —
    method calls carry no try-handler in MIR yet.
    """

    def test_case_transforms(self):
        _assert_matches(self, "imprimir('abc'.capitalize())\n")
        _assert_matches(self, "imprimir('aBc'.capitalize())\n")
        _assert_matches(self, "imprimir('abc def'.title())\n")
        _assert_matches(self, "imprimir('aBc'.swapcase())\n")

    def test_predicates(self):
        _assert_matches(self, "imprimir('abc'.isalpha())\n")
        _assert_matches(self, "imprimir('ab1'.isalpha())\n")
        _assert_matches(self, "imprimir(''.isalpha())\n")
        _assert_matches(self, "imprimir('123'.isdigit())\n")
        _assert_matches(self, "imprimir('12a'.isdigit())\n")
        _assert_matches(self, "imprimir('ab12'.isalnum())\n")
        _assert_matches(self, "imprimir('  '.isspace())\n")
        _assert_matches(self, "imprimir(''.isspace())\n")

    def test_cased_predicates(self):
        _assert_matches(self, "imprimir('Abc'.istitle())\n")
        _assert_matches(self, "imprimir('abc'.istitle())\n")
        _assert_matches(self, "imprimir('ABC'.isupper())\n")
        _assert_matches(self, "imprimir('123'.isupper())\n")
        _assert_matches(self, "imprimir('abc'.islower())\n")

    def test_zfill(self):
        _assert_matches(self, "imprimir('5'.zfill(3))\n")
        _assert_matches(self, "imprimir('-5'.zfill(3))\n")

    def test_just(self):
        _assert_matches(self, "imprimir('ab'.ljust(5))\n")
        _assert_matches(self, "imprimir('ab'.rjust(5))\n")
        _assert_matches(self, "imprimir('ab'.center(6))\n")
        _assert_matches(self, "imprimir('ab'.center(7))\n")
        _assert_matches(self, "imprimir('ab'.center(6, '*'))\n")
        _assert_matches(self, "imprimir('x'.center(4))\n")

    def test_search(self):
        _assert_matches(self, "imprimir('abcabc'.count('bc'))\n")
        _assert_matches(self, "imprimir('abc'.count('z'))\n")
        _assert_matches(self, "imprimir('aaa'.count('aa'))\n")
        _assert_matches(self, "imprimir('abcabc'.count(''))\n")
        _assert_matches(self, "imprimir('abc'.rfind('b'))\n")
        _assert_matches(self, "imprimir('abc'.rfind('z'))\n")
        _assert_matches(self, "imprimir('abc'.index('b'))\n")
        _assert_matches(self, "imprimir('abcabc'.rindex('b'))\n")

    def test_index_missing_raises(self):
        result = compare_native_to_cpython("imprimir('abc'.index('z'))\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"ValueError", result.native.stderr)

    def test_bad_arity_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('a'.zfill())\n")
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('a'.center())\n")


if __name__ == "__main__":
    unittest.main()
