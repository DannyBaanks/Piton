"""DEL_BOUND_MODULE_ONLY_V1: `_bound_<n>` modela solo el ligado a nivel de
modulo. Un local homonimo en otra funcion (p.ej. un helper de stdlib como
locale) no debe marcar el global del modulo como ligado (stdlib2 #32).
"""
import tempfile
import unittest
from pathlib import Path

from native_linux import compile_native_linux, run_native_linux


def _run(source: str):
    with tempfile.TemporaryDirectory(prefix="piton-w8del-") as directory:
        exe = Path(directory) / "prog"
        compile_native_linux(source, exe)
        return run_native_linux(exe, capture_output=True, text=True, timeout=20)


class DelBoundModuleOnlyV1(unittest.TestCase):
    def test_same_named_local_in_other_function_does_not_bind(self):
        r = _run(
            "funcion f ():\n    a = 1\n    devolver a\n"
            "f ()\n"
            "intentar:\n    a = A (10 )\nexcepto BaseException:\n    imprimir (\"EXC\")\n"
            "intentar:\n    borrar a\nexcepto NameError:\n    imprimir (\"EXC2\")\n"
            "a = 5\n"
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "EXC\nEXC2\n")

    def test_global_declared_in_function_still_binds(self):
        r = _run(
            "funcion f ():\n    global a\n    a = 1\n"
            "f ()\nborrar a\n"
            "intentar:\n    borrar a\nexcepto NameError:\n    imprimir (\"NE\")\n"
            "imprimir (\"ok\")\n"
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "NE\nok\n")

    def test_module_binding_then_del(self):
        r = _run("a = 1\nborrar a\nintentar:\n    borrar a\nexcepto NameError:\n    imprimir (\"NE\")\n")
        self.assertEqual(r.stdout, "NE\n")


if __name__ == "__main__":
    unittest.main()
