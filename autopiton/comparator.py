"""
Differential Comparator Module for AutoPiton

Compares CPython and PITON execution results to classify outcomes.
"""

import re
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json

from .cpython_oracle import CPythonResult
from .piton_runner import PitonResult, PitonStage


class Classification(Enum):
    """Classification of comparison results."""
    
    # Positive outcomes
    EQUIV = "EQUIV"  # Behavior is equivalent
    
    # Divergence
    DIVERGENT = "DIVERGENT"  # Different observable behavior
    
    # Support
    UNSUPPORTED = "UNSUPPORTED"  # PITON doesn't support a valid Python feature
    
    # Validity
    INVALID_REFERENCE = "INVALID_REFERENCE"  # CPython rejects the program
    INVALID_ACCEPTED = "INVALID_ACCEPTED"  # PITON accepts invalid Python
    
    # Errors
    CRASH = "CRASH"  # Unexpected crash
    TIMEOUT = "TIMEOUT"  # Timeout in either engine
    INCONCLUSIVE = "INCONCLUSIVE"  # Missing data
    NONDETERMINISTIC = "NONDETERMINISTIC"  # Non-deterministic result


@dataclass
class ComparisonResult:
    """Result of comparing one program across CPython and PITON."""

    source_code: str
    source_hash: str = ""
    cpython: Optional[CPythonResult] = None
    piton: Optional[PitonResult] = None
    classification: Classification = Classification.INCONCLUSIVE
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.source_hash:
            self.source_hash = hashlib.sha256(
                self.source_code.encode("utf-8")
            ).hexdigest()

    def observable_fingerprint(self) -> str:
        """Hash of observable fields only (no timings), for stability checks."""
        cpython = self.cpython
        piton = self.piton
        payload = {
            "classification": self.classification.value,
            "cpython": None if cpython is None else {
                "status": cpython.status,
                "exit": cpython.exit_code,
                "stdout": cpython.stdout,
                "exception_type": cpython.exception_type,
                "exception_message": cpython.exception_message,
            },
            "piton": None if piton is None else {
                "executed": piton.executed,
                "stage": piton.stage_reached.value,
                "failed_stage": piton.failed_stage.value if piton.failed_stage else None,
                "exit": piton.exit_code,
                "stdout": piton.stdout,
                "exception_type": piton.exception_type,
                "exception_message": piton.exception_message,
            },
        }
        encoded = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source_code,
            "source_hash": self.source_hash,
            "classification": self.classification.value,
            "reason": self.reason,
            "details": self.details,
            "cpython": self.cpython.to_dict() if self.cpython else None,
            "piton": self.piton.to_dict() if self.piton else None,
        }


