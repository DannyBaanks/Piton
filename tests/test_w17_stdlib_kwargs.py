"""W17 parity for native stdlib keyword binding and optional parameters."""
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


class NativeStdlibKeywordArguments(unittest.TestCase):
    def test_zlib_compress_level_and_wbits_keywords_match(self):
        _assert_matches(
            self,
            "importar zlib\n"
            "data = b'keyword arguments use stored DEFLATE blocks'\n"
            "packed_kw = zlib.compress(data, level=0, wbits=15)\n"
            "packed_pos = zlib.compress(data, 0, 15)\n"
            "imprimir(packed_kw.hex())\n"
            "imprimir(packed_kw == packed_pos)\n"
            "imprimir(zlib.decompress(packed_kw) == data)\n",
        )

    def test_zlib_compression_modes_match_cpython(self):
        """wbits=-15 (raw DEFLATE) and wbits=15 (zlib) now match CPython."""
        _assert_matches(
            self,
            "importar zlib\n"
            "imprimir(zlib.compress(b'data', level=0, wbits=-15))\n",
        )
        _assert_matches(
            self,
            "importar zlib\n"
            "imprimir(zlib.compress(b'data', wbits=15))\n",
        )

    def test_zlib_compress_data_remains_positional_only(self):
        with pytest.raises(NativeBuildError, match="positional-only argument 'data'"):
            compare_native_to_cpython(
                "importar zlib\n"
                "imprimir(zlib.compress(data=b'data', level=0))\n"
            )

    def test_statistics_data_and_optional_center_keywords_match(self):
        _assert_matches(
            self,
            "importar statistics\n"
            "data = [1, 2, 4]\n"
            "imprimir(statistics.variance(data=data, xbar=2))\n"
            "imprimir(statistics.pvariance(data=data, mu=2))\n"
            "imprimir(statistics.stdev(data=data, xbar=2))\n"
            "imprimir(statistics.pstdev(data=data, mu=2))\n"
            "imprimir(statistics.stdev(data=data))\n",
        )

    def test_statistics_stdev_keyword_center_matches_with_fractional_mean(self):
        _assert_matches(
            self,
            "importar statistics\n"
            "data = [1, 2, 4]\n"
            "imprimir(statistics.stdev(data=data, xbar=2.5))\n"
            "imprimir(statistics.pstdev(data=data, mu=2.5))\n",
        )

    def test_statistics_centered_stdev_checks_sample_size(self):
        _assert_matches(
            self,
            "importar statistics\n"
            "intentar:\n"
            "    statistics.stdev([1], xbar=1)\n"
            "excepto ValueError:\n"
            "    imprimir('too few data points')\n",
        )

    def test_statistics_from_import_accepts_positional_xbar(self):
        _assert_matches(
            self,
            "desde statistics importar stdev\n"
            "imprimir(stdev([1, 2, 4], 2))\n",
        )


if __name__ == "__main__":
    unittest.main()
