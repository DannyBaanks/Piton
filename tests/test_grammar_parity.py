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


class CallTrailingCommaV1(unittest.TestCase):
    """CALL_TRAILING_COMMA_V1: `f(1, 2,)` — coma final en argumentos de call
    (dominaba la long tail: ~12 archivos del tier A fallaban ahi)."""

    def test_trailing_comma_parses(self):
        self.assertIsNotNone(parse("f (1 ,2 ,)\n"))

    def test_keyword_trailing_comma_parses(self):
        self.assertIsNotNone(parse("f (a =1 ,b =2 ,)\n"))

    def test_trailing_comma_after_star_parses(self):
        self.assertIsNotNone(parse("f (*a ,)\n"))

    def test_missing_comma_still_fails(self):
        with self.assertRaises(Exception):
            parse("f (1 2)\n")

    def test_trailing_comma_differential(self):
        _assert_matches(
            self,
            "funcion add (a ,b ):\n    devolver a +b \n"
            "imprimir (add (1 ,2 ,))\n",
        )


class OneLinerChainV1(unittest.TestCase):
    """ONE_LINER_CHAIN_V1: la suite one-liner cierra su NEWLINE, asi que
    sino_si/sino/excepto/finalmente de la línea siguiente entran al
    chain-loop (antes: caian a expr-stmt — 'sino' se convertia en un
    AnnAssign silenciosamente valido y sino_si/finalmente reventaban)."""

    def test_sino_si_one_liner_parses(self):
        self.assertIsNotNone(parse("si 1 :pasar\nsino_si 2 ==2 :pasar\n"))

    def test_sino_one_liner_parses(self):
        self.assertIsNotNone(parse("si 1 :pasar\nsino :pasar\n"))

    def test_while_sino_one_liner_parses(self):
        self.assertIsNotNone(parse("mientras 1 :pasar\nsino :x =1\n"))

    def test_try_finalmente_one_liner_parses(self):
        self.assertIsNotNone(parse("intentar :pasar\nfinalmente :y [0]+=1\n"))

    def test_try_excepto_one_liner_parses(self):
        self.assertIsNotNone(parse("intentar :pasar\nexcepto ValueError :pasar\n"))

    def test_multiline_chain_untouched(self):
        self.assertIsNotNone(
            parse("si 1 :\n    pasar\nsino_si 2 ==2 :\n    pasar\nsino :\n    pasar\n")
        )

    def test_sino_chain_differential(self):
        # Antes del fix el 'sino' one-liner se parseaba como AnnAssign y el
        # programa nativo no imprimia 'b'; ahora produce la cadena real.
        _assert_matches(
            self,
            "si Falso :imprimir ('a')\n"
            "sino_si 1 ==1 :imprimir ('b')\n"
            "sino :imprimir ('c')\n",
        )

    def test_try_finalmente_differential(self):
        _assert_matches(
            self,
            "intentar :imprimir ('t')\nfinalmente :imprimir ('f')\n",
        )

    def test_try_excepto_differential(self):
        _assert_matches(
            self,
            "intentar :\n    lanzar ValueError (\"x\")\n"
            "excepto ValueError como e :\n    imprimir ('e')\n"
            "finalmente :imprimir ('f')\n",
        )


class StarListV1(unittest.TestCase):
    """STAR_LIST_V1: `[a, *b]` / `[*a]` con Starred, como la tupla."""

    def test_star_first_parses(self):
        self.assertIsNotNone(parse("x =[*a ,b ]\n"))

    def test_star_solo_parses(self):
        self.assertIsNotNone(parse("x =[*a ]\n"))

    def test_star_last_parses(self):
        self.assertIsNotNone(parse("x =[a ,*b ]\n"))

    def test_plain_list_untouched(self):
        self.assertIsNotNone(parse("x =[1 ,2 ,]\n"))

    def test_star_list_fails_closed_at_backend(self):
        # El parse sube, pero el backend C no emite extend para Starred-en-list
        # y falla cerrado con 'runtime operation not supported'.
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError) as ctx:
            compare_native_to_cpython("a =[1 ,2 ]\nimprimir ([*a ,3 ])\n")
        self.assertIn("not supported", str(ctx.exception))


