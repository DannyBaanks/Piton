"""Differential parity coverage for native object binary dunders (+, -, %)."""
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


class NativeObjectBinaryDundersParity(unittest.TestCase):
    def test_binary_add_direct_and_reflected(self):
        _assert_matches(
            self,
            "clase Number:\n"
            "    funcion __init__(self, v):\n"
            "        self.v = v\n"
            "    funcion __add__(self, other):\n"
            "        devolver Number(self.v + other)\n"
            "    funcion __radd__(self, other):\n"
            "        devolver Number(other + self.v * 10)\n"
            "a = Number(5)\n"
            "b = Number(3)\n"
            "imprimir((a + 10).v)\n"
            "imprimir((10 + a).v)\n",
        )

    def test_binary_sub_direct_and_reflected(self):
        _assert_matches(
            self,
            "clase Subber:\n"
            "    funcion __init__(self, v):\n"
            "        self.v = v\n"
            "    funcion __sub__(self, other):\n"
            "        devolver Subber(self.v - other)\n"
            "    funcion __rsub__(self, other):\n"
            "        devolver Subber(other - self.v)\n"
            "a = Subber(20)\n"
            "imprimir((a - 5).v)\n"
            "imprimir((50 - a).v)\n",
        )

    def test_binary_mod_direct_and_reflected(self):
        _assert_matches(
            self,
            "clase Modder:\n"
            "    funcion __init__(self, v):\n"
            "        self.v = v\n"
            "    funcion __mod__(self, other):\n"
            "        devolver Modder(self.v % other)\n"
            "    funcion __rmod__(self, other):\n"
            "        devolver Modder(other % self.v)\n"
            "a = Modder(7)\n"
            "imprimir((a % 4).v)\n"
            "imprimir((23 % a).v)\n",
        )

    def test_binop_falls_back_after_notimplemented(self):
        _assert_matches(
            self,
            "clase Izq:\n"
            "    funcion __add__(self, other):\n"
            "        devolver NotImplemented\n"
            "    funcion __sub__(self, other):\n"
            "        devolver NotImplemented\n"
            "    funcion __mod__(self, other):\n"
            "        devolver NotImplemented\n"
            "clase Der:\n"
            "    funcion __radd__(self, other):\n"
            "        devolver 101\n"
            "    funcion __rsub__(self, other):\n"
            "        devolver 102\n"
            "    funcion __rmod__(self, other):\n"
            "        devolver 103\n"
            "a = Izq()\n"
            "b = Der()\n"
            "imprimir(a + b)\n"
            "imprimir(a - b)\n"
            "imprimir(a % b)\n",
        )

    def test_subclass_reflected_override_runs_first(self):
        _assert_matches(
            self,
            "clase Base:\n"
            "    funcion __add__(self, other):\n"
            "        devolver 1\n"
            "    funcion __radd__(self, other):\n"
            "        devolver 2\n"
            "    funcion __sub__(self, other):\n"
            "        devolver 3\n"
            "    funcion __rsub__(self, other):\n"
            "        devolver 4\n"
            "    funcion __mod__(self, other):\n"
            "        devolver 5\n"
            "    funcion __rmod__(self, other):\n"
            "        devolver 6\n"
            "clase Sub(Base):\n"
            "    funcion __radd__(self, other):\n"
            "        devolver 20\n"
            "    funcion __rsub__(self, other):\n"
            "        devolver 40\n"
            "    funcion __rmod__(self, other):\n"
            "        devolver 60\n"
            "b = Base()\n"
            "s = Sub()\n"
            "imprimir(b + s)\n"
            "imprimir(b - s)\n"
            "imprimir(b % s)\n",
        )

    def test_missing_dunders_raise_runtime_typeerror(self):
        _assert_matches(
            self,
            "clase NoDunder:\n"
            "    pasar\n"
            "intentar:\n"
            "    NoDunder() + 2\n"
            "excepto TypeError como e:\n"
            "    imprimir(e)\n"
            "intentar:\n"
            "    3 - NoDunder()\n"
            "excepto TypeError como e:\n"
            "    imprimir(e)\n"
            "intentar:\n"
            "    NoDunder() + NoDunder()\n"
            "excepto TypeError como e:\n"
            "    imprimir(e)\n",
        )

    def test_dynamic_slot_binary_operations(self):
        _assert_matches(
            self,
            "clase OpObj:\n"
            "    funcion __init__(self, x):\n"
            "        self.x = x\n"
            "    funcion __add__(self, other):\n"
            "        devolver self.x + other\n"
            "    funcion __sub__(self, other):\n"
            "        devolver self.x - other\n"
            "    funcion __mod__(self, other):\n"
            "        devolver self.x % other\n"
            "funcion calc(cond, val):\n"
            "    x = None\n"
            "    si cond:\n"
            "        x = OpObj(50)\n"
            "    sino:\n"
            "        x = OpObj(10)\n"
            "    devolver (x + val, x - val, x % val)\n"
            "imprimir(calc(Verdadero, 7))\n"
            "imprimir(calc(Falso, 7))\n",
        )

    def test_dynamic_slot_none_raises_typeerror(self):
        _assert_matches(
            self,
            "clase Box:\n"
            "    funcion __init__(self, x):\n"
            "        self.x = x\n"
            "    funcion __add__(self, other):\n"
            "        devolver self.x + other\n"
            "funcion run(cond, val):\n"
            "    x = None\n"
            "    si cond:\n"
            "        x = Box(50)\n"
            "    intentar:\n"
            "        devolver x + val\n"
            "    excepto TypeError como e:\n"
            "        devolver str(e)\n"
            "imprimir(run(Verdadero, 5))\n"
            "imprimir(run(Falso, 5))\n",
        )

    def test_unsupported_modulo_without_dunder_fails_closed(self):
        with pytest.raises(
            NativeBuildError,
            match="Linux % on a native object without a matching dunder is not supported",
        ):
            compare_native_to_cpython(
                "clase SinMod:\n    pasar\nimprimir(SinMod() % 2)\n"
            )


if __name__ == "__main__":
    unittest.main()
