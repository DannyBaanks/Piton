"""METACLASSES_V1 baseline — corpus diferencial vs CPython 3.12.4.

11 casos reales. Pre-implementación todos los casos con `metaclass=` fallan en
parse, y `clase Meta(type):` falla en MIR (base `type` no definida); esa es la
huella de fallo registrada como baseline (2026-09-21).

Reglas del corpus: solo salidas deterministas (enteros, strings, True/False);
nunca se imprimen reprs de instancias/clases (direcciones y `__main__` no son
portables).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from piton.native_differential import compare_native_to_cpython, _compile_native
from piton.translator import traducir_fuente


class MetaclassesGate(unittest.TestCase):
    def _equivalent(self, source: str):
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_metaclass_keyword_creates_class(self):
        self._equivalent(
            "clase Meta(type):\n"
            "    pass\n"
            "clase A(metaclass=Meta):\n"
            "    pass\n"
            "imprimir(1)\n"
        )

    def test_metaclass_new_hook_runs(self):
        self._equivalent(
            "clase Meta(type):\n"
            "    funcion __new__(mcs, nombre, bases, ns):\n"
            "        imprimir(nombre)\n"
            "        devolver super().__new__(mcs, nombre, bases, ns)\n"
            "clase A(metaclass=Meta):\n"
            "    pass\n"
        )

    def test_metaclass_new_injects_attribute(self):
        self._equivalent(
            "clase Meta(type):\n"
            "    funcion __new__(mcs, nombre, bases, ns):\n"
            "        ns[\"saludo\"] = 42\n"
            "        devolver super().__new__(mcs, nombre, bases, ns)\n"
            "clase A(metaclass=Meta):\n"
            "    pass\n"
            "imprimir(A.saludo)\n"
        )

    def test_metaclass_init_runs_after_new(self):
        self._equivalent(
            "clase Meta(type):\n"
            "    funcion __new__(mcs, nombre, bases, ns):\n"
            "        imprimir(\"new\")\n"
            "        devolver super().__new__(mcs, nombre, bases, ns)\n"
            "    funcion __init__(cls, nombre, bases, ns):\n"
            "        imprimir(\"init\")\n"
            "clase A(metaclass=Meta):\n"
            "    pass\n"
        )

    def test_metaclass_call_intercepts_instantiation(self):
        self._equivalent(
            "clase Meta(type):\n"
            "    funcion __call__(cls):\n"
            "        imprimir(\"call\")\n"
            "        devolver super().__call__()\n"
            "clase A(metaclass=Meta):\n"
            "    pass\n"
            "a = A()\n"
        )

    def test_metaclass_call_can_return_noninstance(self):
        self._equivalent(
            "clase Meta(type):\n"
            "    funcion __call__(cls):\n"
            "        devolver 7\n"
            "clase A(metaclass=Meta):\n"
            "    pass\n"
            "imprimir(A())\n"
        )

    def test_type_three_args_creates_class(self):
        self._equivalent(
            "A = type(\"A\", (), {\"x\": 5})\n"
            "imprimir(A.x)\n"
        )

    def test_type_dynamic_class_instance_attribute(self):
        self._equivalent(
            "A = type(\"A\", (), {\"x\": 9})\n"
            "a = A()\n"
            "imprimir(a.x)\n"
        )

    def test_metaclass_inherited_by_subclass(self):
        self._equivalent(
            "clase Meta(type):\n"
            "    funcion __new__(mcs, nombre, bases, ns):\n"
            "        imprimir(nombre)\n"
            "        devolver super().__new__(mcs, nombre, bases, ns)\n"
            "clase A(metaclass=Meta):\n"
            "    pass\n"
            "clase B(A):\n"
            "    pass\n"
        )

    def test_type_of_class_is_metaclass(self):
        self._equivalent(
            "clase Meta(type):\n"
            "    pass\n"
            "clase A(metaclass=Meta):\n"
            "    pass\n"
            "imprimir(type(A) es Meta)\n"
        )

    def test_metaclass_conflict_fails_closed(self):
        # Dos metaclases incompatibles -> CPython: TypeError "metaclass conflict".
        # Nativo: fail-closed aceptable en build o en runtime; nunca éxito silencioso.
        source = (
            "clase M1(type):\n"
            "    pass\n"
            "clase M2(type):\n"
            "    pass\n"
            "clase A(metaclass=M1):\n"
            "    pass\n"
            "clase B(metaclass=M2):\n"
            "    pass\n"
            "clase C(A, B):\n"
            "    pass\n"
        )
        translated = traducir_fuente(source, "<meta-conflict>")
        oracle = subprocess.run(
            [sys.executable, "-c", translated], capture_output=True, check=False
        )
        self.assertNotEqual(oracle.returncode, 0)
        self.assertIn(b"metaclass conflict", oracle.stderr)
        with tempfile.TemporaryDirectory(prefix="piton-meta-conflict-") as directory:
            try:
                executable = _compile_native(source, Path(directory) / "program.exe")
            except Exception:
                return  # fail-closed en build: aceptable
            run = subprocess.run([str(executable)], capture_output=True, check=False)
            self.assertNotEqual(run.returncode, 0, run.stdout)


if __name__ == "__main__":
    unittest.main()
