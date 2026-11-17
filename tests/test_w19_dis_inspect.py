"""Parity checks for the dis/inspect Tier A blockers (wave 19).

- str % tuple with a runtime-length / unknown-kind right operand (dis.py).
- list.sort(key=...) and sorted() on slot-typed or unknown receivers (inspect.py).
- stable reverse ordering, catchable comparison errors, tuple.sort() AttributeError.
- sys.byteorder as a native constant (dis.py: int.from_bytes(data, sys.byteorder)).
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


class PercentRuntimeTupleParity(unittest.TestCase):
    def test_percent_over_enumerate_loop_variable(self):
        # dis.py: "%4d: %r" % i_c, con i_c de enumerate(...) (forma no conocida en build).
        _assert_matches(
            self,
            "consts = ['a', 2, None]\n"
            "lines = []\n"
            "para i_c en enumerate(consts):\n"
            "    lines.append('%4d: %r' % i_c)\n"
            "para i_n en enumerate(['x', 'yy']):\n"
            "    lines.append('%4d: %s' % i_n)\n"
            "imprimir('\\n'.join(lines))\n",
        )

    def test_percent_enumerate_string_items_do_not_crash(self):
        _assert_matches(
            self,
            "para i_n en enumerate(['x', 'yy']):\n"
            "    imprimir('%s %s' % i_n)\n"
            "xs = ['x', 'yy']\n"
            "para i_n en enumerate(xs):\n"
            "    imprimir('%d|%s' % i_n)\n",
        )

    def test_percent_runtime_tuple_arity_mismatch_is_catchable(self):
        _assert_matches(
            self,
            "pares = [(1,), (1, 2)]\n"
            "para p en pares:\n"
            "    intentar:\n"
            "        imprimir('%s' % p)\n"
            "    excepto TypeError como e:\n"
            "        imprimir('TypeError', e)\n"
            "trios = [(1, 2, 3)]\n"
            "para t en trios:\n"
            "    intentar:\n"
            "        imprimir('%s-%s' % t)\n"
            "    excepto TypeError como e:\n"
            "        imprimir('TypeError', e)\n",
        )

    def test_percent_static_arity_mismatch_still_catchable(self):
        _assert_matches(
            self,
            "par = (1, 2, 3)\n"
            "intentar:\n"
            "    imprimir('%s-%s' % par)\n"
            "excepto TypeError:\n"
            "    imprimir('TypeError')\n",
        )

    def test_percent_unknown_scalar_parameter_matches_cpython(self):
        _assert_matches(
            self,
            "funcion f(x):\n"
            "    devolver '%d|%s' % x\n"
            "imprimir(f((4, 'q')))\n",
        )


class SortDynamicReceiverParity(unittest.TestCase):
    def test_sort_key_on_loop_built_list_of_tuples(self):
        _assert_matches(
            self,
            "pares = []\n"
            "para k en ['b', 'a', 'c']:\n"
            "    pares.append((k, len(k)))\n"
            "pares.sort(key=lambda p: p[0])\n"
            "imprimir(pares)\n"
            "resultados = []\n"
            "para clave en ['b', 'a']:\n"
            "    valor = clave * 2\n"
            "    resultados.append((clave, valor))\n"
            "resultados.sort(key=lambda p: p[0])\n"
            "imprimir(resultados)\n",
        )

    def test_sort_key_on_parameter_list(self):
        _assert_matches(
            self,
            "funcion ordenar(xs):\n"
            "    xs.sort(key=lambda p: p[0])\n"
            "    devolver xs\n"
            "imprimir(ordenar([(2, 'a'), (1, 'b')]))\n"
            "funcion f(x):\n"
            "    x.sort()\n"
            "    devolver x\n"
            "imprimir(f([3, 1, 2]))\n",
        )

    def test_sorted_on_dynamic_value_matches_cpython(self):
        _assert_matches(
            self,
            "funcion f(x):\n"
            "    devolver sorted(x)\n"
            "imprimir(f([3, 1, 2]))\n"
            "imprimir(f((9, 8)))\n"
            "funcion g(x):\n"
            "    devolver sorted(x, key=lambda p: p[0])\n"
            "imprimir(g([(2, 'a'), (1, 'b')]))\n",
        )

    def test_sorted_non_iterable_is_catchable_type_error(self):
        _assert_matches(
            self,
            "funcion f(x):\n"
            "    intentar:\n"
            "        devolver sorted(x)\n"
            "    excepto TypeError:\n"
            "        devolver 'TypeError'\n"
            "imprimir(f(7))\n",
        )

    def test_getmembers_shaped_sort_with_predicate_and_continue(self):
        # Forma de inspect._getmembers. Sin try/except dentro del bucle ni
        # lambdas pasados como parametro (ver NOT_DEMONSTRATED en el informe).
        _assert_matches(
            self,
            "funcion _getmembers(objeto, filtro):\n"
            "    resultados = []\n"
            "    procesados = set()\n"
            "    nombres = ['b', 'a', 'c', 'a']\n"
            "    para clave en nombres:\n"
            "        valor = len(clave) + 3\n"
            "        si clave en procesados:\n"
            "            continuar\n"
            "        si valor > filtro:\n"
            "            resultados.append((clave, valor))\n"
            "        procesados.add(clave)\n"
            "    resultados.sort(key=lambda par: par[0])\n"
            "    devolver resultados\n"
            "imprimir(_getmembers(3, 0))\n",
        )


class SortOrderingParity(unittest.TestCase):
    def test_reverse_sort_keeps_ties_in_original_order(self):
        _assert_matches(
            self,
            "xs = [(1, 'a'), (2, 'c'), (1, 'b')]\n"
            "xs.sort(key=lambda p: p[0], reverse=True)\n"
            "imprimir(xs)\n"
            "imprimir(sorted(xs, key=lambda p: p[0], reverse=True))\n"
            "pares = []\n"
            "para i, v en enumerate(['b', 'a', 'b', 'a']):\n"
            "    pares.append((v, i))\n"
            "pares.sort(key=lambda p: p[0], reverse=True)\n"
            "imprimir(pares)\n",
        )

    def test_incomparable_keys_raise_catchable_type_error(self):
        _assert_matches(
            self,
            "xs = [(2, 'a'), ('x', 'b')]\n"
            "intentar:\n"
            "    xs.sort(key=lambda p: p[0])\n"
            "excepto TypeError como e:\n"
            "    imprimir('TypeError')\n"
            "imprimir('after')\n"
            "itens = [(2, 'a')]\n"
            "itens.append(('x', 'b'))\n"
            "intentar:\n"
            "    itens.sort(key=lambda p: p[0])\n"
            "excepto TypeError como e:\n"
            "    imprimir('TypeError')\n",
        )

    def test_tuple_sort_is_attribute_error(self):
        _assert_matches(
            self,
            "t = (3, 1, 2)\n"
            "intentar:\n"
            "    t.sort(key=abs)\n"
            "excepto AttributeError como e:\n"
            "    imprimir('AttributeError')\n"
            "imprimir(t)\n"
            "funcion f(x):\n"
            "    intentar:\n"
            "        x.sort(key=abs)\n"
            "    excepto AttributeError:\n"
            "        devolver 'AttributeError'\n"
            "    devolver x\n"
            "imprimir(f((3, 1)))\n",
        )


class SysByteorderParity(unittest.TestCase):
    def test_int_from_bytes_with_sys_byteorder(self):
        _assert_matches(
            self,
            "importar sys\n"
            "imprimir(int.from_bytes(b'\\x01\\x02', sys.byteorder))\n"
            "imprimir(sys.byteorder)\n"
            "intentar:\n"
            "    imprimir(int.from_bytes(b'\\x01', 'middle'))\n"
            "excepto ValueError como e:\n"
            "    imprimir('ValueError')\n",
        )


class FailClosedNegatives(unittest.TestCase):
    def test_sort_key_on_scalar_receiver_still_fails_closed(self):
        with pytest.raises(NativeBuildError, match="sort\\(\\) requires a list"):
            compare_native_to_cpython("n = 5\nn.sort(key=abs)\n")


if __name__ == "__main__":
    unittest.main()
