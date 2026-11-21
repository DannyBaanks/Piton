"""W23 parity: ntpath str.startswith() with multiple/dynamic arguments and
sysconfig str.format(**mapping) on a dynamic slot receiver.

ntpath uses `s.startswith(sep)` / `s.startswith(colon_sep, 1)` / tuple prefixes
on str values that arrive as runtime-typed slots (from dicts / parameters).
sysconfig `_subst_vars` calls `s.format(**local_vars)` where `s` is a str
variable (a slot) and `local_vars` is a dict resolved at runtime.

Both targets previously failed at BUILD time:
  ntpath:  NativeBuildError: Linux dynamic str.startswith() currently requires exactly one argument
  sysconfig: NativeBuildError: Linux str.format(**mapping) requires a str receiver, not slot
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


class NtpathStartswithDynamicV1(unittest.TestCase):
    """str.startswith() on a dynamic slot receiver with prefix + start offset
    and tuple of prefixes — the shapes used by ntpath.isabs / expandvars /
    realpath on Linux."""

    def test_startswith_single_prefix(self):
        _assert_matches(
            self,
            "s = '/usr/bin/python'\n"
            "imprimir(s.startswith('/usr'))\n",
        )

    def test_startswith_prefix_and_start(self):
        # ntpath.isabs: s.startswith(sep) or s.startswith(colon_sep, 1)
        _assert_matches(
            self,
            "s = 'C:/Windows'\n"
            "imprimir(s.startswith('C:', 1))\n",
        )

    def test_startswith_tuple_of_prefixes(self):
        _assert_matches(
            self,
            "s = '/usr/bin'\n"
            "imprimir(s.startswith(('/usr', '/opt')))\n",
        )

    def test_startswith_tuple_and_start(self):
        _assert_matches(
            self,
            "s = 'x/usr/bin'\n"
            "imprimir(s.startswith(('/usr', '/opt'), 1))\n",
        )

    def test_startswith_tuple_no_match(self):
        _assert_matches(
            self,
            "s = '/home/user'\n"
            "imprimir(s.startswith(('/usr', '/opt')))\n",
        )

    def test_startswith_start_out_of_range(self):
        _assert_matches(
            self,
            "s = 'abc'\n"
            "imprimir(s.startswith('a', 5))\n",
        )

    def test_startswith_int_prefix_raises_typeerror(self):
        result = compare_native_to_cpython(
            "s = 'abc'\n"
            "imprimir(s.startswith(5))\n",
        )
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError", result.native.stderr)

    def test_startswith_tuple_with_non_str_raises_typeerror(self):
        result = compare_native_to_cpython(
            "s = 'abc'\n"
            "imprimir(s.startswith(('z', 5)))\n",
        )
        self.assertEqual(result.oracle.returncode, 1)
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError", result.native.stderr)


class SysconfigFormatMappingV1(unittest.TestCase):
    """str.format(**mapping) where the receiver is a dynamic slot (a str
    variable) and the mapping is resolved at runtime — sysconfig._subst_vars."""

    def test_format_mapping_on_variable_receiver(self):
        _assert_matches(
            self,
            "d = {'srcdir': '/src', 'projectbase': '/base'}\n"
            "s = '{srcdir}/Include {projectbase}/.'\n"
            "imprimir(s.format(**d))\n",
        )

    def test_format_mapping_literal_receiver(self):
        _assert_matches(
            self,
            "d = {'a': 7}\n"
            "imprimir('x={a}'.format(**d))\n",
        )

    def test_format_mapping_missing_key_is_keyerror(self):
        result = compare_native_to_cpython(
            "d = {'n': 1}\n"
            "imprimir('{zz}'.format(**d))\n",
        )
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"KeyError", result.native.stderr)

    def test_format_mapping_missing_key_is_catchable(self):
        _assert_matches(
            self,
            "d = {'n': 1}\n"
            "intentar:\n"
            "    imprimir('{zz}'.format(**d))\n"
            "excepto KeyError:\n"
            "    imprimir('capturado')\n",
        )

    def test_format_mapping_with_d_spec(self):
        _assert_matches(
            self,
            "d = {'n': 42, 's': 'hi'}\n"
            "imprimir('{s}={n:d}'.format(**d))\n",
        )

    def test_format_mapping_unsupported_spec_raises_valueerror(self):
        result = compare_native_to_cpython(
            "d = {'n': 42}\n"
            "imprimir('{n:x}'.format(**d))\n",
        )
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"ValueError", result.native.stderr)


if __name__ == "__main__":
    unittest.main()
