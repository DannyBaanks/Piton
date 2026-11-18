"""Parity coverage for super() dispatch through modeled and fallback MROs."""
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
    testcase.assertEqual(result.native.stderr, result.oracle.stderr, "stderr mismatch")


class SuperMroBaseAttrAndInitParity(unittest.TestCase):
    def test_mixin_super_resolves_against_later_runtime_subclass_mro(self):
        _assert_matches(
            self,
            "clase Base:\n"
            "    funcion close(self):\n"
            "        imprimir('base')\n"
            "clase Mixin:\n"
            "    funcion close(self):\n"
            "        super().close()\n"
            "        imprimir('mixin')\n"
            "clase Server(Mixin, Base):\n"
            "    pasar\n"
            "Server().close()\n",
        )

    def test_super_init_forwards_arguments_to_modeled_parent(self):
        _assert_matches(
            self,
            "clase Base:\n"
            "    funcion __init__(self, value):\n"
            "        self.value = value\n"
            "clase Child(Base):\n"
            "    funcion __init__(self, value):\n"
            "        super().__init__(value)\n"
            "imprimir(Child(23).value)\n",
        )

    def test_super_attribute_dispatches_to_imported_contextlib_base(self):
        _assert_matches(
            self,
            "desde contextlib importar AbstractContextManager\n"
            "clase Manager(AbstractContextManager):\n"
            "    funcion __init__(self, value):\n"
            "        self.value = value\n"
            "    funcion __enter__(self):\n"
            "        devolver super().__enter__()\n"
            "    funcion __exit__(self, exc_type, exc_value, traceback):\n"
            "        devolver Falso\n"
            "con Manager(17) como manager:\n"
            "    imprimir(manager.value)\n",
        )

    def test_super_init_for_exception_base_preserves_arguments(self):
        _assert_matches(
            self,
            "clase E(Exception):\n"
            "    funcion __init__(self, message):\n"
            "        super().__init__(message)\n"
            "intentar:\n"
            "    lanzar E('edge')\n"
            "excepto E como exc:\n"
            "    imprimir(exc.args)\n",
        )

    def test_super_forwards_negative_and_zero_edge_arguments(self):
        _assert_matches(
            self,
            "clase Base:\n"
            "    funcion adjust(self, value):\n"
            "        devolver value - 1\n"
            "clase Child(Base):\n"
            "    funcion adjust(self, value):\n"
            "        devolver super().adjust(value)\n"
            "imprimir(Child().adjust(-1), Child().adjust(0))\n",
        )

    def test_unmodeled_builtin_super_init_stays_fail_closed(self):
        with pytest.raises(
            NativeBuildError,
            match=r"super\(\).__init__ with arguments requires a modeled base implementation",
        ):
            compare_native_to_cpython(
                "clase Values(tuple):\n"
                "    funcion __init__(self, values):\n"
                "        super().__init__(values)\n"
                "Values([1, 2])\n"
            )

    def test_imported_io_super_init_without_native_implementation_fails_closed(self):
        with pytest.raises(NativeBuildError, match="no modeled base implementation"):
            compare_native_to_cpython(
                "desde _io importar BufferedReader\n"
                "clase Reader(BufferedReader):\n"
                "    funcion __init__(self, source):\n"
                "        super().__init__(source)\n"
                "Reader(Nada)\n"
            )

    def test_missing_super_attribute_stays_fail_closed(self):
        with pytest.raises(NativeBuildError, match="no modeled base implementation"):
            compare_native_to_cpython(
                "clase Base:\n"
                "    pasar\n"
                "clase Child(Base):\n"
                "    funcion run(self):\n"
                "        devolver super().missing()\n"
                "imprimir(Child().run())\n"
            )


if __name__ == "__main__":
    unittest.main()