class LambdaStarV1(unittest.TestCase):
    """LAMBDA_STAR_V1: `lambda *args` / `lambda **kw` con los slots
    vararg/kwarg de _parse_arguments."""

    def test_lambda_star_parses(self):
        self.assertIsNotNone(parse("f =lambda *args :args\n"))

    def test_lambda_dstar_parses(self):
        self.assertIsNotNone(parse("f =lambda **kw :kw\n"))

    def test_lambda_plain_untouched(self):
        self.assertIsNotNone(parse("f =lambda x ,y =2 :x\n"))

    def test_lambda_plain_still_differential(self):
        _assert_matches(
            self,
            "f =lambda a ,b :a +b \n"
            "imprimir (f (1 ,2 ))\n",
        )

    def test_lambda_vararg_fails_closed_at_mir(self):
        # LAMBDA_VARARG_FAILCLOSED_V1: sin el guard, el closure se emitia con
        # has_vararg=0 y el runtime daba TypeError divergente (rc2 vs oracle 0).
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError) as ctx:
            compare_native_to_cpython("f =lambda *args :args\nimprimir (f (1 ,2 ))\n")
        self.assertIn("lambda", str(ctx.exception))

    def test_lambda_kwargs_fails_closed_at_mir(self):
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError) as ctx:
            compare_native_to_cpython("f =lambda **kw :kw\nimprimir (f (a =1 ))\n")
        self.assertIn("lambda", str(ctx.exception))


class YieldExprTerminatorV1(unittest.TestCase):
    """YIELD_EXPR_TERMINATOR_V1: yield sin valor dentro de agrupadores —
    `(producir)` termina en RPAREN (los generadores completos siguen fuera
    del alcance de paridad; gates de parse)."""

    def test_yield_in_parens_parses(self):
        self.assertIsNotNone(parse("imprimir ((producir ))\n"))

    def test_lambda_yield_parses(self):
        self.assertIsNotNone(parse("g =type ((lambda :(producir ))())\n"))

    def test_yield_dict_value_parses(self):
        self.assertIsNotNone(parse("d ={'k':producir }\n"))

    def test_yield_value_still_parses(self):
        self.assertIsNotNone(parse("funcion g ():\n    x =producir 1\n"))

    def test_yield_statement_still_parses(self):
        self.assertIsNotNone(parse("funcion g ():\n    producir\n"))


class FstringSpecBracketV1(unittest.TestCase):
    """FSTRING_SPEC_BRACKET_V1: el ':' de un slice dentro de `[...]` de una
    interpolacion no es format-spec (runpy.py:121 cortaba en `mod_name [`
    y el sub-tokenizador reventaba con ENDMARKER)."""

    def test_slice_in_interpolation_parses(self):
        self.assertIsNotNone(parse('x =f"try {mod_name [:-3 ]}"\n'))

    def test_spec_after_conversion_untouched(self):
        self.assertIsNotNone(parse('x =f"{v !r :>10}"\n'))

    def test_colon_in_string_key_parses(self):
        self.assertIsNotNone(parse('x =f"{d [\'a:b\'] }"\n'))

    def test_slice_interpolation_differential(self):
        _assert_matches(
            self,
            "mod_name ='runpy'\n"
            "imprimir (f\"try {mod_name [:-3 ]}\")\n",
        )


