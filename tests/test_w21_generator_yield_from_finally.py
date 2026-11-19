"""W21 adversarial parity: `producir desde` (yield from) inside native generators.

Covers item sources (list/tuple/range/str/dict/genexpr/sub-generators), the
value of `res = producir desde <delegado>` (StopIteration.value), exception
propagation through the delegating generator, `finalmente:` blocks, and
send() forwarding into delegates. Every case is compared against CPython 3.12.
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


class YieldFromSources(unittest.TestCase):
    def test_yields_all_items_from_list_tuple_range_and_str(self):
        _assert_matches(
            self,
            "funcion g():\n"
            "    producir desde [1, 2]\n"
            "    producir desde (3, 4)\n"
            "    producir desde range(5, 7)\n"
            "    producir desde 'ab'\n"
            "    producir 'fin'\n"
            "imprimir(list(g()))\n",
        )

    def test_yields_from_dict_keys_and_genexpr(self):
        _assert_matches(
            self,
            "funcion g():\n"
            "    producir desde {'a': 1, 'b': 2}\n"
            "    producir desde (x * 2 para x en [1, 2, 3])\n"
            "imprimir(sorted(list(g()), key=repr))\n",
        )

    def test_yields_from_sub_generator_and_nested_delegation(self):
        _assert_matches(
            self,
            "funcion c():\n"
            "    producir 1\n"
            "    devolver 'c'\n"
            "funcion b():\n"
            "    r = producir desde c()\n"
            "    producir r\n"
            "    devolver 'b'\n"
            "funcion a():\n"
            "    r = producir desde b()\n"
            "    producir r\n"
            "imprimir(list(a()))\n",
        )

    def test_empty_list_delegate_has_none_value(self):
        _assert_matches(
            self,
            "funcion g():\n"
            "    r = producir desde []\n"
            "    imprimir('r=', r)\n"
            "    producir 1\n"
            "imprimir(list(g()))\n",
        )


class YieldFromReturnValue(unittest.TestCase):
    def test_return_value_of_direct_sub_generator_is_captured(self):
        _assert_matches(
            self,
            "funcion acumulador():\n"
            "    producir 'a'\n"
            "    producir 'b'\n"
            "    devolver 42\n"
            "funcion consumidor(out):\n"
            "    res = producir desde acumulador()\n"
            "    out.append(res)\n"
            "    producir 'fin'\n"
            "caja = []\n"
            "imprimir(list(consumidor(caja)), caja)\n",
        )

    def test_missing_return_is_none_and_explicit_none_is_none(self):
        _assert_matches(
            self,
            "funcion sub():\n"
            "    producir 1\n"
            "funcion sub_none():\n"
            "    producir 1\n"
            "    devolver None\n"
            "funcion g():\n"
            "    r = producir desde sub()\n"
            "    imprimir('r1=', r)\n"
            "    producir r\n"
            "    s = producir desde sub_none()\n"
            "    imprimir('r2=', s)\n"
            "    producir s\n"
            "imprimir(list(g()))\n",
        )

    def test_return_value_kinds_are_preserved(self):
        _assert_matches(
            self,
            "funcion sub_s():\n"
            "    producir 1\n"
            "    devolver 'texto'\n"
            "funcion g():\n"
            "    r = producir desde sub_s()\n"
            "    imprimir('r=', r)\n"
            "    producir 0\n"
            "imprimir(list(g()))\n",
        )

    def test_return_value_of_generator_held_in_variable(self):
        _assert_matches(
            self,
            "funcion sub():\n"
            "    producir 1\n"
            "    devolver 42\n"
            "funcion g():\n"
            "    s = sub()\n"
            "    res = producir desde s\n"
            "    producir res\n"
            "imprimir(list(g()))\n",
        )

    def test_return_value_of_generator_parameter(self):
        _assert_matches(
            self,
            "funcion sub():\n"
            "    producir 1\n"
            "    devolver 'fin'\n"
            "funcion g(s):\n"
            "    r = producir desde s\n"
            "    producir r\n"
            "imprimir(list(g(sub())))\n",
        )

    def test_exhausted_generator_gives_none_on_second_delegation(self):
        # Negative/edge: StopIteration.value is consumed once; a second
        # `producir desde` over the same exhausted generator yields None.
        _assert_matches(
            self,
            "funcion sub():\n"
            "    producir 1\n"
            "    devolver 5\n"
            "funcion g():\n"
            "    s = sub()\n"
            "    r = producir desde s\n"
            "    producir r\n"
            "    r2 = producir desde s\n"
            "    producir r2\n"
            "imprimir(list(g()))\n",
        )

    def test_return_value_inside_while_and_for_loops(self):
        _assert_matches(
            self,
            "funcion sub(n):\n"
            "    para i en range(n):\n"
            "        producir i\n"
            "    devolver n * 10\n"
            "funcion g():\n"
            "    total = 0\n"
            "    mientras total < 2:\n"
            "        total = total + 1\n"
            "        r = producir desde sub(total)\n"
            "        producir r\n"
            "imprimir(list(g()))\n",
        )


class YieldFromExceptionsAndFinally(unittest.TestCase):
    def test_sub_generator_exception_propagates_to_caller(self):
        # Regression: the exception used to be swallowed as StopIteration and
        # the program exited 0 printing only the items.
        _assert_matches(
            self,
            "funcion sub():\n"
            "    producir 1\n"
            "    lanzar ValueError('boom')\n"
            "funcion g():\n"
            "    producir desde sub()\n"
            "intentar:\n"
            "    imprimir(list(g()))\n"
            "excepto ValueError como e:\n"
            "    imprimir('capturado', e)\n",
        )

    def test_finally_runs_then_exception_propagates(self):
        _assert_matches(
            self,
            "funcion sub():\n"
            "    producir 1\n"
            "    lanzar ValueError('boom')\n"
            "funcion g():\n"
            "    intentar:\n"
            "        producir desde sub()\n"
            "    finalmente:\n"
            "        imprimir('fin')\n"
            "intentar:\n"
            "    imprimir(list(g()))\n"
            "excepto ValueError como e:\n"
            "    imprimir('capturado', e)\n",
        )

    def test_finally_body_yield_from_order(self):
        _assert_matches(
            self,
            "funcion sub():\n"
            "    producir 1\n"
            "    lanzar ValueError('boom')\n"
            "funcion g():\n"
            "    intentar:\n"
            "        producir desde sub()\n"
            "    finalmente:\n"
            "        producir 'f'\n"
            "intentar:\n"
            "    imprimir(list(g()))\n"
            "excepto ValueError como e:\n"
            "    imprimir('cap', e)\n",
        )

    def test_parent_except_catches_sub_exception_and_continues(self):
        _assert_matches(
            self,
            "funcion sub():\n"
            "    producir 1\n"
            "    lanzar KeyError('k')\n"
            "funcion g():\n"
            "    intentar:\n"
            "        producir desde sub()\n"
            "    excepto KeyError como e:\n"
            "        producir 'capt'\n"
            "    producir 'after'\n"
            "imprimir(list(g()))\n",
        )

    def test_non_matching_parent_except_propagates_to_outer(self):
        # Negative/edge: the inner handler does not match the exception class.
        _assert_matches(
            self,
            "funcion sub():\n"
            "    producir 1\n"
            "    lanzar KeyError('k')\n"
            "funcion g():\n"
            "    intentar:\n"
            "        producir desde sub()\n"
            "    excepto ValueError como e:\n"
            "        producir 'nope'\n"
            "intentar:\n"
            "    imprimir(list(g()))\n"
            "excepto KeyError como e2:\n"
            "    imprimir('outer', e2)\n",
        )

    def test_finally_with_normal_completion_and_return_value(self):
        _assert_matches(
            self,
            "funcion sub():\n"
            "    producir 1\n"
            "    devolver 5\n"
            "funcion g():\n"
            "    intentar:\n"
            "        r = producir desde sub()\n"
            "    finalmente:\n"
            "        imprimir('fin')\n"
            "    devolver r + 1\n"
            "q = g()\n"
            "imprimir(list(q))\n",
        )

    def test_close_runs_parent_finally(self):
        _assert_matches(
            self,
            "funcion sub():\n"
            "    producir 1\n"
            "    producir 2\n"
            "funcion g():\n"
            "    intentar:\n"
            "        producir desde sub()\n"
            "    finalmente:\n"
            "        imprimir('limpieza')\n"
            "q = g()\n"
            "imprimir(next(q))\n"
            "q.close()\n"
            "imprimir('cerrado')\n",
        )


class YieldFromSend(unittest.TestCase):
    def test_send_forwards_to_direct_sub_generator(self):
        _assert_matches(
            self,
            "funcion sub():\n"
            "    x = producir 1\n"
            "    imprimir('recv', x)\n"
            "    producir 2\n"
            "funcion g():\n"
            "    producir desde sub()\n"
            "q = g()\n"
            "imprimir(next(q))\n"
            "imprimir(q.send('hola'))\n",
        )

    def test_send_forwards_to_generator_held_in_variable_and_parameter(self):
        _assert_matches(
            self,
            "funcion sub():\n"
            "    x = producir 1\n"
            "    imprimir('recv', x)\n"
            "    producir 2\n"
            "funcion g():\n"
            "    s = sub()\n"
            "    producir desde s\n"
            "q = g()\n"
            "imprimir(next(q))\n"
            "imprimir(q.send('hola'))\n"
            "funcion h(s):\n"
            "    producir desde s\n"
            "q2 = h(sub())\n"
            "imprimir(next(q2))\n"
            "imprimir(q2.send('adios'))\n",
        )

    def test_send_into_list_delegate_raises_attribute_error(self):
        # Negative/edge: a native iterator has no .send(); CPython raises.
        _assert_matches(
            self,
            "funcion g():\n"
            "    producir desde [1, 2]\n"
            "q = g()\n"
            "imprimir(next(q))\n"
            "intentar:\n"
            "    q.send(5)\n"
            "excepto AttributeError como e:\n"
            "    imprimir('AE', e)\n",
        )

    def test_send_into_genexpr_delegate_discards_value(self):
        _assert_matches(
            self,
            "funcion g():\n"
            "    producir desde (x * 2 para x en [1, 2, 3])\n"
            "q = g()\n"
            "imprimir(next(q))\n"
            "imprimir(q.send('hola'))\n",
        )

    def test_send_none_to_list_delegate_is_next(self):
        _assert_matches(
            self,
            "funcion g():\n"
            "    r = producir desde [7, 8]\n"
            "    imprimir('r', r)\n"
            "    producir 0\n"
            "q = g()\n"
            "imprimir(next(q))\n"
            "imprimir(q.send(None))\n"
            "imprimir(q.send(None))\n",
        )


class GeneratorStopIterationPep479(unittest.TestCase):
    def test_stop_iteration_caught_locally_inside_generator(self):
        # Regression: a StopIteration raised in the body and caught by a local
        # `excepto StopIteration` must NOT be converted to RuntimeError.
        _assert_matches(
            self,
            "funcion g():\n"
            "    intentar:\n"
            "        lanzar StopIteration()\n"
            "    excepto StopIteration:\n"
            "        producir 1\n"
            "    producir 2\n"
            "imprimir(list(g()))\n",
        )

    def test_stop_iteration_escaping_generator_becomes_runtime_error(self):
        _assert_matches(
            self,
            "funcion g():\n"
            "    producir 1\n"
            "    lanzar StopIteration()\n"
            "intentar:\n"
            "    imprimir(list(g()))\n"
            "excepto RuntimeError como e:\n"
            "    imprimir('RT', e)\n",
        )

    def test_stop_iteration_from_sub_generator_becomes_runtime_error_in_delegator(self):
        _assert_matches(
            self,
            "funcion s():\n"
            "    producir 1\n"
            "    lanzar StopIteration()\n"
            "funcion g():\n"
            "    producir desde s()\n"
            "    producir 9\n"
            "intentar:\n"
            "    imprimir(list(g()))\n"
            "excepto RuntimeError como e:\n"
            "    imprimir('RT', e)\n",
        )

    def test_stop_iteration_with_non_matching_excepto_becomes_runtime_error(self):
        # Negative/edge: the local handler does not accept StopIteration, so it
        # escapes the generator body and is converted.
        _assert_matches(
            self,
            "funcion g():\n"
            "    intentar:\n"
            "        lanzar StopIteration()\n"
            "    excepto ValueError:\n"
            "        producir 1\n"
            "intentar:\n"
            "    imprimir(list(g()))\n"
            "excepto RuntimeError como e:\n"
            "    imprimir('RT', e)\n",
        )

    def test_finally_runs_before_stop_iteration_runtime_error(self):
        _assert_matches(
            self,
            "funcion g():\n"
            "    intentar:\n"
            "        lanzar StopIteration()\n"
            "    finalmente:\n"
            "        imprimir('fin')\n"
            "intentar:\n"
            "    imprimir(list(g()))\n"
            "excepto RuntimeError como e:\n"
            "    imprimir('RT', e)\n",
        )


class YieldFromFailClosed(unittest.TestCase):
    def test_yield_from_outside_generator_fails_closed(self):
        with pytest.raises(NativeBuildError, match="only valid inside a generator"):
            compare_native_to_cpython("producir desde [1, 2]\n")


if __name__ == "__main__":
    unittest.main()
