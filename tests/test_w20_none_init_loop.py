"""W20 none_init_loop: locals first bound to None and rebound in loops/branches.

Parity with CPython 3.12 (stdout + exit code, or the same exception) for
variables whose static type came out as "none" from the initial binding.
Cases that cannot be dispatched correctly must keep failing closed.
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


class NoneInitLoopParity(unittest.TestCase):
    # Exact reproducers (/var/tmp/piton_w20_orch/repro/none_init_loop.py).
    def test_reproducer_none_then_first_in_function_loop(self):
        _assert_matches(
            self,
            "funcion f(xs):\n"
            "    m = None\n"
            "    para ind en xs:\n"
            "        si m es None:\n"
            "            m = ind\n"
            "    devolver m\n"
            "imprimir(f(['  a', '   b']))\n",
        )

    def test_reproducer_none_then_max_comparison(self):
        _assert_matches(
            self,
            "funcion f(xs):\n"
            "    mejor = None\n"
            "    para x en xs:\n"
            "        si mejor es None o x > mejor:\n"
            "            mejor = x\n"
            "    devolver mejor\n"
            "imprimir(f([3, 9, 2]), f([]))\n",
        )

    def test_reproducer_none_then_str_concat_module_level(self):
        _assert_matches(
            self,
            "r = None\n"
            "para p en ['a', 'b']:\n"
            "    r = p si r es None sino r + p\n"
            "imprimir(r)\n",
        )

    def test_reproducer_none_then_list_append(self):
        _assert_matches(
            self,
            "acc = None\n"
            "para i en range(3):\n"
            "    si acc es None:\n"
            "        acc = []\n"
            "    acc.append(i)\n"
            "imprimir(acc)\n",
        )

    # Branches (static "none" from the first store, rebound in an `si`).
    def test_branch_rebind_in_function_taken_and_not_taken(self):
        _assert_matches(
            self,
            "funcion f(c):\n"
            "    m = None\n"
            "    si c:\n"
            "        m = 7\n"
            "    devolver m\n"
            "imprimir(f(Verdadero), f(Falso))\n",
        )

    def test_branch_rebind_module_level_not_taken(self):
        _assert_matches(
            self,
            "m = None\n"
            "si Falso:\n"
            "    m = 7\n"
            "imprimir(m es None)\n"
            "imprimir(m)\n",
        )

    def test_branch_rebind_module_level_taken(self):
        _assert_matches(
            self,
            "m = None\n"
            "si Verdadero:\n"
            "    m = 7\n"
            "imprimir(m)\n"
            "imprimir(m es None)\n",
        )

    def test_straight_line_none_then_int_in_function(self):
        _assert_matches(
            self,
            "funcion f():\n"
            "    x = None\n"
            "    x = 5\n"
            "    devolver x\n"
            "imprimir(f())\n",
        )

    def test_none_then_int_accumulates_in_loop(self):
        _assert_matches(
            self,
            "t = None\n"
            "para i en range(3):\n"
            "    si t es None:\n"
            "        t = 0\n"
            "    t = t + i\n"
            "imprimir(t)\n",
        )

    def test_none_then_mientras_loop_still_matches(self):
        _assert_matches(
            self,
            "m = None\n"
            "c = 0\n"
            "mientras c < 3:\n"
            "    si m es None:\n"
            "        m = c\n"
            "    c = c + 1\n"
            "imprimir(m)\n",
        )

    def test_none_equality_on_dynamic_value(self):
        _assert_matches(
            self,
            "funcion f(xs):\n"
            "    m = None\n"
            "    para v en xs:\n"
            "        m = v\n"
            "    devolver m == None\n"
            "imprimir(f([1]), f([]))\n",
        )

    # Receiver dispatch by runtime kind: list, dict, str, int.
    def test_list_receiver_store_and_len_after_loop(self):
        _assert_matches(
            self,
            "acc = None\n"
            "para i en range(2):\n"
            "    si acc es None:\n"
            "        acc = []\n"
            "    acc.append(i)\n"
            "imprimir(len(acc))\n",
        )

    def test_dict_receiver_subscript_store(self):
        _assert_matches(
            self,
            "d = None\n"
            "para k en ['a']:\n"
            "    d = {}\n"
            "    d[k] = 1\n"
            "imprimir(d)\n",
        )

    def test_list_receiver_subscript_store(self):
        _assert_matches(
            self,
            "d = None\n"
            "para k en ['a']:\n"
            "    d = [1, 2]\n"
            "    d[0] = 9\n"
            "imprimir(d)\n",
        )

    def test_str_receiver_method_call(self):
        _assert_matches(
            self,
            "s = None\n"
            "para p en ['ab', 'cd']:\n"
            "    s = p\n"
            "imprimir(s.upper())\n",
        )

    def test_int_receiver_attribute_read(self):
        _assert_matches(
            self,
            "d = None\n"
            "para k en ['a']:\n"
            "    d = 3\n"
            "imprimir(d.real)\n",
        )

    # Error paths: the None branch raises the same exception as CPython.
    def test_uncaught_attribute_error_on_still_none_variable(self):
        _assert_matches(
            self,
            "m = None\n"
            "para i en range(0):\n"
            "    m = [1]\n"
            "imprimir('antes')\n"
            "m.append(2)\n"
            "imprimir('despues')\n",
        )

    def test_caught_attribute_error_message_matches(self):
        _assert_matches(
            self,
            "m = None\n"
            "para i en range(0):\n"
            "    m = [1]\n"
            "intentar:\n"
            "    m.append(2)\n"
            "excepto AttributeError como e:\n"
            "    imprimir(e)\n",
        )

    def test_caught_type_error_on_none_item_assignment(self):
        _assert_matches(
            self,
            "d = None\n"
            "para i en range(2):\n"
            "    d = {}\n"
            "intentar:\n"
            "    d[1] = 2\n"
            "excepto TypeError como e:\n"
            "    imprimir('TE')\n",
        )

    # Edge: a rebinding whose type is unknown (loop target from a parameter)
    # must not be guessed; it either matches CPython or fails closed.
    def test_unknown_rebinding_matches_or_fails_closed(self):
        source = (
            "funcion f(xs):\n"
            "    m = None\n"
            "    para v en xs:\n"
            "        m = v\n"
            "    imprimir(m)\n"
            "    devolver m\n"
            "f([1])\n"
        )
        try:
            result = compare_native_to_cpython(source)
        except NativeBuildError:
            return
        self.assertEqual(result.native.returncode, result.oracle.returncode)
        self.assertEqual(result.native.stdout, result.oracle.stdout)

    # Fail-closed cases: operations without dynamic dispatch on a
    # None-conflict variable must not emit C that silently does something else.
    def test_del_item_on_none_conflict_variable_fails_closed(self):
        _assert_fails_closed(
            self,
            "m = None\n"
            "para i en range(2):\n"
            "    m = [1, 2]\n"
            "borrar m[0]\n"
            "imprimir(m)\n",
        )

    def test_object_receiver_on_none_conflict_variable_fails_closed(self):
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
