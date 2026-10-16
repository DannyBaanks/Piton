from __future__ import annotations

import unittest
from piton.native_differential import compare_native_to_cpython


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    diff = compare_native_to_cpython(source)
    testcase.assertEqual(
        diff.native.returncode,
        diff.oracle.returncode,
        f"returncode mismatch\nnative stderr: {diff.native.stderr!r}\noracle stderr: {diff.oracle.stderr!r}",
    )
    testcase.assertEqual(
        diff.native.stdout,
        diff.oracle.stdout,
        f"stdout mismatch\nnative stderr: {diff.native.stderr!r}\noracle stderr: {diff.oracle.stderr!r}",
    )


class TestNativeMethodsV1(unittest.TestCase):

    def test_list_and_tuple_index(self):
        source = """
l = [10, 20, 30, 40, 20]
imprimir(l.index(20))
imprimir(l.index(40))

t = (100, 200, 300)
imprimir(t.index(200))
imprimir(t.index(300))

sl = ['hola', 'mundo', 'piton']
imprimir(sl.index('mundo'))
imprimir(sl.index('piton'))
"""
        _assert_matches(self, source)

    def test_dict_pop_and_setdefault(self):
        source = """
d = {'a': 1, 'b': 2}
imprimir(d.pop('a'))
imprimir(d.pop('z', 99))
imprimir(d.setdefault('b', 100))
imprimir(d.setdefault('x', 500))
imprimir(d.get('x'))
"""
        _assert_matches(self, source)

    def test_dict_clear_and_copy(self):
        source = """
d = {'a': 10, 'b': 20}
c = d.copy()
imprimir(len(c))
imprimir(c.get('a'))
d.clear()
imprimir(len(d))
imprimir(len(c))
"""
        _assert_matches(self, source)

    def test_set_algebra_methods(self):
        source = """
s1 = {1, 2, 3}
s2 = {2, 3, 4}
u = s1.union(s2)
imprimir(len(u))
imprimir(1 en u)
imprimir(4 en u)

inter = s1.intersection(s2)
imprimir(len(inter))
imprimir(2 en inter)
imprimir(1 en inter)

diff = s1.difference(s2)
imprimir(len(diff))
imprimir(1 en diff)
imprimir(2 en diff)

sym = s1.symmetric_difference(s2)
imprimir(len(sym))
imprimir(1 en sym)
imprimir(4 en sym)
imprimir(2 en sym)
"""
        _assert_matches(self, source)

    def test_set_predicates_and_mutators(self):
        source = """
sub = {1, 2}
sup = {1, 2, 3}
dis = {4, 5}

imprimir(sub.issubset(sup))
imprimir(sup.issuperset(sub))
imprimir(sub.isdisjoint(dis))
imprimir(sub.isdisjoint(sup))

c = sub.copy()
imprimir(len(c))
sub.clear()
imprimir(len(sub))
imprimir(len(c))
"""
        _assert_matches(self, source)

    def test_str_removeprefix_and_suffix(self):
        source = """
s = 'TestPitonRunner'
imprimir(s.removeprefix('Test'))
imprimir(s.removeprefix('Otro'))

f = 'archivo.pit'
imprimir(f.removesuffix('.pit'))
imprimir(f.removesuffix('.txt'))
"""
        _assert_matches(self, source)
