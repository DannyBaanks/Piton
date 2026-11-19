"""W20 exception_concrete_type: matching against the builtin exception hierarchy.

Each case is compared against CPython 3.12 (oracle): identical stdout and exit
code. Handlers must match by subclass (ArithmeticError <- ZeroDivisionError,
LookupError <- KeyError/IndexError), tuple handlers must catch every listed
class, the bound `e` must report the RAISED class (not the handler name), and
KeyError must keep CPython's args/str (str(e) is repr(key)).
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


class ExceptionConcreteType(unittest.TestCase):
    def test_arithmetic_handler_catches_zero_division_with_concrete_type(self):
        # Reproducer arith_catches_zde: el tipo ligado es el LEVANTADO, no el handler.
        _assert_matches(
            self,
            "intentar:\n"
            "    1 // 0\n"
            "excepto ArithmeticError como e:\n"
            "    imprimir(type(e).__name__, e)\n",
        )

    def test_lookup_handler_catches_key_error_and_its_repr_message(self):
        # Reproducer lookup_catches_key: KeyError se captura por LookupError y str(e) es repr(k).
        _assert_matches(
            self,
            "intentar:\n"
            "    {}['k']\n"
            "excepto LookupError como e:\n"
            "    imprimir(type(e).__name__, e)\n",
        )

    def test_exception_handler_still_matches_value_error_subclass(self):
        # Reproducer exception_catches_value (ya PARITY): no debe romperse.
        _assert_matches(
            self,
            "intentar:\n"
            "    int('x')\n"
            "excepto Exception como e:\n"
            "    imprimir(type(e).__name__, isinstance(e, ValueError))\n",
        )

    def test_tuple_handler_catches_index_error_with_concrete_type(self):
        # Reproducer tuple_handler: antes silencioso con rc 0 (la excepcion se perdia).
        _assert_matches(
            self,
            "intentar:\n"
            "    [][1]\n"
            "excepto (KeyError, IndexError) como e:\n"
            "    imprimir(type(e).__name__)\n",
        )

    def test_index_error_does_not_leak_into_unrelated_handler(self):
        # Negativo: `excepto IndexError` no atrapa KeyError; el error sale del try.
        _assert_matches(
            self,
            "intentar:\n"
            "    {}['k']\n"
            "excepto IndexError como e:\n"
            "    imprimir('mal', e)\n"
            "imprimir('fin')\n",
        )

    def test_unmatched_tuple_handler_propagates_to_outer_handler(self):
        # Negativo: (ValueError, TypeError) no casa IndexError -> el try exterior lo recoge.
        _assert_matches(
            self,
            "intentar:\n"
            "    intentar:\n"
            "        [][1]\n"
            "    excepto (ValueError, TypeError) como e:\n"
            "        imprimir('mal')\n"
            "excepto LookupError como e:\n"
            "    imprimir('fuera', type(e).__name__, e)\n",
        )

    def test_uncaught_subscript_error_exits_like_cpython(self):
        # Sin try: CPython termina con rc 1 y sin stdout; PITON debe igualarlo.
        _assert_matches(
            self,
            "x = [][1]\n"
            "imprimir('no')\n",
        )

    def test_keyerror_args_and_str_for_integer_key(self):
        # KeyError(5).args == (5,) y str(e) == '5' (repr del entero).
        _assert_matches(
            self,
            "d = {1: 'a'}\n"
            "intentar:\n"
            "    d[5]\n"
            "excepto KeyError como e:\n"
            "    imprimir(e.args, str(e))\n",
        )

    def test_keyerror_str_is_repr_of_string_key(self):
        _assert_matches(
            self,
            "d = {'a': 1}\n"
            "intentar:\n"
            "    d['zz']\n"
            "excepto KeyError como e:\n"
            "    imprimir(str(e), e.args)\n",
        )

    def test_keyerror_message_for_tuple_none_and_float_keys(self):
        # Antes el mensaje de clave no-str era "" (str(e) y e.args divergian).
        _assert_matches(
            self,
            "d = {(1, 2): 'a'}\n"
            "intentar:\n"
            "    d[(3, 4)]\n"
            "excepto KeyError como e:\n"
            "    imprimir(str(e), e.args)\n"
            "d2 = {'a': 1}\n"
            "intentar:\n"
            "    d2[None]\n"
            "excepto KeyError como e:\n"
            "    imprimir(str(e), e.args)\n"
            "d3 = {1: 'a'}\n"
            "intentar:\n"
            "    d3[1.5]\n"
            "excepto KeyError como e:\n"
            "    imprimir(str(e), e.args)\n",
        )

    def test_explicit_keyerror_raise_is_caught_by_lookup_error(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    lanzar KeyError('k')\n"
            "excepto LookupError como e:\n"
            "    imprimir(type(e).__name__, str(e))\n",
        )

    def test_oserror_handler_catches_file_not_found_subclass(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    lanzar FileNotFoundError('falta')\n"
            "excepto OSError como e:\n"
            "    imprimir(type(e).__name__, e)\n",
        )

    def test_value_error_is_not_caught_by_arithmetic_handler(self):
        # Negativo: ValueError no es ArithmeticError; el try exterior lo atrapa.
        _assert_matches(
            self,
            "intentar:\n"
            "    intentar:\n"
            "        int('x')\n"
            "    excepto ArithmeticError:\n"
            "        imprimir('mal')\n"
            "excepto ValueError como e:\n"
            "    imprimir('ok', type(e).__name__)\n",
        )

    def test_multi_clause_try_dispatches_by_subclass(self):
        # Dos clausulas: la primera (LookupError) no casa ZeroDivisionError; la segunda si.
        _assert_matches(
            self,
            "intentar:\n"
            "    1 // 0\n"
            "excepto LookupError:\n"
            "    imprimir('mal')\n"
            "excepto ArithmeticError como e:\n"
            "    imprimir('ok', type(e).__name__, e)\n",
        )

    def test_subscript_in_loop_routes_each_failure(self):
        _assert_matches(
            self,
            "xs = [10, 20]\n"
            "para i en range(4):\n"
            "    intentar:\n"
            "        imprimir(xs[i])\n"
            "    excepto IndexError como e:\n"
            "        imprimir('fuera', i, type(e).__name__)\n",
        )

    def test_two_distinct_subscript_handlers_still_fail_closed(self):
        # Ambiguo: KeyError e IndexError van a handlers distintos y el receptor es
        # dinamico. Debe seguir fallando cerrado en build, no elegir en silencio.
        with pytest.raises(NativeBuildError, match="ambiguous for a dynamically-typed receiver"):
            compare_native_to_cpython(
                "d = {}\n"
                "intentar:\n"
                "    d['k']\n"
                "excepto KeyError:\n"
                "    imprimir('a')\n"
                "excepto IndexError:\n"
                "    imprimir('b')\n"
            )


if __name__ == "__main__":
    unittest.main()
