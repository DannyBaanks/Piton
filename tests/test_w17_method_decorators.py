"""Differential coverage for native class method decorator application."""
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


class MethodDecoratorParity(unittest.TestCase):
    def test_wrapper_receives_instance_and_original_function(self):
        _assert_matches(
            self,
            "funcion doble(fn):\n"
            "    funcion envoltura(este, val):\n"
            "        devolver fn(este, val) * 2\n"
            "    devolver envoltura\n"
            "clase Handler:\n"
            "    @doble\n"
            "    funcion process(este, val):\n"
            "        devolver val + 10\n"
            "imprimir(Handler().process(5))\n",
        )

    def test_parameterized_stacked_decorators_run_at_class_definition(self):
        _assert_matches(
            self,
            "funcion escalar(etiqueta, factor):\n"
            "    imprimir(\"factory\", etiqueta)\n"
            "    funcion decorar(fn):\n"
            "        imprimir(factor)\n"
            "        funcion envoltura(este, valor):\n"
            "            devolver fn(este, valor) * factor + factor\n"
            "        devolver envoltura\n"
            "    devolver decorar\n"
            "clase Handler:\n"
            "    @escalar(\"outer\", 3)\n"
            "    @escalar(\"inner\", 2)\n"
            "    funcion process(este, valor):\n"
            "        devolver valor + 1\n"
            "imprimir(Handler().process(4))\n",
        )

    def test_method_decorator_on_typed_instance_name(self):
        _assert_matches(
            self,
            "funcion con_prefijo(prefijo):\n"
            "    funcion decorar(fn):\n"
            "        funcion envoltura(este, valor):\n"
            "            devolver prefijo + fn(este, valor)\n"
            "        devolver envoltura\n"
            "    devolver decorar\n"
            "clase Handler:\n"
            "    @con_prefijo(\"item:\")\n"
            "    funcion process(este, valor):\n"
            "        devolver texto(valor + 1)\n"
            "handler = Handler()\n"
            "imprimir(handler.process(8))\n",
        )

    def test_functools_wraps_preserves_method_name_and_doc(self):
        _assert_matches(
            self,
            "importar functools\n"
            "funcion doble(fn):\n"
            "    @functools.wraps(fn)\n"
            "    funcion envoltura(este, valor):\n"
            "        devolver fn(este, valor) * 2\n"
            "    devolver envoltura\n"
            "clase Handler:\n"
            "    @doble\n"
            "    funcion process(este, valor):\n"
            "        \"\"\"method documentation\"\"\"\n"
            "        devolver valor + 10\n"
            "handler = Handler()\n"
            "imprimir(handler.process(5))\n"
            "imprimir(handler.process.__name__, handler.process.__doc__)\n"
            "imprimir(Handler.process.__name__)\n",
        )

    def test_decorated_repr_keeps_failing_closed(self):
        source = (
            "funcion prefijo(fn):\n"
            "    funcion envoltura(este):\n"
            "        devolver \"wrapped:\" + fn(este)\n"
            "    devolver envoltura\n"
            "clase Item:\n"
            "    @prefijo\n"
            "    funcion __repr__(este):\n"
            "        devolver \"item\"\n"
            ""
        )
        with pytest.raises(
            NativeBuildError,
            match="decorated special method '__repr__' is not supported by runtime dispatch",
        ):
            compare_native_to_cpython(source)

    def test_unknown_receiver_keeps_failing_closed(self):
        source = (
            "funcion doble(fn):\n"
            "    funcion envoltura(este, valor):\n"
            "        devolver fn(este, valor) * 2\n"
            "    devolver envoltura\n"
            "clase Handler:\n"
            "    @doble\n"
            "    funcion process(este, valor):\n"
            "        devolver valor + 10\n"
            "funcion invocar(obj):\n"
            "    devolver obj.process(5)\n"
            ""
        )
        with pytest.raises(
            NativeBuildError,
            match="decorated method 'process' requires a statically known receiver class",
        ):
            compare_native_to_cpython(source)

    def test_cached_property_keeps_failing_closed(self):
        source = (
            "importar functools\n"
            "clase Handler:\n"
            "    @functools.cached_property\n"
            "    funcion token(este):\n"
            "        devolver 7\n"
            ""
        )
        with pytest.raises(
            NativeBuildError,
            match="cached descriptor storage is unavailable",
        ):
            compare_native_to_cpython(source)

    def test_non_callable_decorator_result_keeps_failing_closed(self):
        source = (
            "funcion no_callable(fn):\n"
            "    devolver 0\n"
            "clase Handler:\n"
            "    @no_callable\n"
            "    funcion process(este, valor):\n"
            "        devolver valor\n"
            ""
        )
        with pytest.raises(
            NativeBuildError,
            match="native class method decorators currently support only",
        ):
            compare_native_to_cpython(source)


if __name__ == "__main__":
    unittest.main()
