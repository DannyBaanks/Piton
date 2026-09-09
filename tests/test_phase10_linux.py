from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

from piton.linux_x86 import build_single_file_initramfs, compile_native_linux, windows_to_wsl_path
from piton.translator import traducir_fuente


class Phase10LinuxGates(unittest.TestCase):
    def _run_linux_diff(self, source: str):
        translated = traducir_fuente(source, "<linux-diff>")
        with tempfile.TemporaryDirectory(prefix="piton-linux-diff-") as directory:
            executable = compile_native_linux(source, Path(directory) / "program")
            linux_path = windows_to_wsl_path(executable)
            native_run = subprocess.run(
                ["wsl.exe", "/usr/bin/env", "-i", linux_path],
                capture_output=True, check=False, timeout=10,
            )
            oracle_run = subprocess.run(
                [sys.executable, "-c", translated],
                capture_output=True, check=False, timeout=10,
            )
            # Normalize: Linux ELF uses \n, Windows CPython uses \r\n
            native_stdout = native_run.stdout.replace(b"\r\n", b"\n")
            oracle_stdout = oracle_run.stdout.replace(b"\r\n", b"\n")
            return native_run.returncode, native_stdout, oracle_stdout, native_run.stderr

    def test_static_elf_x86_64_runs_with_empty_environment(self):
        source = (
            "funcion doble(valor):\n    devolver valor * 2\n"
            "x = 5\n"
            "si x > 2:\n    imprimir(doble(x))\n"
        )
        with tempfile.TemporaryDirectory(prefix="piton-linux-gate-") as directory:
            executable = compile_native_linux(source, Path(directory) / "program")
            linux_path = windows_to_wsl_path(executable)
            run = subprocess.run(
                ["wsl.exe", "/usr/bin/env", "-i", linux_path],
                capture_output=True, check=False,
            )
            self.assertEqual((run.returncode, run.stdout, run.stderr), (0, b"10\n", b""))
            header = subprocess.run(
                ["wsl.exe", "readelf", "-h", linux_path], capture_output=True, text=True, check=False
            )
            dynamic = subprocess.run(
                ["wsl.exe", "readelf", "-d", linux_path], capture_output=True, text=True, check=False
            )
            self.assertEqual(header.returncode, 0, header.stderr)
            self.assertIn("Advanced Micro Devices X86-64", header.stdout)
            self.assertNotIn("NEEDED", dynamic.stdout)
            self.assertNotIn(b"python", executable.read_bytes().lower())

    def test_static_elf_runs_inside_empty_chroot(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-chroot-") as directory:
            root = Path(directory)
            executable = compile_native_linux('imprimir("clean")\n', root / "program")
            linux_root = windows_to_wsl_path(root)
            prepared = subprocess.run(
                ["wsl.exe", "-u", "root", "chmod", "+x", f"{linux_root}/program"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            run = subprocess.run(
                ["wsl.exe", "-u", "root", "chroot", linux_root, "/program"],
                capture_output=True, check=False,
            )
            self.assertEqual((run.returncode, run.stdout, run.stderr), (0, b"clean\n", b""))

    def test_static_elf_boots_as_only_userspace_in_qemu(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-vm-") as directory:
            root = Path(directory)
            executable = compile_native_linux('imprimir("PITON_VM_CLEAN_PASS")\n', root / "program")
            initramfs = build_single_file_initramfs(executable, root / "initramfs.cpio.gz")
            qemu = Path.home() / "scoop" / "apps" / "qemu" / "current" / "qemu-system-x86_64.exe"
            kernel = Path(r"C:\Program Files\WSL\tools\kernel")
            self.assertTrue(qemu.is_file())
            self.assertTrue(kernel.is_file())
            run = subprocess.run(
                [
                    str(qemu), "-machine", "accel=tcg", "-m", "128M",
                    "-kernel", str(kernel), "-initrd", str(initramfs),
                    "-append", "console=ttyS0 panic=1", "-nographic",
                    "-no-reboot", "-monitor", "none",
                ],
                capture_output=True, check=False, timeout=45,
            )
            serial = run.stdout + run.stderr
            self.assertIn(b"PITON_VM_CLEAN_PASS", serial)
            self.assertNotIn(b"No working init found", serial)

    # ── Linux differential tests (scalar subset) ────────────────────────

    def test_linux_diff_hello(self):
        rc, stdout, stderr, _ = self._run_linux_diff('imprimir("hola")\n')
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, b"hola\n")

    def test_linux_diff_arithmetic(self):
        rc, stdout, stderr, _ = self._run_linux_diff('x = 10 * 3 + 7 - 2\nimprimir(x)\n')
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, b"35\n")

    def test_linux_diff_while(self):
        rc, stdout, stderr, _ = self._run_linux_diff('x = 0\nmientras x < 5:\n    x = x + 1\nimprimir(x)\n')
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, b"5\n")

    def test_linux_diff_if_elif_else(self):
        rc, stdout, stderr, _ = self._run_linux_diff('x = 15\nsi x > 20:\n    imprimir("grande")\nsino_si x > 10:\n    imprimir("medio")\nsino:\n    imprimir("chico")\n')
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, b"medio\n")

    def test_linux_diff_function(self):
        rc, stdout, stderr, _ = self._run_linux_diff('funcion cuadrado(n):\n    devolver n * n\nimprimir(cuadrado(7))\n')
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, b"49\n")

    def test_linux_diff_string_compare(self):
        rc, stdout, stderr, _ = self._run_linux_diff('s = "piton"\nimprimir(s == "piton")\nimprimir(s != "otro")\n')
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, b"True\nTrue\n")

    def test_linux_diff_bigint(self):
        rc, stdout, stderr, _ = self._run_linux_diff('x = 1000000000000000000000\nimprimir(x * 2)\n')
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, b"2000000000000000000000\n")

    def test_linux_diff_boolean(self):
        rc, stdout, stderr, _ = self._run_linux_diff('imprimir(Verdadero)\nimprimir(Falso)\n')
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, b"True\nFalse\n")

    def test_linux_diff_no_python_marker(self):
        source = 'imprimir("clean")\n'
        with tempfile.TemporaryDirectory(prefix="piton-linux-marker-") as directory:
            executable = compile_native_linux(source, Path(directory) / "program")
            self.assertNotIn(b"python", executable.read_bytes().lower())
            self.assertNotIn(b"cpython", executable.read_bytes().lower())


if __name__ == "__main__":
    unittest.main()
