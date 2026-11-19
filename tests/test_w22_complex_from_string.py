"""Differential parity for `complex()` built from a string (wave 22).

Covers:
  * `complex(s)` where `s` is a string variable (not only a literal).
  * Valid string forms: `complex("1+2j")`, `complex("3")`, `complex("2.5j")`,
    `complex("-3.5j")`, surrounding whitespace, pure-imaginary forms.
  * Malformed strings raise a *catchable* `ValueError` via `piton_raise_set`
    inside `intentar / excepto ValueError:` (was `exit 1` before the fix).
  * An uncaught `complex(...)` error still terminates with exit code 1, like
    CPython, both at module level and propagating out of a function.
  * Numeric forms and a single complex argument keep working.
  * `complex(1+2j, 3)` fails closed at build time (CPython TypeError).
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


class ComplexFromString(unittest.TestCase):
    def test_complex_string_variable_valid(self):
        _assert_matches(
            self,
            "funcion parse_c(s):\n"
            "    devolver complex(s)\n"
            "imprimir(parse_c(\"1+2j\"), parse_c(\"-3.5j\"), parse_c(\"3\"))\n",
        )

    def test_complex_string_forms_valid(self):
        _assert_matches(
            self,
            "imprimir(complex(\"1+2j\"), complex(\"3\"), complex(\"2.5j\"), "
            "complex(\"-3.5j\"), complex(\"  1+2j  \"), complex(\"1j\"), "
            "complex(\"-j\"), complex(\"0\"))\n",
        )

    def test_complex_malformed_string_caught(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    complex(\"nope\")\n"
            "excepto ValueError:\n"
            "    imprimir(\"nope_val\")\n"
            "intentar:\n"
            "    complex(\"1 + 2j\")\n"
            "excepto ValueError:\n"
            "    imprimir(\"internal_spaces_val\")\n"
            "intentar:\n"
            "    complex(\"\")\n"
            "excepto ValueError:\n"
            "    imprimir(\"empty_val\")\n",
        )

    def test_complex_uncaught_exits_one_like_cpython(self):
        _assert_matches(self, "complex(\"nope\")\n")
        _assert_matches(
            self,
            "funcion g():\n"
            "    complex(\"nope\")\n"
            "g()\n",
        )

    def test_complex_value_propagates_through_function_and_is_caught(self):
        _assert_matches(
            self,
            "funcion interior():\n"
            "    complex(\"nope\")\n"
            "intentar:\n"
            "    interior()\n"
            "excepto ValueError:\n"
            "    imprimir(\"caught_nested\")\n",
        )

    def test_complex_numeric_and_single_complex(self):
        _assert_matches(
            self,
            "imprimir(complex(), complex(1), complex(1.5), complex(1, 2), "
            "complex(1.5, 2.5), complex(1+2j))\n",
        )

    def test_complex_too_many_args_is_catchable_typeerror(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    complex(1, 2, 3)\n"
            "excepto TypeError:\n"
            "    imprimir(\"three_arg_typeerror\")\n",
        )

    def test_complex_complex_and_second_arg_fails_closed(self):
        with pytest.raises(NativeBuildError, match="complex\\(complex, imag\\)"):
            compare_native_to_cpython("print(complex(1+2j, 3))\n")


if __name__ == "__main__":
    unittest.main()
