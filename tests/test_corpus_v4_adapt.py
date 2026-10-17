"""CORPUS_V4_ADAPT_V1: fixes del adaptador mecanico Python->PITON.

tools/parity_corpus_v4.py traduce stdlib real a PITON antes del embudo
adapt -> parse -> mir -> build. Cuatro arreglos medidos sobre el corpus
(docs/parity_corpus_v4_report_2026-10-16*.json):

  FSTRING_CONCAT_V1   la concatenacion implicita `f".." f".."` no se
                      convertia en `+`: 3.12 tokeniza FSTRING_* (no STRING)
                      y la condicion vieja no matcheaba -> ~19 PARSE_FAIL
                      (RPAREN) del tier A.
  IMPORT_TRAILING_COMMA_V1
                      al quitar los parentesis de `desde X importar (...)`
                      la coma final quedaba suelta -> NAME (typing, dis,
                      _distutils_system_mod).
  WRAP_HEAD_NO_KW_V1  envolver tras el primer nombre producia
                      `termios(.tcgetattr, ...)` — un call falso — cuando la
                      sentencia no empezaba por palabra clave.
  SEMI_SPLIT_V1       `a=1;b=2` repartia las partes pero conservaba el token
                      `;`, que la gramatica PITON no tiene -> SEMI.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

from piton.parser import parse

_TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "parity_corpus_v4.py"
_spec = importlib.util.spec_from_file_location("parity_corpus_v4", _TOOL)
pcv4 = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(pcv4)


class FstringConcatV1(unittest.TestCase):
    def test_two_fstrings_join_with_plus(self):
        out = pcv4.python_to_piton('x = f"a" f"b"\n')
        self.assertIn('+f"b"', out)
        parse(out)

    def test_fstring_plus_str(self):
        out = pcv4.python_to_piton('x = f"a" "b"\n')
        self.assertIn('+"b"', out)
        parse(out)

    def test_str_plus_fstring(self):
        out = pcv4.python_to_piton('x = "a" f"b"\n')
        parse(out)

    def test_single_fstring_untouched(self):
        out = pcv4.python_to_piton('x = f"a{1!r}"\n')
        parse(out)


class ImportTrailingCommaV1(unittest.TestCase):
    def test_multiline_import_drops_trailing_comma(self):
        out = pcv4.python_to_piton("from typing import (\n    A, B,\n    C,\n)\n")
        self.assertNotIn(",\n", out.replace(" ", ""))
        parse(out)

    def test_single_line_import_untouched(self):
        out = pcv4.python_to_piton("from typing import A, B\n")
        parse(out)


class WrapHeadNoKwV1(unittest.TestCase):
    def test_bare_expression_tuple_wraps_from_column_zero(self):
        out = pcv4.python_to_piton("termios.tcgetattr, termios.tcsetattr\n")
        self.assertTrue(out.startswith("("), out)
        parse(out)

    def test_return_tuple_still_wraps_after_keyword(self):
        out = pcv4.python_to_piton("def f():\n    return 1, 2\n")
        self.assertIn("devolver (1 ,2 )", out)
        parse(out)


class SemiSplitV1(unittest.TestCase):
    def test_semicolon_becomes_two_lines(self):
        out = pcv4.python_to_piton("val = 1; AP = 2\n")
        self.assertNotIn(";", out)
        parse(out)

    def test_no_semicolon_untouched(self):
        out = pcv4.python_to_piton("val = 1\n")
        parse(out)


class SoftMultilineV1(unittest.TestCase):
    def test_case_opening_paren_multiline(self):
        # turtle.py:3817 (tier A): `case (` cierra su `):` tres lineas
        # despues; exigir ':' en la misma linea dejaba `case` sin traducir
        # y el parser PITON lo veia como nombre.
        out = pcv4.python_to_piton(
            "match x:\n"
            "    case (\n"
            "        1 | 2,\n"
            "    ):\n"
            "        pass\n"
        )
        self.assertIn("caso (", out)
        self.assertIn("segun", out)
        parse(out)

    def test_case_colon_same_line_still_translates(self):
        out = pcv4.python_to_piton("match x:\n    case 1:\n        pass\n")
        self.assertIn("caso", out)
        parse(out)

    def test_match_assignment_untouched(self):
        out = pcv4.python_to_piton("match = re.match(pat, s)\n")
        self.assertIn("match =", out)
        parse(out)

    def test_case_assignment_untouched(self):
        out = pcv4.python_to_piton("case = 1\n")
        self.assertNotIn("caso", out)
        parse(out)


if __name__ == "__main__":
    unittest.main()
