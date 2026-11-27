"""Focused CPython parity coverage for super, dynamic types and object dunders."""
from __future__ import annotations

import unittest

from piton.native_differential import compare_native_to_cpython


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    try:
        result = compare_native_to_cpython(source)
    except Exception as exc:
        testcase.fail(f"comparison raised {type(exc).__name__}: {exc!r}")
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
    testcase.assertEqual(result.native.stderr, result.oracle.stderr, "stderr mismatch")


class SuperObjectsParity(unittest.TestCase):
    def test_super_dispatch_uses_the_runtime_mro(self) -> None:
        _assert_matches(
            self,
            "clase Root:\n"
            "    funcion label(self):\n"
            "        devolver 'root'\n"
            "clase Middle(Root):\n"
            "    funcion label(self):\n"
            "        devolver super().label() + '-middle'\n"
            "clase Leaf(Middle):\n"
            "    funcion label(self):\n"
            "        devolver super().label() + '-leaf'\n"
            "imprimir(Leaf().label())\n",
        )

    def test_three_argument_type_preserves_class_name_and_namespace(self) -> None:
        _assert_matches(
            self,
            'Dynamic = type("Dynamic", (object,), {"answer": 42})\n'
            "imprimir(Dynamic.__name__, Dynamic.__qualname__, Dynamic.answer)\n",
        )

    def test_native_object_multiplication_calls_its_dunder(self) -> None:
        _assert_matches(
            self,
            "clase Factor:\n"
            "    funcion __init__(self, value):\n"
            "        self.value = value\n"
            "    funcion __mul__(self, other):\n"
            "        devolver self.value * other.value\n"
            "imprimir(Factor(6) * Factor(7))\n",
        )

    def test_user_getitem_receives_a_slice(self) -> None:
        _assert_matches(
            self,
            "clase Trace:\n"
            "    funcion __getitem__(self, key):\n"
            "        devolver (key.start, key.stop, key.step)\n"
            "imprimir(Trace()[1:4:2])\n"
            "imprimir(Trace()[:4])\n",
        )


if __name__ == "__main__":
    unittest.main()
