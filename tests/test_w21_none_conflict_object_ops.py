"""W21 none_conflict_object_ops: None-conflict locals whose other values are user objects.

A local first bound to None and later rebound to an instance of a user class
(`object:C`) inside a loop or branch is dynamic (NONE_CONFLICT_SLOT_V1). Its
attribute reads, attribute writes and method calls dispatch at runtime by the
box kind: PK_OBJECT dispatches to the class; PK_NONE raises CPython's
AttributeError. Parity with CPython 3.12 (stdout + exit code, or the same
exception) is required; cases that cannot be matched must keep failing closed.
"""
from __future__ import annotations

import unittest

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


def _assert_fails_closed(testcase: unittest.TestCase, source: str) -> None:
    with testcase.assertRaises(NativeBuildError):
        compare_native_to_cpython(source)


PUNTO = (
    "clase Punto:\n"
    "    funcion __init__(self, x, y):\n"
    "        self.x = x\n"
    "        self.y = y\n"
    "    funcion norma1(self):\n"
    "        devolver self.x + self.y\n"
)


class NoneConflictObjectOpsParity(unittest.TestCase):
    # Exact reproducers (/var/tmp/piton_w21_orch/repro/none_conflict_object_ops.py).
    def test_reproducer_get_attr_and_method_call_after_loop(self):
        _assert_matches(
            self,
            "clase Punto:\n"
            "    funcion __init__(self, x, y):\n"
            "        self.x = x\n"
            "        self.y = y\n"
            "    funcion norma1(self):\n"
            "        devolver self.x + self.y\n"
            "funcion buscar(xs):\n"
            "    mejor = None\n"
            "    para v en xs:\n"
            "        si v > 0:\n"
            "            mejor = Punto(v, v * 2)\n"
            "    si mejor no es None:\n"
            "        devolver (mejor.x, mejor.y, mejor.norma1())\n"
            "    devolver (-1, -1, -1)\n"
            "imprimir(buscar([-1, 3, -2]), buscar([-1, -2]))\n",
        )

    def test_reproducer_set_attr_and_get_attr_in_branch(self):
        _assert_matches(
            self,
            "clase Caja:\n"
            "    funcion __init__(self, v):\n"
            "        self.v = v\n"
            "funcion ajustar(flag):\n"
            "    c = None\n"
            "    si flag:\n"
            "        c = Caja(10)\n"
            "        c.v = c.v + 5\n"
            "    si c es None:\n"
            "        devolver 0\n"
            "    devolver c.v\n"
            "imprimir(ajustar(Verdadero), ajustar(Falso))\n",
        )

    # Constructor argument that is a dynamic (tagged) loop value: the __init__
    # parameter must receive the value, not the address of its box.
    def test_constructor_with_dynamic_loop_value_stores_value(self):
        _assert_matches(
            self,
            "clase Punto:\n"
            "    funcion __init__(self, x):\n"
            "        self.x = x\n"
            "funcion f(xs):\n"
            "    r = Punto(xs[0])\n"
            "    devolver r.x\n"
            "imprimir(f([3, 6]))\n",
        )

    def test_constructor_with_loop_element_in_none_conflict(self):
        _assert_matches(
            self,
            PUNTO
            + "funcion f(xs):\n"
            "    m = None\n"
            "    para v en xs:\n"
            "        m = Punto(v, v)\n"
            "    devolver (m.x, m.norma1())\n"
            "imprimir(f([4, 9]))\n",
        )

    # Uncaught AttributeError on the None path: stdout before the error and the exit code.
    def test_uncaught_attribute_error_on_none_path_after_branch(self):
        _assert_matches(
            self,
            PUNTO
            + "funcion f(flag):\n"
            "    m = None\n"
            "    si flag:\n"
            "        m = Punto(4, 1)\n"
            "    devolver m.x\n"
            "imprimir(f(Verdadero))\n"
            "imprimir(f(Falso))\n",
        )

    def test_uncaught_method_call_on_none_path(self):
        _assert_matches(
            self,
            PUNTO
            + "funcion f(flag):\n"
            "    m = None\n"
            "    si flag:\n"
            "        m = Punto(1, 2)\n"
            "    devolver m.norma1()\n"
            "imprimir(f(Verdadero))\n"
            "imprimir(f(Falso))\n",
        )

    def test_caught_attribute_error_message_on_none_path(self):
        _assert_matches(
            self,
            PUNTO
            + "funcion f(flag):\n"
            "    m = None\n"
            "    para i en range(2):\n"
            "        si flag:\n"
            "            m = Punto(i, i)\n"
            "    intentar:\n"
            "        imprimir(m.x)\n"
            "    excepto AttributeError como e:\n"
            "        imprimir(e)\n"
            "f(Falso)\n"
            "f(Verdadero)\n",
        )

    def test_set_attr_on_none_path_caught(self):
        _assert_matches(
            self,
            PUNTO
            + "funcion f(flag):\n"
            "    m = None\n"
            "    si flag:\n"
            "        m = Punto(1, 2)\n"
            "    intentar:\n"
            "        m.x = 5\n"
            "    excepto AttributeError como e:\n"
            "        imprimir('AE', e)\n"
            "    devolver 0\n"
            "imprimir(f(Falso))\n"
            "imprimir(f(Verdadero))\n",
        )

    def test_method_with_positional_and_keyword_args(self):
        _assert_matches(
            self,
            "clase C:\n"
            "    funcion __init__(self, k):\n"
            "        self.k = k\n"
            "    funcion sumar(self, a, b=10):\n"
            "        devolver self.k + a + b\n"
            "funcion f(xs):\n"
            "    r = None\n"
            "    para v en xs:\n"
            "        r = C(v)\n"
            "    si r es None:\n"
            "        devolver -1\n"
            "    devolver (r.sumar(1), r.sumar(2, b=3))\n"
            "imprimir(f([5, 7]), f([]))\n",
        )

    def test_mutating_method_in_loop_then_read(self):
        _assert_matches(
            self,
            "clase Acum:\n"
            "    funcion __init__(self):\n"
            "        self.total = 0\n"
            "    funcion add(self, v):\n"
            "        self.total = self.total + v\n"
            "        devolver self.total\n"
            "funcion f(xs):\n"
            "    a = None\n"
            "    para v en xs:\n"
            "        si a es None:\n"
            "            a = Acum()\n"
            "        a.add(v)\n"
            "    devolver a.total\n"
            "imprimir(f([1, 2, 3]), f([]))\n",
        )

    def test_missing_attribute_on_object_path_raises_attribute_error(self):
        # A name that is neither a field set in __init__ nor a method must raise
        # CPython's AttributeError, not read whatever the object model returns.
        _assert_matches(
            self,
            PUNTO
            + "funcion f(c):\n"
            "    m = None\n"
            "    si c:\n"
            "        m = Punto(1, 2)\n"
            "    intentar:\n"
            "        devolver m.zzz\n"
            "    excepto AttributeError como e:\n"
            "        devolver str(e)\n"
            "imprimir(f(Verdadero))\n",
        )

    def test_missing_method_on_object_path_caught(self):
        _assert_matches(
            self,
            PUNTO
            + "funcion f(flag):\n"
            "    m = None\n"
            "    si flag:\n"
            "        m = Punto(1, 2)\n"
            "    intentar:\n"
            "        imprimir(m.zzz())\n"
            "    excepto AttributeError como e:\n"
            "        imprimir(e)\n"
            "f(Falso)\n"
            "f(Verdadero)\n",
        )

    def test_bound_method_value_from_none_conflict(self):
        _assert_matches(
            self,
            "clase Punto:\n"
            "    funcion __init__(self, x):\n"
            "        self.x = x\n"
            "    funcion doble(self):\n"
            "        devolver self.x * 2\n"
            "funcion f(flag):\n"
            "    m = None\n"
            "    si flag:\n"
            "        m = Punto(21)\n"
            "    g = m.doble\n"
            "    devolver g()\n"
            "imprimir(f(Verdadero))\n",
        )

    def test_method_mutates_field_then_set_and_read_in_loop(self):
        _assert_matches(
            self,
            "clase Punto:\n"
            "    funcion __init__(self, x):\n"
            "        self.x = x\n"
            "    funcion mover(self, d):\n"
            "        self.x = self.x + d\n"
            "funcion f(xs):\n"
            "    m = None\n"
            "    para d en xs:\n"
            "        si m es None:\n"
            "            m = Punto(0)\n"
            "        m.mover(d)\n"
            "        m.x = m.x * 10\n"
            "    devolver m.x\n"
            "imprimir(f([1, 2]), f([]))\n",
        )

    def test_object_and_int_branches_matches_for_attribute_free_use(self):
        _assert_matches(
            self,
            PUNTO
            + "funcion f(c):\n"
            "    m = None\n"
            "    si c:\n"
            "        m = Punto(2, 3)\n"
            "    sino:\n"
            "        m = 3\n"
            "    si m es None:\n"
            "        devolver -1\n"
            "    devolver 0\n"
            "imprimir(f(Verdadero), f(Falso))\n",
        )

    # Negative / fail-closed cases.
    def test_two_user_classes_on_one_none_conflict_variable_fails_closed(self):
        _assert_fails_closed(
            self,
            "clase A:\n"
            "    funcion __init__(self):\n"
            "        self.v = 1\n"
            "clase B:\n"
            "    funcion __init__(self):\n"
            "        self.v = 2\n"
            "funcion f(c):\n"
            "    m = None\n"
            "    si c:\n"
            "        m = A()\n"
            "    sino:\n"
            "        m = B()\n"
            "    devolver m.v\n"
            "imprimir(f(Verdadero))\n",
        )

    def test_module_level_object_receiver_stays_fail_closed(self):
        # Module-level None-conflict receivers of user classes keep the W20
        # contract (test_w20_none_init_loop.test_object_receiver_on_none_conflict_variable_fails_closed).
        _assert_fails_closed(
            self,
            "clase P:\n"
            "    funcion __init__(self):\n"
            "        self.v = 1\n"
            "m = None\n"
            "para i en range(2):\n"
            "    m = P()\n"
            "imprimir(m.v)\n",
        )


if __name__ == "__main__":
    unittest.main()