class ConstPostfixV1(unittest.TestCase):
    """CONST_POSTFIX_V1: `Verdadero/Falso/Nada/True/False/None` pasan por
    _parse_postfix, no como primary terminal. Resolvio enum.py entero
    (tier A). A nivel runtime el subset todavia no tiene dunder/atributos
    sobre esas constantes (Nada.__class__ -> TypeError nativo vs oracle),
    asi que estos gates son de parse; la divergencia downstream es gap
    documentado, no regresion."""

    def test_none_dunder_parses(self):
        # enum.py:936 (tier A, PARSE_FAIL 'se esperaba DOT' antes del fix)
        self.assertIsNotNone(parse("x =Nada .__new__\n"))

    def test_bool_attr_parses(self):
        self.assertIsNotNone(parse("x =Verdadero .real\n"))

    def test_true_alias_dunder_parses(self):
        self.assertIsNotNone(parse("x =True .__class__\n"))

    def test_none_call_postfix_parses(self):
        self.assertIsNotNone(parse("imprimir (str (Nada .__class__ ))\n"))


class TypeParamsV1(unittest.TestCase):
    """CLASS/FUNC_TYPE_PARAMS_V1: `clase C[T](Base):` y `funcion f[T](x):`
    (PEP 695) consumen balanceados los type params y los descartan; el
    subset nativo no tiene generics (typing.py:2481 clase, typing.py:3024
    funcion). El helper _skip_type_params es compartido por ambos."""

    def test_class_bare_type_params_parses(self):
        self.assertIsNotNone(parse("clase C [T ]:\n    pasar\n"))

    def test_class_bound_type_params_parses(self):
        self.assertIsNotNone(parse("clase C [T :int ](object ):\n    pasar\n"))

    def test_func_type_params_parses(self):
        # typing.py:3048 (funcion reveal_type[T](obj: T, /) -> T)
        self.assertIsNotNone(parse("funcion f [T ](x :T )->T :\n    devolver x\n"))

    def test_func_type_params_annotation_only_parses(self):
        self.assertIsNotNone(parse("funcion f [T ](x :int ):\n    devolver x\n"))

    def test_plain_signature_untouched(self):
        self.assertIsNotNone(parse("funcion f (x ):\n    devolver x\n"))
        self.assertIsNotNone(parse("clase C (object ):\n    pasar\n"))

    def test_generic_class_differential(self):
        _assert_matches(
            self,
            "clase C [T ]:\n"
            "    funcion get (self ):\n"
            "        devolver 1\n"
            "imprimir (C ().get ())\n",
        )

    def test_generic_func_differential(self):
        _assert_matches(
            self,
            "funcion f [T ](x ):\n"
            "    devolver x\n"
            "imprimir (f (3 ))\n",
        )


class AnnotStarArgsV1(unittest.TestCase):
    """ANNOT_STAR_ARGS_V1: los branches `*`/`**` de _parse_arguments
    consumen la anotacion opcional (`*args :int`, `**kwargs :Any`); antes
    dejaban el ':' colgado y _consume(RPAREN) reventaba (typing.py:3048:
    dataclass_transform tiene 9 parametros anotados con ** al final)."""

    def test_dstar_annotation_parses(self):
        self.assertIsNotNone(parse("funcion f (**kwargs :Any ):\n    pasar\n"))

    def test_star_annotation_parses(self):
        self.assertIsNotNone(parse("funcion f (*args :int ):\n    pasar\n"))

    def test_no_annotation_intact(self):
        self.assertIsNotNone(parse("funcion f (*args ):\n    pasar\n"))
        self.assertIsNotNone(parse("funcion f (**kwargs ):\n    pasar\n"))
        self.assertIsNotNone(parse("funcion f (*,x :int =1 ):\n    pasar\n"))

    def test_star_annotation_differential(self):
        _assert_matches(
            self,
            "funcion f (*args :int ):\n"
            "    devolver args\n"
            "imprimir (f (1 ,2 ))\n",
        )

    def test_dstar_annotation_differential(self):
        _assert_matches(
            self,
            "funcion f (**kwargs :int ):\n"
            "    devolver kwargs\n"
            "imprimir (f (a =1 ))\n",
        )


if __name__ == "__main__":
    unittest.main()
