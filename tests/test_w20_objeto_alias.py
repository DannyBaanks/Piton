"""Differential parity tests for Spanish builtin alias shadowing (LEGB) across locals, parameters, loops, globals, and attributes."""
from __future__ import annotations

import unittest

from piton.native_differential import compare_native_to_cpython

_SHADOWABLE_ALIASES = (
    "objeto", "diccionario", "octetos", "arreglo_bytes", "rebanada",
    "conjunto_inmutable", "vista_memoria", "metodo_estatico", "metodo_clase",
    "propiedad", "booleano", "complejo", "decimal", "entero", "texto",
    "tupla", "lista", "conjunto", "rango", "longitud", "enumerar",
    "imprimir", "entrada", "abrir", "ordenar", "suma", "redondear",
    "es_instancia", "es_subclase", "tiene_atr", "obtener_atr",
    "fijar_atr", "establecer_atr", "formato", "tipo",
)


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    result = compare_native_to_cpython(source)
    testcase.assertEqual(
        result.native.returncode,
        result.oracle.returncode,
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\noracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(
        result.native.stdout,
        result.oracle.stdout,
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}\noracle stderr: {result.oracle.stderr!r}",
    )


class TestSpanishBuiltinAliasShadowing(unittest.TestCase):
    def test_exact_reproducers(self):
        cases = {
            "param_objeto": "funcion f(objeto):\n    devolver objeto + 1\nimprimir(f(3))\n",
            "local_objeto": "objeto = 'x'\nimprimir(objeto * 2)\n",
            "loop_objeto": "para objeto en [1, 2]:\n    imprimir(objeto)\n",
            "object_builtin_still_works": "o = object()\nimprimir(type(o).__name__)\n",
        }
        for name, src in cases.items():
            with self.subTest(case=name):
                _assert_matches(self, src)

    def test_all_spanish_aliases_shadowed_as_param_local_loop_global_attr(self):
        # Batch aliases into a single compiled program per context to test all 35 aliases fast and thoroughly.
        non_print = [n for n in _SHADOWABLE_ALIASES if n != "imprimir"]

        param_lines = []
        for idx, n in enumerate(non_print):
            param_lines.append(f"funcion f_{idx}({n}):\n    devolver {n} + 1\nimprimir('{n}', f_{idx}(3))")
        _assert_matches(self, "\n".join(param_lines) + "\n")

        local_lines = ["funcion probar_locales():"]
        for idx, n in enumerate(non_print):
            local_lines.append(f"    {n} = {idx + 10}\n    imprimir('{n}', {n} * 2)")
        local_lines.append("probar_locales()")
        _assert_matches(self, "\n".join(local_lines) + "\n")

        loop_lines = ["funcion probar_loops():"]
        for n in non_print:
            loop_lines.append(f"    para {n} en [7]:\n        imprimir('{n}', {n})")
        loop_lines.append("probar_loops()")
        _assert_matches(self, "\n".join(loop_lines) + "\n")

        global_lines = []
        for idx, n in enumerate(non_print):
            global_lines.append(f"{n} = {idx + 5}\nfuncion g_{idx}():\n    devolver {n}\nimprimir('{n}', g_{idx}())")
        _assert_matches(self, "\n".join(global_lines) + "\n")

        attr_lines = []
        for chunk_idx in range(0, len(non_print), 10):
            chunk = non_print[chunk_idx : chunk_idx + 10]
            cls_name = f"C_{chunk_idx}"
            attr_lines.append(f"clase {cls_name}:\n    funcion __init__(self):")
            for idx, n in enumerate(chunk):
                attr_lines.append(f"        self.{n} = {chunk_idx + idx + 1}")
            attr_lines.append(f"c_{chunk_idx} = {cls_name}()")
            for n in chunk:
                attr_lines.append(f"imprimir('{n}', c_{chunk_idx}.{n})")
        _assert_matches(self, "\n".join(attr_lines) + "\n")

    def test_imprimir_shadowing_with_python_print_and_callee_shadowing(self):
        _assert_matches(
            self,
            "funcion f(imprimir):\n"
            "    devolver [imprimir]\n"
            "print(f(3))\n"
            "imprimir = lambda x: x + 100\n"
            "print(imprimir(1))\n"
            "objeto = lambda x: x + 200\n"
            "print(objeto(5))\n",
        )

    def test_unshadowed_builtin_constructors_still_work(self):
        _assert_matches(
            self,
            "o = object()\n"
            "d = dict([(1, 2)])\n"
            "b = bytes([65, 66])\n"
            "ba = bytearray([67])\n"
            "s = slice(1, 4)\n"
            "imprimir(type(o).__name__, d[1], len(b), len(ba), [10, 20, 30, 40][s])\n",
        )


if __name__ == "__main__":
    unittest.main()
