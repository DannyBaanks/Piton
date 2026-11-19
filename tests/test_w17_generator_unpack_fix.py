"""Differential regression tests for generator call unpacking."""
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


class GeneratorUnpackFix(unittest.TestCase):
    def test_generator_yields_variadic_call_with_star_and_dstar(self):
        _assert_matches(
            self,
            "funcion suma(*a, **k):\n"
            "    devolver sum(a) + sum(k.values())\n"
            "funcion g(xs, kw):\n"
            "    para x en xs:\n"
            "        producir suma(*x, **kw)\n"
            "imprimir(list(g([(1, 2), (3, 4, 5)], {'z': 10})))\n",
        )

    def test_generator_call_unpack_preserves_zero_division_handler(self):
        _assert_matches(
            self,
            "funcion f(a, b):\n"
            "    devolver a // b\n"
            "funcion g(ps):\n"
            "    para p en ps:\n"
            "        intentar:\n"
            "            producir f(*p)\n"
            "        excepto ZeroDivisionError:\n"
            "            producir 'zde'\n"
            "imprimir(list(g([(7, 2), (1, 0), (9, 3)])))\n",
        )

    def test_generator_unpack_arity_error_is_caught_by_caller(self):
        _assert_matches(
            self,
            "funcion f(a):\n"
            "    devolver a\n"
            "funcion g():\n"
            "    producir f(*[1, 2])\n"
            "intentar:\n"
            "    imprimir(list(g()))\n"
            "excepto TypeError como e:\n"
            "    imprimir('TE', e)\n",
        )

    def test_generator_resumes_after_variadic_range_call(self):
        _assert_matches(
            self,
            "funcion f(*a):\n"
            "    devolver len(a)\n"
            "funcion g():\n"
            "    x = 0\n"
            "    para i en range(3):\n"
            "        x = x + f(*range(i))\n"
            "        producir x\n"
            "imprimir(list(g()))\n",
        )

    def test_generator_call_unpack_binding_error_reaches_local_handler(self):
        _assert_matches(
            self,
            "funcion f(*a):\n"
            "    devolver len(a)\n"
            "funcion g():\n"
            "    intentar:\n"
            "        producir f(*42)\n"
            "    excepto TypeError:\n"
            "        producir 'caught'\n"
            "imprimir(list(g()))\n",
        )

    def test_unknown_generator_dstar_mapping_still_fails_closed(self):
        source = (
            "funcion target(value):\n"
            "    devolver value\n"
            "funcion values(mapping):\n"
            "    producir target(**mapping)\n"
            "imprimir(list(values({'value': 7})))\n"
        )
        with pytest.raises(
            NativeBuildError,
            match=r"CALL_UNPACKING_DYNAMIC4_V1: \*\* operand must be a dict",
        ):
            compare_native_to_cpython(source)


if __name__ == "__main__":
    unittest.main()
