"""ITER_PASSTHROUGH_V1 + STR_LEN/REPEAT_V1 + COLL_METHODS_V1.

Three cheap clusters from the remaining probe gaps:

ITER_PASSTHROUGH_V1 — for-loops over builtin iterators:
  `para p en enumerar/zip/map/filter/reversed(...)` died with "iter requires
  a native collection" because iter_new rebuilt an iterator from an
  iterator. Now iter(x) on an iterator returns x (CPython semantics), which
  unblocks the loops — the iter_next dispatch already existed.
  map/filter with a BUILTIN-name callback fails closed (a builtin name has
  no native function address; passing its marker would crash); user-function
  callbacks work.
  On Windows this additionally needed the loop-guard: for-loop exhaustion
  raises StopIteration through piton_raise, which prints and exits when no
  handler accepts it. iter_new now pushes a StopIteration-only frame for
  the loop's lifetime; the loop-exit block (where normal exhaustion AND
  break converge) clears the flag and pops. Covers bare/nested/try-wrapped
  loops and dict iteration.

STR_LEN_V1 / STR_REPEAT_V1 — longitud('hola') is the C string length;
'ab' * 3 (either order, '' for non-positive counts, like CPython).

COLL_METHODS_V1 — builtin collection methods bound by static dispatch,
mirroring STR_METHODS_V1: append/pop/reverse/insert/count/sort (list),
count (tuple), get with and without default (dict), add (set).
Mutators return None; reads follow the get_item convention (statically
int). Arity validated at build time; pop-from-empty, heterogeneous sort
and single-arg get on a missing key exit with the CPython exception name.
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


class IterPassthroughV1(unittest.TestCase):
    def test_for_enumerate(self):
        # int elements (str elements inside the yielded tuples hit the
        # known untagged-strings display limitation on Windows)
        _assert_matches(self, "para p en enumerar([10, 20]):\n    imprimir(p)\n")

    def test_for_zip(self):
        _assert_matches(self, "para p en zip([1, 2], [3, 4]):\n    imprimir(p)\n")

    def test_for_reversed(self):
        _assert_matches(self, "para x en reversed([1, 2]):\n    imprimir(x)\n")

    def test_for_map_user_callback(self):
        _assert_matches(
            self,
            "funcion doble(n):\n    devolver n * 2\n"
            "para x en map(doble, [1, 2]):\n    imprimir(x)\n",
        )

    def test_for_map_builtin_callback_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("para x en map(texto, [1, 2]):\n    imprimir(x)\n")

    def test_iter_of_iterator_is_itself(self):
        _assert_matches(self, "e = enumerar([7])\npara p en e:\n    imprimir(p)\n")

    def test_for_dict(self):
        _assert_matches(self, "para k en {'a': 1, 'b': 2}:\n    imprimir(k)\n")

    def test_for_str(self):
        _assert_matches(self, "para c en 'ab':\n    imprimir(c)\n")

    def test_nested_builtin_iterator_loops(self):
        _assert_matches(
            self,
            "para a en reversed([1, 2]):\n"
            "    para b en zip([9], [8]):\n"
            "        imprimir((a, b))\n",
        )

    def test_break_exits_builtin_iterator_loop(self):
        _assert_matches(
            self,
            "para x en reversed([1, 2, 3]):\n"
            "    si x == 2:\n"
            "        romper\n"
            "    imprimir(x)\n",
        )


class StrLenRepeatV1(unittest.TestCase):
    def test_str_len(self):
        _assert_matches(self, "imprimir(longitud('hola'), longitud(''))\n")

    def test_str_repeat(self):
        _assert_matches(self, "imprimir('ab' * 3)\n")

    def test_str_repeat_right_order(self):
        _assert_matches(self, "imprimir(3 * 'ab')\n")

    def test_str_repeat_non_positive(self):
        _assert_matches(self, "imprimir('ab' * 0, 'ab' * -2)\n")


class CollMethodsV1(unittest.TestCase):
    def test_list_append(self):
        _assert_matches(self, "l = [1]\nl.append(2)\nimprimir(l)\n")

    def test_list_append_returns_none(self):
        _assert_matches(self, "l = [1]\nx = l.append(2)\nimprimir(x)\n")

    def test_list_pop(self):
        _assert_matches(self, "l = [1, 2]\nimprimir(l.pop(), l)\n")

    def test_list_pop_index(self):
        _assert_matches(self, "l = [1, 2, 3]\nimprimir(l.pop(0), l)\n")

    def test_list_pop_empty_fails_at_runtime(self):
        result = compare_native_to_cpython("l = []\nimprimir(l.pop())\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"IndexError", result.native.stderr)

    def test_list_reverse(self):
        _assert_matches(self, "l = [1, 2, 3]\nl.reverse()\nimprimir(l)\n")

    def test_list_insert(self):
        _assert_matches(self, "l = [1, 3]\nl.insert(1, 2)\nimprimir(l)\n")

    def test_list_insert_clamps(self):
        _assert_matches(self, "l = [1]\nl.insert(-5, 0)\nl.insert(99, 2)\nimprimir(l)\n")

    def test_list_count(self):
        _assert_matches(self, "imprimir([1, 1, 2].count(1), [1].count(9))\n")

    def test_tuple_count(self):
        _assert_matches(self, "imprimir((1, 1, 2).count(1))\n")

    def test_list_sort_ints(self):
        _assert_matches(self, "l = [3, 1, 2]\nl.sort()\nimprimir(l)\n")

    @unittest.skipIf(
        sys.platform.startswith("win32"),
        "Windows str elements are untagged raw pointers, indistinguishable "
        "from ints at runtime — the mixed sort cannot fail closed there "
        "(heap PitonStr follow-up)",
    )
    def test_list_sort_mixed_fails_at_runtime(self):
        result = compare_native_to_cpython("l = [1, 'a']\nl.sort()\nimprimir(l)\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError", result.native.stderr)

    @unittest.skipIf(
        sys.platform.startswith("win32"),
        "Windows piton_seq_sort is int-only (like the existing sorted()); "
        "str sort needs the tagged comparison",
    )
    def test_list_sort_strings(self):
        _assert_matches(self, "l = ['b', 'a', 'c']\nl.sort()\nimprimir(l)\n")

    def test_dict_get(self):
        _assert_matches(self, "d = {'a': 1}\nimprimir(d.get('a'))\n")

    def test_dict_get_default(self):
        _assert_matches(self, "imprimir({'a': 1}.get('z', 7))\n")

    def test_dict_get_missing_key_fails_loud(self):
        # the untagged model cannot print an int-or-None union: single-arg
        # get on a missing key raises KeyError (consistent with d[k] here)
        # instead of a silent wrong value.
        result = compare_native_to_cpython("d = {'a': 1}\nimprimir(d.get('z'))\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"KeyError", result.native.stderr)

    def test_set_add(self):
        _assert_matches(self, "s = {1}\ns.add(2)\ns.add(1)\nimprimir(s)\n")

    def test_list_append_wrong_arity_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("l = [1]\nl.append()\nimprimir(l)\n")

    def test_tuple_append_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("t = (1,)\nt.append(2)\nimprimir(t)\n")


if __name__ == "__main__":
    unittest.main()
