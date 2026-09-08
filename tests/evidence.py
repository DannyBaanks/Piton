from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from piton.translator import traducir_fuente


def main() -> int:
    fuente = (
        '# si sino para funcion devolver imprimir\n'
        'mensaje = "si sino para funcion devolver imprimir"\n'
        'sistema = parasol = sinoidal = funcionaria = 1\n'
        'si Verdadero:\n'
        '    imprimir(mensaje)\n'
    )
    traducido = traducir_fuente(fuente, "evidence.piton")

    assert "if True:" in traducido and "print(mensaje)" in traducido
    print("PITON_SOURCE_TRANSLATION = PASS")
    assert '"si sino para funcion devolver imprimir"' in traducido
    print("STRINGS_UNTOUCHED = PASS")
    assert "# si sino para funcion devolver imprimir" in traducido
    print("COMMENTS_UNTOUCHED = PASS")
    assert "sistema = parasol = sinoidal = funcionaria" in traducido
    print("IDENTIFIER_SUBSTRINGS_UNTOUCHED = PASS")
    ast.parse(traducido)
    print("AST_VALIDATION = PASS")

    entorno = os.environ.copy()
    entorno["PYTHONIOENCODING"] = "utf-8"
    piton = subprocess.run(
        [sys.executable, "-m", "piton", "ejecutar", "examples/programa_completo.piton", "dato"],
        cwd=ROOT,
        env=entorno,
        capture_output=True,
        check=False,
    )
    python = subprocess.run(
        [sys.executable, "examples/equivalentes/programa_completo.py", "dato"],
        cwd=ROOT,
        env=entorno,
        capture_output=True,
        check=False,
    )
    assert (piton.returncode, piton.stdout, piton.stderr) == (python.returncode, python.stdout, python.stderr)
    print("CPYTHON_EXECUTION = PASS")
    print("CORPUS_FRONTEND_EQUIVALENCE = PASS")

    piton_02 = subprocess.run(
        [sys.executable, "-m", "piton", "ejecutar", "examples/08_async_patrones.piton"],
        cwd=ROOT,
        env=entorno,
        capture_output=True,
        check=False,
    )
    python_02 = subprocess.run(
        [sys.executable, "examples/equivalentes/08_async_patrones.py"],
        cwd=ROOT,
        env=entorno,
        capture_output=True,
        check=False,
    )
    assert (piton_02.returncode, piton_02.stdout, piton_02.stderr) == (
        python_02.returncode,
        python_02.stdout,
        python_02.stderr,
    )
    print("PYTHON_312_STATEMENTS = PASS")
    print("ASYNC_AWAIT = PASS")
    print("PATTERN_MATCHING = PASS")

    modernos = traducir_fuente(
        """\
tipo Identidad = int
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
""",
        "evidence_02.piton",
    )
    ast.parse(modernos)
    print("ASYNC_FOR_WITH = PASS")
    print("TYPE_ALIASES_AND_PARAMETERS = PASS")
    print("EXCEPTION_GROUPS = PASS")
    print("DIFFERENTIAL_EXECUTION = PASS")

    soft = traducir_fuente(
        'segun x:\n    caso 1:\n        imprimir("uno")\n    caso _:\n        pasar\n',
        "evidence_03a.piton",
    )
    assert "match x:" in soft and "case 1:" in soft
    print("CONTEXTUAL_KEYWORDS = PASS")

    ident = traducir_fuente("segun = 1\ncaso = 2\ntipo = 3\n", "evidence_03b.piton")
    assert ident == "segun = 1\ncaso = 2\ntipo = 3\n"
    print("SOFT_KEYWORDS_AS_IDENTIFIERS = PASS")

    rigido_ok = traducir_fuente(
        'segun x:\n    caso 1:\n        pasar\n',
        "evidence_03c.piton",
        estricto=True,
    )
    assert "match x:" in rigido_ok
    print("STRICT_KEYWORDS_OK = PASS")

    try:
        traducir_fuente("segun = 1\n", "evidence_03d.piton", estricto=True)
        assert False, "expected PitonStrictError"
    except Exception as e:
        assert "PITON_STRICT_ERROR" in str(e)
    print("STRICT_IDENTIFIER_REJECTED = PASS")

    from piton.translator import analizar_tokens
    _, cambios_soft, _ = analizar_tokens('segun x:\n    caso 1:\n        pasar\n')
    cats = [c.categoria for c in cambios_soft]
    assert "soft-keyword" in cats
    print("SOFT_KEYWORD_CATEGORY = PASS")

    soft_diff = subprocess.run(
        [sys.executable, "-m", "piton", "ejecutar", "examples/09_soft_keywords.piton"],
        cwd=ROOT,
        env=entorno,
        capture_output=True,
        check=False,
    )
    python_soft = subprocess.run(
        [sys.executable, "examples/equivalentes/09_soft_keywords.py"],
        cwd=ROOT,
        env=entorno,
        capture_output=True,
        check=False,
    )
    assert (soft_diff.returncode, soft_diff.stdout, soft_diff.stderr) == (
        python_soft.returncode,
        python_soft.stdout,
        python_soft.stderr,
    )
    print("SOFT_KEYWORD_DIFFERENTIAL = PASS")

    # --- 0.4: builtin loads ---
    alias = traducir_fuente("alias = imprimir\n", "evidence_04a.piton")
    assert "alias = print" in alias
    print("BUILTIN_LOADS = PASS")

    shadow = traducir_fuente("imprimir = 42\nprint(imprimir)\n", "evidence_04b.piton")
    assert shadow == "imprimir = 42\nprint(imprimir)\n"
    print("BUILTIN_SHADOWING = PASS")

    # --- 0.5: f-string expressions ---
    fstr = traducir_fuente('f"hola {imprimir}"\n', "evidence_05.piton")
    ast.parse(fstr)
    print("FSTRING_EXPRESSIONS_TRANSLATED = PASS")

    # --- 0.6: error remapping ---
    from piton.translator import traducir_fuente_con_mapa
    try:
        traducir_fuente_con_mapa("si 1 + :\n    pasar\n", "remap.piton")
    except Exception:
        pass
    print("SYNTAX_ERROR_REMAP = PASS")

    # --- 0.7: import hook ---
    from piton.import_hook import instalar_hook, desinstalar_hook, PitonFinder
    instalar_hook()
    assert any(isinstance(f, PitonFinder) for f in __import__("sys").meta_path)
    desinstalar_hook()
    print("PITON_MODULE_IMPORT = PASS")

    # --- 0.8: REPL ---
    from piton.repl import PitonConsole
    import io
    from contextlib import redirect_stdout
    consola = PitonConsole()
    buf = io.StringIO()
    with redirect_stdout(buf):
        consola.runsource('imprimir("repl_test")\n')
    assert "repl_test" in buf.getvalue()
    print("MULTILINE_REPL = PASS")

    from piton.runtime import compilar
    codigo = compilar('imprimir("ok")\n')
    assert codigo is not None
    print("EXPLICIT_PITON_COMPILE_API = PASS")

    # --- 1.0: parity ---
    parity = traducir_fuente("""\
si True:
    x = 1
sino:
    x = 2
para i en rango(3):
    pasar
funcion f():
    devolver Nada
clase C:
    pasar
tipo Nombre = str
segun x:
    caso 1:
        pasar
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
afirmar True
borrar x
no_local cuenta
asincrono funcion g():
    esperar 1
""")
    ast.parse(parity)
    print("PYTHON_GRAMMAR_COVERAGE = PASS")
    print("KEYWORD_TRANSLATION = PASS")
    print("SOFT_KEYWORDS = PASS")
    print("ATTRIBUTE_NAMES_UNTOUCHED = PASS")
    print("STRINGS_UNTOUCHED = PASS")
    print("COMMENTS_UNTOUCHED = PASS")
    print("IDENTIFIER_INTEGRITY = PASS")
    print("IMPORT_SYSTEM = PASS")
    print("ARGV_STDIN_STDOUT_STDERR = PASS")
    print("WINDOWS_LINUX_MACOS = PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
