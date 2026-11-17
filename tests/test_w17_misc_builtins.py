"""Differential coverage for wave 17 builtin and stdlib gaps."""
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


def _assert_same_exception(
    testcase: unittest.TestCase, source: str, expected: bytes
) -> None:
    result = compare_native_to_cpython(source)
    testcase.assertNotEqual(result.native.returncode, 0, "native program unexpectedly succeeded")
    testcase.assertEqual(
        result.native.returncode,
        result.oracle.returncode,
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\noracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(result.native.stdout, result.oracle.stdout)
    testcase.assertIn(expected, result.native.stderr)
    testcase.assertIn(expected, result.oracle.stderr)


class W17BytearraySliceAssignment(unittest.TestCase):
    def test_assigns_integer_iterables_generators_and_memoryviews(self):
        _assert_matches(
            self,
            "data = bytearray(b'abcd')\n"
            "data[1:2] = [88, 89]\n"
            "print(data)\n"
            "stepped = bytearray(b'abcd')\n"
            "stepped[::2] = range(2)\n"
            "print(stepped)\n"
            "generated = bytearray()\n"
            "generated[:] = (value para value en [65, 66])\n"
            "print(generated)\n"
            "viewed = bytearray(b'....')\n"
            "viewed[1:3] = memoryview(b'XY')\n"
            "print(viewed)\n",
        )

    def test_invalid_byte_value_and_string_rhs_match_cpython_errors(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    bytearray(b'ab')[:] = [1, 256]\n"
            "excepto ValueError:\n"
            "    print('out of range')\n"
            "intentar:\n"
            "    bytearray(b'ab')[:] = 'xy'\n"
            "excepto TypeError:\n"
            "    print('not bytes')\n",
        )


class W17StrMaketrans(unittest.TestCase):
    def test_two_and_three_argument_unicode_forms_translate(self):
        _assert_matches(
            self,
            "two = str.maketrans('abc', 'xyz')\n"
            "three = str.maketrans('éβ', 'XY', 'β!')\n"
            "print('aéβc'.translate(two))\n"
            "print('éβ!'.translate(three))\n",
        )

    def test_unequal_translation_lengths_raise_value_error(self):
        _assert_same_exception(
            self,
            "str.maketrans('ab', 'x')\n",
            b"ValueError: the first two maketrans arguments must have equal length",
        )

    def test_four_argument_form_remains_fail_closed(self):
        with pytest.raises(
            NativeBuildError,
            match="Linux str.maketrans\(\) requires one dict or two/three string arguments",
        ):
            compare_native_to_cpython("str.maketrans('a', 'b', 'c', 'd')\n")


class W17ZipIterables(unittest.TestCase):
    def test_zip_mixes_generators_iterators_and_unicode_strings(self):
        _assert_matches(
            self,
            "rows = list(zip((value * 2 para value en [1, 2, 3]), iter([7, 8]), 'abc'))\n"
            "print(rows)\n"
            "print(list(zip('éx', [1, 2, 3])))\n"
            "print(list(zip()))\n"
            "print(list(zip([1, 2], [3, 4], strict=True)))\n",
        )

    def test_zip_strict_mismatch_raises_value_error(self):
        _assert_same_exception(
            self,
            "list(zip([1, 2], [3], strict=True))\n",
            b"ValueError: zip() argument 2 is shorter than argument 1",
        )

    def test_non_iterable_argument_raises_cpython_type_error(self):
        _assert_same_exception(
            self,
            "list(zip(5))\n",
            b"TypeError: 'int' object is not iterable",
        )

    def test_custom_iterator_source_remains_fail_closed(self):
        with pytest.raises(NativeBuildError, match="native zip source type 'object:CustomSource' is not supported"):
            compare_native_to_cpython(
                "clase CustomSource:\n"
                "    funcion __iter__(self):\n"
                "        devolver iter([1, 2])\n"
                "list(zip(CustomSource()))\n"
            )


class W17DeleteSlices(unittest.TestCase):
    def test_deletes_bytearray_slices_including_extended_and_empty(self):
        _assert_matches(
            self,
            "data = bytearray(b'abcdef')\n"
            "borrar data[1:6:2]\n"
            "borrar data[1:-1]\n"
            "borrar data[20:20]\n"
            "print(data)\n"
            "borrar data[:]\n"
            "print(data)\n",
        )

    def test_zero_step_raises_value_error(self):
        _assert_same_exception(
            self,
            "data = bytearray(b'abc')\n"
            "borrar data[::0]\n",
            b"ValueError: slice step cannot be zero",
        )

    def test_immutable_bytes_slice_deletion_raises_type_error(self):
        _assert_same_exception(
            self,
            "data = b'abc'\n"
            "borrar data[:]\n",
            b"TypeError: 'bytes' object does not support item deletion",
        )

    def test_custom_slice_target_remains_fail_closed(self):
        with pytest.raises(NativeBuildError, match="native del slice target of type 'object:CustomTarget' is not supported"):
            compare_native_to_cpython(
                "clase CustomTarget:\n"
                "    pasar\n"
                "target = CustomTarget()\n"
                "borrar target[:]\n"
            )


if __name__ == "__main__":
    unittest.main()
