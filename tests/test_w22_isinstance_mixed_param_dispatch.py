"""Differential parity tests for isinstance/es_instancia with mixed-type parameters (Wave 22)."""
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


class TestIsinstanceMixedParamDispatch(unittest.TestCase):
    """Tests for runtime-typed isinstance parameters with mixed types including collections."""

    def test_isinstance_mixed_list_str(self):
        """Original reproducer: param receives list and str, used in isinstance(s, str)."""
        _assert_matches(
            self,
            "funcion norm(s):\n"
            "    si isinstance(s, str):\n"
            "        devolver \"str:\" + s\n"
            "    devolver \"len:\" + str(len(s))\n"
            "imprimir(norm(\"abc\"), norm([1, 2, 3]))\n",
        )

    def test_isinstance_mixed_bytes_str(self):
        """Original reproducer: param receives bytes, list, str, used in isinstance(x, (str, bytes))."""
        _assert_matches(
            self,
            "funcion kind(x):\n"
            "    si isinstance(x, (str, bytes)):\n"
            "        devolver len(x)\n"
            "    devolver -1\n"
            "imprimir(kind(\"hi\"), kind(b\"hey\"), kind([1, 2]))\n",
        )

    def test_isinstance_mixed_tuple_dict(self):
        """Param receives tuple and dict, used in isinstance."""
        _assert_matches(
            self,
            "funcion classify(x):\n"
            "    si isinstance(x, tuple):\n"
            "        devolver 'tuple'\n"
            "    si isinstance(x, dict):\n"
            "        devolver 'dict'\n"
            "    devolver 'other'\n"
            "imprimir(classify((1, 2)), classify({'a': 1}), classify('str'))\n",
        )

    def test_isinstance_mixed_set_list(self):
        """Param receives set and list, used in isinstance."""
        _assert_matches(
            self,
            "funcion check(x):\n"
            "    si isinstance(x, set):\n"
            "        devolver 'set'\n"
            "    si isinstance(x, list):\n"
            "        devolver 'list'\n"
            "    devolver 'other'\n"
            "imprimir(check({1, 2}), check([1, 2]), check('str'))\n",
        )

    def test_isinstance_mixed_str_list_tuple(self):
        """Param receives str, list, tuple - all handled by runtime dispatch."""
        _assert_matches(
            self,
            "funcion process(x):\n"
            "    si isinstance(x, str):\n"
            "        devolver 'str:' + x\n"
            "    si isinstance(x, list):\n"
            "        devolver 'list:len=' + str(len(x))\n"
            "    si isinstance(x, tuple):\n"
            "        devolver 'tuple:len=' + str(len(x))\n"
            "    devolver 'other'\n"
            "imprimir(process('hello'), process([1,2,3]), process((4,5)))\n",
        )

    def test_isinstance_tuple_type_check(self):
        """isinstance with a tuple of types (str, bytes)."""
        _assert_matches(
            self,
            "funcion is_text(x):\n"
            "    si isinstance(x, (str, bytes)):\n"
            "        devolver True\n"
            "    devolver False\n"
            "imprimir(is_text('hello'), is_text(b'world'), is_text([1,2]))\n",
        )

    def test_es_instancia_mixed_params(self):
        """Test es_instancia (Spanish keyword) with mixed params."""
        _assert_matches(
            self,
            "funcion tipo(x):\n"
            "    si es_instancia(x, str):\n"
            "        devolver 'texto'\n"
            "    si es_instancia(x, list):\n"
            "        devolver 'lista'\n"
            "    devolver 'otro'\n"
            "imprimir(tipo('hola'), tipo([1,2]))\n",
        )

    def test_narrowing_in_isinstance_branch(self):
        """Test that type narrowing works inside isinstance branches for runtime-typed params."""
        _assert_matches(
            self,
            "funcion f(s):\n"
            "    si isinstance(s, str):\n"
            "        devolver s + '!'\n"
            "    devolver str(len(s))\n"
            "imprimir(f('hello'), f([1,2,3]))\n",
        )

    def test_nested_isinstance_branches(self):
        """Test nested isinstance checks with mixed types."""
        _assert_matches(
            self,
            "funcion analyze(x):\n"
            "    si isinstance(x, (list, tuple)):\n"
            "        si isinstance(x, list):\n"
            "            devolver 'list:' + str(len(x))\n"
            "        devolver 'tuple:' + str(len(x))\n"
            "    si isinstance(x, str):\n"
            "        devolver 'str:' + x\n"
            "    devolver 'other'\n"
            "imprimir(analyze([1,2]), analyze((3,4)), analyze('test'))\n",
        )

    def test_mixed_none_and_collections(self):
        """Test None mixed with collections in isinstance."""
        _assert_matches(
            self,
            "funcion handle(x=None):\n"
            "    si x es None:\n"
            "        devolver 'none'\n"
            "    si isinstance(x, list):\n"
            "        devolver 'list:' + str(len(x))\n"
            "    si isinstance(x, str):\n"
            "        devolver 'str:' + x\n"
            "    devolver 'other'\n"
            "imprimir(handle(), handle(None), handle([1,2]), handle('hi'))\n",
        )

    def test_literal_scalar_vs_collection_still_fail_closed(self):
        """Literal scalar (int) vs str should still fail closed (test w17)."""
        with pytest.raises(
            NativeBuildError,
            match=r"parametro 'valor' de 'tipo' recibe tipos distintos",
        ):
            compare_native_to_cpython(
                "funcion tipo(valor):\n"
                "    si es_instancia(valor, str):\n"
                "        devolver valor\n"
                "imprimir(tipo(3))\n"
                "imprimir(tipo('ruta'))\n"
            )

    def test_method_call_on_narrowed_type_fail_closed(self):
        """Method call on narrowed type should still fail closed (test w20)."""
        src = (
            "funcion f(x):\n"
            "    si isinstance(x, str):\n"
            "        devolver x.upper()\n"
            "    devolver x\n"
            "imprimir(f('a'), f(1))\n"
        )
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython(src)


if __name__ == "__main__":
    unittest.main()