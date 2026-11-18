"""Differential parity for default argument values, especially string defaults
emitted by ``_emit_call_signature`` in ``piton/linux_x86.py``.

Before the fix a string default such as the strptime format
``"%a %b %d %H:%M:%S %Y"`` was lowered through ``_slot``/``_value``, which for
strings starting with ``%`` emitted ``_name("%a ...")`` -- an undeclared C
identifier (``piton_usr__a__b__d__H__M__S__Y``) -- and the build died with
``NativeBuildError``.

The fix emits string/constant default slots explicitly,
``piton_slot((long)"...", PK_STR)``, so a default that is referenced as a
first-class value builds and behaves exactly like CPython.

Every default-argument form exercised here verifies at PARITY against the
oracle (CPython 3.12): there is no string-default variant that must keep
failing closed, so no string-default FAIL_CLOSED case exists for this fix.
"""
from __future__ import annotations

import unittest

from piton.native_differential import compare_native_to_cpython
from piton.translator import PitonSyntaxError


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    result = compare_native_to_cpython(source)
    testcase.assertEqual(
        result.native.returncode,
        result.oracle.returncode,
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(
        result.native.stdout,
        result.oracle.stdout,
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}",
    )


class StrptimeCBuildFix(unittest.TestCase):
    def test_strptime_percent_format_default_first_class(self):
        """Repro: a '%'-prefixed string default referenced as a first-class value."""
        _assert_matches(
            self,
            """
funcion _strptime_time(data_string, format="%a %b %d %H:%M:%S %Y"):
    devolver len(data_string) + len(format)
fn = _strptime_time
imprimir(fn("abc"))
imprimir(fn("abc", "%d/%m/%Y"))
""",
        )

    def test_plain_string_default_no_arg_and_override(self):
        _assert_matches(
            self,
            """
funcion saludar(nombre="mundo"):
    devolver "hola " + nombre
imprimir(saludar())
imprimir(saludar("PITON"))
""",
        )

    def test_empty_string_default_edge(self):
        _assert_matches(
            self,
            """
funcion vacio(s=""):
    devolver len(s)
imprimir(vacio())
imprimir(vacio("x"))
""",
        )

    def test_mixed_string_and_int_defaults(self):
        _assert_matches(
            self,
            """
funcion repetir(texto, veces=3, sep="-"):
    r = ""
    i = 0
    mientras i < veces:
        r = r + sep + texto
        i = i + 1
    devolver r
imprimir(repetir("a"))
imprimir(repetir("b", 2, ":"))
""",
        )

    def test_string_default_keyword_override(self):
        _assert_matches(
            self,
            """
funcion f(a, b="default"):
    devolver a + ":" + b
imprimir(f("x", b="override"))
imprimir(f("y"))
""",
        )

    def test_string_default_posonly(self):
        _assert_matches(
            self,
            """
funcion f(s="hola", /):
    devolver s
imprimir(f())
imprimir(f("mundo"))
""",
        )

    def test_string_default_kwonly(self):
        _assert_matches(
            self,
            """
funcion f(*, s="hola"):
    devolver s
imprimir(f())
imprimir(f(s="adios"))
""",
        )

    def test_string_default_closure_first_class(self):
        _assert_matches(
            self,
            """
funcion exterior():
    funcion interior(s="%a %b %d"):
        devolver len(s)
    devolver interior
f = exterior()
imprimir(f())
imprimir(f("%m/%Y"))
""",
        )

    def test_global_string_ref_as_default(self):
        _assert_matches(
            self,
            """
MENSAJE = "global"
funcion f(x=MENSAJE):
    devolver x
imprimir(f())
""",
        )

    def test_yield_expression_as_default_still_fails_closed(self):
        """A `producir` (yield) expression as a default is not a constant and
        must keep failing closed (it never reaches `_emit_call_signature`)."""
        with self.assertRaises(PitonSyntaxError):
            compare_native_to_cpython(
                """
funcion f(x=producir 1):
    devolver x
imprimir(f())
"""
            )


if __name__ == "__main__":
    unittest.main()
