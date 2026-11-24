"""Differential parity checks for native system-zlib integration."""
from __future__ import annotations

import unittest
import zlib

from piton.native_differential import compare_native_to_cpython


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    result = compare_native_to_cpython(source)
    testcase.assertEqual(
        result.native.returncode,
        result.oracle.returncode,
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}; oracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(
        result.native.stdout,
        result.oracle.stdout,
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}; oracle stderr: {result.oracle.stderr!r}",
    )


class ZlibNativeParity(unittest.TestCase):
    def test_compress_default_matches_cpython_bytes(self):
        _assert_matches(
            self,
            "importar zlib\n"
            "imprimir(zlib.compress(b'data').hex())\n",
        )

    def test_compress_levels_match_cpython_bytes(self):
        _assert_matches(
            self,
            "importar zlib\n"
            "d = b'hola hola hola hola mundo' * 20\n"
            "imprimir([zlib.compress(d, n).hex() para n en (1, 6, 9)])\n",
        )

    def test_compress_round_trip_matches_cpython(self):
        _assert_matches(
            self,
            "importar zlib\n"
            "d = bytes(range(256)) * 4\n"
            "imprimir(zlib.decompress(zlib.compress(d)) == d, len(zlib.compress(d)))\n",
        )

    def test_crc32_and_adler32_match_cpython(self):
        _assert_matches(
            self,
            "importar zlib\n"
            "imprimir(zlib.crc32(b'abc'), zlib.adler32(b'abc'))\n",
        )

    def test_empty_input_and_existing_wbits_15_form_match(self):
        _assert_matches(
            self,
            "importar zlib\n"
            "empty = zlib.compress(b'')\n"
            "packed = zlib.compress(b'data', level=0, wbits=15)\n"
            "imprimir(empty.hex())\n"
            "imprimir(zlib.decompress(empty) == b'')\n"
            "imprimir(packed.hex())\n"
            "imprimir(zlib.decompress(packed) == b'data')\n",
        )

    def test_decompresses_a_cpython_generated_stream(self):
        data = b"dynamic Huffman input; " * 50
        compressed = zlib.compress(data)
        _assert_matches(
            self,
            "importar zlib\n"
            f"packed = {compressed!r}\n"
            f"expected = {data!r}\n"
            "imprimir(zlib.decompress(packed) == expected)\n",
        )

    def test_raw_wbits_matches_cpython(self):
        _assert_matches(
            self,
            "importar zlib\n"
            "imprimir(zlib.compress(b'data', level=0, wbits=-15))\n",
        )


if __name__ == "__main__":
    unittest.main()
