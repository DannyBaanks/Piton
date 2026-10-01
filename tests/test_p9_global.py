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


class NonlocalBindingP15(unittest.TestCase):
    """NONLOCAL_BINDING_V1: bad `no_local` bindings fail closed at build.

    The declaration itself used to be dropped in MIR, so every invalid
    case below ran rc=0 natively while CPython raised SyntaxError. Valid
    declarations change nothing (cell capture already worked): the
    validator only adds rejections. Rules mirrored, message flavor
    included: module-level nonlocal, parameter/nonlocal and
    global/nonlocal conflicts, binding or use before the declaration in
    the same scope, and no binding in any enclosing function scope
    (module-level bindings never count; class bodies are transparent).
    """

    def test_write_through_cell(self):
        _assert_matches(
            self,
            "funcion fuera():\n    x = 1\n    funcion dentro():\n"
            "        no_local x\n        x = 2\n    dentro()\n    imprimir(x)\nfuera()\n",
        )

    def test_read_through_cell(self):
        _assert_matches(
            self,
            "funcion fuera():\n    x = 1\n    funcion dentro():\n"
            "        no_local x\n        imprimir(x)\n    dentro()\nfuera()\n",
        )

    def test_escaping_counter(self):
        _assert_matches(
            self,
            "funcion contador():\n    n = 0\n    funcion inc():\n"
            "        no_local n\n        n = n + 1\n        devolver n\n"
            "    devolver inc\nc = contador()\nimprimir(c())\nimprimir(c())\n",
        )

    def test_independent_counters(self):
        _assert_matches(
            self,
            "funcion contador():\n    n = 0\n    funcion inc():\n"
            "        no_local n\n        n = n + 1\n        devolver n\n"
            "    devolver inc\na = contador()\nb = contador()\n"
            "imprimir(a())\nimprimir(a())\nimprimir(b())\n",
        )

    def test_outer_param(self):
        _assert_matches(
            self,
            "funcion a(p):\n    funcion b():\n        no_local p\n"
            "        p = p + 1\n        devolver p\n    devolver b()\nimprimir(a(1))\n",
        )

    def test_assign_without_declaration_stays_local(self):
        _assert_matches(
            self,
            "funcion fuera():\n    x = 1\n    funcion dentro():\n"
            "        x = 2\n        imprimir(x)\n    dentro()\n    imprimir(x)\nfuera()\n",
        )

    def test_no_binding_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("funcion f():\n    no_local x\n    x = 1\n    devolver x\nimprimir(f())\n")

    def test_module_level_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("no_local x\n")

    def test_module_binding_does_not_count(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("g = 0\nfuncion f():\n    no_local g\n    g = 1\nf()\n")

    def test_param_conflict_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("funcion f(x):\n    no_local x\n    devolver x\nimprimir(f(1))\n")

    def test_global_conflict_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython(
                "x = 0\nfuncion f():\n    no_local x\n    global x\n    devolver x\nimprimir(f())\n"
            )

    def test_assign_before_declaration_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython(
                "x = 0\nfuncion f():\n    x = 1\n    no_local x\n    devolver x\nimprimir(f())\n"
            )

    def test_use_before_declaration_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython(
                "x = 0\nfuncion f():\n    imprimir(x)\n    no_local x\n    x = 5\n    devolver x\nimprimir(f())\n"
            )

    def test_nested_no_binding_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython(
                "funcion a():\n    funcion b():\n        no_local w\n"
                "        w = 2\n        devolver w\n    devolver b()\nimprimir(a())\n"
            )


class ForLoopCaptureP15(unittest.TestCase):
    """FOR_LOOP_CAPTURE_V1: nested functions can capture loop variables.

    Loop targets are LOAD nodes in HIR, so the capture filter (built on
    STORE names) excluded them and any nested read of a loop variable
    died in the C compiler. For-targets now count for capture, and loop
    stores route to the cell when captured (a plain store overwrote the
    cell pointer and segfaulted the next cell_load, observed rc=-11).
    """

    def test_nested_read_of_loop_var(self):
        _assert_matches(
            self,
            "funcion a():\n    para i en rango(3):\n        w = i\n"
            "    funcion b():\n        devolver i\n    devolver b()\nimprimir(a())\n",
        )

    def test_nonlocal_over_loop_var(self):
        _assert_matches(
            self,
            "funcion a():\n    para i en rango(3):\n        w = i\n"
            "    funcion b():\n        no_local i\n        devolver i\n    devolver b()\nimprimir(a())\n",
        )

    def test_loop_var_stays_usable_locally(self):
        # NOTE: a() returns w explicitly: printing a bare implicit-None
        # return diverges on Windows (prints 0, a pre-existing backend gap
        # unrelated to capture — Linux prints None). The point here is
        # that i and w stay readable after the loop, captured or not.
        _assert_matches(
            self,
            "funcion a():\n    para i en rango(3):\n        w = i\n"
            "    imprimir(i)\n    imprimir(w)\n    devolver w\nimprimir(a())\n",
        )

    def test_plain_loop_unchanged(self):
        _assert_matches(
            self,
            "funcion a():\n    total = 0\n    para i en rango(3):\n"
            "        total = total + i\n    devolver total\nimprimir(a())\n",
        )


if __name__ == "__main__":
    unittest.main()
