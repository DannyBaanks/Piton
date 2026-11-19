"""Regression coverage for dunder argument binding in native operators."""
from __future__ import annotations

import unittest

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.mir import MIRLoweringError
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


class OperatorDunderArgumentBinding(unittest.TestCase):
    def test_omitted_optional_parameters_use_their_declared_defaults(self):
        _assert_matches(
            self,
            "clase DivisionDirecta:\n"
            "    funcion __floordiv__(self, other, marcador=41):\n"
            "        devolver marcador\n"
            "clase DivisionReflejada:\n"
            "    funcion __rfloordiv__(self, other, marcador=43):\n"
            "        devolver marcador\n"
            "clase PotenciaDirecta:\n"
            "    funcion __pow__(self, exponente, modulo=None):\n"
            "        devolver (exponente, modulo)\n"
            "clase PotenciaReflejada:\n"
            "    funcion __rpow__(self, exponente, modulo=47):\n"
            "        devolver (exponente, modulo)\n"
            "imprimir(DivisionDirecta() // 5)\n"
            "imprimir(5 // DivisionReflejada())\n"
            "imprimir(PotenciaDirecta() ** 2)\n"
            "imprimir(pow(PotenciaDirecta(), 3))\n"
            "imprimir(pow(PotenciaDirecta(), 3, 7))\n"
            "imprimir(5 ** PotenciaReflejada())\n",
        )

    def test_varargs_keyword_only_defaults_and_kwargs_are_bound(self):
        _assert_matches(
            self,
            "clase PotenciaFlexible:\n"
            "    funcion __pow__(self, exponente, *extras, marcador=73, **opciones):\n"
            "        devolver (extras, marcador, opciones)\n"
            "imprimir(PotenciaFlexible() ** 2)\n"
            "imprimir(pow(PotenciaFlexible(), 3, 7))\n",
        )

    def test_required_and_extra_arguments_raise_cpython_typeerrors(self):
        _assert_matches(
            self,
            "clase PotenciaRequiereModulo:\n"
            "    funcion __pow__(self, exponente, modulo):\n"
            "        devolver modulo\n"
            "clase PotenciaSoloExponente:\n"
            "    funcion __pow__(self, exponente):\n"
            "        devolver exponente\n"
            "clase PotenciaRequiereKeyword:\n"
            "    funcion __pow__(self, exponente, *, modulo):\n"
            "        devolver modulo\n"
            "clase DivisionRequiereExtra:\n"
            "    funcion __floordiv__(self, other, requerido):\n"
            "        devolver requerido\n"
            "intentar:\n"
            "    PotenciaRequiereModulo() ** 2\n"
            "excepto TypeError como error:\n"
            "    imprimir(error)\n"
            "intentar:\n"
            "    pow(PotenciaSoloExponente(), 3, 7)\n"
            "excepto TypeError como error:\n"
            "    imprimir(error)\n"
            "intentar:\n"
            "    PotenciaRequiereKeyword() ** 2\n"
            "excepto TypeError como error:\n"
            "    imprimir(error)\n"
            "intentar:\n"
            "    DivisionRequiereExtra() // 2\n"
            "excepto TypeError como error:\n"
            "    imprimir(error)\n",
        )

    def test_unsupported_modulo_operator_still_fails_closed(self):
        with pytest.raises(
            NativeBuildError,
            match="Linux % on a native object without a matching dunder",
        ):
            compare_native_to_cpython(
                "clase SinModulo:\n    pasar\nimprimir(SinModulo() % 2)\n"
            )

    def test_dynamic_operator_dunder_defaults_fail_closed(self):
        source = (
            "funcion crear_marca():\n"
            "    devolver 89\n"
            "clase DefaultDinamico:\n"
            "    funcion __pow__(self, exponente, marca=crear_marca()):\n"
            "        devolver marca\n"
            "imprimir(DefaultDinamico() ** 2)\n"
        )
        with pytest.raises(
            (NativeBuildError, MIRLoweringError),
            match="native __pow__ default for 'marca' must be a static value",
        ):
            compare_native_to_cpython(source)

        named_source = (
            "marca_global = 97\n"
            "clase DefaultConNombre:\n"
            "    funcion __pow__(self, exponente, marca=marca_global):\n"
            "        devolver marca\n"
            "imprimir(DefaultConNombre() ** 2)\n"
        )
        with pytest.raises(
            NativeBuildError,
            match="references a name and cannot be captured at definition time",
        ):
            compare_native_to_cpython(named_source)


if __name__ == "__main__":
    unittest.main()
