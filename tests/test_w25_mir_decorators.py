"""Focused parity checks for decorated method lowering in MIR."""
from __future__ import annotations

from piton.native_differential import compare_native_to_cpython


def _assert_matches(source: str) -> None:
    result = compare_native_to_cpython(source)
    assert result.native.returncode == result.oracle.returncode, (
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}"
    )
    assert result.native.stdout == result.oracle.stdout, (
        f"stdout mismatch\nnative stdout: {result.native.stdout!r}\n"
        f"oracle stdout: {result.oracle.stdout!r}\n"
        f"native stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}"
    )


def test_dynamic_receiver_calls_class_created_decorator() -> None:
    _assert_matches(
        "funcion ajusta(fn):\n"
        "    funcion envuelto(este, x):\n"
        "        devolver fn(este, x) + 10\n"
        "    devolver envuelto\n"
        "clase C:\n"
        "    funcion __init__(este, base):\n"
        "        este.base = base\n"
        "    @ajusta\n"
        "    funcion valor(este, x):\n"
        "        devolver este.base + x\n"
        "funcion invoca(obj, x):\n"
        "    devolver obj.valor(x)\n"
        "imprimir(invoca(C(3), 5))\n"
    )


def test_decorated_getitem_uses_native_subscript_dispatch() -> None:
    _assert_matches(
        "funcion identidad(fn):\n"
        "    devolver fn\n"
        "clase C:\n"
        "    funcion __init__(este, base):\n"
        "        este.base = base\n"
        "    @identidad\n"
        "    funcion __getitem__(este, index):\n"
        "        devolver este.base + index\n"
        "imprimir(C(7)[4])\n"
    )


def test_wrapped_property_remains_a_descriptor_read() -> None:
    _assert_matches(
        "funcion identidad(fn):\n"
        "    devolver fn\n"
        "clase C:\n"
        "    funcion __init__(este, value):\n"
        "        este.value = value\n"
        "    @property\n"
        "    @identidad\n"
        "    funcion valor(este):\n"
        "        devolver este.value\n"
        "imprimir(C(12).valor)\n"
    )


def test_keyword_configured_decorator_factory_captures_typed_flag() -> None:
    _assert_matches(
        "funcion tp_cache(*, typed=False):\n"
        "    funcion decorar(fn):\n"
        "        si typed:\n"
        "            devolver fn\n"
        "        funcion envuelto(este):\n"
        "            devolver fn(este) + 1\n"
        "        devolver envuelto\n"
        "    devolver decorar\n"
        "clase C:\n"
        "    @tp_cache(typed=True)\n"
        "    funcion valor(este):\n"
        "        devolver 7\n"
        "imprimir(C().valor())\n"
    )
