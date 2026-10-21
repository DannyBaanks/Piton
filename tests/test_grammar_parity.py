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
    """STARRED_TUPLE_V1: el `*` abre/compone tuplas parentesis en parse.

    El desempaquetado de runtime (`(*head, tail)=t`) ahora corre: MIR emite
    get_slice con flag as_list (STARRED_SLICE_LIST_V1) porque CPython
    devuelve SIEMPRE lista en `*mid` (tupla/str/list de origen); el backend
    convierte el slice a PK_LIST en linux x86.
    """

    def test_star_led_parses(self):
        self.assertIsNotNone(parse("(*head ,tail )=t\n"))

    def test_star_second_position_parses(self):
        self.assertIsNotNone(parse("(a ,*rest )=t\n"))

    def test_star_in_tuple_value_parses(self):
        self.assertIsNotNone(parse("x = (a ,b ,*c )\n"))

    def test_star_led_differential(self):
        # STARRED_SLICE_LIST_V1: `*head` de una tupla devuelve LISTA (el
        # flag as_list convierte el slice; antes fallaba cerrado en build).
        _assert_matches(self, "t =(1 ,2 ,3 )\n(*head ,tail )=t\nimprimir (head ,tail )\n")

    def test_star_on_str_differential(self):
        _assert_matches(self, "a ,*b ='xyz'\nimprimir (a ,b )\n")


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


class MirStdlibGatesV1(unittest.TestCase):
    """MIR_STDLIB_V1: los 5 huecos que dejaron los 13 MIR_FAIL no-modulo del
    corpus v4 (report after-gaps-v3: 152 MIR_FAIL, 13 sin `native module not
    supplied`), atacados en piton/mir.py + linux_x86.py:

      M1 CLASS_DOCSTRING_V1  docstring de clase: el CONST del body llegaba
          al loop `for method in node.body` y MIR lo rechazaba
          (`__hello__.py`: `class TestFrozenUtf8_1: "<doc>"`).
      M2 UNPACK_* (ver UnpackV1/StarredTupleV1)  sub-tupla anidada +
          target LOAD + star con as_list + indice negativo del resto.
      M3 EXCEPT_TUPLE_V1  `excepto (ValueError, TypeError)`; solo se
          aceptaba un nombre por handler (hashlib.py: except (TypeError,
          ValueError): ...).
      M4 EXC_OSError_V1  OSError/IOError/EnvironmentError no estaban en
          _BUILTIN_EXCEPTIONS (nturl2path.py).
      M5 BASE_BUILTIN_V1  `class C(object)` sin definicion previa de object
          (593: base-class-not-defined).

    Edge documentado (fail-closed honesto): target anidado dentro de un
    `para` (unpack de runtime no existe en el camino del for).
    """

    def test_m1_class_docstring_with_method(self):
        _assert_matches(
            self,
            'clase C :\n'
            '    "doc "\n'
            '    funcion f (self ):\n'
            '        devolver 1 \n'
            'obj = C ()\n'
            'imprimir (obj . f () )\n',
        )

    def test_m1_class_docstring_only(self):
        # __hello__.py: docstring como unico statement del body.
        _assert_matches(
            self,
            'clase D :\n'
            '    "solo doc "\n'
            'imprimir ("si " si D () sino "no ")\n',
        )

    def test_m3_except_tuple_hit(self):
        _assert_matches(
            self,
            'intentar :\n'
            '    lanzar ValueError ("a ")\n'
            'excepto (ValueError ,TypeError ):\n'
            '    imprimir ("valor ")\n',
        )

    def test_m3_except_tuple_second_name(self):
        # el segundo nombre del tupla tambien captura (antes solo el
        # primero estaba en el set del handler).
        _assert_matches(
            self,
            'intentar :\n'
            '    lanzar TypeError ("b ")\n'
            'excepto (ValueError ,TypeError ):\n'
            '    imprimir ("tipo ")\n',
        )

    def test_m3_except_tuple_miss_falls_through(self):
        _assert_matches(
            self,
            'intentar :\n'
            '    lanzar KeyError ("c ")\n'
            'excepto (ValueError ,TypeError ):\n'
            '    imprimir ("nunca ")\n'
            'sino :\n'
            '    imprimir ("otro ")\n',
        )

    def test_m4_oserror_builtin(self):
        _assert_matches(
            self,
            'intentar :\n'
            '    lanzar OSError ("falta ")\n'
            'excepto OSError :\n'
            '    imprimir ("capturado ")\n',
        )

    def test_m4_ioerror_alias(self):
        _assert_matches(
            self,
            'intentar :\n'
            '    lanzar IOError ("io ")\n'
            'excepto IOError :\n'
            '    imprimir ("capturado ")\n',
        )

    def test_m5_object_base(self):
        _assert_matches(
            self,
            'clase C (object ):\n'
            '    funcion f (self ):\n'
            '        devolver 7 \n'
            'imprimir (C () . f () )\n',
        )

    def test_edge_for_nested_target_fails_closed(self):
        # documentado: el `para` no tiene unpack de runtime en el backend;
        # el tipo del indice no es seq y el check es fail-closed.
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError):
            compare_native_to_cpython(
                'datos = [(1 ,(2 ,3 ))]\n'
                'para a ,(b ,c ) en datos :\n'
                '    imprimir (a )\n'
            )


