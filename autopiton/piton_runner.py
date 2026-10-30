"""
PITON Runner Module for AutoPiton

Builds the original Python source through the real PITON native pipeline
(ADAPT, PARSE, MIR, BUILD) inside the sandbox worker, then executes the
resulting static ELF in a separate minimal sandbox root.

Every stage is recorded separately. A build is only accepted when gcc
succeeds and the artifact is a non-empty ELF; `piton run` (the CPython
backend) is deliberately not used as a native-execution substitute.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .sandbox import Sandbox, SandboxConfig


class PitonStage(Enum):
    """PITON execution stages."""
    UNKNOWN = "UNKNOWN"
    ADAPT = "ADAPT"
    PARSE = "PARSE"
    MIR = "MIR"
    BUILD = "BUILD"
    EXECUTE = "EXECUTE"
    HARNESS = "HARNESS"


_PIPELINE_ORDER = [
    PitonStage.ADAPT,
    PitonStage.PARSE,
    PitonStage.MIR,
    PitonStage.BUILD,
    PitonStage.EXECUTE,
]

_NATIVE_EXCEPTION_LINE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Exit|Interrupt|Warning))(?:: (.*))?$"
)


def parse_native_exception(stderr: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract ``TypeName: message`` from the last matching stderr line.

    The native runtime has no traceback; it prints one line on failure.
    """
    for line in reversed(stderr.strip().splitlines()):
        match = _NATIVE_EXCEPTION_LINE.match(line.strip())
        if match:
            return match.group(1), (match.group(2) or "")
    return None, None


@dataclass
class PitonResult:
    """
    Complete result of building and executing a program with native PITON.

    ``stage_reached`` is the furthest stage whose checks passed; ``failed_stage``
    is the first stage that failed (None when the pipeline completed). A
    pre-EXECUTE failure means PITON rejected the program; a HARNESS failure
    means AutoPiton itself could not obtain a verdict.
    """

    source_code: str
    filename: str = "program.py"
    source_hash: str = ""

    stage_reached: PitonStage = PitonStage.UNKNOWN
    failed_stage: Optional[PitonStage] = None
    stages: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    build_ms: float = 0.0
    build_exit_code: Optional[int] = None
    build_stdout: str = ""
    build_stderr: str = ""
    build_timed_out: bool = False
    worker_error: Optional[str] = None

    executed: bool = False
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    execute_ms: float = 0.0
    timed_out: bool = False
    crash: bool = False
    signal_number: Optional[int] = None
    memory_exceeded: bool = False
    output_truncated: bool = False

    exception_type: Optional[str] = None
    exception_message: Optional[str] = None

    artifact_sha256: Optional[str] = None
    surface_sha256: Optional[str] = None

    @property
    def rejected(self) -> bool:
        """PITON refused the program at a real pipeline stage."""
        return (
            not self.executed
            and self.failed_stage is not None
            and self.failed_stage in _PIPELINE_ORDER
        )

    @property
    def harness_failed(self) -> bool:
        return self.failed_stage == PitonStage.HARNESS

    @property
    def succeeded(self) -> bool:
        return (
            self.executed
            and self.exit_code == 0
            and not self.timed_out
            and not self.crash
            and self.exception_type is None
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_hash": self.source_hash,
            "filename": self.filename,
            "stage_reached": self.stage_reached.value,
            "failed_stage": self.failed_stage.value if self.failed_stage else None,
            "stages": self.stages,
            "build_ms": self.build_ms,
            "build_exit_code": self.build_exit_code,
            "build_timed_out": self.build_timed_out,
            "build_stderr_tail": self.build_stderr[-2000:],
            "worker_error": self.worker_error,
            "executed": self.executed,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "execute_ms": self.execute_ms,
            "timed_out": self.timed_out,
            "crash": self.crash,
            "signal_number": self.signal_number,
            "memory_exceeded": self.memory_exceeded,
            "output_truncated": self.output_truncated,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "artifact_sha256": self.artifact_sha256,
            "surface_sha256": self.surface_sha256,
        }


def _stage_or_harness(name: object) -> PitonStage:
    try:
        return PitonStage(str(name))
    except ValueError:
        return PitonStage.HARNESS


