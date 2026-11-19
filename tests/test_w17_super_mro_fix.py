"""Regression coverage for Wave 17 super() MRO lowering."""
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
    testcase.assertEqual(result.native.stderr, result.oracle.stderr, "stderr mismatch")


class SuperMroFixParity(unittest.TestCase):
    def test_unused_super_init_with_builtin_arguments_still_compiles(self):
        _assert_matches(
            self,
            "clase A (classmethod ):\n"
            "    funcion __init__ (cls ,f ):\n"
            "        super ().__init__ (cls ,f )\n"
            "imprimir ('ok' )\n",
        )

    def test_super_init_classmethod_arity_error_is_raised_when_called(self):
        _assert_matches(
            self,
            "clase A(classmethod):\n"
            "    funcion __init__(cls, f):\n"
            "        super().__init__(cls, f)\n"
            "intentar:\n"
            "    A(1)\n"
            "excepto TypeError:\n"
            "    imprimir('caught')\n",
        )

    def test_unmodeled_builtin_init_arguments_remain_fail_closed(self):
        # Unmodeled builtin bases (e.g. tuple) still fail closed; list/dict bases
        # are modeled since W21 super_builtin_collection_base.
        source = (
            "clase Values(tuple):\n"
            "    funcion __init__(self, values):\n"
            "        super().__init__(values)\n"
            "Values([1, 2])\n"
        )
        with pytest.raises(
            NativeBuildError,
            match=r"super\(\)\.__init__ with arguments requires a modeled base implementation",
        ):
            compare_native_to_cpython(source)

    def test_list_base_super_init_with_arguments_matches_cpython(self):
        _assert_matches(
            self,
            "clase Values(list):\n"
            "    funcion __init__(self, values):\n"
            "        super().__init__(values)\n"
            "imprimir(Values([1, 2]))\n",
        )

    def test_plain_closure_inside_method_does_not_inherit_super_receiver(self):
        _assert_matches(
            self,
            "clase F:\n"
            "    funcion __init__(self):\n"
            "        funcion doble(x):\n"
            "            devolver x + x\n"
            "        self.f = doble\n"
            "    funcion aplicar(self, x):\n"
            "        devolver self.f(x)\n"
            "imprimir(F().aplicar(3))\n",
        )

    def test_nested_function_explicit_receiver_keeps_zero_arg_super(self):
        _assert_matches(
            self,
            "clase A:\n"
            "    funcion label(self):\n"
            "        devolver 'A'\n"
            "clase B(A):\n"
            "    funcion label(self):\n"
            "        funcion inner(self):\n"
            "            devolver super().label() + 'B'\n"
            "        devolver inner(self)\n"
            "imprimir(B().label())\n",
        )

    def test_plain_lambda_inside_method_does_not_inherit_super_receiver(self):
        _assert_matches(
            self,
            "clase F:\n"
            "    funcion __init__(self):\n"
            "        self.f = lambda x: x + x\n"
            "    funcion aplicar(self, x):\n"
            "        devolver self.f(x)\n"
            "imprimir(F().aplicar(3))\n",
        )

    def test_lambda_with_explicit_receiver_keeps_zero_arg_super(self):
        _assert_matches(
            self,
            "clase A:\n"
            "    funcion label(self):\n"
            "        devolver 'A'\n"
            "clase B(A):\n"
            "    funcion label(self):\n"
            "        inner = lambda self: super().label() + 'B'\n"
            "        devolver inner(self)\n"
            "imprimir(B().label())\n",
        )
