"""Runtime UnboundLocalError for a local shadowing a module function.

CPython compiles a function that reads a same-named local before any binding
and raises UnboundLocalError only when that read executes. PITON models this
with a per-local flag: reads that can only fail at run time (an earlier binding
on some path, or a read inside try) raise a catchable UnboundLocalError, while
straight-line reads with no earlier binding keep failing closed at build time.
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


class LocalShadowRuntimeUnbound(unittest.TestCase):
    def test_conditional_shadow_binding_keeps_normal_paths(self):
        _assert_matches(
            self,
            "funcion indent(t):\n"
            "    devolver '  ' + t\n"
            "funcion g(x):\n"
            "    si x:\n"
            "        indent = '>>'\n"
            "        devolver indent + 'a'\n"
            "    devolver 'b'\n"
            "imprimir(g(1), g(0), indent('c'))\n",
        )

    def test_read_before_assignment_inside_try_raises_catchable_unbound_local(self):
        _assert_matches(
            self,
            "funcion indent(t):\n"
            "    devolver t\n"
            "funcion g():\n"
            "    intentar:\n"
            "        devolver indent('x')\n"
            "    excepto UnboundLocalError:\n"
            "        devolver 'ULE'\n"
            "    indent = 1\n"
            "imprimir(g())\n",
        )

    def test_unbound_message_matches_cpython(self):
        _assert_matches(
            self,
            "funcion indent(t):\n"
            "    devolver t\n"
            "funcion g(x):\n"
            "    intentar:\n"
            "        devolver indent(x)\n"
            "    excepto NameError como e:\n"
            "        devolver str(e)\n"
            "    indent = 1\n"
            "    devolver 'no'\n"
            "imprimir(g(3))\n",
        )

    def test_unbound_error_is_a_name_error_subclass(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    lanzar UnboundLocalError('m')\n"
            "excepto NameError como e:\n"
            "    imprimir('nameerr', e)\n",
        )

    def test_conditional_binding_then_read_raises_when_unbound_path_taken(self):
        _assert_matches(
            self,
            "funcion indent(t):\n"
            "    devolver t\n"
            "funcion g(x):\n"
            "    si x:\n"
            "        indent = 5\n"
            "    devolver indent\n"
            "imprimir(g(1))\n"
            "intentar:\n"
            "    imprimir(g(0))\n"
            "excepto UnboundLocalError como e:\n"
            "    imprimir('ULE', e)\n",
        )

    def test_else_branch_read_is_checked_only_on_that_path(self):
        _assert_matches(
            self,
            "funcion indent(t):\n"
            "    devolver t\n"
            "funcion g(x):\n"
            "    si x:\n"
            "        indent = 3\n"
            "    sino:\n"
            "        devolver indent\n"
            "    devolver indent\n"
            "imprimir(g(1))\n"
            "intentar:\n"
            "    imprimir(g(0))\n"
            "excepto UnboundLocalError:\n"
            "    imprimir('else ule')\n",
        )

    def test_flag_resets_on_each_call(self):
        _assert_matches(
            self,
            "funcion indent(t):\n"
            "    devolver t\n"
            "funcion g(x):\n"
            "    intentar:\n"
            "        r = indent\n"
            "    excepto UnboundLocalError:\n"
            "        r = 'unbound'\n"
            "    si x:\n"
            "        indent = 7\n"
            "    devolver r\n"
            "imprimir(g(1))\n"
            "imprimir(g(0))\n"
            "imprimir(g(1))\n",
        )

    def test_augmented_assignment_on_unbound_local_raises(self):
        _assert_matches(
            self,
            "funcion indent(t):\n"
            "    devolver t\n"
            "funcion g():\n"
            "    intentar:\n"
            "        indent += 1\n"
            "    excepto UnboundLocalError:\n"
            "        devolver 'aug ule'\n"
            "    devolver indent\n"
            "imprimir(g())\n",
        )

    def test_for_target_shadow_binds_loop_body(self):
        _assert_matches(
            self,
            "funcion indent(t):\n"
            "    devolver t\n"
            "funcion g(xs):\n"
            "    intentar:\n"
            "        r = indent\n"
            "    excepto UnboundLocalError:\n"
            "        r = 'pre'\n"
            "    para indent en xs:\n"
            "        r = r + str(indent)\n"
            "    devolver r\n"
            "imprimir(g([1, 2]))\n",
        )

    def test_uncaught_unbound_local_exits_with_cpython_code(self):
        result = compare_native_to_cpython(
            "funcion indent(t):\n"
            "    devolver t\n"
            "funcion g(x):\n"
            "    si x:\n"
            "        indent = 1\n"
            "    devolver indent\n"
            "imprimir(g(1))\n"
            "imprimir(g(0))\n"
        )
        self.assertEqual(result.native.returncode, result.oracle.returncode)
        self.assertEqual(result.native.stdout, result.oracle.stdout)
        self.assertEqual(result.native.returncode, 1)

    def test_module_function_reads_outside_shadowing_scope_unchanged(self):
        _assert_matches(
            self,
            "funcion escape(x):\n"
            "    devolver x + 100\n"
            "funcion f(flag):\n"
            "    si flag:\n"
            "        escape = 40\n"
            "    sino:\n"
            "        escape = 50\n"
            "    devolver escape\n"
            "imprimir(f(verdadero), f(falso), escape(1))\n",
        )

    def test_straight_line_read_before_later_conditional_bind_fails_closed(self):
        with pytest.raises(
            NativeBuildError,
            match="local 'indent' shadowing a module function may be read before assignment",
        ):
            compare_native_to_cpython(
                "funcion indent(t):\n"
                "    devolver t\n"
                "funcion g(x):\n"
                "    devolver indent\n"
                "    si x:\n"
                "        indent = 1\n"
                "imprimir(g(1))\n"
            )

    def test_walrus_shadow_in_header_fails_closed(self):
        with pytest.raises(NativeBuildError, match="shadowing a module function"):
            compare_native_to_cpython(
                "funcion indent(t):\n"
                "    devolver t\n"
                "funcion g(x):\n"
                "    si (indent := 3) and x:\n"
                "        pasar\n"
                "    intentar:\n"
                "        devolver indent\n"
                "    excepto UnboundLocalError:\n"
                "        devolver 0\n"
                "imprimir(g(1))\n"
            )


if __name__ == "__main__":
    unittest.main()
