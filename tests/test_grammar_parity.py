"""GRAMMAR_PARITY_V1: grammar/lexer gaps cerrados por el corpus v4.

Mide tres huecos que el embudo del corpus v4 (stdlib real, 167 archivos)
diagnostico con evidencia linea a linea (docs/parity_corpus_v4_report_2026-10-16):

  G1 NOT_IN_BND_V1  el lexer fundia 'no en'/'no es' aunque lo que siguiera
                    continuara un identificador: `si no enum_class` ->
                    NOT_IN('no en') + NAME('um_class') y rompia cualquier
                    'no <nombre que empiece por en/es>' (enum/netrc/pathlib
                    fallaron en tier A).
  G2 BARE_NL_V1     `devolver`/`lanzar`/`producir` sin expresion al EOF o
                    seguidos de linea en blanco: el lexer cierra con NL y el
                    parser solo aceptaba NEWLINE -> 'Token(NL)' (37
                    PARSE_FAIL en tier A: 20 devolver, 16 lanzar, 1 producir).
  G3 FSTRING_CONVERSION_V1  f'{x!r}'/'{x!a}' fallaban en lower
                    ('conversion must be !s or omitted'; 11 PARSE_FAIL
                    medidos: 10 tier A + inputoutput en tier B). !r/!a se
                    envuelven en repr()/ascii(), que el backend linux C ya
                    emite; el backend x86 (Windows) no tiene handler de
                    repr()/ascii() nativo, asi que esos gates se saltan alla.

Los gates G2/G3 son diferenciales (nativo vs CPython); G1 es de parse mas
una regresion diferencial del fusionado correcto de `no en`.
"""
from __future__ import annotations

import sys
import unittest

from piton.lower import LoweringError, lower_cst_to_hir
from piton.native_differential import compare_native_to_cpython
from piton.parser import parse


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


class NotInBoundaryV1(unittest.TestCase):
    """G1: 'no en'/'no es' solo se fusiona con la palabra EN si 'en'/'es' no
    es el arranque de otro identificador."""

    def test_name_starting_with_en_after_no(self):
        # enum.py:233 (tier A, PARSE_FAIL NOT_IN antes de G1)
        self.assertIsNotNone(parse("si no enum_class ._use_args_ :\n    pasar\n"))

    def test_name_starting_with_en_predicate(self):
        # netrc.py:113 / pathlib.py:197 (tier A, PARSE_FAIL NOT_IN antes de G1)
        self.assertIsNotNone(parse("si no entryname :\n    lanzar x\n"))
        self.assertIsNotNone(parse("si no entry .es_dir ():\n    continuar\n"))

    def test_name_starting_with_es_after_no(self):
        # misma fusion IS_NOT con la cola del nombre
        self.assertIsNotNone(parse("si no estatura_valida :\n    pasar\n"))

    def test_not_in_still_fuses(self):
        self.assertIsNotNone(parse("si 1 no en [1 ,2] :\n    pasar\n"))

    def test_is_not_still_fuses(self):
        self.assertIsNotNone(parse("si 1 no es 2 :\n    pasar\n"))

    def test_not_in_semantics_differential(self):
        _assert_matches(self, "imprimir (1 no en [1 ,2] )\n")


class BareStatementsNlV1(unittest.TestCase):
    """G2: sentencias sin expresion cierran con NL (EOF / linea en blanco)."""

    def test_bare_return_last_line_of_module(self):
        _assert_matches(self, "funcion f ():\n    imprimir ('a')\n    devolver\n")

    def test_bare_return_eof_after_body(self):
        _assert_matches(self, "funcion f ():\n    devolver\n")

    def test_bare_return_then_blank_line(self):
        _assert_matches(self, "funcion f ():\n    devolver\n\nimprimir ('z')\n")

    def test_bare_reraise_last_line_of_handler(self):
        _assert_matches(
            self,
            "intentar :\n    lanzar ValueError (\"x\")\n"
            "excepto :\n    imprimir ('c')\n    lanzar\n",
        )

    def test_bare_reraise_eof(self):
        _assert_matches(
            self,
            "intentar :\n    lanzar ValueError (\"x\")\nexcepto :\n    lanzar\n",
        )

    def test_bare_yield_parses(self):
        # producir desnudo: parse-level (los generadores no forman parte del
        # alcance de G2; su paridad es un gate aparte).
        self.assertIsNotNone(parse("funcion g ():\n    producir\n"))

    def test_return_with_expression_still_parses(self):
        self.assertIsNotNone(parse("funcion f ():\n    devolver 1\n"))


class FstringConversionV1(unittest.TestCase):
    """G3: f'{x!r}'/'{x!a}' envuelven el arg en repr()/ascii()."""

    # El backend x86 (Windows) no emite repr()/ascii() nativos aun; aqui
    # (linux) el backend C si los tiene.
    _linux_only = sys.platform == "win32"

    @unittest.skipIf(_linux_only, "x86 backend lacks native repr()/ascii() handlers")
    def test_r_conversion_int(self):
        _assert_matches(self, "imprimir (f'{1 !r }')\n")

    @unittest.skipIf(_linux_only, "x86 backend lacks native repr()/ascii() handlers")
    def test_r_conversion_str_keeps_quotes(self):
        _assert_matches(self, "imprimir (f'hola {\"x\" !r }')\n")

    @unittest.skipIf(_linux_only, "x86 backend lacks native repr()/ascii() handlers")
    def test_a_conversion_list(self):
        _assert_matches(self, "imprimir (f'{[1 ,2] !a }')\n")

    @unittest.skipIf(_linux_only, "x86 backend lacks native repr()/ascii() handlers")
    def test_r_conversion_with_format_spec(self):
        # sin espacio antes de '}': con el espacio el spec queda '>10 ' y
        # tambien lo rechaza CPython (ValueError) — paridad fail-closed.
        _assert_matches(self, "imprimir (f'{1.5 !r :>10}')\n")

    @unittest.skipIf(_linux_only, "x86 backend lacks native repr()/ascii() handlers")
    def test_r_inside_expression(self):
        _assert_matches(self, "x ={'a' :1 }\nimprimir (f'k={\"a\" !r } v={x [\"a\"] }')\n")

    def test_s_conversion_still_works(self):
        _assert_matches(self, "imprimir (f'{1 !s }')\n")

    def test_no_conversion_still_works(self):
        _assert_matches(self, "imprimir (f'{1 +1 }')\n")

    def test_unknown_conversion_fails_closed(self):
        with self.assertRaises(LoweringError):
            lower_cst_to_hir(parse("imprimir (f'{1 !d }')\n"))


