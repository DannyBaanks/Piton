"""Differential coverage for lexical names shadowing module functions."""
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


class LocalShadowsModuleFunction(unittest.TestCase):
    def test_local_value_shadows_module_function_without_clobbering_it(self):
        _assert_matches(
            self,
            "funcion escape(x):\n"
            "    devolver x + 100\n"
            "funcion f():\n"
            "    escape = 40\n"
            "    devolver escape + 2\n"
            "imprimir(f(), escape(1))\n",
        )

    def test_local_callable_shadows_module_function_at_call_site(self):
        _assert_matches(
            self,
            "funcion escape(x):\n"
            "    devolver x + 100\n"
            "funcion triplica(x):\n"
            "    devolver x * 3\n"
            "funcion f():\n"
            "    escape = triplica\n"
            "    devolver escape(5)\n"
            "imprimir(f(), escape(1))\n",
        )

    def test_local_assignment_in_each_branch_shadows_module_function(self):
        _assert_matches(
            self,
            "funcion escape(x):\n"
            "    devolver x + 100\n"
            "funcion choose(flag):\n"
            "    si flag:\n"
            "        escape = 40\n"
            "    sino:\n"
            "        escape = 50\n"
            "    devolver escape\n"
            "imprimir(choose(verdadero), choose(falso), escape(1))\n",
        )

    def test_parameter_callable_shadows_module_function(self):
        _assert_matches(
            self,
            "funcion escape(x):\n"
            "    devolver x + 100\n"
            "funcion other(x):\n"
            "    devolver x * 3\n"
            "funcion use(escape):\n"
            "    devolver escape(5)\n"
            "imprimir(use(other), escape(1))\n",
        )

    def test_parameter_non_callable_raises_type_error(self):
        result = compare_native_to_cpython(
            "funcion escape(x):\n"
            "    devolver x + 100\n"
            "funcion use(escape):\n"
            "    devolver escape(5)\n"
            "imprimir(use(42))\n"
        )
        self.assertEqual(result.native.returncode, result.oracle.returncode)
        self.assertEqual(result.native.stdout, result.oracle.stdout)
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError:", result.native.stderr)
        self.assertIn(b"TypeError:", result.oracle.stderr)

    def test_nested_closure_reads_shadowing_local(self):
        _assert_matches(
            self,
            "funcion escape(x):\n"
            "    devolver x + 100\n"
            "funcion outer():\n"
            "    escape = 40\n"
            "    funcion inner():\n"
            "        devolver escape + 2\n"
            "    devolver inner()\n"
            "imprimir(outer(), escape(1))\n",
        )

    def test_nested_function_shadows_module_function_in_closure(self):
        _assert_matches(
            self,
            "funcion escape(x):\n"
            "    devolver x + 100\n"
            "funcion outer():\n"
            "    funcion escape(x):\n"
            "        devolver x * 3\n"
            "    funcion inner():\n"
            "        devolver escape(5)\n"
            "    devolver inner()\n"
            "imprimir(outer(), escape(1))\n",
        )

    def test_global_declaration_reads_module_function(self):
        _assert_matches(
            self,
            "funcion escape(x):\n"
            "    devolver x + 1\n"
            "funcion f():\n"
            "    global escape\n"
            "    devolver escape(5)\n"
            "imprimir(f())\n",
        )

    def test_nested_global_bypasses_outer_local_shadow(self):
        _assert_matches(
            self,
            "funcion escape(x):\n"
            "    devolver x + 100\n"
            "funcion outer():\n"
            "    escape = 40\n"
            "    funcion inner():\n"
            "        global escape\n"
            "        devolver escape(5)\n"
            "    devolver inner() + escape\n"
            "imprimir(outer(), escape(1))\n",
        )

    def test_global_rebinding_updates_module_binding(self):
        _assert_matches(
            self,
            "funcion escape(x):\n"
            "    devolver x + 1\n"
            "funcion replace():\n"
            "    global escape\n"
            "    escape = 41\n"
            "    devolver escape\n"
            "funcion read_global():\n"
            "    devolver escape\n"
            "imprimir(replace(), read_global())\n",
        )

    def test_nonlocal_without_enclosing_binding_fails_closed(self):
        with pytest.raises(NativeBuildError, match="no binding for nonlocal 'escape' found"):
            compare_native_to_cpython(
                "funcion escape(x):\n"
                "    devolver x\n"
                "funcion f():\n"
                "    no_local escape\n"
                "    devolver escape\n"
            )

    def test_local_read_before_assignment_fails_closed(self):
        with pytest.raises(
            NativeBuildError,
            match="local 'escape' shadowing a module function may be read before assignment",
        ):
            compare_native_to_cpython(
                "funcion escape(x):\n"
                "    devolver x + 1\n"
                "funcion f():\n"
                "    devolver escape\n"
                "    escape = 41\n"
            )

    def test_local_self_reference_before_assignment_fails_closed(self):
        with pytest.raises(
            NativeBuildError,
            match="local 'escape' shadowing a module function may be read before assignment",
        ):
            compare_native_to_cpython(
                "funcion escape(x):\n"
                "    devolver x + 1\n"
                "funcion f():\n"
                "    escape = escape + 1\n"
                "    devolver escape\n"
            )


if __name__ == "__main__":
    unittest.main()
