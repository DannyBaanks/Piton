"""Differential coverage for class method decorators: @functools.total_ordering and @abstractmethod."""
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


class ClassMethodDecoratorsParity(unittest.TestCase):
    # ===== @functools.total_ordering tests =====

    def test_total_ordering_with_lt_and_eq(self):
        """@total_ordering synthesizes __gt__, __le__, __ge__ from __lt__ and __eq__."""
        _assert_matches(
            self,
            "importar functools\n"
            "@functools.total_ordering\n"
            "clase Item:\n"
            "    funcion __init__(este, valor):\n"
            "        este.valor = valor\n"
            "    funcion __eq__(este, otro):\n"
            "        devolver este.valor == otro.valor\n"
            "    funcion __lt__(este, otro):\n"
            "        devolver este.valor < otro.valor\n"
            "a = Item(1)\n"
            "b = Item(2)\n"
            "imprimir(a < b, a <= b, a > b, a >= b, a == b, a != b)\n",
        )

    def test_total_ordering_with_le_and_eq(self):
        """@total_ordering synthesizes __lt__, __gt__, __ge__ from __le__ and __eq__."""
        _assert_matches(
            self,
            "importar functools\n"
            "@functools.total_ordering\n"
            "clase Item:\n"
            "    funcion __init__(este, valor):\n"
            "        este.valor = valor\n"
            "    funcion __eq__(este, otro):\n"
            "        devolver este.valor == otro.valor\n"
            "    funcion __le__(este, otro):\n"
            "        devolver este.valor <= otro.valor\n"
            "a = Item(1)\n"
            "b = Item(2)\n"
            "imprimir(a < b, a <= b, a > b, a >= b, a == b, a != b)\n",
        )

    def test_total_ordering_with_gt_and_eq(self):
        """@total_ordering synthesizes __lt__, __le__, __ge__ from __gt__ and __eq__."""
        _assert_matches(
            self,
            "importar functools\n"
            "@functools.total_ordering\n"
            "clase Item:\n"
            "    funcion __init__(este, valor):\n"
            "        este.valor = valor\n"
            "    funcion __eq__(este, otro):\n"
            "        devolver este.valor == otro.valor\n"
            "    funcion __gt__(este, otro):\n"
            "        devolver este.valor > otro.valor\n"
            "a = Item(1)\n"
            "b = Item(2)\n"
            "imprimir(a < b, a <= b, a > b, a >= b, a == b, a != b)\n",
        )

    def test_total_ordering_with_ge_and_eq(self):
        """@total_ordering synthesizes __lt__, __le__, __gt__ from __ge__ and __eq__."""
        _assert_matches(
            self,
            "importar functools\n"
            "@functools.total_ordering\n"
            "clase Item:\n"
            "    funcion __init__(este, valor):\n"
            "        este.valor = valor\n"
            "    funcion __eq__(este, otro):\n"
            "        devolver este.valor == otro.valor\n"
            "    funcion __ge__(este, otro):\n"
            "        devolver este.valor >= otro.valor\n"
            "a = Item(1)\n"
            "b = Item(2)\n"
            "imprimir(a < b, a <= b, a > b, a >= b, a == b, a != b)\n",
        )

    def test_total_ordering_fails_without_ordering_method(self):
        """@total_ordering requires at least one ordering method."""
        source = (
            "importar functools\n"
            "@functools.total_ordering\n"
            "clase Item:\n"
            "    funcion __init__(este, valor):\n"
            "        este.valor = valor\n"
            "    funcion __eq__(este, otro):\n"
            "        devolver este.valor == otro.valor\n"
        )
        with pytest.raises(
            NativeBuildError,
            match="must define at least one ordering operation",
        ):
            compare_native_to_cpython(source)

    def test_total_ordering_inheritance(self):
        """@total_ordering works with inheritance."""
        _assert_matches(
            self,
            "importar functools\n"
            "@functools.total_ordering\n"
            "clase Base:\n"
            "    funcion __init__(este, valor):\n"
            "        este.valor = valor\n"
            "    funcion __eq__(este, otro):\n"
            "        devolver este.valor == otro.valor\n"
            "    funcion __lt__(este, otro):\n"
            "        devolver este.valor < otro.valor\n"
            "clase Derived(Base):\n"
            "    pasar\n"
            "a = Derived(1)\n"
            "b = Derived(2)\n"
            "imprimir(a < b, a <= b, a > b, a >= b)\n",
        )

    # ===== @abstractmethod tests =====

    def test_abstractmethod_on_plain_class(self):
        """@abstractmethod on a plain class (not ABC) is just a marker."""
        _assert_matches(
            self,
            "desde abc importar abstractmethod\n"
            "clase Plain:\n"
            "    @abstractmethod\n"
            "    funcion value(este):\n"
            "        devolver 42\n"
            "imprimir(Plain().value())\n",
        )

    def test_abstractmethod_with_property(self):
        """@abstractmethod stacked with @property."""
        _assert_matches(
            self,
            "desde abc importar abstractmethod\n"
            "clase Plain:\n"
            "    @property\n"
            "    @abstractmethod\n"
            "    funcion value(este):\n"
            "        devolver 42\n"
            "imprimir(Plain().value)\n",
        )

    def test_abstractmethod_with_property_reverse_order_fails_closed(self):
        """@abstractmethod stacked with @property in reverse order fails in CPython too."""
        source = (
            "desde abc importar abstractmethod\n"
            "clase Plain:\n"
            "    @abstractmethod\n"
            "    @property\n"
            "    funcion value(este):\n"
            "        devolver 42\n"
            "imprimir(Plain().value)\n"
        )
        with pytest.raises(
            NativeBuildError,
            match="attribute '__isabstractmethod__' of 'property' objects is not writable",
        ):
            compare_native_to_cpython(source)

    def test_abstractmethod_with_staticmethod(self):
        """@abstractmethod stacked with @staticmethod."""
        _assert_matches(
            self,
            "desde abc importar abstractmethod\n"
            "clase Plain:\n"
            "    @staticmethod\n"
            "    @abstractmethod\n"
            "    funcion value():\n"
            "        devolver 42\n"
            "imprimir(Plain.value())\n",
        )

    def test_abstractmethod_with_classmethod(self):
        """@abstractmethod stacked with @classmethod."""
        _assert_matches(
            self,
            "desde abc importar abstractmethod\n"
            "clase Plain:\n"
            "    @classmethod\n"
            "    @abstractmethod\n"
            "    funcion value(cls):\n"
            "        devolver 42\n"
            "imprimir(Plain.value())\n",
        )

    def test_abc_abstractmethod_prevents_instantiation(self):
        """ABC with unimplemented @abstractmethod prevents instantiation."""
        source = (
            "desde abc importar ABC, abstractmethod\n"
            "clase Incomplete(ABC):\n"
            "    @abstractmethod\n"
            "    funcion value(este):\n"
            "        devolver 42\n"
            "Incomplete()\n"
        )
        with pytest.raises(
            NativeBuildError,
            match="native abstract class 'Incomplete' cannot be instantiated",
        ):
            compare_native_to_cpython(source)

    def test_abc_abstractmethod_with_property(self):
        """ABC with @property @abstractmethod prevents instantiation if not implemented."""
        source = (
            "desde abc importar ABC, abstractmethod\n"
            "clase Incomplete(ABC):\n"
            "    @property\n"
            "    @abstractmethod\n"
            "    funcion value(este):\n"
            "        devolver 42\n"
            "Incomplete()\n"
        )
        with pytest.raises(
            NativeBuildError,
            match="native abstract class 'Incomplete' cannot be instantiated",
        ):
            compare_native_to_cpython(source)

    def test_abc_concrete_implementation(self):
        """ABC with implemented @abstractmethod can be instantiated."""
        _assert_matches(
            self,
            "desde abc importar ABC, abstractmethod\n"
            "clase Complete(ABC):\n"
            "    @abstractmethod\n"
            "    funcion value(este):\n"
            "        devolver 42\n"
            "clase Concrete(Complete):\n"
            "    funcion value(este):\n"
            "        devolver 100\n"
            "imprimir(Concrete().value())\n",
        )

    def test_abc_abstractmethod_via_module_attribute(self):
        """@abc.abstractmethod via module attribute."""
        _assert_matches(
            self,
            "importar abc\n"
            "clase Plain:\n"
            "    @abc.abstractmethod\n"
            "    funcion value(este):\n"
            "        devolver 42\n"
            "imprimir(Plain().value())\n",
        )

    # ===== @_sslcopydoc tests (marker decorator like abstractmethod) =====

    def test_sslcopydoc_marker_on_method(self):
        """@_sslcopydoc is a marker decorator that copies docstrings."""
        _assert_matches(
            self,
            "funcion _sslcopydoc(fn):\n"
            "    devolver fn\n"
            "clase Handler:\n"
            "    @_sslcopydoc\n"
            "    funcion process(este, valor):\n"
            "        devolver valor * 2\n"
            "imprimir(Handler().process(5))\n",
        )

    def test_sslcopydoc_with_property(self):
        """@_sslcopydoc stacked with @property."""
        _assert_matches(
            self,
            "funcion _sslcopydoc(fn):\n"
            "    devolver fn\n"
            "clase Handler:\n"
            "    @property\n"
            "    @_sslcopydoc\n"
            "    funcion value(este):\n"
            "        devolver 42\n"
            "imprimir(Handler().value)\n",
        )


if __name__ == "__main__":
    unittest.main()