"""Parity checks for arity / argument-binding TypeError messages (wave 20).

Every case compares PITON's native build against CPython 3.12 through
``compare_native_to_cpython``: same exit code and byte-identical stdout. The
error messages are printed by the program itself (``imprimir('TE', e)``), so a
message that differs from CPython's text is a stdout divergence.
"""
from __future__ import annotations

import unittest

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


_CATCH = "excepto TypeError como e:\n    imprimir('TE', e)\n"


class ClosureArityMessage(unittest.TestCase):
    def test_closure_too_many_positional_uses_qualname_and_is_catchable(self):
        _assert_matches(
            self,
            "funcion fab():\n"
            "    k = 3\n"
            "    funcion doble(x):\n"
            "        devolver k * x\n"
            "    devolver doble\n"
            "d = fab()\n"
            "intentar:\n"
            "    d(2, 4)\n"
            + _CATCH
            + "imprimir('after', d(2))\n",
        )

    def test_function_too_few_reports_missing_names_singular_and_plural(self):
        _assert_matches(
            self,
            "funcion g(a, b):\n"
            "    devolver a\n"
            "intentar:\n"
            "    g(1)\n"
            + _CATCH
            + "funcion t(a, b, c):\n"
            "    devolver a\n"
            "intentar:\n"
            "    t()\n"
            + _CATCH
            + "intentar:\n"
            "    t(1)\n"
            + _CATCH,
        )

    def test_function_too_many_plural_and_defaults_from_to(self):
        _assert_matches(
            self,
            "funcion g(a, b):\n"
            "    devolver a\n"
            "funcion h(a, b=1):\n"
            "    devolver a\n"
            "funcion u(a):\n"
            "    devolver a\n"
            "intentar:\n"
            "    g(1, 2, 3)\n"
            + _CATCH
            + "intentar:\n"
            "    h(1, 2, 3)\n"
            + _CATCH
            + "intentar:\n"
            "    u(1, 2)\n"
            + _CATCH
            + "f = lambda x: x\n"
            "intentar:\n"
            "    f(1, 2)\n"
            + _CATCH,
        )

    def test_default_missing_and_lambda_defaults(self):
        _assert_matches(
            self,
            "funcion g(a, b=1):\n"
            "    devolver a\n"
            "intentar:\n"
            "    g()\n"
            + _CATCH
            + "f = lambda x, y=1: x + y\n"
            "intentar:\n"
            "    f()\n"
            + _CATCH
            + "imprimir(f(1), f(1, 2))\n",
        )

    def test_missing_three_names_uses_comma_and_list_form(self):
        _assert_matches(
            self,
            "funcion g(a, b, c):\n"
            "    devolver a\n"
            "intentar:\n"
            "    g()\n"
            + _CATCH,
        )

    def test_method_too_many_is_catchable_and_execution_continues(self):
        _assert_matches(
            self,
            "clase C:\n"
            "    funcion m(este, a):\n"
            "        devolver a\n"
            "intentar:\n"
            "    C().m(1, 2)\n"
            + _CATCH
            + "imprimir('after')\n",
        )

    def test_method_missing_positional_message(self):
        _assert_matches(
            self,
            "clase C:\n"
            "    funcion m(este, a):\n"
            "        devolver a\n"
            "intentar:\n"
            "    C().m()\n"
            + _CATCH,
        )

    def test_keyword_only_missing_and_too_many(self):
        _assert_matches(
            self,
            "funcion g(a, *, k):\n"
            "    devolver a\n"
            "intentar:\n"
            "    g(1)\n"
            + _CATCH
            + "intentar:\n"
            "    g(1, 2, k=3)\n"
            + _CATCH
            + "funcion p(a, b=2, *, k):\n"
            "    devolver a\n"
            "intentar:\n"
            "    p(1, 2, 3, k=4)\n"
            + _CATCH
            + "funcion w(a, *, k, j):\n"
            "    devolver a\n"
            "intentar:\n"
            "    w()\n"
            + _CATCH,
        )

    def test_unexpected_and_multiple_keyword_are_catchable(self):
        _assert_matches(
            self,
            "funcion g(a):\n"
            "    devolver a\n"
            "intentar:\n"
            "    g(1, zz=2)\n"
            + _CATCH
            + "funcion h(a, b):\n"
            "    devolver a\n"
            "intentar:\n"
            "    h(1, 2, a=2)\n"
            + _CATCH
            + "clase C:\n"
            "    funcion m(este, a):\n"
            "        devolver a\n"
            "intentar:\n"
            "    C().m(1, zz=2)\n"
            + _CATCH
            + "intentar:\n"
            "    C().m(1, a=2)\n"
            + _CATCH,
        )

    def test_keyword_calls_that_are_valid_still_bind_like_cpython(self):
        _assert_matches(
            self,
            "funcion g(a, b=2, c=3):\n"
            "    devolver a * 100 + b * 10 + c\n"
            "imprimir(g(1), g(1, c=9), g(1, 5, c=6))\n"
            "funcion k(a, *, k=5):\n"
            "    devolver a + k\n"
            "imprimir(k(1), k(1, k=2))\n"
            "clase C:\n"
            "    funcion m(este, a, b=7):\n"
            "        devolver a + b\n"
            "imprimir(C().m(1), C().m(1, b=2), C().m(b=3, a=4))\n"
            "clase D:\n"
            "    funcion m(este, a, *, k=1):\n"
            "        devolver a + k\n"
            "imprimir(D().m(1), D().m(1, k=2), D().m(5, k=7))\n",
        )

    def test_closure_keyword_and_kwonly_errors(self):
        _assert_matches(
            self,
            "funcion fab():\n"
            "    funcion h(x, y=2):\n"
            "        devolver x + y\n"
            "    devolver h\n"
            "d = fab()\n"
            "intentar:\n"
            "    d(1, zz=3)\n"
            + _CATCH
            + "intentar:\n"
            "    d(1, x=3)\n"
            + _CATCH
            + "intentar:\n"
            "    d(1, 2, 3)\n"
            + _CATCH
            + "imprimir(d(1, y=5), d(1))\n",
        )

    def test_closure_missing_and_kwonly_messages(self):
        _assert_matches(
            self,
            "funcion fab():\n"
            "    funcion h(x, y):\n"
            "        devolver x + y\n"
            "    devolver h\n"
            "d = fab()\n"
            "intentar:\n"
            "    d(1)\n"
            + _CATCH
            + "funcion fab2():\n"
            "    funcion q(x, *, k):\n"
            "        devolver x + k\n"
            "    devolver q\n"
            "e = fab2()\n"
            "intentar:\n"
            "    e(1)\n"
            + _CATCH
            + "imprimir(e(1, k=4))\n",
        )

    def test_nested_closure_qualname_is_full_path(self):
        _assert_matches(
            self,
            "funcion a():\n"
            "    funcion b():\n"
            "        funcion c(x):\n"
            "            devolver x\n"
            "        devolver c\n"
            "    devolver b()\n"
            "d = a()\n"
            "intentar:\n"
            "    d(1, 2)\n"
            + _CATCH,
        )

    def test_constructor_arity_messages_use_init_qualname(self):
        _assert_matches(
            self,
            "clase C:\n"
            "    funcion __init__(este, a):\n"
            "        este.a = a\n"
            "intentar:\n"
            "    C(1, 2)\n"
            + _CATCH
            + "intentar:\n"
            "    C()\n"
            + _CATCH
            + "intentar:\n"
            "    C(a=1, zz=2)\n"
            + _CATCH,
        )

    def test_star_argument_arity_uses_runtime_counts(self):
        _assert_matches(
            self,
            "funcion g(a, b):\n"
            "    devolver a\n"
            "xs = [2, 3]\n"
            "intentar:\n"
            "    g(1, *xs)\n"
            + _CATCH
            + "funcion r(a, b, c):\n"
            "    devolver a\n"
            "funcion run(ys):\n"
            "    devolver r(*ys)\n"
            "intentar:\n"
            "    run([1])\n"
            + _CATCH
            + "funcion v(a, b, *args):\n"
            "    devolver a\n"
            "funcion run2(ys):\n"
            "    devolver v(*ys)\n"
            "intentar:\n"
            "    run2([1])\n"
            + _CATCH,
        )

    def test_positional_only_passed_as_keyword(self):
        _assert_matches(
            self,
            "funcion p(a, b, /, c, d):\n"
            "    devolver a\n"
            "intentar:\n"
            "    p(1, 2, b=3, c=4, d=5)\n"
            + _CATCH
            + "funcion q(a, b, /, **kw):\n"
            "    devolver len(kw)\n"
            "imprimir(q(1, 2, b=3))\n",
        )

    def test_dynamic_callee_arity_from_parameter(self):
        _assert_matches(
            self,
            "funcion g(a, b):\n"
            "    devolver a\n"
            "funcion aplicar(f):\n"
            "    devolver f(1, 2, 3)\n"
            "intentar:\n"
            "    aplicar(g)\n"
            + _CATCH
            + "funcion aplicar2(f):\n"
            "    devolver f(1)\n"
            "intentar:\n"
            "    aplicar2(g)\n"
            + _CATCH,
        )

    def test_method_keyword_call_with_positional_only_params_fails_closed(self):
        # Positional-only parameters bound by keyword are not modelled on the
        # method keyword path: the build must fail closed, not run.
        source = (
            "clase C:\n"
            "    funcion m(este, a, /, b):\n"
            "        devolver a\n"
            "imprimir(C().m(1, b=2))\n"
        )
        with self.assertRaises(NativeBuildError):
            compare_native_to_cpython(source)


if __name__ == "__main__":
    unittest.main()
