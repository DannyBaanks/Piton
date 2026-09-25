"""Oracle diferencial para el subconjunto x86 nativo.

CPython se usa únicamente durante las pruebas como oracle. El ejecutable
nativo producido no carga ni ejecuta Python.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .translator import traducir_fuente


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


def native_target() -> str:
    """Backend under test: ``PITON_NATIVE_TARGET`` (``linux`` | ``win64``) or the host's own."""
    target = os.environ.get("PITON_NATIVE_TARGET")
    if target:
        if target not in {"linux", "win64"}:
            raise ValueError(f"PITON_NATIVE_TARGET must be linux or win64, got {target!r}")
        return target
    return "linux" if platform.system() == "Linux" else "win64"


def runs_under_wine() -> bool:
    """Win64 PEs built off Windows are executed through wine."""
    return native_target() == "win64" and os.name != "nt"


def native_command(executable: str | Path) -> list[str]:
    """argv that runs a native executable of the current target on this host."""
    if runs_under_wine():
        return ["wine", str(executable)]
    return [str(executable)]


def strip_wine_noise(stderr: bytes) -> bytes:
    """Drop wine's own sporadic ``wine client error:`` lines (host noise, never
    program output). Only applied when the PE runs under wine."""
    if not runs_under_wine() or b"wine client error:" not in stderr:
        return stderr
    kept = [line for line in stderr.splitlines(keepends=True) if not line.startswith(b"wine client error:")]
    return b"".join(kept)


def native_env() -> dict[str, str] | None:
    if runs_under_wine():
        return {**os.environ, "WINEDEBUG": "-all"}
    return None


def _compile_native(source: str, output: Path) -> Path:
    if native_target() == "linux":
        from .linux_x86 import compile_native_linux
        return compile_native_linux(source, output)
    from .x86 import compile_native
    return compile_native(source, output)


def compare_native_to_cpython(source: str) -> DifferentialResult:
    translated = traducir_fuente(source, "<native-differential>")
    with tempfile.TemporaryDirectory(prefix="piton-native-diff-") as directory:
        executable = _compile_native(source, Path(directory) / "program.exe")
        native_run = subprocess.run(
            native_command(executable), capture_output=True, check=False, env=native_env()
        )
        oracle_run = subprocess.run(
            [sys.executable, "-c", translated], capture_output=True, check=False
        )
    native_out, native_err = native_run.stdout, strip_wine_noise(native_run.stderr)
    if runs_under_wine():
        # The oracle is this host's CPython (LF); the PE writes through the
        # Windows CRT in text mode (CRLF). Compare line content, not newlines.
        native_out = native_out.replace(b"\r\n", b"\n")
        native_err = native_err.replace(b"\r\n", b"\n")
    return DifferentialResult(
        native=Observation(native_run.returncode, native_out, native_err),
        oracle=Observation(oracle_run.returncode, oracle_run.stdout, oracle_run.stderr),
    )
