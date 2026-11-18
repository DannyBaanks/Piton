"""TIER_B_DIVERGENCE_MAP_V1: el mapa por ejemplo de los scripts divergentes.

`parity_corpus_v4.py` dice QUE script diverge (todo-o-nada). Este gate
comprueba que el diagnostico por ejemplo sigue viendo EXACTAMENTE los
ejemplos que divergen: si un arreglo del backend rompe de nuevo el
formateo (el caso historico: el namespace de keywords pisaba los
posicionales), el ejemplo 34 de inputoutput vuelve a divergir y este test
se cae — sin que la metrica global (72.7%) se mueva.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "divmap", REPO / "tools" / "tier_b_divergence_map.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DIVMAP = _load_tool()


class InstrumentV1(unittest.TestCase):
    def test_numbers_every_example(self):
        source = "try:\n    print(1)\nexcept BaseException:\n    print('EXC')\n"
        instrumented, count = DIVMAP.instrument(source)
        self.assertEqual(count, 1)
        self.assertIn("print('@@1')", instrumented)

    def test_exposes_the_swallowed_exception(self):
        source = "try:\n    print(1)\nexcept BaseException:\n    print('EXC')\n"
        instrumented, _count = DIVMAP.instrument(source)
        self.assertIn("except BaseException as _e:", instrumented)
        self.assertIn("print('EXC', _e)", instrumented)

    def test_honest_rule_treats_any_exception_as_exc(self):
        self.assertEqual(DIVMAP._honest(["EXC KeyError 'x'"]), ["EXC"])
        self.assertEqual(DIVMAP._honest(["hola"]), ["hola"])


class InputoutputDivergenceV1(unittest.TestCase):
    """inputoutput.py actualmente equivale en los 68 ejemplos.

    Medido (2026-11-16, backend linux_x86): el ejemplo 41, que imprimia
    valores internos incorrectos al formatear el resultado de `vars()`, ya
    coincide con CPython. Los ejemplos 39 y 61 tambien coinciden cuando el
    mapa ejecuta ambos lados en su directorio temporal compartido.
    """

    @unittest.skipIf(
        sys.platform == "win32",
        "Linux ELF native backend is not available on the Windows CI runner",
    )
    def test_divergent_examples_are_exactly_these(self):
        result = DIVMAP.run_script("inputoutput")
        self.assertNotIn("error", result, result.get("error"))
        divergent = {chunk["chunk"] for chunk in result["chunks"]}
        self.assertEqual(result["chunks_total"], 68)
        self.assertEqual(divergent, set(), f"divergencias en inputoutput: {divergent}")


if __name__ == "__main__":
    unittest.main()
