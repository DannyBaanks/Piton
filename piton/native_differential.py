"""Oracle diferencial para el subconjunto x86 nativo.

CPython se usa únicamente durante las pruebas como oracle. El ejecutable
nativo producido no carga ni ejecuta Python.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .translator import traducir_fuente
from .x86 import compile_native


@dataclass(frozen=True)
class Observation:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class DifferentialResult:
    native: Observation
    oracle: Observation

    @property
    def equivalent(self) -> bool:
        return self.native == self.oracle


def compare_native_to_cpython(source: str) -> DifferentialResult:
    translated = traducir_fuente(source, "<native-differential>")
    with tempfile.TemporaryDirectory(prefix="piton-native-diff-") as directory:
        executable = compile_native(source, Path(directory) / "program.exe")
        native_run = subprocess.run([str(executable)], capture_output=True, check=False)
        oracle_run = subprocess.run(
            [sys.executable, "-c", translated], capture_output=True, check=False
        )
    return DifferentialResult(
        native=Observation(native_run.returncode, native_run.stdout, native_run.stderr),
        oracle=Observation(oracle_run.returncode, oracle_run.stdout, oracle_run.stderr),
    )
