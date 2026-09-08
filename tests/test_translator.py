from __future__ import annotations

import ast
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from piton.runtime import ejecutar_archivo
from piton.translator import (
    MapaFuente,
    PitonStrictError,
    PitonSyntaxError,
    analizar_tokens,
    traducir_fuente,
    traducir_fuente_con_mapa,
)


ROOT = Path(__file__).resolve().parents[1]


class TranslatorTests(unittest.TestCase):
    def test_keywords_02_generan_ast_de_python_312(self) -> None:
        fuente = """\
tipo Identificador = int

asincrono funcion obtener(valor):
    esperar valor

funcion exterior():
    cuenta = 0
    funcion interior(valor):
        no_local cuenta
        afirmar valor es no Nada
        segun valor:
            caso 1:
                borrar valor
            caso _:
                pasar
"""
        traducido = traducir_fuente(fuente)
        arbol = ast.parse(traducido)
        self.assertIsInstance(arbol.body[0], ast.TypeAlias)
        self.assertIn("async def obtener", traducido)
        self.assertIn("match valor:", traducido)
        self.assertIn("assert valor is not None", traducido)

    def test_compuestos_modernos_y_genericos(self) -> None:
        fuente = """\
funcion identidad[T](valor: T) -> T:
    devolver valor

clase Caja[T]:
    pasar

asincrono funcion recorrer(iterable, gestor):
    asincrono para elemento en iterable:
        asincrono con gestor como recurso:
            producir elemento

intentar:
    lanzar ExceptionGroup("grupo", [ValueError("x")])
excepto* ValueError:
    pasar
"""
        traducido = traducir_fuente(fuente)
        ast.parse(traducido)
        self.assertIn("def identidad[T]", traducido)
        self.assertIn("class Caja[T]", traducido)
        self.assertIn("async for elemento in iterable", traducido)
        self.assertIn("async with gestor as recurso", traducido)
        self.assertIn("except* ValueError", traducido)

    def test_aliases_02_en_atributos_strings_y_comentarios_no_cambian(self) -> None:
        fuente = (
            '# asincrono esperar segun caso afirmar borrar no_local es tipo\n'
            'mensaje = "asincrono esperar segun caso afirmar borrar no_local es tipo"\n'
            'objeto.caso = objeto.tipo\n'
        )
        self.assertEqual(traducir_fuente(fuente), fuente)

    def test_keywords_principales_generan_ast_valido(self) -> None:
        fuente = """\
importar math
desde pathlib importar Path como Ruta

clase Ejemplo:
    funcion valores(self, limite):
        para numero en rango(limite):
            si numero y no Falso:
                producir numero
            sino_si numero o Falso:
                pasar
            sino:
                continuar
        devolver Nada

intentar:
    con abrir como recurso:
        mientras Verdadero:
            romper
excepto ValueError como error:
    lanzar error
finalmente:
    pasar
"""
        traducido = traducir_fuente(fuente)
        ast.parse(traducido)
        self.assertIn("def valores", traducido)
        self.assertIn("yield numero", traducido)
        self.assertIn("finally:", traducido)

    def test_strings_y_comentarios_quedan_intactos(self) -> None:
        fuente = '# si sino para imprimir\nmensaje = "si sino para funcion devolver imprimir"\n'
        traducido = traducir_fuente(fuente)
        self.assertIn("# si sino para imprimir", traducido)
        self.assertIn('"si sino para funcion devolver imprimir"', traducido)

    def test_substrings_quedan_intactos(self) -> None:
        fuente = "sistema = parasol = sinoidal = funcionaria = 1\n"
        self.assertEqual(traducir_fuente(fuente), fuente)

    def test_alias_builtin_solo_en_llamada_directa(self) -> None:
        fuente = "imprimir('ok')\nalias = imprimir\nobjeto.imprimir()\n"
        traducido = traducir_fuente(fuente)
        self.assertIn("print('ok')", traducido)
        self.assertIn("alias = print", traducido)
        self.assertIn("objeto.imprimir()", traducido)

    def test_todos_los_aliases_builtin(self) -> None:
        casos = {
            "imprimir": "print",
            "entrada": "input",
            "rango": "range",
            "longitud": "len",
            "enumerar": "enumerate",
            "lista": "list",
            "diccionario": "dict",
            "conjunto": "set",
            "tupla": "tuple",
            "entero": "int",
            "decimal": "float",
            "texto": "str",
            "booleano": "bool",
        }
        for piton, python in casos.items():
            with self.subTest(alias=piton):
                self.assertEqual(traducir_fuente(f"{piton}()\n"), f"{python}()\n")

    def test_metodo_con_nombre_builtin_no_se_reescribe(self) -> None:
        fuente = "clase Caja:\n    funcion imprimir(self):\n        devolver 1\n"
        traducido = traducir_fuente(fuente)
        self.assertIn("def imprimir(self):", traducido)

    def test_f_string_expresion_traducida(self) -> None:
        fuente = 'mensaje = f"si {imprimir}"\n'
        traducido = traducir_fuente(fuente)
        self.assertIn("print", traducido)

    def test_f_string_llaves_escapadas_intactas(self) -> None:
        fuente = 'mensaje = f"llaves {{imprimir}}"\n'
        traducido = traducir_fuente(fuente)
        self.assertIn("{{imprimir}}", traducido)

    def test_lineas_se_conservan(self) -> None:
        fuente = "si Verdadero:\n    imprimir('si')\nsino:\n    pasar\n"
        self.assertEqual(len(fuente.splitlines()), len(traducir_fuente(fuente).splitlines()))

    def test_analisis_reporta_cambios(self) -> None:
        _, cambios, _ = analizar_tokens('imprimir("si")  # sino\n')
        self.assertEqual([(c.original, c.traducido) for c in cambios], [("imprimir", "print")])

    def test_error_sintactico_tiene_ubicacion(self) -> None:
        with self.assertRaises(PitonSyntaxError) as contexto:
            traducir_fuente("si Verdadero\n    pasar\n", "fallo.piton")
        self.assertEqual(contexto.exception.archivo, "fallo.piton")
        self.assertIsNotNone(contexto.exception.linea)
        self.assertIn("PITON_SYNTAX_ERROR", str(contexto.exception))

    def test_ejecucion_preserva_argumentos(self) -> None:
        salida = io.StringIO()
        with redirect_stdout(salida):
            ejecutar_archivo(ROOT / "examples" / "programa_completo.piton", ["uno", "dos"])
        self.assertIn("argumentos: ['uno', 'dos']", salida.getvalue())

    # --- 0.3: soft keywords ---

    def test_segun_se_transforma_en_stmt_start(self) -> None:
        fuente = "segun x:\n    caso 1:\n        pasar\n"
        traducido = traducir_fuente(fuente)
        self.assertIn("match x:", traducido)
        self.assertIn("case 1:", traducido)

    def test_segun_como_identificador_se_conserva(self) -> None:
        fuente = "segun = 1\nprint(segun)\n"
        traducido = traducir_fuente(fuente)
        self.assertEqual(traducido, fuente)

    def test_segun_funcion_call_se_conserva(self) -> None:
        fuente = "resultado = segun(x)\n"
        self.assertEqual(traducir_fuente(fuente), fuente)

    def test_segun_en_parametro_se_conserva(self) -> None:
        fuente = "funcion f(segun):\n    imprimir(segun)\n"
        traducido = traducir_fuente(fuente)
        self.assertIn("def f(segun):", traducido)
        self.assertNotIn("def f(match):", traducido)

    def test_caso_keyword_dentro_de_match(self) -> None:
        fuente = "segun x:\n    caso 1:\n        imprimir('uno')\n    caso _:\n        pasar\n"
        traducido = traducir_fuente(fuente)
        self.assertIn("case 1:", traducido)
        self.assertIn("case _:", traducido)

    def test_caso_identificador_fuera_de_match(self) -> None:
        fuente = "caso = 1\nprint(caso)\n"
        self.assertEqual(traducir_fuente(fuente), fuente)

    def test_caso_identificador_dentro_de_match_con_asignacion(self) -> None:
        fuente = "segun x:\n    caso 1:\n        caso = 99\n"
        traducido = traducir_fuente(fuente)
        self.assertIn("case 1:", traducido)
        self.assertIn("caso = 99", traducido)

    def test_tipo_type_alias(self) -> None:
        fuente = "tipo Nombre = str\n"
        traducido = traducir_fuente(fuente)
        self.assertIn("type Nombre = str", traducido)

    def test_tipo_identificador_se_conserva(self) -> None:
        fuente = "tipo = 5\nprint(tipo)\n"
        self.assertEqual(traducir_fuente(fuente), fuente)

    def test_tipo_funcion_call_se_conserva(self) -> None:
        fuente = "x = tipo(objeto)\n"
        self.assertEqual(traducir_fuente(fuente), fuente)

    def test_tipo_parametro_se_conserva(self) -> None:
        fuente = "funcion f(tipo):\n    imprimir(tipo)\n"
        traducido = traducir_fuente(fuente)
        self.assertIn("def f(tipo):", traducido)

    def test_soft_en_atributo_se_conserva(self) -> None:
        fuente = "objeto.segun = 1\nobjeto.caso = 2\nobjeto.tipo = 3\n"
        self.assertEqual(traducir_fuente(fuente), fuente)

    def test_soft_en_string_y_comentario_se_conserva(self) -> None:
        fuente = '# segun caso tipo\nmensaje = "segun caso tipo"\n'
        self.assertEqual(traducir_fuente(fuente), fuente)

    def test_match_anidado(self) -> None:
        fuente = """\
segun x:
    caso 1:
        segun z:
            caso 2:
                imprimir("profundo")
            caso _:
                pasar
    caso _:
        pasar
"""
        traducido = traducir_fuente(fuente)
        self.assertEqual(traducido.count("match"), 2)
        self.assertEqual(traducido.count("case"), 4)

    # --- 0.3: strict mode ---

    def test_strict_error_para_segun_identificador(self) -> None:
        with self.assertRaises(PitonStrictError) as ctx:
            traducir_fuente("segun = 1\n", estricto=True)
        self.assertIn("PITON_STRICT_ERROR", str(ctx.exception))
        self.assertIn("segun", str(ctx.exception))

    def test_strict_error_para_caso_identificador(self) -> None:
        with self.assertRaises(PitonStrictError) as ctx:
            traducir_fuente("caso = 1\n", estricto=True)
        self.assertIn("PITON_STRICT_ERROR", str(ctx.exception))

    def test_strict_error_para_tipo_identificador(self) -> None:
        with self.assertRaises(PitonStrictError) as ctx:
            traducir_fuente("tipo = 1\n", estricto=True)
        self.assertIn("PITON_STRICT_ERROR", str(ctx.exception))

    def test_strict_no_error_para_keyword_contextual(self) -> None:
        fuente = "segun x:\n    caso 1:\n        pasar\n"
        traducido = traducir_fuente(fuente, estricto=True)
        self.assertIn("match x:", traducido)

    def test_strict_no_error_para_atributo(self) -> None:
        fuente = "objeto.segun = 1\n"
        self.assertEqual(traducir_fuente(fuente, estricto=True), fuente)

    def test_strict_no_error_para_string(self) -> None:
        fuente = 'mensaje = "segun caso tipo"\n'
        self.assertEqual(traducir_fuente(fuente, estricto=True), fuente)

    # --- 0.4: builtins as loads ---

    def test_builtin_load_en_asignacion(self) -> None:
        fuente = "alias = imprimir\n"
        traducido = traducir_fuente(fuente)
        self.assertEqual(traducido, "alias = print\n")

    def test_builtin_load_en_return(self) -> None:
        fuente = "funcion f():\n    devolver imprimir\n"
        traducido = traducir_fuente(fuente)
        self.assertIn("return print", traducido)

    def test_builtin_shadowing_local(self) -> None:
        fuente = "imprimir = 42\nprint(imprimir)\n"
        traducido = traducir_fuente(fuente)
        self.assertEqual(traducido, fuente)

    def test_builtin_global_declaration(self) -> None:
        fuente = "global imprimir\nimprimir = 42\nprint(imprimir)\n"
        traducido = traducir_fuente(fuente)
        self.assertEqual(traducido, fuente)

    def test_source_map_agrega_y_resuelve(self) -> None:
        _, _, mapa = analizar_tokens('imprimir("hola")\n')
        mapa.agregar(1, 10, 1, 0)
        self.assertEqual(mapa.resolver(1, 10), (1, 0))
        self.assertIsNone(mapa.resolver(99, 0))

    def test_source_map_en_traduccion(self) -> None:
        _, mapa = traducir_fuente_con_mapa('imprimir("hola")\n', validar=False)
        self.assertIsInstance(mapa, MapaFuente)

    def test_source_map_remapea_error(self) -> None:
        fuente = "si 1 + :\n    pasar\n"
        try:
            traducir_fuente(fuente, "test.piton")
        except PitonSyntaxError:
            pass

    # --- 0.5: f-string expressions ---

    def test_fstring_expresion_sencilla(self) -> None:
        fuente = 'mensaje = f"hola {imprimir}"\n'
        traducido = traducir_fuente(fuente)
        self.assertIn("print", traducido)

    def test_fstring_llaves_escapadas(self) -> None:
        fuente = 'mensaje = f"llaves {{imprimir}}"\n'
        traducido = traducir_fuente(fuente)
        self.assertIn("{{imprimir}}", traducido)

    def test_fstring_completa_intacta(self) -> None:
        fuente = 'mensaje = f"si {1 + 2} no {3}"\n'
        self.assertEqual(traducir_fuente(fuente), fuente)

    # --- 0.6: error remapping ---

    def test_syntax_error_remap(self) -> None:
        fuente = "si 1 + :\n    pasar\n"
        with self.assertRaises(PitonSyntaxError) as ctx:
            traducir_fuente(fuente, "remap.piton")
        self.assertEqual(ctx.exception.archivo, "remap.piton")

    def test_source_map_existe(self) -> None:
        traducido, mapa = traducir_fuente_con_mapa('imprimir("hola")\n')
        self.assertIsInstance(mapa, MapaFuente)

    # --- 0.7: import hook ---

    def test_import_hook_instalable(self) -> None:
        from piton.import_hook import instalar_hook, desinstalar_hook, PitonFinder
        instalar_hook()
        self.assertTrue(any(isinstance(f, PitonFinder) for f in __import__("sys").meta_path))
        desinstalar_hook()
        self.assertFalse(any(isinstance(f, PitonFinder) for f in __import__("sys").meta_path))

    # --- 0.8: REPL ---

    def test_repl_console_traduce(self) -> None:
        from piton.repl import PitonConsole
        import io
        from contextlib import redirect_stdout
        consola = PitonConsole()
        salida = io.StringIO()
        with redirect_stdout(salida):
            consola.runsource('imprimir("hola")\n')
        self.assertIn("hola", salida.getvalue())

    def test_compilar_api(self) -> None:
        from piton.runtime import compilar
        codigo = compilar('imprimir("ok")\n')
        self.assertIsNotNone(codigo)

    # --- 1.0: parity ---

    def test_1_0_parity_all_hard_keywords(self) -> None:
        fuente = """\
si True:
    x = 1
sino_si False:
    x = 2
sino:
    x = 3
para i en rango(3):
    mientras False:
        pasar
funcion f():
    devolver Nada
intentar:
    pasar
excepto ValueError:
    pasar
finalmente:
    pasar
con abrir() como f:
    pasar
importar sys
desde os importar path
clase C:
    pasar
"""
        traducido = traducir_fuente(fuente)
        ast.parse(traducido)
        self.assertIn("if True:", traducido)
        self.assertIn("elif False:", traducido)
        self.assertIn("else:", traducido)
        self.assertIn("for i in range(3):", traducido)
        self.assertIn("while False:", traducido)
        self.assertIn("def f():", traducido)
        self.assertIn("return None", traducido)
        self.assertIn("try:", traducido)
        self.assertIn("except ValueError:", traducido)
        self.assertIn("finally:", traducido)
        self.assertIn("with open() as f:", traducido)
        self.assertIn("import sys", traducido)
        self.assertIn("from os import path", traducido)
        self.assertIn("class C:", traducido)

    def test_1_0_parity_all_soft_keywords(self) -> None:
        fuente = """\
tipo Nombre = str
segun x:
    caso 1:
        pasar
    caso _:
        pasar
"""
        traducido = traducir_fuente(fuente)
        ast.parse(traducido)
        self.assertIn("type Nombre = str", traducido)
        self.assertIn("match x:", traducido)
        self.assertIn("case 1:", traducido)
        self.assertIn("case _:", traducido)

    def test_1_0_parity_all_modern_statements(self) -> None:
        fuente = """\
tipo Identidad = int
funcion identidad[T](valor: T) -> T:
    devolver valor
clase Caja[T]:
    pasar
asincrono funcion recorrer(iterable, gestor):
    asincrono para elemento en iterable:
        asincrono con gestor como recurso:
            producir elemento
afirmar True
borrar x
no_local cuenta
"""
        traducido = traducir_fuente(fuente)
        ast.parse(traducido)
        self.assertIn("type Identidad = int", traducido)
        self.assertIn("def identidad[T]", traducido)
        self.assertIn("class Caja[T]", traducido)
        self.assertIn("async def recorrer", traducido)
        self.assertIn("async for elemento in iterable", traducido)
        self.assertIn("async with gestor as recurso", traducido)
        self.assertIn("yield elemento", traducido)
        self.assertIn("assert True", traducido)
        self.assertIn("del x", traducido)
        self.assertIn("nonlocal cuenta", traducido)


if __name__ == "__main__":
    unittest.main()