class WalrusStmtV1(unittest.TestCase):
    """WALRUS_STMT_V1: `NAME := expr` sin parentesis en cualquier expr."""

    def test_walrus_in_si_condition(self):
        _assert_matches(self, "n =0\nsi n := 5 :\n    imprimir (n )\n")

    def test_walrus_in_mientras_condition(self):
        _assert_matches(self, "x =0\nmientras x := 0 :\n    imprimir ('no')\nimprimir ('fin')\n")

    def test_walrus_binds_comparison_in_condition(self):
        # `n := 5 > 3` evalua el comparo completo (precedencia de :=)
        _assert_matches(self, "n =0\nsi n := 5 > 3 :\n    imprimir (n )\n")

    def test_walrus_paren_still_parses(self):
        _assert_matches(self, "imprimir ((n := 5) )\n")

    def test_walrus_as_call_argument(self):
        _assert_matches(self, "imprimir ((n := 5) *2 )\n")

    def test_conditional_without_walrus_untouched(self):
        self.assertIsNotNone(parse("si x :\n    pasar\n"))


class SubscriptTupleV1(unittest.TestCase):
    """SUBSCRIPT_TUPLE_V1: `d[k1, k2]` es la tupla implicita d[(k1, k2)]."""

    def test_subscript_tuple_store(self):
        _assert_matches(self, "d ={}\nd [1 ,2 ]=7\nimprimir (d [1 ,2 ])\n")

    def test_subscript_tuple_read(self):
        _assert_matches(self, "d ={}\nd [(1 ,2 ) ]=7\nimprimir (d [1 ,2 ])\n")

    def test_subscript_single_key_untouched(self):
        _assert_matches(self, "d ={}\nd ['a' ]=1\nimprimir (d ['a' ])\n")

    def test_subscript_slice_untouched(self):
        _assert_matches(self, "x = [1 ,2 ,3 ]\nimprimir (x [1 :2 ])\n")


class ForTupleIterV1(unittest.TestCase):
    """FOR_TUPLE_ITER_V1: `para g en a, b :` admite la tupla desnuda."""

    def test_for_bare_tuple_iter(self):
        _assert_matches(self, "a =1\nb =2\npara g en a ,b :\n    imprimir (g )\n")

    def test_for_paren_tuple_iter_untouched(self):
        _assert_matches(self, "para g en (1 ,2 ) :\n    imprimir (g )\n")

    def test_for_single_iter_untouched(self):
        _assert_matches(self, "para g en [1 ,2 ] :\n    imprimir (g )\n")


class StarredTupleV1(unittest.TestCase):
    """STARRED_TUPLE_V1: el `*` abre/compone tuplas paréntesis en parse.

    El unpacking de runtime (`set_target`) todavia no existe en el subconjunto
    nativo: el gate diferencial falla cerrado con NativeBuildError, que es el
    comportamiento honesto (el registro concreto lo marcara el corpus).
    """

    def test_star_led_parses(self):
        self.assertIsNotNone(parse("(*head ,tail )=t\n"))

    def test_star_second_position_parses(self):
        self.assertIsNotNone(parse("(a ,*rest )=t\n"))

    def test_star_in_tuple_value_parses(self):
        self.assertIsNotNone(parse("x = (a ,b ,*c )\n"))

    def test_unpack_fails_closed(self):
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError):
            compare_native_to_cpython("t =(1 ,2 ,3 )\n(*head ,tail )=t\n")


class DictMergeV1(unittest.TestCase):
    """DICT_MERGE_V1: `{**a, **b}` parsea; el merge de runtime no existe y
    MIR lo rechaza fail-closed con mensaje claro (antes el parser moria en
    DOUBLE_STAR)."""

    def test_merge_parses(self):
        self.assertIsNotNone(parse("a = {**x ,**y }\n"))

    def test_mixed_literal_parses(self):
        self.assertIsNotNone(parse("a = {1 :2 ,**x }\n"))

    def test_plain_dict_untouched(self):
        _assert_matches(self, "a = {1 :2 ,3 :4 }\nimprimir (a )\n")

    def test_merge_fails_closed_at_mir(self):
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError) as ctx:
            compare_native_to_cpython("x ={}\ny ={}\nz ={**x ,**y }\n")
        self.assertIn("dict literal merge", str(ctx.exception))


class EllipsisV1(unittest.TestCase):
    """ELLIPSIS_V1: `...` parsea como constante; el backend C no codifica
    Ellipsis aun y falla cerrado con mensaje propio (antes: PARSE_FAIL)."""

    def test_ellipsis_parses(self):
        self.assertIsNotNone(parse("x = type (...)\n"))

    def test_ellipsis_in_call_parses(self):
        self.assertIsNotNone(parse("imprimir (...)\n"))

    def test_backend_fails_closed(self):
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError) as ctx:
            compare_native_to_cpython("imprimir (type (...))\n")
        self.assertIn("Ellipsis", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
