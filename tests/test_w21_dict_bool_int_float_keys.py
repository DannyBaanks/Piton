"""W21 parity: dict/set key equality across bool, int and integral float.

CPython 3.12: True == 1 == 1.0 and False == 0 == 0.0 (same hash, same key).
Native PITON must treat them as the same key, keep the first key object on
update, and compare int/float exactly (no rounding of the int to double).
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


class DictBoolIntFloatKeys(unittest.TestCase):
    def test_bool_lookup_on_int_key(self):
        _assert_matches(
            self,
            "d = {1: 'uno', 0: 'cero'}\n"
            "imprimir(d[Verdadero], d[Falso], Verdadero en d, Falso en d)\n",
        )

    def test_int_key_overwrite_by_bool_keeps_first_key_object(self):
        _assert_matches(
            self,
            "d = {Verdadero: 'a', 1: 'b', Falso: 'x', 0: 'y'}\n"
            "imprimir(len(d), d[1], d[Verdadero], d[0], d[Falso])\n"
            "print(list(d.keys()), list(d.values()))\n",
        )

    def test_set_literal_dedups_bool_int_float(self):
        _assert_matches(
            self,
            "s = {1, Verdadero, 0, Falso, 2}\n"
            "imprimir(len(s), 1 en s, Verdadero en s, 0 en s, Falso en s)\n"
            "t = {Verdadero, 1.0, 1}\n"
            "imprimir(len(t), list(t))\n",
        )

    def test_setitem_getitem_get_setdefault_pop_del_on_bool_and_float(self):
        _assert_matches(
            self,
            "d = {1: 'a'}\n"
            "d[Verdadero] = 'z'\n"
            "imprimir(len(d), d[1], list(d.keys()))\n"
            "d[2.0] = 'b'\n"
            "d[2] = 'c'\n"
            "imprimir(len(d), d[2.0], list(d.keys()))\n"
            "imprimir(d.get(Falso), d.get(1.0, 'x'))\n"
            "imprimir(d.setdefault(Verdadero, 'q'), len(d))\n"
            "imprimir(d.pop(1.0), len(d))\n"
            "e = {1: 'a', 2: 'b'}\n"
            "borrar e[Verdadero]\n"
            "imprimir(len(e), list(e.keys()))\n",
        )

    def test_set_add_discard_on_bool_and_float(self):
        _assert_matches(
            self,
            "s = {1}\n"
            "s.add(Verdadero)\n"
            "imprimir(len(s), list(s))\n"
            "u = {1, 2}\n"
            "u.discard(1.0)\n"
            "imprimir(len(u), 1 en u, 2.0 en u)\n",
        )

    def test_duplicate_literal_keys_collapse_like_cpython(self):
        _assert_matches(
            self,
            "d = {'a': 1, 'a': 2}\n"
            "imprimir(len(d), d['a'])\n"
            "k = 1\n"
            "e = {k: 'a', Verdadero: 'b'}\n"
            "imprimir(len(e), e[1], e[Verdadero])\n"
            "f = {1: 'a', 1.0: 'b'}\n"
            "imprimir(len(f), f[1], list(f.keys()))\n",
        )

    def test_membership_and_equality_across_bool_int(self):
        _assert_matches(
            self,
            "imprimir(1 en [Verdadero], Verdadero en [1], 0 en [Falso])\n"
            "imprimir([1] == [Verdadero], (0, 1) == (Falso, 1.0))\n"
            "imprimir({1: 'a'} == {Verdadero: 'a'})\n",
        )

    def test_int_float_key_equality_is_exact_not_rounded(self):
        # 2**53 + 1 is not representable as a double; CPython compares exactly.
        _assert_matches(
            self,
            "d = {9007199254740993: 'a'}\n"
            "imprimir(9007199254740992.0 en d, 9007199254740993.0 en d)\n"
            "imprimir(9007199254740993 == 9007199254740992.0, "
            "9007199254740992 == 9007199254740992.0)\n",
        )

    def test_non_integral_float_never_matches_int_key(self):
        _assert_matches(
            self,
            "d = {1: 'a'}\n"
            "imprimir(1.5 en d, 1.0 en d, 0 en {-0.0: 1}, d[1.0])\n"
            "x = 9007199254740992.0\n"
            "imprimir(x == 9007199254740992, x != 9007199254740992, 1.5 == 1)\n",
        )

    def test_statically_unhashable_literal_key_fails_closed(self):
        # CPython raises TypeError: unhashable type: 'list'. Native must not
        # silently accept the list as a key; it refuses at build time.
        with pytest.raises(NativeBuildError, match="unhashable type 'list' is not supported"):
            compare_native_to_cpython("d = {[1]: 'a', 2: 'b'}\nimprimir(d)\n")
        with pytest.raises(NativeBuildError, match="unhashable type 'set' is not supported"):
            compare_native_to_cpython("d = {{1}: 'a', 2: 'b'}\nimprimir(d)\n")
        with pytest.raises(NativeBuildError, match="unhashable type 'list' is not supported"):
            compare_native_to_cpython("d = {[1]: 'a'}\nimprimir(d)\n")
        with pytest.raises(NativeBuildError, match="unhashable type 'list' is not supported"):
            compare_native_to_cpython("s = {[1], 2}\nimprimir(len(s))\n")


if __name__ == "__main__":
    unittest.main()
