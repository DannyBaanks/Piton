"""W21 user_exc_subclass_type: runtime class of values statically typed as a base.

`excepto Base como e:` binds `e` with the static type `object:Base`, but the
live instance may be a subclass (`lanzar Der(...)`). `type(e).__name__`,
`type(e) is X` and `isinstance(e, X)` must read the LIVE class and walk its
real ancestor chain (multi-level, builtin roots, tuples), not fold to the
handler's static class. Each case is compared against CPython 3.12 (oracle):
identical stdout and exit code.
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


class UserExcSubclassType(unittest.TestCase):
    def test_base_handler_binds_live_subclass_name_and_isinstance(self):
        # Reproducer caught_by_base_class_name: type(e).__name__ es 'Der', no 'Base'.
        _assert_matches(
            self,
            "clase Base(Exception):\n"
            "    pasar\n"
            "clase Der(Base):\n"
            "    pasar\n"
            "intentar:\n"
            "    lanzar Der('detalle')\n"
            "excepto Base como e:\n"
            "    imprimir(type(e).__name__, isinstance(e, Der), isinstance(e, Base), str(e))\n",
        )

    def test_base_handler_keeps_custom_attribute_of_subclass(self):
        # Reproducer custom_attr_on_subclass: el nombre es el vivo y e.codigo sigue accesible.
        _assert_matches(
            self,
            "clase Base(Exception):\n"
            "    pasar\n"
            "clase Der(Base):\n"
            "    funcion __init__(self, codigo):\n"
            "        self.codigo = codigo\n"
            "intentar:\n"
            "    lanzar Der(404)\n"
            "excepto Base como e:\n"
            "    imprimir(type(e).__name__, e.codigo)\n",
        )

    def test_grandparent_builtin_chain_matches_value_error_handler(self):
        # Reproducer caught_by_grandparent: B(A(ValueError)) lo captura `excepto ValueError`.
        _assert_matches(
            self,
            "clase A(ValueError):\n"
            "    pasar\n"
            "clase B(A):\n"
            "    pasar\n"
            "intentar:\n"
            "    lanzar B('msg')\n"
            "excepto ValueError como e:\n"
            "    imprimir(type(e).__name__, isinstance(e, B), isinstance(e, A), isinstance(e, ValueError))\n",
        )

    def test_grandparent_user_chain_isinstance_and_exception_root(self):
        _assert_matches(
            self,
            "clase Base(Exception):\n"
            "    pasar\n"
            "clase Der(Base):\n"
            "    pasar\n"
            "intentar:\n"
            "    lanzar Der('w')\n"
            "excepto Base como e:\n"
            "    imprimir(isinstance(e, Exception), isinstance(e, BaseException), isinstance(e, ValueError))\n",
        )

    def test_multi_level_subclass_resolves_every_ancestor(self):
        _assert_matches(
            self,
            "clase Base(Exception):\n"
            "    pasar\n"
            "clase Mid(Base):\n"
            "    pasar\n"
            "clase Leaf(Mid):\n"
            "    pasar\n"
            "intentar:\n"
            "    lanzar Leaf('q')\n"
            "excepto Base como e:\n"
            "    imprimir(type(e).__name__, isinstance(e, Mid), isinstance(e, Leaf), isinstance(e, Exception))\n",
        )

    def test_sibling_subclass_is_not_reported_as_other_sibling(self):
        # Caso negativo: una hermana no es Der ni Otra al mismo tiempo.
        _assert_matches(
            self,
            "clase Base(Exception):\n"
            "    pasar\n"
            "clase Der(Base):\n"
            "    pasar\n"
            "clase Otra(Base):\n"
            "    pasar\n"
            "intentar:\n"
            "    lanzar Otra('o')\n"
            "excepto Base como e:\n"
            "    imprimir(type(e).__name__, isinstance(e, Der), isinstance(e, Otra))\n",
        )

    def test_handler_with_base_live_class_is_unchanged(self):
        # Control positivo: sin subclase, el tipo sigue siendo la base.
        _assert_matches(
            self,
            "clase Base(Exception):\n"
            "    pasar\n"
            "intentar:\n"
            "    lanzar Base('y')\n"
            "excepto Base como e:\n"
            "    imprimir(type(e).__name__, isinstance(e, Base), str(e))\n",
        )

    def test_tuple_handler_binds_live_second_class(self):
        # Con varias clases en la tupla el tipo vivo sale del levantado (B), no de la primera (A).
        _assert_matches(
            self,
            "clase A(Exception):\n"
            "    pasar\n"
            "clase B(Exception):\n"
            "    pasar\n"
            "intentar:\n"
            "    lanzar B('x')\n"
            "excepto (A, B) como e:\n"
            "    imprimir(type(e).__name__, isinstance(e, B), isinstance(e, A))\n",
        )

    def test_tuple_handler_attribute_of_live_class(self):
        _assert_matches(
            self,
            "clase A(Exception):\n"
            "    funcion __init__(self, c):\n"
            "        self.c = c\n"
            "clase B(Exception):\n"
            "    funcion __init__(self, c):\n"
            "        self.c = c\n"
            "intentar:\n"
            "    lanzar B(7)\n"
            "excepto (A, B) como e:\n"
            "    imprimir(type(e).__name__, e.c)\n",
        )

    def test_three_class_tuple_with_grandchild_live_class(self):
        _assert_matches(
            self,
            "clase A(Exception):\n"
            "    pasar\n"
            "clase B(Exception):\n"
            "    pasar\n"
            "clase C(B):\n"
            "    pasar\n"
            "intentar:\n"
            "    lanzar C('c')\n"
            "excepto (A, B, ValueError) como e:\n"
            "    imprimir(type(e).__name__, isinstance(e, B), isinstance(e, A), str(e))\n",
        )

    def test_subclass_handler_first_then_base_handler_keep_own_names(self):
        _assert_matches(
            self,
            "clase Base(Exception):\n"
            "    pasar\n"
            "clase Der(Base):\n"
            "    pasar\n"
            "intentar:\n"
            "    lanzar Der('k')\n"
            "excepto Der como e:\n"
            "    imprimir('der', type(e).__name__)\n"
            "excepto Base como f:\n"
            "    imprimir('base', type(f).__name__)\n",
        )

    def test_tuple_user_and_builtin_handler_reports_live_name(self):
        _assert_matches(
            self,
            "clase A(Exception):\n"
            "    pasar\n"
            "intentar:\n"
            "    lanzar ValueError('v')\n"
            "excepto (A, ValueError) como e:\n"
            "    imprimir(type(e).__name__, str(e))\n",
        )

    def test_bare_reraise_keeps_live_subclass_for_outer_handler(self):
        # SUBCLASS_RERAISE_V1: `lanzar` dentro de `excepto Base` debe llegar a `excepto Der`.
        _assert_matches(
            self,
            "clase Base(Exception):\n"
            "    pasar\n"
            "clase Der(Base):\n"
            "    pasar\n"
            "intentar:\n"
            "    intentar:\n"
            "        lanzar Der('r')\n"
            "    excepto Base como e:\n"
            "        lanzar\n"
            "excepto Der como f:\n"
            "    imprimir(type(f).__name__, isinstance(f, Der))\n",
        )

    def test_identity_on_exception_type_fails_closed(self):
        # `is` entre clases no esta soportado: debe fallar cerrado en build, no compilar en silencio.
        with pytest.raises(NativeBuildError, match="binary operator not supported: is"):
            compare_native_to_cpython(
                "clase Base(Exception):\n"
                "    pasar\n"
                "clase Der(Base):\n"
                "    pasar\n"
                "intentar:\n"
                "    lanzar Der('z')\n"
                "excepto Base como e:\n"
                "    imprimir(type(e) is Der)\n"
            )


if __name__ == "__main__":
    unittest.main()