class M6SeqDefaultsV1(unittest.TestCase):
    """M6 STATIC_CONST_EVAL_V1 + SEQ_VALUE_V1 + SEQ_PARAM_DEFAULT_V1 +
    STR_PARAM_DEFAULT_V1: los defaults no-const evaluables a literal.

    MIR acepta defaults TUPLE/LIST de consts (incl. vacios: getopt
    `output=[]`, warnings `warn(..., ())`); el backend los codifica como
    IIFE piton_seq_new (op const y _value), anota el tipo del param en el
    callee y la inferencia de return types. Name/Attribute siguen
    fail-closed (el binding de modulo no es const en definicion: quopri
    `output=output`, warnings `Warning`).

    Edge documentado: un argumento escalar explicito en un param con
    default collection/str es fail-closed en build (CPython lo admitiria,
    el subset no puede re-anotar el callee por llamada).
    """

    def test_list_empty_default(self):
        _assert_matches(self, "funcion f (x =[]) :\n    devolver 0 \nimprimir (f () )\n")

    def test_tuple_empty_default(self):
        _assert_matches(self, "funcion f (x =()) :\n    devolver 1 \nimprimir (f () )\n")

    def test_tuple_const_default_roundtrip(self):
        _assert_matches(
            self,
            "funcion f (a =1 ,b =(2 ,3 )) :\n"
            "    devolver (a ,b )\n"
            "imprimir (f () )\n",
        )

    def test_list_default_roundtrip(self):
        _assert_matches(self, "funcion f (a =[1 ,2 ]) :\n    devolver a \nimprimir (f () )\n")

    def test_mixed_const_list_default(self):
        _assert_matches(
            self,
            'funcion f (a =["x ",1 ,Verdadero ,Nada ]) :\n'
            "    devolver a \n"
            "imprimir (f () )\n",
        )

    def test_kwonly_tuple_default(self):
        _assert_matches(self, "funcion f (*,b =(5 ,6 )) :\n    devolver b \nimprimir (f () )\n")

    def test_str_default_roundtrip(self):
        # STR_PARAM_DEFAULT_V1: preexistente — `devolver (a, b)` con default
        # str imprimia el puntero del param como int.
        _assert_matches(
            self,
            'funcion f (a =1 ,b ="x ") :\n'
            "    devolver (a ,b )\n"
            "imprimir (f () )\n"
            'imprimir (f (2 ,"y "))\n',
        )

    def test_collection_arg_override(self):
        _assert_matches(self, "funcion f (a =[1 ,2 ]) :\n    devolver a \nimprimir (f ([9 ]))\n")

    def test_scalar_arg_to_collection_default_fails_closed(self):
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError):
            compare_native_to_cpython("funcion f (a =[1 ]) :\n    devolver 3 \nimprimir (f (7 ))\n")

    def test_int_arg_to_str_default_fails_closed(self):
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError):
            compare_native_to_cpython('funcion f (b ="x ") :\n    devolver b \nimprimir (f (7 ))\n')


class DownstreamGatesV1(unittest.TestCase):
    """DOWNSTREAM_V1: familias que dejaron los MIR_FAIL no-modulo tras
    mir-v1/m6 (report after-m6), atacadas en mir.py + linux_x86.py:

      ITER_NEXT_ATTR_V1  `it.__next__` como bound method (heapq:468
          `next = it.__next__` + `next()` posterior). Edge documentado:
          sobre enumerate/zip/map el retorno tupla no lleva anotacion a
          traves del bound call y se imprimiria como puntero.
      NEXT_SHADOW_V1  `next()` con cero args baja como bound-method call
          (antes: el branch builtin next rechazaba la aridad 0 y heapq
          moria con 'requires one iterator').
      SPLIT_MAXSPLIT_KW_V1  `s.split(sep, maxsplit=n)` expande el kwarg a
          posicional (nturl2path:70); unico metodo builtin con firma kw
          soportada fuera de dict.update.
      STR_SPLIT_NOMATCH_FIX  bug preexistente: el scan de piton_str_split
          perdia hasta nl-1 chars del ultimo segmento cuando el sep no
          aparecia ('a :b :c '.split(': ') devolia 'a :b :c').
      SEQ_ELEM_REPR_V1  bug preexistente: los elems str de list/dict/set/tuple
          se escribian raw (print(["a\\\\b"]) divergiaba); ahora usan el repr
          completo de CPython; el print top-level queda intacto.
    """

    def test_iter_next_attr_bound(self):
        _assert_matches(
            self,
            "it = iter ([1 ,2 ,3 ])\n"
            "nxt = it . __next__\n"
            "imprimir (nxt () ,nxt () ,nxt () )\n",
        )

    def test_iter_next_attr_exhaustion(self):
        _assert_matches(
            self,
            "it = iter ([1 ])\n"
            "nxt = it . __next__\n"
            "imprimir (nxt () )\n"
            "intentar :\n"
            "    nxt ()\n"
            "excepto StopIteration :\n"
            '    imprimir ("fin ")\n',
        )

    def test_next_shadow_zero_args(self):
        # heap-like: el nombre local next sombra al builtin; 0 args no
        # puede ser el builtin next (requiere >=1).
        _assert_matches(
            self,
            "it = iter ([7 ])\n"
            "next = it . __next__\n"
            "imprimir (next () )\n",
        )

    def test_split_maxsplit_kw(self):
        _assert_matches(self, 'p = "a :b :c "\nimprimir (p . split (": ",maxsplit =1 ))\n')

    def test_split_maxsplit_zero(self):
        _assert_matches(self, 'p = "a :b "\nimprimir (p . split (": ",maxsplit =0 ))\n')

    def test_split_no_match_keeps_tail(self):
        # STR_SPLIT_NOMATCH_FIX: preexistente — sin match el segmento es la
        # cadena COMPLETA (antes perdia el trailing space).
        _assert_matches(self, 'p = "a :b :c "\nimprimir (p . split (": "))\n')

    def test_split_plain_untouched(self):
        _assert_matches(self, 'imprimir ("a ,b ,c " . split (","))\n')

    def test_seq_elem_backslash_repr(self):
        # SEQ_ELEM_REPR_V1: preexistente — elems con repr escapado.
        _assert_matches(self, 'imprimir (["a\\\\b "])\n')
        _assert_matches(self, 'imprimir ({"k\\\\a ":1 })\n')
        _assert_matches(self, 'imprimir ({"x\\\\y "})\n')
        _assert_matches(self, 'imprimir ([["a\\\\b "]])\n')

    def test_seq_elem_quote_repr(self):
        # repr elige comillas dobles cuando el texto tiene ' (regla CPython).
        _assert_matches(self, 'imprimir (["el \'cero\' "])\n')

    def test_str_top_level_untouched(self):
        _assert_matches(self, 'imprimir ("a\\\\b ")\n')


