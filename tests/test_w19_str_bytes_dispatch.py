"""Differential parity for methods on runtime-typed str/bytes parameters."""
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


class StrBytesDispatch(unittest.TestCase):
    def test_isinstance_branch_dispatches_bytes_decode(self):
        _assert_matches(
            self,
            "funcion f(p):\n"
            "    si isinstance(p, bytes):\n"
            "        devolver 'B' + p.decode()\n"
            "    devolver 'S' + p\n"
            "imprimir(f('x'), f(b'y'))\n",
        )

    def test_isinstance_branch_dispatches_str_encode_and_keeps_bytes_result(self):
        _assert_matches(
            self,
            "funcion g(p):\n"
            "    si isinstance(p, str):\n"
            "        p = p.encode()\n"
            "    devolver (len(p), p[:2])\n"
            "imprimir(g('hola'), g(b'adios'))\n",
        )

    def test_join_dispatches_by_separator_kind_and_handles_empty_iterables(self):
        _assert_matches(
            self,
            "funcion h(partes, sep):\n"
            "    devolver sep.join(partes)\n"
            "imprimir(h(['a', 'b'], '-'), h([b'a', b'b'], b'+'))\n"
            "imprimir(h([], '-'), h([], b'+'))\n",
        )

    def test_unimplemented_dynamic_str_bytes_method_stays_fail_closed(self):
        with pytest.raises(
            NativeBuildError,
            match=r"dynamic str/bytes method 'removeprefix' is not supported",
        ):
            compare_native_to_cpython(
                "funcion f(p):\n"
                "    si isinstance(p, bytes):\n"
                "        devolver p.removeprefix(b'x')\n"
                "    devolver p.removeprefix('x')\n"
                "imprimir(f('xyz'), f(b'xyz'))\n"
            )


if __name__ == "__main__":
    unittest.main()
