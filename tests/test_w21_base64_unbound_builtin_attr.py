"""Differential coverage for int.from_bytes used as a first-class value (Linux).

Covers `from_bytes = int.from_bytes` as a local, global, closure capture and
callback (map/filter/argument), with CPython 3.12 parity for results and for
the TypeError/ValueError messages exercised here. Known divergence kept out of
the parity set: an exception raised inside `list(map(...))` is swallowed in a
try block (pre-existing, affects every callback, not only int.from_bytes).
"""
from __future__ import annotations

import sys
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


@unittest.skipIf(sys.platform.startswith("win32"), "Linux backend feature")
class IntFromBytesAsValueV1(unittest.TestCase):
    def test_base64_style_alias_in_function_and_module(self):
        _assert_matches(
            self,
            "from_bytes = int.from_bytes\n"
            "datos = b'\\x01\\x02\\x03\\x04\\x05'\n"
            "c = from_bytes(datos[0: 5])\n"
            "imprimir(c, c >> 30, (c >> 20) & 0x3ff)\n"
            "imprimir(from_bytes(b'\\x01\\x02', 'big'), from_bytes(b'\\x01\\x02', 'little'))\n"
            "funcion convertir(d):\n"
            "    fn = int.from_bytes\n"
            "    devolver fn(d, 'little')\n"
            "imprimir(convertir(b'\\x02\\x01'))\n"
            "g = int.from_bytes\n"
            "funcion usar(x):\n"
            "    devolver g(x, 'big')\n"
            "imprimir(usar(b'\\x05\\x06'))\n",
        )

    def test_closure_capture_and_callback_arguments(self):
        _assert_matches(
            self,
            "funcion fabrica():\n"
            "    fn = int.from_bytes\n"
            "    funcion inner(b):\n"
            "        devolver fn(b, 'big')\n"
            "    devolver inner\n"
            "h = fabrica()\n"
            "imprimir(h(b'\\x01\\x02'))\n"
            "funcion aplicar(f, x):\n"
            "    devolver f(x)\n"
            "imprimir(aplicar(int.from_bytes, b'\\x01\\x02'))\n",
        )

    def test_map_and_filter_keep_bigint_and_truthiness(self):
        _assert_matches(
            self,
            "imprimir(list(map(int.from_bytes, [b'\\x01', b'\\x01\\x00', bytearray(b'\\x02')])))\n"
            "imprimir(list(map(int.from_bytes, [[1, 2], [255]])))\n"
            "imprimir(list(map(int.from_bytes, [b'\\x01' * 9, b'\\x00'])))\n"
            "imprimir(list(filter(int.from_bytes, [b'\\x00', b'\\x01', b'\\x00\\x00', b'\\x02'])))\n"
            "para v en map(int.from_bytes, [b'\\x01\\x02', b'\\x03']):\n"
            "    imprimir(v)\n",
        )

    def test_keyword_arguments_through_value_form(self):
        _assert_matches(
            self,
            "f = int.from_bytes\n"
            "imprimir(f(b'\\xff\\xfe', 'big', signed=Verdadero), f(b'\\xff\\xfe', signed=Falso))\n"
            "imprimir(f(bytes=b'\\x01\\x02', byteorder='little'))\n"
            "imprimir(f(b'\\xff' * 9, 'big', signed=Falso))\n"
            "imprimir(f(b'\\x80' + b'\\x00' * 8, 'little', signed=Verdadero))\n",
        )

    def test_argument_errors_match_cpython_messages(self):
        _assert_matches(
            self,
            "f = int.from_bytes\n"
            "intentar:\n"
            "    f(5)\n"
            "excepto TypeError como e:\n"
            "    imprimir(str(e))\n"
            "intentar:\n"
            "    f(b'\\x01', 'medio')\n"
            "excepto ValueError como e:\n"
            "    imprimir(str(e))\n"
            "intentar:\n"
            "    f('ab')\n"
            "excepto TypeError como e:\n"
            "    imprimir(str(e))\n"
            "intentar:\n"
            "    f(Nada)\n"
            "excepto TypeError como e:\n"
            "    imprimir(str(e))\n"
            "intentar:\n"
            "    f(b'\\x01', foo=1)\n"
            "excepto TypeError como e:\n"
            "    imprimir(str(e))\n"
            "intentar:\n"
            "    f(b'\\x01', 'big', byteorder='big')\n"
            "excepto TypeError como e:\n"
            "    imprimir(str(e))\n"
            "intentar:\n"
            "    f(b'\\x01', 'big', signed=1, foo=2)\n"
            "excepto TypeError como e:\n"
            "    imprimir(str(e))\n",
        )

    def test_unsupported_forms_still_fail_closed_at_build(self):
        # Static form with a non-literal-type byteorder and wrong arity: build error.
        with pytest.raises(NativeBuildError, match="byteorder must be a string"):
            compare_native_to_cpython(
                "f = int.from_bytes\nimprimir(f(b'\\x01', b'big'))\n"
            )
        # Direct (statically typed) calls with bad arity fail closed at build.
        with pytest.raises(NativeBuildError, match="takes 1-2 positional arguments"):
            compare_native_to_cpython(
                "f = int.from_bytes\nimprimir(f(b'\\x01', 'big', 'x'))\n"
            )
        with pytest.raises(NativeBuildError, match="takes 1-2 positional arguments"):
            compare_native_to_cpython("f = int.from_bytes\nimprimir(f())\n")
        # The unbound int.to_bytes value form is still unsupported.
        with pytest.raises(NativeBuildError, match=r"unbound int.to_bytes\(\) is not supported"):
            compare_native_to_cpython("imprimir(int.to_bytes(5, 2))\n")

    def test_result_wider_than_64_bits_through_closure_fails_closed_at_runtime(self):
        # Known limitation: a closure-captured call returns a raw long, which
        # cannot carry a bigint. It must fail loudly, never print a pointer.
        result = compare_native_to_cpython(
            "funcion fabrica():\n"
            "    fn = int.from_bytes\n"
            "    funcion inner(b):\n"
            "        devolver fn(b, 'big')\n"
            "    devolver inner\n"
            "h = fabrica()\n"
            "imprimir(h(b'\\xff' * 9))\n"
        )
        self.assertNotEqual(result.native.returncode, 0)
        self.assertEqual(result.native.stdout, b"")
        self.assertIn(b"NotImplementedError", result.native.stderr)


if __name__ == "__main__":
    unittest.main()
