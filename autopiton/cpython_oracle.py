"""
CPython Oracle Module for AutoPiton

Executes the original Python source under CPython inside the fail-closed
sandbox and captures a structured result: compilation vs execution status,
exception type/message/traceback, outputs, exit code, resource flags, and
filesystem effects. This is the ground-truth reference for comparison.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cpython_wrapper import RESULT_FILENAME
from .sandbox import Sandbox, SandboxConfig

PYTHON_EXECUTABLE = "/usr/bin/python3"


@dataclass
class CPythonResult:
    """Structured result of executing one program with CPython."""
    
    source_hash: str

    # ok | exception | compile_error | system_exit | timeout | crash |
    # harness_error | missing_report
    status: str = "missing_report"

    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    crash: bool = False
    signal_number: Optional[int] = None
    memory_exceeded: bool = False
    output_truncated: bool = False

    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    exception_traceback: Optional[str] = None
    cause_type: Optional[str] = None
    cause_message: Optional[str] = None
    context_type: Optional[str] = None
    context_message: Optional[str] = None

    fs_created: List[Dict[str, Any]] = field(default_factory=list)
    python_version: str = ""
    python_executable: str = PYTHON_EXECUTABLE

    @property
    def compiled(self) -> bool:
        return self.status not in {"compile_error", "harness_error", "missing_report"}

    @property
    def harness_failed(self) -> bool:
        return self.status in {"harness_error", "missing_report"}

    @property
    def succeeded(self) -> bool:
        """Program ran to completion with exit status 0."""
        return (
            self.status in {"ok", "system_exit"}
            and self.exit_code == 0
            and not self.timed_out
            and not self.crash
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_hash": self.source_hash,
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "crash": self.crash,
            "signal_number": self.signal_number,
            "memory_exceeded": self.memory_exceeded,
            "output_truncated": self.output_truncated,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "exception_traceback": self.exception_traceback,
            "cause_type": self.cause_type,
            "cause_message": self.cause_message,
            "context_type": self.context_type,
            "context_message": self.context_message,
            "fs_created": self.fs_created,
            "python_version": self.python_version,
            "python_executable": self.python_executable,
        }
    


class CPythonOracle:
    """
    Oracle that executes Python programs with CPython in the sandbox.

    Any divergence from CPython behavior is a potential bug in PITON; this
    oracle never modifies or adapts the reference source.
    """

    def __init__(
        self,
        timeout: float = 10.0,
        memory_limit_mb: int = 256,
    ):
        self.timeout = timeout
        self.memory_limit_mb = memory_limit_mb
    
    def execute(self, source_code: str, filename: str = "program.py") -> CPythonResult:
        result = CPythonResult(
            source_hash=hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
            python_version="",
        )
        config = SandboxConfig(
            timeout_seconds=self.timeout,
            memory_limit_mb=self.memory_limit_mb,
        )

        with Sandbox(config) as sandbox:
            run = sandbox.run_cpython(source_code, filename)
            report = self._read_report(sandbox.workdir / RESULT_FILENAME)

        result.exit_code = run.exit_code
        result.stdout = run.stdout
        result.stderr = run.stderr
        result.duration_ms = run.duration_ms
        result.timed_out = run.timed_out
        result.crash = run.crash
        result.signal_number = run.signal_number
        result.memory_exceeded = run.memory_exceeded
        result.output_truncated = run.output_truncated
        result.fs_created = run.fs_created

        if run.sandbox_error:
            result.status = "harness_error"
            result.stderr = run.sandbox_error
            return result
        if run.timed_out:
            result.status = "timeout"
            return result
        if run.crash:
            result.status = "crash"
            return result
        if report is None:
            result.status = "missing_report"
            return result

        status = report.get("status")
        if status not in {"ok", "exception", "compile_error", "system_exit"}:
            result.status = "harness_error"
            return result
        result.status = status
        result.python_version = str(report.get("python_version") or result.python_version)
        result.python_executable = str(report.get("python_executable") or result.python_executable)
        result.exception_type = report.get("type")
        result.exception_message = report.get("message")
        result.exception_traceback = report.get("traceback")
        cause = report.get("cause") or {}
        result.cause_type = cause.get("type")
        result.cause_message = cause.get("message")
        context = report.get("context") or {}
        result.context_type = context.get("type")
        result.context_message = context.get("message")
        return result
    
    @staticmethod
    def _read_report(path: Path) -> Optional[Dict[str, Any]]:
        if not path.is_file():
            return None
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return report if isinstance(report, dict) else None
    
    def get_version(self) -> str:
        try:
            completed = subprocess.run(
                [PYTHON_EXECUTABLE, "-I", "-S", "-c",
                 "import sys; print(sys.version.split()[0])"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                return completed.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        return "unknown"

    def get_platform(self) -> str:
        return sys.platform


# Global oracle instance
_oracle: Optional[CPythonOracle] = None


def get_oracle() -> CPythonOracle:
    """Get or create the global CPython oracle."""
    global _oracle
    if _oracle is None:
        _oracle = CPythonOracle()
    return _oracle
