"""Regression coverage for dict.get() slot boxing in generated C."""
from __future__ import annotations

import unittest

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


class DictGetSlotBoxingParity(unittest.TestCase):
    def assert_matches_cpython(self, source: str) -> None:
        result = compare_native_to_cpython(source)
        self.assertEqual(
            result.native.returncode,
            result.oracle.returncode,
            f"returncode mismatch\nnative stderr: {result.native.stderr!r}\noracle stderr: {result.oracle.stderr!r}",
        )
        self.assertEqual(
            result.native.stdout,
            result.oracle.stdout,
            f"stdout mismatch\nnative stderr: {result.native.stderr!r}\noracle stderr: {result.oracle.stderr!r}",
        )

    def test_get_boxes_known_value_with_mismatched_default(self) -> None:
        self.assert_matches_cpython(
            "d = {'found': 7}\n"
            "print(d.get('found', 'fallback'))\n"
            "print(d.get('missing', 'fallback'))\n"
        )

    def test_get_boxes_heterogeneous_value_types(self) -> None:
        self.assert_matches_cpython(
            "d = {'number': 7, 'text': 'hello'}\n"
            "print(d.get('number', 'fallback'))\n"
            "print(d.get('missing', 'fallback'))\n"
        )

    def test_get_with_invalid_arity_still_fails_closed(self) -> None:
        with pytest.raises(
            NativeBuildError,
            match=r"Linux dict\.get\(\) requires one or two arguments",
        ):
            compare_native_to_cpython("d = {}\nprint(d.get())\n")


if __name__ == "__main__":
    unittest.main()
