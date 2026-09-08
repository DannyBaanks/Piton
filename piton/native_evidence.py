"""Reproducible evidence for the Windows x86-64 native subset."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .translator import traducir_fuente
from .x86 import NativeBuildError


ORACLE_FLAGS = ("-X", "utf8", "-B", "-I", "-S")
ORACLE_ENV = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
}
EVIDENCE_GATES = frozenset({
    "X86_CODE_EMITTED",
    "EXECUTABLE_LINKED",
    "PE_IMPORT_TABLE_READ",
    "PE_IMPORTS_PYTHON",
    "PYTHON_MARKER_ABSENT",
    "WINDOWS_EMPTY_ENV_EXECUTION",
    "NATIVE_DIFFERENTIAL_SUBSET",
    "NATIVE_CORPUS",
    "ORACLE_PINNED",
})
_VERIFIED_EVIDENCE = object()


@dataclass(frozen=True, init=False)
class VerifiedWindowsEvidence:
    receipt: dict[str, Any]

    def __init__(self, receipt: dict[str, Any], verification: object) -> None:
        if verification is not _VERIFIED_EVIDENCE:
            raise TypeError("use load_windows_evidence() to obtain verified evidence")
        object.__setattr__(self, "receipt", receipt)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_version(command: str, *arguments: str) -> str:
    executable = shutil.which(command)
    if not executable:
        return "ABSENT"
    completed = subprocess.run(
        [executable, *arguments], capture_output=True, text=True, check=False,
        encoding="utf-8", errors="replace",
    )
    output = completed.stdout or completed.stderr
    return output.splitlines()[0] if output else f"exit={completed.returncode}"


def inspect_windows_pe(executable: str | Path) -> dict[str, Any]:
    path = Path(executable).resolve()
    image = path.read_bytes()
    if image[:2] != b"MZ" or len(image) < 0x40:
        raise NativeBuildError(f"not a PE executable: {path}")
    pe_offset = struct.unpack_from("<I", image, 0x3C)[0]
    if image[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise NativeBuildError(f"invalid PE signature: {path}")
    machine = struct.unpack_from("<H", image, pe_offset + 4)[0]

    objdump = shutil.which("objdump")
    if not objdump:
        raise NativeBuildError("native evidence requires objdump in PATH")
    inspected = subprocess.run(
        [objdump, "-p", str(path)], capture_output=True, text=True, check=False,
        encoding="utf-8", errors="replace",
    )
    if inspected.returncode:
        raise NativeBuildError(inspected.stderr or inspected.stdout)
    imports = sorted(set(re.findall(r"DLL Name:\s*(\S+)", inspected.stdout, re.IGNORECASE)))
    python_imports = [name for name in imports if "python" in name.lower()]

    return {
        "format": "PE",
        "machine": f"0x{machine:04x}",
        "x86_64": machine == 0x8664,
        "size": len(image),
        "sha256": _sha256(path),
        "imports": imports,
        "python_imports": python_imports,
        "python_marker_in_image": b"python" in image.lower(),
    }


def _observe(source_path: Path, executable_path: Path) -> tuple[subprocess.CompletedProcess[bytes], subprocess.CompletedProcess[bytes]]:
    source_text = source_path.read_text(encoding="utf-8-sig")
    native = subprocess.run(
        [str(executable_path)], cwd=executable_path.parent, env={},
        capture_output=True, check=False,
    )
    oracle = subprocess.run(
        [sys.executable, *ORACLE_FLAGS, "-c", traducir_fuente(source_text, str(source_path))],
        cwd=source_path.parent, env=ORACLE_ENV, capture_output=True, check=False,
    )
    return native, oracle


def _run_native_corpus() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    test_path = root / "tests" / "test_phase5.py"
    environment = os.environ.copy()
    environment.update(ORACLE_ENV)
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.test_phase5", "-q"],
        cwd=root, env=environment, capture_output=True, check=False,
    )
    output = completed.stdout + completed.stderr
    match = re.search(rb"Ran (\d+) tests?", output)
    return {
        "path": test_path,
        "sha256": _sha256(test_path),
        "exit_code": completed.returncode,
        "test_count": int(match.group(1)) if match else 0,
        "stdout_hex": completed.stdout.hex(),
        "stderr_hex": completed.stderr.hex(),
    }


def _normalize_corpus_output(output: bytes) -> bytes:
    return re.sub(rb"Ran (\d+) tests? in [0-9.]+s", rb"Ran \1 tests in <TIME>s", output)


def _gate_results(
    inspection: dict[str, Any],
    native: subprocess.CompletedProcess[bytes],
    oracle: subprocess.CompletedProcess[bytes],
    corpus: dict[str, Any],
) -> dict[str, str]:
    equivalent = (native.returncode, native.stdout, native.stderr) == (
        oracle.returncode, oracle.stdout, oracle.stderr,
    )
    gates = {
        "X86_CODE_EMITTED": inspection["x86_64"],
        "EXECUTABLE_LINKED": True,
        "PE_IMPORT_TABLE_READ": bool(inspection["imports"]),
        "PE_IMPORTS_PYTHON": not inspection["python_imports"],
        "PYTHON_MARKER_ABSENT": not inspection["python_marker_in_image"],
        "WINDOWS_EMPTY_ENV_EXECUTION": native.returncode == 0,
        "NATIVE_DIFFERENTIAL_SUBSET": equivalent,
        "NATIVE_CORPUS": corpus["exit_code"] == 0 and corpus["test_count"] > 0,
        "ORACLE_PINNED": platform.python_implementation() == "CPython" and platform.python_version() == "3.12.4",
    }
    return {name: "PASS" if passed else "FAIL" for name, passed in gates.items()}


def build_windows_evidence(
    source: str | Path,
    executable: str | Path,
    report: str | Path,
) -> dict[str, Any]:
    source_path = Path(source).resolve()
    executable_path = Path(executable).resolve()
    report_path = Path(report).resolve()
    inspection = inspect_windows_pe(executable_path)
    native, oracle = _observe(source_path, executable_path)
    corpus = _run_native_corpus()
    gates = _gate_results(inspection, native, oracle, corpus)
    receipt = {
        "schema": "piton-native-subset-evidence-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": os.path.relpath(source_path, report_path.parent).replace("\\", "/"),
            "name": source_path.name,
            "sha256": _sha256(source_path),
        },
        "artifact": {
            "path": os.path.relpath(executable_path, report_path.parent).replace("\\", "/"),
            **inspection,
        },
        "native_observation": {
            "exit_code": native.returncode,
            "stdout_hex": native.stdout.hex(),
            "stderr_hex": native.stderr.hex(),
            "environment": "empty",
        },
        "oracle_observation": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "flags": list(ORACLE_FLAGS),
            "exit_code": oracle.returncode,
            "stdout_hex": oracle.stdout.hex(),
            "stderr_hex": oracle.stderr.hex(),
        },
        "native_corpus": {
            "path": os.path.relpath(corpus["path"], report_path.parent).replace("\\", "/"),
            "sha256": corpus["sha256"],
            "exit_code": corpus["exit_code"],
            "test_count": corpus["test_count"],
            "stdout_hex": corpus["stdout_hex"],
            "stderr_hex": corpus["stderr_hex"],
        },
        "toolchain": {
            "nasm": _tool_version("nasm", "-v"),
            "gcc": _tool_version("gcc", "--version"),
            "objdump": _tool_version("objdump", "--version"),
        },
        "gates": gates,
        "native_subset_1_0": "PASS" if all(state == "PASS" for state in gates.values()) else "FAIL",
        "windows_clean_machine_execution": "NOT_DEMONSTRATED",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def load_windows_evidence(report: str | Path) -> VerifiedWindowsEvidence:
    report_path = Path(report).resolve()
    receipt = json.loads(report_path.read_text(encoding="utf-8"))
    if receipt.get("schema") != "piton-native-subset-evidence-v1":
        raise NativeBuildError("invalid native evidence schema")
    artifact = receipt.get("artifact", {})
    relative_path = artifact.get("path")
    if not relative_path:
        raise NativeBuildError("native evidence does not identify its artifact")
    artifact_path = (report_path.parent / relative_path).resolve()
    if not artifact_path.is_file():
        raise NativeBuildError(f"native evidence artifact is missing: {artifact_path}")
    if _sha256(artifact_path) != artifact.get("sha256"):
        raise NativeBuildError("native evidence artifact SHA-256 mismatch")
    source = receipt.get("source", {})
    source_relative_path = source.get("path")
    if not source_relative_path:
        raise NativeBuildError("native evidence does not identify its source")
    source_path = (report_path.parent / source_relative_path).resolve()
    if not source_path.is_file():
        raise NativeBuildError(f"native evidence source is missing: {source_path}")
    if _sha256(source_path) != source.get("sha256"):
        raise NativeBuildError("native evidence source SHA-256 mismatch")
    corpus_record = receipt.get("native_corpus", {})
    corpus_relative_path = corpus_record.get("path")
    if not corpus_relative_path:
        raise NativeBuildError("native evidence does not identify its corpus")
    corpus_path = (report_path.parent / corpus_relative_path).resolve()
    if not corpus_path.is_file() or _sha256(corpus_path) != corpus_record.get("sha256"):
        raise NativeBuildError("native evidence corpus SHA-256 mismatch")

    current_inspection = inspect_windows_pe(artifact_path)
    recorded_inspection = {key: value for key, value in artifact.items() if key != "path"}
    if current_inspection != recorded_inspection:
        raise NativeBuildError("native evidence PE inspection mismatch")
    native, oracle = _observe(source_path, artifact_path)
    corpus = _run_native_corpus()
    expected_gates = _gate_results(current_inspection, native, oracle, corpus)
    if set(receipt.get("gates", {})) != EVIDENCE_GATES:
        raise NativeBuildError("native evidence gate set is incomplete")
    if receipt["gates"] != expected_gates or receipt.get("native_subset_1_0") != "PASS":
        raise NativeBuildError("native evidence gate result mismatch")
    expected_native = {
        "exit_code": native.returncode,
        "stdout_hex": native.stdout.hex(),
        "stderr_hex": native.stderr.hex(),
        "environment": "empty",
    }
    if receipt.get("native_observation") != expected_native:
        raise NativeBuildError("native evidence observation mismatch")
    expected_oracle = {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "flags": list(ORACLE_FLAGS),
        "exit_code": oracle.returncode,
        "stdout_hex": oracle.stdout.hex(),
        "stderr_hex": oracle.stderr.hex(),
    }
    if receipt.get("oracle_observation") != expected_oracle:
        raise NativeBuildError("native evidence oracle observation mismatch")
    for key in ("sha256", "exit_code", "test_count"):
        if corpus_record.get(key) != corpus[key]:
            raise NativeBuildError("native evidence corpus result mismatch")
    for key in ("stdout_hex", "stderr_hex"):
        try:
            recorded_output = bytes.fromhex(corpus_record.get(key, ""))
            current_output = bytes.fromhex(corpus[key])
        except ValueError as error:
            raise NativeBuildError("native evidence corpus output is invalid") from error
        if _normalize_corpus_output(recorded_output) != _normalize_corpus_output(current_output):
            raise NativeBuildError("native evidence corpus output mismatch")
    expected_toolchain = {
        "nasm": _tool_version("nasm", "-v"),
        "gcc": _tool_version("gcc", "--version"),
        "objdump": _tool_version("objdump", "--version"),
    }
    if receipt.get("toolchain") != expected_toolchain:
        raise NativeBuildError("native evidence toolchain mismatch")
    if receipt.get("windows_clean_machine_execution") != "NOT_DEMONSTRATED":
        raise NativeBuildError("native evidence contains an unsupported clean-machine claim")
    return VerifiedWindowsEvidence(receipt, _VERIFIED_EVIDENCE)
