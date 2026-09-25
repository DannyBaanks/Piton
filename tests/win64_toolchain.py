"""Win64 PE gate for tests that build and run ``program.exe``.

The Win64 backend emits NASM for ``-f win64`` and links against the MSVCRT C
runtime, so building and *running* the PE only works on Windows. Elsewhere we
still lower to MIR and emit the NASM (so fail-closed checks and emitter crashes
stay real failures) and skip only the assemble/link/run step.

With ``PITON_WIN64_LINKCHECK=1`` (needs ``nasm`` and ``zig`` on PATH) the
non-Windows gate goes further: it assembles the program, cross-compiles
``native_runtime.c`` with ``zig cc -target x86_64-windows-gnu`` and links the
PE, so a missing runtime symbol or a C error fails the test. Only running the
executable is left to a Windows host.

With ``PITON_NATIVE_TARGET=win64`` off Windows (needs ``nasm``,
``x86_64-w64-mingw32-gcc`` and ``wine``) nothing is skipped: PEs are really
built and executed through wine, and differential tests exercise the Win64
backend instead of the Linux one.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import piton.x86 as x86

from piton.native_differential import native_command, native_env, native_target

WINDOWS = os.name == "nt"
WINE_MODE = (
    not WINDOWS
    and native_target() == "win64"
    and bool(shutil.which("wine") and shutil.which("nasm") and x86.win64_c_compiler())
)
SKIP_REASON = "Win64 PE build+run needs Windows (nasm -f win64 + MSVCRT runtime)"
LINKED_REASON = "Win64 PE assembled and linked with zig; running it needs Windows"

_runtime_object: Path | None = None


def _zig_runtime(workdir: Path) -> Path:
    """Cross-compile native_runtime.c once per process."""
    global _runtime_object
    if _runtime_object is None or not _runtime_object.is_file():
        cache = Path(tempfile.mkdtemp(prefix="piton-w64rt-"))
        target = cache / "native_runtime.obj"
        source = Path(x86.__file__).with_name("native_runtime.c")
        built = subprocess.run(
            ["zig", "cc", "-target", "x86_64-windows-gnu", "-std=c11", "-O2", "-c", str(source), "-o", str(target)],
            capture_output=True, text=True, check=False,
        )
        if built.returncode:
            raise x86.NativeBuildError(built.stderr or built.stdout)
        _runtime_object = target
    return _runtime_object


def _emit_then_skip(mir, output):
    assembly = x86.emit_nasm(mir)
    if os.environ.get("PITON_WIN64_LINKCHECK") and shutil.which("nasm") and shutil.which("zig"):
        with tempfile.TemporaryDirectory(prefix="piton-w64link-") as directory:
            workdir = Path(directory)
            (workdir / "program.asm").write_text(assembly, encoding="ascii")
            assembled = subprocess.run(
                ["nasm", "-f", "win64", str(workdir / "program.asm"), "-o", str(workdir / "program.obj")],
                capture_output=True, text=True, check=False,
            )
            if assembled.returncode:
                raise x86.NativeBuildError(assembled.stderr or assembled.stdout)
            linked = subprocess.run(
                ["zig", "cc", "-target", "x86_64-windows-gnu", str(workdir / "program.obj"),
                 str(_zig_runtime(workdir)), "-o", str(workdir / "program.exe")],
                capture_output=True, text=True, check=False,
            )
            if linked.returncode:
                raise x86.NativeBuildError(linked.stderr or linked.stdout)
        raise unittest.SkipTest(LINKED_REASON)
    raise unittest.SkipTest(SKIP_REASON)


def install() -> None:
    """Route PE builds through emit-then-skip on non-Windows hosts (unless wine mode)."""
    if not WINDOWS and not WINE_MODE:
        x86._compile_native_mir = _emit_then_skip


def run_native(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run for a built PE: direct on Windows, through wine elsewhere."""
    if WINE_MODE:
        kwargs.setdefault("env", native_env())
        argv = native_command(argv[0]) + list(argv[1:])
    return subprocess.run(argv, **kwargs)


def run_oracle(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run the CPython oracle. Under wine it is this host's CPython, so its
    output is converted to the Windows CRT text mode (LF -> CRLF) that the
    PE and a Windows CPython both produce."""
    completed = subprocess.run(argv, **kwargs)
    if WINE_MODE and isinstance(completed.stdout, bytes):
        completed.stdout = completed.stdout.replace(b"\n", b"\r\n")
    return completed


requires_windows = unittest.skipUnless(WINDOWS or WINE_MODE, SKIP_REASON)
