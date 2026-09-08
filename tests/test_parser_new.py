"""Comparación estructural del parser nuevo y el traductor de referencia."""
import ast
import unittest

from piton.parser import parse
from piton.backend_python import generate_python_from_cst
from piton.translator import traducir_fuente

CASES = [
    # Básico
    'imprimir("hola")\n',
    # Variables
    'x = 42\nimprimir(x)\n',
    # Funciones
    'funcion saludar(nombre):\n    devolver "hola " + nombre\n\nimprimir(saludar("mundo"))\n',
    # If/else
    'si Verdadero:\n    imprimir("si")\nsino:\n    imprimir("no")\n',
    # While
    'i = 0\nmientras i < 3:\n    imprimir(i)\n    i = i + 1\n',
    # For
    'para x en rango(3):\n    imprimir(x)\n',
    # Clase
    'clase Caja:\n    funcion __init__(self, v):\n        self.v = v\n    funcion get(self):\n        devolver self.v\n\nc = Caja(10)\nimprimir(c.get())\n',
    # Try/except
    'intentar:\n    x = 1 / 0\nexcepto ZeroDivisionError:\n    imprimir("div by zero")\n',
    # With
    'con abrir("test.txt", "w") como f:\n    f.escribir("hola")\n',
    # Async
    'asincrono funcion main():\n    esperar 1\n    imprimir("async")\n',
    # Match
    'x = 1\nsegun x:\n    caso 1:\n        imprimir("uno")\n    caso _:\n        imprimir("otro")\n',
    # Import
    'importar sys\ndesde os importar path\n',
    # F-string
    'nombre = "mundo"\nimprimir(f"hola {nombre}")\n',
]


class ParserParityTests(unittest.TestCase):
    def test_parser_and_translator_generate_equivalent_ast(self) -> None:
        for source in CASES:
            with self.subTest(source=source):
                generated = generate_python_from_cst(parse(source))
                translated = traducir_fuente(source, "test.piton")
                self.assertEqual(
                    ast.dump(ast.parse(generated), include_attributes=False),
                    ast.dump(ast.parse(translated), include_attributes=False),
                )


if __name__ == "__main__":
    unittest.main()
