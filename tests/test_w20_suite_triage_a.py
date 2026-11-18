"""Differential parity tests for Wave 20 suite_triage_a regressions (makedirs on slot path, list subscript_store with slot index)."""
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


class TestSuiteTriageAParity(unittest.TestCase):
    def test_list_subscript_store_with_isinstance_slot_index(self):
        _assert_matches(
            self,
            "tabla = ['a', 'b', 'c']\n"
            "funcion fijar(idx, val):\n"
            "    si no isinstance(idx, int):\n"
            "        lanzar TypeError('no int')\n"
            "    tabla[idx] = val\n"
            "fijar(1, 'Z')\n"
            "imprimir(tabla)\n",
        )

    def test_makedirs_on_dirname_and_invalid_path_type_error(self):
        _assert_matches(
            self,
            "importar os, tempfile\n"
            "d = tempfile.mkdtemp()\n"
            "p = os.path.join(d, 'sub', 'archivo.txt')\n"
            "os.makedirs(os.path.dirname(p), exist_ok=Verdadero)\n"
            "imprimir(os.path.isdir(os.path.dirname(p)))\n"
            "intentar:\n"
            "    os.makedirs(123)\n"
            "excepto TypeError:\n"
            "    imprimir('TypeError')\n",
        )


if __name__ == "__main__":
    unittest.main()
