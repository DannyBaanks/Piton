"""TRY_ELSE_V1: `intentar/excepto/sino` — the else runs on clean completion.

The probe case (`intentar: ... excepto: ... sino: ...`) died in MIR with
"native try does not support else yet". Now: the try body jumps to a new
else block instead of straight to end/finally; handlers still jump past
it (CPython semantics). Pure MIR change — no new ops, both backends
unchanged.
"""
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


class TryElseV1(unittest.TestCase):
    def test_else_runs_on_clean_completion(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    imprimir('t')\n"
            "excepto Exception:\n"
            "    imprimir('e')\n"
            "sino:\n"
            "    imprimir('s')\n",
        )

    def test_else_skipped_when_handler_runs(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    lanzar ValueError('x')\n"
            "excepto ValueError:\n"
            "    imprimir('c')\n"
            "sino:\n"
            "    imprimir('s')\n",
        )

    def test_else_with_finally_clean(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    imprimir('t')\n"
            "excepto Exception:\n"
            "    imprimir('e')\n"
            "sino:\n"
            "    imprimir('s')\n"
            "finalmente:\n"
            "    imprimir('f')\n",
        )

    def test_else_with_finally_raised(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    lanzar ValueError('x')\n"
            "excepto ValueError:\n"
            "    imprimir('c')\n"
            "sino:\n"
            "    imprimir('s')\n"
            "finalmente:\n"
            "    imprimir('f')\n",
        )

    def test_else_with_returns(self):
        _assert_matches(
            self,
            "funcion f(c):\n"
            "    intentar:\n"
            "        si c:\n"
            "            lanzar ValueError('x')\n"
            "        devolver 1\n"
            "    excepto ValueError:\n"
            "        devolver 2\n"
            "    sino:\n"
            "        devolver 3\n"
            "imprimir(f(Falso), f(Verdadero))\n",
        )


if __name__ == "__main__":
    unittest.main()
