"""Parity checks for user subclasses of builtin list/dict (W21 super_builtin_collection_base).

Parity = byte-identical stdout and identical exit code against CPython 3.12.
Constructs that the native backend cannot match must fail closed with
NativeBuildError at build time; they must never build and print something else.
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


# Reproducers from /var/tmp/piton_w21_orch/repro/super_builtin_collection_base.py
REPRO_SUBCLASS_LIST_SUPER = (
    "clase MiLista(list):\n"
    "    funcion __init__(self, xs):\n"
    "        super().__init__(xs)\n"
    "    funcion agregar_doble(self, x):\n"
    "        super().append(x)\n"
    "        super().append(x * 2)\n"
    "ml = MiLista([1, 2])\n"
    "ml.agregar_doble(5)\n"
    "imprimir(list(ml), len(ml))\n"
)
REPRO_SUBCLASS_DICT_SUPER = (
    "clase MiDict(dict):\n"
    "    funcion claves_ordenadas(self):\n"
    "        devolver sorted(list(super().keys()))\n"
    "md = MiDict({'b': 2, 'a': 1})\n"
    "imprimir(md.claves_ordenadas(), md['a'])\n"
)


class SuperBuiltinCollectionParity(unittest.TestCase):
    def test_reproducer_list_subclass_super_init_append(self):
        _assert_matches(self, REPRO_SUBCLASS_LIST_SUPER)

    def test_reproducer_dict_subclass_super_keys(self):
        _assert_matches(self, REPRO_SUBCLASS_DICT_SUPER)

    def test_list_subclass_len_iter_repr_getitem_contains(self):
        _assert_matches(
            self,
            "clase L(list):\n"
            "    funcion __init__(self, xs):\n"
            "        super().__init__(xs)\n"
            "l = L([3, 1, 2])\n"
            "imprimir(len(l), repr(l), str(l))\n"
            "para x en l:\n"
            "    imprimir(x)\n"
            "imprimir(l[0], l[-1], 2 in l, 9 in l)\n"
            "l[0] = 7\n"
            "imprimir(l[0], list(l))\n",
        )

    def test_list_subclass_default_ctor_and_empty_ctor(self):
        _assert_matches(
            self,
            "clase L(list):\n"
            "    pasar\n"
            "a = L([1, 2, 3])\n"
            "b = L()\n"
            "b.append(4)\n"
            "imprimir(list(a), len(a), list(b))\n"
            "imprimir(isinstance(a, list), isinstance(a, dict))\n",
        )

    def test_list_subclass_super_index_count_pop_getitem(self):
        _assert_matches(
            self,
            "clase L(list):\n"
            "    funcion pos(self, x):\n"
            "        devolver super().index(x)\n"
            "    funcion cuenta(self, x):\n"
            "        devolver super().count(x)\n"
            "    funcion quitar_primero(self):\n"
            "        devolver super().pop(0)\n"
            "    funcion primero(self):\n"
            "        devolver super().__getitem__(0)\n"
            "l = L([1, 2, 2])\n"
            "imprimir(l.pos(2), l.cuenta(2), l.primero())\n"
            "imprimir(l.quitar_primero(), list(l))\n",
        )

    def test_list_subclass_user_method_overrides_append(self):
        _assert_matches(
            self,
            "clase L(list):\n"
            "    funcion append(self, x):\n"
            "        super().append(x * 10)\n"
            "l = L()\n"
            "l.append(2)\n"
            "imprimir(list(l))\n",
        )

    def test_indirect_list_subclass_inherits_collection_members(self):
        _assert_matches(
            self,
            "clase A(list):\n"
            "    funcion __init__(self, xs):\n"
            "        super().__init__(xs)\n"
            "clase B(A):\n"
            "    funcion total(self):\n"
            "        devolver sum(self)\n"
            "b = B([1, 2, 3])\n"
            "b.append(4)\n"
            "imprimir(b.total(), len(b), list(b))\n",
        )

    def test_list_subclass_sum_sorted_copy_eq_bool(self):
        _assert_matches(
            self,
            "clase L(list):\n"
            "    pasar\n"
            "ints = L([1, 2, 3])\n"
            "floats = L([1.5, 2.0])\n"
            "imprimir(sum(ints), sum(floats))\n"
            "imprimir(sorted(L([3, 1, 2])))\n"
            "c = ints.copy()\n"
            "ints.append(9)\n"
            "imprimir(c, list(ints))\n"
            "imprimir(ints == [1, 2, 3, 9], ints != [1, 2, 3])\n"
            "si L():\n"
            "    imprimir('no')\n"
            "sino:\n"
            "    imprimir('vacia')\n",
        )

    def test_dict_subclass_get_items_values_setdefault_update(self):
        _assert_matches(
            self,
            "clase D(dict):\n"
            "    funcion relleno(self):\n"
            "        super().update({'z': 26})\n"
            "        devolver super().setdefault('z', 0)\n"
            "    funcion obtener(self, k):\n"
            "        devolver super().get(k, 'no')\n"
            "d = D({'a': 1})\n"
            "d['b'] = 2\n"
            "imprimir(len(d), d['a'], d['b'], 'a' in d)\n"
            "imprimir(d.get('a'), d.get('q'), d.get('q', 5))\n"
            "imprimir(list(d.items()), list(d.values()))\n"
            "imprimir(d.relleno(), d['z'], d.obtener('a'), d.obtener('q'))\n"
            "imprimir(sorted(d.keys()), sorted(d))\n",
        )

    def test_dict_subclass_pop_del_copy_clear_iteration(self):
        _assert_matches(
            self,
            "clase D(dict):\n"
            "    funcion sacar(self, k):\n"
            "        devolver super().pop(k)\n"
            "d = D({'a': 1, 'b': 2, 'c': 3})\n"
            "imprimir(d.sacar('a'), len(d))\n"
            "borrar d['b']\n"
            "c = d.copy()\n"
            "para k en d:\n"
            "    imprimir(k)\n"
            "d.clear()\n"
            "imprimir(len(d), c)\n",
        )

    def test_dict_subclass_missing_key_raises_keyerror(self):
        _assert_matches(
            self,
            "clase D(dict):\n"
            "    pasar\n"
            "d = D()\n"
            "imprimir(d['nope'])\n",
        )

    def test_dict_subclass_sorted_items_and_repr(self):
        _assert_matches(
            self,
            "clase D(dict):\n"
            "    pasar\n"
            "d = D({'b': 1, 'a': 2})\n"
            "imprimir(sorted(d.items()))\n"
            "imprimir(repr(d), str(d))\n"
            "imprimir(d == {'b': 1, 'a': 2}, d != {'b': 1})\n",
        )

    def test_subclass_of_subclass_inherits_dict_members(self):
        _assert_matches(
            self,
            "clase D(dict):\n"
            "    pasar\n"
            "clase E(D):\n"
            "    funcion n(self):\n"
            "        devolver len(self)\n"
            "e = E({'x': 1, 'y': 2})\n"
            "imprimir(e.n(), e['x'], sorted(e.keys()))\n",
        )

    def test_user_list_subclass_passed_to_builtins_stays_typed(self):
        _assert_matches(
            self,
            "clase L(list):\n"
            "    pasar\n"
            "l = L([1, 2])\n"
            "imprimir(l)\n"
            "imprimir([l])\n"
            "imprimir(len(l), list(l), sum(l))\n"
            "xs = [l]\n"
            "imprimir(len(xs[0]))\n",
        )


class SuperBuiltinCollectionFailClosed(unittest.TestCase):
    def test_passing_subclass_to_user_function_fails_closed(self):
        with pytest.raises(NativeBuildError, match="passing a list/dict subclass instance to 'total'"):
            compare_native_to_cpython(
                "clase L(list):\n    pasar\n"
                "funcion total(xs):\n    devolver sum(xs)\n"
                "l = L([1, 2, 3])\nimprimir(total(l))\n"
            )

    def test_storing_subclass_with_append_fails_closed(self):
        with pytest.raises(NativeBuildError, match="storing a list/dict subclass instance with 'append'"):
            compare_native_to_cpython(
                "clase L(list):\n    pasar\n"
                "xs = []\nl = L([7])\nxs.append(l)\nimprimir(len(xs[0]))\n"
            )

    def test_map_over_subclass_instances_fails_closed(self):
        with pytest.raises(NativeBuildError, match="map\\(\\) over list/dict subclass instances"):
            compare_native_to_cpython(
                "clase L(list):\n    pasar\n"
                "l = L([1, 2])\nimprimir(list(map(len, [l])))\n"
            )

    def test_enumerate_over_subclass_fails_closed(self):
        with pytest.raises(NativeBuildError, match="enumerate\\(\\) over list/dict subclass"):
            compare_native_to_cpython(
                "clase L(list):\n    pasar\n"
                "l = L([5, 6])\npara i, x en enumerate(l):\n    imprimir(i, x)\n"
            )

    def test_reversed_over_subclass_fails_closed(self):
        with pytest.raises(NativeBuildError, match="reversed\\(\\) over list/dict subclass"):
            compare_native_to_cpython(
                "clase L(list):\n    pasar\n"
                "l = L([5, 6])\nimprimir(list(reversed(l)))\n"
            )

    def test_dict_item_store_of_subclass_fails_closed(self):
        with pytest.raises(NativeBuildError, match="storing a list/dict subclass instance into an item"):
            compare_native_to_cpython(
                "clase L(list):\n    pasar\n"
                "d = {}\nl = L([1])\nd['k'] = l\nimprimir(len(d['k']))\n"
            )

    def test_attribute_store_of_subclass_fails_closed(self):
        with pytest.raises(NativeBuildError, match="storing a list/dict subclass instance into attribute 'v'"):
            compare_native_to_cpython(
                "clase L(list):\n    pasar\n"
                "clase H(object):\n    pasar\n"
                "h = H()\nl = L([1, 2])\nh.v = l\nimprimir(len(h.v))\n"
            )

    def test_user_dunder_override_on_collection_subclass_fails_closed(self):
        # W22 dict_subclass_dunder_overrides added CPython-parity __missing__,
        # __getitem__, and __setitem__ overrides on dict subclasses; other
        # dunders such as __repr__ on list/dict subclasses still fail closed.
        missing_res = compare_native_to_cpython(
            "clase D(dict):\n"
            "    funcion __missing__(self, k):\n"
            "        devolver 0\n"
            "d = D()\nimprimir(d['q'])\n"
        )
        self.assertEqual(missing_res.native.returncode, missing_res.oracle.returncode)
        self.assertEqual(missing_res.native.stdout, missing_res.oracle.stdout)
        with pytest.raises(NativeBuildError, match="overriding '__repr__'"):
            compare_native_to_cpython(
                "clase L(list):\n"
                "    funcion __repr__(self):\n"
                "        devolver 'MI'\n"
                "l = L([1])\nimprimir(l)\n"
            )

    def test_keyword_constructor_and_super_keyword_fail_closed(self):
        with pytest.raises(NativeBuildError, match="constructor with keyword arguments"):
            compare_native_to_cpython("clase D(dict):\n    pasar\nd = D(a=1)\nimprimir(d['a'])\n")
        with pytest.raises(NativeBuildError, match="super\\(\\).get\\(\\) with keyword arguments"):
            compare_native_to_cpython(
                "clase D(dict):\n"
                "    funcion f(self):\n"
                "        devolver super().get('a', default=3)\n"
                "d = D()\nimprimir(d.f())\n"
            )

    def test_list_subclass_pop_with_index_fails_closed(self):
        with pytest.raises(NativeBuildError, match="list-subclass method 'pop'"):
            compare_native_to_cpython(
                "clase L(list):\n    pasar\nl = L([1, 2])\nimprimir(l.pop(0))\n"
            )

    def test_sum_of_dict_subclass_fails_closed(self):
        with pytest.raises(NativeBuildError, match="sum\\(\\) of a dict subclass"):
            compare_native_to_cpython(
                "clase D(dict):\n    pasar\nd = D({1: 2})\nimprimir(sum(d))\n"
            )


if __name__ == "__main__":
    unittest.main()
