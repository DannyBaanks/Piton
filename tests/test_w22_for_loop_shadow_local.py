"""Parity for ``para`` loop targets that shadow module-level functions.

A ``para campo, valor en xs:`` target binds its names before every iteration of
the loop body, so reading a target that happens to share a name with a module
function *inside the body* is legitimate and must match CPython. The shadow
initialization flow analysis in ``piton/mir.py`` (`_validate_local_shadow_initialization`)
used to reject this at build time with a false "read before assignment"
`MIRLoweringError`, blocking `dataclasses.py` at Tier A.

Straight-line reads with no earlier binding, and reads of a loop target after
the loop (where the loop may run zero times), must still fail closed.
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
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}"
        f"\noracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(
        result.native.stdout,
        result.oracle.stdout,
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}"
        f"\noracle stderr: {result.oracle.stderr!r}",
    )


class ForLoopTargetShadowsModuleFunction(unittest.TestCase):
    def test_for_target_shadows_fn_plain_body(self):
        _assert_matches(
            self,
            "funcion campo(x):\n"
            "    devolver x + 1\n"
            "funcion usar(xs):\n"
            "    para campo en xs:\n"
            "        devolver campo\n"
            "    devolver 0\n"
            "imprimir(usar([5, 6, 7]))\n",
        )

    def test_for_target_shadows_fn_inside_si(self):
        _assert_matches(
            self,
            "funcion field(x):\n"
            "    devolver x + 1\n"
            "funcion usar(pares, flag):\n"
            "    si flag:\n"
            "        para field, value en pares:\n"
            "            devolver field\n"
            "    devolver 0\n"
            "imprimir(field(10), usar([(1, \"a\"), (2, \"b\")], Verdadero))\n",
        )

    def test_for_target_shadows_fn_inside_try(self):
        _assert_matches(
            self,
            "funcion item(x):\n"
            "    devolver x * 2\n"
            "funcion usar(xs):\n"
            "    intentar:\n"
            "        para item en xs:\n"
            "            x = item + 1\n"
            "    excepto Exception:\n"
            "        pasar\n"
            "    devolver 1\n"
            "imprimir(item(3), usar([1, 2, 3]))\n",
        )

    def test_for_tuple_unpack_target_shadows_fn(self):
        _assert_matches(
            self,
            "funcion clave(x):\n"
            "    devolver x * 2\n"
            "funcion usar(pares):\n"
            "    para k, clave en pares:\n"
            "        devolver clave\n"
            "    devolver 0\n"
            "imprimir(usar([(1, 5), (2, 6)]))\n",
        )

    def test_for_target_shadows_fn_empty_loop_body(self):
        # Loop runs zero times: the body never reads the target, so both sides
        # return the fallback value and the module function is untouched.
        _assert_matches(
            self,
            "funcion campo(x):\n"
            "    devolver x + 1\n"
            "funcion usar(xs):\n"
            "    para campo en xs:\n"
            "        devolver campo\n"
            "    devolver 0\n"
            "imprimir(campo(3), usar([]))\n",
        )

    def test_straight_line_read_before_bind_still_fails_closed(self):
        # Genuine read-before-assignment: no for-loop involved, must still fail
        # closed at build time (the fix must not weaken this check).
        with pytest.raises(
            NativeBuildError,
            match="local 'campo' shadowing a module function may be read before assignment",
        ):
            compare_native_to_cpython(
                "funcion campo(x):\n"
                "    devolver x + 1\n"
                "funcion usar(flag):\n"
                "    devolver campo\n"
                "    si flag:\n"
                "        campo = 1\n"
                "imprimir(usar(Verdadero))\n"
            )

    def test_for_target_read_after_loop_still_fails_closed(self):
        # Reading a loop target after the loop (the loop may run zero times, so
        # the name may be unbound) must still fail closed at build time.
        with pytest.raises(
            NativeBuildError,
            match="local 'item' shadowing a module function may be read before assignment",
        ):
            compare_native_to_cpython(
                "funcion item(x):\n"
                "    devolver x * 2\n"
                "funcion usar(xs):\n"
                "    para item en xs:\n"
                "        pasar\n"
                "    devolver item\n"
                "imprimir(usar([]))\n"
            )


if __name__ == "__main__":
    unittest.main()
