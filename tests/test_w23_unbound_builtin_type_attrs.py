"""Differential coverage for unbound builtin type attributes/methods (Linux).

Covers ``int.__new__``, ``int.__repr__``, ``int.__module__``,
``str.capitalize``, ``str.lower``, ``str.join``, ``type.__dict__`` and
``type.__subclasses__`` used both as first-class unbound values
(``f = int.__repr__; f(5)``) and as direct calls (``int.__repr__(5)``).
Each case is checked against CPython 3.12 with
``piton.native_differential.compare_native_to_cpython`` (byte-identical
stdout and identical exit code, or the same caught exception message).

``int.__aenter__`` / ``int.__aexit__`` are build-only targets here: the
stdlib analyser infers ``int`` for a user class in ``contextlib``'s
``cls.__aenter__`` / ``cls.__aexit__``, so those attribute reads must
compile even though CPython's real ``int`` has no such attributes.  They
are therefore not asserted as CPython-runtime values.

Negative cases (wrong receiver, wrong ``__new__`` class) and fail-closed
cases (unsupported attribute, wrong arity) are included below.
"""
from __future__ import annotations

import sys
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


@unittest.skipIf(sys.platform.startswith("win32"), "Linux backend feature")
class UnboundBuiltinTypeAttrsV1(unittest.TestCase):
    # int.__repr__ -----------------------------------------------------
    def test_int_repr_unbound_value(self):
        _assert_matches(
            self,
            "f = int.__repr__\n"
            "imprimir(f(5), f(-17), f(0))\n",
        )

    def test_int_repr_direct_call(self):
        _assert_matches(
            self,
            "imprimir(int.__repr__(42), int.__repr__(-3))\n",
        )

    def test_int_repr_wrong_receiver_raises_typeerror(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    int.__repr__('nope')\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n",
        )

    # int.__new__ ------------------------------------------------------
    def test_int_new_unbound_value(self):
        _assert_matches(
            self,
            "f = int.__new__\n"
            "imprimir(f(int, 5), f(int, -17), f(int, 0))\n",
        )

    def test_int_new_direct_call(self):
        _assert_matches(
            self,
            "imprimir(int.__new__(int, 42))\n",
        )

    def test_int_new_non_type_class_raises_typeerror(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    int.__new__(5, 2)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n",
        )

    def test_int_new_unrelated_type_raises_typeerror(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    int.__new__(str, 2)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n",
        )

    def test_int_new_unrelated_user_class_raises_typeerror(self):
        _assert_matches(
            self,
            "clase MiInt:\n"
            "    funcion __new__(cls, valor):\n"
            "        devolver int.__new__(cls, valor)\n"
            "intentar:\n"
            "    MiInt.__new__(MiInt, 3)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n",
        )

    def test_int_new_non_type_str_reports_actual_type(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    int.__new__('x', 2)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n",
        )

    def test_int_new_wrong_arity_fails_closed(self):
        with pytest.raises(NativeBuildError, match="requires exactly 2 positional argument"):
            compare_native_to_cpython("imprimir(int.__new__(int))\n")
        with pytest.raises(NativeBuildError, match="requires exactly 2 positional argument"):
            compare_native_to_cpython("imprimir(int.__new__(int, 5, 6))\n")

    # int.__module__ ---------------------------------------------------
    def test_int_module_as_value(self):
        _assert_matches(
            self,
            "f = int.__module__\n"
            "imprimir(f)\n",
        )

    def test_int_module_direct_attribute(self):
        _assert_matches(self, "imprimir(int.__module__)\n")

    # str.capitalize ---------------------------------------------------
    def test_str_capitalize_unbound_value(self):
        _assert_matches(
            self,
            "f = str.capitalize\n"
            "imprimir(f('hola'), f('HOLA'), f(''), f('a'), f('123abc'))\n",
        )

    def test_str_capitalize_direct_call(self):
        _assert_matches(
            self,
            "imprimir(str.capitalize('piton'), str.capitalize('PITON'))\n",
        )

    def test_str_capitalize_wrong_receiver_raises_typeerror(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    str.capitalize(5)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n",
        )

    # str.lower --------------------------------------------------------
    def test_str_lower_unbound_value(self):
        _assert_matches(
            self,
            "f = str.lower\n"
            "imprimir(f('HOLA'), f('hola'), f(''), f('A1b2'))\n",
        )

    def test_str_lower_direct_call(self):
        _assert_matches(
            self,
            "imprimir(str.lower('PITON'), str.lower('Piton'))\n",
        )

    def test_str_lower_wrong_receiver_raises_typeerror(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    str.lower(5)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n",
        )

    # str.join ---------------------------------------------------------
    def test_str_join_unbound_value(self):
        _assert_matches(
            self,
            "f = str.join\n"
            "imprimir(f('-', ['a', 'b', 'c']), f('', []), f('x', ['y']))\n",
        )

    def test_str_join_direct_call(self):
        _assert_matches(
            self,
            "imprimir(str.join('-', ['uno', 'dos', 'tres']), str.join('', ['a', 'b']))\n",
        )

    def test_str_join_wrong_receiver_raises_typeerror(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    str.join(5, ['a'])\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n",
        )

    def test_str_join_wrong_arity_fails_closed(self):
        with pytest.raises(NativeBuildError, match="requires exactly 2 positional argument"):
            compare_native_to_cpython("imprimir(str.join('-'))\n")
        with pytest.raises(NativeBuildError, match="requires exactly 2 positional argument"):
            compare_native_to_cpython("imprimir(str.join('-', ['a'], 'x'))\n")

    # type.__dict__ ----------------------------------------------------
    def test_type_dict_unbound_value(self):
        _assert_matches(
            self,
            "f = type.__dict__\n"
            "imprimir(len(f) > 0)\n",
        )

    def test_type_dict_direct_attribute(self):
        _assert_matches(self, "imprimir(len(type.__dict__) > 0)\n")

    # type.__subclasses__ ----------------------------------------------
    def test_type_subclasses_unbound_value(self):
        _assert_matches(
            self,
            "f = type.__subclasses__\n"
            "imprimir(type(f(object)) == list)\n",
        )

    def test_type_subclasses_direct_call(self):
        _assert_matches(
            self,
            "imprimir(type(type.__subclasses__(object)) == list)\n",
        )

    def test_type_subclasses_wrong_receiver_raises_typeerror(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    type.__subclasses__(5)\n"
            "excepto TypeError como error:\n"
            "    imprimir(str(error))\n",
        )

    # Fail-closed ------------------------------------------------------
    def test_unsupported_unbound_attrs_fail_closed(self):
        with pytest.raises(NativeBuildError, match=r"unbound int\.to_bytes\(\) is not supported"):
            compare_native_to_cpython("imprimir(int.to_bytes(5, 2))\n")
        with pytest.raises(NativeBuildError, match="is not supported as a value"):
            compare_native_to_cpython("imprimir(int.__str__)\n")


if __name__ == "__main__":
    unittest.main()
