#!/usr/bin/env python3
"""Test rápido del parser nuevo vs translator actual."""
import sys
sys.path.insert(0, r"C:\Development\ISyCo Git\PITON")

from piton.parser import parse
from piton.backend_python import generate_python_from_cst
from piton.translator import traducir_fuente

# Tests simples
tests = [
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

print("=" * 60)
print("COMPARACIÓN: Parser nuevo (CST) vs Translator actual (tokenize)")
print("=" * 60)

for i, fuente in enumerate(tests, 1):
    print(f"\n--- Test {i} ---")
    print(f"Pitón:\n{fuente.strip()}")

    try:
        # Parser nuevo
        cst = parse(fuente)
        py_new = generate_python_from_cst(cst)
        print(f"Parser nuevo OK")
    except Exception as e:
        py_new = f"ERROR: {e}"
        print(f"Parser nuevo FAIL: {e}")

    try:
        # Translator actual
        py_old = traducir_fuente(fuente, "test.piton")
        print(f"Translator actual OK")
    except Exception as e:
        py_old = f"ERROR: {e}"
        print(f"Translator actual FAIL: {e}")

    print(f"Nuevo:\n{py_new.strip()}")
    print(f"Actual:\n{py_old.strip()}")

    if py_new.strip() == py_old.strip():
        print("✓ IGUALES")
    else:
        print("✗ DIFERENTES")

print("\n" + "=" * 60)
print("FIN")