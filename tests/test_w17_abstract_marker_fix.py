"""Differential coverage for ABC abstract-method resolution."""
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


class AbstractMarkerParity(unittest.TestCase):
    def test_abstractmethod_decorator_does_not_make_plain_class_abstract(self):
        _assert_matches(
            self,
            "desde abc importar abstractmethod\n"
            "clase Plain:\n"
            "    @abstractmethod\n"
            "    funcion value(este):\n"
            "        devolver 12\n"
            "imprimir(Plain().value())\n",
        )

    def test_local_decorator_shadowing_import_is_not_abc_abstractmethod(self):
        _assert_matches(
            self,
            "desde abc importar ABC, abstractmethod\n"
            "funcion abstractmethod(fn):\n"
            "    imprimir('user decorator')\n"
            "    funcion wrapped(este):\n"
            "        devolver fn(este) + 1\n"
            "    devolver wrapped\n"
            "clase Concrete(ABC):\n"
            "    @abstractmethod\n"
            "    funcion value(este):\n"
            "        devolver 7\n"
            "imprimir(Concrete().value())\n",
        )

    def test_abc_subclass_with_unimplemented_abstract_method_stays_fail_closed(self):
        source = (
            "desde abc importar ABC, abstractmethod\n"
            "clase Incomplete(ABC):\n"
            "    @abstractmethod\n"
            "    funcion value(este):\n"
            "        devolver 7\n"
            "Incomplete()\n"
        )
        with pytest.raises(
            NativeBuildError,
            match="native abstract class 'Incomplete' cannot be instantiated",
        ):
            compare_native_to_cpython(source)

    def test_explicit_abcmeta_with_unimplemented_abstract_method_stays_fail_closed(self):
        source = (
            "importar abc\n"
            "clase Incomplete(metaclass=abc.ABCMeta):\n"
            "    @abc.abstractmethod\n"
            "    funcion value(este):\n"
            "        devolver 7\n"
            "Incomplete()\n"
        )
        with pytest.raises(
            NativeBuildError,
            match="native abstract class 'Incomplete' cannot be instantiated",
        ):
            compare_native_to_cpython(source)


if __name__ == "__main__":
    unittest.main()
