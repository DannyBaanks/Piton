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

    # ── Linux differential tests (rich subset, vs CPython oracle) ──────

    def _assert_linux_equiv(self, source: str):
        rc, native_out, oracle_out, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, f"native stderr={stderr!r}")
        self.assertEqual(native_out, oracle_out, f"native={native_out!r} oracle={oracle_out!r}")

    def test_linux_rich_floats(self):
        self._assert_linux_equiv('imprimir(1.5)\nimprimir(-1.5)\nimprimir(1.25 + 2.5)\nimprimir(3.0 * 0.5)\nimprimir(1.5 < 2)\nimprimir(abs(-2.5))\n')

    def test_linux_rich_lists(self):
        self._assert_linux_equiv('imprimir([1, 2, 3])\nimprimir(longitud([1, 2, 3]))\nimprimir([4, 5][-1])\nimprimir(sum([1, 2, 3, 4, 5]))\n')

    def test_linux_rich_tuples(self):
        self._assert_linux_equiv('imprimir((1, 2, 3))\nimprimir((10, 20)[1])\nt = (5,)\nimprimir(t)\nimprimir(longitud(t))\n')

    def test_linux_rich_dicts(self):
        self._assert_linux_equiv('imprimir({1: 2, 3: 4})\nimprimir(longitud({1: 2, 3: 4}))\nimprimir({1: 2}[1])\nd = {10: 20, 30: 40}\nimprimir(d[30])\n')

    def test_linux_rich_sets(self):
        self._assert_linux_equiv('imprimir({1, 2, 3})\nimprimir(longitud({1, 2, 2, 3}))\n')

    def test_linux_rich_objects(self):
        self._assert_linux_equiv('clase Punto:\n    funcion __init__(self, px, py):\n        self.px = px\n        self.py = py\np = Punto(3, 4)\nimprimir(p.px)\nimprimir(p.py)\n')

    def test_linux_rich_methods(self):
        self._assert_linux_equiv('clase Contador:\n    funcion __init__(self):\n        self.valor = 0\n    funcion incrementar(self):\n        self.valor = self.valor + 1\nc = Contador()\nc.incrementar()\nc.incrementar()\nimprimir(c.valor)\n')

    def test_linux_rich_inheritance(self):
        self._assert_linux_equiv('clase Base:\n    funcion __init__(self, x):\n        self.x = x\n    funcion get_x(self):\n        devolver self.x\nclase Hija(Base):\n    funcion doble(self):\n        devolver self.x * 2\nh = Hija(5)\nimprimir(h.get_x())\nimprimir(h.doble())\n')

    def test_linux_rich_multilevel_inheritance(self):
        self._assert_linux_equiv('clase A:\n    funcion __init__(self, x):\n        self.x = x\n    funcion get_x(self):\n        devolver self.x\nclase B(A):\n    funcion multiply(self, n):\n        devolver self.x * n\nclase C(B):\n    funcion triple(self):\n        devolver self.x * 3\nc = C(4)\nimprimir(c.get_x())\nimprimir(c.multiply(5))\nimprimir(c.triple())\n')

    def test_linux_rich_exception_caught(self):
        self._assert_linux_equiv('intentar:\n    lanzar ValueError("x")\nexcepto ValueError:\n    imprimir("caught")\n')

    def test_linux_rich_exception_finally(self):
        self._assert_linux_equiv('intentar:\n    imprimir("try")\nfinalmente:\n    imprimir("finally")\n')

    def test_linux_rich_exception_after_catch(self):
        self._assert_linux_equiv('intentar:\n    lanzar ValueError("boom")\n    imprimir("no reach")\nexcepto ValueError:\n    x = 10 + 20\n    imprimir(x)\n')

    def test_linux_rich_stdlib_type(self):
        self._assert_linux_equiv('imprimir(type(42))\nimprimir(type("hola"))\nimprimir(type(Verdadero))\nimprimir(type(Nada))\n')

    def test_linux_rich_stdlib_min_max_abs(self):
        self._assert_linux_equiv('imprimir(abs(-42))\nimprimir(min(5, 3))\nimprimir(max(5, 3))\n')

    def test_linux_rich_math_sqrt(self):
        self._assert_linux_equiv('importar math\nimprimir(math.sqrt(9))\n')

    def test_linux_rich_augmented(self):
        self._assert_linux_equiv('x = 10\nx += 5\nx *= 2\nx -= 3\nimprimir(x)\n')

    def test_linux_rich_nested_while(self):
        self._assert_linux_equiv('total = 0\ni = 0\nmientras i < 3:\n    j = 0\n    mientras j < 3:\n        total = total + 1\n        j = j + 1\n    i = i + 1\nimprimir(total)\n')

    def test_linux_rich_ternary_if(self):
        self._assert_linux_equiv('x = 10\nsi x % 2 == 0:\n    r = "par"\nsino:\n    r = "impar"\nimprimir(r)\n')

    def test_linux_rich_collection_reassign(self):
        self._assert_linux_equiv('x = [1, 2, 3]\nx = [4, 5]\nimprimir(x)\nimprimir(longitud(x))\n')

    def test_linux_rich_multifunction(self):
        self._assert_linux_equiv('funcion suma(a, b):\n    devolver a + b\nfuncion producto(a, b):\n    devolver a * b\nimprimir(suma(3, 4))\nimprimir(producto(3, 4))\n')

    # ── Linux function default values (FUNCTION_DEFAULTS_V1) ───────────

    def test_linux_default_int(self):
        self._assert_linux_equiv('funcion saludar(n=10):\n    devolver n + 1\nimprimir(saludar())\nimprimir(saludar(5))\n')

    def test_linux_default_bool(self):
        self._assert_linux_equiv('funcion flag(marcar=Verdadero):\n    si marcar:\n        devolver 1\n    sino:\n        devolver 0\nimprimir(flag())\nimprimir(flag(Falso))\n')

    def test_linux_default_multiple_partial(self):
        self._assert_linux_equiv('funcion op(a=1, b=2, c=3):\n    devolver a + b + c\nimprimir(op())\nimprimir(op(10))\nimprimir(op(10, 20))\nimprimir(op(10, 20, 30))\n')

    def test_linux_default_nested_calls(self):
        self._assert_linux_equiv('funcion inc(n=1):\n    devolver n + 1\nimprimir(inc(inc(inc())))\n')

    # ── Linux keyword arguments (FUNCTION_KEYWORD_ARGS_V1) ─────────────

    def test_linux_kw_simple(self):
        self._assert_linux_equiv('funcion saludar(nombre, edad):\n    devolver nombre + edad\nimprimir(saludar(nombre=5, edad=3))\n')

    def test_linux_kw_order(self):
        self._assert_linux_equiv('funcion op(a, b, c):\n    devolver a * 100 + b * 10 + c\nimprimir(op(c=3, a=1, b=2))\n')

    def test_linux_kw_with_default(self):
        self._assert_linux_equiv('funcion f(a, b=10):\n    devolver a + b\nimprimir(f(a=5))\nimprimir(f(a=5, b=20))\n')

    def test_linux_kw_positional_mix(self):
        self._assert_linux_equiv('funcion f(a, b, c=3):\n    devolver a + b + c\nimprimir(f(1, c=10, b=2))\n')

    def test_linux_kw_middle_default_skipped(self):
        self._assert_linux_equiv('funcion f(a=1, b=2, c=3):\n    devolver a * 100 + b * 10 + c\nimprimir(f(c=5))\n')


if __name__ == "__main__":
    unittest.main()
