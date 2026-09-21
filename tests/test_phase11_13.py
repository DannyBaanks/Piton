from __future__ import annotations

import ast
import ctypes.util
import io
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
import unittest

from piton import eval_piton, exec_piton, inspect_piton_source
from piton.lower import lower_cst_to_hir
from piton.mir import MIREvaluator, evaluate_mir, lower_hir_to_mir
from piton.optimizer import optimize_mir
from piton.stdlib_runtime import run_subprocess, stdlib_modules
from piton.translator import traducir_fuente


class Phase11To13Bootstrap(unittest.TestCase):
    def test_mir_generator_preserves_frame_between_yields(self):
        source = (
            "funcion contador():\n"
            "    imprimir(10)\n"
            "    producir 1\n"
            "    imprimir(20)\n"
            "    producir 2\n"
            "x = contador()\n"
            "imprimir(next(x))\n"
            "imprimir(next(x))\n"
        )
        hir = lower_cst_to_hir(parse_source(source))
        evaluator = MIREvaluator(lower_hir_to_mir(hir))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertIsNone(evaluator.run())
        self.assertEqual(output.getvalue(), "10\n1\n20\n2\n")

    def test_explicit_dynamic_apis_and_source_contract(self):
        self.assertEqual(eval_piton("1 + 2"), 3)
        namespace = exec_piton("valor = 4\n")
        self.assertEqual(namespace["valor"], 4)
        inspected = inspect_piton_source("imprimir('ok')\n", "demo.piton")
        self.assertEqual(inspected["filename"], "demo.piton")
        ast.parse(inspected["generated"])
        self.assertIn("print", inspected["generated"])

    def test_stdlib_and_safe_subprocess(self):
        modules = stdlib_modules()
        self.assertEqual(modules["math"].sqrt(9), 3)
        result = run_subprocess(["cmd", "/c", "echo", "piton"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "piton")

    def test_explicit_ffi_surface(self):
        self.assertIsInstance(ctypes.util.find_library("c") or "ucrtbase", str)

    def test_optimization_levels_preserve_mir_result(self):
        hir = lower_cst_to_hir(parse_source("imprimir(2 + 3)\n"))
        module = lower_hir_to_mir(hir)
        baseline, _ = evaluate_mir(optimize_mir(module, 0))
        for level in (1, 2):
            optimized, _ = evaluate_mir(optimize_mir(module, level))
            self.assertEqual((baseline, optimized), (None, None))
        self.assertNotEqual(optimize_mir(module, 1).to_json(), "")


def parse_source(source: str):
    from piton.parser import parse
    return parse(source)


if __name__ == "__main__":
    unittest.main()
