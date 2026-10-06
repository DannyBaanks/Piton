"""Tests E2E del CLI de Pitón por subprocess (entry point real).

No mocks: se ejecuta `[sys.executable, "-m", "piton", ...]` como proceso.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOLA = ROOT / "examples" / "01_hola.piton"
PROGRAMA_COMPLETO = ROOT / "examples" / "programa_completo.piton"


def correr(*argumentos: str, cwd: Path = ROOT, timeout: int = 120,
           entrada: str | None = None) -> subprocess.CompletedProcess[str]:
    entorno = os.environ.copy()
    entorno["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "piton", *argumentos],
        cwd=cwd,
        env=entorno,
        text=True,
        encoding="utf-8",
        input=entrada,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


class CliE2E(unittest.TestCase):
    def test_help(self):
        r = correr("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        for palabra in ("run", "build", "check", "emit", "repl", "targets", "doctor", "evidence", "version"):
            self.assertIn(palabra, r.stdout)

    def test_version_flag_y_subcomando(self):
        for args in [("--version",), ("-V",), ("version",)]:
            r = correr(*args)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertRegex(r.stdout.strip(), r"^Piton \d+\.\d+\.\d+$")

    def test_run_cpython_default(self):
        r = correr("run", str(HOLA))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "Hola, mundo\n")

    def test_run_forwarding_args_con_doble_guion(self):
        r = correr("run", str(PROGRAMA_COMPLETO), "--", "dato", "uno")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("argumentos: ['dato', 'uno']", r.stdout)

    def test_run_engine_native(self):
        r = correr("run", str(HOLA), "--engine", "native", timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "Hola, mundo\n")

    def test_check_ok(self):
        r = correr("check", str(HOLA))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PITON_CHECK = PASS", r.stdout)

    def test_build_linux_native_y_ejecuta(self):
        if sys.platform == "win32" and not shutil.which("wsl"):
            self.skipTest("requiere WSL para el target linux-x86_64")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / ("hola.exe" if sys.platform == "win32" else "hola")
            r = correr("build", str(HOLA), "--target", "linux-x86_64", "-o", str(out), timeout=180)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("PITON_NATIVE_BUILD = PASS", r.stdout)
            self.assertTrue(out.exists())

    def test_build_target_invalido_argparse(self):
        r = correr("build", str(HOLA), "--target", "macos")
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid choice", r.stderr)

    def test_build_target_no_soportado_en_host_falla_limpio(self):
        if sys.platform == "win32":
            self.skipTest("en Windows el target Windows es válido")
        r = correr("build", str(HOLA), "--target", "windows-x86_64")
        self.assertEqual(r.returncode, 1)
        self.assertIn("PITON_NATIVE_BUILD_ERROR", r.stderr)
        self.assertIn("requiere host Windows", r.stderr)

    def test_build_no_sobrescribe_fuente(self):
        with tempfile.TemporaryDirectory() as tmp:
            fuente = Path(tmp) / "prog.piton"
            fuente.write_text('imprimir("ok")\n', encoding="utf-8")
            before = fuente.read_text(encoding="utf-8")
            r = correr("build", str(fuente), "-o", str(fuente))
            self.assertEqual(r.returncode, 1)
            self.assertIn("overwrite", r.stderr)
            self.assertEqual(fuente.read_text(encoding="utf-8"), before)

    def test_build_evidence_requiere_windows_target(self):
        r = correr("build", str(HOLA), "--target", "linux-x86_64", "--evidence", "recibo.json")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--evidence", r.stderr)

    def test_emit_python_stdout_puro(self):
        r = correr("emit", "python", str(HOLA))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertEqual(r.stdout, 'print("Hola, mundo")\n')

    def test_emit_ast(self):
        r = correr("emit", "ast", str(HOLA))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Module(", r.stdout)

    def test_emit_tokens(self):
        r = correr("emit", "tokens", str(HOLA))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("imprimir", r.stdout)

    def test_emit_mir_json_determinista(self):
        r = correr("emit", "mir", str(HOLA))
        self.assertEqual(r.returncode, 0, r.stderr)
        parsed = json.loads(r.stdout)
        self.assertIn("functions", parsed)

    def test_targets(self):
        r = correr("targets")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("windows-x86_64", r.stdout)
        self.assertIn("linux-x86_64", r.stdout)

    def test_doctor_estructura(self):
        r = correr("doctor")
        self.assertIn("PITON DOCTOR", r.stdout)
        self.assertIn("RESULT:", r.stdout)

    def test_evidence_summary(self):
        r = correr("evidence", "--format", "summary")
        self.assertIn("NATIVE_SUBSET_1_0 =", r.stdout)
        self.assertIn("PITON_NATIVE_SUBSET_PARITY =", r.stdout)

    def test_syntax_error_exit_2_sin_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            malo = Path(tmp) / "malo.piton"
            malo.write_text("funcion rota(\n", encoding="utf-8")
            r = correr("run", str(malo))
            self.assertEqual(r.returncode, 2)
            self.assertIn("PITON_SYNTAX_ERROR", r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_archivo_inexistente(self):
        r = correr("run", "/tmp/no_existe_piton_e2e.piton")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_path_con_espacios(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp) / "dir con espacios"
            carpeta.mkdir()
            prog = carpeta / "hola mundo.piton"
            prog.write_text('imprimir("espacios")\n', encoding="utf-8")
            r = correr("run", str(prog))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "espacios\n")

    def test_path_unicode(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp) / "ñoño_übér"
            carpeta.mkdir()
            prog = carpeta / "programa_ñ.piton"
            prog.write_text('imprimir("unicode")\n', encoding="utf-8")
            r = correr("run", str(prog))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "unicode\n")

    def test_stdout_stderr_separados_emit(self):
        r = correr("emit", "python", str(HOLA))
        self.assertEqual(r.stderr, "")

    def test_legacy_aliases(self):
        r = correr("ejecutar", str(HOLA))
        self.assertEqual((r.returncode, r.stdout), (0, "Hola, mundo\n"))
        r = correr("traducir", str(HOLA))
        self.assertEqual((r.returncode, r.stdout), (0, 'print("Hola, mundo")\n'))
        r = correr("verificar", str(HOLA))
        self.assertEqual(r.returncode, 0)
        self.assertIn("PITON_VALIDATION = PASS", r.stdout)
        r = correr("tokens", str(HOLA))
        self.assertEqual(r.returncode, 0)
        r = correr("ast", str(HOLA))
        self.assertEqual(r.returncode, 0)
        self.assertIn("Module(", r.stdout)

    def test_legacy_compilar_linux(self):
        if sys.platform == "win32" and not shutil.which("wsl"):
            self.skipTest("requiere WSL para el target linux-x86_64")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "hola_legacy"
            r = correr("compilar", str(HOLA), "--backend", "linux", "-o", str(out), timeout=180)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(out.exists())

    def test_repl_banner_engine(self):
        r = correr("repl", timeout=10, entrada='imprimir("desde_repl")\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        # code.InteractiveConsole envía banner/exitmsg a stderr cuando stdin no es TTY
        self.assertIn("engine: CPython", r.stdout + r.stderr)
        self.assertIn("desde_repl", r.stdout)

    def test_test_basico(self):
        r = correr("test", "tests/test_phase14.py", "-q", timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-500:] if r.stderr else "")
        self.assertIn("passed", r.stdout + r.stderr)

    def test_corpus_focalizado(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_json = Path(tmp) / "corpus.json"
            out_md = Path(tmp) / "corpus.md"
            r = correr("corpus", "--areas", "literals", "--limit", "4",
                       "--jobs", "4", "--timeout", "15",
                       "--out", str(out_json), "--markdown", str(out_md), timeout=600)
            self.assertEqual(r.returncode, 0, (r.stderr or "")[-500:])
            self.assertTrue(out_json.exists())
            data = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertIn("summary", data)


if __name__ == "__main__":
    unittest.main()
