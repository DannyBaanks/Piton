"""Parity checks for builtin callables passed to native filter/map."""
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


class FilterMapBuiltinCallbacks(unittest.TestCase):
    def test_filter_bool_none_and_len_preserve_mixed_truthiness(self):
        _assert_matches(
            self,
            "raw = [0, 7, False, True, '', 'ok', [], [16]]\n"
            "f_none = list(filter(None, raw))\n"
            "nums = [-5, -2, 0, 3, 7]\n"
            "f_pos = list(filter(lambda x: x > 0, nums))\n"
            "f_bool = tuple(filter(bool, raw))\n"
            "f_bool_nums = tuple(filter(bool, [0, 1, 2, 0, 4]))\n"
            "sizes = [[], [0], '', 'x']\n"
            "f_len = list(filter(len, sizes))\n"
            "print(f_none, f_pos, f_bool)\n"
            "print(f_bool_nums, f_len)\n",
        )

    def test_map_string_int_float_abs_len_repr_and_ascii(self):
        _assert_matches(
            self,
            "print(list(map(str, [0, False, True, '', 'ok', [], [16], None])))\n"
            "print(list(map(int, ['1', '-2', 3.8, True])))\n"
            "print(list(map(float, ['1.5', 2, False])))\n"
            "print(list(map(abs, [-5, 0, -3])))\n"
            "print(list(map(len, [[], [1, 2], '', 'ok', {'k': 1}])))\n"
            "print(list(map(repr, [True, 'ok', [16]])))\n"
            "print(list(map(ascii, ['é', 'ok'])))\n",
        )

    def test_map_ord_chr_and_iter_callbacks(self):
        _assert_matches(
            self,
            "print(list(map(ord, ['A', 'é'])))\n"
            "print(list(map(chr, [65, 233])))\n"
            "para inner en map(iter, [[1], [2]]):\n"
            "    print(next(inner))\n",
        )

    def test_unsupported_builtin_callback_fails_closed(self):
        with pytest.raises(NativeBuildError, match="builtin callback 'dir' is not supported"):
            compare_native_to_cpython("print(list(map(dir, [1])))\n")
        with pytest.raises(NativeBuildError, match="CPython hash randomization cannot be matched"):
            compare_native_to_cpython("print(list(map(hash, ['value'])))\n")


if __name__ == "__main__":
    unittest.main()
