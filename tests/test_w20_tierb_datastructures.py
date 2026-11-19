"""Parity regression tests for Tier B datastructures.py (zip(*matrix) and generator expression unpacking)."""
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


class TierBDatastructuresZipStarParity(unittest.TestCase):
    def test_zip_star_matrix_transpose_matches_cpython(self):
        _assert_matches(
            self,
            "matrix = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]]\n"
            "imprimir(list(zip(*matrix)))\n",
        )

    def test_genexpr_star_extend_and_invalid_unpack_raises_type_error(self):
        _assert_matches(
            self,
            "imprimir([*(x * 2 para x en range(4))])\n"
            "intentar:\n"
            "    imprimir(list(zip(*[1, 2])))\n"
            "excepto TypeError como e:\n"
            "    imprimir('TypeError', type(e).__name__)\n",
        )


if __name__ == "__main__":
    unittest.main()
