"""Differential regressions for Linux C symbol and imported-class aliases."""
from __future__ import annotations

import re

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


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
    if result.native.returncode:
        native_error = re.search(rb"([A-Za-z_][A-Za-z_0-9]*(?:Error|Exception)):", result.native.stderr)
        oracle_error = re.search(rb"([A-Za-z_][A-Za-z_0-9]*(?:Error|Exception)):", result.oracle.stderr)
        assert native_error is not None and oracle_error is not None
        assert native_error.group(1) == oracle_error.group(1)


def test_datetime_imported_class_alias_replace_matches_leap_day_edge():
    _assert_matches(
        "desde datetime importar date como datetime_date\n"
        "d = datetime_date(2020, 2, 29)\n"
        "print(d.replace(year=2021, day=28))\n"
    )


def test_weakset_imported_class_alias_dynamic_method_dispatch():
    _assert_matches(
        "desde _weakrefset importar WeakSet\n"
        "clase Item:\n"
        "    pasar\n"
        "funcion add_member(items, item):\n"
        "    items.add(item)\n"
        "    devolver items.__len__()\n"
        "items = WeakSet()\n"
        "item = Item()\n"
        "print(add_member(items, item))\n"
    )


def test_user_global_log_does_not_collide_with_runtime_libm_log():
    _assert_matches(
        "log = 12\n"
        "funcion read_log():\n"
        "    devolver log\n"
        "desde random importar gauss\n"
        "gauss()\n"
        "print(read_log())\n"
    )


def test_user_function_log_does_not_collide_with_runtime_libm_log():
    _assert_matches(
        "funcion log(value):\n"
        "    devolver value + 2\n"
        "desde random importar gauss\n"
        "gauss()\n"
        "print(log(-2))\n"
    )


def test_user_function_sqrt_does_not_collide_with_runtime_libm_sqrt():
    _assert_matches(
        "funcion sqrt(value):\n"
        "    devolver value + 3\n"
        "desde random importar gauss\n"
        "gauss()\n"
        "print(sqrt(2))\n"
    )


def test_bytes_strip_variants_trim_ascii_whitespace_by_byte():
    _assert_matches(
        "data = b'\\x00 \\talpha\\r\\n\\xff'\n"
        "print(data.strip())\n"
        "print(data.lstrip())\n"
        "print(data.rstrip())\n"
    )


def test_bytes_strip_custom_chars_and_bytearray_strip_fail_closed():
    with pytest.raises(NativeBuildError, match=r"Linux bytes.strip\(\) requires no arguments"):
        compare_native_to_cpython("print(b'xx'.strip(b'x'))\n")
    with pytest.raises(NativeBuildError, match="bytearray strip methods are not supported"):
        compare_native_to_cpython("print(bytearray(b' xx ').strip())\n")


def test_imported_method_alias_name_collision_fails_closed():
    with pytest.raises(
        NativeBuildError,
        match="native imported class method alias 'WeakSet__add' collides with another function",
    ):
        compare_native_to_cpython(
            "funcion WeakSet__add(value):\n"
            "    devolver value\n"
            "desde _weakrefset importar WeakSet\n"
            "items = WeakSet()\n"
        )
