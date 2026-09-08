from __future__ import annotations

import ast
import contextlib
import dataclasses
import io
import json
import subprocess
import sys
import unittest
from enum import Enum

from piton.analysis import analyze_scopes
from piton.backend_python import generate_python_from_cst, generate_python_from_hir
from piton.lower import lower_cst_to_hir
from piton.mir import evaluate_mir, lower_hir_to_mir
from piton.parser import parse
from piton.translator import traducir_fuente


def shape(node):
    """Serialización estable de la estructura propia, sin posiciones volátiles."""
    if isinstance(node, Enum):
        return node.name
    if dataclasses.is_dataclass(node):
        result = {"kind": type(node).__name__}
        for field in dataclasses.fields(node):
            if field.name in {"line", "col", "end_line", "end_col", "start_byte", "end_byte"}:
                continue
            result[field.name] = shape(getattr(node, field.name))
        return result
    if isinstance(node, list):
        return [shape(item) for item in node]
    if isinstance(node, dict):
        return {key: shape(value) for key, value in sorted(node.items())}
    return node


def run_python(source: str) -> tuple[int, str, str]:
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            exec(compile(source, "<phase2>", "exec"), {})
    except Exception as error:  # pragma: no cover - exercised by differential assertion
        return 1, output.getvalue(), f"{type(error).__name__}: {error}"
    return 0, output.getvalue(), ""


class Phase2Gates(unittest.TestCase):
    def test_parse_without_cpython_translator(self):
        code = (
            "from piton.parser import parse\n"
            "import piton.translator\n"
            "piton.translator.traducir_fuente = lambda *a, **k: (_ for _ in ()).throw(AssertionError('translator called'))\n"
            "module = parse('x = 1\\n')\n"
            "assert type(module).__name__ == 'Module'\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scope_analysis_gate(self):
        module = parse(
            "x = 1\n"
            "funcion suma(a):\n"
            "    y = a\n"
            "    devolver y\n"
        )
        result = analyze_scopes(module)
        self.assertEqual(set(result.module_scope.bindings), {"x", "suma"})
        function = next(scope for scope in result.all_scopes if scope.name == "suma")
        self.assertEqual(set(function.bindings), {"a", "y"})
        self.assertFalse(result.errors)

    def test_ast_is_stable(self):
        source = "si Verdadero:\n    imprimir(1)\n"
        first = shape(parse(source))
        second = shape(parse(source))
        self.assertEqual(first, second)
        json.dumps(first, sort_keys=True)

    def test_collection_literal_shape_and_postfix(self):
        module = parse("a = {}\nb = {1,}\nc = {1: 2,}\nd = [3][0]\n")
        values = [statement.value for statement in module.body]
        self.assertEqual(type(values[0]).__name__, "Dict")
        self.assertEqual(type(values[1]).__name__, "Set")
        self.assertEqual(type(values[2]).__name__, "Dict")
        self.assertEqual(type(values[3]).__name__, "Subscript")

    def test_raise_and_except_lowering(self):
        hir = lower_cst_to_hir(parse(
            'intentar:\n    lanzar ValueError("x")\nexcepto ValueError:\n    imprimir("ok")\n'
        ))
        statement = hir.body[0]
        self.assertEqual(statement.kind.name, "TRY")
        self.assertEqual(statement.body[0].kind.name, "RAISE")
        self.assertEqual(statement.handlers[0].type_.name, "ValueError")

    def test_python_backend_from_piton_ast(self):
        source = "x = 1\nsi x:\n    imprimir(x)\n"
        generated = generate_python_from_cst(parse(source))
        ast.parse(generated)
        self.assertEqual(generated.splitlines(), ["x = 1", "if x:", "    print(x)"])

    def test_hir_lowering_and_backend(self):
        source = "x = 1\nfuncion identidad(valor):\n    devolver valor\n"
        hir = lower_cst_to_hir(parse(source))
        generated = generate_python_from_hir(hir)
        ast.parse(generated)
        self.assertIn("x = 1", generated)
        self.assertIn("def identidad(valor):", generated)

    def test_differential_corpus_gate(self):
        corpus = [
            "x = 2\nimprimir(x + 3)\n",
            "si Verdadero:\n    imprimir('si')\n",
            "total = 0\npara i en rango(3):\n    total = total + i\nimprimir(total)\n",
            "funcion doble(x):\n    devolver x * 2\nimprimir(doble(4))\n",
        ]
        for source in corpus:
            with self.subTest(source=source):
                generated = generate_python_from_cst(parse(source))
                oracle = traducir_fuente(source, "phase2.piton")
                self.assertEqual(run_python(generated), run_python(oracle))

    def test_phase2_surface_features(self):
        source = (
            "@decorador\n"
            "funcion doble(x: int) -> int:\n"
            "    devolver x * 2\n"
            "valores = [doble(x) para x en rango(3) si x > 0]\n"
            "imprimir(f'resultado: {valores[1]}')\n"
        )
        generated = generate_python_from_cst(parse(source))
        ast.parse(generated)
        self.assertIn("@decorador", generated)
        self.assertIn("[doble(x) for x in range(3) if x > 0]", generated)
        self.assertIn("f\"resultado: {valores[1]}\"", generated)

    def test_phase3_source_to_hir_gate(self):
        hir = lower_cst_to_hir(parse("x = 2\nfuncion doble(x):\n    devolver x * 2\n"))
        self.assertEqual(hir.kind.name, "MODULE")
        self.assertEqual([node.kind.name for node in hir.body], ["ASSIGN", "FUNC_DEF"])

    def test_phase3_hir_to_mir_and_determinism(self):
        hir = lower_cst_to_hir(parse("x = 2\nsi x > 1:\n    x = x + 1\n"))
        first = lower_hir_to_mir(hir)
        second = lower_hir_to_mir(hir)
        self.assertEqual(first.to_json(), second.to_json())
        operations = {
            instruction.op
            for function in first.functions
            for block in function.blocks
            for instruction in block.instructions
        }
        self.assertTrue({"const", "load", "store", "compare", "branch", "jump"} <= operations)

    def test_phase3_mir_trace_and_oracle(self):
        source = "x = 2\nimprimir(x + 3)\n"
        hir = lower_cst_to_hir(parse(source))
        mir = lower_hir_to_mir(hir)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result, trace = evaluate_mir(mir)
        oracle = run_python(traducir_fuente(source, "phase3.piton"))
        self.assertEqual((result, output.getvalue()), (None, oracle[1]))
        with contextlib.redirect_stdout(io.StringIO()):
            _, second_trace = evaluate_mir(mir)
        self.assertEqual(trace, second_trace)
        self.assertTrue(trace)


if __name__ == "__main__":
    unittest.main()
