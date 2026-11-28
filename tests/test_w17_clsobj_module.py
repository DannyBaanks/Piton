"""Parity checks for runtime module class objects in the native backend."""
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


class ModuleClassObjectParity(unittest.TestCase):
    def test_type_of_module_has_cpython_name_and_repr(self):
        _assert_matches(
            self,
            "importar sys\n"
            "imprimir(type(sys).__name__)\n"
            "imprimir(type(sys))\n",
        )

    def test_module_type_identity_and_isinstance(self):
        _assert_matches(
            self,
            "importar sys\n"
            "importar types\n"
            "imprimir(type(sys) es types.ModuleType)\n"
            "imprimir(isinstance(sys, types.ModuleType))\n"
            "imprimir(isinstance(1, types.ModuleType))\n",
        )

    def test_three_argument_type_call_matches_cpython(self):
        _assert_matches(
            self,
            "Dynamic = type('Dynamic', (), {'x': 7})\n"
            "imprimir(Dynamic.__name__, Dynamic.__qualname__, Dynamic.x)\n",
        )


if __name__ == "__main__":
    unittest.main()
