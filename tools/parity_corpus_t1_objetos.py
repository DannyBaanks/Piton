#!/usr/bin/env python3
"""Runner honesto del corpus T1 de objetos y métodos.

Compara los esperados capturados previamente con CPython 3.12 contra el
backend nativo de PITON. Usa exactamente el camino del corpus primario:
``parity_corpus_v4._stage_impl`` y ``piton.linux_x86.compile_native_linux``.

Criterio:
- ``EQUIV``: ambos terminan con el mismo código y, o bien stdout coincide byte
  a byte, o bien ambos lanzan la excepción con el mismo tipo y mensaje.
- ``DIVERGE``: cualquier otra ejecución; el diff conserva bytes y texto sin
  normalizar.
- ``BUILD_FAIL``: el ejemplo no llegó a compilarse. No se descarta: cuenta
  como divergencia y aparece en el resumen.

Uso:
    python3 tools/parity_corpus_t1_objetos.py \
        --json docs/parity_corpus_t1_objetos_report.json
"""
from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import json
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import parity_corpus_v4 as primary
from piton.linux_x86 import compile_native_linux

CORPUS_DIR = REPO / "corpus_t1" / "objetos"
EXPECTED_PATH = REPO / "corpus_t1" / "expected" / "objetos.json"
EXCEPTION_RE = re.compile(
    r"^([A-Za-z_][A-Za-z_0-9]*(?:Error|Exception|Warning)): ?(.*)$",
    re.MULTILINE,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _exception_info(stderr: str) -> dict[str, str] | None:
    matches = list(EXCEPTION_RE.finditer(stderr))
    if not matches:
        return None
    match = matches[-1]
    return {"type": match.group(1), "message": match.group(2)}


def _text(blob: bytes) -> str:
    return blob.decode("utf-8", "replace")


def _byte_diff(expected: bytes, actual: bytes) -> dict:
    limit = min(len(expected), len(actual))
    offset = limit
    for index in range(limit):
        if expected[index] != actual[index]:
            offset = index
            break
    window = 64
    return {
        "first_difference_byte": offset,
        "expected_length": len(expected),
        "actual_length": len(actual),
        "expected_window_hex": expected[offset:offset + window].hex(),
        "actual_window_hex": actual[offset:offset + window].hex(),
        "expected_window_repr": repr(expected[offset:offset + window]),
        "actual_window_repr": repr(actual[offset:offset + window]),
    }


def _unified_diff(expected: bytes, actual: bytes, expected_name: str, actual_name: str) -> str:
    expected_lines = _text(expected).splitlines(keepends=True)
    actual_lines = _text(actual).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            expected_lines,
            actual_lines,
            fromfile=expected_name,
            tofile=actual_name,
        )
    )


def _process_record(returncode: int, stdout: bytes, stderr: bytes) -> dict:
    stderr_text = _text(stderr)
    return {
        "exit_code": returncode,
        "stdout": _text(stdout),
        "stderr": stderr_text,
        "stdout_base64": base64.b64encode(stdout).decode("ascii"),
        "stderr_base64": base64.b64encode(stderr).decode("ascii"),
        "exception": _exception_info(stderr_text),
    }


def _compare(expected: dict, actual: dict) -> tuple[str, dict | None]:
    expected_stdout = expected["stdout"].encode("utf-8")
    actual_stdout = actual["stdout_base64"]
    actual_stdout_bytes = base64.b64decode(actual_stdout)
    expected_stderr = expected["stderr"].encode("utf-8")
    actual_stderr_bytes = base64.b64decode(actual["stderr_base64"])

    expected_exception = expected.get("exception")
    actual_exception = actual.get("exception")
    same_exception = (
        expected_exception is not None
        and actual_exception is not None
        and expected_exception["type"] == actual_exception["type"]
        and expected_exception["message"] == actual_exception["message"]
    )

    if expected["exit_code"] == 0 and actual["exit_code"] == 0:
        if expected_stdout == actual_stdout_bytes:
            return "EQUIV", None
        kind = "stdout_mismatch"
    elif expected["exit_code"] != 0 and actual["exit_code"] != 0:
        if expected["exit_code"] == actual["exit_code"] and same_exception:
            return "EQUIV", None
        kind = "exception_mismatch"
    elif expected["exit_code"] != 0:
        kind = "piton_did_not_raise"
    else:
        kind = "piton_raised_unexpectedly"

    diff = {
        "kind": kind,
        "expected": expected,
        "actual": actual,
        "stdout_byte_diff": _byte_diff(expected_stdout, actual_stdout_bytes),
        "stdout_unified_diff": _unified_diff(
            expected_stdout, actual_stdout_bytes, "cpython.stdout", "piton.stdout"
        ),
        "stderr_byte_diff": _byte_diff(expected_stderr, actual_stderr_bytes),
        "stderr_unified_diff": _unified_diff(
            expected_stderr, actual_stderr_bytes, "cpython.stderr", "piton.stderr"
        ),
    }
    return "DIVERGE", diff


