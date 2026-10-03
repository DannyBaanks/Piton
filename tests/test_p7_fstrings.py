"""FSTRINGS_V1: f'...' with plain expressions.

The probe case `n = 3; imprimir(f'n={n}')` died in lowering
(`runtime_call "unsupported"` — the CST/HIR nodes existed but neither the
parser-adjacent lowering nor MIR handled them). Now: f-strings with plain
expressions lower in lower.py straight to `'<template>'.format(arg, ...)`
— reusing STR_METHODS_V1 in both backends with zero mir/backend work.
Conversions (!r !s !a) and format specs fail closed (LoweringError,
wrapped to NativeBuildError at the compile entry points).
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


class FStringsV1(unittest.TestCase):
    def test_basic(self):
        _assert_matches(self, "n = 3\nimprimir(f'n={n}')\n")

    def test_expressions(self):
        _assert_matches(self, "imprimir(f'{1 + 2} {2 * 3}')\n")

    def test_mixed_types(self):
        _assert_matches(self, "x = 'a'\nimprimir(f'{x} {1} {2.5} {Verdadero} {Nada}')\n")

    def test_nested(self):
        _assert_matches(self, "x = 7\nimprimir(f'v={f\"[{x}]\"}')\n")

    def test_escaped_braces(self):
        _assert_matches(self, "imprimir(f'{{}}')\n")

    def test_empty(self):
        _assert_matches(self, "imprimir(f'')\n")

    def test_method_call_inside(self):
        _assert_matches(self, "imprimir(f\"{'a'.upper()}\")\n")

    def test_conversion_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("x = 1\nimprimir(f'{x!r}')\n")

    def test_format_spec(self):
        # P36: f-string format_spec lowers to the .format() placeholder.
        _assert_matches(self, "x = 1.5\nimprimir(f'{x:.2f}')\n")
        _assert_matches(self, "imprimir(f'{42:5d}')\n")


if __name__ == "__main__":
    unittest.main()