def _reached_before(stage: PitonStage) -> PitonStage:
    if stage not in _PIPELINE_ORDER:
        return PitonStage.UNKNOWN
    index = _PIPELINE_ORDER.index(stage)
    return _PIPELINE_ORDER[index - 1] if index > 0 else PitonStage.UNKNOWN


class PitonRunner:
    """
    Builds and executes programs with the PITON native backend.

    The build runs in the fail-closed sandbox with the repository bound
    read-only; execution runs the static ELF in a separate minimal root.
    """

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        timeout: float = 10.0,
        build_timeout: float = 60.0,
        memory_limit_mb: int = 256,
    ):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.timeout = timeout
        self.build_timeout = build_timeout
        self.memory_limit_mb = memory_limit_mb

    def execute(self, source_code: str, filename: str = "program.py") -> PitonResult:
        """Build and run *source_code* with PITON, stage by stage."""
        from .piton_worker import ARTIFACT_FILENAME, RESULT_FILENAME

        result = PitonResult(
            source_code=source_code,
            filename=filename,
            source_hash=hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
        )
        config = SandboxConfig(
            timeout_seconds=self.timeout,
            memory_limit_mb=self.memory_limit_mb,
        )

        with Sandbox(config) as sandbox:
            build = sandbox.run_build_worker(
                self.repo_root, source_code, filename, timeout=self.build_timeout
            )
            result.build_ms = build.duration_ms
            result.build_exit_code = build.exit_code
            result.build_stdout = build.stdout
            result.build_stderr = build.stderr
            result.build_timed_out = build.timed_out

            report = self._read_report(sandbox.workdir / RESULT_FILENAME, result)
            if result.worker_error:
                result.failed_stage = PitonStage.HARNESS
                return result
            if build.timed_out:
                result.failed_stage = PitonStage.BUILD
                result.worker_error = (
                    f"build worker exceeded {self.build_timeout:.0f}s"
                )
                return result
            if build.sandbox_error:
                result.failed_stage = PitonStage.HARNESS
                result.worker_error = build.sandbox_error
                return result

            result.stages = report.get("stages", {})
            adapt = result.stages.get("ADAPT", {})
            result.surface_sha256 = adapt.get("surface_sha256")

            failed = report.get("failed_stage")
            if failed:
                stage = _stage_or_harness(failed)
                result.failed_stage = stage
                result.stage_reached = (
                    PitonStage.UNKNOWN if stage == PitonStage.HARNESS else _reached_before(stage)
                )
                if stage == PitonStage.HARNESS:
                    result.worker_error = self._stage_error(result.stages.get("HARNESS", {}))
                return result

            result.stage_reached = PitonStage.BUILD
            build_info = result.stages.get("BUILD", {})
            result.artifact_sha256 = build_info.get("artifact_sha256")
            artifact = str(build_info.get("artifact_name", ARTIFACT_FILENAME))

            native = sandbox.run_native(artifact)
            result.execute_ms = native.duration_ms
            result.executed = native.sandbox_error is None
            result.exit_code = native.exit_code
            result.stdout = native.stdout
            result.stderr = native.stderr
            result.timed_out = native.timed_out
            result.crash = native.crash
            result.signal_number = native.signal_number
            result.memory_exceeded = native.memory_exceeded
            result.output_truncated = native.output_truncated
            if native.sandbox_error:
                result.failed_stage = PitonStage.HARNESS
                result.worker_error = native.sandbox_error
                return result

            result.stage_reached = PitonStage.EXECUTE
            result.exception_type, result.exception_message = parse_native_exception(
                native.stderr
            )
            return result

    @staticmethod
    def _read_report(path: Path, result: PitonResult) -> Dict[str, Any]:
        if not path.is_file():
            result.worker_error = f"worker produced no report at {path.name}"
            return {}
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            result.worker_error = f"unreadable worker report: {type(exc).__name__}: {exc}"
            return {}
        if not isinstance(report, dict):
            result.worker_error = "worker report is not a JSON object"
            return {}
        return report

    @staticmethod
    def _stage_error(stage_report: Dict[str, Any]) -> str:
        error_type = stage_report.get("error_type", "HarnessError")
        message = stage_report.get("error_message", "")
        return f"{error_type}: {message}".strip()

