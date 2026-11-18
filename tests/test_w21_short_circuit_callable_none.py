"""W21 short_circuit_callable_none: `y`/`o` short-circuit and callable|None params.

A parameter with default None that also receives a lambda/closure shares the
int slot in the native backend. The None singleton must stay falsy through
`no`, `y`/`o` boxing and `es None`, and a function that mixes a dynamic callable
result with another return kind must be typed as a tagged slot.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from piton.native_differential import _compile_native, compare_native_to_cpython
from piton.x86 import NativeBuildError


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    result = compare_native_to_cpython(source)
    testcase.assertEqual(
        result.native.returncode,
        result.oracle.returncode,
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(
        result.native.stdout,
        result.oracle.stdout,
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}",
    )


CASES = {
    "or_short_circuit_none_callable": "funcion filtrar(xs, predicado=None):\n    res = []\n    para x en xs:\n        si no predicado o predicado(x):\n            res.append(x)\n    devolver res\nimprimir(filtrar([1, 2, 3]), filtrar([1, 2, 3], lambda v: v > 1))\n",
    "and_short_circuit_callable": "funcion aplicar(x, fn=None):\n    si fn y fn(x) > 10:\n        devolver 'grande'\n    devolver 'chico'\nimprimir(aplicar(5), aplicar(5, lambda v: v * 3), aplicar(2, lambda v: v * 3))\n",
    "is_none_on_callable_param": "funcion revisar(fn=None):\n    si fn es None:\n        devolver 'sin_fn'\n    devolver fn(7)\nimprimir(revisar(), revisar(lambda z: z + 1))\n",
}


class ShortCircuitCallableNone(unittest.TestCase):
    def test_repro_or_short_circuit_none_or_callable(self):
        _assert_matches(self, CASES["or_short_circuit_none_callable"])

    def test_repro_and_short_circuit_callable(self):
        _assert_matches(self, CASES["and_short_circuit_callable"])

    def test_repro_is_none_on_callable_param(self):
        _assert_matches(self, CASES["is_none_on_callable_param"])

    def test_not_none_param_is_truthy_only_when_callable(self):
        _assert_matches(
            self,
            "funcion neg(fn=None):\n"
            "    devolver no fn\n"
            "imprimir(neg(), neg(lambda: 1), neg(0))\n",
        )

    def test_none_operand_of_or_and_and_return_the_operand(self):
        _assert_matches(
            self,
            "funcion h(fn=None):\n"
            "    devolver fn o 'x'\n"
            "funcion k(fn=None):\n"
            "    devolver fn y 5\n"
            "imprimir(h(), h(''), h(7))\n"
            "imprimir(k(), k(0), k(2))\n",
        )

    def test_is_none_with_none_and_callable_call_sites(self):
        _assert_matches(
            self,
            "funcion revisar(fn=None):\n"
            "    si fn es None:\n"
            "        devolver 'sin_fn'\n"
            "    si fn no es None:\n"
            "        devolver 'con_fn'\n"
            "    devolver 'otro'\n"
            "funcion cuadrar(v, fn=None):\n"
            "    si v es None y fn es None:\n"
            "        devolver 1\n"
            "    si fn es None o fn(v):\n"
            "        devolver 2\n"
            "    devolver 3\n"
            "imprimir(revisar(), revisar(lambda z: z), revisar(None))\n"
            "imprimir(cuadrar(None), cuadrar(4, lambda w: w > 3), cuadrar(None, lambda w: w), cuadrar(1))\n",
        )

    def test_mixed_return_of_dynamic_call_and_str_literal(self):
        # The str literal and the dynamic call result share one return slot;
        # an empty str must survive the caller's repr() and print.
        _assert_matches(
            self,
            "funcion g(f=None):\n"
            "    si f es None:\n"
            "        devolver ''\n"
            "    devolver f(7)\n"
            "imprimir(repr(g()), g(lambda z: z + 1), repr(g(lambda z: 0)))\n"
            "imprimir(repr(g()))\n",
        )

    def test_user_function_named_like_builtin_map_is_not_builtin(self):
        # `filtrar` is a user function here; it must not be lowered as the
        # builtin filter alias (which requires exactly two arguments).
        _assert_matches(
            self,
            "funcion filtrar(xs):\n"
            "    res = []\n"
            "    para x en xs:\n"
            "        si x > 1:\n"
            "            res.append(x)\n"
            "    devolver res\n"
            "imprimir(filtrar([1, 2, 3]))\n",
        )

    def test_builtin_map_with_one_argument_still_fails_closed(self):
        source = "imprimir(map(lambda v: v))\n"
        with tempfile.TemporaryDirectory(prefix="w21-sc-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "map/filter require a unary callback"):
                _compile_native(source, Path(directory) / "program.exe")


if __name__ == "__main__":
    unittest.main()
