#!/usr/bin/env python3
"""Runner del corpus T1 de FUNCIONES DE LIBRERIA (el punto ciego T7).

Mide exactamente lo que el corpus primario no puede: una funcion propia que
LLAMA a una funcion de libreria e imprime su valor de retorno (AGENTS2 §T7).
El corpus primario solo ejecuta sentencias de modulo, donde str/join/slice
funcionan; aqui el retorno cruza la frontera de funcion.

Dos modos, en este orden (metodologia T1):

  --capture   captura los esperados del ORACULO (CPython 3.12 del sistema) y
              escribe corpus_t1/expected/funciones.json. Se ejecuta ANTES de
              mirar PITON. Nunca se ejecuta el backend nativo en este modo.
  (default)   mide PITON por el mismo camino que el corpus primario:
              parity_corpus_v4._stage_impl + piton.linux_x86.compile_native_linux.

Criterio (AGENTS2 §1, sin normalizar nada):
  - EQUIV: rc 0 en ambos y stdout byte a byte identico; o rc != 0 en ambos con
    el mismo exception type Y mensaje (y el mismo rc).
  - DIVERGE: cualquier otro caso. BUILD_FAIL (no compila / timeout de compilado)
    cuenta como divergencia: el denominador NO se encoge.
  - sha256 del ejemplo debe coincidir con el esperado; si no, BUILD_FAIL.

Uso:
  python3 tools/parity_corpus_t1_funciones.py --capture
  python3 tools/parity_corpus_t1_funciones.py [--json RUTA]
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

CORPUS_DIR = REPO / "corpus_t1" / "funciones"
EXPECTED_PATH = REPO / "corpus_t1" / "expected" / "funciones.json"
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


def _unified_diff(expected: bytes, actual: bytes,
                  expected_name: str, actual_name: str) -> str:
    expected_lines = _text(expected).splitlines(keepends=True)
    actual_lines = _text(actual).splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        expected_lines, actual_lines,
        fromfile=expected_name, tofile=actual_name))


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
    actual_stdout_bytes = base64.b64decode(actual["stdout_base64"])
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
            expected_stdout, actual_stdout_bytes, "cpython.stdout", "piton.stdout"),
        "stderr_byte_diff": _byte_diff(expected_stderr, actual_stderr_bytes),
        "stderr_unified_diff": _unified_diff(
            expected_stderr, actual_stderr_bytes, "cpython.stderr", "piton.stderr"),
    }
    return "DIVERGE", diff


def _git_sha() -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                          capture_output=True, text=True, timeout=10)
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def _git_status() -> list[str]:
    proc = subprocess.run(["git", "status", "--short"], cwd=REPO,
                          capture_output=True, text=True, timeout=10)
    return proc.stdout.splitlines() if proc.returncode == 0 else ["git status failed"]


def capture_corpus(corpus_dir: Path, expected_path: Path,
                   oracle_timeout: float = 5.0) -> dict:
    """Esperados del ORACULO. CPython 3.12 real; PITON no se invoca aqui."""
    files = sorted(corpus_dir.glob("*.py"))
    if not files:
        raise SystemExit(f"corpus vacio: {corpus_dir}")
    examples: dict[str, dict] = {}
    for path in files:
        proc = subprocess.run([sys.executable, str(path)], capture_output=True,
                              timeout=oracle_timeout, stdin=subprocess.DEVNULL)
        record = _process_record(proc.returncode, proc.stdout, proc.stderr)
        examples[path.name] = {
            "path": str(path.relative_to(REPO)),
            "sha256": _sha256(path),
            "stdout": record["stdout"],
            "stderr": record["stderr"],
            "exit_code": record["exit_code"],
            "exception": record["exception"],
        }
    payload = {
        "schema": "piton-corpus-t1-funciones-expected-v1",
        "area": "funciones",
        "oracle": f"CPython {platform.python_version()}",
        "captured_before_piton": True,
        "capture_method": "subprocess [sys.executable, <file>], stdout+stderr+exit code crudos",
        "total": len(examples),
        "git_sha": _git_sha(),
        "examples": examples,
    }
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    expected_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _build_fail(name: str, path: Path, expected: dict,
                stage_fail: str, detail: str) -> dict:
    return {
        "name": name,
        "path": str(path.relative_to(REPO)),
        "status": "BUILD_FAIL",
        "expected_sha256": expected["sha256"],
        "actual_sha256": _sha256(path),
        "stage_fail": stage_fail,
        "detail": detail,
        "diff": {
            "kind": "build_fail",
            "expected": expected,
            "actual": None,
            "stage_fail": stage_fail,
            "detail": detail,
        },
    }


def run_corpus(corpus_dir: Path, expected_path: Path, timeout: float) -> dict:
    payload = json.loads(expected_path.read_text(encoding="utf-8"))
    expected_examples = payload.get("examples", {})
    files = sorted(corpus_dir.glob("*.py"))
    if set(expected_examples) != {path.name for path in files}:
        raise RuntimeError(
            "corpus/expected mismatch: "
            f"missing_expected={sorted({p.name for p in files} - set(expected_examples))} "
            f"missing_corpus={sorted(set(expected_examples) - {p.name for p in files})}")

    results = []
    for path in files:
        name = path.name
        expected = expected_examples[name]
        actual_hash = _sha256(path)
        if actual_hash != expected["sha256"]:
            results.append(_build_fail(
                name, path, expected, "EXPECTED_HASH_MISMATCH",
                f"expected sha256={expected['sha256']} actual sha256={actual_hash}"))
            continue
        try:
            stage, adapted = primary._stage_impl(str(path))
        except Exception as exc:
            results.append(_build_fail(
                name, path, expected, "INSTRUMENT_ERROR",
                f"{type(exc).__name__}: {exc}"))
            continue
        if stage.get("stage_fail"):
            results.append(_build_fail(
                name, path, expected, stage["stage_fail"],
                str(stage.get("detail", "stage failed"))))
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="piton-t1-func-") as directory:
                executable = compile_native_linux(adapted, Path(directory) / "p")
                native = subprocess.run(
                    [str(executable)], cwd=directory, stdin=subprocess.DEVNULL,
                    capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            results.append(_build_fail(
                name, path, expected, "TIMEOUT",
                f"native run did not terminate after {timeout}s"))
            continue
        except Exception as exc:
            results.append(_build_fail(
                name, path, expected, "BUILD_FAIL",
                f"{type(exc).__name__}: {exc}"))
            continue
        actual = _process_record(native.returncode, native.stdout, native.stderr)
        status, diff = _compare(expected, actual)
        results.append({
            "name": name,
            "path": str(path.relative_to(REPO)),
            "status": status,
            "expected_sha256": expected["sha256"],
            "actual_sha256": actual_hash,
            "stage": stage,
            "actual": actual,
            "diff": diff,
        })

    by_status: dict[str, int] = {}
    for result in results:
        by_status[result["status"]] = by_status.get(result["status"], 0) + 1
    equiv = by_status.get("EQUIV", 0)
    diverge = len(results) - equiv
    build_failed = [r["name"] for r in results if r["status"] == "BUILD_FAIL"]
    return {
        "meta": {
            "schema": "piton-corpus-t1-parity-v1",
            "area": "funciones",
            "blind_spot": "AGENTS2 T7: retorno de funcion de libreria, no sentencia de modulo",
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
            "diverge_definition": "non-EQUIV, including BUILD_FAIL",
            "timeout_seconds": timeout,
        },
        "total": len(results),
        "equiv": equiv,
        "diverge": diverge,
        "by_status": dict(sorted(by_status.items())),
        "not_demonstrated": build_failed,
        "results": results,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS_DIR)
    parser.add_argument("--expected", type=Path, default=EXPECTED_PATH)
    parser.add_argument("--capture", action="store_true",
                        help="captura esperados del ORACULO (CPython) y sale; PITON no se invoca")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)

    if args.capture:
        payload = capture_corpus(args.corpus, args.expected)
        print(json.dumps({
            "captured": payload["total"],
            "expected_path": str(args.expected),
            "oracle": payload["oracle"],
            "captured_before_piton": payload["captured_before_piton"],
        }, ensure_ascii=False, indent=2))
        return 0

    report = run_corpus(args.corpus, args.expected, args.timeout)
    encoded = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