def _git_status() -> list[str]:
    proc = subprocess.run(
        ["git", "status", "--short"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.splitlines() if proc.returncode == 0 else ["git status failed"]


def _git_sha() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def _build_fail(name: str, path: Path, expected: dict, stage: dict, detail: str) -> dict:
    return {
        "name": name,
        "path": str(path.relative_to(REPO)),
        "status": "BUILD_FAIL",
        "expected_sha256": expected["sha256"],
        "actual_sha256": _sha256(path),
        "stage": stage,
        "detail": detail,
        "diff": {
            "kind": "build_fail",
            "expected": expected,
            "actual": None,
            "stage": stage,
            "detail": detail,
        },
    }


def run_corpus(corpus_dir: Path, expected_path: Path, timeout: float) -> dict:
    payload = json.loads(expected_path.read_text(encoding="utf-8"))
    expected_examples = payload.get("examples", {})
    files = sorted(corpus_dir.glob("*.py"))
    expected_names = set(expected_examples)
    actual_names = {path.name for path in files}
    if expected_names != actual_names:
        raise RuntimeError(
            "corpus/expected mismatch: "
            f"missing_expected={sorted(actual_names - expected_names)} "
            f"missing_corpus={sorted(expected_names - actual_names)}"
        )

    results = []
    for path in files:
        expected = expected_examples[path.name]
        actual_hash = _sha256(path)
        if actual_hash != expected["sha256"]:
            result = _build_fail(
                path.name,
                path,
                expected,
                {"stage_fail": "EXPECTED_HASH_MISMATCH"},
                f"expected sha256={expected['sha256']} actual sha256={actual_hash}",
            )
            results.append(result)
            continue

        try:
            stage, adapted = primary._stage_impl(str(path))
        except Exception as exc:
            result = _build_fail(
                path.name,
                path,
                expected,
                {"stage_fail": "INSTRUMENT_ERROR"},
                f"{type(exc).__name__}: {exc}",
            )
            results.append(result)
            continue

        if stage.get("stage_fail"):
            result = _build_fail(
                path.name,
                path,
                expected,
                stage,
                str(stage.get("detail", "stage failed")),
            )
            results.append(result)
            continue

        try:
            with tempfile.TemporaryDirectory(prefix="piton-t1-objetos-") as directory:
                executable = compile_native_linux(adapted, Path(directory) / "p")
                native = subprocess.run(
                    [str(executable)],
                    cwd=directory,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=timeout,
                )
        except subprocess.TimeoutExpired as exc:
            result = _build_fail(
                path.name,
                path,
                expected,
                {"stage_fail": "TIMEOUT"},
                f"native timeout after {timeout}s: {exc}",
            )
            results.append(result)
            continue
        except Exception as exc:
            result = _build_fail(
                path.name,
                path,
                expected,
                {"stage_fail": "BUILD_FAIL"},
                f"{type(exc).__name__}: {exc}",
            )
            results.append(result)
            continue

        actual = _process_record(native.returncode, native.stdout, native.stderr)
        status, diff = _compare(expected, actual)
        results.append(
            {
                "name": path.name,
                "path": str(path.relative_to(REPO)),
                "status": status,
                "expected_sha256": expected["sha256"],
                "actual_sha256": actual_hash,
                "stage": stage,
                "actual": actual,
                "diff": diff,
            }
        )

    by_status: dict[str, int] = {}
    for result in results:
        status = result["status"]
        by_status[status] = by_status.get(status, 0) + 1
    equiv = by_status.get("EQUIV", 0)
    diverge = len(results) - equiv
    not_demonstrated = [
        result["name"]
        for result in results
        if result["status"] == "BUILD_FAIL"
    ]
    return {
        "meta": {
            "schema": "piton-corpus-t1-parity-v1",
            "area": "objetos",
            "backend": "piton.linux_x86.compile_native_linux",
            "primary_path": [
                "tools/parity_corpus_v4.py:_stage_impl",
                "piton.linux_x86.compile_native_linux",
            ],
            "oracle_version": platform.python_version(),
            "expected_path": str(expected_path.relative_to(REPO)),
            "expected_sha256": _sha256(expected_path),
            "git_sha": _git_sha(),
            "git_status_short": _git_status(),
            "working_tree_dirty": bool(_git_status()),
            "diverge_definition": "non-EQUIV, including BUILD_FAIL",
            "timeout_seconds": timeout,
        },
        "total": len(results),
        "equiv": equiv,
        "diverge": diverge,
        "by_status": dict(sorted(by_status.items())),
        "not_demonstrated": not_demonstrated,
        "results": results,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS_DIR)
    parser.add_argument("--expected", type=Path, default=EXPECTED_PATH)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)

    report = run_corpus(args.corpus, args.expected, args.timeout)
    encoded = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