class CorpusFinishParseV1(unittest.TestCase):
    """CORPUS_FINISH_PARSE_V1: los 3 PARSE_FAIL del corpus v4 (report
    after-downstream-v1), atacados en lexer.py + parser.py:

      FSTRING_QUOTE_REUSE_V1 (PEP 701, CPython 3.12): dentro del
      replacement field la quote del f-string NO cierra el token; los
      strings internos se escanean como bloques con brace-matching
      (turtle:3820 f'({', '.join(call_args)})', traceback:681 con
      '__notes__' en la misma quote).
      NO_VAR_V1: la variable `no` de pydoc:283 (`no = []`, :284
      `no.append(x)`, :285 `devolver (yes, no)`) — legal en Python, keyword
      en PITON. El parser la trata como nombre cuando el token siguiente
      no puede iniciar una expresion (= . ) ] } , : ; == != < <= > >=
      NEWLINE/DEDENT/EOF).
      ONE_IF_ASSIGN_V1 (pydoc:1812): el adaptador reescribe el one-liner
      `if c: a, b = v1, v2` como `(si c :a ,b )=(v1 ,v2 )`; se desugara a
      un IfStmt real con la asignacion en el body.
    """

    def test_fstring_quote_reuse_join(self):
        # turtle:3820 — quote simple dentro de f'...' con {expr}.
        _assert_matches(
            self,
            "call_args =['a ','b ']\n"
            "call_text =f'({', '.join (call_args )})'\n"
            "imprimir (call_text )\n",
        )

    def test_fstring_quote_reuse_args(self):
        # traceback:681 — string con la misma quote como argumento del expr.
        _assert_matches(
            self,
            "e =1 \n"
            "m =f'Ignored error getting __notes__: {'__notes__'} {e }'\n"
            "imprimir (m )\n",
        )

    def test_fstring_plain_and_escapes_untouched(self):
        _assert_matches(self, "x =2 \nimprimir (f'val {x }',f'{{lit}} {x }')\n")

    def test_pydoc_no_variable(self):
        _assert_matches(
            self,
            "s =[1 ,2 ]\n"
            "no =[] \n"
            "si 1 en s :\n"
            "    pasar \n"
            "sino :\n"
            "    no .append (9 )\n"
            "imprimir (no )\n",
        )

    def test_pydoc_no_return_tuple(self):
        # pydoc:285 `return yes, no` adaptado.
        _assert_matches(self, "no =4 \nimprimir ((1 ,no ))\n")

    def test_no_operator_untouched(self):
        _assert_matches(self, "imprimir (no Verdadero ,no (1 ==2 ),no no 1 )\n")

    def test_no_in_fusion_untouched(self):
        _assert_matches(self, "imprimir (1 no en [2 ],'a ' no es 2 )\n")

    def test_one_if_assign_parses(self):
        # forma exclusiva del adaptador (tier A compila, no ejecuta).
        self.assertIsNotNone(parse("(si 1 :x )=(2 )\n"))
        self.assertIsNotNone(parse("x ,y =0 ,0 \n(si 1 :x ,y )=(2 ,3 )\n"))

    def test_one_liner_tuple_assign_differential(self):
        # la semantica desugared, via el one-liner nativo de PITON.
        _assert_matches(self, "x ,y =0 ,0 \nsi 1 :x ,y =2 ,3 \nimprimir ((x ,y ))\n")

    def test_one_liner_pydoc_shape_differential(self):
        _assert_matches(
            self,
            "n =2 \nmodule ,m =0 ,0 \nsi n >1 :module ,m =7 ,n +1 \nimprimir ((module ,m ))\n",
        )


