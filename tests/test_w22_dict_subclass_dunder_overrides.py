"""Parity checks for dict subclass dunder overrides (__setitem__, __getitem__, __missing__)."""
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


class DictSubclassDunderOverrides(unittest.TestCase):
    def test_dict_subclass_setitem_with_super(self):
        _assert_matches(
            self,
            "clase MiDict(dict):\n"
            "    funcion __setitem__(self, k, v):\n"
            "        super().__setitem__(k, v * 10)\n"
            "d = MiDict()\n"
            'd["a"] = 2\n'
            'imprimir(d["a"])\n',
        )

    def test_dict_subclass_getitem_with_super(self):
        _assert_matches(
            self,
            "clase MiDict(dict):\n"
            "    funcion __getitem__(self, k):\n"
            "        v = super().__getitem__(k)\n"
            "        devolver v * 2\n"
            'd = MiDict({"a": 5})\n'
            'imprimir(d["a"])\n',
        )

    def test_dict_subclass_missing_basic(self):
        _assert_matches(
            self,
            "clase DefaultZero(dict):\n"
            "    funcion __missing__(self, k):\n"
            "        devolver 0\n"
            'd = DefaultZero({"a": 5})\n'
            'imprimir(d["a"], d["faltante"])\n',
        )

    def test_dict_subclass_missing_with_super_getitem(self):
        _assert_matches(
            self,
            "clase MiDict(dict):\n"
            "    funcion __missing__(self, k):\n"
            '        devolver super().__getitem__("default")\n'
            "    funcion __getitem__(self, k):\n"
            "        si k en self:\n"
            "            devolver super().__getitem__(k)\n"
            "        sino:\n"
            "            devolver self.__missing__(k)\n"
            'd = MiDict({"default": 42})\n'
            'imprimir(d["missing"])\n',
        )

    def test_dict_subclass_setitem_tracking(self):
        _assert_matches(
            self,
            "clase TrackingDict(dict):\n"
            "    funcion __init__(self):\n"
            "        self.sets = 0\n"
            "    funcion __setitem__(self, k, v):\n"
            "        self.sets = self.sets + 1\n"
            "        super().__setitem__(k, v)\n"
            "d = TrackingDict()\n"
            'd["a"] = 1\n'
            'd["b"] = 2\n'
            'imprimir(d.sets, d["a"], d["b"])\n',
        )

    def test_dict_subclass_getitem_override_no_super(self):
        _assert_matches(
            self,
            "clase MiDict(dict):\n"
            "    funcion __getitem__(self, k):\n"
            '        devolver "custom:" + str(k)\n'
            'd = MiDict({"a": 5})\n'
            'imprimir(d["a"], d["b"])\n',
        )

    def test_dict_subclass_missing_returns_int(self):
        _assert_matches(
            self,
            "clase DefaultInt(dict):\n"
            "    funcion __missing__(self, k):\n"
            "        devolver -1\n"
            "d = DefaultInt()\n"
            'imprimir(d["x"])\n',
        )

    # Negative/edge cases: these must still fail closed
    def test_list_subclass_setitem_fails_closed(self):
        with pytest.raises(NativeBuildError, match="native list-subclass overriding '__setitem__' is not supported"):
            compare_native_to_cpython(
                "clase MiList(list):\n"
                "    funcion __setitem__(self, k, v):\n"
                "        super().__setitem__(k, v * 10)\n"
            )

    def test_list_subclass_getitem_fails_closed(self):
        with pytest.raises(NativeBuildError, match="native list-subclass overriding '__getitem__' is not supported"):
            compare_native_to_cpython(
                "clase MiList(list):\n"
                "    funcion __getitem__(self, k):\n"
                "        devolver super().__getitem__(k) * 2\n"
            )

    def test_dict_subclass_repr_fails_closed(self):
        with pytest.raises(NativeBuildError, match="native dict-subclass overriding '__repr__' is not supported"):
            compare_native_to_cpython(
                "clase MiDict(dict):\n"
                "    funcion __repr__(self):\n"
                '        devolver "custom"\n'
            )

    def test_dict_subclass_str_fails_closed(self):
        with pytest.raises(NativeBuildError, match="native dict-subclass overriding '__str__' is not supported"):
            compare_native_to_cpython(
                "clase MiDict(dict):\n"
                "    funcion __str__(self):\n"
                '        devolver "custom"\n'
            )

    def test_dict_subclass_len_fails_closed(self):
        with pytest.raises(NativeBuildError, match="native dict-subclass overriding '__len__' is not supported"):
            compare_native_to_cpython(
                "clase MiDict(dict):\n"
                "    funcion __len__(self):\n"
                "        devolver 0\n"
            )

    def test_dict_subclass_iter_fails_closed(self):
        with pytest.raises(NativeBuildError, match="native dict-subclass overriding '__iter__' is not supported"):
            compare_native_to_cpython(
                "clase MiDict(dict):\n"
                "    funcion __iter__(self):\n"
                "        devolver iter([])\n"
            )


if __name__ == "__main__":
    unittest.main()