class DifferentialComparator:
    """Classifies one CPython/PITON result pair per the AutoPiton contract."""
    
    def compare(
        self,
        source_code: str,
        cpython: Optional[CPythonResult] = None,
        piton: Optional[PitonResult] = None,
    ) -> ComparisonResult:
        result = ComparisonResult(source_code=source_code, cpython=cpython, piton=piton)
        if cpython is None or piton is None:
            result.reason = "missing execution result"
            return result

        if cpython.timed_out or piton.timed_out:
            result.classification = Classification.TIMEOUT
            if cpython.timed_out:
                result.reason = "CPython exceeded the time limit"
            else:
                result.reason = f"PITON {piton.failed_stage or PitonStage.EXECUTE} exceeded the time limit"
            return result

        if cpython.crash or piton.crash:
            result.classification = Classification.CRASH
            result.reason = (
                f"process crash (cpython signal={cpython.signal_number}, "
                f"piton signal={piton.signal_number})"
            )
            return result

        if cpython.harness_failed or piton.harness_failed:
            piton_stage = piton.failed_stage.value if piton.failed_stage else "none"
            result.classification = Classification.INCONCLUSIVE
            result.reason = (
                f"harness failure (cpython={cpython.status}, piton={piton_stage})"
            )
            return result

        if not piton.executed:
            if not piton.rejected:
                result.classification = Classification.INCONCLUSIVE
                result.reason = "PITON has no execution and no pipeline stage failure"
                return result
            if cpython.succeeded:
                result.classification = Classification.UNSUPPORTED
                result.reason = (
                    "CPython completed; PITON rejected the program at "
                    f"{piton.failed_stage.value}"
                )
                result.details = {"piton_stage_error": self._stage_error_detail(piton)}
                return result
            result.classification = Classification.INVALID_REFERENCE
            result.reason = (
                f"reference rejects ({self._reference_failure(cpython)}); "
                f"PITON rejects at {piton.failed_stage.value}"
            )
            return result

        if cpython.succeeded:
            if piton.succeeded:
                return self._compare_success(cpython, piton, result)
            result.classification = Classification.DIVERGENT
            result.reason = "CPython completed; PITON failed during execution"
            result.details = {
                "cpython_stdout": cpython.stdout,
                "piton_stdout": piton.stdout,
                "piton_exit": piton.exit_code,
                "piton_stderr": piton.stderr[-2000:],
            }
            return result

        if piton.succeeded:
            result.classification = Classification.INVALID_ACCEPTED
            result.reason = (
                f"reference rejects ({self._reference_failure(cpython)}); "
                "PITON executed the program successfully"
            )
            result.details = {
                "cpython_exit": cpython.exit_code,
                "cpython_exception": cpython.exception_type,
                "piton_stdout": piton.stdout,
            }
            return result

        return self._compare_failures(cpython, piton, result)

    @staticmethod
    def _reference_failure(cpython: CPythonResult) -> str:
        if cpython.status == "compile_error":
            return f"compile_error {cpython.exception_type}: {cpython.exception_message}"
        if cpython.exception_type:
            return f"{cpython.status} {cpython.exception_type}: {cpython.exception_message}"
        return f"{cpython.status} exit={cpython.exit_code}"

    @staticmethod
    def _stage_error_detail(piton: PitonResult) -> Dict[str, Any]:
        stage = piton.failed_stage.value if piton.failed_stage else None
        info = piton.stages.get(stage, {}) if stage else {}
        return {
            "stage": stage,
            "error_type": info.get("error_type"),
            "error_message": info.get("error_message"),
        }
    
    def _compare_success(
        self,
        cpython: CPythonResult,
        piton: PitonResult,
        result: ComparisonResult,
    ) -> ComparisonResult:
        cp_stdout = self._normalize_output(cpython.stdout)
        pt_stdout = self._normalize_output(piton.stdout)
        cp_stderr = self._normalize_output(cpython.stderr)
        pt_stderr = self._normalize_output(piton.stderr)
        if (
            cp_stdout == pt_stdout
            and cp_stderr == pt_stderr
            and cpython.exit_code == piton.exit_code
        ):
            result.classification = Classification.EQUIV
            result.reason = "identical stdout, stderr and exit status"
            return result
        result.classification = Classification.DIVERGENT
        result.reason = "both completed but observable output differs"
        result.details = {
            "cpython_stdout": cpython.stdout,
            "piton_stdout": piton.stdout,
            "cpython_stderr": cpython.stderr,
            "piton_stderr": piton.stderr,
            "cpython_exit": cpython.exit_code,
            "piton_exit": piton.exit_code,
        }
        return result

    def _compare_failures(
        self,
        cpython: CPythonResult,
        piton: PitonResult,
        result: ComparisonResult,
    ) -> ComparisonResult:
        cp_stdout = self._normalize_output(cpython.stdout)
        pt_stdout = self._normalize_output(piton.stdout)
        if cp_stdout != pt_stdout:
            result.classification = Classification.DIVERGENT
            result.reason = "both failed but stdout differs"
            result.details = {
                "cpython_stdout": cpython.stdout,
                "piton_stdout": piton.stdout,
            }
            return result

        cp_exc = self._normalize_exception(cpython.exception_type)
        pt_exc = self._normalize_exception(piton.exception_type)
        if not cp_exc or not pt_exc:
            result.classification = Classification.DIVERGENT
            result.reason = "both failed but at least one has no structured exception"
            result.details = {
                "cpython_exit": cpython.exit_code,
                "piton_exit": piton.exit_code,
                "piton_stderr": piton.stderr[-2000:],
            }
            return result

        if cp_exc != pt_exc:
            result.classification = Classification.DIVERGENT
            result.reason = f"different exception types: CPython={cp_exc}, PITON={pt_exc}"
            return result

        cp_msg = self._normalize_exception_message(cpython.exception_message or "")
        pt_msg = self._normalize_exception_message(piton.exception_message or "")
        if cp_msg != pt_msg:
            result.classification = Classification.DIVERGENT
            result.reason = f"both raise {cp_exc} but messages differ"
            result.details = {
                "cpython_message": cpython.exception_message,
                "piton_message": piton.exception_message,
            }
            return result

        result.classification = Classification.EQUIV
        result.reason = f"both raise {cp_exc} with the same message"
        return result

    def _normalize_output(self, output: str) -> str:
        return output.replace("\r\n", "\n").replace("\r", "\n").rstrip()

    def _normalize_exception(self, exc_type: Optional[str]) -> str:
        if not exc_type:
            return ""
        return exc_type.rsplit(".", 1)[-1]

    def _normalize_exception_message(self, msg: str) -> str:
        if not msg:
            return ""
        msg = msg.replace("\r\n", "\n").replace("\r", "\n").strip()
        msg = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", msg)
        return msg.replace("/work/", "")


# Global comparator instance
_comparator: Optional[DifferentialComparator] = None


def get_comparator() -> DifferentialComparator:
    """Get or create the global comparator."""
    global _comparator
    if _comparator is None:
        _comparator = DifferentialComparator()
    return _comparator
