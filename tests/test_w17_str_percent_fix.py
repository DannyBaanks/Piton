"""Differential regressions for runtime mapping %-formatting."""
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


class RuntimeMappingPercentFormat(unittest.TestCase):
    def test_runtime_mapping_zero_padding_and_dynamic_key(self):
        _assert_matches(
            self,
            "d = {}\n"
            "for_k = 'nom'\n"
            "d[for_k] = 'PITON'\n"
            "d['n'] = 3\n"
            "imprimir('%(nom)s v%(n)03d' % d)\n",
        )

    def test_runtime_mapping_flags_precision_conversions_and_percent(self):
        _assert_matches(
            self,
            "mapping = {}\n"
            "key = 'x'\nmapping[key] = 'ab'\n"
            "key = 'f'\nmapping[key] = 1.25\n"
            "key = 'n'\nmapping[key] = 31\n"
            "key = 'r'\nmapping[key] = 'hi'\n"
            "imprimir('[%(x)-5s]|[%(x)5.2s]|%(f)+.2f|%(n)x|%(r)r|%%' % mapping)\n",
        )

    def test_runtime_mapping_ignores_single_length_modifier(self):
        _assert_matches(
            self,
            "mapping = {}\n"
            "key = 'n'\n"
            "mapping[key] = 7\n"
            "imprimir('%(n)ld|%(n)hd|%(n)Ld' % mapping)\n"
        )

    def test_missing_mapping_key_carries_key_argument(self):
        _assert_matches(
            self,
            "d = {}\n"
            "d['a'] = 1\n"
            "intentar:\n"
            "    imprimir('%(b)s' % d)\n"
            "excepto KeyError como e:\n"
            "    imprimir('KE', e)\n"
            "    imprimir('STR', str(e))\n"
            "    imprimir('ARGS', e.args)\n",
        )

    def test_mapping_with_only_escaped_percent_ignores_mapping(self):
        _assert_matches(
            self,
            "mapping = {}\n"
            "imprimir('%%' % mapping)\n",
        )

    def test_unknown_static_mapping_conversion_stays_fail_closed(self):
        with pytest.raises(
            NativeBuildError,
            match=r"str % conversion %q is not supported",
        ):
            compare_native_to_cpython("imprimir('%(x)q' % {'x': 1})\n")

    def test_positional_length_modifier_stays_fail_closed(self):
        with pytest.raises(
            NativeBuildError,
            match="str % length modifiers are not supported",
        ):
            compare_native_to_cpython("imprimir('%ld' % 5)\n")


if __name__ == "__main__":
    unittest.main()
