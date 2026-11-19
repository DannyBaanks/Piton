"""Parity checks for function decorators on class special (dunder) methods.

Runtime dispatch (repr/str/len/hash/comparisons/truth) calls the class's dunder
symbol directly. A decorated dunder therefore gets a forwarder under that symbol
that invokes the decorated object, so identity and wrapper decorators both run.
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


_PREFIJO = (
    "funcion prefijo(fn):\n"
    "    funcion envuelto(self):\n"
    "        devolver '<' + fn(self) + '>'\n"
    "    devolver envuelto\n"
)

_PREFIJO2 = (
    "funcion prefijo(fn):\n"
    "    funcion envuelto(self, otro):\n"
    "        devolver fn(self, otro)\n"
    "    devolver envuelto\n"
)


class DecoratedDunderMethods(unittest.TestCase):
    def test_identity_decorators_on_repr_str_eq_lt_len_hash(self):
        _assert_matches(
            self,
            "funcion identidad(fn):\n"
            "    devolver fn\n"
            "clase ficha:\n"
            "    funcion __init__(self, v):\n"
            "        self.v = v\n"
            "    @identidad\n"
            "    funcion __repr__(self):\n"
            "        devolver f'ficha({self.v})'\n"
            "    @identidad\n"
            "    funcion __str__(self):\n"
            "        devolver 'S' + str(self.v)\n"
            "    @identidad\n"
            "    funcion __eq__(self, otro):\n"
            "        devolver self.v == otro.v\n"
            "    @identidad\n"
            "    funcion __lt__(self, otro):\n"
            "        devolver self.v < otro.v\n"
            "    @identidad\n"
            "    funcion __len__(self):\n"
            "        devolver self.v\n"
            "    @identidad\n"
            "    funcion __hash__(self):\n"
            "        devolver self.v\n"
            "imprimir(repr(ficha(7)), str(ficha(7)), ficha(7) == ficha(7), ficha(7) == ficha(8))\n"
            "imprimir(ficha(1) < ficha(2), ficha(3) < ficha(2), len(ficha(4)), hash(ficha(5)))\n",
        )

    def test_wrapper_decorator_on_repr_runs_on_repr_and_str(self):
        _assert_matches(
            self,
            _PREFIJO
            + "clase Nodo:\n"
            "    funcion __init__(self, n):\n"
            "        self.n = n\n"
            "    @prefijo\n"
            "    funcion __repr__(self):\n"
            "        devolver f'Nodo:{self.n}'\n"
            "imprimir(repr(Nodo(3)), str(Nodo(3)))\n",
        )

    def test_wrapper_decorator_on_eq_and_ne_receives_other_argument(self):
        _assert_matches(
            self,
            _PREFIJO2
            + "clase P:\n"
            "    funcion __init__(self, v):\n"
            "        self.v = v\n"
            "    @prefijo\n"
            "    funcion __eq__(self, otro):\n"
            "        devolver self.v == otro.v\n"
            "imprimir(P(1) == P(1), P(1) == P(2), P(1) != P(2), P(1) != P(1))\n",
        )

    def test_wrapper_decorator_on_lt_and_le_comparisons(self):
        _assert_matches(
            self,
            _PREFIJO2
            + "clase P:\n"
            "    funcion __init__(self, v):\n"
            "        self.v = v\n"
            "    @prefijo\n"
            "    funcion __lt__(self, otro):\n"
            "        devolver self.v < otro.v\n"
            "imprimir(P(1) < P(2), P(3) < P(2), P(2) > P(1))\n",
        )

    def test_wrapper_decorator_on_len_and_hash_changes_result(self):
        _assert_matches(
            self,
            "funcion prefijo(fn):\n"
            "    funcion envuelto(self):\n"
            "        devolver fn(self) + 100\n"
            "    devolver envuelto\n"
            "clase C:\n"
            "    funcion __init__(self, n):\n"
            "        self.n = n\n"
            "    @prefijo\n"
            "    funcion __len__(self):\n"
            "        devolver self.n\n"
            "    @prefijo\n"
            "    funcion __hash__(self):\n"
            "        devolver self.n\n"
            "imprimir(len(C(4)), hash(C(5)))\n",
        )

    def test_wrapper_decorator_on_str_and_explicit_dunder_call(self):
        _assert_matches(
            self,
            "funcion corchetes(fn):\n"
            "    funcion envuelto(self):\n"
            "        devolver '[' + fn(self) + ']'\n"
            "    devolver envuelto\n"
            "clase S:\n"
            "    @corchetes\n"
            "    funcion __str__(self):\n"
            "        devolver 'abc'\n"
            "s = S()\n"
            "imprimir(str(s))\n"
            "imprimir(s)\n"
            "imprimir(s.__str__())\n",
        )

    def test_wrapper_decorator_on_bool_controls_truth_value(self):
        _assert_matches(
            self,
            "funcion negar(fn):\n"
            "    funcion envuelto(self):\n"
            "        devolver no fn(self)\n"
            "    devolver envuelto\n"
            "clase B:\n"
            "    @negar\n"
            "    funcion __bool__(self):\n"
            "        devolver Verdadero\n"
            "imprimir(bool(B()))\n"
            "si B():\n"
            "    imprimir('si')\n"
            "sino:\n"
            "    imprimir('no')\n",
        )

    def test_stacked_wrapper_decorators_apply_bottom_up(self):
        _assert_matches(
            self,
            "funcion a(fn):\n"
            "    funcion ea(self):\n"
            "        devolver 'A' + fn(self)\n"
            "    devolver ea\n"
            "funcion b(fn):\n"
            "    funcion eb(self):\n"
            "        devolver 'B' + fn(self)\n"
            "    devolver eb\n"
            "clase Q:\n"
            "    @a\n"
            "    @b\n"
            "    funcion __repr__(self):\n"
            "        devolver 'q'\n"
            "imprimir(repr(Q()))\n",
        )

    def test_decorator_that_replaces_result_without_calling_method(self):
        _assert_matches(
            self,
            "funcion siempre(fn):\n"
            "    funcion envuelto(self):\n"
            "        devolver 'fijo'\n"
            "    devolver envuelto\n"
            "clase W:\n"
            "    @siempre\n"
            "    funcion __repr__(self):\n"
            "        devolver 'otro'\n"
            "imprimir(repr(W()))\n",
        )

    def test_decorated_dunder_with_default_parameter_fails_closed(self):
        source = (
            _PREFIJO2
            + "clase P:\n"
            "    @prefijo\n"
            "    funcion __eq__(self, otro=None):\n"
            "        devolver Verdadero\n"
            "imprimir(P() == P())\n"
        )
        with pytest.raises(
            NativeBuildError,
            match="decorated special method '__eq__' requires plain positional parameters",
        ):
            compare_native_to_cpython(source)

    def test_decorated_dunder_outside_dispatch_set_fails_closed(self):
        source = (
            "funcion prefijo(fn):\n"
            "    funcion envuelto(self, clave):\n"
            "        devolver fn(self, clave)\n"
            "    devolver envuelto\n"
            "clase T:\n"
            "    @prefijo\n"
            "    funcion __getitem__(self, clave):\n"
            "        devolver clave\n"
            "imprimir(T()[1])\n"
        )
        with pytest.raises(
            NativeBuildError,
            match="decorated special method '__getitem__' is not supported by runtime dispatch",
        ):
            compare_native_to_cpython(source)
