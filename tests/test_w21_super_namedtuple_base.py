"""Parity checks for classes that inherit from a collections.namedtuple base.

``Base = namedtuple('Base', [...])`` used as a base class is rewritten to a
tuple-backed class definition (piton/namedtuple_bases.py). These tests pin the
CPython 3.12 behaviour of that path and the fail-closed boundaries.
"""
from __future__ import annotations

import unittest

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError

PRE = (
    "desde collections importar namedtuple\n"
    "Base = namedtuple('Base', ['x', 'y'])\n"
    "clase Punto(Base):\n"
    "    pasar\n"
)


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


class NamedtupleSubclassParity(unittest.TestCase):
    def test_reproducer_method_and_tuple(self):
        _assert_matches(
            self,
            "desde collections importar namedtuple\n"
            "Base = namedtuple('Base', ['x', 'y'])\n"
            "clase Punto(Base):\n"
            "    funcion suma(self):\n"
            "        devolver self.x + self.y\n"
            "p = Punto(3, 4)\n"
            "imprimir(p.x, p.y, p.suma(), tuple(p))\n",
        )

    def test_reproducer_super_iter(self):
        _assert_matches(
            self,
            "desde collections importar namedtuple\n"
            "Par = namedtuple('Par', 'a b')\n"
            "clase MiPar(Par):\n"
            "    funcion como_lista(self):\n"
            "        devolver list(super().__iter__())\n"
            "mp = MiPar(10, 20)\n"
            "imprimir(mp.como_lista())\n",
        )

    def test_field_index_len_and_membership(self):
        _assert_matches(
            self,
            PRE
            + "p = Punto(3, 4)\n"
            + "imprimir(p.x, p.y, p[0], p[-1], len(p))\n"
            + "imprimir(tuple(p), list(p), 4 en p, 5 en p)\n",
        )

    def test_keyword_and_mixed_construction(self):
        _assert_matches(
            self,
            PRE
            + "p = Punto(x=3, y=4)\n"
            + "q = Punto(3, y=4)\n"
            + "imprimir(p.x, p.y, q.x, q.y)\n",
        )

    def test_arity_errors_are_type_errors(self):
        _assert_matches(
            self,
            PRE
            + "intentar:\n    Punto(1)\nexcepto TypeError:\n    imprimir('faltan')\n"
            + "intentar:\n    Punto(1, 2, 3)\nexcepto TypeError:\n    imprimir('sobran')\n",
        )

    def test_index_out_of_range_is_index_error(self):
        _assert_matches(
            self,
            PRE
            + "p = Punto(3, 4)\n"
            + "intentar:\n    p[5]\nexcepto IndexError:\n    imprimir('IE')\n",
        )

    def test_equality_with_tuple_and_namedtuple(self):
        _assert_matches(
            self,
            PRE
            + "p = Punto(3, 4)\n"
            + "imprimir(p == (3, 4), p == (3, 5), p != (3, 4))\n"
            + "imprimir(p == Punto(3, 4), p == Punto(3, 5))\n",
        )

    def test_asdict_fields_and_repr(self):
        _assert_matches(
            self,
            PRE
            + "p = Punto(3, 4)\n"
            + "imprimir(p._asdict(), p._fields)\n"
            + "imprimir(repr(p), str(p))\n",
        )

    def test_field_assignment_raises_but_extra_attribute_is_allowed(self):
        _assert_matches(
            self,
            PRE
            + "p = Punto(3, 4)\n"
            + "intentar:\n    p.x = 5\nexcepto AttributeError como e:\n    imprimir('AE', e)\n"
            + "p.extra = 9\n"
            + "imprimir(p.extra, p.x)\n",
        )

    def test_missing_attribute_message_uses_subclass_name(self):
        _assert_matches(
            self,
            PRE
            + "p = Punto(3, 4)\n"
            + "intentar:\n    p.z\nexcepto AttributeError como e:\n    imprimir('AE', e)\n",
        )

    def test_custom_methods_and_super_iter_on_subclass(self):
        _assert_matches(
            self,
            PRE
            + "clase Q(Base):\n"
            + "    funcion suma(self):\n"
            + "        devolver self.x + self.y\n"
            + "    funcion como_lista(self):\n"
            + "        devolver list(super().__iter__())\n"
            + "q = Q(3, 4)\n"
            + "imprimir(q.suma(), q.como_lista(), tuple(q))\n",
        )

    def test_count_and_index(self):
        _assert_matches(
            self,
            PRE
            + "p = Punto(3, 4)\n"
            + "imprimir(p.count(3), p.index(4))\n",
        )


class NamedtupleSubclassFailClosed(unittest.TestCase):
    def test_unpacking_subclass_instance_fails_closed(self):
        with pytest.raises(NativeBuildError, match="unpack of user iterable object 'Punto'"):
            compare_native_to_cpython(PRE + "p = Punto(3, 4)\na, b = p\nimprimir(a, b)\n")

    def test_slicing_subclass_instance_fails_closed(self):
        with pytest.raises(NativeBuildError, match="slicing of user object 'Punto'"):
            compare_native_to_cpython(PRE + "p = Punto(3, 4)\nimprimir(list(p[0:1]))\n")

    def test_subclass_with_init_still_fails_closed(self):
        with pytest.raises(NativeBuildError, match="super"):
            compare_native_to_cpython(
                PRE
                + "clase K(Base):\n"
                + "    funcion __init__(self, a, b):\n"
                + "        super().__init__(a, b)\n"
                + "k = K(3, 4)\n"
                + "imprimir(k.x)\n"
            )