class BuildFailGatesV1(unittest.TestCase):
    """BUILD_FAIL_V1: las familias downstream de build que dejaron los 9
    BUILD_FAIL del corpus v4 (report after-corpus-finish-v1), atacadas en
    linux_x86.py + mir.py:

      SPLIT_ELEM_STR_V1  str.split anota elems str (comp[0].upper() de
          nturl2path:34 moria con 'method receiver class is not statically
          known'; splitlines/rsplit ya lo hacian).
      MODULE_GLOBAL_READ_V1  leer un nombre de modulo dentro de una funcion
          sin `global` (opcode/stat: constantes) — CPython lo permite; el
          store sin declarar sigue siendo local (shadowing intacto) y se
          promueven a static los nombres leidos.
      DEL_FN_SCOPE_V1  `del def_op` al final de opcode.py:282 no mata los
          loads de funciones llamadas antes del del; el NameError estatico
          aplica al flujo lineal de <module> y a deletes en la misma fn.
      REPR_RUNTIME_V1 + SLOT_REPR_FWD  los metodos __repr__ usan
          piton_slot_repr del runtime que vive en el string C de bigint;
          sin bigint (__future__.py: solo clases) el ld fallaba con
          implicit/never defined. __future__.py ahora BUILD OK completo.
      FUNC_IMPORT_ALIAS_V1  `importar sys` dentro de una funcion (getopt:
          main) registra el alias para el patron sys.argv -> op sys_argv.

    Edge documentado (familia A, tipado estatico insuficiente): method
    calls sobre params/attrs/call-results sin tipo (nturl2path .replace,
    colorsys min/max key, sitecustomize import no provisto, token
    globals().items()); return-inference de comparisons imprime 1 vs True.
    """

    def test_split_elem_attr(self):
        _assert_matches(self, 'p ="a:b "\ncomp =p .split (":")\nimprimir (comp [0 ].upper () )\n')

    def test_split_elem_index(self):
        _assert_matches(self, 'p ="a:b "\ncomp =p .split (":")\nimprimir (comp [0 ][-1 ])\n')

    def test_split_maxsplit_elem(self):
        _assert_matches(self, 'p ="a:b:c "\ncomp =p .split (":",1 )\nimprimir (comp [1 ].upper () )\n')

    def test_read_module_const_in_fn(self):
        _assert_matches(self, "S_IFDIR =0o040000 \nfuncion f ():\n    devolver S_IFDIR \nimprimir (f () )\n")

    def test_read_module_globals_expr(self):
        _assert_matches(self, "A =10 \nB =20 \nfuncion f ():\n    devolver A +B \nimprimir (f () )\n")

    def test_read_module_reassigned_between_calls(self):
        _assert_matches(self, "x =1 \nfuncion f ():\n    devolver x \nimprimir (f () )\nx =2 \nimprimir (f () )\n")

    def test_del_module_cleanup_fn_loads_alive(self):
        # opcode.py:282 — del de limpieza al final; las funciones viven.
        _assert_matches(self, 'funcion def_op (n ,o ):\n    pasar \ndef_op ("a ",1 )\nborrar def_op \n')

    def test_del_then_module_load_still_fails_closed(self):
        # el flujo lineal de <module> sigue modelando el NameError.
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError):
            compare_native_to_cpython("x =1 \nborrar x \nimprimir (x )\n")

    def test_repr_method_without_bigint(self):
        # REPR_RUNTIME_V1: antes ni compilaba (ld: piton_slot_repr
        # implicit/never defined). El __repr__ de un int funciona; el de
        # attr str diverge (familia A, documentado).
        _assert_matches(
            self,
            "clase P :\n"
            "    funcion __init__ (self ,v ):\n"
            "        self .v =v \n"
            "    funcion __repr__ (self ):\n"
            "        devolver repr (self .v )\n"
            "p =P (42 )\n"
            "imprimir (p )\n",
        )

    def test_func_import_sys_argv(self):
        _assert_matches(
            self,
            "funcion main ():\n"
            "    importar sys \n"
            "    devolver longitud (sys .argv )\n"
            "imprimir (main ()>=0 )\n",
        )

    def test_func_import_slice_argv(self):
        _assert_matches(
            self,
            "funcion main ():\n"
            "    importar sys \n"
            "    args =sys .argv [1: ]\n"
            "    devolver longitud (args )\n"
            "imprimir (main ()>=0 )\n",
        )


