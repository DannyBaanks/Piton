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

    def test_strip_with_chars_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('xx'.strip('x'))\n")


class JoinV1(unittest.TestCase):
    @unittest.skipIf(
        sys.platform.startswith("win32"),
        "Windows backend: literal str elements are untagged, so the join "
        "type check raises TypeError at runtime (asserted in the Windows gate below)",
    )
    def test_join_list(self):
        _assert_matches(self, "imprimir(','.join(['a', 'b']), '-'.join([]))\n")

    @unittest.skipIf(
        sys.platform.startswith("win32"),
        "Windows backend: literal str elements are untagged (TypeError gate below)",
    )
    def test_join_tuple(self):
        _assert_matches(self, "imprimir('+'.join(('a', 'b')))\n")

    @unittest.skipIf(
        not sys.platform.startswith("win32"),
        "Linux backend prints the joined string; only the Windows untagged "
        "elements raise here",
    )
    def test_join_literal_raises_on_windows(self):
        result = compare_native_to_cpython("imprimir(','.join(['a', 'b']))\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError", result.native.stderr)

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

    def test_format_bad_field_fails_at_runtime(self):
        result = compare_native_to_cpython("imprimir('{x}'.format(1))\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"ValueError", result.native.stderr)

    def test_format_bad_index_fails_at_runtime(self):
        result = compare_native_to_cpython("imprimir('{5}'.format(1))\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"IndexError", result.native.stderr)

    def test_unknown_method_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir('a'.title())\n")


class StrMethodsChainedV1(unittest.TestCase):
    def test_chained_str_calls(self):
        _assert_matches(self, "imprimir('  a,b '.strip().upper().replace(',', ';'))\n")


if __name__ == "__main__":
    unittest.main()
