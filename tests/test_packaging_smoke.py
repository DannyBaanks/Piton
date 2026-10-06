"""Smoke test de packaging: venv limpio + pip install no-editable + entrypoints.

Verifica que la CLI instalada funciona sin depender del checkout del repo.
Ejecutable como `python3 tests/test_packaging_smoke.py` o vía pytest.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOLA = ROOT / "examples" / "01_hola.piton"


def _correr(cmd: list[str], timeout: int = 600, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    entorno = os.environ.copy()
    entorno["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(cmd, env=entorno, text=True, encoding="utf-8",
                          capture_output=True, timeout=timeout, check=False,
                          cwd=str(cwd or tempfile.gettempdir()))


class PackagingSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="piton-smoke-")
        tmp = Path(cls._tmp.name)
        cls.venv = tmp / "venv"
        r = _correr([sys.executable, "-m", "venv", "--system-site-packages", str(cls.venv)], timeout=300)
        if r.returncode != 0:
            raise unittest.SkipTest(f"venv no disponible: {r.stderr[-200:]}")
        cls.bin = cls.venv / ("Scripts" if os.name == "nt" else "bin")
        cls.venv_python = cls.bin / ("python.exe" if os.name == "nt" else "python")
        # --no-build-isolation: setuptools viene de --system-site-packages; así
        # el smoke no depende de red.
        r = _correr([str(cls.venv_python), "-m", "pip", "install", "--no-build-isolation", str(ROOT)],
                    timeout=600)
        if r.returncode != 0:
            raise unittest.SkipTest(f"pip install falló: {r.stderr[-300:]}")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_entry_points_existen(self):
        for nombre in ("pi", "piton", "pitn"):
            exe = self.bin / (nombre + ".exe" if os.name == "nt" else nombre)
            self.assertTrue(exe.exists(), f"entry point ausente: {exe}")

    def test_pi_version(self):
        r = _correr([str(self.bin / ("pi.exe" if os.name == "nt" else "pi")), "--version"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout.strip(), r"^Piton \d+\.\d+\.\d+$")

    def test_pi_help_descubrible(self):
        r = _correr([str(self.bin / ("pi.exe" if os.name == "nt" else "pi")), "--help"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("run", r.stdout)
        self.assertIn("doctor", r.stdout)

    def test_pi_run_fixture(self):
        r = _correr([str(self.bin / ("pi.exe" if os.name == "nt" else "pi")), "run", str(HOLA)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "Hola, mundo\n")

    def test_pi_build_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / ("hola.exe" if sys.platform == "win32" else "hola")
            pi = self.bin / ("pi.exe" if os.name == "nt" else "pi")
            r = _correr([str(pi), "build", str(HOLA), "-o", str(out)], timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(out.exists())

    def test_python_m_piton(self):
        r = _correr([str(self.venv_python), "-m", "piton", "version"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Piton", r.stdout)


if __name__ == "__main__":
    unittest.main()
