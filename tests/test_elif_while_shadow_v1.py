"""Regresiones para cadenas `sino_si`/`elif` dentro de `mientras`/`while` y
`SHADOW_BUILTIN_V1` (nombres de tipos del dialecto reasignados).

Cubre dos bugs de control de flujo y shadowing:
1. `piton/parser.py:_parse_if`: multiples `sino_si` se apilaban en `orelse`
   como hermanos en vez de encadenarse en `orelse[-1]`, haciendo que tras un
   `sino_si` verdadero se ejecutaran tambien las ramas siguientes.
2. `piton/mir.py:_lower_if`: la rama `sino_si` (`len(node.orelse) == 1` con
   `HIRKind.IF`) llamaba recursivamente sin emitir `jump` del `end_block`
   interno al `end_block` externo, dejando bloques vacios que en C emitian
   `goto <fn>___exit;` dentro de un bucle `mientras`.
3. `piton/linux_x86.py:_shadowed_name` (`SHADOW_BUILTIN_V1`): variables
   llamadas `lista`, `rango`, etc. en modulo o funcion dejaban de resolverse
   como el objeto de tipo builtin.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from piton.linux_x86 import compile_native_linux  # noqa: E402
from piton.lower import lower_cst_to_hir  # noqa: E402
from piton.mir import lower_hir_to_mir  # noqa: E402
from piton.parser import IfStmt, parse  # noqa: E402


def _compile_and_run(source: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        exe = Path(tmpdir) / "prog"
        compile_native_linux(source, exe)
        return subprocess.run(
            [str(exe)], capture_output=True, text=True, timeout=15
        )


class ElifWhileAndShadowTest(unittest.TestCase):
    def test_parser_chains_multiple_sino_si(self) -> None:
        src = (
            "si x == 0:\n"
            "    a = 1\n"
            "sino_si x == 1:\n"
            "    a = 2\n"
            "sino_si x == 2:\n"
            "    a = 3\n"
            "sino:\n"
            "    a = 4\n"
        )
        mod = parse(src)
        root = mod.body[0]
        self.assertIsInstance(root, IfStmt)
        self.assertEqual(len(root.orelse), 1)
        first_elif = root.orelse[0]
        self.assertIsInstance(first_elif, IfStmt)
        self.assertEqual(len(first_elif.orelse), 1)
        second_elif = first_elif.orelse[0]
        self.assertIsInstance(second_elif, IfStmt)
        self.assertEqual(len(second_elif.orelse), 1)

    def test_mir_all_if_blocks_terminate_inside_while(self) -> None:
        src = (
            "funcion clasificar(n):\n"
            "    i = 0\n"
            "    out = []\n"
            "    mientras i < n:\n"
            "        si i == 0:\n"
            "            out.append('a')\n"
            "        sino_si i == 1:\n"
            "            out.append('b')\n"
            "        sino_si i == 2:\n"
            "            out.append('c')\n"
            "        sino:\n"
            "            out.append('d')\n"
            "        i = i + 1\n"
            "    devolver out\n"
        )
        mir = lower_hir_to_mir(lower_cst_to_hir(parse(src)))
        fn = next(f for f in mir.functions if f.name == "clasificar")
        for block in fn.blocks:
            self.assertTrue(
                block.instructions
                and block.instructions[-1].op in {"jump", "branch", "return"},
                f"Bloque sin terminador en {fn.name}.{block.label}: {block.instructions}",
            )

    def test_sino_si_chain_inside_mientras_function_and_module(self) -> None:
        src = (
            "funcion clasificar(n):\n"
            "    i = 0\n"
            "    out = []\n"
            "    mientras i < n:\n"
            "        si i == 0:\n"
            "            out.append('a')\n"
            "        sino_si i == 1:\n"
            "            out.append('b')\n"
            "        sino_si i == 2:\n"
            "            out.append('c')\n"
            "        sino:\n"
            "            out.append('d')\n"
            "        i = i + 1\n"
            "    devolver out\n"
            "\n"
            "funcion solo_un_sino_si(n):\n"
            "    i = 0\n"
            "    out = []\n"
            "    mientras i < n:\n"
            "        si i == 0:\n"
            "            out.append(10)\n"
            "        sino_si i == 1:\n"
            "            out.append(20)\n"
            "        sino:\n"
            "            out.append(30)\n"
            "        i = i + 1\n"
            "    devolver out\n"
            "\n"
            "funcion con_control_flujo(n):\n"
            "    i = 0\n"
            "    out = []\n"
            "    mientras i < n:\n"
            "        i = i + 1\n"
            "        si i == 2:\n"
            "            continuar\n"
            "        sino_si i == 4:\n"
            "            romper\n"
            "        sino_si i == 3:\n"
            "            out.append('tres')\n"
            "        out.append(i)\n"
            "    devolver out\n"
            "\n"
            "imprimir(clasificar(5))\n"
            "imprimir(solo_un_sino_si(4))\n"
            "imprimir(con_control_flujo(6))\n"
            "j = 0\n"
            "mod_out = []\n"
            "mientras j < 4:\n"
            "    si j == 0:\n"
            "        mod_out.append('x')\n"
            "    sino_si j == 1:\n"
            "        mod_out.append('y')\n"
            "    sino:\n"
            "        mod_out.append('z')\n"
            "    j = j + 1\n"
            "imprimir(mod_out)\n"
        )
        r = _compile_and_run(src)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            r.stdout,
            "['a', 'b', 'c', 'd', 'd']\n"
            "[10, 20, 30, 30]\n"
            "[1, 'tres', 3]\n"
            "['x', 'y', 'z', 'z']\n",
        )

    def test_shadow_builtin_type_names_and_fnmatch(self) -> None:
        src = (
            "importar fnmatch\n"
            "lista = [1, 2, 3]\n"
            "funcion medir():\n"
            "    texto = 'hola'\n"
            "    devolver longitud(texto)\n"
            "imprimir(longitud(lista))\n"
            "imprimir(medir())\n"
            "imprimir(fnmatch.fnmatch('abc1', 'abc[0-9]'))\n"
            "imprimir(fnmatch.fnmatch('abcx', 'abc[0-9]'))\n"
        )
        r = _compile_and_run(src)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "3\n4\nTrue\nFalse\n")


if __name__ == "__main__":
    unittest.main()
