"""Differential parity tests for mixed None/scalar/str parameters in isinstance() and `es None` checks."""
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


class TestMixedNoneParamsParity(unittest.TestCase):
    def test_exact_reproducers(self):
        cases = {
            "int_or_none_isinstance": (
                "funcion f(co):\n"
                "    si isinstance(co, int):\n"
                "        devolver co + 1\n"
                "    devolver 'nada'\n"
                "imprimir(f(1), f(None))\n"
            ),
            "setlocale_like": (
                "funcion setloc(cat, valor=None):\n"
                "    si valor es None:\n"
                "        devolver ('consulta', cat)\n"
                "    si isinstance(valor, str):\n"
                "        devolver ('fija', cat, valor)\n"
                "    devolver ('otro', cat)\n"
                "imprimir(setloc(1), setloc(2, 'es'), setloc(3, 7))\n"
            ),
        }
        for name, src in cases.items():
            with self.subTest(case=name):
                _assert_matches(self, src)

    def test_mixed_int_float_bool_str_none_with_es_no_es_none(self):
        _assert_matches(
            self,
            "funcion clasificar(x=None):\n"
            "    si x es None:\n"
            "        devolver 'nulo'\n"
            "    si x no es None y isinstance(x, str):\n"
            "        devolver 'texto:' + x\n"
            "    si isinstance(x, int):\n"
            "        devolver 'entero'\n"
            "    devolver 'otro'\n"
            "imprimir(clasificar(), clasificar(None), clasificar('hola'), clasificar(42), clasificar(3.5))\n",
        )

    def test_mixed_param_with_method_call_fails_closed(self):
        src = (
            "funcion f(x):\n"
            "    si isinstance(x, str):\n"
            "        devolver x.upper()\n"
            "    devolver x\n"
            "imprimir(f('a'), f(1))\n"
        )
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython(src)


if __name__ == "__main__":
    unittest.main()