class ParamStrGatesV1(unittest.TestCase):
    """PARAM_STR_V1: tipado estatico extendido para stdlib real — 4 fixes
    de la familia A (report after-build-fail-v1):

      SHARED_COLL_ELEMS_V1  los globals promovidos a static heredan el elem
          type en el store (opcode: `para oplist en oplists` con .append
          sobre el for-target; antes 'method receiver not statically
          known').
      FOR_EXIT_CLEAR_V1  (bug preexistente GRAVE): el exit del for via
          iteradores dejaba el exc_flag del StopIteration de agotamiento;
          en <module> nadie lo revisa, pero el caller de una funcion si —
          TODO `para x en xs` dentro de una funcion moria con rc 1 y un
          'StopIteration' fantasma al volver. El end_block limpia el flag
          (solo recibe agotamiento y breaks).
      USAGE_PARAM_TYPE_V1  si TODOS los usos de un param como receiver son
          metodos exclusivos de str (replace/startswith/...), se tipa str
          por uso; el call-site rechaza argumentos de tipo conocido
          contradictorio (float/list/dict...) fail-closed (nturl2path
          url.replace, getopt args.startswith).
      MINMAX_VARIADIC_V1  min/max de N>=3 escalares homogeneos (colorsys:
          max(r, g, b) floats) — distinguibles del key= porque alli
          values[0] es list/tuple; mezclas heterogeneas fallan cerradas.
    """

    def test_shared_coll_elems_for_target_append(self):
        _assert_matches(
            self,
            'oplists =[[1 ],[2 ]]\nfuncion ps ():\n    para oplist en oplists :\n        oplist .append (9 )\n    devolver longitud (oplists [0 ])\nimprimir (ps () )\n',
        )

    def test_for_in_function_plain(self):
        # FOR_EXIT_CLEAR_V1: antes rc 1 con 'StopIteration' fantasma.
        _assert_matches(
            self,
            'funcion f ():\n    xs =[1 ,2 ]\n    total =0 \n    para x en xs :\n        total =total +x \n    devolver total \nimprimir (f () )\n',
        )

    def test_for_in_function_promoted_global(self):
        _assert_matches(
            self,
            'xs =[1 ,2 ]\nfuncion f ():\n    total =0 \n    para x en xs :\n        total =total +x \n    devolver total \nimprimir (f () )\n',
        )

    def test_for_in_function_break(self):
        _assert_matches(
            self,
            'xs =[1 ,2 ,3 ]\nfuncion f ():\n    para x en xs :\n        si x ==2 :\n            romper \n    devolver x \nimprimir (f () )\n',
        )

    def test_for_module_untouched(self):
        _assert_matches(
            self,
            'xs =[1 ,2 ]\ntotal =0 \npara x en xs :\n    total =total +x \nimprimir (total )\n',
        )

    def test_usage_param_str_replace(self):
        _assert_matches(
            self,
            'funcion limpiar (url ):\n    devolver url .replace (":","|")\nimprimir (limpiar ("a:b ") )\n',
        )

    def test_usage_param_str_multi(self):
        _assert_matches(
            self,
            'funcion f (s ):\n    devolver s .upper () +s .strip ()\nimprimir (f (" ab ") )\n',
        )

    def test_usage_param_contradiction_fails_closed(self):
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError):
            compare_native_to_cpython('funcion f (s ):\n    devolver s .upper ()\nimprimir (f (1.5 ))\n')

    def test_usage_param_list_methods_untouched(self):
        # MIR-REACH-V1 (USAGE_LIST_SEED_V1): ANTES este gate documentaba el
        # fail-closed de `xs.append(1)` sobre un param sin tipo (familia A).
        # Ahora el mutador exclusivo de list siembra "list" y el programa
        # corre igual que en CPython.
        _assert_matches(self, 'funcion f (xs ):\n    xs .append (1 )\n    devolver 0 \nimprimir (f ([0 ]))\n')

    def test_minmax_varadic_ints(self):
        _assert_matches(self, "imprimir (min (4 ,2 ,7 ,1 ),max (4 ,2 ,7 ,1 ))\n")

    def test_minmax_varadic_floats(self):
        _assert_matches(self, "imprimir (min (0.5 ,0.2 ,0.9 ),max (1.5 ,2.5 ,0.5 ))\n")

    def test_minmax_varadic_strs(self):
        _assert_matches(self, 'imprimir (max ("a ","c ","b "),min ("b ","a ","c "))\n')

    def test_minmax_varadic_mixed_fails_closed(self):
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError):
            compare_native_to_cpython("imprimir (min (1 ,2.5 ,3 ))\n")

    def test_minmax_two_args_untouched(self):
        _assert_matches(self, "imprimir (min (2 ,5 ),min ([3 ,1 ]))\n")


