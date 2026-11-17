"""Differential coverage for runtime %-format templates and operands."""
from __future__ import annotations

import unittest

from piton.native_differential import compare_native_to_cpython


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


def _assert_same_exception(testcase: unittest.TestCase, source: str, summary: bytes) -> None:
    result = compare_native_to_cpython(source)
    testcase.assertEqual(result.native.returncode, 1)
    testcase.assertEqual(result.oracle.returncode, 1)
    testcase.assertEqual(result.native.stderr.splitlines()[-1], summary)
    testcase.assertEqual(result.oracle.stderr.splitlines()[-1], summary)


class RuntimePercentFormat(unittest.TestCase):
    def test_dynamic_width_precision_and_negative_width(self):
        _assert_matches(
            self,
            "width = 5\n"
            "string_width = 4\n"
            "precision = 2\n"
            "negative_width = -5\n"
            "negative_precision = -1\n"
            "print('%*d|%-*s|%.*f' % (width, 7, string_width, 'x', precision, 1.25))\n"
            "print('%*d|%.*f' % (negative_width, 7, negative_precision, 1.25))\n",
        )

    def test_dynamic_mapping_key(self):
        _assert_matches(
            self,
            "key = 'name'\n"
            "mapping = {}\n"
            "mapping[key] = 'PITON'\n"
            "print('%(name)s' % mapping)\n",
        )

    def test_dynamic_template_with_runtime_tuple_arity(self):
        _assert_matches(
            self,
            "template = str('%s=%d')\n"
            "values = tuple(['item', 4])\n"
            "print(template % values)\n",
        )

    def test_dynamic_width_type_error_matches_cpython(self):
        _assert_same_exception(
            self,
            "print('%*d' % ('wide', 7))\n",
            b"TypeError: * wants int",
        )

    def test_dynamic_mapping_missing_key_matches_cpython(self):
        _assert_same_exception(
            self,
            "mapping = {}\n"
            "print('%(missing)s' % mapping)\n",
            b"KeyError: 'missing'",
        )

    def test_dynamic_template_argument_count_error_matches_cpython(self):
        _assert_same_exception(
            self,
            "template = str('%s %s')\n"
            "values = tuple(['only one'])\n"
            "print(template % values)\n",
            b"TypeError: not enough arguments for format string",
        )

    def test_dynamic_template_unsupported_conversion_raises_value_error(self):
        _assert_same_exception(
            self,
            "template = str('%q')\n"
            "print(template % 1)\n",
            b"ValueError: unsupported format character 'q' (0x71) at index 1",
        )


if __name__ == "__main__":
    unittest.main()
