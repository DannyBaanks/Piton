"""Native zlib compression and decompression for Linux x86_64."""

import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from native_linux import compile_native_linux, run_native_linux


def _native(source: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory(prefix="piton-zlib-") as directory:
        executable = Path(directory) / "prog"
        compile_native_linux(source, executable)
        return run_native_linux(executable, capture_output=True, text=True, timeout=30)


class ZlibNativeV1(unittest.TestCase):
    def test_compress_round_trip_and_rfc1950_compatibility(self):
        data = b"witch which has which witches wrist watch"
        source = (
            "importar zlib\n"
            f"data = {data!r}\n"
            "packed = zlib.compress(data, level=-1)\n"
            "restored = zlib.decompress(packed)\n"
            "imprimir(restored == data)\n"
            "imprimir(len(packed))\n"
            "imprimir(zlib.crc32(restored))\n"
            "empty = zlib.compress(b'')\n"
            "imprimir(zlib.decompress(empty) == b'')\n"
            "imprimir(packed.hex())\n"
        )
        native = _native(source)
        self.assertEqual(native.returncode, 0, native.stderr)
        same, length, checksum, empty_ok, encoded = native.stdout.splitlines()
        self.assertEqual(same, "True")
        # Wave 19 zlib_native emits real RFC1950 DEFLATE streams matching CPython,
        # replacing the old stored-only (len(data) + 11) encoder.
        self.assertEqual(int(length), len(zlib.compress(data)))
        self.assertEqual(int(checksum), zlib.crc32(data))
        self.assertEqual(empty_ok, "True")
        self.assertEqual(zlib.decompress(bytes.fromhex(encoded)), data)

    def test_decompresses_cpython_huffman_streams_and_large_stored_streams(self):
        data = (b"dynamic and fixed Huffman DEFLATE blocks; " * 30) + b"\x00tail"
        compressed = zlib.compress(data)
        dynamic_data = b"lorem ipsum dolor sit amet consectetur adipiscing elit " * 100
        dynamic_compressed = zlib.compress(dynamic_data)
        self.assertEqual((compressed[2] >> 1) & 3, 1)
        self.assertEqual((dynamic_compressed[2] >> 1) & 3, 2)
        source = (
            "importar zlib\n"
            f"packed = {compressed!r}\n"
            f"expected = {data!r}\n"
            "imprimir(zlib.decompress(packed) == expected)\n"
            f"dynamic_packed = {dynamic_compressed!r}\n"
            f"dynamic_expected = {dynamic_data!r}\n"
            "imprimir(zlib.decompress(dynamic_packed) == dynamic_expected)\n"
            "large = b'a' * 70000\n"
            "stored = zlib.compress(large, 0)\n"
            "imprimir(zlib.decompress(stored) == large)\n"
            "imprimir(len(stored))\n"
        )
        native = _native(source)
        self.assertEqual(native.returncode, 0, native.stderr)
        self.assertEqual(native.stdout.splitlines(), ["True", "True", "True", str(70000 + 2 + 10 + 4)])


if __name__ == "__main__":
    unittest.main()
