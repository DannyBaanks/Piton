from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

from piton.linux_x86 import (
    build_single_file_initramfs,
    compile_native_linux,
    compile_native_linux_files,
    linux_run_cmd,
    windows_to_wsl_path,
    _is_native_linux,
)
from piton.translator import traducir_fuente
from piton.x86 import NativeBuildError


class Phase10LinuxGates(unittest.TestCase):
    def _run_linux_diff(self, source: str):
        translated = traducir_fuente(source, "<linux-diff>")
        with tempfile.TemporaryDirectory(prefix="piton-linux-diff-") as directory:
            executable = compile_native_linux(source, Path(directory) / "program")
            if _is_native_linux():
                native_run = subprocess.run(
                    [str(executable)],
                    capture_output=True, check=False, timeout=10,
                )
            else:
                linux_path = windows_to_wsl_path(executable)
                native_run = subprocess.run(
                    linux_run_cmd([linux_path]),
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
                linux_run_cmd([linux_path]),
                capture_output=True, check=False,
            )
            self.assertEqual((run.returncode, run.stdout, run.stderr), (0, b"10\n", b""))
            header = subprocess.run(
                linux_run_cmd(["readelf", "-h", linux_path]), capture_output=True, text=True, check=False
            )
            dynamic = subprocess.run(
                linux_run_cmd(["readelf", "-d", linux_path]), capture_output=True, text=True, check=False
            )
            self.assertEqual(header.returncode, 0, header.stderr)
            self.assertIn("Advanced Micro Devices X86-64", header.stdout)
            self.assertNotIn("NEEDED", dynamic.stdout)
            self.assertNotIn(b"python", executable.read_bytes().lower())

    def test_static_elf_runs_inside_empty_chroot(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-chroot-") as directory:
            root = Path(directory)
            executable = compile_native_linux('imprimir("clean")\n', root / "program")
            linux_root = str(root) if _is_native_linux() else windows_to_wsl_path(root)
            prepared = subprocess.run(
                linux_run_cmd(["chmod", "+x", f"{linux_root}/program"]),
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            run = subprocess.run(
                ["sudo", "chroot", str(root), "/program"],
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

    def test_linux_multi_inheritance_method_resolution(self):
        self._assert_linux_equiv(
            'clase A:\n    funcion saludo(self):\n        devolver 1\n'
            'clase B:\n    funcion saludo(self):\n        devolver 2\n'
            'clase C(A, B):\n    funcion doble(self):\n        devolver self.saludo() + self.saludo()\n'
            'c = C()\nimprimir(c.saludo())\nimprimir(c.doble())\n'
        )

    def test_linux_multi_inheritance_diamond(self):
        self._assert_linux_equiv(
            'clase Base:\n    funcion mensaje(self):\n        devolver 5\n'
            'clase Izq(Base):\n    funcion extra(self):\n        devolver 3\n'
            'clase Der(Base):\n    funcion mensaje(self):\n        devolver 7\n'
            'clase Fin(Izq, Der):\n    funcion total(self):\n        devolver self.mensaje() + self.extra()\n'
            'f = Fin()\nimprimir(f.mensaje())\nimprimir(f.total())\n'
        )

    def test_linux_super_chain_method_override(self):
        self._assert_linux_equiv(
            'clase A:\n    funcion valor(self):\n        devolver 1\n'
            'clase B(A):\n    funcion valor(self):\n        devolver super().valor() + 10\n'
            'clase C(B):\n    funcion valor(self):\n        devolver super().valor() + 100\n'
            'c = C()\nimprimir(c.valor())\n'
        )

    def test_linux_super_with_user_arguments(self):
        self._assert_linux_equiv(
            'clase A:\n    funcion add(self, a, b):\n        devolver a + b\n'
            'clase B(A):\n    funcion add(self, a, b):\n        devolver super().add(a, b) + 1\n'
            'b = B()\nimprimir(b.add(3, 4))\n'
        )

    def test_linux_super_in_constructor_chain(self):
        self._assert_linux_equiv(
            'clase A:\n    funcion __init__(self, x):\n        self.x = x\n'
            '    funcion get_x(self):\n        devolver self.x\n'
            'clase B(A):\n    funcion __init__(self, x, val):\n        self.v = val\n'
            '        super().__init__(x)\n    funcion get_v(self):\n        devolver self.v\n'
            'b = B(10, 20)\nimprimir(b.get_x())\nimprimir(b.get_v())\n'
        )

    def test_linux_self_method_call_in_plain_class(self):
        self._assert_linux_equiv(
            'clase Caja:\n    funcion __init__(self, valor):\n        self.valor = valor\n'
            '    funcion base(self):\n        devolver self.valor\n'
            '    funcion doble(self):\n        devolver self.base() * 2\n'
            'caja = Caja(21)\nimprimir(caja.doble())\n'
        )

    def test_linux_multi_inheritance_c3_conflict_fails_closed(self):
        source = (
            'clase O:\n    funcion m(self):\n        devolver 1\n'
            'clase X(O):\n    funcion m(self):\n        devolver 1\n'
            'clase Y(O):\n    funcion m(self):\n        devolver 2\n'
            'clase First(X, Y):\n    funcion m(self):\n        devolver 3\n'
            'clase Second(Y, X):\n    funcion m(self):\n        devolver 4\n'
            'clase E(First, Second):\n    funcion m(self):\n        devolver 5\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-linux-c3-fail-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "inconsistent method resolution order"):
                compile_native_linux(source, Path(directory) / "program")

    def test_linux_super_outside_method_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-super-fail-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "directly inside a class method"):
                compile_native_linux("super().x()\n", Path(directory) / "program")

    def test_linux_super_undefined_attribute_fails_closed(self):
        source = (
            'clase A:\n    funcion f(self):\n        devolver super().g()\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-linux-super-fail-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "no base class in MRO"):
                compile_native_linux(source, Path(directory) / "program")

    def test_linux_rich_exception_caught(self):
        self._assert_linux_equiv('intentar:\n    lanzar ValueError("x")\nexcepto ValueError:\n    imprimir("caught")\n')

    def test_linux_rich_exception_finally(self):
        self._assert_linux_equiv('intentar:\n    imprimir("try")\nfinalmente:\n    imprimir("finally")\n')

    def test_linux_rich_exception_after_catch(self):
        self._assert_linux_equiv('intentar:\n    lanzar ValueError("boom")\n    imprimir("no reach")\nexcepto ValueError:\n    x = 10 + 20\n    imprimir(x)\n')

    def test_linux_custom_exception_caught_by_exact_type(self):
        self._assert_linux_equiv('clase ErrorApp(Exception):\n    funcion __init__(self, m):\n        self.m = m\nintentar:\n    lanzar ErrorApp("boom")\nexcepto ErrorApp:\n    imprimir("caught-app")\n')

    def test_linux_custom_exception_caught_by_Exception_base(self):
        self._assert_linux_equiv('clase ErrorApp(Exception):\n    funcion __init__(self, m):\n        self.m = m\nintentar:\n    lanzar ErrorApp("boom")\nexcepto Exception:\n    imprimir("base")\n')

    def test_linux_custom_exception_subclass_caught_by_base(self):
        self._assert_linux_equiv('clase ErrorBase(Exception):\n    funcion __init__(self, m):\n        self.m = m\nclase ErrorHijo(ErrorBase):\n    funcion __init__(self, m):\n        self.m = m\nintentar:\n    lanzar ErrorHijo("desc")\nexcepto ErrorBase:\n    imprimir("caught-hijo")\n')

    def test_linux_custom_exception_subclass_of_valueerror(self):
        self._assert_linux_equiv('clase ErrorApp(ValueError):\n    funcion __init__(self, m):\n        self.m = m\nintentar:\n    lanzar ErrorApp("boom")\nexcepto ValueError:\n    imprimir("caught-val")\n')

    def test_linux_bare_reraise_to_outer_handler(self):
        self._assert_linux_equiv('intentar:\n    intentar:\n        lanzar ValueError("boom")\n    excepto ValueError:\n        imprimir("inner")\n        lanzar\nexcepto ValueError:\n    imprimir("outer")\n')

    def test_linux_bare_reraise_custom_to_base(self):
        self._assert_linux_equiv('clase ErrorApp(Exception):\n    funcion __init__(self, m):\n        self.m = m\nintentar:\n    intentar:\n        lanzar ErrorApp("x")\n    excepto ErrorApp:\n        imprimir("inner")\n        lanzar\nexcepto Exception:\n    imprimir("outer")\n')

    def test_linux_custom_exception_uncaught_exits_with_message(self):
        source = 'clase ErrorApp(Exception):\n    funcion __init__(self, m):\n        self.m = m\nintentar:\n    lanzar ErrorApp("boom")\nexcepto ValueError:\n    imprimir("no match")\n'
        rc, native_out, oracle_out, stderr = self._run_linux_diff(source)
        self.assertNotEqual(rc, 0)
        self.assertIn(b"ErrorApp", stderr)

    def test_linux_bare_reraise_unhandled_exits(self):
        source = 'intentar:\n    lanzar ValueError("boom")\nexcepto ValueError:\n    lanzar\nimprimir("never")\n'
        rc, native_out, oracle_out, stderr = self._run_linux_diff(source)
        self.assertNotEqual(rc, 0)
        self.assertIn(b"ValueError", stderr)

    def test_linux_raise_plain_class_rejected(self):
        from piton.linux_x86 import NativeBuildError, compile_native_linux
        with tempfile.TemporaryDirectory(prefix="piton-linux-diff-") as directory:
            source = "clase Punto:\n    funcion __init__(self, x):\n        self.x = x\nlanzar Punto(3)\n"
            with self.assertRaisesRegex(NativeBuildError, "subclass Exception"):
                compile_native_linux(source, Path(directory) / "program")

    def test_linux_bare_reraise_outside_handler_rejected(self):
        from piton.linux_x86 import NativeBuildError, compile_native_linux
        with tempfile.TemporaryDirectory(prefix="piton-linux-diff-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "requires an enclosing except handler"):
                compile_native_linux("funcion f():\n    lanzar\nf()\n", Path(directory) / "program")

    def test_linux_bare_reraise_from_catchall_rejected(self):
        # RERAISE_COMPLETE_V1: no longer rejected — re-raise propagates with
        # the runtime type and dies unhandled with exit 1 (nothing enclosing).
        from piton.linux_x86 import compile_native_linux
        with tempfile.TemporaryDirectory(prefix="piton-linux-diff-") as directory:
            source = 'intentar:\n    lanzar ValueError("x")\nexcepto Exception:\n    lanzar\nimprimir("done")\n'
            executable = compile_native_linux(source, Path(directory) / "program")
            linux_path = windows_to_wsl_path(executable)
            run = subprocess.run(
                linux_run_cmd([linux_path]),
                capture_output=True, check=False,
            )
            self.assertEqual(run.returncode, 1)
            self.assertIn(b"ValueError", run.stderr)

    def test_linux_with_multiple_items(self):
        self._assert_linux_equiv(
            'clase CM:\n'
            '    funcion __enter__(self):\n        devolver 1\n'
            '    funcion __exit__(self, t, m, tb):\n        devolver Falso\n'
            'con CM() como a, CM() como b:\n    imprimir(a + b)\n'
        )

    def test_linux_with_multiple_propagates(self):
        self._assert_linux_equiv(
            'clase CM:\n'
            '    funcion __enter__(self):\n        devolver 1\n'
            '    funcion __exit__(self, t, m, tb):\n        devolver Falso\n'
            'intentar:\n'
            '    con CM() como a, CM() como b:\n        lanzar ValueError("x")\n'
            'excepto ValueError:\n    imprimir("caught")\n'
        )

    def test_linux_class_getattr_hook_missing(self):
        self._assert_linux_equiv(
            'clase C:\n'
            '    funcion __init__(self):\n'
            '        self.x = 7\n'
            '    funcion __getattr__(self, nombre):\n'
            '        devolver 99\n'
            'c = C()\n'
            'imprimir(c.noExiste)\n'
        )

    def test_linux_class_setattr_hook(self):
        self._assert_linux_equiv(
            'clase C:\n'
            '    funcion __setattr__(self, nombre, valor):\n'
            '        imprimir(valor)\n'
            'c = C()\n'
            'c.x = 5\n'
        )

    def test_linux_class_delattr_hook(self):
        self._assert_linux_equiv(
            'clase C:\n'
            '    funcion __delattr__(self, nombre):\n'
            '        imprimir("del")\n'
            'c = C()\n'
            'c.x = 1\n'
            'borrar c.x\n'
        )

    def test_linux_class_call_routes_to_call(self):
        self._assert_linux_equiv(
            'clase C:\n'
            '    funcion __init__(self, base):\n'
            '        self.base = base\n'
            '    funcion __call__(self, x):\n'
            '        devolver self.base + x\n'
            'c = C(10)\n'
            'imprimir(c(21))\n'
        )

    def test_linux_class_eq_custom_dispatch(self):
        self._assert_linux_equiv(
            'clase C:\n'
            '    funcion __init__(self, v):\n'
            '        self.v = v\n'
            '    funcion __eq__(self, otra):\n'
            '        devolver self.v == otra.v\n'
            'a = C(7)\n'
            'b = C(7)\n'
            'imprimir(a == b)\n'
        )

    def test_linux_class_is_identity(self):
        self._assert_linux_equiv(
            'clase C:\n'
            '    funcion __init__(self):\n'
            '        self.x = 1\n'
            'a = C()\n'
            'b = a\n'
            'c = C()\n'
            'imprimir(a es b)\n'
            'imprimir(a es c)\n'
        )

    def test_linux_class_str_dispatch(self):
        self._assert_linux_equiv(
            'clase C:\n'
            '    funcion __init__(self, v):\n'
            '        self.v = v\n'
            '    funcion __str__(self):\n'
            '        devolver "caja"\n'
            'imprimir(C(1))\n'
        )

    def test_linux_class_len_dispatch(self):
        self._assert_linux_equiv(
            'clase C:\n'
            '    funcion __init__(self):\n'
            '        self.n = 5\n'
            '    funcion __len__(self):\n'
            '        devolver self.n\n'
            'imprimir(longitud(C()))\n'
        )

    # ── BASE_EXCEPTION_V1 ───────────────────────────────────────────────

    def test_linux_base_exception_catches_valueerror(self):
        self._assert_linux_equiv(
            'intentar:\n    lanzar ValueError("boom")\n'
            'excepto BaseException:\n    imprimir("caught")\n'
        )

    def test_linux_base_exception_direct_raise(self):
        self._assert_linux_equiv(
            'intentar:\n    lanzar BaseException("raw")\n'
            'excepto BaseException:\n    imprimir("caught-raw")\n'
        )

    def test_linux_base_exception_as_custom_parent(self):
        self._assert_linux_equiv(
            'clase ErrorCritico(BaseException):\n'
            '    funcion __init__(self, m):\n'
            '        self.m = m\n'
            'intentar:\n    lanzar ErrorCritico("fatal")\n'
            'excepto BaseException:\n    imprimir("caught-critico")\n'
        )

    def test_linux_base_exception_uncaught_exits_with_message(self):
        source = 'lanzar BaseException("crit")\n'
        rc, native_out, oracle_out, stderr = self._run_linux_diff(source)
        self.assertNotEqual(rc, 0)
        self.assertIn(b"BaseException", stderr)

    # ── EXCEPTION_FROM_V1 / EXCEPTION_CHAIN_V1 ────────────────────────────

    def test_linux_raise_from_cause_chain(self):
        # raise ValueError("caused") from TypeError("cause") - outer handler catches it
        self._assert_linux_equiv(
            'intentar:\n'
            '    intentar:\n'
            '        lanzar TypeError("causa original")\n'
            '    excepto TypeError:\n'
            '        lanzar ValueError("nuevo error") desde TypeError("causa original")\n'
            'excepto ValueError:\n'
            '    imprimir("caught")\n'
        )

    def test_linux_raise_from_none_suppresses_context(self):
        # raise ValueError("clean") from None - outer handler catches it
        self._assert_linux_equiv(
            'intentar:\n'
            '    intentar:\n'
            '        lanzar TypeError("should be suppressed")\n'
            '    excepto TypeError:\n'
            '        lanzar ValueError("clean") desde Nada\n'
            'excepto ValueError:\n'
            '    imprimir("caught")\n'
        )

    def test_linux_raise_from_variable_in_handler(self):
        # bare raise from in handler (reraise with new type) - outer handler catches it
        self._assert_linux_equiv(
            'intentar:\n'
            '    intentar:\n'
            '        lanzar ValueError("original")\n'
            '    excepto ValueError:\n'
            '        lanzar RuntimeError("desde handler")\n'
            'excepto RuntimeError:\n'
            '    imprimir("caught")\n'
        )

    def test_linux_raise_bare_in_handler_sets_context(self):
        # bare raise in handler sets __context__ (not __cause__) - outer handler catches it
        self._assert_linux_equiv(
            'intentar:\n'
            '    intentar:\n'
            '        lanzar ValueError("original")\n'
            '    excepto ValueError:\n'
            '        lanzar RuntimeError("nuevo")\n'
            'excepto RuntimeError:\n'
            '    imprimir("caught")\n'
        )

    # TODO: Attribute access on exception variables (e.__cause__, e.__context__) not yet implemented
#     def test_linux_exception_cause_attribute(self):
#         # Test __cause__ attribute access
#         self._assert_linux_equiv(
#             'intentar:\n'
#             '    intentar:\n'
#             '        lanzar TypeError("cause")\n'
#             '    excepto TypeError:\n'
#             '        lanzar ValueError("effect") desde TypeError("cause")\n'
#             'excepto ValueError como e:\n'
#             '    imprimir(e.__cause__ is not Nada)\n'
#         )
#
#     def test_linux_exception_context_attribute(self):
#         # Test __context__ attribute access (bare raise)
#         self._assert_linux_equiv(
#             'intentar:\n'
#             '    intentar:\n'
#             '        lanzar TypeError("context")\n'
#             '    excepto TypeError:\n'
#             '        lanzar ValueError("effect")\n'
#             'excepto ValueError como e:\n'
#             '    imprimir(e.__context__ is not Nada)\n'
#         )
#
#     def test_linux_exception_cause_none_when_not_set(self):
#         # __cause__ is None when not explicitly set
#         self._assert_linux_equiv(
#             'intentar:\n'
#             '    lanzar ValueError("no cause")\n'
#             'excepto ValueError como e:\n'
#             '    imprimir(e.__cause__ is Nada)\n'
#         )
#
#     def test_linux_exception_context_none_when_not_set(self):
#         # __context__ is None when no previous exception
#         self._assert_linux_equiv(
#             'intentar:\n'
#             '    lanzar ValueError("no context")\n'
#             'excepto ValueError como e:\n'
#             '    imprimir(e.__context__ is Nada)\n'
#         )

    def test_linux_exception_chain_uncaught_prints_cause(self):
        # Uncaught exception with __cause__ prints full chain
        source = (
            'intentar:\n'
            '    lanzar TypeError("cause")\n'
            'excepto TypeError:\n'
            '    lanzar ValueError("effect") desde TypeError("cause")\n'
        )
        rc, native_out, oracle_out, stderr = self._run_linux_diff(source)
        self.assertNotEqual(rc, 0)
        self.assertIn(b"TypeError: cause", stderr)
        self.assertIn(b"The above exception was the direct cause", stderr)
        self.assertIn(b"ValueError: effect", stderr)

    def test_linux_exception_chain_uncaught_prints_context(self):
        # Uncaught exception with __context__ prints full chain
        source = (
            'intentar:\n'
            '    lanzar TypeError("context")\n'
            'excepto TypeError:\n'
            '    lanzar ValueError("effect")\n'
        )
        rc, native_out, oracle_out, stderr = self._run_linux_diff(source)
        self.assertNotEqual(rc, 0)
        self.assertIn(b"TypeError: context", stderr)
        self.assertIn(b"During handling of the above exception", stderr)
        self.assertIn(b"ValueError: effect", stderr)

    def test_linux_rich_stdlib_type(self):
        self._assert_linux_equiv('imprimir(type(42))\nimprimir(type("hola"))\nimprimir(type(Verdadero))\nimprimir(type(Nada))\n')

    def test_linux_rich_stdlib_min_max_abs(self):
        self._assert_linux_equiv('imprimir(abs(-42))\nimprimir(min(5, 3))\nimprimir(max(5, 3))\n')

    def test_linux_rich_math_sqrt(self):
        self._assert_linux_equiv('importar math\nimprimir(math.sqrt(9))\n')

    # ── STDLIB_TIER1_V1: math.floor/ceil/sin/cos/log + sys.exit/argv + os.name ──

    def test_linux_math_floor_ceil_match_cpython(self):
        self._assert_linux_equiv(
            'importar math\n'
            'imprimir(math.floor(2.7))\nimprimir(math.floor(-2.3))\n'
            'imprimir(math.ceil(2.3))\nimprimir(math.ceil(-2.7))\n'
        )

    def test_linux_math_floor_ceil_of_int_value(self):
        self._assert_linux_equiv(
            'importar math\n'
            'imprimir(math.floor(7))\nimprimir(math.ceil(7.0))\n'
        )

    def test_linux_math_sin_cos_log_match_cpython(self):
        # Short-repr values so the freestanding 12-digit float printer matches CPython repr.
        self._assert_linux_equiv(
            'importar math\n'
            'imprimir(math.sin(0.0))\nimprimir(math.cos(0.0))\nimprimir(math.log(1.0))\n'
        )

    def test_linux_math_floor_nan_fails_value_error(self):
        # CPython returns an arbitrary-precision int here; native V1 is int64-only
        # and fails closed at the float-to-int boundary.
        rc, _native_out, _oracle_out, stderr = self._run_linux_diff(
            'importar math\nimprimir(math.floor(1e300))\n'
        )
        self.assertNotEqual(rc, 0)
        self.assertIn(b'OverflowError: cannot convert float infinity to integer', stderr)

    def test_linux_sys_exit_with_code(self):
        rc, native_out, _oracle_out, _stderr = self._run_linux_diff(
            'importar sys\nimprimir("antes")\nsys.exit(3)\n'
        )
        self.assertEqual(rc, 3)
        self.assertEqual(native_out, b'antes\n')

    def test_linux_sys_exit_zero_code(self):
        rc, native_out, _oracle_out, _stderr = self._run_linux_diff(
            'importar sys\nsys.exit()\n'
        )
        self.assertEqual(rc, 0)
        self.assertEqual(native_out, b'')

    def test_linux_sys_argv_is_list_with_entry(self):
        # CPython -c: sys.argv == ['-c']; nativo: argv[0] = ruta del ejecutable.
        self._assert_linux_equiv(
            'importar sys\nimprimir(len(sys.argv))\n'
        )

    def test_linux_os_name_match(self):
        self._assert_linux_equiv('importar os\nimprimir(os.name)\n')

    def test_linux_os_name_from_import(self):
        self._assert_linux_equiv('desde os importar name\nimprimir(name)\n')

    def test_linux_rich_math_floor(self):
        self._assert_linux_equiv('importar math\nimprimir(math.floor(3.7))\nimprimir(math.floor(-3.7))\n')

    def test_linux_rich_math_ceil(self):
        self._assert_linux_equiv('importar math\nimprimir(math.ceil(3.2))\nimprimir(math.ceil(-3.2))\n')

    def test_linux_rich_math_sin(self):
        self._assert_linux_equiv('importar math\nimprimir(math.sin(0))\nimprimir(math.sin(1.57079632679))\n')

    def test_linux_rich_math_cos(self):
        self._assert_linux_equiv('importar math\nimprimir(math.cos(0))\nimprimir(math.cos(3.14159265359))\n')

    def test_linux_rich_math_log(self):
        self._assert_linux_equiv('importar math\nimprimir(math.log(1))\n')

    def test_linux_rich_sys_argv(self):
        # sys.argv when running as standalone ELF contains the program path
        # (similar to CPython's behavior when running a script file)
        source = 'importar sys\nimprimir(sys.argv)\n'
        translated = traducir_fuente(source, "<linux-diff>")
        with tempfile.TemporaryDirectory(prefix="piton-linux-diff-") as directory:
            executable = compile_native_linux(source, Path(directory) / "program")
            if _is_native_linux():
                native_run = subprocess.run(
                    [str(executable)],
                    capture_output=True, check=False, timeout=10,
                )
            else:
                linux_path = windows_to_wsl_path(executable)
                native_run = subprocess.run(
                    linux_run_cmd([linux_path]),
                    capture_output=True, check=False, timeout=10,
                )
            # Native ELF returns [program_path] when run standalone
            self.assertEqual(native_run.returncode, 0)
            self.assertTrue(native_run.stdout.strip().startswith(b'['))

    def test_linux_rich_os_name(self):
        self._assert_linux_equiv('importar os\nimprimir(os.name)\n')

    def test_linux_rich_sys_exit(self):
        self._assert_linux_equiv('importar sys\nimprimir(sys.exit(0))\n')

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

    # ── Linux *args (FUNCTION_STARARGS_V1) ──────────────────────────────

    def test_linux_starargs_pure_empty_and_many(self):
        self._assert_linux_equiv('funcion contar(*args):\n    devolver longitud(args)\nimprimir(contar())\nimprimir(contar(1, 2, 3, 4, 5))\n')

    def test_linux_list_and_tuple_for_loop(self):
        self._assert_linux_equiv(
            'total = 0\n'
            'para valor en [1, 2, 3]:\n'
            '    total = total + valor\n'
            'para valor en (4, 5):\n'
            '    total = total + valor\n'
            'imprimir(total)\n'
        )

    def test_linux_iter_and_next_list_tuple(self):
        self._assert_linux_equiv(
            'a = iter([4, 5])\n'
            'b = iter((8, 9))\n'
            'imprimir(next(a))\n'
            'imprimir(next(a))\n'
            'imprimir(next(b))\n'
        )

    def test_linux_iter_and_next_dict_set(self):
        self._assert_linux_equiv(
            'it = iter({"uno": 1, "dos": 2})\n'
            'imprimir(next(it))\n'
            'imprimir(next(it))\n'
            'set_it = iter({1, 2})\n'
            'imprimir(next(set_it) + next(set_it))\n'
        )

    def test_linux_stop_iteration_is_caught(self):
        self._assert_linux_equiv(
            'it = iter([7])\n'
            'intentar:\n'
            '    imprimir(next(it))\n'
            '    next(it)\n'
            'excepto StopIteration:\n'
            '    imprimir("exhausted")\n'
        )

    def test_linux_user_defined_iter_and_next(self):
        self._assert_linux_equiv(
            'clase Uno:\n'
            '    funcion __iter__(self):\n'
            '        devolver self\n'
            '    funcion __next__(self):\n'
            '        devolver 7\n'
            'u = Uno()\n'
            'imprimir(next(iter(u)))\n'
            'imprimir(next(u))\n'
        )

    def test_linux_user_defined_stop_iteration_propagates(self):
        self._assert_linux_equiv(
            'clase Uno:\n'
            '    funcion __init__(self):\n'
            '        self.usado = 0\n'
            '    funcion __iter__(self):\n'
            '        devolver self\n'
            '    funcion __next__(self):\n'
            '        si self.usado:\n'
            '            lanzar StopIteration()\n'
            '        self.usado = 1\n'
            '        devolver 7\n'
            'it = iter(Uno())\n'
            'intentar:\n'
            '    imprimir(next(it))\n'
            '    next(it)\n'
            'excepto StopIteration:\n'
            '    imprimir("exhausted")\n'
        )

    def test_linux_enumerate_iterator(self):
        self._assert_linux_equiv(
            'it = enumerar([10, 20], 3)\n'
            'imprimir(next(it))\n'
            'imprimir(next(it))\n'
        )

    def test_linux_reversed_and_zip_iterators(self):
        self._assert_linux_equiv(
            'r = reversed([1, 2, 3])\n'
            'imprimir(next(r))\n'
            'z = zip([1, 2], [10, 20, 30])\n'
            'imprimir(next(z))\n'
            'imprimir(next(z))\n'
        )

    def test_linux_map_and_filter_iterators(self):
        self._assert_linux_equiv(
            'funcion doble(x):\n'
            '    devolver x * 2\n'
            'funcion es_par(x):\n'
            '    devolver x % 2 == 0\n'
            'imprimir(next(map(doble, [1, 2])))\n'
            'imprimir(next(filter(es_par, [1, 2, 3, 4])))\n'
        )

    def test_linux_map_accepts_closure_callback(self):
        self._assert_linux_equiv(
            'funcion ejecutar():\n'
            '    m = 3\n'
            '    funcion multiplicar(x):\n'
            '        devolver x * m\n'
            '    imprimir(next(map(multiplicar, [2])))\n'
            'ejecutar()\n'
        )

    def test_linux_map_accepts_lambda_callback(self):
        self._assert_linux_equiv('imprimir(next(map(lambda x: x * 3, [2])))\n')

    def test_linux_filter_accepts_lambda_callback(self):
        self._assert_linux_equiv('imprimir(next(filter(lambda x: x > 2, [1, 2, 3, 4])))\n')

    def test_linux_map_lambda_with_capture(self):
        self._assert_linux_equiv(
            'm = 5\n'
            'imprimir(next(map(lambda x: x + m, [1, 2])))\n'
        )

    def test_linux_sorted_builtin(self):
        self._assert_linux_equiv('imprimir(sorted([3, 1, 2]))\n')

    def test_linux_builtins_core_v2(self):
        # M14 BUILTINS_CORE_V2: paridad CPython en Linux para
        # all/any/bin/chr/ord/pow/round.
        self._assert_linux_equiv(
            'imprimir(ord("A"))\n'
            'imprimir(ord("ñ"))\n'
            'imprimir(chr(97))\n'
            'imprimir(bin(-7))\n'
            'imprimir(bin(7))\n'
            'imprimir(pow(2, 10))\n'
            'imprimir(pow(2.5, 3))\n'
            'imprimir(pow(2.0, -1))\n'
            'imprimir(any([0, 0, 4]))\n'
            'imprimir(all([1, 0]))\n'
            'imprimir(any([]))\n'
            'imprimir(all([]))\n'
            'imprimir(round(2.5))\n'
            'imprimir(round(3.5))\n'
            'imprimir(round(-2.5))\n'
            'imprimir(round(7))\n'
        )

    def test_linux_type_conversion_v1(self):
        # M14 TYPE_CONVERSION_V1: paridad CPython en Linux.
        self._assert_linux_equiv(
            'imprimir(entero("42"))\n'
            'imprimir(entero(" -17 "))\n'
            'imprimir(entero(2.9))\n'
            'imprimir(entero(-2.9))\n'
            'imprimir(entero(Verdadero))\n'
            'imprimir(texto(42))\n'
            'imprimir(texto(2.5))\n'
            'imprimir(texto(3.0))\n'
            'imprimir(texto(15.625))\n'
            'imprimir(texto(Verdadero))\n'
            'imprimir(texto(Nada))\n'
            'imprimir(texto("hola"))\n'
            'imprimir(decimal(3))\n'
            'imprimir(decimal("-0.5"))\n'
            'imprimir(decimal(Verdadero))\n'
            'imprimir(booleano(0))\n'
            'imprimir(booleano(-1))\n'
            'imprimir(booleano(0.0))\n'
            'imprimir(booleano(-0.0))\n'
            'imprimir(booleano("x"))\n'
            'imprimir(booleano(""))\n'
            'imprimir(booleano([]))\n'
            'imprimir(booleano([0]))\n'
            'imprimir(booleano(Nada))\n'
        )

    def test_linux_math_tier1_v1(self):
        # M14 MATH_TIER1_V1: sqrt/floor/ceil/trunc/fabs/gcd + pi/e.
        self._assert_linux_equiv(
            'importar math\n'
            'imprimir(math.sqrt(9))\n'
            'imprimir(math.floor(2.7))\n'
            'imprimir(math.floor(-2.3))\n'
            'imprimir(math.ceil(2.1))\n'
            'imprimir(math.ceil(-2.9))\n'
            'imprimir(math.trunc(-2.9))\n'
            'imprimir(math.fabs(-3.5))\n'
            'imprimir(math.gcd(12, 18))\n'
            'imprimir(math.gcd(0, 7))\n'
            'imprimir(math.floor(math.pi * 1000))\n'
            'imprimir(math.floor(math.e * 100))\n'
        )

    def test_linux_type_conversion_fail_closed_catchable(self):
        self._assert_linux_equiv(
            'intentar:\n'
            '    n = entero("abc")\n'
            '    imprimir(n)\n'
            'excepto ValueError:\n'
            '    imprimir("int-invalido ValueError atrapado")\n'
            'intentar:\n'
            '    m = decimal("xyz")\n'
            '    imprimir(m)\n'
            'excepto ValueError:\n'
            '    imprimir("float-invalido ValueError atrapado")\n'
        )

    def test_linux_builtins_core_v2_fail_closed_catchable(self):
        # Las violaciones fuera de la matriz M14 v1 son excepciones
        # capturables por intentar/excepto (también bajo WSL).
        self._assert_linux_equiv(
            'intentar:\n'
            '    a = ord("")\n'
            '    imprimir(a)\n'
            'excepto TypeError:\n'
            '    imprimir("ord-vacio TypeError atrapado")\n'
            'intentar:\n'
            '    b = chr(2000000)\n'
            '    imprimir(b)\n'
            'excepto ValueError:\n'
            '    imprimir("chr-rango ValueError atrapado")\n'
            'intentar:\n'
            '    c = ord("ab")\n'
            '    imprimir(c)\n'
            'excepto TypeError:\n'
            '    imprimir("ord-multichar TypeError atrapado")\n'
        )

    def test_linux_starargs_sum_and_subscript(self):
        self._assert_linux_equiv('funcion resumir(*args):\n    devolver sum(args) + args[0] * 10\nimprimir(resumir(2, 3, 4))\n')

    def test_linux_starargs_fixed_and_default(self):
        self._assert_linux_equiv('funcion peso(base=1, *extras):\n    devolver base * 10 + longitud(extras)\nimprimir(peso())\nimprimir(peso(5, 7, 9))\nimprimir(peso(base=4))\n')

    # ── Linux **kwargs (FUNCTION_KWARGS_V1) ─────────────────────────────

    def test_linux_kwargs_pure_dict(self):
        self._assert_linux_equiv('funcion leer(**kw):\n    devolver longitud(kw) * 10 + kw["x"]\nimprimir(leer(x=4, z=8))\n')

    def test_linux_kwargs_fixed_and_starargs(self):
        self._assert_linux_equiv('funcion total(base, *extras, **opciones):\n    devolver base + sum(extras) + opciones["extra"]\nimprimir(total(1, 2, 3, extra=4))\n')

    # ── Linux call-site unpacking (CALL_UNPACKING_LITERAL_V1) ───────────

    def test_linux_kwargs_unpacking_literal(self):
        self._assert_linux_equiv(
            'funcion f(base, extra=0):\n'
            '    devolver base + extra\n'
            'imprimir(f(**{"base": 2, "extra": 3}))\n'
        )

    def test_linux_starargs_unpacking_literal(self):
        self._assert_linux_equiv(
            'funcion f(base, extra):\n'
            '    devolver base * 10 + extra\n'
            'imprimir(f(*[1, 2]))\n'
        )

    def test_linux_starargs_unpacking_dynamic(self):
        self._assert_linux_equiv(
            'funcion f(base, extra):\n'
            '    devolver base * 10 + extra\n'
            'xs = [1, 2]\n'
            'imprimir(f(*xs))\n'
        )

    def test_linux_kwargs_unpacking_dynamic(self):
        self._assert_linux_equiv(
            'funcion f(base, extra=0):\n'
            '    devolver base + extra\n'
            'kw = {"base": 2, "extra": 3}\n'
            'imprimir(f(**kw))\n'
        )

    def test_linux_bound_method_retrieval_and_later_call(self):
        self._assert_linux_equiv(
            'clase C:\n'
            '    funcion __init__(self, base):\n'
            '        self.base = base\n'
            '    funcion suma(self, extra):\n'
            '        devolver self.base + extra\n'
            'c = C(7)\n'
            'm = c.suma\n'
            'imprimir(m(5))\n'
        )

    def test_linux_bound_method_frame_abi(self):
        self._assert_linux_equiv(
            'clase C:\n'
            '    funcion suma(self, a, b, c, d):\n'
            '        devolver a + b + c + d\n'
            'c = C()\n'
            'm = c.suma\n'
            'imprimir(m(1, 2, 3, 4))\n'
        )

    def test_linux_decorators_apply_bottom_up_and_rebind(self):
        self._assert_linux_equiv(
            'funcion doble(fn):\n'
            '    funcion envuelta(x):\n'
            '        devolver fn(x) * 2\n'
            '    devolver envuelta\n'
            '@doble\n'
            'funcion inc(x):\n'
            '    devolver x + 1\n'
            'imprimir(inc(3))\n'
        )

    def test_linux_frame_abi_supports_more_than_four_parameters(self):
        self._assert_linux_equiv(
            'funcion suma(a, b, c, d, e):\n'
            '    devolver a + b + c + d + e\n'
            'imprimir(suma(1, 2, 3, 4, 5))\n'
        )

    def test_linux_function_annotations_preserve_call_semantics(self):
        self._assert_linux_equiv(
            'funcion suma(a: int, b: int) -> int:\n'
            '    devolver a + b\n'
            'imprimir(suma(2, 3))\n'
        )

    def test_linux_async_with_awaits_enter_and_exit(self):
        self._assert_linux_equiv(
            'importar asyncio\n'
            'clase CM:\n'
            '    asincrono funcion __aenter__(self):\n'
            '        devolver 7\n'
            '    asincrono funcion __aexit__(self, t, m, tb):\n'
            '        devolver Falso\n'
            'asincrono funcion run_async():\n'
            '    asincrono con CM() como x:\n'
            '        devolver x\n'
            'imprimir(asyncio.run(run_async()))\n'
        )

    def test_linux_async_exception_is_caught_inside_coroutine(self):
        self._assert_linux_equiv(
            'importar asyncio\n'
            'asincrono funcion run_async():\n'
            '    intentar:\n'
            '        lanzar ValueError("async boom")\n'
            '    excepto ValueError como error:\n'
            '        devolver 1\n'
            'imprimir(asyncio.run(run_async()))\n'
        )

    # ── Linux signature markers (FUNCTION_SIGNATURE_MARKERS_V1) ─────────

    def test_linux_positional_only(self):
        self._assert_linux_equiv('funcion unir(a, /, b):\n    devolver a * 10 + b\nimprimir(unir(2, 3))\n')

    def test_linux_keyword_only_required_and_default(self):
        self._assert_linux_equiv('funcion escalar(base, *, factor=2, extra):\n    devolver base * factor + extra\nimprimir(escalar(3, extra=1))\nimprimir(escalar(3, factor=4, extra=1))\n')

    def test_linux_signature_markers_combined(self):
        self._assert_linux_equiv('funcion total(base, /, *extras, ajuste=3, **opciones):\n    devolver base + sum(extras) + ajuste + opciones["final"]\nimprimir(total(1, 2, 3, ajuste=4, final=5))\n')

    # ── Linux recursion + closures (FRAME_MODEL_V1) ─────────────────────

    def test_linux_native_recursion_factorial(self):
        self._assert_linux_equiv('funcion factorial(n):\n    si n <= 1:\n        devolver 1\n    devolver n * factorial(n - 1)\nimprimir(factorial(10))\n')

    def test_linux_recursive_closure_with_capture(self):
        self._assert_linux_equiv('funcion crear():\n    m = 10\n    funcion filtrar(n):\n        si n <= 1:\n            devolver 1 + m - 10\n        devolver n * filtrar(n - 1)\n    devolver filtrar(6)\nimprimir(crear())\n')

    def test_linux_nested_closure_transitive_capture(self):
        self._assert_linux_equiv('funcion outer(a):\n    b = a + 1\n    funcion mid(c):\n        funcion inner(d):\n            devolver a + b + c + d\n        devolver inner(1)\n    devolver mid(2)\nimprimir(outer(10))\n')

    def test_linux_closure_frame_abi_more_than_four_captures_and_args(self):
        self._assert_linux_equiv(
            'funcion fabricar():\n'
            '    uno = 1\n'
            '    dos = 2\n'
            '    tres = 3\n'
            '    cuatro = 4\n'
            '    cinco = 5\n'
            '    funcion sumar(a, b, c, d, e):\n'
            '        devolver uno + dos + tres + cuatro + cinco + a + b + c + d + e\n'
            '    devolver sumar\n'
            'f = fabricar()\n'
            'imprimir(f(6, 7, 8, 9, 10))\n'
        )

    def test_linux_variadic_closure_escape(self):
        self._assert_linux_equiv(
            "funcion fabrica():\n"
            "    base = 10\n"
            "    funcion escapada(x, *resto):\n"
            "        devolver base + x + sum(resto)\n"
            "    devolver escapada\n"
            "f = fabrica()\n"
            "imprimir(f(1, 2, 3))\n"
        )

    def test_linux_iter_callable_sentinel(self):
        self._assert_linux_equiv(
            "funcion contador():\n"
            "    x = 3\n"
            "    funcion fuente():\n"
            "        no_local x\n"
            "        x = x - 1\n"
            "        devolver x\n"
            "    devolver fuente\n"
            "f = contador()\n"
            "it = iter(f, 0)\n"
            "imprimir(next(it))\n"
            "imprimir(next(it))\n"
        )

    def test_linux_next_with_default(self):
        self._assert_linux_equiv(
            "xs = [7]\n"
            "it = iter(xs)\n"
            "imprimir(next(it))\n"
            "imprimir(next(it, -1))\n"
            "imprimir(next(it, -2))\n"
        )

    def test_linux_immutable_scalar_closure(self):
        self._assert_linux_equiv('funcion exterior(x):\n    factor = 3\n    funcion interior(valor):\n        devolver x + factor * valor\n    devolver interior(4)\nimprimir(exterior(2))\n')

    def test_linux_escaped_closure_callable_later(self):
        self._assert_linux_equiv('funcion fabricar(x):\n    funcion suma(m):\n        devolver x + m\n    devolver suma\nf = fabricar(40)\nimprimir(f(2))\n')

    def test_linux_nonlocal_counter_mutation(self):
        self._assert_linux_equiv('funcion contador():\n    n = 0\n    funcion sube(paso):\n        no_local n\n        n = n + paso\n        devolver n\n    devolver sube\nc = contador()\nimprimir(c(5))\nimprimir(c(7))\n')

    def test_linux_nonlocal_write_only_capture(self):
        self._assert_linux_equiv('funcion contador():\n    n = 0\n    funcion home():\n        no_local n\n        n = n + 1\n    home()\n    home()\n    devolver n\nimprimir(contador())\n')

    def test_linux_callback_plain_function_and_closure(self):
        self._assert_linux_equiv('funcion cuadrado(n):\n    devolver n*n\nfuncion aplicar(fn, n):\n    devolver fn(n)\nimprimir(aplicar(cuadrado, 9))\nfuncion fab():\n    k = 3\n    funcion doble(x):\n        devolver k * x\n    devolver doble\nd = fab()\nimprimir(aplicar(d, 5))\n')

    def test_linux_closure_two_cells_two_arguments(self):
        self._assert_linux_equiv('funcion fab():\n    a = 1\n    b = 2\n    funcion suma(x, m):\n        devolver a + b + x + m\n    devolver suma\ns = fab()\nimprimir(s(10, 20))\n')

    def test_linux_independent_closure_instances(self):
        self._assert_linux_equiv('funcion contador():\n    n = 0\n    funcion sube(paso):\n        no_local n\n        n = n + paso\n        devolver n\n    devolver sube\na = contador()\nb = contador()\nimprimir(a(1))\nimprimir(a(2))\nimprimir(b(10))\n')

    def test_linux_nonlocal_mutual_swap_of_cells(self):
        self._assert_linux_equiv('funcion fab():\n    m = 1\n    k = 2\n    funcion par(x):\n        no_local m\n        no_local k\n        t = m\n        m = k\n        k = t\n        devolver m + k + x\n    devolver par\np = fab()\nimprimir(p(0))\nimprimir(p(0))\n')

    def test_linux_chained_closure_factories(self):
        self._assert_linux_equiv('funcion nivel1(x):\n    funcion nivel2():\n        funcion nivel3(m):\n            devolver x + m\n        devolver nivel3\n    devolver nivel2\na = nivel1(100)\nb = a()\nimprimir(b(5))\n')

    def test_linux_closure_arity_fail_closed(self):
        source = 'funcion fab():\n    k = 3\n    funcion doble(x):\n        devolver k * x\n    devolver doble\nd = fab()\nimprimir(d(2, 4))\n'
        with tempfile.TemporaryDirectory(prefix="piton-linux-arity-") as directory:
            executable = compile_native_linux(source, Path(directory) / "program")
            linux_path = windows_to_wsl_path(executable)
            run = subprocess.run(
                linux_run_cmd([linux_path]),
                capture_output=True, check=False, timeout=10,
            )
        self.assertEqual(run.returncode, 2)
        self.assertIn(b"TypeError: closure called with wrong number of arguments", run.stderr)

    # ── Linux packages (IMPORT_PACKAGE_V1) ──────────────────────────────

    def _assert_package_equiv(self, package_init, submodules, main):
        with tempfile.TemporaryDirectory(prefix="piton-linux-package-") as directory:
            root = Path(directory)
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.piton").write_text(package_init, encoding="utf-8")
            (pkg_dir / "__init__.py").write_text(
                traducir_fuente(package_init, "<pkg-init>"), encoding="utf-8"
            )
            for sub_name, sub_src in (submodules or {}).items():
                (pkg_dir / f"{sub_name}.piton").write_text(sub_src, encoding="utf-8")
                (pkg_dir / f"{sub_name}.py").write_text(
                    traducir_fuente(sub_src, f"<pkg-{sub_name}>"), encoding="utf-8"
                )
            entry = root / "main.piton"
            entry.write_text(main, encoding="utf-8")
            main_py = root / "main.py"
            main_py.write_text(traducir_fuente(main, "<main>"), encoding="utf-8")
            executable = compile_native_linux_files(entry, root / "program")
            linux_path = windows_to_wsl_path(executable)
            native_run = subprocess.run(
                linux_run_cmd([linux_path]),
                capture_output=True, check=False, timeout=10,
            )
            oracle_run = subprocess.run(
                [sys.executable, str(main_py)], capture_output=True, check=False, timeout=10,
            )
            native_stdout = native_run.stdout.replace(b"\r\n", b"\n")
            oracle_stdout = oracle_run.stdout.replace(b"\r\n", b"\n")
            self.assertEqual(
                (native_run.returncode, native_stdout),
                (oracle_run.returncode, oracle_stdout),
                native_run.stderr,
            )

    def test_linux_package_import_uses_init(self):
        init = "funcion cuadrado(n):\n    devolver n * n\n"
        main = "importar pkg\nimprimir(pkg.cuadrado(7))\n"
        self._assert_package_equiv(init, None, main)

    def test_linux_package_from_import_submodule(self):
        init = "funcion doble(x):\n    devolver x * 2\n"
        submodules = {
            "numeros": "funcion suma(a, b):\n    devolver a + b\n",
        }
        main = (
            "importar pkg\n"
            "desde pkg.numeros importar suma\n"
            "imprimir(pkg.doble(21))\n"
            "imprimir(suma(40, 2))\n"
        )
        self._assert_package_equiv(init, submodules, main)

    def test_linux_package_from_import_init_function(self):
        init = "funcion triple(n):\n    devolver n * 3\n"
        main = "desde pkg importar triple\nimprimir(triple(8))\n"
        self._assert_package_equiv(init, None, main)

    # ── MODULE_METADATA_V1 (Linux mirror) ───────────────────────────────

    def test_linux_module_metadata_source_mode_cpython_equiv(self):
        source = (
            "importar sys\n"
            "imprimir(__name__)\n"
            "imprimir(__package__)\n"
            'imprimir(sys.modules["__main__"].__name__)\n'
            'imprimir(sys.modules["__main__"].__package__)\n'
            'imprimir(sys.modules["sys"].__name__)\n'
            'imprimir(sys.modules["sys"].__package__)\n'
        )
        self._assert_linux_equiv(source)

    def test_linux_module_metadata_files_mode_cpython_equiv(self):
        main = (
            "importar sys\nimportar util\nimportar pkg\n"
            "desde pkg.numeros importar suma\n"
            "imprimir(util.doble(21))\n"
            "imprimir(suma(40, 2))\n"
            'imprimir(sys.modules["util"].__name__)\n'
            'imprimir(sys.modules["util"].__package__)\n'
            'imprimir(sys.modules["pkg"].__name__)\n'
            'imprimir(sys.modules["pkg"].__package__)\n'
            'imprimir(sys.modules["pkg.numeros"].__name__)\n'
            'imprimir(sys.modules["pkg.numeros"].__package__)\n'
            'imprimir(sys.modules["__main__"].__name__)\n'
            'imprimir(sys.modules["__main__"].__package__)\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-linux-meta-") as directory:
            root = Path(directory)
            util_src = "funcion doble(x):\n    devolver x * 2\n"
            (root / "util.piton").write_text(util_src, encoding="utf-8")
            (root / "util.py").write_text(traducir_fuente(util_src, "<util>"), encoding="utf-8")
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            init_src = "funcion triple(n):\n    devolver n * 3\n"
            (pkg_dir / "__init__.piton").write_text(init_src, encoding="utf-8")
            (pkg_dir / "__init__.py").write_text(traducir_fuente(init_src, "<pkg-init>"), encoding="utf-8")
            numeros_src = "funcion suma(a, b):\n    devolver a + b\n"
            (pkg_dir / "numeros.piton").write_text(numeros_src, encoding="utf-8")
            (pkg_dir / "numeros.py").write_text(traducir_fuente(numeros_src, "<pkg-numeros>"), encoding="utf-8")
            entry = root / "main.piton"
            entry.write_text(main, encoding="utf-8")
            main_py = root / "main.py"
            main_py.write_text(traducir_fuente(main, "<main>"), encoding="utf-8")
            executable = compile_native_linux_files(entry, root / "program")
            linux_path = windows_to_wsl_path(executable)
            native_run = subprocess.run(
                linux_run_cmd([linux_path]),
                capture_output=True, check=False, timeout=10,
            )
            oracle_run = subprocess.run(
                [sys.executable, str(main_py)], capture_output=True, check=False, timeout=10,
            )
            native_stdout = native_run.stdout.replace(b"\r\n", b"\n")
            oracle_stdout = oracle_run.stdout.replace(b"\r\n", b"\n")
            self.assertEqual(
                (native_run.returncode, native_stdout),
                (oracle_run.returncode, oracle_stdout),
                native_run.stderr,
            )

    def test_linux_module_metadata_entry_file_set_in_files_mode(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-meta-file-") as directory:
            root = Path(directory)
            entry = root / "main.piton"
            entry.write_text("importar sys\nimprimir(__file__)\n", encoding="utf-8")
            executable = compile_native_linux_files(entry, root / "program")
            linux_path = windows_to_wsl_path(executable)
            completed = subprocess.run(
                linux_run_cmd([linux_path]),
                capture_output=True, check=False, timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
            self.assertTrue(
                completed.stdout.decode(errors="replace").replace("\r", "").strip().endswith("main.piton"),
                completed.stdout,
            )

    def test_linux_package_missing_init_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-package-fail-") as directory:
            root = Path(directory)
            root.joinpath("pkg").mkdir()
            (root / "pkg" / "sub.piton").write_text(
                "funcion fn():\n    devolver 1\n", encoding="utf-8"
            )
            entry = root / "main.piton"
            entry.write_text("desde pkg.sub importar fn\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "requires package"):
                compile_native_linux_files(entry, root / "program")

    # ── IMPORT_RELATIVE_V1 (Linux mirror) ──────────────────────────────

    def test_linux_relative_from_import_in_package_equiv(self):
        init = (
            "funcion triple(n):\n    devolver n * 3\n"
            "desde . importar numeros\n"
            "desde .operaciones importar resta\n"
        )
        submodules = {
            "numeros": "funcion suma(a, b):\n    devolver a + b\n",
            "operaciones": "funcion resta(a, b):\n    devolver a - b\n",
        }
        main = (
            "importar pkg\nimportar sys\n"
            "imprimir(pkg.triple(3))\n"
            "imprimir(pkg.numeros.suma(20, 22))\n"
            "imprimir(pkg.operaciones.resta(100, 7))\n"
            'imprimir(sys.modules["pkg"].__package__)\n'
            'imprimir(sys.modules["pkg.numeros"].__name__)\n'
            'imprimir(sys.modules["pkg.numeros"].__package__)\n'
            'imprimir(sys.modules["pkg.operaciones"].__package__)\n'
        )
        self._assert_package_equiv(init, submodules, main)

    def test_linux_dotted_import_binds_top_and_chain_calls(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-dotted-") as directory:
            root = Path(directory)
            pkg2 = root / "pkg2"
            pkg2.mkdir()
            (pkg2 / "__init__.piton").write_text("", encoding="utf-8")
            (pkg2 / "__init__.py").write_text("", encoding="utf-8")
            sub = pkg2 / "subpkg"
            sub.mkdir()
            sub_init = (
                "funcion agrupar(n):\n    devolver n + 1\n"
                "desde . importar deep\n"
                "desde .otro importar doble\n"
            )
            (sub / "__init__.piton").write_text(sub_init, encoding="utf-8")
            (sub / "__init__.py").write_text(
                traducir_fuente(sub_init, "<subpkg-init>"), encoding="utf-8"
            )
            deep_src = "funcion canal(n):\n    devolver n * 100\n"
            (sub / "deep.piton").write_text(deep_src, encoding="utf-8")
            (sub / "deep.py").write_text(traducir_fuente(deep_src, "<deep>"), encoding="utf-8")
            otro_src = "funcion doble(n):\n    devolver n * 2\n"
            (sub / "otro.piton").write_text(otro_src, encoding="utf-8")
            (sub / "otro.py").write_text(traducir_fuente(otro_src, "<otro>"), encoding="utf-8")
            main = (
                "importar pkg2.subpkg\n"
                "imprimir(pkg2.subpkg.deep.canal(7))\n"
                "imprimir(pkg2.subpkg.otro.doble(21))\n"
                "imprimir(pkg2.subpkg.agrupar(1))\n"
            )
            entry = root / "main.piton"
            entry.write_text(main, encoding="utf-8")
            main_py = root / "main.py"
            main_py.write_text(traducir_fuente(main, "<main>"), encoding="utf-8")
            executable = compile_native_linux_files(entry, root / "program")
            linux_path = windows_to_wsl_path(executable)
            native_run = subprocess.run(
                linux_run_cmd([linux_path]),
                capture_output=True, check=False, timeout=10,
            )
            oracle_run = subprocess.run(
                [sys.executable, str(main_py)], capture_output=True, check=False, timeout=10,
            )
            native_stdout = native_run.stdout.replace(b"\r\n", b"\n")
            oracle_stdout = oracle_run.stdout.replace(b"\r\n", b"\n")
            self.assertEqual(
                (native_run.returncode, native_stdout),
                (oracle_run.returncode, oracle_stdout),
                native_run.stderr,
            )

    def test_linux_relative_import_in_entry_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-relative-entry-") as directory:
            root = Path(directory)
            entry = root / "main.piton"
            entry.write_text("desde . importar numeros\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "no parent package"):
                compile_native_linux_files(entry, root / "program")

    def test_linux_relative_import_beyond_one_level_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-relative-deep-") as directory:
            root = Path(directory)
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.piton").write_text(
                "desde .. importar otro\n", encoding="utf-8"
            )
            entry = root / "main.piton"
            entry.write_text("importar pkg\n", encoding="utf-8")
            # M8: N levels now allowed — `..` from a 1-segment package fails
            # because it escapes the top package, not because "level too deep".
            with self.assertRaisesRegex(Exception, "escapes the package"):
                compile_native_linux_files(entry, root / "program")

    def test_linux_relative_two_levels_up_supported(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-rel2-") as directory:
            root = Path(directory)
            pkg_dir = root / "pkg"
            sub_dir = pkg_dir / "sub"
            pkg_dir.mkdir(); sub_dir.mkdir()
            (pkg_dir / "__init__.piton").write_text("pasar\n", encoding="utf-8")
            (sub_dir / "__init__.piton").write_text("pasar\n", encoding="utf-8")
            (pkg_dir / "comun.piton").write_text(
                "funcion comun_x():\n    devolver 100\n", encoding="utf-8"
            )
            (sub_dir / "deep.piton").write_text(
                "desde .. importar comun\n"
                "funcion doble(x):\n    devolver comun.comun_x() + x\n",
                encoding="utf-8",
            )
            entry = root / "main.piton"
            entry.write_text(
                "desde pkg.sub.deep importar doble\n"
                "imprimir(doble(3))\n", encoding="utf-8"
            )
            executable = compile_native_linux_files(entry, root / "program")
            run = subprocess.run(
                ["wsl.exe", "/usr/bin/env", "-i", windows_to_wsl_path(executable)],
                capture_output=True, check=False,
            )
            self.assertEqual((run.returncode, run.stdout), (0, b"103\n"))

    def test_linux_module_attribute_value_access_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-module-attr-") as directory:
            root = Path(directory)
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.piton").write_text(
                "funcion fn():\n    devolver 1\n", encoding="utf-8"
            )
            entry = root / "main.piton"
            entry.write_text("importar pkg\nimprimir(pkg.numeros)\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "module function calls"):
                compile_native_linux_files(entry, root / "program")

    def test_linux_module_attr_chain_keyword_args_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-module-kw-") as directory:
            root = Path(directory)
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.piton").write_text("", encoding="utf-8")
            (pkg_dir / "numeros.piton").write_text(
                "funcion suma(a, b):\n    devolver a + b\n", encoding="utf-8"
            )
            entry = root / "main.piton"
            entry.write_text(
                "importar pkg\nimprimir(pkg.numeros.suma(a=1, b=2))\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(Exception, "keyword arguments"):
                compile_native_linux_files(entry, root / "program")

    # ── IMPORT_STAR_V1 (Linux mirror) ───────────────────────────────────

    def test_linux_star_from_package_equiv(self):
        init = (
            "funcion cuadrado(n):\n    devolver n * n\n"
            "funcion doble(n):\n    devolver n * 2\n"
            "funcion _privada(n):\n    devolver n\n"
        )
        main = (
            "desde pkg importar *\n"
            "imprimir(cuadrado(6))\n"
            "imprimir(doble(21))\n"
        )
        self._assert_package_equiv(init, None, main)

    def test_linux_star_from_standalone_module_equiv(self):
        module_src = "funcion resta(a, b):\n    devolver a - b\nfuncion doble(n):\n    devolver n * 2\n"
        main = (
            "desde lib importar *\n"
            "imprimir(resta(100, 7))\n"
            "imprimir(doble(21))\n"
        )
        with tempfile.TemporaryDirectory(prefix="piton-linux-star-lib-") as directory:
            root = Path(directory)
            (root / "lib.piton").write_text(module_src, encoding="utf-8")
            (root / "lib.py").write_text(traducir_fuente(module_src, "<lib>"), encoding="utf-8")
            entry = root / "main.piton"
            entry.write_text(main, encoding="utf-8")
            main_py = root / "main.py"
            main_py.write_text(traducir_fuente(main, "<main>"), encoding="utf-8")
            executable = compile_native_linux_files(entry, root / "program")
            linux_path = windows_to_wsl_path(executable)
            native_run = subprocess.run(
                linux_run_cmd([linux_path]),
                capture_output=True, check=False, timeout=10,
            )
            oracle_run = subprocess.run(
                [sys.executable, str(main_py)], capture_output=True, check=False, timeout=10,
            )
            native_stdout = native_run.stdout.replace(b"\r\n", b"\n")
            oracle_stdout = oracle_run.stdout.replace(b"\r\n", b"\n")
            self.assertEqual(
                (native_run.returncode, native_stdout),
                (oracle_run.returncode, oracle_stdout),
                native_run.stderr,
            )

    def test_linux_star_from_dotted_submodule_equiv(self):
        init = ""
        submodules = {
            "tools": "funcion suma(a, b):\n    devolver a + b\nfuncion producto(a, b):\n    devolver a * b\n",
        }
        main = (
            "desde pkg.tools importar *\n"
            "imprimir(suma(40, 2))\n"
            "imprimir(producto(6, 7))\n"
        )
        self._assert_package_equiv(init, submodules, main)

    def test_linux_star_relative_in_entry_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-star-entry-") as directory:
            root = Path(directory)
            entry = root / "main.piton"
            entry.write_text("desde . importar *\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "no parent package"):
                compile_native_linux_files(entry, root / "program")

    def test_linux_star_in_package_init_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-star-init-") as directory:
            root = Path(directory)
            pkg_dir = root / "pkg"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.piton").write_text("desde . importar *\n", encoding="utf-8")
            entry = root / "main.piton"
            entry.write_text("importar pkg\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "entry module"):
                compile_native_linux_files(entry, root / "program")

    def test_linux_star_from_builtin_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-star-builtin-") as directory:
            root = Path(directory)
            entry = root / "main.piton"
            entry.write_text("desde math importar *\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "builtin modules"):
                compile_native_linux_files(entry, root / "program")

    # ── IMPORT_CYCLIC_V1 (Linux mirror) ─────────────────────────────────

    def _assert_sibling_equiv_linux(self, module_files, main):
        with tempfile.TemporaryDirectory(prefix="piton-linux-cycle-") as directory:
            root = Path(directory)
            for mod_name, src in module_files.items():
                (root / f"{mod_name}.piton").write_text(src, encoding="utf-8")
                (root / f"{mod_name}.py").write_text(traducir_fuente(src, f"<{mod_name}>"), encoding="utf-8")
            (root / "main.piton").write_text(main, encoding="utf-8")
            (root / "main.py").write_text(traducir_fuente(main, "<main>"), encoding="utf-8")
            executable = compile_native_linux_files(root / "main.piton", root / "program")
            native_run = subprocess.run(
                linux_run_cmd([windows_to_wsl_path(executable)]),
                capture_output=True, check=False, timeout=10,
            )
            oracle_run = subprocess.run(
                [sys.executable, str(root / "main.py")], capture_output=True, check=False, timeout=10,
            )
            self.assertEqual(
                (native_run.returncode, native_run.stdout.replace(b"\r\n", b"\n")),
                (oracle_run.returncode, oracle_run.stdout.replace(b"\r\n", b"\n")),
                native_run.stderr,
            )

    def test_linux_cyclic_mutual_from_import_ok(self):
        modules = {
            "a": (
                "funcion fa(a):\n"
                "    devolver a + 1\n"
                "desde b importar fb\n"
                "funcion fab(a):\n"
                "    devolver fb(a) + 10\n"
            ),
            "b": (
                "funcion fb(a):\n"
                "    devolver a * 2\n"
                "desde a importar fa\n"
                "funcion fba(a):\n"
                "    devolver fa(a) + 100\n"
            ),
        }
        main = (
            "importar a\n"
            "importar b\n"
            "imprimir(a.fa(1))\n"
            "imprimir(a.fab(10))\n"
            "imprimir(b.fb(5))\n"
            "imprimir(b.fba(3))\n"
        )
        self._assert_sibling_equiv_linux(modules, main)

    def test_linux_cyclic_from_import_defined_before_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-cycle-fail-") as directory:
            root = Path(directory)
            (root / "a.piton").write_text(
                "desde b importar fb\nfuncion fa(a):\n    devolver a\n", encoding="utf-8"
            )
            (root / "b.piton").write_text(
                "desde a importar fa\nfuncion fb(a):\n    devolver a\n", encoding="utf-8"
            )
            entry = root / "main.piton"
            entry.write_text("importar a\n", encoding="utf-8")
            with self.assertRaisesRegex(NativeBuildError, "partially initialized"):
                compile_native_linux_files(entry, root / "program")

    def test_linux_cyclic_same_module_bare_call_in_imported_module(self):
        modules = {
            "m": (
                "funcion doble(x):\n"
                "    devolver x * 2\n"
                "funcion cuadrado(x):\n"
                "    devolver doble(x) * doble(x)\n"
            ),
        }
        main = "importar m\nimprimir(m.cuadrado(3))\n"
        self._assert_sibling_equiv_linux(modules, main)

    def test_linux_cyclic_plain_import_cycle_ok(self):
        modules = {
            "a": "importar b\nfuncion fa(x):\n    devolver b.fb(x) + 1\n",
            "b": "importar a\nfuncion fb(x):\n    devolver x * 3\n",
        }
        main = "importar a\nimprimir(a.fa(7))\n"
        self._assert_sibling_equiv_linux(modules, main)

    def test_linux_descriptors_property_get_set_del(self):
        source = (
            'clase P:\n'
            '    funcion __init__(self):\n'
            '        self.campos = 0\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver self.campos\n'
            '    @x.setter\n'
            '    funcion x(self, v):\n'
            '        self.campos = v\n'
            '    @x.deleter\n'
            '    funcion x(self):\n'
            '        self.campos = -1\n'
            'p = P()\n'
            'p.x = 5\n'
            'imprimir(p.x)\n'
            'borrar p.x\n'
            'imprimir(p.x)\n'
        )
        self._assert_linux_equiv(source)

    def test_linux_descriptors_property_inheritance(self):
        source = (
            'clase Base:\n'
            '    funcion __init__(self):\n'
            '        self.campos = 3\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver self.campos\n'
            '    @x.setter\n'
            '    funcion x(self, v):\n'
            '        self.campos = v\n'
            'clase Hija(Base):\n'
            '    pasar\n'
            'h = Hija()\n'
            'imprimir(h.x)\n'
            'h.x = 9\n'
            'imprimir(h.x)\n'
        )
        self._assert_linux_equiv(source)

    def test_linux_descriptors_set_on_getter_only_fails_closed(self):
        source = (
            'clase P:\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver 1\n'
            'p = P()\n'
            'p.x = 5\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-linux-prop-set-fail-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "property 'x' of 'P' object has no setter"):
                compile_native_linux(source, Path(directory) / "program")

    def test_linux_descriptors_del_on_getter_only_fails_closed(self):
        source = (
            'clase P:\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver 1\n'
            'p = P()\n'
            'borrar p.x\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-linux-prop-del-fail-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "property 'x' of 'P' object has no deleter"):
                compile_native_linux(source, Path(directory) / "program")

    def test_linux_descriptors_property_call_fails_closed(self):
        source = (
            'clase P:\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver 1\n'
            'p = P()\n'
            'imprimir(p.x())\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-linux-prop-call-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "not a method"):
                compile_native_linux(source, Path(directory) / "program")

    def test_linux_descriptors_setter_without_getter_fails_closed(self):
        source = (
            'clase P:\n'
            '    @x.setter\n'
            '    funcion x(self, v):\n'
            '        pass\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-linux-prop-err-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "requires the property getter"):
                compile_native_linux(source, Path(directory) / "program")

    def test_linux_descriptors_unknown_decorator_fails_closed(self):
        source = (
            'clase P:\n'
            '    @miclase\n'
            '    funcion x(self):\n'
            '        devolver 1\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-linux-prop-dec-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "only @property"):
                compile_native_linux(source, Path(directory) / "program")

    def test_linux_descriptors_duplicate_property_fails_closed(self):
        source = (
            'clase P:\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver 1\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver 2\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-linux-prop-dup-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "duplicate"):
                compile_native_linux(source, Path(directory) / "program")

    def test_linux_descriptors_del_non_property_fails_closed(self):
        source = (
            'clase P:\n'
            '    funcion __init__(self):\n'
            '        self.campos = 0\n'
            '    @property\n'
            '    funcion x(self):\n'
            '        devolver self.campos\n'
            '    @x.setter\n'
            '    funcion x(self, v):\n'
            '        self.campos = v\n'
            '    @x.deleter\n'
            '    funcion x(self):\n'
            '        self.campos = -1\n'
            'p = P()\n'
            'borrar p.campos\n'
        )
        with tempfile.TemporaryDirectory(prefix="piton-linux-del-nonprop-") as directory:
            with self.assertRaisesRegex(NativeBuildError, "is not a property"):
                compile_native_linux(source, Path(directory) / "program")

    def test_linux_for_break(self):
        source = (
            'suma = 0\n'
            'para i en [1, 2, 3, 4, 5]:\n'
            '    si i == 3:\n'
            '        romper\n'
            '    suma = suma + i\n'
            'imprimir(suma)\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_for_continue(self):
        source = (
            'suma = 0\n'
            'para i en [1, 2, 3, 4, 5]:\n'
            '    si i == 3:\n'
            '        continuar\n'
            '    suma = suma + i\n'
            'imprimir(suma)\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_for_else_no_break(self):
        source = (
            'para i en [1, 2, 3]:\n'
            '    imprimir(i)\n'
            'sino:\n'
            '    imprimir("else")\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_for_else_with_break(self):
        source = (
            'para i en [1, 2, 3]:\n'
            '    si i == 2:\n'
            '        romper\n'
            '    imprimir(i)\n'
            'sino:\n'
            '    imprimir("else")\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_for_str(self):
        source = (
            'total = ""\n'
            'para c en "hola":\n'
            '    total = total + c\n'
            'imprimir(total)\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_for_str_else(self):
        source = (
            'para c en "ab":\n'
            '    imprimir(c)\n'
            'sino:\n'
            '    imprimir("done")\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_for_str_break(self):
        source = (
            'para c en "abcde":\n'
            '    si c == "c":\n'
            '        romper\n'
            '    imprimir(c)\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_for_range(self):
        source = (
            'suma = 0\n'
            'para i en rango(5):\n'
            '    suma = suma + i\n'
            'imprimir(suma)\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_for_range_else(self):
        source = (
            'para i en rango(3):\n'
            '    imprimir(i)\n'
            'sino:\n'
            '    imprimir("else")\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_for_range_break(self):
        source = (
            'para i en rango(10):\n'
            '    si i == 3:\n'
            '        romper\n'
            '    imprimir(i)\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_while_break(self):
        source = (
            'i = 0\n'
            'mientras i < 5:\n'
            '    si i == 3:\n'
            '        romper\n'
            '    i = i + 1\n'
            'imprimir(i)\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_while_continue(self):
        source = (
            'suma = 0\n'
            'i = 0\n'
            'mientras i < 5:\n'
            '    i = i + 1\n'
            '    si i == 3:\n'
            '        continuar\n'
            '    suma = suma + i\n'
            'imprimir(suma)\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_while_else_no_break(self):
        source = (
            'i = 0\n'
            'mientras i < 3:\n'
            '    i = i + 1\n'
            'sino:\n'
            '    imprimir("else")\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_while_else_with_break(self):
        source = (
            'i = 0\n'
            'mientras i < 5:\n'
            '    si i == 2:\n'
            '        romper\n'
            '    i = i + 1\n'
            'sino:\n'
            '    imprimir("else")\n'
            'imprimir(i)\n'
        )
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)


class ComprehensionsV2Linux(unittest.TestCase):
    def _run_linux_diff(self, source: str):
        translated = traducir_fuente(source, "<linux-diff>")
        with tempfile.TemporaryDirectory(prefix="piton-linux-comp-") as directory:
            executable = compile_native_linux(source, Path(directory) / "program")
            linux_path = windows_to_wsl_path(executable)
            native_run = subprocess.run(
                linux_run_cmd([linux_path]),
                capture_output=True, check=False, timeout=10,
            )
            oracle_run = subprocess.run(
                [sys.executable, "-c", translated],
                capture_output=True, check=False, timeout=10,
            )
            native_stdout = native_run.stdout.replace(b"\r\n", b"\n")
            oracle_stdout = oracle_run.stdout.replace(b"\r\n", b"\n")
            return native_run.returncode, native_stdout, oracle_stdout, native_run.stderr

    def assert_linux_matches(self, source: str):
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_list_comp_basic(self):
        self.assert_linux_matches(
            'cuadrados = [x * x para x en [1, 2, 3, 4, 5]]\nimprimir(cuadrados)\n'
        )

    def test_linux_list_comp_with_if(self):
        self.assert_linux_matches(
            'pares = [x para x en [1, 2, 3, 4, 5, 6] si x % 2 == 0]\nimprimir(pares)\n'
        )

    def test_linux_list_comp_chained_for(self):
        self.assert_linux_matches(
            'pairs = [(a, b) para a en [1, 2] para b en [3, 4]]\nimprimir(pairs)\n'
        )

    def test_linux_list_comp_with_tuple_element(self):
        self.assert_linux_matches(
            't = [(x, x * 2) para x en [1, 2, 3]]\nimprimir(t)\n'
        )

    def test_linux_list_of_tuples_literal(self):
        self.assert_linux_matches(
            't = [(1, 2), (3, 4)]\nimprimir(t)\n'
        )

    def test_linux_nested_list_literal(self):
        self.assert_linux_matches(
            't = [[1, 2], [3, 4]]\nimprimir(t)\n'
        )

    def test_linux_tuple_of_mixed_literal(self):
        self.assert_linux_matches(
            't = (1, [2, 3], (4, 5))\nimprimir(t)\n'
        )

    def test_linux_list_comp_nested(self):
        self.assert_linux_matches(
            't = [[x * z para z en [1, 2, 3]] para x en [1, 2]]\nimprimir(t)\n'
        )

    def test_linux_genexpr_is_iterable_and_stateful(self):
        self.assert_linux_matches(
            'g = (x * 2 para x en [1, 2, 3])\n'
            'imprimir(next(g))\n'
            'imprimir(next(iter(g)))\n'
        )

    def test_linux_genexpr_stop_iteration(self):
        self.assert_linux_matches(
            'g = (x para x en [7])\n'
            'intentar:\n'
            '    imprimir(next(g))\n'
            '    next(g)\n'
            'excepto StopIteration:\n'
            '    imprimir("exhausted")\n'
        )

    def test_linux_set_comp_basic_and_deduplicates(self):
        self.assert_linux_matches(
            's = {x para x en [1, 2, 2, 3]}\nimprimir(s)\n'
        )

    def test_linux_set_comp_with_if(self):
        self.assert_linux_matches(
            's = {x para x en [1, 2, 3, 4] si x % 2 == 0}\nimprimir(s)\n'
        )

    def test_linux_dict_comp_basic(self):
        self.assert_linux_matches(
            'd = {x: x * x para x en [1, 2, 3]}\nimprimir(d)\n'
        )

    def test_linux_dict_comp_with_if(self):
        self.assert_linux_matches(
            'd = {x: x * 2 para x en [1, 2, 3, 4] si x > 2}\nimprimir(d)\n'
        )


class GeneratorsLinux(unittest.TestCase):
    def _run_linux_diff(self, source: str):
        translated = traducir_fuente(source, "<linux-diff>")
        with tempfile.TemporaryDirectory(prefix="piton-linux-gen-") as directory:
            executable = compile_native_linux(source, Path(directory) / "program")
            linux_path = windows_to_wsl_path(executable)
            native_run = subprocess.run(
                linux_run_cmd([linux_path]),
                capture_output=True, check=False, timeout=10,
            )
            oracle_run = subprocess.run(
                [sys.executable, "-c", translated],
                capture_output=True, check=False, timeout=10,
            )
            native_stdout = native_run.stdout.replace(b"\r\n", b"\n")
            oracle_stdout = oracle_run.stdout.replace(b"\r\n", b"\n")
            return native_run.returncode, native_stdout, oracle_stdout, native_run.stderr

    def assert_linux_matches(self, source: str):
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def test_linux_generator_suspends_between_next_calls(self):
        self.assert_linux_matches(
            "funcion gen():\n"
            "    imprimir(10)\n"
            "    producir 1\n"
            "    imprimir(20)\n"
            "    producir 2\n"
            "x = gen()\n"
            "imprimir(next(x))\n"
            "imprimir(next(x))\n"
        )

    def test_linux_generator_with_params(self):
        self.assert_linux_matches(
            "funcion gen(n):\n"
            "    producir n\n"
            "    producir n + 1\n"
            "x = gen(5)\n"
            "imprimir(next(x))\n"
            "imprimir(next(x))\n"
        )

    def test_linux_generator_preserves_locals_between_yields(self):
        self.assert_linux_matches(
            "funcion gen():\n"
            "    valor = 1\n"
            "    producir valor\n"
            "    valor = valor + 1\n"
            "    producir valor\n"
            "x = gen()\n"
            "imprimir(next(x))\n"
            "imprimir(next(x))\n"
        )

    def test_linux_generator_exhaustion_raises_stop_iteration(self):
        self.assert_linux_matches(
            "funcion gen():\n"
            "    producir 1\n"
            "x = gen()\n"
            "imprimir(next(x))\n"
            "intentar:\n"
            "    next(x)\n"
            "excepto StopIteration:\n"
            "    imprimir(99)\n"
        )

    def test_linux_generator_instances_keep_independent_state(self):
        self.assert_linux_matches(
            "funcion gen(n):\n"
            "    producir n\n"
            "    producir n + 10\n"
            "x = gen(1)\n"
            "z = gen(2)\n"
            "imprimir(next(x))\n"
            "imprimir(next(z))\n"
            "imprimir(next(x))\n"
            "imprimir(next(z))\n"
        )

    def test_linux_generator_send_after_next(self):
        self.assert_linux_matches(
            "funcion echo():\n"
            "    producir 0\n"
            "    producir 1\n"
            "    producir 2\n"
            "x = echo()\n"
            "imprimir(next(x))\n"
            "imprimir(x.send(5))\n"
            "imprimir(x.send(7))\n"
            "intentar:\n"
            "    next(x)\n"
            "excepto StopIteration:\n"
            "    imprimir(99)\n"
        )

    def test_linux_generator_send_first_call_fails(self):
        self.assert_linux_matches(
            "funcion gen():\n"
            "    producir 1\n"
            "x = gen()\n"
            "intentar:\n"
            "    x.send(7)\n"
            "excepto TypeError:\n"
            "    imprimir(99)\n"
        )

    def test_linux_generator_throw_at_caller(self):
        self.assert_linux_matches(
            "funcion gen():\n"
            "    producir 1\n"
            "    producir 2\n"
            "x = gen()\n"
            "imprimir(next(x))\n"
            "intentar:\n"
            "    x.throw(ValueError)\n"
            "excepto ValueError:\n"
            "    imprimir(88)\n"
            "intentar:\n"
            "    next(x)\n"
            "excepto StopIteration:\n"
            "    imprimir(99)\n"
        )

    def test_linux_generator_close_stops_iteration(self):
        self.assert_linux_matches(
            "funcion gen():\n"
            "    producir 1\n"
            "    producir 2\n"
            "x = gen()\n"
            "imprimir(next(x))\n"
            "x.close()\n"
            "intentar:\n"
            "    next(x)\n"
            "excepto StopIteration:\n"
            "    imprimir(99)\n"
        )

    def test_linux_generator_close_is_idempotent(self):
        self.assert_linux_matches(
            "funcion gen():\n"
            "    producir 1\n"
            "x = gen()\n"
            "x.close()\n"
            "x.close()\n"
            "imprimir(7)\n"
        )

    def test_linux_yield_from_delegates_subgen_values(self):
        self.assert_linux_matches(
            "funcion sub():\n"
            "    producir 1\n    producir 2\n"
            "funcion outer():\n"
            "    producir desde sub()\n    producir 9\n"
            "g = outer()\n"
            "imprimir(next(g))\nimprimir(next(g))\nimprimir(next(g))\n"
        )

    def test_linux_yield_from_sends_forward(self):
        self.assert_linux_matches(
            "funcion sub():\n"
            "    producir 1\n    producir 2\n"
            "funcion outer():\n"
            "    producir desde sub()\n"
            "g = outer()\n"
            "imprimir(next(g))\nimprimir(g.send(5))\n"
        )

    def test_linux_generator_return_value_completes(self):
        self.assert_linux_matches(
            "funcion sub():\n"
            "    producir 1\n"
            "    devolver 99\n"
            "funcion outer():\n"
            "    producir desde sub()\n"
            "    producir 7\n"
            "g = outer()\n"
            "imprimir(next(g))\nimprimir(next(g))\n"
        )

    def test_linux_exception_binding_as_name(self):
        self.assert_linux_matches(
            'intentar:\n'
            '    lanzar ValueError("boom")\n'
            'excepto ValueError como e:\n'
            '    imprimir(e)\n'

        )

    def test_linux_async_raise_with_cause_chain_caught(self):
        self.assert_linux_matches(
            'intentar:\n'
            '    lanzar ValueError("externo") desde TypeError("causa")\n'
            'excepto ValueError como e:\n'
            '    imprimir(e)\n'
        )

    def test_linux_base_exception_catches_anything(self):
        self.assert_linux_matches(
            'intentar:\n    lanzar TypeError("c1")\nexcepto BaseException como e:\n    imprimir("caught")\n'
        )

    def test_linux_bare_reraise_from_catchall_handler(self):
        self.assert_linux_matches(
            'intentar:\n'
            '    intentar:\n'
            '        lanzar ValueError("boom2")\n'
            '    excepto Exception:\n'
            '        lanzar\n'
            'excepto ValueError como e:\n'
            '    imprimir(e)\n'
        )

    def test_linux_exception_binding_as_name_no_message(self):
        self.assert_linux_matches(
            'intentar:\n'
            '    lanzar ValueError()\n'
            'excepto ValueError como e:\n'
            '    imprimir(e)\n'
        )


class CoroutinesLinux(unittest.TestCase):
    def assert_linux_matches(self, source: str):
        rc, native, oracle, stderr = self._run_linux_diff(source)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(native, oracle)

    def _run_linux_diff(self, source):
        with tempfile.TemporaryDirectory(prefix="piton-linux-async-") as directory:
            root = Path(directory)
            (root / "main.piton").write_text(source, encoding="utf-8")
            (root / "main.py").write_text(traducir_fuente(source, "<main>"), encoding="utf-8")
            executable = compile_native_linux(source, root / "program")
            native_run = subprocess.run(
                ["wsl.exe", "/usr/bin/env", "-i", windows_to_wsl_path(executable)],
                capture_output=True, check=False, timeout=10,
            )
            oracle_run = subprocess.run(
                [sys.executable, str(root / "main.py")], capture_output=True, check=False, timeout=10,
            )
            return (
                native_run.returncode,
                native_run.stdout.replace(b"\r\n", b"\n"),
                oracle_run.stdout.replace(b"\r\n", b"\n"),
                native_run.stderr,
            )

    def test_linux_async_nested_await(self):
        self.assert_linux_matches(
            "importar asyncio\n"
            "asincrono funcion interior():\n"
            "    devolver 10\n"
            "asincrono funcion exterior():\n"
            "    v = esperar interior()\n"
            "    imprimir(v)\n"
            "asyncio.run(exterior())\n"
        )

    def test_linux_async_with_computation(self):
        self.assert_linux_matches(
            "importar asyncio\n"
            "asincrono funcion calcular(n):\n"
            "    devolver n * n + 1\n"
            "asincrono funcion principal():\n"
            "    r = esperar calcular(5)\n"
            "    imprimir(r)\n"
            "asyncio.run(principal())\n"
        )

    def test_linux_async_deep_chain_with_params(self):
        self.assert_linux_matches(
            "importar asyncio\n"
            "asincrono funcion base(n):\n"
            "    devolver n + 1\n"
            "asincrono funcion medio(n):\n"
            "    a = esperar base(n)\n"
            "    devolver a * 2\n"
            "asincrono funcion cima(n):\n"
            "    b = esperar medio(n)\n"
            "    c = esperar base(b)\n"
            "    devolver c + 100\n"
            "asincrono funcion principal():\n"
            "    imprimir(esperar cima(5))\n"
            "asyncio.run(principal())\n"
        )


    def test_linux_async_generator_async_for(self):
        self.assert_linux_matches(
            "importar asyncio\n"
            "asincrono funcion contar():\n"
            "    producir 1\n"
            "    producir 2\n"
            "    producir 3\n"
            "asincrono funcion principal():\n"
            "    asincrono para x en contar():\n"
            "        imprimir(x)\n"
            "asyncio.run(principal())\n"
        )

    def test_linux_async_generator_params_and_await_inside(self):
        self.assert_linux_matches(
            "importar asyncio\n"
            "asincrono funcion base(n):\n"
            "    devolver n + 1\n"
            "asincrono funcion contar(hasta):\n"
            "    producir 1\n"
            "    producir 2\n"
            "    v = esperar base(10)\n"
            "    producir v\n"
            "    producir 3\n"
            "asincrono funcion principal():\n"
            "    asincrono para x en contar(4):\n"
            "        imprimir(x)\n"
            "asyncio.run(principal())\n"
        )

    def test_linux_async_generator_for_else(self):
        self.assert_linux_matches(
            "importar asyncio\n"
            "asincrono funcion contar():\n"
            "    producir 1\n"
            "    producir 2\n"
            "asincrono funcion principal():\n"
            "    asincrono para x en contar():\n"
            "        imprimir(x)\n"
            "    sino:\n"
            "        imprimir(99)\n"
            "asyncio.run(principal())\n"
        )

    # ── TASK_SCHEDULER_V1 (M9) mirrors: create_task / gather / sleep(0) / cancel ──

    def test_linux_task_create_await_and_result(self):
        self.assert_linux_matches(
            "importar asyncio\n"
            "asincrono funcion saluda(n):\n"
            "    esperar asyncio.sleep(0)\n"
            "    devolver n * 10\n"
            "asincrono funcion principal():\n"
            "    t1 = asyncio.create_task(saluda(1))\n"
            "    imprimir(esperar t1)\n"
            "asyncio.run(principal())\n"
        )

    def test_linux_task_gather_direct_calls(self):
        self.assert_linux_matches(
            "importar asyncio\n"
            "asincrono funcion saluda(n):\n"
            "    esperar asyncio.sleep(0)\n"
            "    devolver n * 10\n"
            "asincrono funcion principal():\n"
            "    imprimir(esperar asyncio.gather(saluda(2), saluda(3)))\n"
            "asyncio.run(principal())\n"
        )

    def test_linux_task_gather_sleep0_interleave_order(self):
        self.assert_linux_matches(
            "importar asyncio\n"
            "asincrono funcion t(n):\n"
            "    esperar asyncio.sleep(0)\n"
            "    esperar asyncio.sleep(0)\n"
            "    devolver n\n"
            "asincrono funcion principal():\n"
            "    a = asyncio.create_task(t(1))\n"
            "    b = asyncio.create_task(t(2))\n"
            "    c = asyncio.create_task(t(3))\n"
            "    imprimir(esperar asyncio.gather(a, b, c))\n"
            "asyncio.run(principal())\n"
        )

    def test_linux_task_gather_already_finished_tasks(self):
        self.assert_linux_matches(
            "importar asyncio\n"
            "asincrono funcion s(n):\n"
            "    devolver n * 10\n"
            "asincrono funcion principal():\n"
            "    t1 = asyncio.create_task(s(1))\n"
            "    imprimir(esperar t1)\n"
            "    t2 = asyncio.create_task(s(2))\n"
            "    imprimir(esperar t2)\n"
            "    imprimir(esperar asyncio.gather(t1, t2))\n"
            "asyncio.run(principal())\n"
        )

    def test_linux_task_await_chain_with_sleep0(self):
        self.assert_linux_matches(
            "importar asyncio\n"
            "asincrono funcion hoja(n):\n"
            "    esperar asyncio.sleep(0)\n"
            "    devolver n + 1\n"
            "asincrono funcion medio():\n"
            "    x = esperar hoja(10)\n"
            "    esperar asyncio.sleep(0)\n"
            "    devolver x * 2\n"
            "asincrono funcion principal():\n"
            "    imprimir(esperar medio())\n"
            "asyncio.run(principal())\n"
        )

    def _run_linux_expected_fail(self, source: str, fragment: str) -> None:
        with tempfile.TemporaryDirectory(prefix="piton-linux-async-") as directory:
            root = Path(directory)
            executable = compile_native_linux(source, root / "program")
            native_run = subprocess.run(
                ["wsl.exe", "/usr/bin/env", "-i", windows_to_wsl_path(executable)],
                capture_output=True, check=False, timeout=10,
            )
            self.assertNotEqual(native_run.returncode, 0)
            self.assertIn(fragment.encode(), native_run.stderr)

    def test_linux_task_cancel_await_propagates(self):
        self._run_linux_expected_fail(
            "importar asyncio\n"
            "asincrono funcion s(n):\n"
            "    devolver n\n"
            "asincrono funcion principal():\n"
            "    t1 = asyncio.create_task(s(1))\n"
            "    t1.cancel()\n"
            "    imprimir(esperar t1)\n"
            "asyncio.run(principal())\n",
            "CancelledError",
        )

    def test_linux_task_cancel_gather_member_propagates(self):
        self._run_linux_expected_fail(
            "importar asyncio\n"
            "asincrono funcion s(n):\n"
            "    devolver n\n"
            "asincrono funcion principal():\n"
            "    t1 = asyncio.create_task(s(1))\n"
            "    t1.cancel()\n"
            "    imprimir(esperar asyncio.gather(t1))\n"
            "asyncio.run(principal())\n",
            "CancelledError",
        )

    def test_linux_task_sleep1_uses_real_timer(self):
        returncode, stdout, oracle, stderr = self._run_linux_diff(
            "importar asyncio\n"
            "asincrono funcion p():\n"
            "    esperar asyncio.sleep(1)\n"
            "    devolver 1\n"
            "imprimir(asyncio.run(p()))\n",
        )
        self.assertEqual((returncode, stdout, oracle, stderr), (0, b"1\n", b"1\n", b""))

    def test_linux_task_gather_non_task_fails_closed(self):
        self._run_linux_expected_fail(
            "importar asyncio\n"
            "asincrono funcion p():\n"
            "    imprimir(esperar asyncio.gather(5))\n"
            "asyncio.run(p())\n",
            "gather requires tasks",
        )

    def test_linux_task_await_non_awaitable_fails_closed(self):
        self._run_linux_expected_fail(
            "importar asyncio\n"
            "asincrono funcion p():\n"
            "    esperar 42\n"
            "asyncio.run(p())\n",
            "object is not awaitable",
        )


class WithProtocolLinuxV1(unittest.TestCase):
    """M10 — WITH_PROTOCOL_V1 on the static-ELF Linux backend, differential vs
    CPython 3.12 (mirror of WithProtocolNativeV1 in test_phase5.py)."""

    def _run_linux_diff(self, source):
        translated = traducir_fuente(source, "<linux-with>")
        with tempfile.TemporaryDirectory(prefix="piton-linux-with-") as directory:
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
            native_stdout = native_run.stdout.replace(b"\r\n", b"\n")
            oracle_stdout = oracle_run.stdout.replace(b"\r\n", b"\n")
            return native_run.returncode, native_stdout, oracle_stdout, native_run.stderr

    def test_linux_with_normal_path(self):
        rc, out, oracle_out, err = self._run_linux_diff(
            'clase CM:\n'
            '    funcion __enter__(self):\n'
            '        imprimir("enter")\n'
            '        devolver 42\n'
            '    funcion __exit__(self, tipo, mensaje, tb):\n'
            '        imprimir("exit")\n'
            '        devolver Falso\n'
            'con CM() como x:\n'
            '    imprimir("body")\n'
            '    imprimir(x)\n'
        )
        self.assertEqual((rc, out), (0, oracle_out), err)
        self.assertEqual(err, b"")

    def test_linux_m13_self_cycle_is_safe(self):
        """M13 cycle fixture: mutual object references must not corrupt teardown."""
        rc, out, oracle_out, err = self._run_linux_diff(
            'clase Nodo:\n'
            '    funcion __init__(self):\n'
            '        self.ref = self\n'
            'a = Nodo()\n'
            'b = Nodo()\n'
            'a.ref = b\n'
            'b.ref = a\n'
            'imprimir("ok")\n'
        )
        self.assertEqual((rc, out), (0, oracle_out), err)
        self.assertEqual(err, b"")

    def test_linux_with_exception_propagates_to_handler(self):
        rc, out, oracle_out, err = self._run_linux_diff(
            'clase CM:\n'
            '    funcion __enter__(self):\n'
            '        imprimir("enter")\n'
            '        devolver 1\n'
            '    funcion __exit__(self, tipo, mensaje, tb):\n'
            '        imprimir("exit")\n'
            '        imprimir(mensaje)\n'
            '        devolver Falso\n'
            'intentar:\n'
            '    con CM() como w:\n'
            '        lanzar ValueError("boom")\n'
            'excepto ValueError:\n'
            '    imprimir("handler")\n'
        )
        self.assertEqual((rc, out), (0, oracle_out), err)
        self.assertEqual(err, b"")

    def test_linux_with_suppression(self):
        rc, out, oracle_out, err = self._run_linux_diff(
            'clase CM:\n'
            '    funcion __enter__(self):\n'
            '        devolver 1\n'
            '    funcion __exit__(self, tipo, mensaje, tb):\n'
            '        imprimir("suprimo")\n'
            '        devolver Verdadero\n'
            'intentar:\n'
            '    con CM() como z:\n'
            '        lanzar ValueError("boom")\n'
            'excepto ValueError:\n'
            '    imprimir("handler")\n'
            'imprimir("fin")\n'
        )
        self.assertEqual((rc, out), (0, oracle_out), err)
        self.assertEqual(err, b"")

    def test_linux_with_unhandled_exception(self):
        rc, out, oracle_out, err = self._run_linux_diff(
            'clase CM:\n'
            '    funcion __enter__(self):\n'
            '        devolver 1\n'
            '    funcion __exit__(self, tipo, mensaje, tb):\n'
            '        imprimir("exit")\n'
            '        imprimir(tipo)\n'
            '        imprimir(mensaje)\n'
            '        devolver Falso\n'
            'con CM() como x:\n'
            '    lanzar ValueError("boom")\n'
        )
        self.assertEqual(rc, 1, err)
        self.assertIn(b"ValueError: boom", err)
        self.assertIn(b"exit", out)
        self.assertIn(b"ValueError", out)
        self.assertIn(b"boom", out)

    def test_linux_with_requires_dunder_methods(self):
        with tempfile.TemporaryDirectory(prefix="piton-linux-with-") as directory:
            with self.assertRaisesRegex(Exception, "must define __enter__ and __exit__"):
                compile_native_linux(
                    'clase CM:\n    funcion __init__(self):\n        self.x = 1\n'
                    'con CM() como y:\n    imprimir(y)\n',
                    Path(directory) / "program",
                )


class FinalizersLinuxV1(unittest.TestCase):
    """M13 FINALIZERS_V1 on Linux: __del__ runs once before process exit."""

    def _run_linux_diff(self, source):
        translated = traducir_fuente(source, "<linux-finalizer>")
        with tempfile.TemporaryDirectory(prefix="piton-linux-finalizer-") as directory:
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
            native_stdout = native_run.stdout.replace(b"\r\n", b"\n")
            oracle_stdout = oracle_run.stdout.replace(b"\r\n", b"\n")
            return native_run.returncode, native_stdout, oracle_stdout, native_run.stderr

    def test_linux_finalizer_runs_at_exit(self):
        rc, out, oracle_out, err = self._run_linux_diff(
            'clase Recurso:\n'
            '    funcion __del__(self):\n'
            '        imprimir("cerrado")\n'
            'r = Recurso()\n'
            'imprimir("listo")\n'
        )
        self.assertEqual((rc, out), (0, oracle_out), err)
        self.assertEqual(err, b"")

    def test_linux_finalizer_runs_once_for_self_cycle(self):
        rc, out, oracle_out, err = self._run_linux_diff(
            'clase Nodo:\n'
            '    funcion __init__(self):\n'
            '        self.ref = self\n'
            '    funcion __del__(self):\n'
            '        imprimir("del")\n'
            'Nodo()\n'
            'imprimir("ok")\n'
        )
        self.assertEqual((rc, out), (0, oracle_out), err)
        self.assertEqual(err, b"")


class CycleGCLinuxV1(unittest.TestCase):
    """M13 GC_CYCLES_V1 on Linux: self-contained C-harness cycle collection test."""

    # Self-contained freestanding C program that embeds the heap allocator,
    # GC registry, refcounting, and cycle collector.  Compiled with gcc as
    # a normal userspace binary (not -nostdlib) so write()/exit() resolve
    # via libc.
    _HARNESS_C = r'''
#include <stdint.h>
#include <unistd.h>
#include <stdlib.h>

typedef unsigned long usize;
typedef struct{long bits;int kind;}PitonSlot;
#define PK_NONE 0
#define PK_INT  1
#define PK_LIST 1
#define PK_DICT 2
#define PK_SET  3
#define PK_OBJECT 4
#define PK_TUPLE 5
#define PK_STR 6

static void piton_memzero(void*p,usize n){unsigned char*c=p;for(usize i=0;i<n;++i)c[i]=0;}
static void piton_memcpy(void*d,const void*s,usize n){unsigned char*a=d;const unsigned char*b=s;for(usize i=0;i<n;++i)a[i]=b[i];}
static int piton_strcmp(const char*a,const char*b){while(*a&&*b){if(*a!=*b)return(*a>*b)?1:-1;++a;++b;}return(*a==*b)?0:((*a>*b)?1:-1);}

static unsigned char piton_heap_data[4*1024*1024];
static long piton_heap_inited=0;
typedef struct piton_heap_block{usize size;int free;struct piton_heap_block*next;}piton_heap_block;
static piton_heap_block*piton_heap_freelist=0;
static void piton_heap_init(void){
    if(piton_heap_inited)return;piton_heap_inited=1;
    piton_heap_freelist=(piton_heap_block*)piton_heap_data;
    piton_heap_freelist->size=sizeof(piton_heap_data)-sizeof(piton_heap_block);
    piton_heap_freelist->free=1;piton_heap_freelist->next=0;}
static void*piton_heap_alloc(usize n){
    if(!piton_heap_inited)piton_heap_init();
    usize req=n+sizeof(piton_heap_block);if(req<16)req=16;
    piton_heap_block**prev=&piton_heap_freelist;piton_heap_block*cur=piton_heap_freelist;
    while(cur){
        if(cur->free&&cur->size>=req){
            if(cur->size>=req+sizeof(piton_heap_block)+16){
                piton_heap_block*rest=(piton_heap_block*)((char*)cur+req);
                rest->size=cur->size-req;rest->free=1;rest->next=cur->next;*prev=rest;
            }else{*prev=cur->next;}
            cur->free=0;cur->next=0;piton_memzero((char*)cur+sizeof(piton_heap_block),n);return(char*)cur+sizeof(piton_heap_block);}
        prev=&cur->next;cur=cur->next;}
    write(2,"MemoryError: GC heap exhausted\n",31);exit(1);return 0;}
static void piton_heap_free(void*ptr){
    if(!ptr)return;piton_heap_block*b=(piton_heap_block*)((char*)ptr-sizeof(piton_heap_block));
    b->free=1;b->next=piton_heap_freelist;piton_heap_freelist=b;}

typedef struct{long refcount;long kind;long length;long capacity;PitonSlot*items;}PitonSeq;
typedef struct{PitonSlot key;PitonSlot value;}PitonDictEntry;
typedef struct{long refcount;long kind;long length;long capacity;PitonDictEntry*items;}PitonDict;
typedef struct{long refcount;long kind;long length;long capacity;PitonSlot*items;}PitonSet;
typedef struct{const char*name;PitonSlot value;}PitonAttr;
typedef struct PitonObject{long refcount;long kind;const char*class_name;const char*parent_name;long length;PitonAttr attrs[32];long finalizer;long finalizer_called;struct PitonObject*next_all;}PitonObject;

static void**gc_nodes=0;static long gc_count=0;static long gc_capacity=0;
static long live_collections=0;static long live_dicts=0;static long live_sets=0;static long live_objects=0;
static void piton_gc_register(void*raw){
    if(!raw)return;for(long i=0;i<gc_count;++i)if(gc_nodes[i]==raw)return;
    if(gc_count>=gc_capacity){long nc=gc_capacity?gc_capacity*2:16;
        void**g=(void**)piton_heap_alloc((usize)nc*sizeof(void*));
        if(gc_count)piton_memcpy(g,gc_nodes,(usize)gc_count*sizeof(void*));gc_nodes=g;gc_capacity=nc;}
    gc_nodes[gc_count++]=raw;}
static void piton_gc_unregister(void*raw){
    for(long i=0;i<gc_count;++i){if(gc_nodes[i]!=raw)continue;gc_nodes[i]=gc_nodes[--gc_count];return;}}

static void piton_slot_incref(PitonSlot v){if(v.kind>=PK_LIST&&v.kind<=PK_OBJECT){long*rc=(long*)v.bits;++(*rc);}}
static void piton_slot_decref(PitonSlot v);
static void piton_slot_decref(PitonSlot v){
    if(v.kind<PK_LIST||v.kind>PK_OBJECT)return;long*rc=(long*)v.bits;if(!rc)return;
    if(--(*rc)>0)return;
    switch(v.kind){
    case PK_LIST:case PK_TUPLE:{PitonSeq*s=(PitonSeq*)v.bits;
        for(long i=0;i<s->length;++i)piton_slot_decref(s->items[i]);
        piton_gc_unregister(s);piton_heap_free(s);--live_collections;break;}
    case PK_DICT:{PitonDict*d=(PitonDict*)v.bits;
        for(long i=0;i<d->length;++i){piton_slot_decref(d->items[i].key);piton_slot_decref(d->items[i].value);}
        piton_gc_unregister(d);piton_heap_free(d);--live_dicts;break;}
    case PK_SET:{PitonSet*s=(PitonSet*)v.bits;
        for(long i=0;i<s->length;++i)piton_slot_decref(s->items[i]);
        piton_gc_unregister(s);piton_heap_free(s);--live_sets;break;}
    case PK_OBJECT:{PitonObject*o=(PitonObject*)v.bits;
        for(long i=0;i<o->length;++i)piton_slot_decref(o->attrs[i].value);
        piton_gc_unregister(o);piton_heap_free(o);--live_objects;break;}
    default:break;}}

static void piton_gc_detach_node(void*raw){
    if(!raw)return;long kind=((long*)raw)[1];PitonSlot none={0,PK_NONE};
    switch(kind){
    case PK_LIST:case PK_TUPLE:{PitonSeq*s=(PitonSeq*)raw;
        for(long i=0;i<s->length;++i){PitonSlot old=s->items[i];s->items[i]=none;piton_slot_decref(old);}break;}
    case PK_DICT:{PitonDict*d=(PitonDict*)raw;
        for(long i=0;i<d->length;++i){PitonSlot ok=d->items[i].key,ov=d->items[i].value;
            d->items[i].key=none;d->items[i].value=none;piton_slot_decref(ok);piton_slot_decref(ov);}break;}
    case PK_SET:{PitonSet*s=(PitonSet*)raw;
        for(long i=0;i<s->length;++i){PitonSlot old=s->items[i];s->items[i]=none;piton_slot_decref(old);}break;}
    case PK_OBJECT:{PitonObject*o=(PitonObject*)raw;
        for(long i=0;i<o->length;++i){PitonSlot old=o->attrs[i].value;o->attrs[i].value=none;piton_slot_decref(old);}break;}
    default:break;}}
static void piton_gc_free_node(void*raw){
    if(!raw)return;long kind=((long*)raw)[1];piton_gc_unregister(raw);
    switch(kind){
    case PK_LIST:case PK_TUPLE:{piton_heap_free(raw);--live_collections;break;}
    case PK_DICT:{piton_heap_free(raw);--live_dicts;break;}
    case PK_SET:{piton_heap_free(raw);--live_sets;break;}
    case PK_OBJECT:{piton_heap_free(raw);--live_objects;break;}
    default:break;}}
void piton_gc_collect(void){
    long n=gc_count;if(!n)return;
    void**snapshot=(void**)piton_heap_alloc((usize)n*sizeof(void*));
    piton_memcpy(snapshot,gc_nodes,(usize)n*sizeof(void*));
    for(long i=0;i<n;++i){long*k=(long*)snapshot[i];++(*k);}
    for(long i=0;i<n;++i)piton_gc_detach_node(snapshot[i]);
    for(long i=0;i<n;++i)piton_gc_free_node(snapshot[i]);
    piton_heap_free(snapshot);}
long piton_total_live_count(void){return live_collections+live_dicts+live_sets+live_objects;}

void*piton_collection_new(long kind,long cap){
    piton_heap_init();PitonSeq*s=(PitonSeq*)piton_heap_alloc(sizeof(PitonSeq));
    s->refcount=1;s->kind=kind;s->length=0;s->capacity=cap;
    s->items=cap>0?(PitonSlot*)piton_heap_alloc((usize)cap*sizeof(PitonSlot)):0;
    piton_gc_register(s);++live_collections;return s;}
void piton_list_append(void*raw,long val,long tag){
    PitonSeq*s=(PitonSeq*)raw;if(!s)return;
    if(s->length>=s->capacity){long nc=s->capacity?s->capacity*2:4;
        PitonSlot*na=(PitonSlot*)piton_heap_alloc((usize)nc*sizeof(PitonSlot));
        if(s->items){for(long i=0;i<s->length;++i)na[i]=s->items[i];}
        s->items=na;s->capacity=nc;}
    PitonSlot v={val,(int)tag};piton_slot_incref(v);s->items[s->length++]=v;}
void piton_collection_free(void*raw){
    if(!raw)return;PitonSlot v={0,PK_NONE};
    long kind=((long*)raw)[1];v.bits=(long)raw;v.kind=kind;piton_slot_decref(v);}
void*piton_object_new(const char*name){
    piton_heap_init();PitonObject*o=(PitonObject*)piton_heap_alloc(sizeof(PitonObject));
    o->refcount=1;o->kind=PK_OBJECT;o->class_name=name;o->parent_name=0;o->length=0;
    o->finalizer=0;o->finalizer_called=0;o->next_all=0;
    piton_gc_register(o);++live_objects;return o;}
void piton_object_set_tagged(void*raw,const char*name,long val,long tag){
    PitonObject*o=(PitonObject*)raw;if(!raw)return;
    for(long i=0;i<o->length;++i){
        if(piton_strcmp(o->attrs[i].name,name)==0){
            piton_slot_decref(o->attrs[i].value);PitonSlot v={val,(int)tag};piton_slot_incref(v);o->attrs[i].value=v;return;}}
    if(o->length>=32){write(2,"AttributeError\n",15);exit(1);}
    o->attrs[o->length].name=name;PitonSlot v={val,(int)tag};piton_slot_incref(v);
    o->attrs[o->length].value=v;o->length++;}
void piton_object_free(void*raw){piton_collection_free(raw);}

static long str_len(const char*s){long n=0;while(s[n])++n;return n;}
static void expect_count(const char*name,long expected){
    long actual=piton_total_live_count();
    if(actual!=expected){
        write(2,name,str_len(name));write(2,": FAIL\n",7);_exit(1);}}

int main(void){
    /* Test 1: list self-cycle */
    void*self_list=piton_collection_new(1,0);
    piton_list_append(self_list,(long)self_list,1);
    piton_collection_free(self_list);
    expect_count("list self-cycle before",1);
    piton_gc_collect();
    expect_count("list self-cycle after",0);

    /* Test 2: object<->list mutual cycle */
    void*owner=piton_object_new("Nodo");
    void*items=piton_collection_new(1,0);
    piton_object_set_tagged(owner,"items",(long)items,1);
    piton_list_append(items,(long)owner,1);
    piton_object_free(owner);
    piton_collection_free(items);
    expect_count("obj-list cycle before",2);
    piton_gc_collect();
    expect_count("obj-list cycle after",0);

    /* Test 3: empty collect is idempotent */
    piton_gc_collect();
    expect_count("empty collect",0);

    write(1,"GC_CYCLES_V1_LINUX: PASS\n",25);return 0;
}
'''

    def test_gc_collects_cycles_linux(self):
        with tempfile.TemporaryDirectory(prefix="piton-gc-linux-") as directory:
            harness = Path(directory) / "gc_cycle_linux.c"
            harness.write_text(self._HARNESS_C, encoding="utf-8")
            exe = Path(directory) / "gc_cycle_linux"
            built = subprocess.run(
                ["wsl.exe", "gcc", "-std=c11", "-O2",
                 windows_to_wsl_path(harness), "-o", windows_to_wsl_path(exe)],
                capture_output=True, text=True, check=False, timeout=30,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            completed = subprocess.run(
                ["wsl.exe", "/usr/bin/env", "-i", windows_to_wsl_path(exe)],
                capture_output=True, text=True, check=False, timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("GC_CYCLES_V1_LINUX: PASS", completed.stdout)


if __name__ == "__main__":
    unittest.main()
