"""Differential coverage for the augmented-assignment dunder protocol (x op= y).

CPython tries ``x.__iop__(y)`` first, falls back to the binary protocol when it
is missing or returns NotImplemented, and raises ``TypeError`` with the ``op=``
spelling when nothing accepts the operands.
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


class AugmentedDunderProtocol(unittest.TestCase):
    def test_reproducer_isub_falls_back_to_sub(self):
        _assert_matches(
            self,
            "clase V:\n"
            "    funcion __init__(este, x):\n"
            "        este.x = x\n"
            "    funcion __sub__(este, o):\n"
            "        devolver V(este.x - o)\n"
            "v = V(10)\n"
            "v -= 3\n"
            "imprimir(v.x)\n",
        )

    def test_reproducer_iadd_inplace_mutates_and_rebinds(self):
        _assert_matches(
            self,
            "clase B:\n"
            "    funcion __init__(este):\n"
            "        este.xs = []\n"
            "    funcion __iadd__(este, o):\n"
            "        este.xs.append(o)\n"
            "        devolver este\n"
            "b = B()\n"
            "b += 1\n"
            "b += 2\n"
            "imprimir(b.xs)\n",
        )

    def test_reproducer_no_dunder_raises_augmented_typeerror(self):
        _assert_matches(
            self,
            "clase N:\n"
            "    pasar\n"
            "n = N()\n"
            "intentar:\n"
            "    n -= 1\n"
            "excepto TypeError como e:\n"
            "    imprimir('TE', e)\n",
        )

    def test_every_inplace_dunder_is_dispatched_with_its_own_name(self):
        _assert_matches(
            self,
            "clase M:\n"
            "    funcion __init__(este, v):\n"
            "        este.v = v\n"
            "    funcion __imul__(este, o):\n"
            "        devolver ('imul', o)\n"
            "    funcion __itruediv__(este, o):\n"
            "        devolver ('itruediv', o)\n"
            "    funcion __ifloordiv__(este, o):\n"
            "        devolver ('ifloordiv', o)\n"
            "    funcion __imod__(este, o):\n"
            "        devolver ('imod', o)\n"
            "    funcion __ipow__(este, o):\n"
            "        devolver ('ipow', o)\n"
            "    funcion __ilshift__(este, o):\n"
            "        devolver ('ilshift', o)\n"
            "    funcion __irshift__(este, o):\n"
            "        devolver ('irshift', o)\n"
            "    funcion __iand__(este, o):\n"
            "        devolver ('iand', o)\n"
            "    funcion __ior__(este, o):\n"
            "        devolver ('ior', o)\n"
            "    funcion __ixor__(este, o):\n"
            "        devolver ('ixor', o)\n"
            "m = M(1)\n"
            "m *= 2\n"
            "imprimir(m)\n"
            "m = M(1)\n"
            "m /= 2\n"
            "imprimir(m)\n"
            "m = M(1)\n"
            "m //= 2\n"
            "imprimir(m)\n"
            "m = M(1)\n"
            "m %= 2\n"
            "imprimir(m)\n"
            "m = M(1)\n"
            "m **= 2\n"
            "imprimir(m)\n"
            "m = M(1)\n"
            "m <<= 2\n"
            "imprimir(m)\n"
            "m = M(1)\n"
            "m >>= 2\n"
            "imprimir(m)\n"
            "m = M(1)\n"
            "m &= 2\n"
            "imprimir(m)\n"
            "m = M(1)\n"
            "m |= 2\n"
            "imprimir(m)\n"
            "m = M(1)\n"
            "m ^= 2\n"
            "imprimir(m)\n",
        )

    def test_binary_only_dunders_serve_augmented_operators(self):
        _assert_matches(
            self,
            "clase B:\n"
            "    funcion __sub__(este, o):\n"
            "        devolver 'sub'\n"
            "    funcion __mul__(este, o):\n"
            "        devolver 'mul'\n"
            "    funcion __pow__(este, o):\n"
            "        devolver 'pow'\n"
            "    funcion __lshift__(este, o):\n"
            "        devolver 'lshift'\n"
            "    funcion __and__(este, o):\n"
            "        devolver 'and'\n"
            "    funcion __xor__(este, o):\n"
            "        devolver 'xor'\n"
            "b = B()\n"
            "b -= 1\n"
            "imprimir(b)\n"
            "b = B()\n"
            "b *= 1\n"
            "imprimir(b)\n"
            "b = B()\n"
            "b **= 1\n"
            "imprimir(b)\n"
            "b = B()\n"
            "b <<= 1\n"
            "imprimir(b)\n"
            "b = B()\n"
            "b &= 1\n"
            "imprimir(b)\n"
            "b = B()\n"
            "b ^= 1\n"
            "imprimir(b)\n",
        )

    def test_notimplemented_from_iop_falls_back_to_binary(self):
        _assert_matches(
            self,
            "clase A:\n"
            "    funcion __iadd__(este, o):\n"
            "        devolver NotImplemented\n"
            "    funcion __add__(este, o):\n"
            "        devolver 'add' + str(o)\n"
            "a = A()\n"
            "a += 5\n"
            "imprimir(a)\n",
        )

    def test_iop_may_rebind_to_a_different_type(self):
        _assert_matches(
            self,
            "clase A:\n"
            "    funcion __iadd__(este, o):\n"
            "        devolver 'nuevo'\n"
            "a = A()\n"
            "a += 1\n"
            "imprimir(a)\n",
        )

    def test_reflected_dunder_on_right_operand(self):
        _assert_matches(
            self,
            "clase R:\n"
            "    funcion __rsub__(este, o):\n"
            "        devolver o * 100\n"
            "x = 7\n"
            "x -= R()\n"
            "imprimir(x)\n",
        )

    def test_missing_dunders_raise_augmented_typeerror_both_sides(self):
        _assert_matches(
            self,
            "clase Q:\n"
            "    pasar\n"
            "x = 7\n"
            "intentar:\n"
            "    x -= Q()\n"
            "excepto TypeError como e:\n"
            "    imprimir('TE', e)\n"
            "n = Q()\n"
            "intentar:\n"
            "    n **= 2\n"
            "excepto TypeError como e:\n"
            "    imprimir('TE', e)\n"
            "y = 3\n"
            "intentar:\n"
            "    y += Q()\n"
            "excepto TypeError como e:\n"
            "    imprimir('TE', e)\n",
        )

    def test_exception_inside_iop_propagates(self):
        _assert_matches(
            self,
            "clase E:\n"
            "    funcion __iadd__(este, o):\n"
            "        lanzar ValueError('dentro')\n"
            "intentar:\n"
            "    e = E()\n"
            "    e += 1\n"
            "excepto ValueError como x:\n"
            "    imprimir('VE', x)\n",
        )

    def test_subclass_reflected_override_runs_before_base_binary(self):
        _assert_matches(
            self,
            "clase Base:\n"
            "    funcion __isub__(este, o):\n"
            "        devolver NotImplemented\n"
            "    funcion __sub__(este, o):\n"
            "        devolver 'base'\n"
            "clase Der(Base):\n"
            "    funcion __rsub__(este, o):\n"
            "        devolver 'der-r'\n"
            "b = Base()\n"
            "b -= Der()\n"
            "imprimir(b)\n",
        )

    def test_dynamic_slot_operands_use_the_same_protocol(self):
        _assert_matches(
            self,
            "clase C:\n"
            "    funcion __init__(este):\n"
            "        este.xs = []\n"
            "    funcion __iadd__(este, o):\n"
            "        este.xs.append(o)\n"
            "        devolver este\n"
            "lst = [C(), C()]\n"
            "lst[0] += 4\n"
            "lst[0] += 6\n"
            "imprimir(lst[0].xs)\n"
            "imprimir(lst[1].xs)\n",
        )

    def test_attribute_target_augmented_assignment(self):
        _assert_matches(
            self,
            "clase C:\n"
            "    funcion __init__(este):\n"
            "        este.n = 0\n"
            "    funcion __iadd__(este, o):\n"
            "        este.n = este.n + o\n"
            "        devolver este\n"
            "clase Caja:\n"
            "    funcion __init__(este):\n"
            "        este.c = C()\n"
            "q = Caja()\n"
            "q.c += 3\n"
            "q.c += 4\n"
            "imprimir(q.c.n)\n",
        )

    def test_matmul_augmented_protocol_and_builtin_typeerror(self):
        _assert_matches(
            self,
            "clase Mt:\n"
            "    funcion __imatmul__(este, o):\n"
            "        devolver ('im', o)\n"
            "m = Mt()\n"
            "m @= 2\n"
            "imprimir(m)\n"
            "clase Rm:\n"
            "    funcion __rmatmul__(este, o):\n"
            "        devolver ('rmm', o)\n"
            "r = Rm()\n"
            "x = 3\n"
            "x @= r\n"
            "imprimir(x)\n"
            "intentar:\n"
            "    r @= 2\n"
            "excepto TypeError como e:\n"
            "    imprimir('TE', e)\n",
        )

    def test_builtin_matmul_augmented_still_fails_closed(self):
        with pytest.raises(NativeBuildError, match="@= between builtin operands is not supported"):
            compare_native_to_cpython("x = 3\nx @= 2\nimprimir(x)\n")

    def test_operator_without_any_dunder_still_fails_closed_for_plain_matmul(self):
        with pytest.raises(NativeBuildError, match="Linux @ on a native object without a matching dunder"):
            compare_native_to_cpython(
                "clase SinMatmul:\n    pasar\nimprimir(SinMatmul() @ 2)\n"
            )


if __name__ == "__main__":
    unittest.main()
