from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def correr(*argumentos: str, entrada: str | None = None) -> subprocess.CompletedProcess[str]:
    entorno = os.environ.copy()
    entorno["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "piton", *argumentos],
        cwd=ROOT,
        env=entorno,
        text=True,
        encoding="utf-8",
        input=entrada,
        capture_output=True,
        check=False,
    )


class CliAndCorpusTests(unittest.TestCase):
    SALIDAS = {
        "01_hola.piton": "Hola, mundo\n",
        "02_condicionales.piton": "dos\n",
        "03_bucles.piton": "10\n",
        "04_funciones_clases.piton": "qué onda Danny\n¿y tú quién eres alv?\n",
        "05_excepciones_imports.piton": "3.0\nlisto\n",
        "06_colecciones.piton": "[0, 4, 16]\n3 True None\n",
        "07_seguridad_lexica.piton": (
            "si sino para funcion devolver imprimir\n"
            "sis sol onda Ada\n"
            "atributo intacto\n"
        ),
        "08_async_patrones.piton": "activo: Danny\nlista: 1+2\nnada\n4\n",
    }

    def test_corpus_ejecuta_con_salida_esperada(self) -> None:
        for nombre, esperado in self.SALIDAS.items():
            with self.subTest(nombre=nombre):
                resultado = correr("ejecutar", str(ROOT / "examples" / nombre))
                self.assertEqual(resultado.returncode, 0, resultado.stderr)
                self.assertEqual(resultado.stdout, esperado)

    def test_piton_y_python_equivalente_coinciden(self) -> None:
        piton = correr("ejecutar", str(ROOT / "examples" / "programa_completo.piton"), "dato")
        entorno = os.environ.copy()
        entorno["PYTHONIOENCODING"] = "utf-8"
        python = subprocess.run(
            [sys.executable, str(ROOT / "examples" / "equivalentes" / "programa_completo.py"), "dato"],
            cwd=ROOT,
            env=entorno,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual((piton.returncode, piton.stdout, piton.stderr), (python.returncode, python.stdout, python.stderr))

    def test_async_patrones_y_python_equivalente_coinciden(self) -> None:
        piton = correr("ejecutar", str(ROOT / "examples" / "08_async_patrones.piton"))
        entorno = os.environ.copy()
        entorno["PYTHONIOENCODING"] = "utf-8"
        python = subprocess.run(
            [sys.executable, str(ROOT / "examples" / "equivalentes" / "08_async_patrones.py")],
            cwd=ROOT,
            env=entorno,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual((piton.returncode, piton.stdout, piton.stderr), (python.returncode, python.stdout, python.stderr))

    def test_soft_keywords_ejecuta(self) -> None:
        resultado = correr("ejecutar", str(ROOT / "examples" / "09_soft_keywords.piton"))
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertIn("Danny", resultado.stdout)
        self.assertIn("es Danny", resultado.stdout)
        self.assertIn("42", resultado.stdout)

    def test_soft_keywords_python_equivalente_coinciden(self) -> None:
        piton = correr("ejecutar", str(ROOT / "examples" / "09_soft_keywords.piton"))
        entorno = os.environ.copy()
        entorno["PYTHONIOENCODING"] = "utf-8"
        python = subprocess.run(
            [sys.executable, str(ROOT / "examples" / "equivalentes" / "09_soft_keywords.py")],
            cwd=ROOT,
            env=entorno,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual((piton.returncode, piton.stdout, piton.stderr), (python.returncode, python.stdout, python.stderr))

    def test_traducir_a_archivo(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            salida = Path(temporal) / "hola.py"
            resultado = correr("traducir", str(ROOT / "examples" / "01_hola.piton"), "-o", str(salida))
            self.assertEqual(resultado.returncode, 0, resultado.stderr)
            self.assertEqual(salida.read_text(encoding="utf-8"), 'print("Hola, mundo")\n')

    def test_verificar_no_ejecuta(self) -> None:
        resultado = correr("verificar", str(ROOT / "examples" / "01_hola.piton"))
        self.assertEqual(resultado.returncode, 0)
        self.assertIn("PITON_VALIDATION = PASS", resultado.stdout)
        self.assertNotIn("Hola, mundo", resultado.stdout)

    def test_tokens_demuestra_transformados_e_ignorados(self) -> None:
        resultado = correr("tokens", str(ROOT / "examples" / "07_seguridad_lexica.piton"))
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertIn("TRANSFORMA 'imprimir' -> 'print'", resultado.stdout)
        self.assertIn("COMMENT    conserva", resultado.stdout)
        self.assertIn("STRING     conserva", resultado.stdout)
        self.assertIn("NAME       conserva   'sistema'", resultado.stdout)
        self.assertNotIn("DEDENT     TRANSFORMA", resultado.stdout)

    def test_tokens_soft_keywords(self) -> None:
        resultado = correr("tokens", str(ROOT / "examples" / "09_soft_keywords.piton"))
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertIn("TRANSFORMA 'segun' -> 'match' (soft-keyword)", resultado.stdout)
        self.assertIn("TRANSFORMA 'caso' -> 'case' (soft-keyword)", resultado.stdout)
        self.assertIn("TRANSFORMA 'tipo' -> 'type' (soft-keyword)", resultado.stdout)

    def test_ast_command(self) -> None:
        resultado = correr("ast", str(ROOT / "examples" / "01_hola.piton"))
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertIn("Module", resultado.stdout)

    def test_strict_mode_cli_rechaza_soft_keyword_ident(self) -> None:
        fixture = str(ROOT / "tests" / "fixtures" / "soft_keyword_ident.piton")
        resultado = correr("verificar", fixture, "-x")
        self.assertEqual(resultado.returncode, 2)
        self.assertIn("PITON_STRICT_ERROR", resultado.stderr)

    def test_strict_mode_no_afecta_keywords_contextuales(self) -> None:
        resultado = correr("verificar", str(ROOT / "examples" / "09_soft_keywords.piton"), "-x")
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertIn("PITON_VALIDATION = PASS", resultado.stdout)

    def test_error_cli_es_legible_y_no_ejecuta(self) -> None:
        resultado = correr("verificar", str(ROOT / "tests" / "fixtures" / "error_sintaxis.piton"))
        self.assertEqual(resultado.returncode, 2)
        self.assertIn("PITON_SYNTAX_ERROR", resultado.stderr)
        self.assertIn("archivo:", resultado.stderr)
        self.assertIn("linea:", resultado.stderr)
        self.assertIn("Python rechazó la traducción:", resultado.stderr)

    def test_entrada_preserva_stdin(self) -> None:
        resultado = correr(
            "ejecutar",
            str(ROOT / "tests" / "fixtures" / "entrada.piton"),
            entrada="Danny\n",
        )
        self.assertEqual((resultado.returncode, resultado.stdout, resultado.stderr), (0, "hola Danny\n", ""))


if __name__ == "__main__":
    unittest.main()
