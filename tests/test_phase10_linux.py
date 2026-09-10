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
    windows_to_wsl_path,
)
from piton.translator import traducir_fuente
from piton.x86 import NativeBuildError


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
        from piton.linux_x86 import NativeBuildError, compile_native_linux
        with tempfile.TemporaryDirectory(prefix="piton-linux-diff-") as directory:
            source = 'intentar:\n    lanzar ValueError("x")\nexcepto Exception:\n    lanzar\nimprimir("done")\n'
            with self.assertRaisesRegex(NativeBuildError, "catch-all"):
                compile_native_linux(source, Path(directory) / "program")

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

    # ── Linux *args (FUNCTION_STARARGS_V1) ──────────────────────────────

    def test_linux_starargs_pure_empty_and_many(self):
        self._assert_linux_equiv('funcion contar(*args):\n    devolver longitud(args)\nimprimir(contar())\nimprimir(contar(1, 2, 3, 4, 5))\n')

    def test_linux_starargs_sum_and_subscript(self):
        self._assert_linux_equiv('funcion resumir(*args):\n    devolver sum(args) + args[0] * 10\nimprimir(resumir(2, 3, 4))\n')

    def test_linux_starargs_fixed_and_default(self):
        self._assert_linux_equiv('funcion peso(base=1, *extras):\n    devolver base * 10 + longitud(extras)\nimprimir(peso())\nimprimir(peso(5, 7, 9))\nimprimir(peso(base=4))\n')

    # ── Linux **kwargs (FUNCTION_KWARGS_V1) ─────────────────────────────

    def test_linux_kwargs_pure_dict(self):
        self._assert_linux_equiv('funcion leer(**kw):\n    devolver longitud(kw) * 10 + kw["x"]\nimprimir(leer(x=4, z=8))\n')

    def test_linux_kwargs_fixed_and_starargs(self):
        self._assert_linux_equiv('funcion total(base, *extras, **opciones):\n    devolver base + sum(extras) + opciones["extra"]\nimprimir(total(1, 2, 3, extra=4))\n')

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
                ["wsl.exe", "/usr/bin/env", "-i", linux_path],
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
                ["wsl.exe", "/usr/bin/env", "-i", linux_path],
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
                ["wsl.exe", "/usr/bin/env", "-i", linux_path],
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
                ["wsl.exe", "/usr/bin/env", "-i", linux_path],
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
                ["wsl.exe", "/usr/bin/env", "-i", linux_path],
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
            with self.assertRaisesRegex(Exception, "beyond one level"):
                compile_native_linux_files(entry, root / "program")

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
                ["wsl.exe", "/usr/bin/env", "-i", linux_path],
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
                ["wsl.exe", "/usr/bin/env", "-i", windows_to_wsl_path(executable)],
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


if __name__ == "__main__":
    unittest.main()
