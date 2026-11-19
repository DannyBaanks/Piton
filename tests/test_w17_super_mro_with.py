"""Wave 17 parity coverage for zero-arg super() and inherited with methods."""
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
    testcase.assertEqual(
        result.native.stderr,
        result.oracle.stderr,
        "stderr mismatch",
    )


class SuperMroWithParity(unittest.TestCase):
    def test_super_builtin_base_empty_init_and_instance_attributes(self):
        _assert_matches(
            self,
            "clase Base(list):\n"
            "    funcion __init__(self):\n"
            "        super().__init__()\n"
            "        self.value = 8\n"
            "    funcion get(self):\n"
            "        devolver self.value\n"
            "imprimir(Base().get())\n",
        )

    def test_super_classmethod_uses_lexical_class_and_cls_receiver(self):
        _assert_matches(
            self,
            "clase A:\n"
            "    @classmethod\n"
            "    funcion label(cls):\n"
            "        devolver 'A'\n"
            "clase B(A):\n"
            "    @classmethod\n"
            "    funcion label(cls):\n"
            "        devolver super().label() + 'B'\n"
            "imprimir(B.label())\n",
        )

    def test_super_nested_function_uses_explicit_receiver(self):
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

    def test_super_resolves_unambiguous_class_alias_base(self):
        _assert_matches(
            self,
            "clase A:\n"
            "    funcion label(self):\n"
            "        devolver 'A'\n"
            "Original = A\n"
            "clase B(Original):\n"
            "    funcion label(self):\n"
            "        devolver super().label() + 'B'\n"
            "imprimir(B().label())\n",
        )

    def test_super_dynamic_base_expression_stays_fail_closed(self):
        source = (
            "clase A:\n"
            "    funcion label(self):\n"
            "        devolver 'A'\n"
            "funcion select_base():\n"
            "    devolver A\n"
            "Selected = select_base()\n"
            "clase B(Selected):\n"
            "    funcion label(self):\n"
            "        devolver super().label() + 'B'\n"
            "imprimir(B().label())\n"
        )
        with pytest.raises(NativeBuildError, match="cannot resolve dynamically evaluated base 'Selected'"):
            compare_native_to_cpython(source)

    def test_super_builtin_method_without_native_representation_fails_closed(self):
        source = (
            "clase Base(list):\n"
            "    funcion size(self):\n"
            "        devolver super().__len__()\n"
            "imprimir(Base().size())\n"
        )
        with pytest.raises(
            NativeBuildError,
            match="super\(\).__len__ for builtin base 'list' has no modeled native implementation",
        ):
            compare_native_to_cpython(source)

    def test_with_inherits_protocol_from_contextlib_abstract_manager(self):
        _assert_matches(
            self,
            "desde contextlib importar AbstractContextManager\n"
            "clase Manager(AbstractContextManager):\n"
            "    funcion __init__(self):\n"
            "        self.tag = 17\n"
            "    funcion __exit__(self, tipo, valor, traceback):\n"
            "        imprimir('exit')\n"
            "        devolver Falso\n"
            "con Manager() como manager:\n"
            "    imprimir(manager.tag)\n",
        )

    def test_contextlib_abstract_method_without_override_fails_closed(self):
        source = (
            "desde contextlib importar AbstractContextManager\n"
            "clase Incomplete(AbstractContextManager):\n"
            "    pasar\n"
            "con Incomplete():\n"
            "    imprimir('body')\n"
        )
        with pytest.raises(
            NativeBuildError,
            match="native abstract class 'Incomplete' cannot be instantiated",
        ):
            compare_native_to_cpython(source)

    def test_with_missing_protocol_raises_the_cpython_typeerror(self):
        _assert_matches(
            self,
            "clase Plain:\n"
            "    pasar\n"
            "intentar:\n"
            "    con Plain():\n"
            "        imprimir('body')\n"
            "excepto TypeError como exc:\n"
            "    imprimir(str(exc))\n",
        )

    def test_with_inherited_partial_protocol_raises_for_runtime_type(self):
        _assert_matches(
            self,
            "clase EnterOnly:\n"
            "    funcion __enter__(self):\n"
            "        devolver self\n"
            "clase Child(EnterOnly):\n"
            "    pasar\n"
            "intentar:\n"
            "    con Child():\n"
            "        imprimir('body')\n"
            "excepto TypeError como exc:\n"
            "    imprimir(str(exc))\n",
        )


if __name__ == "__main__":
    unittest.main()
