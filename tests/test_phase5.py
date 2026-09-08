from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from piton.x86 import compile_native, compile_native_files
from piton.native_differential import compare_native_to_cpython


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

    def test_x86_rejects_escaping_closure(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "cannot escape"):
                compile_native(
                    "funcion exterior(x):\n    funcion interior():\n        devolver x\n    devolver interior\n",
                    Path(directory) / "program.exe",
                )

    def test_x86_typed_raise_caught_by_except(self):
        source = (
            'intentar:\n'
            '    lanzar ValueError("x")\n'
            'excepto ValueError:\n'
            '    imprimir("caught")\n'
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_x86_rejects_finally_until_unwind_exists(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "finally"):
                compile_native(
                    'intentar:\n    imprimir("x")\nfinalmente:\n    imprimir("done")\n',
                    Path(directory) / "program.exe",
                )

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

    def test_x86_rejects_class_inheritance(self):
        with tempfile.TemporaryDirectory(prefix="piton-phase5-") as directory:
            with self.assertRaisesRegex(Exception, "no inheritance"):
                compile_native(
                    "clase Base:\n    pasar\nclase Hija(Base):\n    pasar\n",
                    Path(directory) / "program.exe",
                )

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

    def test_x86_bigint_arithmetic(self):
        source = "imprimir(1180591620717411303424 + 1)\n"
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)


if __name__ == "__main__":
    unittest.main()
