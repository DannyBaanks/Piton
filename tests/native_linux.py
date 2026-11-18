"""Platform-aware access to the Linux ELF native backend for tests.

The differential suites that call ``compile_native_linux`` exercise the Linux
ELF backend, not the Windows PE backend. GitHub's Windows runner has no WSL
distribution, so attempting that build there measures a missing runner
capability rather than PITON behaviour.

This helper keeps the test dependency explicit: Linux hosts use the real
backend unchanged; Windows hosts skip only when the test actually asks for a
Linux ELF build. Set ``PITON_RUN_LINUX_NATIVE_ON_WINDOWS=1`` to opt in when a
local WSL distribution is intentionally configured for such a run.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest


SKIP_REASON = (
    "Linux ELF native backend is not available on this Windows host "
    "(the GitHub Windows runner has no WSL distribution)"
)


def _windows_opt_in() -> bool:
    return os.environ.get("PITON_RUN_LINUX_NATIVE_ON_WINDOWS") == "1"


def require_linux_native() -> None:
    """Skip a test that must exercise the Linux ELF backend.

    Call this before helpers that themselves invoke the Linux toolchain (for
    example ``tools.parity_corpus_v4._stage_impl``), so an environment failure
    cannot be reported as a PITON build failure.
    """
    if os.name == "nt" and not _windows_opt_in():
        pytest.skip(SKIP_REASON)


def compile_native_linux(source: str, output: str | Path) -> Path:
    """Compile a Linux ELF binary, or skip when the host cannot do so."""
    require_linux_native()

    from piton.linux_x86 import compile_native_linux as compile_linux

    return compile_linux(source, output)


def gcc_compile(c_path: str | Path, output_path: str | Path) -> None:
    """Compile emitted Linux C, or skip when the host cannot do so."""
    require_linux_native()

    from piton.linux_x86 import _gcc_compile

    _gcc_compile(Path(c_path), Path(output_path))


def _wsl_path(path: Path) -> str:
    """Map a Windows path through the selected WSL distribution."""
    mapped = subprocess.run(
        ["wsl.exe", "--exec", "wslpath", "-a", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if mapped.returncode:
        detail = (mapped.stderr or mapped.stdout or "").strip()
        raise OSError(f"WSL could not map the ELF path: {detail or mapped.returncode}")
    result = mapped.stdout.strip()
    if not result:
        raise OSError("WSL returned an empty path for the Linux ELF artifact")
    return result


def run_native_linux(
    executable: str | Path,
    *args: str | Path,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Run a Linux ELF artifact on Linux or through WSL on Windows.

    Windows cannot execute an ELF image directly.  When the Windows opt-in is
    enabled, the artifact is therefore run inside the configured WSL
    distribution instead of handing the ELF path to ``CreateProcess``.
    """
    require_linux_native()

    executable_path = Path(executable).resolve()
    if os.name == "nt":
        wsl_executable = _wsl_path(executable_path)
        chmod = subprocess.run(
            ["wsl.exe", "--exec", "chmod", "+x", wsl_executable],
            capture_output=True,
            text=True,
            check=False,
        )
        if chmod.returncode:
            detail = (chmod.stderr or chmod.stdout or "").strip()
            raise OSError(
                f"WSL could not make the Linux ELF executable: {detail or chmod.returncode}"
            )
        command = ["wsl.exe", "/usr/bin/env", "-i", wsl_executable]
        command.extend(str(arg) for arg in args)
    else:
        command = [str(executable_path), *(str(arg) for arg in args)]

    return subprocess.run(command, **kwargs)
