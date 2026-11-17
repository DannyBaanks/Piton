"""Runtime dispatch for %-format conversions over tagged PitonSlot values."""
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


class PercentDynamicNumericSlot(unittest.TestCase):
    def test_decimal_integer_specs_truncate_dynamic_floats(self):
        _assert_matches(
            self,
            "importar json\n"
            "imprimir('%d %i %u %d' % (json.loads('3.7'), json.loads('-3.7'), "
            "json.loads('true'), json.loads('3.7')))\n"
            "imprimir('%+06d %.4i' % (json.loads('-3.7'), json.loads('7')))\n",
        )

    def test_integer_radix_specs_dispatch_from_slot_kind(self):
        _assert_matches(
            self,
            "importar json\n"
            "imprimir('%i %u %x %X %o %#x' % "
            "(json.loads('255'), json.loads('255'), json.loads('255'), "
            "json.loads('255'), json.loads('255'), json.loads('255')))\n"
            "imprimir('%#x %#o %#08x' % "
            "(json.loads('-255'), json.loads('-255'), json.loads('-255')))\n",
        )

    def test_float_specs_use_runtime_float_and_round_half_even(self):
        _assert_matches(
            self,
            "importar json\n"
            "imprimir('%.0f %.0f %f %.2e %.4g %#g %+010.2f %.1f %.1e %.1g %F %.2E %.4G' % "
            "(json.loads('2.5'), json.loads('3.5'), json.loads('1.25'), "
            "json.loads('1.25'), json.loads('1.25'), json.loads('1.25'), "
            "json.loads('1.25'), json.loads('2'), json.loads('true'), "
            "json.loads('true'), json.loads('1.25'), json.loads('1.25'), "
            "json.loads('1.25')))\n",
        )

    def test_dynamic_char_spec_accepts_integer_and_single_character_string(self):
        _assert_matches(
            self,
            "importar json\n"
            "imprimir('%c%c' % (json.loads('65'), json.loads(\"\\\"z\\\"\")))\n",
        )

    def test_dynamic_numeric_type_errors_match_cpython(self):
        cases = (
            ("importar json\nimprimir('%d' % json.loads(\"\\\"x\\\"\"))\n", b"TypeError", b"%d format: a real number is required, not str"),
            ("importar json\nimprimir('%i' % json.loads('null'))\n", b"TypeError", b"%i format: a real number is required, not NoneType"),
            ("importar json\nimprimir('%x' % json.loads('1.5'))\n", b"TypeError", b"%x format: an integer is required, not float"),
            ("importar json\nimprimir('%f' % json.loads(\"\\\"x\\\"\"))\n", b"TypeError", b"must be real number, not str"),
            ("importar json\nimprimir('%d' % json.loads('[1]'))\n", b"TypeError", b"%d format: a real number is required, not list"),
            ("importar json\nimprimir('%c' % json.loads(\"\\\"xy\\\"\"))\n", b"TypeError", b"%c requires int or char"),
        )
        for source, error_type, message in cases:
            with self.subTest(message=message):
                result = compare_native_to_cpython(source)
                self.assertNotEqual(result.native.returncode, 0)
                self.assertEqual(result.native.returncode, result.oracle.returncode)
                self.assertIn(error_type, result.native.stderr)
                self.assertIn(error_type, result.oracle.stderr)
                self.assertIn(message, result.native.stderr)
                self.assertIn(message, result.oracle.stderr)

    def test_integer_spec_non_finite_float_errors_match_cpython(self):
        cases = (
            ("NaN", b"ValueError", b"cannot convert float NaN to integer"),
            ("Infinity", b"OverflowError", b"cannot convert float infinity to integer"),
            ("-Infinity", b"OverflowError", b"cannot convert float infinity to integer"),
        )
        for value, error_type, message in cases:
            with self.subTest(value=value):
                result = compare_native_to_cpython(
                    "importar json\nimprimir('%d' % json.loads('" + value + "'))\n"
                )
                self.assertNotEqual(result.native.returncode, 0)
                self.assertEqual(result.native.returncode, result.oracle.returncode)
                self.assertIn(error_type, result.native.stderr)
                self.assertIn(error_type, result.oracle.stderr)
                self.assertIn(message, result.native.stderr)
                self.assertIn(message, result.oracle.stderr)

    def test_bigints_from_dynamic_mapping_lookup_keep_integer_semantics(self):
        _assert_matches(
            self,
            "importar json\n"
            "values = {'p': 2 ** 100, 'n': -(2 ** 100)}\n"
            "funcion get_value(d, k):\n"
            "    devolver d[k]\n"
            "p = get_value(values, json.loads('\\\"p\\\"'))\n"
            "n = get_value(values, json.loads('\\\"n\\\"'))\n"
            "imprimir('%d %#x %#o %.1e' % (p, p, p, p))\n"
            "imprimir('%d %#x %#o %.1e' % (n, n, n, n))\n",
        )

    def test_static_string_for_decimal_integer_still_fails_closed(self):
        with pytest.raises(NativeBuildError, match="Linux %d requires a number, not str"):
            compare_native_to_cpython("imprimir('%d' % 'x')\n")

if __name__ == "__main__":
    unittest.main()