class MirReachGatesV1(unittest.TestCase):
    """MIR-REACH-V1: cerrar MIR_FAIL alcanzables del corpus v4 (report
    after-param-str-v1: 9 alcanzables de 151 MIR_FAIL).

      FUNC_SIG_PRESCAN_V1  firmas de funciones de modulo registradas ANTES
          de bajar cuerpos (bisect: insort_right, def linea 4, llama con
          key= a bisect_right, def linea 21 — el registro tardio reventaba
          el keyword call forward).
      USAGE_LIST_SEED_V1   mutadores exclusivos de list (append/extend/
          insert/sort/reverse) siembran "list" en el param (bisect:
          a.insert); str/dict/set/float args conocidos rechazados.
      SEQ_USAGE_SEED_V1    len() + get_item sobre el param sin metodo
          contradictorio siembra "list" (bisect_right: len(a), a[mid]).

    Alcance declarado: bisect pipeline completo. quopri/warnings (defaults
    Name/Attribute), heapq (min/max default=), hashlib (dynamic call+kw),
    copyreg/types (unpack dinamico), _sitebuiltins (SystemExit), abc
    (super-MRO) siguen fail-closed — familias mas profundas.
    """

    def test_forward_keyword_call_resolves(self):
        # FUNC_SIG_PRESCAN_V1: keyword call a funcion definida DESPUES.
        _assert_matches(
            self,
            "funcion f ():\n    devolver g (1 ,b =2 )\n"
            "funcion g (a ,b ):\n    devolver a + b \n"
            "imprimir (f ())\n",
        )

    def test_forward_keyword_default_fill(self):
        # FUNC_SIG_PRESCAN_V1: el keyword call forward rellena el default
        # estatico de un param no provisto (g(b=6) -> a=1).
        _assert_matches(
            self,
            "funcion f ():\n    devolver g (b =6 )\n"
            "funcion g (a =1 ,b =2 ):\n    devolver a + b \n"
            "imprimir (f ())\n",
        )

    def test_usage_param_list_append(self):
        # USAGE_LIST_SEED_V1: append en param siembra list (bisect insert).
        _assert_matches(
            self,
            "funcion f (xs ):\n    xs .append (1 )\n    devolver longitud (xs )\n"
            "imprimir (f ([0 ]))\n",
        )

    def test_usage_param_list_insert(self):
        # USAGE_LIST_SEED_V1: insert devuelve None y muta in-place.
        _assert_matches(
            self,
            "funcion f (xs ):\n    xs .insert (0 ,9 )\n    devolver xs \n"
            "imprimir (f ([1 ,2 ]))\n",
        )

    def test_usage_param_list_contradiction_fails_closed(self):
        # USAGE_LIST_SEED_V1: arg str conocido a param list → rechazado.
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError):
            compare_native_to_cpython(
                "funcion f (xs ):\n    xs .append (1 )\n    devolver longitud (xs )\n"
                "imprimir (f ('ab' ))\n"
            )

    def test_seq_usage_len_index(self):
        # SEQ_USAGE_SEED_V1: len+get_item sin method_call siembra list
        # (bisect_right: len(a), a[mid]).
        _assert_matches(
            self,
            "funcion f (xs ,i ):\n    devolver xs [i ]\n"
            "imprimir (f ([7 ,8 ,9 ] ,1 ))\n",
        )

    def test_seq_usage_len_index_str_contradiction(self):
        # SEQ_USAGE_SEED_V1: str arg conocido a param seq (len+index) →
        # rechazado. El callee necesita AMBAS operaciones para el seed.
        from piton.x86 import NativeBuildError
        with self.assertRaises(NativeBuildError):
            compare_native_to_cpython(
                "funcion f (xs ,i ):\n    devolver longitud (xs )+ xs [i ]\n"
                "imprimir (f ('ab' ,0 ))\n"
            )

    def test_bisect_real_compiles(self):
        # El archivo real del corpus completa el pipeline (MIR_FAIL ->
        # BUILD_OK; el unico archivo stdlib ganado por esta tanda).
        import importlib.util as _ilu
        import pathlib as _pl
        from piton.linux_x86 import compile_native_linux as _c
        _spec = _ilu.spec_from_file_location("pcv4", "tools/parity_corpus_v4.py")
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _src = _mod.python_to_piton(_pl.Path("/usr/lib/python3.12/bisect.py").read_text())
        _c(_src, _pl.Path("/tmp/opencode/gate_bisect.exe"))
        self.assertTrue(True)


