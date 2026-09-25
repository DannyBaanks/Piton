from __future__ import annotations

import os
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest

from piton.x86 import NativeBuildError, compile_native, compile_native_files, _scan_native_modules
from tests.win64_toolchain import install as _install_win64_gate, requires_windows, run_native, run_oracle

_install_win64_gate()
from piton.native_differential import compare_native_to_cpython
from piton.translator import traducir_fuente
from piton.parser import parse
from piton.lower import lower_cst_to_hir
from piton.mir import MIRLowerer


class Phase5Gates(unittest.TestCase):
    def build_run(self, source: str) -> tuple[str, bytes]:
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            executable = compile_native(source, Path(directory) / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
            return completed.stdout.decode(), executable.read_bytes()

    def test_x86_hello_and_no_python_dependency(self):
        output, image = self.build_run('imprimir("hola")\n')
        self.assertEqual(output, "hola\r\n")
        self.assertNotIn(b"python", image.lower())

    def test_x86_arithmetic_and_branch(self):
        output, _ = self.build_run("x = 2\nsi x > 1:\n    imprimir(x + 3)\n")
        self.assertEqual(output, "5\r\n")

    def test_x86_function(self):
        output, _ = self.build_run("funcion doble(x):\n    devolver x * 2\nimprimir(doble(4))\n")
        self.assertEqual(output, "8\r\n")

    def test_x86_loop(self):
        output, _ = self.build_run(
            "i = 0\n"
            "mientras i < 3:\n"
            "    imprimir(i)\n"
            "    i = i + 1\n"
        )
        self.assertEqual(output, "0\r\n1\r\n2\r\n")

    def test_x86_bool_none_and_string_comparison(self):
        output, _ = self.build_run(
            "imprimir(Verdadero)\n"
            "imprimir(Falso)\n"
            "imprimir(Nada)\n"
            'imprimir("a" < "b")\n'
        )
        self.assertEqual(output, "True\r\nFalse\r\nNone\r\nTrue\r\n")

    def test_x86_floor_division_and_modulo_match_python(self):
        output, _ = self.build_run(
            "imprimir(-7 // 3)\n"
            "imprimir(-7 % 3)\n"
            "imprimir(7 // -3)\n"
            "imprimir(7 % -3)\n"
        )
        self.assertEqual(output, "-3\r\n2\r\n-3\r\n-2\r\n")

    def test_x86_true_division_matches_python(self):
        # DIVISION_V1: int/int is correctly rounded like CPython (also past 2**53).
        result = compare_native_to_cpython(
            "imprimir(7 / 2)\nimprimir(-7 / 2)\nimprimir(1 / 3)\nimprimir(0 / -5)\n"
            "imprimir(9007199254740993 / 1)\nimprimir(7.0 / 2.0)\nimprimir(10 / 4.0)\n"
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_conditional_expression(self):
        # `a si c sino b`: lazy, right-associative, not confused with a comprehension filter.
        result = compare_native_to_cpython(
            "funcion f():\n    imprimir(\"llamada\")\n    devolver 7\n"
            "x = 5\nimprimir(1 si x < 3 sino 2 si x < 6 sino 3)\n"
            "imprimir(f() si x > 9 sino 0)\n"
            "imprimir(\"par\" si x % 2 == 0 sino \"impar\")\n"
            "xs = [1, 2, 3, 4]\nimprimir([v para v en xs si v > 2])\nimprimir([v si v > 2 sino 0 para v en xs])\n"
        )
        self.assertTrue(result.equivalent, result)

    def test_float_literal_forms_parse_like_python(self):
        from piton.lexer import tokenize
        for literal in ("1.5e10", "2.5e-3", "1.", ".5", "1.e5", "1.7976931348623157e308"):
            with self.subTest(literal=literal):
                numbers = [t.value for t in tokenize(literal + "\n") if t.type.name == "NUMBER"]
                self.assertEqual(numbers, [literal])

    def test_x86_float_repr_matches_python(self):
        # FLOAT_REPR_V1: shortest round-trip repr, CPython's exponent layout.
        result = compare_native_to_cpython(
            "imprimir(0.1 + 0.2)\nimprimir(1e16)\nimprimir(-0.0)\nimprimir(1e-5)\nimprimir(0.0001)\n"
            "imprimir(123456789.125)\nimprimir(5e-324)\nimprimir(1.7976931348623157e308)\n"
            "imprimir(2.5 * 4.0)\nimprimir(texto(1.0 / 3.0))\n"
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_math_sqrt_domain_error_is_catchable(self):
        result = compare_native_to_cpython(
            "importar math\nintentar:\n    imprimir(math.sqrt(-1))\nexcepto ValueError:\n    imprimir(\"dominio\")\n"
            "imprimir(math.sqrt(2))\n"
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_runtime_exception_skips_mismatched_handler(self):
        # A runtime-raised exception must not land in an inner handler of another type.
        result = compare_native_to_cpython(
            "intentar:\n    intentar:\n        imprimir(chr(-1))\n    excepto TypeError:\n        imprimir(\"mal\")\n"
            "excepto ValueError:\n    imprimir(\"bien\")\n"
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_division_by_zero_is_catchable(self):
        result = compare_native_to_cpython(
            "intentar:\n    imprimir(5 // 0)\nexcepto ZeroDivisionError:\n    imprimir(1)\n"
            "intentar:\n    imprimir(5 % 0)\nexcepto ArithmeticError:\n    imprimir(2)\n"
            "intentar:\n    imprimir(5 / 0)\nexcepto Exception:\n    imprimir(3)\n"
            "intentar:\n    intentar:\n        imprimir(5.0 / 0)\n    excepto ValueError:\n        imprimir(0)\n"
            "excepto ZeroDivisionError:\n    imprimir(4)\n"
        )
        self.assertTrue(result.equivalent, result)

    def test_native_differential_corpus(self):
        corpus = (
            "imprimir(Verdadero)\nimprimir(Falso)\nimprimir(Nada)\n",
            "imprimir(-7 // 3)\nimprimir(-7 % 3)\nimprimir(7 // -3)\nimprimir(7 % -3)\n",
            'imprimir("a" < "b")\nimprimir("z" == "z")\n',
            'imprimir("pit" + "on")\n',
            "imprimir(6 & 3)\nimprimir(4 | 1)\nimprimir(7 ^ 3)\nimprimir(2 << 3)\nimprimir(-8 >> 2)\n",
            'imprimir("a" == 1)\nimprimir(Nada != 0)\n',
            "x = 2\nsi x > 1:\n    imprimir(x + 3)\n",
        )
        for source in corpus:
            with self.subTest(source=source):
                result = compare_native_to_cpython(source)
                self.assertTrue(result.equivalent, result)

    def test_x86_string_addition_and_truthiness(self):
        source = (
            'imprimir("a" + "b")\n'
            'si "":\n    imprimir("bad")\nsino:\n    imprimir("empty")\n'
            'si "x":\n    imprimir("truthy")\n'
            'imprimir(no "")\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_multi_inheritance_method_resolution(self):
        source = (
            'clase A:\n'
            '    funcion saludo(self):\n'
            '        devolver 1\n'
            'clase B:\n'
            '    funcion saludo(self):\n'
            '        devolver 2\n'
            'clase C(A, B):\n'
            '    funcion doble(self):\n'
            '        devolver self.saludo() + self.saludo()\n'
            'c = C()\n'
            'imprimir(c.saludo())\n'
            'imprimir(c.doble())\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    # ── MODULE_METADATA_V1 ──────────────────────────────────────────────

    # ── IMPORT_RELATIVE_V1 ──────────────────────────────────────────────

    # ── IMPORT_STAR_V1 ──────────────────────────────────────────────────

    # ── IMPORT_CYCLIC_V1 ────────────────────────────────────────────────

    # ── TASK_SCHEDULER_V1 (M9): create_task / await task / gather / sleep(0) / cancel ──

    # ── Semantic Differential Corpus (25 programs) ──────────────────────

    # ── FUNCTION_KEYWORD_ARGS_V1 ─────────────────────────────────────────

    # ── FUNCTION_STARARGS_V1 ─────────────────────────────────────────────

    def test_x86_starargs_pure_empty_and_many(self):
        result = compare_native_to_cpython(
            'funcion contar(*args):\n'
            '    devolver longitud(args)\n'
            'imprimir(contar())\n'
            'imprimir(contar(1, 2, 3, 4, 5))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_starargs_sum_and_subscript(self):
        result = compare_native_to_cpython(
            'funcion resumir(*args):\n'
            '    devolver sum(args) + args[0] * 10\n'
            'imprimir(resumir(2, 3, 4))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_starargs_fixed_and_default(self):
        result = compare_native_to_cpython(
            'funcion peso(base=1, *extras):\n'
            '    devolver base * 10 + longitud(extras)\n'
            'imprimir(peso())\n'
            'imprimir(peso(5, 7, 9))\n'
            'imprimir(peso(base=4))\n'
        )
        self.assertTrue(result.equivalent, result)

    # ── FUNCTION_KWARGS_V1 ───────────────────────────────────────────────

    def test_x86_rejects_incompatible_ordering(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "ordering not supported"):
                compile_native('imprimir("a" < 1)\n', Path(directory) / "program.exe")

    def test_x86_supports_more_than_four_arguments_via_frame_abi(self):
        result = compare_native_to_cpython(
            "funcion f(a, b, c, d, e):\n"
            "    devolver a\n"
            "imprimir(f(1, 2, 3, 4, 5))\n"
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_variadic_closure_escape(self):
        result = compare_native_to_cpython(
            "funcion fabrica():\n"
            "    base = 10\n"
            "    funcion escapada(x, *resto):\n"
            "        devolver base + x + sum(resto)\n"
            "    devolver escapada\n"
            "f = fabrica()\n"
            "imprimir(f(1, 2, 3))\n"
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_iter_callable_sentinel(self):
        result = compare_native_to_cpython(
            "funcion contador():\n"
            "    x = 3\n"
            "    funcion fuente():\n"
            "        no_local x\n"
            "        x = x - 1\n"
            "        devolver x\n"
            "    devolver fuente\n"
            "f = contador()\n"
            "it = iter(f, 0)\n"
            "imprimir(next(it))\n"
            "imprimir(next(it))\n"
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_next_with_default(self):
        result = compare_native_to_cpython(
            "xs = [7]\n"
            "it = iter(xs)\n"
            "imprimir(next(it))\n"
            "imprimir(next(it, -1))\n"
            "imprimir(next(it, -2))\n"
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_integer_collections(self):
        corpus = (
            "imprimir([1, 2, 3])\nimprimir(longitud([1, 2, 3]))\nimprimir([4, 5][-1])\n",
            "imprimir((1,))\nimprimir(longitud((1, 2)))\nimprimir((7, 8)[0])\n",
            "imprimir({1: 2, 3: 4})\nimprimir(longitud({1: 2, 3: 4}))\nimprimir({1: 2}[1])\n",
            "imprimir({1, 2})\nimprimir(longitud({1, 2, 2}))\n",
        )
        for source in corpus:
            with self.subTest(source=source):
                result = compare_native_to_cpython(source)
                self.assertTrue(result.equivalent, result)

    def test_x86_float_subset_and_bigint_literals(self):
        corpus = (
            "imprimir(1.5)\nimprimir(-1.5)\nimprimir(1.25 + 2.5)\nimprimir(3.0 * 0.5)\nimprimir(1.5 < 2)\n",
            "imprimir(1180591620717411303424)\nimprimir(-1180591620717411303424)\n",
        )
        for source in corpus:
            with self.subTest(source=source):
                result = compare_native_to_cpython(source)
                self.assertTrue(result.equivalent, result)

    def test_x86_collection_heap_cleanup_under_reassignment(self):
        source = (
            "i = 0\n"
            "x = [0]\n"
            "mientras i < 100:\n"
            "    x = [i, i + 1]\n"
            "    i = i + 1\n"
            "imprimir(longitud(x))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_immutable_scalar_closure(self):
        source = (
            "funcion exterior(x):\n"
            "    factor = 3\n"
            "    funcion interior(valor):\n"
            "        devolver x + factor * valor\n"
            "    devolver interior(4)\n"
            "imprimir(exterior(2))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_escaped_closure_callable_later(self):
        source = (
            "funcion fabricar(x):\n"
            "    funcion suma(m):\n"
            "        devolver x + m\n"
            "    devolver suma\n"
            "f = fabricar(40)\n"
            "imprimir(f(2))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_nonlocal_counter_mutation(self):
        source = (
            "funcion contador():\n"
            "    n = 0\n"
            "    funcion sube(paso):\n"
            "        no_local n\n"
            "        n = n + paso\n"
            "        devolver n\n"
            "    devolver sube\n"
            "c = contador()\n"
            "imprimir(c(5))\n"
            "imprimir(c(7))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_nonlocal_write_only_capture(self):
        source = (
            "funcion contador():\n"
            "    n = 0\n"
            "    funcion home():\n"
            "        no_local n\n"
            "        n = n + 1\n"
            "    home()\n"
            "    home()\n"
            "    devolver n\n"
            "imprimir(contador())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_callback_plain_function_and_closure(self):
        source = (
            "funcion cuadrado(n):\n"
            "    devolver n*n\n"
            "funcion aplicar(fn, n):\n"
            "    devolver fn(n)\n"
            "imprimir(aplicar(cuadrado, 9))\n"
            "funcion fab():\n"
            "    k = 3\n"
            "    funcion doble(x):\n"
            "        devolver k * x\n"
            "    devolver doble\n"
            "d = fab()\n"
            "imprimir(aplicar(d, 5))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_fn_stored_in_variable(self):
        source = (
            "funcion cuadrado(n):\n"
            "    devolver n*n\n"
            "g = cuadrado\n"
            "imprimir(g(9))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_closure_two_cells_two_arguments(self):
        source = (
            "funcion fab():\n"
            "    a = 1\n"
            "    b = 2\n"
            "    funcion suma(x, m):\n"
            "        devolver a + b + x + m\n"
            "    devolver suma\n"
            "s = fab()\n"
            "imprimir(s(10, 20))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_independent_closure_instances(self):
        source = (
            "funcion contador():\n"
            "    n = 0\n"
            "    funcion sube(paso):\n"
            "        no_local n\n"
            "        n = n + paso\n"
            "        devolver n\n"
            "    devolver sube\n"
            "a = contador()\n"
            "b = contador()\n"
            "imprimir(a(1))\n"
            "imprimir(a(2))\n"
            "imprimir(b(10))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_nonlocal_mutual_swap_of_cells(self):
        source = (
            "funcion fab():\n"
            "    m = 1\n"
            "    k = 2\n"
            "    funcion par(x):\n"
            "        no_local m\n"
            "        no_local k\n"
            "        t = m\n"
            "        m = k\n"
            "        k = t\n"
            "        devolver m + k + x\n"
            "    devolver par\n"
            "p = fab()\n"
            "imprimir(p(0))\n"
            "imprimir(p(0))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_chained_closure_factories(self):
        source = (
            "funcion nivel1(x):\n"
            "    funcion nivel2():\n"
            "        funcion nivel3(m):\n"
            "            devolver x + m\n"
            "        devolver nivel3\n"
            "    devolver nivel2\n"
            "a = nivel1(100)\n"
            "b = a()\n"
            "imprimir(b(5))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_closure_arity_fail_closed(self):
        source = (
            "funcion fab():\n"
            "    k = 3\n"
            "    funcion doble(x):\n"
            "        devolver k * x\n"
            "    devolver doble\n"
            "d = fab()\n"
            "imprimir(d(2, 4))\n"
        )
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            executable = compile_native(source, Path(directory) / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
        self.assertEqual(completed.returncode, 2)
        self.assertIn(b"TypeError: closure called with wrong number of arguments", completed.stderr)

    def test_x86_native_recursion_factorial(self):
        source = (
            "funcion factorial(n):\n"
            "    si n <= 1:\n"
            "        devolver 1\n"
            "    devolver n * factorial(n - 1)\n"
            "imprimir(factorial(10))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_native_recursion_in_both_backends(self):
        source = (
            "funcion fibonacci(n):\n"
            "    si n <= 1:\n"
            "        devolver n\n"
            "    devolver fibonacci(n - 1) + fibonacci(n - 2)\n"
            "imprimir(fibonacci(20))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_recursive_closure_with_capture(self):
        source = (
            "funcion crear():\n"
            "    m = 10\n"
            "    funcion filtrar(n):\n"
            "        si n <= 1:\n"
            "            devolver 1 + m - 10\n"
            "        devolver n * filtrar(n - 1)\n"
            "    devolver filtrar(6)\n"
            "imprimir(crear())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_nested_closure_transitive_capture(self):
        source = (
            "funcion outer(a):\n"
            "    b = a + 1\n"
            "    funcion mid(c):\n"
            "        funcion inner(d):\n"
            "            devolver a + b + c + d\n"
            "        devolver inner(1)\n"
            "    devolver mid(2)\n"
            "imprimir(outer(10))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_closure_frame_abi_more_than_four_captures_and_args(self):
        source = (
            "funcion fabricar():\n"
            "    uno = 1\n"
            "    dos = 2\n"
            "    tres = 3\n"
            "    cuatro = 4\n"
            "    cinco = 5\n"
            "    funcion sumar(a, b, c, d, e):\n"
            "        devolver uno + dos + tres + cuatro + cinco + a + b + c + d + e\n"
            "    devolver sumar\n"
            "f = fabricar()\n"
            "imprimir(f(6, 7, 8, 9, 10))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_typed_raise_caught_by_except(self):
        source = (
            'intentar:\n'
            '    lanzar ValueError("x")\n'
            'excepto ValueError:\n'
            '    imprimir("caught")\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_stop_iteration_is_caught(self):
        source = (
            'it = iter([7])\n'
            'intentar:\n'
            '    imprimir(next(it))\n'
            '    next(it)\n'
            'excepto StopIteration:\n'
            '    imprimir("exhausted")\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_user_defined_iter_and_next(self):
        source = (
            'clase Uno:\n'
            '    funcion __iter__(self):\n'
            '        devolver self\n'
            '    funcion __next__(self):\n'
            '        devolver 7\n'
            'u = Uno()\n'
            'imprimir(next(iter(u)))\n'
            'imprimir(next(u))\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_user_defined_stop_iteration_propagates(self):
        source = (
            'clase Uno:\n'
            '    funcion __init__(self):\n'
            '        self.usado = 0\n'
            '    funcion __iter__(self):\n'
            '        devolver self\n'
            '    funcion __next__(self):\n'
            '        si self.usado:\n'
            '            lanzar StopIteration()\n'
            '        self.usado = 1\n'
            '        devolver 7\n'
            'it = iter(Uno())\n'
            'intentar:\n'
            '    imprimir(next(it))\n'
            '    next(it)\n'
            'excepto StopIteration:\n'
            '    imprimir("exhausted")\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_enumerate_iterator(self):
        source = (
            'it = enumerar([10, 20], 3)\n'
            'imprimir(next(it))\n'
            'imprimir(next(it))\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_reversed_and_zip_iterators(self):
        source = (
            'r = reversed([1, 2, 3])\n'
            'imprimir(next(r))\n'
            'z = zip([1, 2], [10, 20, 30])\n'
            'imprimir(next(z))\n'
            'imprimir(next(z))\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_map_and_filter_iterators(self):
        source = (
            'funcion doble(x):\n'
            '    devolver x * 2\n'
            'funcion es_par(x):\n'
            '    devolver x % 2 == 0\n'
            'imprimir(next(map(doble, [1, 2])))\n'
            'imprimir(next(filter(es_par, [1, 2, 3, 4])))\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_map_accepts_closure_callback(self):
        source = (
            'funcion ejecutar():\n'
            '    m = 3\n'
            '    funcion multiplicar(x):\n'
            '        devolver x * m\n'
            '    imprimir(next(map(multiplicar, [2])))\n'
            'ejecutar()\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_map_accepts_lambda_callback(self):
        source = (
            'imprimir(next(map(lambda x: x * 3, [2])))\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_filter_accepts_lambda_callback(self):
        source = (
            'imprimir(next(filter(lambda x: x > 2, [1, 2, 3, 4])))\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_map_lambda_with_capture(self):
        source = (
            'm = 5\n'
            'imprimir(next(map(lambda x: x + m, [1, 2])))\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_sorted_builtin(self):
        result = compare_native_to_cpython('imprimir(sorted([3, 1, 2]))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_finally_runs_after_try(self):
        source = (
            'intentar:\n'
            '    imprimir("try")\n'
            'finalmente:\n'
            '    imprimir("finally")\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_finally_runs_after_except(self):
        source = (
            'intentar:\n'
            '    lanzar ValueError("boom")\n'
            'excepto ValueError:\n'
            '    imprimir("caught")\n'
            'finalmente:\n'
            '    imprimir("finally")\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_finally_runs_on_no_exception(self):
        source = (
            'x = 1\n'
            'intentar:\n'
            '    x = x + 1\n'
            'finalmente:\n'
            '    imprimir(x)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_except_computation(self):
        source = (
            'intentar:\n'
            '    lanzar ValueError("boom")\n'
            '    imprimir("should not reach")\n'
            'excepto ValueError:\n'
            '    x = 10 + 20\n'
            '    imprimir(x)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_finally_with_try_computation(self):
        source = (
            'x = 0\n'
            'intentar:\n'
            '    x = 5 * 3\n'
            'finalmente:\n'
            '    imprimir(x)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_uncaught_exception_exits(self):
        source = 'lanzar ValueError("uncaught")\n'
        result = compare_native_to_cpython(source)
        self.assertFalse(result.equivalent)
        self.assertIn(b"ValueError", result.native.stderr)

    def test_x86_custom_exception_caught_by_exact_type(self):
        source = (
            "clase ErrorApp(Exception):\n"
            "    funcion __init__(self, m):\n"
            "        self.m = m\n"
            "intentar:\n"
            "    lanzar ErrorApp(\"boom\")\n"
            "excepto ErrorApp:\n"
            "    imprimir(\"caught-app\")\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_custom_exception_caught_by_Exception_base(self):
        source = (
            "clase ErrorApp(Exception):\n"
            "    funcion __init__(self, m):\n"
            "        self.m = m\n"
            "intentar:\n"
            "    lanzar ErrorApp(\"boom\")\n"
            "excepto Exception:\n"
            "    imprimir(\"base\")\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_custom_exception_uncaught_exits_with_message(self):
        source = (
            "clase ErrorApp(Exception):\n"
            "    funcion __init__(self, m):\n"
            "        self.m = m\n"
            "intentar:\n"
            "    lanzar ErrorApp(\"boom\")\n"
            "excepto ValueError:\n"
            "    imprimir(\"no match\")\n"
        )
        result = compare_native_to_cpython(source)
        self.assertFalse(result.equivalent)
        self.assertIn(b"ErrorApp", result.native.stderr)

    def test_x86_custom_exception_subclass_caught_by_base(self):
        source = (
            "clase ErrorBase(Exception):\n"
            "    funcion __init__(self, m):\n"
            "        self.m = m\n"
            "clase ErrorHijo(ErrorBase):\n"
            "    funcion __init__(self, m):\n"
            "        self.m = m\n"
            "intentar:\n"
            "    lanzar ErrorHijo(\"desc\")\n"
            "excepto ErrorBase:\n"
            "    imprimir(\"caught-hijo\")\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_custom_exception_subclass_of_valueerror(self):
        source = (
            "clase ErrorApp(ValueError):\n"
            "    funcion __init__(self, m):\n"
            "        self.m = m\n"
            "intentar:\n"
            "    lanzar ErrorApp(\"boom\")\n"
            "excepto ValueError:\n"
            "    imprimir(\"caught-val\")\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_bare_reraise_to_outer_handler(self):
        source = (
            'intentar:\n'
            '    intentar:\n'
            '        lanzar ValueError("boom")\n'
            '    excepto ValueError:\n'
            '        imprimir("inner")\n'
            '        lanzar\n'
            'excepto ValueError:\n'
            '    imprimir("outer")\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_bare_reraise_custom_to_base(self):
        source = (
            "clase ErrorApp(Exception):\n"
            "    funcion __init__(self, m):\n"
            "        self.m = m\n"
            "intentar:\n"
            "    intentar:\n"
            "        lanzar ErrorApp(\"x\")\n"
            "    excepto ErrorApp:\n"
            "        imprimir(\"inner\")\n"
            "        lanzar\n"
            "excepto Exception:\n"
            "    imprimir(\"outer\")\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_bare_reraise_unhandled_exits(self):
        source = (
            'intentar:\n'
            '    lanzar ValueError("boom")\n'
            'excepto ValueError:\n'
            '    lanzar\n'
            'imprimir("never")\n'
        )
        result = compare_native_to_cpython(source)
        self.assertFalse(result.equivalent)
        self.assertIn(b"ValueError", result.native.stderr)

    def test_x86_bare_reraise_outside_handler_rejected(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "requires an enclosing except handler"):
                compile_native("funcion f():\n    lanzar\nf()\n", Path(directory) / "program.exe")

    def test_x86_bare_reraise_from_catchall_rejected(self):
        # RERAISE_COMPLETE_V1: catch-all bare re-raise now propagates with the
        # runtime type through the static handler chain (no textual change
        # beyond the normal unhandled exit when no enclosing handler exists).
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            source = (
                'intentar:\n'
                '    lanzar ValueError("x")\n'
                'excepto Exception:\n'
                '    lanzar\n'
                'imprimir("done")\n'
            )
            executable = compile_native(source, Path(directory) / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual(completed.returncode, 1)
            self.assertIn("ValueError", completed.stderr.decode(errors="replace"))

    def test_x86_raise_plain_class_rejected(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            source = (
                "clase Punto:\n"
                "    funcion __init__(self, x):\n"
                "        self.x = x\n"
                "lanzar Punto(3)\n"
            )
            with self.assertRaisesRegex(Exception, "subclass Exception"):
                compile_native(source, Path(directory) / "program.exe")

    def test_x86_abs_int(self):
        result = compare_native_to_cpython('imprimir(abs(-42))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_abs_float(self):
        result = compare_native_to_cpython('imprimir(abs(-3.14))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_min_max_int(self):
        result = compare_native_to_cpython('imprimir(min(10, 20))\nimprimir(max(10, 20))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_min_max_float(self):
        result = compare_native_to_cpython('imprimir(min(1.5, 2.5))\nimprimir(max(1.5, 2.5))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_sum_list(self):
        result = compare_native_to_cpython('imprimir(sum([1, 2, 3]))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_builtins_core_v2(self):
        # M14 BUILTINS_CORE_V2: all/any/bin/chr/ord/pow/round con paridad CPython.
        result = compare_native_to_cpython(
            'imprimir(ord("A"))\n'
            'imprimir(ord("ñ"))\n'
            'imprimir(chr(97))\n'
            'imprimir(bin(-7))\n'
            'imprimir(bin(7))\n'
            'imprimir(pow(2, 10))\n'
            'imprimir(pow(2.5, 3))\n'
            'imprimir(pow(2.0, -1))\n'
            'imprimir(any([0, 0, 4]))\n'
            'imprimir(all([1, 0]))\n'
            'imprimir(any([]))\n'
            'imprimir(all([]))\n'
            'imprimir(round(2.5))\n'
            'imprimir(round(3.5))\n'
            'imprimir(round(-2.5))\n'
            'imprimir(round(7))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_type_conversion_v1(self):
        # M14 TYPE_CONVERSION_V1: entero/decimal/texto/booleano con paridad CPython.
        result = compare_native_to_cpython(
            'imprimir(entero("42"))\n'
            'imprimir(entero(" -17 "))\n'
            'imprimir(entero(2.9))\n'
            'imprimir(entero(-2.9))\n'
            'imprimir(entero(Verdadero))\n'
            'imprimir(texto(42))\n'
            'imprimir(texto(2.5))\n'
            'imprimir(texto(3.0))\n'
            'imprimir(texto(15.625))\n'
            'imprimir(texto(Verdadero))\n'
            'imprimir(texto(Nada))\n'
            'imprimir(texto("hola"))\n'
            'imprimir(decimal(3))\n'
            'imprimir(decimal("-0.5"))\n'
            'imprimir(decimal(Verdadero))\n'
            'imprimir(booleano(0))\n'
            'imprimir(booleano(-1))\n'
            'imprimir(booleano(0.0))\n'
            'imprimir(booleano(-0.0))\n'
            'imprimir(booleano("x"))\n'
            'imprimir(booleano(""))\n'
            'imprimir(booleano([]))\n'
            'imprimir(booleano([0]))\n'
            'imprimir(booleano(Nada))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_math_tier1_v1(self):
        # M14 MATH_TIER1_V1: sqrt/floor/ceil/trunc/fabs/gcd + pi/e.
        result = compare_native_to_cpython(
            'importar math\n'
            'imprimir(math.sqrt(9))\n'
            'imprimir(math.floor(2.7))\n'
            'imprimir(math.floor(-2.3))\n'
            'imprimir(math.ceil(2.1))\n'
            'imprimir(math.ceil(-2.9))\n'
            'imprimir(math.trunc(-2.9))\n'
            'imprimir(math.fabs(-3.5))\n'
            'imprimir(math.gcd(12, 18))\n'
            'imprimir(math.gcd(0, 7))\n'
            'imprimir(math.floor(math.pi * 1000))\n'
            'imprimir(math.floor(math.e * 100))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_type_conversion_fail_closed_catchable(self):
        # entero("abc") / decimal("xyz") levantan ValueError CAPTURABLE
        # (antes: acceso invalido 0xC0000005 — exit crash sin excepcion).
        output, _ = self.build_run(
            'intentar:\n'
            '    n = entero("abc")\n'
            '    imprimir(n)\n'
            'excepto ValueError:\n'
            '    imprimir("int-invalido ValueError atrapado")\n'
            'intentar:\n'
            '    m = decimal("xyz")\n'
            '    imprimir(m)\n'
            'excepto ValueError:\n'
            '    imprimir("float-invalido ValueError atrapado")\n'
        )
        self.assertEqual(
            output,
            "int-invalido ValueError atrapado\r\n"
            "float-invalido ValueError atrapado\r\n",
        )

    def test_x86_builtins_core_v2_fail_closed_catchable(self):
        # Las violaciones fuera de la matriz M14 v1 levantan excepciones
        # capturables por intentar/excepto (no terminan el proceso).
        output, _ = self.build_run(
            'intentar:\n'
            '    x = ord("")\n'
            '    imprimir(x)\n'
            'excepto TypeError:\n'
            '    imprimir("ord-vacio TypeError atrapado")\n'
            'intentar:\n'
            '    y = chr(2000000)\n'
            '    imprimir(y)\n'
            'excepto ValueError:\n'
            '    imprimir("chr-rango ValueError atrapado")\n'
            'intentar:\n'
            '    z = pow(2, -1)\n'
            '    imprimir(z)\n'
            'excepto TypeError:\n'
            '    imprimir("pow-neg fail-closed atrapado")\n'
            'intentar:\n'
            '    w = ord("ab")\n'
            '    imprimir(w)\n'
            'excepto TypeError:\n'
            '    imprimir("ord-multichar TypeError atrapado")\n'
        )
        self.assertEqual(
            output,
            "ord-vacio TypeError atrapado\r\n"
            "chr-rango ValueError atrapado\r\n"
            "pow-neg fail-closed atrapado\r\n"
            "ord-multichar TypeError atrapado\r\n",
        )

    def test_x86_type_int(self):
        result = compare_native_to_cpython('imprimir(type(42))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_type_str(self):
        result = compare_native_to_cpython('imprimir(type("hola"))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_type_list(self):
        result = compare_native_to_cpython('imprimir(type([1, 2]))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_type_bool(self):
        result = compare_native_to_cpython('imprimir(type(Verdadero))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_type_none(self):
        result = compare_native_to_cpython('imprimir(type(Nada))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_finite_pure_generator_for_loop(self):
        source = (
            "funcion valores():\n"
            "    producir 2\n"
            "    producir 4\n"
            "    producir 6\n"
            "para valor en valores():\n"
            "    imprimir(valor)\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_list_and_tuple_for_loop(self):
        source = (
            "total = 0\n"
            "para valor en [1, 2, 3]:\n"
            "    total = total + valor\n"
            "para valor en (4, 5):\n"
            "    total = total + valor\n"
            "imprimir(total)\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_iter_and_next_list_tuple(self):
        source = (
            "a = iter([4, 5])\n"
            "b = iter((8, 9))\n"
            "imprimir(next(a))\n"
            "imprimir(next(a))\n"
            "imprimir(next(b))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_iter_and_next_dict_set(self):
        source = (
            'it = iter({"uno": 1, "dos": 2})\n'
            'imprimir(next(it))\n'
            'imprimir(next(it))\n'
            'set_it = iter({1, 2})\n'
            'imprimir(next(set_it) + next(set_it))\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_can_be_assigned(self):
        source = (
            "funcion valores():\n"
            "    producir 1\n"
            "    producir 2\n"
            "x = valores()\n"
            "imprimir(next(x))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_suspends_between_next_calls(self):
        source = (
            "funcion gen():\n"
            "    imprimir(10)\n"
            "    producir 1\n"
            "    imprimir(20)\n"
            "    producir 2\n"
            "x = gen()\n"
            "imprimir(next(x))\n"
            "imprimir(next(x))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_with_params(self):
        source = (
            "funcion gen(n):\n"
            "    producir n\n"
            "    producir n + 1\n"
            "x = gen(5)\n"
            "imprimir(next(x))\n"
            "imprimir(next(x))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_preserves_locals_between_yields(self):
        source = (
            "funcion gen():\n"
            "    valor = 1\n"
            "    producir valor\n"
            "    valor = valor + 1\n"
            "    producir valor\n"
            "x = gen()\n"
            "imprimir(next(x))\n"
            "imprimir(next(x))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_exhaustion_raises_stop_iteration(self):
        source = (
            "funcion gen():\n"
            "    producir 1\n"
            "x = gen()\n"
            "imprimir(next(x))\n"
            "intentar:\n"
            "    next(x)\n"
            "excepto StopIteration:\n"
            "    imprimir(99)\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_instances_keep_independent_state(self):
        source = (
            "funcion gen(n):\n"
            "    producir n\n"
            "    producir n + 10\n"
            "x = gen(1)\n"
            "z = gen(2)\n"
            "imprimir(next(x))\n"
            "imprimir(next(z))\n"
            "imprimir(next(x))\n"
            "imprimir(next(z))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_send_after_next(self):
        source = (
            "funcion echo():\n"
            "    total = 0\n"
            "    producir 0\n"
            "    producir 1\n"
            "    producir 2\n"
            "x = echo()\n"
            "imprimir(next(x))\n"
            "imprimir(x.send(5))\n"
            "imprimir(x.send(7))\n"
            "intentar:\n"
            "    next(x)\n"
            "excepto StopIteration:\n"
            "    imprimir(99)\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_send_first_call_fails(self):
        source = (
            "funcion gen():\n"
            "    producir 1\n"
            "x = gen()\n"
            "intentar:\n"
            "    x.send(7)\n"
            "excepto TypeError:\n"
            "    imprimir(99)\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_throw_at_caller(self):
        source = (
            "funcion gen():\n"
            "    producir 1\n"
            "    producir 2\n"
            "x = gen()\n"
            "imprimir(next(x))\n"
            "intentar:\n"
            "    x.throw(ValueError)\n"
            "excepto ValueError:\n"
            "    imprimir(88)\n"
            "intentar:\n"
            "    next(x)\n"
            "excepto StopIteration:\n"
            "    imprimir(99)\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_close_stops_iteration(self):
        source = (
            "funcion gen():\n"
            "    producir 1\n"
            "    producir 2\n"
            "x = gen()\n"
            "imprimir(next(x))\n"
            "x.close()\n"
            "intentar:\n"
            "    next(x)\n"
            "excepto StopIteration:\n"
            "    imprimir(99)\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_close_is_idempotent(self):
        source = (
            "funcion gen():\n"
            "    producir 1\n"
            "x = gen()\n"
            "x.close()\n"
            "x.close()\n"
            "imprimir(7)\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_exception_binding_as_name(self):
        source = (
            'intentar:\n'
            '    lanzar ValueError("boom")\n'
            'excepto ValueError como e:\n'
            '    imprimir(e)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_exception_binding_as_name_no_message(self):
        source = (
            'intentar:\n'
            '    lanzar ValueError()\n'
            'excepto ValueError como e:\n'
            '    imprimir(e)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_exception_binding_scoped_to_handler(self):
        source = (
            'intentar:\n'
            '    lanzar TypeError("nosuh")\n'
            'excepto TypeError como e:\n'
            '    imprimir(e)\n'
            'imprimir(5)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_yield_from_in_async_fails_closed(self):
        # yield-from over plain iterables is supported (YIELD_FROM_ITERABLE_V1);
        # inside an async function it stays closed, as CPython's SyntaxError.
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "not valid inside an async function"):
                compile_native(
                    "asincrono funcion gen():\n"
                    "    producir desde [1, 2, 3]\n"
                    "imprimir(1)\n",
                    Path(directory) / "program.exe",
                )

    def test_x86_yield_from_delegates_subgen_values(self):
        result = compare_native_to_cpython(
            "funcion sub():\n"
            "    producir 1\n    producir 2\n"
            "funcion outer():\n"
            "    producir desde sub()\n    producir 9\n"
            "g = outer()\n"
            "imprimir(next(g))\nimprimir(next(g))\nimprimir(next(g))\n"
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_yield_from_sends_forward(self):
        result = compare_native_to_cpython(
            "funcion sub():\n"
            "    producir 1\n    producir 2\n"
            "funcion outer():\n"
            "    producir desde sub()\n"
            "g = outer()\n"
            "imprimir(next(g))\nimprimir(g.send(5))\n"
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_return_value_completes(self):
        result = compare_native_to_cpython(
            "funcion sub():\n"
            "    producir 1\n"
            "    devolver 99\n"
            "funcion outer():\n"
            "    producir desde sub()\n"
            "    producir 7\n"
            "g = outer()\n"
            "imprimir(next(g))\nimprimir(next(g))\n"
        )
        self.assertTrue(result.equivalent, result)
    def test_x86_generator_return_with_value(self):
        source = (
            "funcion gen():\n"
            "    producir 1\n"
            "    producir 2\n"
            "    devolver 42\n"
            "x = gen()\n"
            "imprimir(next(x))\n"
            "imprimir(next(x))\n"
            "intentar:\n"
            "    next(x)\n"
            "excepto StopIteration:\n"
            "    imprimir(99)\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_close_raises_generator_exit(self):
        source = (
            "funcion gen():\n"
            "    producir 1\n"
            "x = gen()\n"
            "imprimir(next(x))\n"
            "intentar:\n"
            "    x.close()\n"
            "excepto GeneratorExit:\n"
            "    imprimir(99)\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_yield_from_basic(self):
        source = (
            "funcion generador():\n"
            "    producir desde [1, 2, 3]\n"
            "x = generador()\n"
            "imprimir(next(x))\n"
            "imprimir(next(x))\n"
            "imprimir(next(x))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_generator_yield_from_subgenerator(self):
        source = (
            "funcion sub():\n"
            "    producir 10\n"
            "    producir 20\n"
            "    producir 30\n"
            "funcion gen():\n"
            "    producir desde sub()\n"
            "x = gen()\n"
            "imprimir(next(x))\n"
            "imprimir(next(x))\n"
            "imprimir(next(x))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_simple_class_fields_and_method(self):
        source = (
            "clase Caja:\n"
            "    funcion __init__(self, valor):\n"
            "        self.valor = valor\n"
            "    funcion doble(self):\n"
            "        devolver self.valor * 2\n"
            "caja = Caja(4)\n"
            "imprimir(caja.valor)\n"
            "imprimir(caja.doble())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_simple_inheritance(self):
        source = (
            'clase Base:\n'
            '    funcion __init__(self, x):\n'
            '        self.x = x\n'
            '    funcion get_x(self):\n'
            '        devolver self.x\n'
            'clase Hija(Base):\n'
            '    funcion doble(self):\n'
            '        devolver self.x * 2\n'
            'h = Hija(5)\n'
            'imprimir(h.get_x())\n'
            'imprimir(h.doble())\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_multilevel_inheritance(self):
        source = (
            'clase A:\n'
            '    funcion __init__(self, x):\n'
            '        self.x = x\n'
            '    funcion get_x(self):\n'
            '        devolver self.x\n'
            'clase B(A):\n'
            '    funcion multiply(self, n):\n'
            '        devolver self.x * n\n'
            'clase C(B):\n'
            '    funcion triple(self):\n'
            '        devolver self.x * 3\n'
            'c = C(4)\n'
            'imprimir(c.get_x())\n'
            'imprimir(c.multiply(5))\n'
            'imprimir(c.triple())\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_multi_inheritance_diamond(self):
        source = (
            'clase Base:\n'
            '    funcion mensaje(self):\n'
            '        devolver 5\n'
            'clase Izq(Base):\n'
            '    funcion extra(self):\n'
            '        devolver 3\n'
            'clase Der(Base):\n'
            '    funcion mensaje(self):\n'
            '        devolver 7\n'
            'clase Fin(Izq, Der):\n'
            '    funcion total(self):\n'
            '        devolver self.mensaje() + self.extra()\n'
            'f = Fin()\n'
            'imprimir(f.mensaje())\n'
            'imprimir(f.total())\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_super_chain_method_override(self):
        source = (
            'clase A:\n'
            '    funcion valor(self):\n'
            '        devolver 1\n'
            'clase B(A):\n'
            '    funcion valor(self):\n'
            '        devolver super().valor() + 10\n'
            'clase C(B):\n'
            '    funcion valor(self):\n'
            '        devolver super().valor() + 100\n'
            'c = C()\n'
            'imprimir(c.valor())\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_super_with_user_arguments(self):
        source = (
            'clase A:\n'
            '    funcion add(self, a, b):\n'
            '        devolver a + b\n'
            'clase B(A):\n'
            '    funcion add(self, a, b):\n'
            '        devolver super().add(a, b) + 1\n'
            'b = B()\n'
            'imprimir(b.add(3, 4))\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_super_in_constructor_chain(self):
        source = (
            'clase A:\n'
            '    funcion __init__(self, x):\n'
            '        self.x = x\n'
            '    funcion get_x(self):\n'
            '        devolver self.x\n'
            'clase B(A):\n'
            '    funcion __init__(self, x, val):\n'
            '        self.v = val\n'
            '        super().__init__(x)\n'
            '    funcion get_v(self):\n'
            '        devolver self.v\n'
            'b = B(10, 20)\n'
            'imprimir(b.get_x())\n'
            'imprimir(b.get_v())\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_multi_inheritance_c3_conflict_fails_closed(self):
        source = (
            'clase O:\n'
            '    funcion m(self):\n'
            '        devolver 1\n'
            'clase X(O):\n'
            '    funcion m(self):\n'
            '        devolver 1\n'
            'clase Y(O):\n'
            '    funcion m(self):\n'
            '        devolver 2\n'
            'clase First(X, Y):\n'
            '    funcion m(self):\n'
            '        devolver 3\n'
            'clase Second(Y, X):\n'
            '    funcion m(self):\n'
            '        devolver 4\n'
            'clase E(First, Second):\n'
            '    funcion m(self):\n'
            '        devolver 5\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-c3-fail-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "inconsistent method resolution order"):
                compile_native(source, Path(directory) / "program.exe")

    def test_x86_super_outside_method_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-super-fail-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "directly inside a class method"):
                compile_native("super().x()\n", Path(directory) / "program.exe")

    def test_x86_super_bare_value_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-super-fail-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "directly inside a class method"):
                compile_native("x = super()\n", Path(directory) / "program.exe")

    def test_x86_super_undefined_attribute_fails_closed(self):
        source = (
            'clase A:\n'
            '    funcion f(self):\n'
            '        devolver super().g()\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-super-fail-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "no base class in MRO"):
                compile_native(source, Path(directory) / "program.exe")

    def test_x86_self_method_call_in_plain_class(self):
        source = (
            'clase Caja:\n'
            '    funcion __init__(self, valor):\n'
            '        self.valor = valor\n'
            '    funcion base(self):\n'
            '        devolver self.valor\n'
            '    funcion doble(self):\n'
            '        devolver self.base() * 2\n'
            'caja = Caja(21)\n'
            'imprimir(caja.doble())\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_multifile_native_module(self):
        with tempfile.TemporaryDirectory(prefix="piton-multifile-") as directory:
            root = Path(directory)
            (root / "util.piton").write_text(
                "funcion doble(valor):\n    devolver valor * 2\n", encoding="utf-8"
            )
            entry = root / "main.piton"
            entry.write_text(
                "importar util\nimprimir(util.doble(6))\n", encoding="utf-8"
            )
            executable = compile_native_files(entry, root / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, b"12\r\n", b""))

    def test_x86_multifile_missing_module_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-multifile-") as directory:
            root = Path(directory)
            entry = root / "main.piton"
            entry.write_text("importar ausente\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "module not found"):
                compile_native_files(entry, root / "program.exe")

    def test_x86_from_import_single_function(self):
        with tempfile.TemporaryDirectory(prefix="piton-fromimport-") as directory:
            root = Path(directory)
            (root / "mathlib.piton").write_text(
                "funcion cuadrado(n):\n    devolver n * n\n", encoding="utf-8"
            )
            entry = root / "main.piton"
            entry.write_text(
                "desde mathlib importar cuadrado\nimprimir(cuadrado(7))\n", encoding="utf-8"
            )
            executable = compile_native_files(entry, root / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual((completed.returncode, completed.stdout), (0, b"49\r\n"))

    def test_x86_from_import_multiple_functions(self):
        with tempfile.TemporaryDirectory(prefix="piton-fromimport-") as directory:
            root = Path(directory)
            (root / "ops.piton").write_text(
                "funcion suma(a, b):\n    devolver a + b\n"
                "funcion resta(a, b):\n    devolver a - b\n",
                encoding="utf-8",
            )
            entry = root / "main.piton"
            entry.write_text(
                "desde ops importar suma, resta\nimprimir(suma(10, 3))\nimprimir(resta(10, 3))\n",
                encoding="utf-8",
            )
            executable = compile_native_files(entry, root / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual((completed.returncode, completed.stdout), (0, b"13\r\n7\r\n"))

    def test_x86_from_import_missing_module_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-fromimport-") as directory:
            root = Path(directory)
            entry = root / "main.piton"
            entry.write_text("desde fantasma importar algo\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "module not found"):
                compile_native_files(entry, root / "program.exe")

    def test_x86_from_import_math(self):
        source = (
            "desde math importar sqrt\n"
            "imprimir(sqrt(16))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_import_and_from_import_combined(self):
        with tempfile.TemporaryDirectory(prefix="piton-combined-") as directory:
            root = Path(directory)
            (root / "utils.piton").write_text(
                "funcion doble(x):\n    devolver x * 2\n"
                "funcion triple(x):\n    devolver x * 3\n",
                encoding="utf-8",
            )
            entry = root / "main.piton"
            entry.write_text(
                "importar utils\ndesde utils importar triple\n"
                "imprimir(utils.doble(5))\nimprimir(triple(5))\n",
                encoding="utf-8",
            )
            executable = compile_native_files(entry, root / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual((completed.returncode, completed.stdout), (0, b"10\r\n15\r\n"))

    def _assert_package_equiv(self, package_init, submodules, main):
        with tempfile.TemporaryDirectory(prefix="piton-package-") as directory:
            root = Path(directory)
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.piton").write_text(package_init, encoding="utf-8")
            (pkg_dir / "__init__.py").write_text(
                traducir_fuente(package_init, "<pkg-init>"), encoding="utf-8"
            )
            for sub_name, sub_src in (submodules or {}).items():
                (pkg_dir / f"{sub_name}.piton").write_text(sub_src, encoding="utf-8")
                (pkg_dir / f"{sub_name}.py").write_text(
                    traducir_fuente(sub_src, f"<pkg-{sub_name}>"), encoding="utf-8"
                )
            entry = root / "main.piton"
            entry.write_text(main, encoding="utf-8")
            main_py = root / "main.py"
            main_py.write_text(traducir_fuente(main, "<main>"), encoding="utf-8")
            executable = compile_native_files(entry, root / "program.exe")
            native_run = run_native([str(executable)], capture_output=True, check=False)
            oracle_run = run_oracle([sys.executable, str(main_py)], capture_output=True, check=False)
            self.assertEqual(
                (native_run.returncode, native_run.stdout),
                (oracle_run.returncode, oracle_run.stdout),
                native_run.stderr,
            )

    def _assert_sibling_equiv(self, module_files, main):
        with tempfile.TemporaryDirectory(prefix="piton-cycle-") as directory:
            root = Path(directory)
            for mod_name, src in module_files.items():
                (root / f"{mod_name}.piton").write_text(src, encoding="utf-8")
                (root / f"{mod_name}.py").write_text(traducir_fuente(src, f"<{mod_name}>"), encoding="utf-8")
            (root / "main.piton").write_text(main, encoding="utf-8")
            (root / "main.py").write_text(traducir_fuente(main, "<main>"), encoding="utf-8")
            executable = compile_native_files(root / "main.piton", root / "program.exe")
            native_run = run_native([str(executable)], capture_output=True, check=False)
            oracle_run = run_oracle([sys.executable, str(root / "main.py")], capture_output=True, check=False)
            self.assertEqual(
                (native_run.returncode, native_run.stdout),
                (oracle_run.returncode, oracle_run.stdout),
                native_run.stderr,
            )

    def test_x86_package_import_uses_init(self):
        init = "funcion cuadrado(n):\n    devolver n * n\n"
        main = "importar pkg\nimprimir(pkg.cuadrado(7))\n"
        self._assert_package_equiv(
            init,
            None,
            main,
        )

    def test_x86_package_from_import_init_function(self):
        init = "funcion triple(n):\n    devolver n * 3\n"
        main = "desde pkg importar triple\nimprimir(triple(8))\n"
        self._assert_package_equiv(
            init,
            None,
            main,
        )

    def test_x86_package_init_and_submodule_combined(self):
        init = "funcion doble(x):\n    devolver x * 2\n"
        submodules = {
            "numeros": "funcion suma(a, b):\n    devolver a + b\n",
        }
        main = (
            "importar pkg\n"
            "desde pkg.numeros importar suma\n"
            "imprimir(pkg.doble(21))\n"
            "imprimir(suma(40, 2))\n"
        )
        self._assert_package_equiv(
            init,
            submodules,
            main,
        )

    # ── MODULE_METADATA_V1 ──────────────────────────────────────────────

    def test_x86_module_metadata_source_mode_cpython_equiv(self):
        source = (
            "importar sys\n"
            "imprimir(__name__)\n"
            "imprimir(__package__)\n"
            'imprimir(sys.modules["__main__"].__name__)\n'
            'imprimir(sys.modules["__main__"].__package__)\n'
            'imprimir(sys.modules["sys"].__name__)\n'
            'imprimir(sys.modules["sys"].__package__)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(
            result.equivalent,
            f"native={result.native.stdout!r} oracle={result.oracle.stdout!r}",
        )

    def test_x86_module_metadata_files_mode_cpython_equiv(self):
        main = (
            "importar sys\nimportar util\nimportar pkg\n"
            "desde pkg.numeros importar suma\n"
            "imprimir(util.doble(21))\n"
            "imprimir(suma(40, 2))\n"
            'imprimir(sys.modules["util"].__name__)\n'
            'imprimir(sys.modules["util"].__package__)\n'
            'imprimir(sys.modules["pkg"].__name__)\n'
            'imprimir(sys.modules["pkg"].__package__)\n'
            'imprimir(sys.modules["pkg.numeros"].__name__)\n'
            'imprimir(sys.modules["pkg.numeros"].__package__)\n'
            'imprimir(sys.modules["__main__"].__name__)\n'
            'imprimir(sys.modules["__main__"].__package__)\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-meta-files-") as directory:
            root = Path(directory)
            util_src = "funcion doble(x):\n    devolver x * 2\n"
            (root / "util.piton").write_text(util_src, encoding="utf-8")
            (root / "util.py").write_text(traducir_fuente(util_src, "<util>"), encoding="utf-8")
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            init_src = "funcion triple(n):\n    devolver n * 3\n"
            (pkg_dir / "__init__.piton").write_text(init_src, encoding="utf-8")
            (pkg_dir / "__init__.py").write_text(traducir_fuente(init_src, "<pkg-init>"), encoding="utf-8")
            numeros_src = "funcion suma(a, b):\n    devolver a + b\n"
            (pkg_dir / "numeros.piton").write_text(numeros_src, encoding="utf-8")
            (pkg_dir / "numeros.py").write_text(traducir_fuente(numeros_src, "<pkg-numeros>"), encoding="utf-8")
            entry = root / "main.piton"
            entry.write_text(main, encoding="utf-8")
            main_py = root / "main.py"
            main_py.write_text(traducir_fuente(main, "<main>"), encoding="utf-8")
            executable = compile_native_files(entry, root / "program.exe")
            native_run = run_native([str(executable)], capture_output=True, check=False)
            oracle_run = run_oracle([sys.executable, str(main_py)], capture_output=True, check=False)
            self.assertEqual(
                (native_run.returncode, native_run.stdout),
                (oracle_run.returncode, oracle_run.stdout),
                native_run.stderr,
            )

    def test_x86_module_metadata_entry_file_set_in_files_mode(self):
        with tempfile.TemporaryDirectory(prefix="piton-meta-mainfile-") as directory:
            root = Path(directory)
            entry = root / "main.piton"
            entry.write_text("importar sys\nimprimir(__file__)\n", encoding="utf-8")
            executable = compile_native_files(entry, root / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
            self.assertTrue(
                completed.stdout.decode(errors="replace").strip().endswith("main.piton"),
                completed.stdout,
            )

    def test_x86_package_missing_init_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-package-fail-") as directory:
            root = Path(directory)
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            (pkg_dir / "sub.piton").write_text(
                "funcion fn():\n    devolver 1\n", encoding="utf-8"
            )
            entry = root / "main.piton"
            entry.write_text("desde pkg.sub importar fn\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "requires package"):
                compile_native_files(entry, root / "program.exe")

    # ── IMPORT_RELATIVE_V1 ──────────────────────────────────────────────

    def test_x86_relative_from_import_in_package_equiv(self):
        init = (
            "funcion triple(n):\n    devolver n * 3\n"
            "desde . importar numeros\n"
            "desde .operaciones importar resta\n"
        )
        submodules = {
            "numeros": "funcion suma(a, b):\n    devolver a + b\n",
            "operaciones": "funcion resta(a, b):\n    devolver a - b\n",
        }
        main = (
            "importar pkg\nimportar sys\n"
            "imprimir(pkg.triple(3))\n"
            "imprimir(pkg.numeros.suma(20, 22))\n"
            "imprimir(pkg.operaciones.resta(100, 7))\n"
            'imprimir(sys.modules["pkg"].__package__)\n'
            'imprimir(sys.modules["pkg.numeros"].__name__)\n'
            'imprimir(sys.modules["pkg.numeros"].__package__)\n'
            'imprimir(sys.modules["pkg.operaciones"].__package__)\n'
        )
        self._assert_package_equiv(init, submodules, main)

    def test_x86_dotted_import_binds_top_and_chain_calls(self):
        with tempfile.TemporaryDirectory(prefix="piton-dotted-") as directory:
            root = Path(directory)
            pkg2 = root / "pkg2"
            pkg2.mkdir()
            (pkg2 / "__init__.piton").write_text("", encoding="utf-8")
            (pkg2 / "__init__.py").write_text("", encoding="utf-8")
            sub = pkg2 / "subpkg"
            sub.mkdir()
            sub_init = (
                "funcion agrupar(n):\n    devolver n + 1\n"
                "desde . importar deep\n"
                "desde .otro importar doble\n"
            )
            (sub / "__init__.piton").write_text(sub_init, encoding="utf-8")
            (sub / "__init__.py").write_text(
                traducir_fuente(sub_init, "<subpkg-init>"), encoding="utf-8"
            )
            deep_src = "funcion canal(n):\n    devolver n * 100\n"
            (sub / "deep.piton").write_text(deep_src, encoding="utf-8")
            (sub / "deep.py").write_text(traducir_fuente(deep_src, "<deep>"), encoding="utf-8")
            otro_src = "funcion doble(n):\n    devolver n * 2\n"
            (sub / "otro.piton").write_text(otro_src, encoding="utf-8")
            (sub / "otro.py").write_text(traducir_fuente(otro_src, "<otro>"), encoding="utf-8")
            main = (
                "importar pkg2.subpkg\n"
                "imprimir(pkg2.subpkg.deep.canal(7))\n"
                "imprimir(pkg2.subpkg.otro.doble(21))\n"
                "imprimir(pkg2.subpkg.agrupar(1))\n"
            )
            entry = root / "main.piton"
            entry.write_text(main, encoding="utf-8")
            main_py = root / "main.py"
            main_py.write_text(traducir_fuente(main, "<main>"), encoding="utf-8")
            executable = compile_native_files(entry, root / "program.exe")
            native_run = run_native([str(executable)], capture_output=True, check=False)
            oracle_run = run_oracle([sys.executable, str(main_py)], capture_output=True, check=False)
            self.assertEqual(
                (native_run.returncode, native_run.stdout),
                (oracle_run.returncode, oracle_run.stdout),
                native_run.stderr,
            )

    def test_x86_dotted_import_asname_binds_top(self):
        with tempfile.TemporaryDirectory(prefix="piton-dotted-") as directory:
            root = Path(directory)
            pkg2 = root / "pkg2"
            pkg2.mkdir()
            (pkg2 / "__init__.piton").write_text("", encoding="utf-8")
            (pkg2 / "__init__.py").write_text("", encoding="utf-8")
            sub = pkg2 / "subpkg"
            sub.mkdir()
            sub_init = "funcion agrupar(n):\n    devolver n + 1\ndesde . importar deep\n"
            (sub / "__init__.piton").write_text(sub_init, encoding="utf-8")
            (sub / "__init__.py").write_text(traducir_fuente(sub_init, "<subpkg-init>"), encoding="utf-8")
            (sub / "deep.piton").write_text("funcion canal(n):\n    devolver n * 100\n", encoding="utf-8")
            (sub / "deep.py").write_text(
                traducir_fuente("funcion canal(n):\n    devolver n * 100\n", "<deep>"), encoding="utf-8"
            )
            main = (
                "importar pkg2.subpkg como P\n"
                "imprimir(P.deep.canal(3))\n"
                "imprimir(P.agrupar(2))\n"
            )
            entry = root / "main.piton"
            entry.write_text(main, encoding="utf-8")
            main_py = root / "main.py"
            main_py.write_text(traducir_fuente(main, "<main>"), encoding="utf-8")
            executable = compile_native_files(entry, root / "program.exe")
            native_run = run_native([str(executable)], capture_output=True, check=False)
            oracle_run = run_oracle([sys.executable, str(main_py)], capture_output=True, check=False)
            self.assertEqual(
                (native_run.returncode, native_run.stdout),
                (oracle_run.returncode, oracle_run.stdout),
                native_run.stderr,
            )

    def test_x86_relative_import_in_entry_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-relative-entry-") as directory:
            root = Path(directory)
            entry = root / "main.piton"
            entry.write_text("desde . importar numeros\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "no parent package"):
                compile_native_files(entry, root / "program.exe")

    def test_x86_relative_import_beyond_one_level_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-relative-deep-") as directory:
            root = Path(directory)
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.piton").write_text(
                "desde .. importar otro\n", encoding="utf-8"
            )
            entry = root / "main.piton"
            entry.write_text("importar pkg\n", encoding="utf-8")
            # M8 IMPORT_RELATIVE_V2: the guard's reach is now the package scope
            # (N levels allowed); `..` from a 1-segment package escapes it, so
            # the fail-closed trigger moved from counting levels to the escape
            # check. Message updated accordingly.
            with self.assertRaisesRegex(Exception, "escapes the package"):
                compile_native_files(entry, root / "program.exe")

    def test_x86_relative_two_levels_up_supported(self):
        # desde .. importar desde un submodule (separes, package context becomes
        # 'pkg.sub', '..' strips one segment → resolves en pkg).
        with tempfile.TemporaryDirectory(prefix="piton-relative-two-") as directory:
            root = Path(directory)
            pkg_dir = root / "pkg"
            sub_dir = pkg_dir / "sub"
            pkg_dir.mkdir(); sub_dir.mkdir()
            (pkg_dir / "__init__.piton").write_text("pasar\n", encoding="utf-8")
            (sub_dir / "__init__.piton").write_text("pasar\n", encoding="utf-8")
            (pkg_dir / "comun.piton").write_text(
                "funcion comun_x():\n    devolver 100\n", encoding="utf-8",
            )
            (sub_dir / "deep.piton").write_text(
                "desde .. importar comun\n"
                "funcion doble(x):\n    devolver comun.comun_x() + x\n",
                encoding="utf-8",
            )
            entry = root / "main.piton"
            entry.write_text(
                "desde pkg.sub.deep importar doble\n"
                "imprimir(doble(3))\n", encoding="utf-8"
            )
            executable = compile_native_files(entry, root / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
            self.assertEqual(completed.stdout, b"103\r\n")

    def test_x86_module_attribute_value_access_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-module-attr-") as directory:
            root = Path(directory)
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.piton").write_text(
                "funcion fn():\n    devolver 1\n", encoding="utf-8"
            )
            entry = root / "main.piton"
            entry.write_text("importar pkg\nimprimir(pkg.numeros)\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "module function calls"):
                compile_native_files(entry, root / "program.exe")

    def test_x86_module_attr_chain_keyword_args_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-module-kw-") as directory:
            root = Path(directory)
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.piton").write_text("", encoding="utf-8")
            (pkg_dir / "numeros.piton").write_text(
                "funcion suma(a, b):\n    devolver a + b\n", encoding="utf-8"
            )
            entry = root / "main.piton"
            entry.write_text(
                "importar pkg\nimprimir(pkg.numeros.suma(a=1, b=2))\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(Exception, "keyword arguments"):
                compile_native_files(entry, root / "program.exe")

    def test_x86_dotted_import_missing_module_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-package-fail-") as directory:
            root = Path(directory)
            root.joinpath("pkg").mkdir()
            (root / "pkg" / "__init__.piton").write_text(
                "funcion fn():\n    devolver 1\n", encoding="utf-8"
            )
            entry = root / "main.piton"
            entry.write_text("importar pkg.sub\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "module not found"):
                compile_native_files(entry, root / "program.exe")

    # ── IMPORT_STAR_V1 ──────────────────────────────────────────────────

    def _star_alias_map(self, root, main_src, *, module_files=None, pkg_init=None, submodules=None):
        entry = root / "main.piton"
        entry.write_text(main_src, encoding="utf-8")
        for name, src in (module_files or {}).items():
            (root / name).write_text(src, encoding="utf-8")
        if pkg_init is not None:
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.piton").write_text(pkg_init, encoding="utf-8")
            for name, src in (submodules or {}).items():
                (pkg_dir / f"{name}.piton").write_text(src, encoding="utf-8")
        modules, from_imports, module_meta = _scan_native_modules(entry)
        hir = lower_cst_to_hir(parse(entry.read_text(encoding="utf-8-sig")))
        lowerer = MIRLowerer()
        lowerer.lower(
            hir, modules, from_imports=from_imports,
            entry_file=str(entry), module_meta=module_meta,
        )
        return lowerer.from_import_aliases

    def test_x86_star_from_package_excludes_private(self):
        with tempfile.TemporaryDirectory(prefix="piton-star-priv-") as directory:
            root = Path(directory)
            aliases = self._star_alias_map(
                root,
                "desde pkg importar *\n",
                pkg_init=(
                    "funcion cuadrado(n):\n    devolver n * n\n"
                    "funcion doble(n):\n    devolver n * 2\n"
                    "funcion _privada(n):\n    devolver n\n"
                ),
            )
            self.assertEqual(aliases["cuadrado"], "pkg__cuadrado")
            self.assertEqual(aliases["doble"], "pkg__doble")
            self.assertNotIn("_privada", aliases)

    def test_x86_star_from_package_equiv(self):
        init = (
            "funcion cuadrado(n):\n    devolver n * n\n"
            "funcion doble(n):\n    devolver n * 2\n"
            "funcion _privada(n):\n    devolver n\n"
        )
        main = (
            "desde pkg importar *\n"
            "imprimir(cuadrado(6))\n"
            "imprimir(doble(21))\n"
        )
        self._assert_package_equiv(init, None, main)

    def test_x86_star_from_standalone_module_equiv(self):
        module_src = "funcion resta(a, b):\n    devolver a - b\nfuncion doble(n):\n    devolver n * 2\n"
        main = (
            "desde lib importar *\n"
            "imprimir(resta(100, 7))\n"
            "imprimir(doble(21))\n"
        )
        with tempfile.TemporaryDirectory(prefix="piton-star-lib-") as directory:
            root = Path(directory)
            (root / "lib.piton").write_text(module_src, encoding="utf-8")
            (root / "lib.py").write_text(traducir_fuente(module_src, "<lib>"), encoding="utf-8")
            entry = root / "main.piton"
            entry.write_text(main, encoding="utf-8")
            main_py = root / "main.py"
            main_py.write_text(traducir_fuente(main, "<main>"), encoding="utf-8")
            executable = compile_native_files(entry, root / "program.exe")
            native_run = run_native([str(executable)], capture_output=True, check=False)
            oracle_run = run_oracle([sys.executable, str(main_py)], capture_output=True, check=False)
            self.assertEqual(
                (native_run.returncode, native_run.stdout),
                (oracle_run.returncode, oracle_run.stdout),
                native_run.stderr,
            )

    def test_x86_star_from_dotted_submodule_equiv(self):
        init = ""
        submodules = {
            "tools": "funcion suma(a, b):\n    devolver a + b\nfuncion producto(a, b):\n    devolver a * b\n",
        }
        main = (
            "desde pkg.tools importar *\n"
            "imprimir(suma(40, 2))\n"
            "imprimir(producto(6, 7))\n"
        )
        self._assert_package_equiv(init, submodules, main)

    def test_x86_star_relative_in_entry_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-star-entry-") as directory:
            root = Path(directory)
            entry = root / "main.piton"
            entry.write_text("desde . importar *\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "no parent package"):
                compile_native_files(entry, root / "program.exe")

    def test_x86_star_in_package_init_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-star-init-") as directory:
            root = Path(directory)
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.piton").write_text("desde . importar *\n", encoding="utf-8")
            entry = root / "main.piton"
            entry.write_text("importar pkg\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "entry module"):
                compile_native_files(entry, root / "program.exe")

    def test_x86_star_from_builtin_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-star-builtin-") as directory:
            root = Path(directory)
            entry = root / "main.piton"
            entry.write_text("desde math importar *\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "builtin modules"):
                compile_native_files(entry, root / "program.exe")

    def test_x86_star_mixed_with_explicit_names_fails_closed(self):
        for star_source in (
            "desde m importar a, *\n",
            "desde m importar *, a\n",
            "importar *\n",
        ):
            with self.subTest(source=star_source):
                with self.assertRaises(Exception):
                    parse(star_source)

    # ── IMPORT_CYCLIC_V1 ────────────────────────────────────────────────

    def test_x86_cyclic_mutual_from_import_ok(self):
        modules = {
            "a": (
                "funcion fa(a):\n"
                "    devolver a + 1\n"
                "desde b importar fb\n"
                "funcion fab(a):\n"
                "    devolver fb(a) + 10\n"
            ),
            "b": (
                "funcion fb(a):\n"
                "    devolver a * 2\n"
                "desde a importar fa\n"
                "funcion fba(a):\n"
                "    devolver fa(a) + 100\n"
            ),
        }
        main = (
            "importar a\n"
            "importar b\n"
            "imprimir(a.fa(1))\n"
            "imprimir(a.fab(10))\n"
            "imprimir(b.fb(5))\n"
            "imprimir(b.fba(3))\n"
        )
        self._assert_sibling_equiv(modules, main)

    def test_x86_cyclic_from_import_defined_before_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-cycle-fail-") as directory:
            root = Path(directory)
            (root / "a.piton").write_text(
                "desde b importar fb\nfuncion fa(a):\n    devolver a\n", encoding="utf-8"
            )
            (root / "b.piton").write_text(
                "desde a importar fa\nfuncion fb(a):\n    devolver a\n", encoding="utf-8"
            )
            entry = root / "main.piton"
            entry.write_text("importar a\n", encoding="utf-8")
            with self.assertRaisesRegex(NativeBuildError, "partially initialized"):
                compile_native_files(entry, root / "program.exe")

    def test_x86_cyclic_same_module_bare_call_in_imported_module(self):
        modules = {
            "m": (
                "funcion doble(x):\n"
                "    devolver x * 2\n"
                "funcion cuadrado(x):\n"
                "    devolver doble(x) * doble(x)\n"
            ),
        }
        main = "importar m\nimprimir(m.cuadrado(3))\n"
        self._assert_sibling_equiv(modules, main)

    def test_x86_cyclic_plain_import_cycle_ok(self):
        modules = {
            "a": "importar b\nfuncion fa(x):\n    devolver b.fb(x) + 1\n",
            "b": "importar a\nfuncion fb(x):\n    devolver x * 3\n",
        }
        main = "importar a\nimprimir(a.fa(7))\n"
        self._assert_sibling_equiv(modules, main)

    def test_x86_async_run_await_and_math_stdlib(self):
        source = (
            "importar asyncio\n"
            "importar math\n"
            "asincrono funcion valor():\n"
            "    devolver 9\n"
            "asincrono funcion principal():\n"
            "    imprimir(esperar valor())\n"
            "    imprimir(math.sqrt(9))\n"
            "asyncio.run(principal())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_rejects_escaping_coroutine(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "must be awaited"):
                compile_native(
                    "asincrono funcion valor():\n    devolver 1\nx = valor()\n",
                    Path(directory) / "program.exe",
                )

    def test_x86_async_simple_coroutine(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion saludar():\n"
            '    imprimir("hola")\n'
            "asyncio.run(saludar())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_async_nested_await(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion interior():\n"
            "    devolver 10\n"
            "asincrono funcion exterior():\n"
            "    v = esperar interior()\n"
            "    imprimir(v)\n"
            "asyncio.run(exterior())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_async_with_computation(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion calcular(n):\n"
            "    devolver n * n + 1\n"
            "asincrono funcion principal():\n"
            "    r = esperar calcular(5)\n"
            "    imprimir(r)\n"
            "asyncio.run(principal())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_async_deep_chain_with_params(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion base(n):\n"
            "    devolver n + 1\n"
            "asincrono funcion medio(n):\n"
            "    a = esperar base(n)\n"
            "    devolver a * 2\n"
            "asincrono funcion cima(n):\n"
            "    b = esperar medio(n)\n"
            "    c = esperar base(b)\n"
            "    devolver c + 100\n"
            "asincrono funcion principal():\n"
            "    imprimir(esperar cima(5))\n"
            "asyncio.run(principal())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_async_rejects_await_outside_async(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "await is only valid"):
                compile_native(
                    "devolver esperar 1\n",
                    Path(directory) / "program.exe",
                )

    def test_x86_async_generator_async_for(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion contar():\n"
            "    producir 1\n"
            "    producir 2\n"
            "    producir 3\n"
            "asincrono funcion principal():\n"
            "    asincrono para x en contar():\n"
            "        imprimir(x)\n"
            "asyncio.run(principal())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_async_generator_params_and_await_inside(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion base(n):\n"
            "    devolver n + 1\n"
            "asincrono funcion contar(hasta):\n"
            "    producir 1\n"
            "    producir 2\n"
            "    v = esperar base(10)\n"
            "    producir v\n"
            "    producir 3\n"
            "asincrono funcion principal():\n"
            "    asincrono para x en contar(4):\n"
            "        imprimir(x)\n"
            "asyncio.run(principal())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_async_generator_bare_call_is_not_a_coroutine(self):
        # Unlike an async function, an async generator call must NOT be awaited:
        # creating the object is enough (no "must be awaited" error).
        source = (
            "importar asyncio\n"
            "asincrono funcion contar():\n"
            "    producir 1\n"
            "    producir 2\n"
            "asincrono funcion principal():\n"
            "    g = contar()\n"
            "    imprimir(1)\n"
            "asyncio.run(principal())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_async_for_else(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion contar():\n"
            "    producir 1\n"
            "    producir 2\n"
            "asincrono funcion principal():\n"
            "    asincrono para x en contar():\n"
            "        imprimir(x)\n"
            "    sino:\n"
            "        imprimir(99)\n"
            "asyncio.run(principal())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_async_for_else_skipped_on_break(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion contar():\n"
            "    producir 1\n"
            "    producir 2\n"
            "    producir 3\n"
            "asincrono funcion principal():\n"
            "    asincrono para x en contar():\n"
            "        si x == 2:\n"
            "            romper\n"
            "        imprimir(x)\n"
            "    sino:\n"
            "        imprimir(99)\n"
            "asyncio.run(principal())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_async_for_requires_async_generator(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "requires an async generator call"):
                compile_native(
                    "importar asyncio\n"
                    "asincrono funcion principal():\n"
                    "    asincrono para x en [1, 2]:\n"
                    "        imprimir(x)\n"
                    "asyncio.run(principal())\n",
                    Path(directory) / "program.exe",
                )

    def test_x86_rejects_await_async_generator(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "cannot await an async generator"):
                compile_native(
                    "importar asyncio\n"
                    "asincrono funcion contar():\n"
                    "    producir 1\n"
                    "asincrono funcion principal():\n"
                    "    v = esperar contar()\n"
                    "asyncio.run(principal())\n",
                    Path(directory) / "program.exe",
                )

    def test_x86_async_for_outside_async_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "only valid inside a native async function"):
                compile_native(
                    "asincrono funcion contar():\n"
                    "    producir 1\n"
                    "asincrono para x en contar():\n"
                    "    imprimir(x)\n",
                    Path(directory) / "program.exe",
                )

    # ── TASK_SCHEDULER_V1 (M9): create_task / await task / gather / sleep(0) / cancel ──

    def test_x86_task_create_await_and_result(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion saluda(n):\n"
            "    esperar asyncio.sleep(0)\n"
            "    devolver n * 10\n"
            "asincrono funcion principal():\n"
            "    t1 = asyncio.create_task(saluda(1))\n"
            "    imprimir(esperar t1)\n"
            "asyncio.run(principal())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_task_gather_direct_calls(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion saluda(n):\n"
            "    esperar asyncio.sleep(0)\n"
            "    devolver n * 10\n"
            "asincrono funcion principal():\n"
            "    r = esperar asyncio.gather(saluda(2), saluda(3))\n"
            "    imprimir(r)\n"
            "    devolver 7\n"
            "x = asyncio.run(principal())\n"
            "imprimir(x)\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_task_gather_sleep0_interleave_order(self):
        # CPython's asyncio is FIFO: with three tasks each sleeping twice,
        # the gather result is deterministic [1, 2, 3].
        source = (
            "importar asyncio\n"
            "asincrono funcion t(n):\n"
            "    esperar asyncio.sleep(0)\n"
            "    esperar asyncio.sleep(0)\n"
            "    devolver n\n"
            "asincrono funcion principal():\n"
            "    a = asyncio.create_task(t(1))\n"
            "    b = asyncio.create_task(t(2))\n"
            "    c = asyncio.create_task(t(3))\n"
            "    imprimir(esperar asyncio.gather(a, b, c))\n"
            "asyncio.run(principal())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_task_gather_already_finished_tasks(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion s(n):\n"
            "    devolver n * 10\n"
            "asincrono funcion principal():\n"
            "    t1 = asyncio.create_task(s(1))\n"
            "    imprimir(esperar t1)\n"
            "    t2 = asyncio.create_task(s(2))\n"
            "    imprimir(esperar t2)\n"
            "    imprimir(esperar asyncio.gather(t1, t2))\n"
            "    devolver 7\n"
            "x = asyncio.run(principal())\n"
            "imprimir(x)\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_task_await_chain_with_sleep0(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion hoja(n):\n"
            "    esperar asyncio.sleep(0)\n"
            "    devolver n + 1\n"
            "asincrono funcion medio():\n"
            "    x = esperar hoja(10)\n"
            "    esperar asyncio.sleep(0)\n"
            "    devolver x * 2\n"
            "asincrono funcion principal():\n"
            "    imprimir(esperar medio())\n"
            "asyncio.run(principal())\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_task_cancel_await_propagates(self):
        # CPython: await on a cancelled task raises CancelledError in the awaiter
        # (the root here), which escapes asyncio.run as an unhandled exception.
        source = (
            "importar asyncio\n"
            "asincrono funcion s(n):\n"
            "    devolver n\n"
            "asincrono funcion principal():\n"
            "    t1 = asyncio.create_task(s(1))\n"
            "    t1.cancel()\n"
            "    imprimir(esperar t1)\n"
            "asyncio.run(principal())\n"
        )
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            executable = compile_native(source, Path(directory) / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual(completed.returncode, 1, completed.stderr.decode(errors="replace"))
            self.assertIn("CancelledError", completed.stderr.decode(errors="replace"))

    def test_x86_task_cancel_gather_member_propagates(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion s(n):\n"
            "    devolver n\n"
            "asincrono funcion principal():\n"
            "    t1 = asyncio.create_task(s(1))\n"
            "    t1.cancel()\n"
            "    imprimir(esperar asyncio.gather(t1))\n"
            "asyncio.run(principal())\n"
        )
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            executable = compile_native(source, Path(directory) / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual(completed.returncode, 1, completed.stderr.decode(errors="replace"))
            self.assertIn("CancelledError", completed.stderr.decode(errors="replace"))

    def test_x86_task_sleep1_uses_real_timer(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion p():\n"
            "    esperar asyncio.sleep(1)\n"
            "    devolver 1\n"
            "imprimir(asyncio.run(p()))\n"
        )
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            executable = compile_native(source, Path(directory) / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
            self.assertEqual(completed.stdout, b"1\r\n")

    def test_x86_task_gather_non_task_fails_closed(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion p():\n"
            "    imprimir(esperar asyncio.gather(5))\n"
            "asyncio.run(p())\n"
        )
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            executable = compile_native(source, Path(directory) / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("gather requires tasks", completed.stderr.decode(errors="replace"))

    def test_x86_task_await_non_awaitable_fails_closed(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion p():\n"
            "    esperar 42\n"
            "asyncio.run(p())\n"
        )
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            executable = compile_native(source, Path(directory) / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual(completed.returncode, 1, completed.stderr.decode(errors="replace"))
            self.assertIn("object is not awaitable", completed.stderr.decode(errors="replace"))

    def test_x86_task_cancel_attribute_on_int_fails_closed(self):
        source = (
            "importar asyncio\n"
            "asincrono funcion p():\n"
            "    x = 5\n"
            "    x.cancel()\n"
            "    devolver 0\n"
            "asyncio.run(p())\n"
        )
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            executable = compile_native(source, Path(directory) / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual(completed.returncode, 1, completed.stderr.decode(errors="replace"))
            self.assertIn("object has no attribute 'cancel'", completed.stderr.decode(errors="replace"))

    def test_x86_task_teardown_is_leak_clean(self):
        # The gather result list must be owned by the awaiting coroutine and
        # freed at its exit: the teardown live-count tripwire returns 0.
        source = (
            "importar asyncio\n"
            "asincrono funcion s(n):\n"
            "    devolver n\n"
            "asincrono funcion p():\n"
            "    imprimir(esperar asyncio.gather(s(1), s(2)))\n"
            "asyncio.run(p())\n"
        )
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            executable = compile_native(source, Path(directory) / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
            self.assertEqual(completed.stdout.decode(errors="replace"), "[1, 2]\r\n")

    def test_x86_bigint_arithmetic(self):
        source = "imprimir(1180591620717411303424 + 1)\n"
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_dict_print_and_lookup(self):
        corpus = (
            "imprimir({1: 2, 3: 4})\n",
            "imprimir({1: 2, 3: 4}[3])\n",
            "d = {10: 20, 30: 40}\nimprimir(d[10])\nimprimir(d[30])\n",
        )
        for source in corpus:
            with self.subTest(source=source):
                result = compare_native_to_cpython(source)
                self.assertTrue(result.equivalent, result)

    def test_x86_set_print_and_len(self):
        corpus = (
            "imprimir({1, 2, 3})\n",
            "imprimir(longitud({1, 2, 2, 3}))\n",
        )
        for source in corpus:
            with self.subTest(source=source):
                result = compare_native_to_cpython(source)
                self.assertTrue(result.equivalent, result)

    def test_x86_tuple_print_and_lookup(self):
        corpus = (
            "imprimir((1, 2, 3))\n",
            "imprimir((10, 20)[1])\n",
            "t = (5,)\nimprimir(t)\nimprimir(longitud(t))\n",
        )
        for source in corpus:
            with self.subTest(source=source):
                result = compare_native_to_cpython(source)
                self.assertTrue(result.equivalent, result)

    def test_x86_collection_reassignment_cleanup(self):
        source = (
            "x = [1, 2, 3]\n"
            "x = [4, 5]\n"
            "x = [6]\n"
            "imprimir(x)\n"
            "imprimir(longitud(x))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_dict_reassignment_cleanup(self):
        source = (
            "d = {1: 10, 2: 20}\n"
            "d = {3: 30}\n"
            "imprimir(d)\n"
            "imprimir(longitud(d))\n"
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    # ── Semantic Differential Corpus (25 programs) ──────────────────────

    def test_x86_function_default_int(self):
        result = compare_native_to_cpython('funcion saludar(n=10):\n    devolver n + 1\nimprimir(saludar())\nimprimir(saludar(5))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_function_default_bool(self):
        result = compare_native_to_cpython('funcion flag(marcar=Verdadero):\n    si marcar:\n        devolver 1\n    sino:\n        devolver 0\nimprimir(flag())\nimprimir(flag(Falso))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_function_multiple_defaults_partial(self):
        result = compare_native_to_cpython('funcion op(a=1, b=2, c=3):\n    devolver a + b + c\nimprimir(op())\nimprimir(op(10))\nimprimir(op(10, 20))\nimprimir(op(10, 20, 30))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_function_default_non_const_rejected(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "constant"):
                compile_native('funcion f(n=1 + 2):\n    devolver n\n', Path(directory) / "program.exe")

    # ── FUNCTION_KEYWORD_ARGS_V1 ─────────────────────────────────────────

    def test_x86_kw_simple(self):
        result = compare_native_to_cpython('funcion saludar(nombre, edad):\n    devolver nombre + edad\nimprimir(saludar(nombre=5, edad=3))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_kw_order(self):
        result = compare_native_to_cpython('funcion op(a, b, c):\n    devolver a * 100 + b * 10 + c\nimprimir(op(c=3, a=1, b=2))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_kw_with_default(self):
        result = compare_native_to_cpython('funcion f(a, b=10):\n    devolver a + b\nimprimir(f(a=5))\nimprimir(f(a=5, b=20))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_kw_positional_mix(self):
        result = compare_native_to_cpython('funcion f(a, b, c=3):\n    devolver a + b + c\nimprimir(f(1, c=10, b=2))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_kw_middle_default_skipped(self):
        result = compare_native_to_cpython('funcion f(a=1, b=2, c=3):\n    devolver a * 100 + b * 10 + c\nimprimir(f(c=5))\n')
        self.assertTrue(result.equivalent, result)

    def test_x86_kw_unexpected_rejected(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "unexpected keyword"):
                compile_native('funcion f(a):\n    devolver a\nimprimir(f(x=1))\n', Path(directory) / "program.exe")

    def test_x86_kw_missing_required_rejected(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "missing required"):
                compile_native('funcion f(a, b):\n    devolver a\nimprimir(f(a=1))\n', Path(directory) / "program.exe")

    def test_x86_kw_duplicate_rejected(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "multiple values"):
                compile_native('funcion f(a):\n    devolver a\nimprimir(f(1, a=2))\n', Path(directory) / "program.exe")

    # ── FUNCTION_STARARGS_V1 ─────────────────────────────────────────────

    def test_x86_decorator_wraps_closure(self):
        result = compare_native_to_cpython(
            'funcion doble(fn):\n'
            '    funcion envuelta(x):\n'
            '        devolver fn(x) * 2\n'
            '    devolver envuelta\n'
            '@doble\n'
            'funcion inc(x):\n'
            '    devolver x + 1\n'
            'imprimir(inc(3))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_default_then_starargs(self):
        result = compare_native_to_cpython(
            'funcion peso(base=1, *extras):\n'
            '    devolver base * 10 + longitud(extras)\n'
            'imprimir(peso())\n'
            'imprimir(peso(5, 7, 9))\n'
            'imprimir(peso(base=4))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_starargs_unexpected_keyword_rejected(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "unexpected keyword"):
                compile_native(
                    'funcion f(*args):\n    devolver longitud(args)\nimprimir(f(x=1))\n',
                    Path(directory) / "program.exe",
                )

    # ── FUNCTION_KWARGS_V1 ───────────────────────────────────────────────

    def test_x86_kwargs_pure_dict(self):
        result = compare_native_to_cpython(
            'funcion leer(**kw):\n'
            '    devolver longitud(kw) * 10 + kw["x"]\n'
            'imprimir(leer(x=4, z=8))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_kwargs_fixed_and_starargs(self):
        result = compare_native_to_cpython(
            'funcion total(base, *extras, **opciones):\n'
            '    devolver base + sum(extras) + opciones["extra"]\n'
            'imprimir(total(1, 2, 3, extra=4))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_kwargs_unpacking_literal(self):
        result = compare_native_to_cpython(
            'funcion f(base, extra=0):\n'
            '    devolver base + extra\n'
            'imprimir(f(**{"base": 2, "extra": 3}))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_starargs_unpacking_literal(self):
        result = compare_native_to_cpython(
            'funcion f(base, extra):\n'
            '    devolver base * 10 + extra\n'
            'imprimir(f(*[1, 2]))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_call_unpacking_dynamic(self):
        result = compare_native_to_cpython(
            'funcion f(x):\n    devolver x\n'
            'xs = [1]\nimprimir(f(*xs))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_bound_method_retrieval_and_later_call(self):
        result = compare_native_to_cpython(
            'clase C:\n'
            '    funcion __init__(self, base):\n'
            '        self.base = base\n'
            '    funcion suma(self, extra):\n'
            '        devolver self.base + extra\n'
            'c = C(7)\n'
            'm = c.suma\n'
            'imprimir(m(5))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_bound_method_frame_abi(self):
        result = compare_native_to_cpython(
            'clase C:\n'
            '    funcion suma(self, a, b, c, d):\n'
            '        devolver a + b + c + d\n'
            'c = C()\n'
            'm = c.suma\n'
            'imprimir(m(1, 2, 3, 4))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_decorators_apply_bottom_up_and_rebind(self):
        result = compare_native_to_cpython(
            'funcion doble(fn):\n'
            '    funcion envuelta(x):\n'
            '        devolver fn(x) * 2\n'
            '    devolver envuelta\n'
            '@doble\n'
            'funcion inc(x):\n'
            '    devolver x + 1\n'
            'imprimir(inc(3))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_frame_abi_supports_more_than_four_parameters(self):
        result = compare_native_to_cpython(
            'funcion suma(a, b, c, d, e):\n'
            '    devolver a + b + c + d + e\n'
            'imprimir(suma(1, 2, 3, 4, 5))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_function_annotations_preserve_call_semantics(self):
        result = compare_native_to_cpython(
            'funcion suma(a: int, b: int) -> int:\n'
            '    devolver a + b\n'
            'imprimir(suma(2, 3))\n'
        )
        self.assertTrue(result.equivalent, result)

    # ── FUNCTION_SIGNATURE_MARKERS_V1 ───────────────────────────────────

    # ── FUNCTION_SIGNATURE_MARKERS_V1 ───────────────────────────────────

    def test_x86_positional_only(self):
        result = compare_native_to_cpython(
            'funcion unir(a, /, b):\n'
            '    devolver a * 10 + b\n'
            'imprimir(unir(2, 3))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_positional_only_keyword_rejected(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "missing required"):
                compile_native(
                    'funcion unir(a, /, b):\n    devolver a + b\nimprimir(unir(a=2, b=3))\n',
                    Path(directory) / "program.exe",
                )

    def test_x86_keyword_only_required_and_default(self):
        result = compare_native_to_cpython(
            'funcion escalar(base, *, factor=2, extra):\n'
            '    devolver base * factor + extra\n'
            'imprimir(escalar(3, extra=1))\n'
            'imprimir(escalar(3, factor=4, extra=1))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_signature_markers_combined(self):
        result = compare_native_to_cpython(
            'funcion total(base, /, *extras, ajuste=3, **opciones):\n'
            '    devolver base + sum(extras) + ajuste + opciones["final"]\n'
            'imprimir(total(1, 2, 3, ajuste=4, final=5))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_corpus_01_arithmetic_chain(self):
        result = compare_native_to_cpython('x = 10\nb = x * 3 + 7\nc = b // 2\nimprimir(c)\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_02_boolean_logic(self):
        result = compare_native_to_cpython('imprimir(Verdadero == Verdadero)\nimprimir(Falso != Verdadero)\nimprimir(no Verdadero)\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_03_comparison_chain(self):
        result = compare_native_to_cpython('imprimir(1 < 2)\nimprimir(3 >= 3)\nimprimir("a" == "a")\nimprimir(1 != 2)\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_04_string_ops(self):
        result = compare_native_to_cpython('s = "hola"\nimprimir(s == "hola")\nimprimir(s != "mundo")\nimprimir(type(s))\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_05_list_basics(self):
        result = compare_native_to_cpython('l = [10, 20, 30]\nimprimir(longitud(l))\nimprimir(l)\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_06_dict_basics(self):
        result = compare_native_to_cpython('d = {1: "uno", 2: "dos"}\nimprimir(longitud(d))\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_07_set_basics(self):
        result = compare_native_to_cpython('s = {1, 2, 3}\nimprimir(longitud(s))\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_08_while_loop(self):
        result = compare_native_to_cpython('x = 0\nmientras x < 5:\n    x = x + 1\nimprimir(x)\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_09_for_loop(self):
        result = compare_native_to_cpython('suma = 0\ni = 0\nmientras i < 5:\n    suma = suma + i\n    i = i + 1\nimprimir(suma)\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_10_function_call(self):
        result = compare_native_to_cpython('funcion cuadrado(x):\n    devolver x * x\nimprimir(cuadrado(7))\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_11_if_elif_else(self):
        result = compare_native_to_cpython('x = 15\nsi x > 20:\n    imprimir("grande")\nsino_si x > 10:\n    imprimir("medio")\nsino:\n    imprimir("chico")\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_12_nested_loops(self):
        result = compare_native_to_cpython('total = 0\ni = 0\nmientras i < 3:\n    j = 0\n    mientras j < 3:\n        total = total + 1\n        j = j + 1\n    i = i + 1\nimprimir(total)\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_13_abs_min_max(self):
        result = compare_native_to_cpython('imprimir(abs(-10))\nimprimir(min(5, 3))\nimprimir(max(5, 3))\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_14_type_check(self):
        result = compare_native_to_cpython('imprimir(type(42))\nimprimir(type("hola"))\nimprimir(type(Verdadero))\nimprimir(type(Nada))\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_15_exception_caught(self):
        result = compare_native_to_cpython('intentar:\n    lanzar ValueError("test")\nexcepto ValueError:\n    imprimir("caught")\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_16_finally_block(self):
        result = compare_native_to_cpython('intentar:\n    imprimir("try")\nfinalmente:\n    imprimir("finally")\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_17_class_basic(self):
        source = (
            'clase Punto:\n'
            '    funcion __init__(self, px, py):\n'
            '        self.px = px\n'
            '        self.py = py\n'
            'p = Punto(3, 4)\n'
            'imprimir(p.px)\n'
            'imprimir(p.py)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_corpus_18_class_method(self):
        source = (
            'clase Contador:\n'
            '    funcion __init__(self):\n'
            '        self.valor = 0\n'
            '    funcion incrementar(self):\n'
            '        self.valor = self.valor + 1\n'
            'c = Contador()\n'
            'c.incrementar()\n'
            'c.incrementar()\n'
            'imprimir(c.valor)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_corpus_19_class_inheritance(self):
        source = (
            'clase Base:\n'
            '    funcion __init__(self, x):\n'
            '        self.x = x\n'
            '    funcion get_x(self):\n'
            '        devolver self.x\n'
            'clase Hija(Base):\n'
            '    funcion doble(self):\n'
            '        devolver self.x * 2\n'
            'h = Hija(5)\n'
            'imprimir(h.get_x())\n'
            'imprimir(h.doble())\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_corpus_20_bigint(self):
        result = compare_native_to_cpython('x = 1000000000000000000000000\nb = x * 2\nimprimir(b)\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_21_float_ops(self):
        result = compare_native_to_cpython('x = 3.14\nb = x * 2\nimprimir(b)\nimprimir(abs(-2.5))\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_22_ternary(self):
        result = compare_native_to_cpython('x = 10\nsi x % 2 == 0:\n    r = "par"\nsino:\n    r = "impar"\nimprimir(r)\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_23_augmented_assign(self):
        result = compare_native_to_cpython('x = 10\nx += 5\nx *= 2\nx -= 3\nimprimir(x)\n')
        self.assertTrue(result.equivalent, result)

    def test_corpus_24_multiple_functions(self):
        source = (
            'funcion suma(a, b):\n'
            '    devolver a + b\n'
            'funcion producto(a, b):\n'
            '    devolver a * b\n'
            'imprimir(suma(3, 4))\n'
            'imprimir(producto(3, 4))\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_corpus_25_sum_collection(self):
        result = compare_native_to_cpython('imprimir(sum([1, 2, 3, 4, 5]))\nimprimir(sum({10, 20, 30}))\n')
        self.assertTrue(result.equivalent, result)

    def test_descriptors_property_getter(self):
        source = (
            'clase P:\n'
            '    funcion __init__(self):\n'
            '        self.campos = 41\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver self.campos + 1\n'
            'p = P()\n'
            'imprimir(p.x)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_descriptors_property_get_set(self):
        source = (
            'clase P:\n'
            '    funcion __init__(self):\n'
            '        self.campos = 0\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver self.campos\n'
            '    @x.setter\n'
            '    funcion x(self, v):\n'
            '        self.campos = v * 2\n'
            'p = P()\n'
            'p.x = 5\n'
            'imprimir(p.x)\n'
            'p.x = 10\n'
            'imprimir(p.x)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_descriptors_property_get_set_del(self):
        source = (
            'clase P:\n'
            '    funcion __init__(self):\n'
            '        self.campos = 0\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver self.campos\n'
            '    @x.setter\n'
            '    funcion x(self, v):\n'
            '        self.campos = v\n'
            '    @x.deleter\n'
            '    funcion x(self):\n'
            '        self.campos = -1\n'
            'p = P()\n'
            'p.x = 5\n'
            'imprimir(p.x)\n'
            'borrar p.x\n'
            'imprimir(p.x)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_descriptors_property_inheritance_mro(self):
        source = (
            'clase Base:\n'
            '    funcion __init__(self):\n'
            '        self.campos = 3\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver self.campos\n'
            '    @x.setter\n'
            '    funcion x(self, v):\n'
            '        self.campos = v\n'
            'clase Hija(Base):\n'
            '    pasar\n'
            'h = Hija()\n'
            'imprimir(h.x)\n'
            'h.x = 9\n'
            'imprimir(h.x)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_descriptors_property_shadow_in_subclass(self):
        source = (
            'clase Base:\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver 1\n'
            'clase Hija(Base):\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver 2\n'
            'h = Hija()\n'
            'imprimir(h.x)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_descriptors_property_set_on_getter_only_fails_closed(self):
        source = (
            'clase P:\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver 1\n'
            'p = P()\n'
            'p.x = 5\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-prop-set-fail-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "property 'x' of 'P' object has no setter"):
                compile_native(source, Path(directory) / "program.exe")

    def test_descriptors_property_del_on_getter_only_fails_closed(self):
        source = (
            'clase P:\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver 1\n'
            'p = P()\n'
            'borrar p.x\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-prop-del-fail-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "property 'x' of 'P' object has no deleter"):
                compile_native(source, Path(directory) / "program.exe")

    def test_descriptors_property_call_fails_closed(self):
        source = (
            'clase P:\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver 1\n'
            'p = P()\n'
            'imprimir(p.x())\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-prop-call-fail-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "not a method"):
                compile_native(source, Path(directory) / "program.exe")

    def test_descriptors_setter_without_getter_fails_closed(self):
        source = (
            'clase P:\n'
            '    @x.setter\n'
            '    funcion x(self, v):\n'
            '        pass\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-prop-mismatch-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "requires the property getter"):
                compile_native(source, Path(directory) / "program.exe")

    def test_descriptors_unknown_class_decorator_fails_closed(self):
        source = (
            'clase P:\n'
            '    @miclase\n'
            '    funcion x(self):\n'
            '        devolver 1\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-prop-unknown-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "only @property"):
                compile_native(source, Path(directory) / "program.exe")

    def test_descriptors_duplicate_property_fails_closed(self):
        source = (
            'clase P:\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver 1\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver 2\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-prop-dup-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "duplicate"):
                compile_native(source, Path(directory) / "program.exe")

    def test_descriptors_del_plain_attribute_next_to_property(self):
        source = (
            'clase P:\n'
            '    funcion __init__(self):\n'
            '        self.campos = 0\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver self.campos\n'
            '    @x.setter\n'
            '    funcion x(self, v):\n'
            '        self.campos = v\n'
            '    @x.deleter\n'
            '    funcion x(self):\n'
            '        self.campos = -1\n'
            'p = P()\n'
            'borrar p.campos\n'
            'p.campos = 5\n'
            'imprimir(p.x)\n'
            'intentar:\n'
            '    borrar p.otro\n'
            'excepto AttributeError:\n'
            '    imprimir("sin otro")\n'
        )
        # A plain instance attribute next to a property is deleted like CPython.
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_for_break(self):
        source = (
            'suma = 0\n'
            'para i en [1, 2, 3, 4, 5]:\n'
            '    si i == 3:\n'
            '        romper\n'
            '    suma = suma + i\n'
            'imprimir(suma)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_for_continue(self):
        source = (
            'suma = 0\n'
            'para i en [1, 2, 3, 4, 5]:\n'
            '    si i == 3:\n'
            '        continuar\n'
            '    suma = suma + i\n'
            'imprimir(suma)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_for_else_no_break(self):
        source = (
            'para i en [1, 2, 3]:\n'
            '    imprimir(i)\n'
            'sino:\n'
            '    imprimir("else")\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_for_else_with_break(self):
        source = (
            'para i en [1, 2, 3]:\n'
            '    si i == 2:\n'
            '        romper\n'
            '    imprimir(i)\n'
            'sino:\n'
            '    imprimir("else")\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_while_break(self):
        source = (
            'i = 0\n'
            'mientras i < 5:\n'
            '    si i == 3:\n'
            '        romper\n'
            '    i = i + 1\n'
            'imprimir(i)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_while_continue(self):
        source = (
            'suma = 0\n'
            'i = 0\n'
            'mientras i < 5:\n'
            '    i = i + 1\n'
            '    si i == 3:\n'
            '        continuar\n'
            '    suma = suma + i\n'
            'imprimir(suma)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_while_else_no_break(self):
        source = (
            'i = 0\n'
            'mientras i < 3:\n'
            '    i = i + 1\n'
            'sino:\n'
            '    imprimir("else")\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_while_else_with_break(self):
        source = (
            'i = 0\n'
            'mientras i < 5:\n'
            '    si i == 2:\n'
            '        romper\n'
            '    i = i + 1\n'
            'sino:\n'
            '    imprimir("else")\n'
            'imprimir(i)\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)


class ComprehensionsV2Native(unittest.TestCase):
    def assert_native_matches(self, source: str):
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_list_comp_basic(self):
        self.assert_native_matches(
            'cuadrados = [x * x para x en [1, 2, 3, 4, 5]]\nimprimir(cuadrados)\n'
        )

    def test_list_comp_with_if(self):
        self.assert_native_matches(
            'pares = [x para x en [1, 2, 3, 4, 5, 6] si x % 2 == 0]\nimprimir(pares)\n'
        )

    def test_list_comp_chained_for(self):
        self.assert_native_matches(
            'pairs = [(a, b) para a en [1, 2] para b en [3, 4]]\nimprimir(pairs)\n'
        )

    def test_list_comp_chained_for_triple(self):
        self.assert_native_matches(
            't = [(a, b, c) para a en [1, 2] para b en [3, 4] para c en [5, 6]]\nimprimir(t)\n'
        )

    def test_list_comp_with_tuple_element(self):
        self.assert_native_matches(
            't = [(x, x * 2) para x en [1, 2, 3]]\nimprimir(t)\n'
        )

    def test_list_comp_tuple_element_with_if(self):
        self.assert_native_matches(
            't = [(x, x * 2) para x en [1, 2, 3, 4] si x % 2 == 0]\nimprimir(t)\n'
        )

    def test_list_comp_multiple_if(self):
        self.assert_native_matches(
            't = [x para x en [0, 1, 2, 3, 4, 5, 6, 7, 8, 9] si x > 3 si x < 7]\nimprimir(t)\n'
        )

    def test_list_comp_nested(self):
        self.assert_native_matches(
            't = [[x * z para z en [1, 2, 3]] para x en [1, 2]]\nimprimir(t)\n'
        )

    def test_list_of_tuples_literal(self):
        self.assert_native_matches(
            't = [(1, 2), (3, 4)]\nimprimir(t)\n'
        )

    def test_nested_list_literal(self):
        self.assert_native_matches(
            't = [[1, 2], [3, 4]]\nimprimir(t)\n'
        )

    def test_tuple_of_lists_literal(self):
        self.assert_native_matches(
            't = ([1, 2], [3, 4])\nimprimir(t)\n'
        )

    def test_tuple_of_tuples_literal(self):
        self.assert_native_matches(
            't = ((1, 2), (3, 4))\nimprimir(t)\n'
        )

    def test_tuple_of_mixed_literal(self):
        self.assert_native_matches(
            't = (1, [2, 3], (4, 5))\nimprimir(t)\n'
        )

    def test_3deep_nested_literal(self):
        self.assert_native_matches(
            't = [[[1]]]\nimprimir(t)\n'
        )

    def test_list_comp_filter_neq(self):
        self.assert_native_matches(
            't = [x para x en [1, 2, 3, 4, 5] si x != 3]\nimprimir(t)\n'
        )

    def test_genexpr_is_iterable_and_stateful(self):
        self.assert_native_matches(
            'g = (x * 2 para x en [1, 2, 3])\n'
            'imprimir(next(g))\n'
            'imprimir(next(iter(g)))\n'
        )

    def test_genexpr_stop_iteration(self):
        self.assert_native_matches(
            'g = (x para x en [7])\n'
            'intentar:\n'
            '    imprimir(next(g))\n'
            '    next(g)\n'
            'excepto StopIteration:\n'
            '    imprimir("exhausted")\n'
        )

    def test_set_comp_basic_and_deduplicates(self):
        self.assert_native_matches(
            's = {x para x en [1, 2, 2, 3]}\nimprimir(s)\n'
        )

    def test_set_comp_with_if(self):
        self.assert_native_matches(
            's = {x para x en [1, 2, 3, 4] si x % 2 == 0}\nimprimir(s)\n'
        )

    def test_dict_comp_basic(self):
        self.assert_native_matches(
            'd = {x: x * x para x en [1, 2, 3]}\nimprimir(d)\n'
        )

    def test_dict_comp_with_if(self):
        self.assert_native_matches(
            'd = {x: x * 2 para x en [1, 2, 3, 4] si x > 2}\nimprimir(d)\n'
        )


class CycleGCNativeV1(unittest.TestCase):
    """M13 GC_CYCLES_V1 runtime slice on Win64: direct cycle-observation tests."""

    @requires_windows
    def test_gc_collects_list_self_cycle_and_object_graph(self):
        from piton.x86 import win64_c_compiler
        gcc = win64_c_compiler() or "gcc"
        runtime = Path(__file__).resolve().parents[1] / "piton" / "native_runtime.c"
        source = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern void *piton_collection_new(int64_t kind, int64_t capacity);
extern void piton_list_append(void *raw, int64_t value, int64_t type_tag);
extern void piton_collection_free(void *raw);
extern void *piton_object_new(const char *class_name);
extern void piton_object_set_tagged(void *raw, const char *name, int64_t raw_ptr);
extern void piton_object_free(void *raw);
extern void piton_gc_collect(void);
extern int64_t piton_total_live_count(void);

static void expect_count(const char *name, int64_t expected) {
    int64_t actual = piton_total_live_count();
    if (actual != expected) {
        fprintf(stderr, "%s: expected=%lld actual=%lld\n", name,
                (long long)expected, (long long)actual);
        exit(1);
    }
}

int main(void) {
    void *self_list = piton_collection_new(1, 0);
    piton_list_append(self_list, (int64_t)self_list, 1);
    piton_collection_free(self_list);
    expect_count("list self-cycle before collect", 1);
    piton_gc_collect();
    expect_count("list self-cycle after collect", 0);

    void *owner = piton_object_new("Nodo");
    void *items = piton_collection_new(1, 0);
    piton_object_set_tagged(owner, "items", (int64_t)items);
    piton_list_append(items, (int64_t)owner, 1);
    piton_object_free(owner);
    piton_collection_free(items);
    expect_count("object-list cycle before collect", 2);
    piton_gc_collect();
    expect_count("object-list cycle after collect", 0);

    piton_gc_collect();
    expect_count("empty collect", 0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="piton-gc-v1-") as directory:
            parseable = Path(directory) / "gc_cycle_test.exe"
            harness = Path(directory) / "gc_cycle_test.c"
            harness.write_text(source, encoding="utf-8")
            built = subprocess.run(
                [gcc, "-std=c11", "-O2", str(harness), str(runtime), "-o", str(parseable), "-lm"],
                capture_output=True, text=True,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            completed = run_native([str(parseable)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)


class WithProtocolNativeV1(unittest.TestCase):

    """M10 — WITH_PROTOCOL_V1: `con CM() como x:` with exception unwind and
    suppression, differential vs CPython 3.12."""

    def assert_native_matches(self, source):
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_with_normal_path(self):
        self.assert_native_matches(
            'clase CM:\n'
            '    funcion __enter__(self):\n'
            '        imprimir("enter")\n'
            '        devolver 42\n'
            '    funcion __exit__(self, tipo, mensaje, tb):\n'
            '        imprimir("exit")\n'
            '        devolver Falso\n'
            'con CM() como x:\n'
            '    imprimir("body")\n'
            '    imprimir(x)\n'
        )

    def test_m13_self_cycle_reclaimed_at_module_teardown(self):
        """M13 GC_CYCLES_V1 first slice: mutual object cycles tear down safely."""
        self.assert_native_matches(
            'clase Nodo:\n'
            '    funcion __init__(self):\n'
            '        self.ref = self\n'
            'a = Nodo()\n'
            'b = Nodo()\n'
            'a.ref = b\n'
            'b.ref = a\n'
            'imprimir("ok")\n'
        )

    def test_with_exception_propagates_to_handler(self):
        self.assert_native_matches(
            'clase CM:\n'
            '    funcion __enter__(self):\n'
            '        imprimir("enter")\n'
            '        devolver 1\n'
            '    funcion __exit__(self, tipo, mensaje, tb):\n'
            '        imprimir("exit")\n'
            '        imprimir(mensaje)\n'
            '        devolver Falso\n'
            'intentar:\n'
            '    con CM() como w:\n'
            '        lanzar ValueError("boom")\n'
            'excepto ValueError:\n'
            '    imprimir("handler")\n'
        )

    def test_with_suppression(self):
        self.assert_native_matches(
            'clase CM:\n'
            '    funcion __enter__(self):\n'
            '        devolver 1\n'
            '    funcion __exit__(self, tipo, mensaje, tb):\n'
            '        imprimir("suprimo")\n'
            '        devolver Verdadero\n'
            'intentar:\n'
            '    con CM() como z:\n'
            '        lanzar ValueError("boom")\n'
            'excepto ValueError:\n'
            '    imprimir("handler")\n'
            'imprimir("fin")\n'
        )

    def test_with_unhandled_exception(self):
        source = (
            'clase CM:\n'
            '    funcion __enter__(self):\n'
            '        devolver 1\n'
            '    funcion __exit__(self, tipo, mensaje, tb):\n'
            '        imprimir("exit")\n'
            '        imprimir(tipo)\n'
            '        imprimir(mensaje)\n'
            '        devolver Falso\n'
            'con CM() como x:\n'
            '    lanzar ValueError("boom")\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-with-unhandled-") as directory:
            executable = compile_native(source, Path(directory) / "program.exe")
            completed = run_native([str(executable)], capture_output=True, check=False)
            self.assertEqual(completed.returncode, 1, completed.stderr.decode(errors="replace"))
            self.assertEqual(
                completed.stdout.decode(errors="replace"),
                "exit\r\nValueError\r\nboom\r\n",
            )
            self.assertIn("ValueError: boom", completed.stderr.decode(errors="replace"))

    def test_with_requires_dunder_methods(self):
        with tempfile.TemporaryDirectory(prefix="piton-with-dunder-") as directory:
            with self.assertRaisesRegex(Exception, "must define __enter__ and __exit__"):
                compile_native(
                    'clase CM:\n    funcion __init__(self):\n        self.x = 1\n'
                    'con CM() como y:\n    imprimir(y)\n',
                    Path(directory) / "program.exe",
                )

    def test_with_requires_direct_constructor(self):
        with tempfile.TemporaryDirectory(prefix="piton-with-ctor-") as directory:
            with self.assertRaisesRegex(Exception, "direct call"):
                compile_native(
                    'clase CM:\n'
                    '    funcion __enter__(self):\n        devolver 1\n'
                    '    funcion __exit__(self, t, m, tb):\n        devolver Falso\n'
                    'xobj = CM()\n'
                    'con xobj como y:\n    imprimir(y)\n',
                    Path(directory) / "program.exe",
                )

    def test_with_multiple_items_fails_closed(self):
        # WITH_MULTIPLE_V1 closed the old fail: nested lowers are real
        result = compare_native_to_cpython(
            'clase CM:\n'
            '    funcion __enter__(self):\n        devolver 1\n'
            '    funcion __exit__(self, t, m, tb):\n        devolver Falso\n'
            'con CM() como a, CM() como b:\n    imprimir(a + b)\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_with_multiple_suppress_and_propagate(self):
        result = compare_native_to_cpython(
            'clase CM:\n'
            '    funcion __enter__(self):\n        devolver 1\n'
            '    funcion __exit__(self, t, m, tb):\n        devolver Falso\n'
            'intentar:\n'
            '    con CM() como a, CM() como b:\n        lanzar ValueError("x")\n'
            'excepto ValueError:\n    imprimir("caught")\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_class_getattr_hook_missing(self):
        result = compare_native_to_cpython(
            'clase C:\n'
            '    funcion __init__(self):\n'
            '        self.x = 7\n'
            '    funcion __getattr__(self, nombre):\n'
            '        devolver 99\n'
            'c = C()\n'
            'imprimir(c.noExiste)\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_class_getattr_field_wins_over_hook(self):
        result = compare_native_to_cpython(
            'clase C:\n'
            '    funcion __init__(self):\n'
            '        self.x = 7\n'
            '    funcion __getattr__(self, nombre):\n'
            '        devolver 99\n'
            'c = C()\n'
            'x = c.x\n'
            'imprimir(x)\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_class_setattr_hook(self):
        result = compare_native_to_cpython(
            'clase C:\n'
            '    funcion __setattr__(self, nombre, valor):\n'
            '        imprimir(valor)\n'
            'c = C()\n'
            'c.x = 5\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_class_delattr_hook(self):
        result = compare_native_to_cpython(
            'clase C:\n'
            '    funcion __delattr__(self, nombre):\n'
            '        imprimir("del")\n'
            'c = C()\n'
            'c.x = 1\n'
            'borrar c.x\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_class_call_routes_to_call(self):
        result = compare_native_to_cpython(
            'clase C:\n'
            '    funcion __init__(self, base):\n'
            '        self.base = base\n'
            '    funcion __call__(self, x):\n'
            '        devolver self.base + x\n'
            'c = C(10)\n'
            'imprimir(c(21))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_class_eq_custom_dispatch(self):
        result = compare_native_to_cpython(
            'clase C:\n'
            '    funcion __init__(self, v):\n'
            '        self.v = v\n'
            '    funcion __eq__(self, otra):\n'
            '        devolver self.v == otra.v\n'
            'a = C(7)\n'
            'b = C(7)\n'
            'c = C(9)\n'
            'imprimir(a == b)\n'
            'imprimir(a == c)\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_class_is_identity(self):
        result = compare_native_to_cpython(
            'clase C:\n'
            '    funcion __init__(self):\n'
            '        self.x = 1\n'
            'a = C()\n'
            'b = a\n'
            'c = C()\n'
            'imprimir(a es b)\n'
            'imprimir(a es c)\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_class_str_dispatch(self):
        result = compare_native_to_cpython(
            'clase C:\n'
            '    funcion __init__(self, v):\n'
            '        self.v = v\n'
            '    funcion __str__(self):\n'
            '        devolver "caja"\n'
            'imprimir(C(1))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_x86_class_len_dispatch(self):
        result = compare_native_to_cpython(
            'clase C:\n'
            '    funcion __init__(self):\n'
            '        self.n = 5\n'
            '    funcion __len__(self):\n'
            '        devolver self.n\n'
            'imprimir(longitud(C()))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_with_async_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-with-async-") as directory:
            with self.assertRaisesRegex(Exception, "__aenter__"):
                compile_native(
                    'clase CM:\n'
                    '    funcion __enter__(self):\n        devolver 1\n'
                    '    funcion __exit__(self, t, m, tb):\n        devolver Falso\n'
                    'asincrono funcion p():\n'
                    '    asincrono con CM() como y:\n        imprimir(y)\n',
                    Path(directory) / "program.exe",
                )

    def test_async_raise_with_cause_chain_caught(self):
        # EXCEPTION_CHAINING_V1: the cause rides along; catching the outer type
        # gives us the outer message (the cause shows only when unhandled).
        result = compare_native_to_cpython(
            'intentar:\n'
            '    lanzar ValueError("externo") desde TypeError("causa")\n'
            'excepto ValueError como e:\n'
            '    imprimir(e)\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_raise_from_chain_visible_unhandled(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            executable = compile_native(
                'lanzar ValueError("externo") desde TypeError("causa")\n',
                Path(directory) / "program.exe",
            )
            completed = run_native([str(executable)], capture_output=True, check=False)
            stderr = completed.stderr.decode(errors="replace")
            self.assertNotEqual(completed.returncode, 0, stderr)
            self.assertIn("TypeError", stderr)
            self.assertIn("ValueError", stderr)

    def test_base_exception_catches_anything(self):
        result = compare_native_to_cpython(
            'intentar:\n    lanzar TypeError("c1")\nexcepto BaseException como e:\n    imprimir("caught")\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_bare_reraise_from_catchall_handler(self):
        result = compare_native_to_cpython(
            'intentar:\n'
            '    intentar:\n'
            '        lanzar ValueError("boom2")\n'
            '    excepto Exception:\n'
            '        lanzar\n'
            'excepto ValueError como e:\n'
            '    imprimir(e)\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_async_with_awaits_enter_and_exit(self):
        result = compare_native_to_cpython(
            'importar asyncio\n'
            'clase CM:\n'
            '    asincrono funcion __aenter__(self):\n'
            '        devolver 7\n'
            '    asincrono funcion __aexit__(self, t, m, tb):\n'
            '        devolver Falso\n'
            'asincrono funcion run_async():\n'
            '    asincrono con CM() como x:\n'
            '        devolver x\n'
            'imprimir(asyncio.run(run_async()))\n'
        )
        self.assertTrue(result.equivalent, result)

    def test_async_exception_is_caught_inside_coroutine(self):
        result = compare_native_to_cpython(
            'importar asyncio\n'
            'asincrono funcion run_async():\n'
            '    intentar:\n'
            '        lanzar ValueError("async boom")\n'
            '    excepto ValueError como error:\n'
            '        devolver 1\n'
            'imprimir(asyncio.run(run_async()))\n'
        )
        self.assertTrue(result.equivalent, result)


class FinalizersNativeV1(unittest.TestCase):
    """M13 FINALIZERS_V1: __del__ runs exactly once before program exit."""

    def assert_native_matches(self, source):
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_finalizer_runs_at_exit(self):
        self.assert_native_matches(
            'clase Recurso:\n'
            '    funcion __del__(self):\n'
            '        imprimir("cerrado")\n'
            'r = Recurso()\n'
            'imprimir("listo")\n'
        )

    def test_finalizer_runs_once_for_self_cycle(self):
        self.assert_native_matches(
            'clase Nodo:\n'
            '    funcion __init__(self):\n'
            '        self.ref = self\n'
            '    funcion __del__(self):\n'
            '        imprimir("del")\n'
            'Nodo()\n'
            'imprimir("ok")\n'
        )


if __name__ == "__main__":
    unittest.main()
