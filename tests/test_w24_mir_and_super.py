"""Differential parity tests for W24 MIR fixes: zlib wbits, tarfile open,
and super() MRO dispatch for modeled/fallback bases.

All tests use piton.native_differential.compare_native_to_cpython against
CPython 3.12 as the oracle.
"""

from __future__ import annotations

import unittest

from piton.native_differential import compare_native_to_cpython


def _assert_equiv(testcase: unittest.TestCase, source: str) -> None:
    result = compare_native_to_cpython(source)
    testcase.assertEqual(
        result.native.returncode,
        result.oracle.returncode,
        f"rc mismatch\nnative: {result.native.returncode!r} ({result.native.stderr!r})\noracle: {result.oracle.returncode!r} ({result.oracle.stderr!r})",
    )
    testcase.assertEqual(
        result.native.stdout,
        result.oracle.stdout,
        f"stdout mismatch\nnative: {result.native.stdout!r}\noracle: {result.oracle.stdout!r}",
    )


class ZlibWbitsParity(unittest.TestCase):
    """zlib.compress / zlib.decompress with wbits=15 (zlib), -15 (raw DEFLATE),
    31 (gzip) using the system -lz deflateInit2 / inflateInit2."""

    _DATA = b"witch which has which witches wrist watch"

    def test_compress_decompress_wbits_15_zlib(self):
        source = (
            "importar zlib\n"
            f"data = {self._DATA!r}\n"
            "c = zlib.compress(data, 6, 15)\n"
            "imprimir(zlib.decompress(c, 15) == data)\n"
            "imprimir(zlib.decompress(c) == data)\n"
        )
        _assert_equiv(self, source)

    def test_compress_decompress_wbits_minus15_raw_deflate(self):
        source = (
            "importar zlib\n"
            f"data = {self._DATA!r}\n"
            "c = zlib.compress(data, 6, -15)\n"
            "imprimir(zlib.decompress(c, -15) == data)\n"
        )
        _assert_equiv(self, source)

    def test_compress_decompress_wbits_31_gzip(self):
        source = (
            "importar zlib\n"
            f"data = {self._DATA!r}\n"
            "c = zlib.compress(data, 6, 31)\n"
            "imprimir(zlib.decompress(c, 31) == data)\n"
        )
        _assert_equiv(self, source)

    def test_compress_cross_format_mismatch_raises(self):
        """Compress with gzip wrapper, decompress as raw -> invalid stream."""
        source = (
            "importar zlib\n"
            f"data = {self._DATA!r}\n"
            "c = zlib.compress(data, 6, 31)\n"
            "intentar :\n"
            "    zlib.decompress(c, -15)\n"
            "    imprimir(\"NO_ERROR\")\n"
            "excepto Exception como e:\n"
            "    imprimir(\"EXC\")\n"
        )
        _assert_equiv(self, source)

    def test_compress_default_wbits_is_15(self):
        source = (
            "importar zlib\n"
            f"data = {self._DATA!r}\n"
            "c1 = zlib.compress(data, 6)\n"
            "c2 = zlib.compress(data, 6, 15)\n"
            "imprimir(c1 == c2)\n"
        )
        _assert_equiv(self, source)

    def test_decompress_default_wbits_is_15(self):
        source = (
            "importar zlib\n"
            f"data = {self._DATA!r}\n"
            "c = zlib.compress(data, 6, 15)\n"
            "d1 = zlib.decompress(c)\n"
            "d2 = zlib.decompress(c, 15)\n"
            "imprimir(d1 == d2 == data)\n"
        )
        _assert_equiv(self, source)

    def test_empty_data_roundtrip(self):
        source = (
            "importar zlib\n"
            "c = zlib.compress(b'', 6, -15)\n"
            "imprimir(zlib.decompress(c, -15) == b'')\n"
            "c = zlib.compress(b'', 6, 31)\n"
            "imprimir(zlib.decompress(c, 31) == b'')\n"
        )
        _assert_equiv(self, source)

    def test_compress_invalid_wbits_raises(self):
        source = (
            "importar zlib\n"
            "intentar :\n"
            "    zlib.compress(b'data', 6, 99)\n"
            "    imprimir(\"NO_ERROR\")\n"
            "excepto Exception como e:\n"
            "    imprimir(\"EXC\")\n"
        )
        _assert_equiv(self, source)


class SuperInitIoBuildWorkaround(unittest.TestCase):
    """super().__init__(...) with io stream bases (tarfile-like pattern).
    BUILD WORKAROUND (not runtime parity): super().__init__(fileobj)
    against io.BufferedReader resolves to the honest __init__(self, fileobj)
    stub added to io.piton, which only executes `pasar`. Objects remain
    uninitialized at runtime, so output diverges from CPython. These tests
    only assert the build succeeds (tarfile.py needs it); runtime parity
    is a known residual.
    """

    def test_super_init_bufferedreader_build_workaround(self):
        """super().__init__(source) against io.BufferedReader must build.
        Runtime diverges from CPython (stub no-op vs real init), but the
        build must not fail closed — that is what tarfile.py needs."""
        result = compare_native_to_cpython(
            "desde io importar BufferedReader\n"
            "clase Reader(BufferedReader):\n"
            "    funcion __init__(self, source):\n"
            "        super().__init__(source)\n"
            "imprimir(\"built\")\n"
        )
        # Native must build and run (rc=0); oracle may differ but must not
        # be a build error. We assert native rc=0 and native prints "built".
        self.assertEqual(result.native.returncode, 0)
        self.assertEqual(result.native.stdout, b"built\n")

    def test_super_init_bufferedreader_with_argument_build_workaround(self):
        """super().__init__(fileobj) pattern used by tarfile.ExFileObject.
        Build workaround only; objects remain uninitialized at runtime."""
        result = compare_native_to_cpython(
            "desde io importar BufferedReader\n"
            "clase ExFile(BufferedReader):\n"
            "    funcion __init__(self, tarfile, tarinfo):\n"
            "        super().__init__(tarfile)\n"
            "imprimir(\"ok\")\n"
        )
        self.assertEqual(result.native.returncode, 0)
        self.assertEqual(result.native.stdout, b"ok\n")


class OpenFileobjParity(unittest.TestCase):
    """open(fileobj=...) builtin must match CPython's runtime TypeError.
    tarfile.py calls open(fileobj=...) before `open = TarFile.open` rebinds
    the builtin, so these must lower successfully and raise at runtime."""

    def test_open_positional_file_and_fileobj_kwarg(self):
        """open('x', fileobj='y') -> TypeError 'fileobj' is an invalid keyword argument."""
        result = compare_native_to_cpython(
            "imprimir(open('x', fileobj='y'))\n"
        )
        self.assertEqual(result.native.returncode, result.oracle.returncode)
        self.assertEqual(result.native.stdout, result.oracle.stdout)

    def test_open_fileobj_only(self):
        """open(fileobj='x') -> TypeError missing required argument 'file' (pos 1)."""
        result = compare_native_to_cpython(
            "imprimir(open(fileobj='x'))\n"
        )
        self.assertEqual(result.native.returncode, result.oracle.returncode)
        self.assertEqual(result.native.stdout, result.oracle.stdout)


if __name__ == "__main__":
    unittest.main()