class GetoptGatesV1(unittest.TestCase):
    """GETOPT-V1: cerrar el BUILD_FAIL de getopt.py (parametros de tipo
    secuencia deducidos por uso + reconciliados en el call-site).

      SEQ_SLICE_SEED_V1   un param con indice+slice (y sin metodo str) se
          tipa list; el seed por slice es AMBIGUO (slice es legal en str y
          en list) y el call-site lo reconcilia con el tipo del argumento.
      ELEM_KIND_V1        el tipo del elemento se deduce del uso de
          `param[i]` (metodo str, directo o a traves de un store).
      CALL_ARG_TYPE_V1    el tipo del param deducido del call-site (el
          argumento conocido) se aplica a params usados como secuencia.
      STR_ARG_EVIDENCE_V1 un param pasado como argumento de un metodo str
          es un str.
      GETTEXT_IDENTITY_V1 `from gettext import gettext as X` es la
          identidad -> su resultado conserva la plantilla del `%`.
      STR_OFFSET_PREFIX_V1  s.startswith(p, start) / s.endswith(p, start).
      UNBOUND_BASE_CALL_V1   `Exception.__init__(self, ...)` es desbound:
          el self ya viene en los argumentos.
    """

    def test_seq_slice_seed_and_reconcile(self):
        # el slice del param es legal; el elemento se deduce str.
        _assert_matches(
            self,
            "funcion f (xs ):\n    cola = xs [1 :]\n    imprimir (cola )\n"
            "f (['a' ,'b' ])\n",
        )

    def test_call_arg_type_reconciles_ambiguous_seed(self):
        # el param se indexa y se slicea (seed ambiguo) pero el call-site
        # lo pasa como str -> el param es str y s[0] es un caracter.
        # NOTA: el tipo del call-site llega al callee cuando el callee esta
        # DEFINIDO DESPUES (llamada hacia adelante) — el patron de getopt.
        _assert_matches(
            self,
            "funcion f (xs ):\n    devolver g (xs )\n"
            "funcion g (s ):\n    devolver s [0 ]\n"
            "imprimir (f ('hola' ) )\n",
        )

    def test_str_arg_evidence_is_str(self):
        # un param usado como argumento de startswith es str.
        _assert_matches(
            self,
            "funcion f (s ):\n    devolver s .upper ()\n"
            "imprimir (f ('abc' ) )\n",
        )

    def test_getopt_real_compiles(self):
        # el archivo real del corpus completa el pipeline.
        import importlib.util as _ilu
        import pathlib as _pl
        from piton.linux_x86 import compile_native_linux as _c
        _spec = _ilu.spec_from_file_location("pcv4g", "tools/parity_corpus_v4.py")
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _src = _mod.python_to_piton(_pl.Path("/usr/lib/python3.12/getopt.py").read_text())
        _c(_src, _pl.Path("/tmp/opencode/gate_getopt.exe"))
        self.assertTrue(True)

    def test_str_startswith_with_offset(self):
        # STR_OFFSET_PREFIX_V1: s.startswith(p, start) — la comparacion es
        # sobre s[start:].
        _assert_matches(self, "imprimir ('abcd' .startswith ('bc' ,1 ))\n")
        _assert_matches(self, "imprimir ('abcd' .endswith ('bc' ,1 ))\n")


class StdlibV2GatesV1(unittest.TestCase):
    """STDLIB-V2: capacidades nuevas para cerrar los alcanzables de la
    stdlib (systemexit, defaults no constantes, dict(genexpr), min/max
    default=, sort(reverse=), generadores grandes).

      EXC_BUILTIN_FULL_V1  el resto de excepciones builtin de CPython
          (SystemExit, KeyboardInterrupt, ...) se pueden elevar.
      NON_CONST_DEFAULT_V1 un default que es un NOMBRE se materializa en el
          call-site (variable de la closure, clase o excepcion builtin).
      DICT_FROM_PAIRS_V1   dict(genexpr de pares) arma el dict en runtime.
      MINMAX_DEFAULT_V1    min/max con `default=` (y `key=`).
      SORT_REVERSE_KW_V1   lista.sort(reverse=True).
      GEN_SLOTS_V1         el tope de slots persistidos de un generador.
      PRESCAN_CLASS_V1    el pre-scan de firmas corre antes de bajar los
          cuerpos de las CLASES (un metodo puede llamar a una funcion de
          modulo definida despues).
    """

    def test_systemexit_is_raisable(self):
        # EXC_BUILTIN_FULL_V1: SystemExit es una excepcion builtin.
        _assert_matches(
            self,
            "intentar :\n    lanzar SystemExit (3 )\n"
            "excepto SystemExit :\n    imprimir ('caught' )\n",
        )

    def test_non_const_default_closure(self):
        # NON_CONST_DEFAULT_V1: el default referencia una variable de la
        # funcion que envuelve a la closure.
        _assert_matches(
            self,
            "funcion g (a ):\n"
            "    funcion h (b =a ):\n"
            "        devolver b\n"
            "    devolver h ()\n"
            "imprimir (g (7 ))\n",
        )

    def test_dict_from_genexpr(self):
        # DICT_FROM_PAIRS_V1: dict() desde un generador de pares.
        _assert_matches(
            self,
            "d = diccionario ((k ,v ) para k ,v en [('a' ,1 )] )\n"
            "imprimir (d ['a' ])\n",
        )

    def test_minmax_default(self):
        # MINMAX_DEFAULT_V1: default= (y el camino de clave).
        _assert_matches(self, "imprimir (min ([] , default =5 ))\n")
        _assert_matches(self, "imprimir (min (['b' ,'a' ] , default ='z' ))\n")
        _assert_matches(self, "imprimir (max ([3 ,1 ,2 ] , default =0 ))\n")

    def test_sort_reverse(self):
        # SORT_REVERSE_KW_V1: lista.sort(reverse=True).
        _assert_matches(
            self,
            "x = [3 ,1 ,2 ]\nx .sort (reverse =Verdadero )\nimprimir (x )\n",
        )

    def test_class_method_calls_later_module_function(self):
        # PRESCAN_CLASS_V1: un metodo de clase llama a una funcion de
        # modulo DEFINIDA DESPUES.
        _assert_matches(
            self,
            "clase C :\n"
            "    funcion usar (self ):\n"
            "        devolver tarde ()\n"
            "funcion tarde ():\n"
            "    devolver 5\n"
            "imprimir (C () .usar ())\n",
        )

    def test_compat_pickle_real_compiles(self):
        # El archivo real del corpus completa el pipeline.
        import importlib.util as _ilu
        import pathlib as _pl
        from piton.linux_x86 import compile_native_linux as _c
        _spec = _ilu.spec_from_file_location("pcv4c", "tools/parity_corpus_v4.py")
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _src = _mod.python_to_piton(_pl.Path("/usr/lib/python3.12/_compat_pickle.py").read_text())
        _c(_src, _pl.Path("/tmp/opencode/gate_compat_pickle.exe"))
        self.assertTrue(True)


