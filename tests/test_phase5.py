from __future__ import annotations

import os
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest

from piton.x86 import NativeBuildError, compile_native, compile_native_files
from piton.native_differential import compare_native_to_cpython
from piton.translator import traducir_fuente


class Phase5Gates(unittest.TestCase):
    def build_run(self, source: str) -> tuple[str, bytes]:
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            executable = compile_native(source, Path(directory) / "program.exe")
            completed = subprocess.run([str(executable)], capture_output=True, check=False)
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

    def test_x86_rejects_true_division_instead_of_miscompiling(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "true division"):
                compile_native("imprimir(7 / 2)\n", Path(directory) / "program.exe")

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

    def test_x86_rejects_incompatible_ordering(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "ordering not supported"):
                compile_native('imprimir("a" < 1)\n', Path(directory) / "program.exe")

    def test_x86_rejects_more_than_four_arguments(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "more than four"):
                compile_native(
                    "funcion f(a, b, c, d, e):\n    devolver a\nimprimir(f(1, 2, 3, 4, 5))\n",
                    Path(directory) / "program.exe",
                )

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
            completed = subprocess.run([str(executable)], capture_output=True, check=False)
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

    def test_x86_typed_raise_caught_by_except(self):
        source = (
            'intentar:\n'
            '    lanzar ValueError("x")\n'
            'excepto ValueError:\n'
            '    imprimir("caught")\n'
        )
        result = compare_native_to_cpython(source)
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
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            source = (
                'intentar:\n'
                '    lanzar ValueError("x")\n'
                'excepto Exception:\n'
                '    lanzar\n'
                'imprimir("done")\n'
            )
            with self.assertRaisesRegex(Exception, "catch-all"):
                compile_native(source, Path(directory) / "program.exe")

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

    def test_x86_rejects_generator_escape(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "cannot escape"):
                compile_native(
                    "funcion valores():\n    producir 1\nx = valores()\n",
                    Path(directory) / "program.exe",
                )

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
            completed = subprocess.run([str(executable)], capture_output=True, check=False)
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
            completed = subprocess.run([str(executable)], capture_output=True, check=False)
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
            completed = subprocess.run([str(executable)], capture_output=True, check=False)
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
            completed = subprocess.run([str(executable)], capture_output=True, check=False)
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
            native_run = subprocess.run([str(executable)], capture_output=True, check=False)
            oracle_run = subprocess.run([sys.executable, str(main_py)], capture_output=True, check=False)
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
            native_run = subprocess.run([str(executable)], capture_output=True, check=False)
            oracle_run = subprocess.run([sys.executable, str(main_py)], capture_output=True, check=False)
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
            completed = subprocess.run([str(executable)], capture_output=True, check=False)
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
            native_run = subprocess.run([str(executable)], capture_output=True, check=False)
            oracle_run = subprocess.run([sys.executable, str(main_py)], capture_output=True, check=False)
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
            native_run = subprocess.run([str(executable)], capture_output=True, check=False)
            oracle_run = subprocess.run([sys.executable, str(main_py)], capture_output=True, check=False)
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
            with self.assertRaisesRegex(Exception, "beyond one level"):
                compile_native_files(entry, root / "program.exe")

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

    def test_x86_async_rejects_await_outside_async(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "await is only valid"):
                compile_native(
                    "devolver esperar 1\n",
                    Path(directory) / "program.exe",
                )

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

    def test_x86_kwargs_unpacking_rejected(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "unpacking"):
                compile_native(
                    'funcion f(**kw):\n    devolver longitud(kw)\nimprimir(f(**{"x": 1}))\n',
                    Path(directory) / "program.exe",
                )

    def test_x86_starargs_unpacking_rejected(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "positional unpacking"):
                compile_native(
                    'funcion f(*args):\n    devolver longitud(args)\nimprimir(f(*[1, 2]))\n',
                    Path(directory) / "program.exe",
                )

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


if __name__ == "__main__":
    unittest.main()
