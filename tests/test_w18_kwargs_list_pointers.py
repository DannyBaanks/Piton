"""Parity regressions for element kinds crossing native call boundaries."""
from __future__ import annotations

import unittest

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


CASES = {
    "list_of_kwargs_results": "funcion f(**k):\n    devolver sorted(k.items())\nimprimir([f(**{'a': 1}), f()])\n",
    "tuple_of_results": "funcion f(**k):\n    devolver list(k)\nimprimir((f(a=1), f(b=2, c=3)))\n",
    "dict_of_results": "funcion f(*a):\n    devolver list(a)\nimprimir({'x': f(1, 2), 'y': f()})\n",
    "gen_kwargs": "funcion f(**k):\n    devolver sorted(k.items())\nfuncion g(ds):\n    para d en ds:\n        producir f(**d)\nimprimir(list(g([{'b': 2, 'a': 1}, {}])))\n",
    "nested_append": "funcion f(**k):\n    devolver dict(k)\nxs = []\nxs.append(f(a=1))\nxs.append(f())\nimprimir(xs)\n",
}


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


class KwargsListPointerParity(unittest.TestCase):
    def test_list_of_kwargs_results(self):
        _assert_matches(self, CASES["list_of_kwargs_results"])

    def test_tuple_of_results(self):
        _assert_matches(self, CASES["tuple_of_results"])

    def test_dict_of_results(self):
        _assert_matches(self, CASES["dict_of_results"])

    def test_gen_kwargs(self):
        _assert_matches(self, CASES["gen_kwargs"])

    def test_nested_append(self):
        _assert_matches(self, CASES["nested_append"])

    def test_vararg_slot_elements_keep_their_kind_in_all_literals_and_yields(self):
        _assert_matches(
            self,
            "funcion first(*args):\n"
            "    devolver args[0]\n"
            "funcion passthrough(xs):\n"
            "    para x en xs:\n"
            "        producir x\n"
            "mixed = [first('list'), 7]\n"
            "imprimir(mixed)\n"
            "imprimir(mixed[0], mixed[1])\n"
            "imprimir((first('tuple'), 8))\n"
            "imprimir({'key': first('dict'), first('dynamic key'): 'value'})\n"
            "imprimir({first('set')})\n"
            "imprimir(list(passthrough([first('yield')])))\n",
        )

    def test_non_mapping_kwargs_operand_still_raises_type_error(self):
        _assert_matches(
            self,
            "funcion f(**kw):\n"
            "    devolver kw\n"
            "intentar:\n"
            "    imprimir(f(**[('x', 1)]))\n"
            "excepto TypeError:\n"
            "    imprimir('TypeError')\n",
        )

    def test_generator_with_own_kwargs_signature_still_fails_closed(self):
        with pytest.raises(
            NativeBuildError,
            match=r"native generator 'g' with \*\*kwargs is not supported yet",
        ):
            compare_native_to_cpython(
                "funcion g(**kw):\n"
                "    producir kw\n"
                "imprimir(list(g()))\n"
            )

if __name__ == "__main__":
    unittest.main()
