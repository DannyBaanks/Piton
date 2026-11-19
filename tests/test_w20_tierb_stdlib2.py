"""Parity regression tests for Tier B stdlib2.py (Decimal truediv return kind & local shadow of module state)."""
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


class TierBStdlib2DecimalParity(unittest.TestCase):
    def test_decimal_truediv_and_getcontext_prec_matches_cpython(self):
        _assert_matches(
            self,
            "desde decimal importar Decimal, getcontext\n"
            "imprimir(round(Decimal('0.70') * Decimal('1.05'), 2))\n"
            "imprimir(Decimal('1.00') % Decimal('.10'))\n"
            "getcontext().prec = 36\n"
            "imprimir(Decimal(1) / Decimal(7))\n",
        )

    def test_user_truediv_returning_object_keeps_object_type(self):
        _assert_matches(
            self,
            "clase Caja:\n"
            "    funcion __init__(self, v):\n"
            "        self.v = v\n"
            "    funcion __truediv__(self, otro):\n"
            "        devolver Caja(self.v * 10 + otro.v)\n"
            "    funcion __repr__(self):\n"
            "        devolver f'Caja({self.v})'\n"
            "c = Caja(3) / Caja(4)\n"
            "imprimir(c, c.v)\n",
        )


if __name__ == "__main__":
    unittest.main()