class DynamicCallGatesV1(unittest.TestCase):
    """DYNAMIC_CALL_V1: llamar a un callable que NO se conoce en build time
    con los argumentos y el dict de keywords resueltos en RUNTIME.

      - `f(*args)` / `f(*args, **kw)` con `f` dinamico.
      - `f(k=v)` con `f` dinamico (los keywords viajan como dict).
      - `recv.attr(*args, **kw)` con el receiver dinamico (el callee es el
        valor del atributo, resuelto en runtime).
      - el frame lo arma el runtime segun el layout del callee (params
        fijos, tupla del *args, dict del **kwargs).
      - DEL_ORDER_V1: un `borrar` solo mata los loads posteriores a el en el
        MISMO bloque (el orden entre bloques no es modelable).
    """

    def test_dynamic_call_with_star_args(self):
        # el callee es una variable: los args se arman en runtime.
        _assert_matches(
            self,
            "funcion aplicar (f ,args ):\n"
            "    devolver f (*args )\n"
            "funcion doble (x ):\n"
            "    devolver x *2\n"
            "imprimir (aplicar (doble ,(3 ,) ))\n",
        )

    def test_dynamic_call_with_kwargs(self):
        # los keywords viajan como dict al runtime; el callee debe aceptar
        # **kwargs (el runtime lo coloca en su slot).
        _assert_matches(
            self,
            "funcion externa ():\n"
            "    funcion suma (**kw ):\n"
            "        devolver kw ['a'] + kw ['b']\n"
            "    funcion aplicar (f ,**kw ):\n"
            "        devolver f (**kw )\n"
            "    devolver aplicar (suma ,a =1 ,b =2 )\n"
            "imprimir (externa ())\n",
        )

    def test_dynamic_method_call_compiles(self):
        # el callee es el valor del atributo del receiver, resuelto en
        # runtime: el archivo COMPILA (el receptor de una clase de PITON no
        # expone sus metodos como atributo en runtime — limite documentado).
        import importlib.util as _ilu
        import pathlib as _pl
        from piton.native_differential import compare_native_to_cpython as _cmp
        _ = _cmp
        from piton.linux_x86 import compile_native_linux as _c
        _c(
            "funcion usar (obj ):\n"
            "    devolver obj .texto ()\n"
            "clase P :\n"
            "    funcion texto (self ):\n"
            "        devolver 'hola'\n"
            "imprimir (usar (P () ))\n",
            _pl.Path("/tmp/opencode/gate_dynamic_method.exe"),
        )
        self.assertTrue(True)


class RaiseValueGatesV1(unittest.TestCase):
    """RAISE_VALUE_V1: `raise <instancia>` — se levanta el VALOR (no una
    clase): el runtime deriva el tipo del class_name del objeto y lo lleva
    al handler (warnings: `raise message` con un Warning instanciado)."""

    def test_raise_instance_is_caught(self):
        _assert_matches(
            self,
            "clase W (Exception ):\n"
            "    pasar\n"
            "intentar :\n"
            "    m = W ()\n"
            "    lanzar m\n"
            "excepto W :\n"
            "    imprimir ('caught' )\n",
        )


class SuperBaseGatesV1(unittest.TestCase):
    """SUPER_BUILTIN_BASE_V1 / METACLASS_KEYWORD_V1: `super().__init__()`
    hacia una base BUILTIN (abc: classmethod) es un no-op semantico (en
    CPython resuelve contra type.__init__, sin efecto observable), y el
    keyword `metaclass=` de la definicion de clase se acepta (los metodos
    propios del metaclass fallan cerrados al invocarse)."""

    def test_super_init_to_builtin_base_is_noop(self):
        _assert_matches(
            self,
            "clase A (classmethod ):\n"
            "    funcion __init__ (cls ,f ):\n"
            "        super ().__init__ (cls ,f )\n"
            "imprimir ('ok' )\n",
        )

    def test_class_with_metaclass_keyword(self):
        # METACLASS_KEYWORD_V1: el keyword `metaclass=` se acepta y el
        # metaclass NO se aplica (limite documentado: en CPython el
        # metaclass se llama como fabrica de la clase — aqui la clase es
        # plana). El gate afirma el comportamiento del backend, no la
        # paridad con CPython (que ademas falla con este metaclass).
        import importlib.util as _ilu
        import pathlib as _pl
        from piton.linux_x86 import compile_native_linux as _c
        import subprocess
        _out = _pl.Path("/tmp/opencode/gate_metaclass.exe")
        _c(
            "clase M :\n"
            "    pasar\n"
            "clase A (metaclass =M ):\n"
            "    pasar\n"
            "imprimir ('ok' )\n",
            _out,
        )
        _run = subprocess.run([str(_out)], capture_output=True, text=True)
        self.assertEqual(_run.returncode, 0)
        self.assertEqual(_run.stdout, "ok\n")


if __name__ == "__main__":
    unittest.main()
