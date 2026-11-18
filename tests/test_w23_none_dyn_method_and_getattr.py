"""W23 none_dyn_method_and_getattr: method_call / get_attr sobre una variable
cuyo valor es None en una rama y un valor no-None de tipo estatico
desconocido (p.ej. `d.get(k)`) en otra. Despacho en runtime por kind de la
caja: None lanza el AttributeError de CPython; los kinds soportados despachan
al metodo real; el resto falla cerrado (build) o con error explicito.
Paridad con CPython 3.12 (stdout + exit code), o fail-closed.
"""
from __future__ import annotations

import unittest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    result = compare_native_to_cpython(source)
    testcase.assertEqual(
        result.native.returncode, result.oracle.returncode,
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}",
    )
    testcase.assertEqual(
        result.native.stdout, result.oracle.stdout,
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}",
    )


def _fn(expr: str, fallback: str = '"E"') -> str:
    return (
        "funcion f(d, flag):\n"
        "    st = None\n"
        "    si flag:\n"
        "        st = d.get(\"k\")\n"
        "    intentar:\n"
        f"        devolver {expr}\n"
        "    excepto AttributeError:\n"
        f"        devolver {fallback}\n"
    )


class NoneDynMethodParity(unittest.TestCase):
    def test_str_upper_value_and_none(self):
        _assert_matches(self, _fn("st.upper()") +
                        "imprimir(f({\"k\": \"abc\"}, Verdadero), f({\"k\": \"abc\"}, Falso))\n")

    def test_str_strip_len(self):
        _assert_matches(self, _fn("len(st.strip())", "-1") +
                        "imprimir(f({\"k\": \" abc \"}, Verdadero), f({\"k\": \"x\"}, Falso))\n")

    def test_none_branch_raises_attribute_error(self):
        _assert_matches(self, _fn("st.upper()") +
                        "imprimir(f({\"k\": \"abc\"}, Falso))\n")

    def test_uncaught_none_method_same_exception(self):
        _assert_matches(
            self,
            "funcion f(d, flag):\n"
            "    st = None\n"
            "    si flag:\n"
            "        st = d.get(\"k\")\n"
            "    devolver st.upper()\n"
            "imprimir(f({\"k\": \"abc\"}, Falso))\n",
        )

    def test_unsupported_method_name_fails_closed(self):
        with self.assertRaises(NativeBuildError):
            compare_native_to_cpython(
                _fn("st.nombre_inexistente()") +
                "imprimir(f({\"k\": \"abc\"}, Verdadero))\n"
            )

    def test_str_count_parity(self):
        _assert_matches(self, _fn("st.count(\"a\")", "-1") +
                        "imprimir(f({\"k\": \"banana\"}, Verdadero), f({\"k\": \"x\"}, Falso))\n")


if __name__ == "__main__":
    unittest.main()
