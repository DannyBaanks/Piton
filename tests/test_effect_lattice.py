from __future__ import annotations

import re
import unittest
from dataclasses import replace
from pathlib import Path

from piton.lower import lower_cst_to_hir
from piton.mir import (
    MIR_EFFECT_CLASSES,
    MIR_OP_EFFECTS,
    MIRInstruction,
    MIRLoweringError,
    annotate_module_effects,
    chain_effect_tokens,
    classify_instruction_effects,
    lower_hir_to_mir,
    verify_effect_chain,
)
from piton.native_differential import compare_native_to_cpython
from piton.parser import parse

_REPO_ROOT = Path(__file__).resolve().parent.parent

# ME battery: covers arithmetic, collections, control, functions, exceptions,
# the M10 with-protocol, generators, classes and the async scheduler.
_BATTERY = (
    "imprimir(2 + 3)\n",
    "x = [1, 2, 3]\nx.append(4)\nimprimir(x)\nimprimir(longitud(x))\n",
    'd = {"a": 1}\nd["b"] = 2\nimprimir(d["b"])\n',
    'intentar:\n    lanzar ValueError("boom")\nexcepto ValueError:\n    imprimir("caught")\n',
    "funcion doble(n):\n    devolver n * 2\nimprimir(doble(21))\n",
    "x = 0\nmientras x < 3:\n    x = x + 1\nimprimir(x)\n",
    "clase CM:\n"
    "    funcion __enter__(self):\n        devolver 1\n"
    "    funcion __exit__(self, t, m, tb):\n        devolver Falso\n"
    "con CM() como r:\n    imprimir(r)\n",
    "funcion gen():\n    producir 1\n    producir 2\npara v en gen():\n    imprimir(v)\n",
    (
        "clase Punto:\n"
        "    funcion __init__(self, px):\n        self.px = px\n"
        "    funcion get(self):\n        devolver self.px\n"
        "p = Punto(7)\nimprimir(p.get())\n"
    ),
    (
        'importar asyncio\n'
        'asincrono funcion p():\n    esperar asyncio.sleep(0)\n    imprimir("ok")\n'
        "asyncio.run(p())\n"
    ),
)

# Effect-neutral battery for the differential check (compact on purpose; the
# full neutrality evidence is the phase5 + phase10 regression suites).
_NEUTRAL_BATTERY = (
    "imprimir(2 + 3)\n",
    "x = 0\nmientras x < 3:\n    x = x + 1\nimprimir(x)\n",
    'intentar:\n    lanzar ValueError("boom")\nexcepto ValueError:\n    imprimir("caught")\n',
    "funcion doble(n):\n    devolver n * 2\nimprimir(doble(21))\n",
)


def _lower(source: str):
    return lower_hir_to_mir(lower_cst_to_hir(parse(source)))


class EffectLatticeV1(unittest.TestCase):
    def test_table_covers_every_emitted_op(self):
        """Every op string emitted by mir.py/optimizer.py is classified."""
        ops: set[str] = set()
        for name in ("piton/mir.py", "piton/optimizer.py"):
            text = (_REPO_ROOT / name).read_text(encoding="utf-8")
            for match in re.finditer(r"\.emit\(\s*['\"]([a-zA-Z0-9_]+)['\"]", text):
                ops.add(match.group(1))
            for match in re.finditer(r"MIRInstruction\(\s*['\"]([a-zA-Z0-9_]+)['\"]", text):
                ops.add(match.group(1))
        unclassified = ops - set(MIR_OP_EFFECTS)
        self.assertEqual(unclassified, set(), f"ops sin clasificar: {sorted(unclassified)}")
        for klass in MIR_OP_EFFECTS.values():
            self.assertIn(klass, MIR_EFFECT_CLASSES)

    def test_classification_fails_closed_on_unknown_op(self):
        with self.assertRaisesRegex(MIRLoweringError, "EFFECT_CLASSIFICATION_V1"):
            classify_instruction_effects(MIRInstruction("op_inventada", (), None))

    def test_annotate_and_chain_on_battery(self):
        for source in _BATTERY:
            with self.subTest(source=source[:40]):
                module = _lower(source)
                seen_pure = seen_effect = False
                for function in module.functions:
                    for block in function.blocks:
                        for instruction in block.instructions:
                            self.assertTrue(
                                instruction.effects,
                                f"sin clasificar: {instruction.op}",
                            )
                            self.assertIn(instruction.effects[0], MIR_EFFECT_CLASSES)
                            if instruction.effects[0] == "PURE":
                                seen_pure = True
                                self.assertIsNone(instruction.token)
                                self.assertIsNone(instruction.token_prev)
                            else:
                                seen_effect = True
                                self.assertIsNotNone(instruction.token)
                self.assertTrue(seen_pure and seen_effect)
                verify_effect_chain(module)  # must not raise

    def test_chain_verifier_detects_tamper(self):
        module = _lower('x = [1]\nx.append(2)\nimprimir(x)\n')
        broken = False
        for function in module.functions:
            for block in function.blocks:
                for index, instruction in enumerate(block.instructions):
                    if instruction.token == 1:
                        block.instructions[index] = replace(instruction, token_prev=99)
                        broken = True
        self.assertTrue(broken, "la bateria no produjo una cadena de efectos >= 2")
        with self.assertRaisesRegex(MIRLoweringError, "EFFECT_TOKEN_CHAIN_V1"):
            verify_effect_chain(module)

    def test_schema_includes_effect_fields_and_stays_deterministic(self):
        source = 'x = 1\nimprimir(x)\n'
        first = _lower(source).to_json()
        second = _lower(source).to_json()
        self.assertEqual(first, second)  # MIR_DETERMINISTIC preserved post-rebaseline
        self.assertIn('"effects":', first)
        self.assertIn('"token":', first)
        self.assertIn('"token_prev":', first)

    def test_effect_neutrality_small_corpus(self):
        for source in _NEUTRAL_BATTERY:
            with self.subTest(source=source[:40]):
                result = compare_native_to_cpython(source)
                self.assertTrue(result.equivalent, result)


if __name__ == "__main__":
    unittest.main()
