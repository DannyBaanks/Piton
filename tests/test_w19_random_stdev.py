"""W19 random_stdev: AttributeError de atributos de modulo ausentes.

CPython 3.12: `random.stdev` y `math.no_existe` lanzan AttributeError con el
mensaje "module 'M' has no attribute 'a'". Antes, el nativo emitia un NameError
del simbolo mangled (random__stdev) o un AttributeError con solo el nombre.
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


class RandomStdevMissingAttribute(unittest.TestCase):
    def test_bundled_module_missing_call_is_catchable_attribute_error(self):
        _assert_matches(
            self,
            "importar random\n"
            "intentar:\n"
            "    random.stdev([1, 2, 3])\n"
            "excepto AttributeError como e:\n"
            "    imprimir('AE', e)\n",
        )

    def test_native_module_missing_attribute_message_matches_cpython(self):
        _assert_matches(
            self,
            "importar math\n"
            "intentar:\n"
            "    math.no_existe\n"
            "excepto AttributeError como e:\n"
            "    imprimir('AE', e)\n",
        )

    def test_missing_attribute_in_value_position_and_alias_call(self):
        _assert_matches(
            self,
            "importar random\n"
            "intentar:\n"
            "    f = random.stdev\n"
            "excepto AttributeError como e:\n"
            "    imprimir('V', e)\n"
            "importar random como r\n"
            "intentar:\n"
            "    r.stdev([1])\n"
            "excepto AttributeError como e:\n"
            "    imprimir('A', e)\n",
        )

    def test_missing_attribute_inside_function_is_catchable(self):
        _assert_matches(
            self,
            "importar random\n"
            "importar math\n"
            "funcion f():\n"
            "    intentar:\n"
            "        random.stdev(2)\n"
            "    excepto AttributeError como e:\n"
            "        imprimir('F', e)\n"
            "funcion g():\n"
            "    devolver math.no_existe\n"
            "intentar:\n"
            "    g()\n"
            "excepto AttributeError como e:\n"
            "    imprimir('G', e)\n"
            "f()\n",
        )

    def test_uncaught_missing_attribute_keeps_stdout_and_exit_code(self):
        # Edge: el error sin intentar debe parar tras lo ya impreso, rc distinto de 0.
        _assert_matches(
            self,
            "importar math\n"
            "imprimir('antes')\n"
            "math.no_existe\n"
            "imprimir('no llega')\n",
        )

    def test_arguments_are_not_evaluated_when_attribute_is_missing(self):
        # CPython resuelve random.stdev antes de evaluar los argumentos.
        _assert_matches(
            self,
            "importar random\n"
            "funcion h():\n"
            "    imprimir('args evaluados')\n"
            "    devolver 1\n"
            "intentar:\n"
            "    random.stdev(h())\n"
            "excepto AttributeError como e:\n"
            "    imprimir('AE', e)\n",
        )

    def test_existing_module_attributes_still_work(self):
        _assert_matches(
            self,
            "importar random\n"
            "importar math\n"
            "random.seed(7)\n"
            "imprimir(random.random() < 1.0)\n"
            "imprimir(random.choice([5]))\n"
            "imprimir(math.pi)\n",
        )

    def test_aliased_native_module_value_access_still_fails_closed(self):
        # Edge fail-closed preexistente: `math como m` en posicion de valor
        # no se baja (no se acepta, no se inventa un valor).
        with pytest.raises(NativeBuildError, match="native module attribute value access is not supported"):
            compare_native_to_cpython(
                "importar math como m\n"
                "intentar:\n"
                "    m.no_existe\n"
                "excepto AttributeError como e:\n"
                "    imprimir('AE', e)\n"
            )


if __name__ == "__main__":
    unittest.main()
