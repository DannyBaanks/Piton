"""Parity tests for del_slice and get_slice on None-conflict / dynamic variables.

These tests cover the W22 task: support get_slice and del_slice on None-conflict
/ dynamic variables with CPython parity (raising TypeError when the runtime
value is None or non-subscriptable, and slicing/deleting when it is
str/list/tuple).
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


class DelGetSliceNoneConflict(unittest.TestCase):
    # --- del_slice on None-conflict variable ---
    
    def test_del_slice_none_conflict_list(self):
        """del_slice on variable that is None in one branch, list in another."""
        _assert_matches(
            self,
            "funcion recortar(flag):\n"
            "    buf = None\n"
            "    si flag:\n"
            "        buf = [1, 2, 3, 4]\n"
            "    si flag:\n"
            "        borrar buf[1:3]\n"
            "    devolver buf\n"
            "imprimir(recortar(Verdadero), recortar(Falso))\n",
        )

    def test_del_slice_none_conflict_str(self):
        """del_slice on variable that is None in one branch, str in another.
        
        CPython raises TypeError: 'str' object does not support item deletion
        """
        _assert_matches(
            self,
            "funcion test(flag):\n"
            "    s = None\n"
            "    si flag:\n"
            "        s = 'hola'\n"
            "    si flag:\n"
            "        borrar s[1:3]\n"
            "    devolver s\n"
            "imprimir(test(Verdadero), test(Falso))\n",
        )

    def test_del_slice_none_conflict_tuple(self):
        """del_slice on variable that is None in one branch, tuple in another.
        
        CPython raises TypeError: 'tuple' object does not support item deletion
        """
        _assert_matches(
            self,
            "funcion test(flag):\n"
            "    t = None\n"
            "    si flag:\n"
            "        t = (1, 2, 3, 4)\n"
            "    si flag:\n"
            "        borrar t[1:3]\n"
            "    devolver t\n"
            "imprimir(test(Verdadero), test(Falso))\n",
        )

    def test_del_slice_none_conflict_bytes(self):
        """del_slice on variable that is None in one branch, bytes in another.
        
        CPython raises TypeError: 'bytes' object does not support item deletion
        """
        _assert_matches(
            self,
            "funcion test(flag):\n"
            "    b = None\n"
            "    si flag:\n"
            "        b = b'hola'\n"
            "    si flag:\n"
            "        borrar b[1:3]\n"
            "    devolver b\n"
            "imprimir(test(Verdadero), test(Falso))\n",
        )

    def test_del_slice_none_conflict_bytearray(self):
        """del_slice on variable that is None in one branch, bytearray in another.
        
        bytearray supports item deletion.
        """
        _assert_matches(
            self,
            "funcion test(flag):\n"
            "    b = None\n"
            "    si flag:\n"
            "        b = bytearray(b'hola')\n"
            "    si flag:\n"
            "        borrar b[1:3]\n"
            "    devolver b\n"
            "imprimir(test(Verdadero), test(Falso))\n",
        )

    # --- get_slice on None-conflict variable ---
    
    def test_get_slice_none_conflict_str(self):
        """get_slice on variable that is None in one branch, str in another."""
        _assert_matches(
            self,
            "funcion prefijo(flag):\n"
            "    filename = None\n"
            "    si flag:\n"
            "        filename = 'archivo.py'\n"
            "    si flag:\n"
            "        devolver filename[:3]\n"
            "    devolver ''\n"
            "imprimir(prefijo(Verdadero), prefijo(Falso))\n",
        )

    def test_get_slice_none_conflict_list(self):
        """get_slice on variable that is None in one branch, list in another."""
        _assert_matches(
            self,
            "funcion test(flag):\n"
            "    lst = None\n"
            "    si flag:\n"
            "        lst = [1, 2, 3, 4]\n"
            "    si flag:\n"
            "        devolver lst[1:3]\n"
            "    devolver []\n"
            "imprimir(test(Verdadero), test(Falso))\n",
        )

    def test_get_slice_none_conflict_tuple(self):
        """get_slice on variable that is None in one branch, tuple in another."""
        _assert_matches(
            self,
            "funcion test(flag):\n"
            "    t = None\n"
            "    si flag:\n"
            "        t = (1, 2, 3, 4)\n"
            "    si flag:\n"
            "        devolver t[1:3]\n"
            "    devolver ()\n"
            "imprimir(test(Verdadero), test(Falso))\n",
        )

    def test_get_slice_none_conflict_bytes(self):
        """get_slice on variable that is None in one branch, bytes in another."""
        _assert_matches(
            self,
            "funcion test(flag):\n"
            "    b = None\n"
            "    si flag:\n"
            "        b = b'hola'\n"
            "    si flag:\n"
            "        devolver b[1:3]\n"
            "    devolver b''\n"
            "imprimir(test(Verdadero), test(Falso))\n",
        )

    def test_get_slice_none_conflict_bytearray(self):
        """get_slice on variable that is None in one branch, bytearray in another."""
        _assert_matches(
            self,
            "funcion test(flag):\n"
            "    b = None\n"
            "    si flag:\n"
            "        b = bytearray(b'hola')\n"
            "    si flag:\n"
            "        devolver b[1:3]\n"
            "    devolver bytearray()\n"
            "imprimir(test(Verdadero), test(Falso))\n",
        )

    def test_get_slice_none_conflict_range(self):
        """get_slice on variable that is None in one branch, range in another."""
        _assert_matches(
            self,
            "funcion test(flag):\n"
            "    r = None\n"
            "    si flag:\n"
            "        r = range(10)\n"
            "    si flag:\n"
            "        devolver r[1:3]\n"
            "    devolver range(0)\n"
            "imprimir(test(Verdadero), test(Falso))\n",
        )

    # --- Dynamic variable (reassigned in loop) ---
    
    def test_del_slice_dynamic_in_loop(self):
        """del_slice on variable reassigned in a loop."""
        _assert_matches(
            self,
            "funcion test():\n"
            "    buf = None\n"
            "    i = 0\n"
            "    mientras i < 3:\n"
            "        si i == 1:\n"
            "            buf = [1, 2, 3, 4]\n"
            "        si no buf es None:\n"
            "            borrar buf[1:2]\n"
            "        i = i + 1\n"
            "    devolver buf\n"
            "imprimir(test())\n",
        )

    def test_get_slice_dynamic_in_loop(self):
        """get_slice on variable reassigned in a loop."""
        _assert_matches(
            self,
            "funcion test():\n"
            "    filename = None\n"
            "    i = 0\n"
            "    mientras i < 3:\n"
            "        si i == 1:\n"
            "            filename = 'archivo.py'\n"
            "        si no filename es None:\n"
            "            devolver filename[:3]\n"
            "        i = i + 1\n"
            "    devolver ''\n"
            "imprimir(test())\n",
        )

    # --- Edge cases: user class instances (should have CPython parity at runtime) ---
    
    def test_del_slice_object_receiver_parity(self):
        """del_slice on a user class instance should raise TypeError at runtime (CPython parity)."""
        _assert_matches(
            self,
            "clase MiLista:\n"
            "    pass\n"
            "funcion test():\n"
            "    x = None\n"
            "    si Verdadero:\n"
            "        x = MiLista()\n"
            "    borrar x[1:3]\n"
            "    devolver x\n"
            "imprimir(test())\n",
        )

    def test_get_slice_object_receiver_parity(self):
        """get_slice on a user class instance should raise TypeError at runtime (CPython parity)."""
        _assert_matches(
            self,
            "clase MiLista:\n"
            "    pass\n"
            "funcion test():\n"
            "    x = None\n"
            "    si Verdadero:\n"
            "        x = MiLista()\n"
            "    devolver x[:3]\n"
            "imprimir(test())\n",
        )


if __name__ == "__main__":
    unittest.main()