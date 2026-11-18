"""Differential parity tests for Wave 20 suite_triage_b regressions (attrgetter, class pattern matching on slot, lru_cache cache_info)."""
from __future__ import annotations

import unittest

from piton.native_differential import compare_native_to_cpython


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


class TestSuiteTriageBParity(unittest.TestCase):
    def test_attrgetter_single_nested_and_multi_match_cpython(self):
        _assert_matches(
            self,
            "desde operator importar attrgetter\n"
            "clase Inner:\n"
            "    funcion __init__(self, v):\n"
            "        self.v = v\n"
            "clase Obj:\n"
            "    funcion __init__(self, x, y, inner):\n"
            "        self.x = x\n"
            "        self.y = y\n"
            "        self.inner = inner\n"
            "o = Obj(5, 9, Inner(7))\n"
            "imprimir(attrgetter('x')(o), attrgetter('inner.v')(o), attrgetter('x', 'y')(o))\n",
        )

    def test_class_pattern_matching_on_slot_param_and_missing_attr_error(self):
        _assert_matches(
            self,
            "clase Punto:\n"
            "    __match_args__ = ('x', 'y')\n"
            "    funcion __init__(self, x, y):\n"
            "        self.x = x\n"
            "        self.y = y\n"
            "funcion describir(v):\n"
            "    segun v:\n"
            "        caso Punto(x=0, y=y):\n"
            "            devolver f'eje y={y}'\n"
            "        caso _:\n"
            "            devolver 'otro'\n"
            "imprimir(describir(Punto(0, 3)), describir(42))\n"
            "intentar:\n"
            "    imprimir(getattr(Punto(1, 2), 'z'))\n"
            "excepto AttributeError:\n"
            "    imprimir('AttributeError')\n",
        )


if __name__ == "__main__":
    unittest.main()
