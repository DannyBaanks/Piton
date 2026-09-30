"""GLOBAL_DECL_V1: `global g` binds loads/stores to the shared cell.

The probe case (`g = 0`, `funcion f(): global g; g = 1`, `f()`,
`imprimir(g)`) died on the `global` statement itself. Now: MIR lowers
`Global(names)` to a `global_decl` metadata op; both backends pre-scan
module-stored names, declared names and constant initializer types, and
emit the shared cells in file scope (C globals on Linux, .data labels
on Windows) so every function references the same variable.

Scope rules (CPython-compatible for the common patterns):
  - `global g` + write/read -> the shared cell (both backends);
  - write without declaration stays local (unchanged, CPython-correct);
  - read without declaration fails closed with an actionable message
    instead of C spew (CPython allows it; the subset needs the explicit
    opt-in — a follow-up can lift this);
  - params and function-local stores shadow legitimately (unchanged);
  - `global` inside generators fails closed (persisted slot layouts
    would shadow the cell silently).

Bonus fix in the same area: the translator's `global` handling had an
off-by-one that swallowed the next statement's first NAME into the
assigned set — `global g` followed by `imprimir(g)` left `imprimir`
untranslated (a NameError in the CPython oracle path itself).
"""
from __future__ import annotations

import unittest

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.translator import traducir_fuente
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


class GlobalDeclV1(unittest.TestCase):
    def test_write_with_declaration(self):
        _assert_matches(self, "g = 0\nfuncion f():\n    global g\n    g = 1\nf()\nimprimir(g)\n")

    def test_read_with_declaration(self):
        _assert_matches(self, "g = 7\nfuncion f():\n    global g\n    imprimir(g)\nf()\n")

    def test_read_str_with_declaration(self):
        _assert_matches(self, "g = 'hola'\nfuncion f():\n    global g\n    imprimir(g)\nf()\n")

    def test_two_functions_share_cell(self):
        _assert_matches(
            self,
            "g = 0\nfuncion f():\n    global g\n    g = 1\n"
            "funcion h():\n    global g\n    g = g + 10\nf()\nh()\nimprimir(g)\n",
        )

    def test_augmented_assign_with_declaration(self):
        _assert_matches(self, "g = 10\nfuncion f():\n    global g\n    g += 5\nf()\nimprimir(g)\n")

    def test_method_on_global_collection(self):
        _assert_matches(self, "g = [1]\nfuncion f():\n    global g\n    g.append(2)\nf()\nimprimir(g)\n")

    def test_percent_format_over_global_tuple(self):
        _assert_matches(self, "t = (1, 'a')\nfuncion f():\n    global t\n    imprimir('%d-%s' % t)\nf()\n")

    def test_write_without_declaration_stays_local(self):
        _assert_matches(self, "g = 0\nfuncion f():\n    g = 1\nf()\nimprimir(g)\n")

    def test_local_store_shadows(self):
        _assert_matches(self, "g = 0\nfuncion f():\n    g = 5\n    imprimir(g)\nf()\nimprimir(g)\n")

    def test_param_shadows(self):
        _assert_matches(self, "g = 9\nfuncion f(g):\n    imprimir(g)\nf(1)\nimprimir(g)\n")

    def test_read_without_declaration_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("g = 0\nfuncion f():\n    imprimir(g)\nf()\n")

    def test_global_inside_generator_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython(
                "g = 0\nfuncion gen():\n    global g\n    g = 1\n    producir 1\nimprimir('ok')\n"
            )


class TranslatorGlobalV1(unittest.TestCase):
    """The translator must keep translating builtins after a `global` line."""

    def test_builtin_after_global_translates(self):
        out = traducir_fuente("g = 0\nfuncion f():\n    global g\n    imprimir(g)\nf()\n", "<t>")
        self.assertIn("print(g)", out)

    def test_multi_name_global(self):
        out = traducir_fuente("g = 0\nfuncion f():\n    global g, imprimir\n    imprimir(g)\nf()\n", "<t>")
        # `imprimir` is genuinely shadowed here: it must NOT translate
        self.assertIn("imprimir(g)", out)


if __name__ == "__main__":
    unittest.main()
