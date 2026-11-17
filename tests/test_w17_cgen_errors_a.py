"""Parity coverage for generated C fixes in dict.get slot boxing."""
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


class DictGetSlotBoxingParity(unittest.TestCase):
    def test_get_with_unknown_value_type_and_default(self):
        _assert_matches(
            self,
            "funcion show(mapping, key):\n"
            "    print(mapping.get(key, 'fallback'))\n"
            "show({'known': 'value'}, 'known')\n"
            "show({}, 'missing')\n",
        )

    def test_get_with_mixed_value_and_default_types_on_hit_and_miss(self):
        _assert_matches(
            self,
            "mapping = {'known': 7}\n"
            "print(mapping.get('known', 'fallback'))\n"
            "print(mapping.get('missing', 'fallback'))\n",
        )

    def test_get_with_unsupported_arity_still_fails_closed(self):
        with pytest.raises(NativeBuildError, match="one or two arguments"):
            compare_native_to_cpython(
                "mapping = {'known': 7}\n"
                "print(mapping.get('known', 'fallback', 'extra'))\n"
            )


if __name__ == "__main__":
    unittest.main()
