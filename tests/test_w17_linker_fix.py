"""Regression tests for prefixed C identifiers in object string conversion."""
from __future__ import annotations

from piton.native_differential import compare_native_to_cpython


def _assert_matches(source: str) -> None:
    result = compare_native_to_cpython(source)
    assert result.native.returncode == result.oracle.returncode, (
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}"
    )
    assert result.native.stdout == result.oracle.stdout, (
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}"
    )


def test_str_dispatch_passes_initialized_instance_to_dunder_str():
    _assert_matches(
        "clase Point:\n"
        "    funcion __init__(self, x, y):\n"
        "        self.x = x\n"
        "        self.y = y\n"
        "    funcion __str__(self):\n"
        "        devolver f'({self.x},{self.y})'\n"
        "print(str(Point(2, 3)))\n"
    )


def test_str_repr_fallback_preserves_zero_and_negative_attributes():
    _assert_matches(
        "clase Point:\n"
        "    funcion __init__(self, x, y):\n"
        "        self.x = x\n"
        "        self.y = y\n"
        "    funcion __repr__(self):\n"
        "        devolver f'Point({self.x},{self.y})'\n"
        "print(str(Point(0, -3)))\n"
    )


def test_user_libc_names_and_sqrt_variable_coexist_with_math_sqrt():
    _assert_matches(
        "funcion exit(value):\n"
        "    devolver value + 1\n"
        "funcion printf(value):\n"
        "    devolver value + 2\n"
        "funcion malloc(value):\n"
        "    devolver value + 3\n"
        "importar math\n"
        "sqrt = 12\n"
        "print(exit(4), printf(4), malloc(4))\n"
        "print(sqrt, math.sqrt(9))\n"
    )
