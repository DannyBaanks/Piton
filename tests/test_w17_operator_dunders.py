"""Differential coverage for runtime floor-division and power dunders."""
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


class OperatorDunderRuntimeDispatch(unittest.TestCase):
    def test_floordiv_direct_and_reflected(self):
        _assert_matches(
            self,
            "clase Directo:\n"
            "    funcion __floordiv__(self, other):\n"
            "        devolver 23\n"
            "clase Reflejo:\n"
            "    funcion __rfloordiv__(self, other):\n"
            "        devolver 29\n"
            "imprimir(Directo() // 2)\n"
            "imprimir(2 // Reflejo())\n",
        )

    def test_floordiv_falls_back_after_notimplemented(self):
        _assert_matches(
            self,
            "clase Izquierda:\n"
            "    funcion __floordiv__(self, other):\n"
            "        devolver NotImplemented\n"
            "clase Derecha:\n"
            "    funcion __rfloordiv__(self, other):\n"
            "        devolver 31\n"
            "imprimir(Izquierda() // Derecha())\n",
        )

    def test_subclass_reflected_override_runs_first(self):
        _assert_matches(
            self,
            "clase Base:\n"
            "    funcion __floordiv__(self, other):\n"
            "        devolver 1\n"
            "    funcion __rfloordiv__(self, other):\n"
            "        devolver 2\n"
            "clase Derivada(Base):\n"
            "    funcion __rfloordiv__(self, other):\n"
            "        devolver 37\n"
            "imprimir(Base() // Derivada())\n",
        )

    def test_pow_direct_reflected_and_notimplemented_fallback(self):
        _assert_matches(
            self,
            "clase Potencia:\n"
            "    funcion __pow__(self, other):\n"
            "        devolver 41\n"
            "clase PotenciaReflejada:\n"
            "    funcion __rpow__(self, other):\n"
            "        devolver 43\n"
            "clase Rechazo:\n"
            "    funcion __pow__(self, other):\n"
            "        devolver NotImplemented\n"
            "clase Acepta:\n"
            "    funcion __rpow__(self, other):\n"
            "        devolver 47\n"
            "imprimir(Potencia() ** 2)\n"
            "imprimir(2 ** PotenciaReflejada())\n"
            "imprimir(Rechazo() ** Acepta())\n",
        )

    def test_pow_three_arguments_passes_modulus_to_pow(self):
        _assert_matches(
            self,
            "clase PotenciaModular:\n"
            "    funcion __pow__(self, other, modulo):\n"
            "        devolver other * modulo + 3\n"
            "imprimir(pow(PotenciaModular(), 4, 7))\n",
        )

    def test_no_dunder_raises_cpython_typeerror_messages(self):
        _assert_matches(
            self,
            "clase SinOperacion:\n"
            "    pasar\n"
            "intentar:\n"
            "    SinOperacion() // 2\n"
            "excepto TypeError como error:\n"
            "    imprimir(error)\n"
            "intentar:\n"
            "    SinOperacion() ** 2\n"
            "excepto TypeError como error:\n"
            "    imprimir(error)\n",
        )

    def test_ternary_pow_does_not_call_reflected_pow(self):
        _assert_matches(
            self,
            "clase PotenciaModular:\n"
            "    funcion __pow__(self, other, modulo):\n"
            "        devolver NotImplemented\n"
            "    funcion __rpow__(self, other, modulo):\n"
            "        devolver 53\n"
            "intentar:\n"
            "    pow(PotenciaModular(), 2, 5)\n"
            "excepto TypeError como error:\n"
            "    imprimir(error)\n",
        )

    def test_unimplemented_modulo_operator_still_fails_closed(self):
        with pytest.raises(NativeBuildError, match="Linux % on a native object without a matching dunder"):
            compare_native_to_cpython(
                "clase SinModulo:\n    pasar\nimprimir(SinModulo() % 2)\n"
            )


if __name__ == "__main__":
    unittest.main()
