"""Regression coverage for legacy marker method decorators."""
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


class MethodDecoratorMarkerRegression(unittest.TestCase):
    def test_bare_and_module_attribute_markers_keep_native_method_lowering(self):
        _assert_matches(
            self,
            "importar abc\n"
            "desde abc importar abstractmethod\n"
            "clase Plain:\n"
            "    @abc.abstractmethod\n"
            "    funcion first(este):\n"
            "        devolver 3\n"
            "    @abstractmethod\n"
            "    funcion second(este):\n"
            "        devolver 4\n"
            "plain = Plain()\n"
            "imprimir(plain.first(), plain.second())\n",
        )

    def test_user_definition_shadowing_marker_name_is_applied(self):
        _assert_matches(
            self,
            "funcion abstractmethod(fn):\n"
            "    imprimir(\"user decorator\")\n"
            "    funcion wrapped(este):\n"
            "        devolver fn(este) + 1\n"
            "    devolver wrapped\n"
            "clase Plain:\n"
            "    @abstractmethod\n"
            "    funcion value(este):\n"
            "        devolver 7\n"
            "imprimir(Plain().value())\n",
        )

    def test_lru_cache_marker_keeps_native_caching_behavior(self):
        _assert_matches(
            self,
            "importar functools\n"
            "calls = [0]\n"
            "@functools.lru_cache(maxsize=Nada)\n"
            "funcion compute(value):\n"
            "    calls[0] = calls[0] + 1\n"
            "    devolver value * 2\n"
            "imprimir(compute(5), compute(5), calls[0])\n",
        )

    def test_cached_property_stays_fail_closed(self):
        source = (
            "importar functools\n"
            "clase Handler:\n"
            "    @functools.cached_property\n"
            "    funcion token(este):\n"
            "        devolver 7\n"
        )
        with pytest.raises(
            NativeBuildError,
            match="cached descriptor storage is unavailable",
        ):
            compare_native_to_cpython(source)


if __name__ == "__main__":
    unittest.main()
