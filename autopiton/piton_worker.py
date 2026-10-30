"""PITON compile-pipeline worker, executed inside the AutoPiton sandbox.

Stages performed here: ADAPT (Python -> PITON surface), PARSE, MIR lowering,
BUILD (C emission, gcc, artifact verification). Execution is NOT performed
here: the sandbox runs the resulting static ELF in a separate minimal root.

The worker imports the real PITON compiler from a read-only repository bind
(``/piton-repo``) and never modifies it. gcc failures are fatal here, unlike
``piton.linux_x86._gcc_compile`` which currently swallows them; the artifact
is verified (exists, non-empty, ELF magic) before BUILD is declared passed.
"""

import hashlib
import json
import os
import subprocess
import sys
import traceback

WORKER_FILENAME = "piton_worker.py"
INPUT_FILENAME = ".autopiton_input.json"
RESULT_FILENAME = "autopiton_piton_result.json"
ADAPTED_FILENAME = "program_adapted.piton"
C_FILENAME = "program.c"
ARTIFACT_FILENAME = "program.bin"

GCC_TIMEOUT_SECONDS = 30
GCC_FLAGS = [
    "-std=c11",
    "-w",
    "-Wno-error",
    "-O2",
    "-ffreestanding",
    "-fno-stack-protector",
    "-fno-pie",
    "-no-pie",
    "-nostdlib",
    "-static",
]


def _error_payload(exc: BaseException) -> dict:
    return {
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "error_traceback": "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )[-4000:],
    }


def _write_result(workdir: str, report: dict) -> None:
    path = os.path.join(workdir, RESULT_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, sort_keys=True)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main(argv: list) -> int:
    if len(argv) != 3:
        sys.stderr.write("usage: piton_worker.py <repo> <workdir> <input_json>\n")
        return 2
    repo, workdir, input_path = argv
    sys.path.insert(0, repo)

    try:
        with open(input_path, "r", encoding="utf-8") as handle:
            request = json.load(handle)
        source = request["source"]
        filename = request.get("filename", "program.py")
    except BaseException as exc:
        _write_result(workdir, {"failed_stage": "HARNESS", "stages": {}, **_error_payload(exc)})
        return 2

    report = {
        "format": "autopiton-piton-worker/1",
        "filename": filename,
        "source_sha256": _sha256_text(source),
        "stages": {},
        "failed_stage": None,
    }

    def fail(stage: str, exc: BaseException) -> int:
        report["failed_stage"] = stage
        report["stages"][stage] = {"passed": False, **_error_payload(exc)}
        _write_result(workdir, report)
        return 1

    try:
        from piton.parser import parse
        from piton.lower import lower_cst_to_hir
        from piton.mir import lower_hir_to_mir
        from piton.linux_x86 import LinuxCEmitter
        from tools.parity_corpus_v4 import python_to_piton
    except BaseException as exc:
        _write_result(workdir, {"failed_stage": "HARNESS", "stages": {}, **_error_payload(exc)})
        return 2

    try:
        adapted = python_to_piton(source)
    except BaseException as exc:
        return fail("ADAPT", exc)
    report["stages"]["ADAPT"] = {
        "passed": True,
        "surface_sha256": _sha256_text(adapted),
        "surface_chars": len(adapted),
    }
    with open(os.path.join(workdir, ADAPTED_FILENAME), "w", encoding="utf-8") as handle:
        handle.write(adapted)

    try:
        cst = parse(adapted)
    except BaseException as exc:
        return fail("PARSE", exc)
    report["stages"]["PARSE"] = {"passed": True}

    try:
        mir = lower_hir_to_mir(lower_cst_to_hir(cst))
    except BaseException as exc:
        return fail("MIR", exc)
    report["stages"]["MIR"] = {"passed": True}

    try:
        c_source = LinuxCEmitter().emit(mir)
    except BaseException as exc:
        return fail("BUILD", exc)
    c_path = os.path.join(workdir, C_FILENAME)
    with open(c_path, "w", encoding="utf-8") as handle:
        handle.write(c_source)
    artifact_path = os.path.join(workdir, ARTIFACT_FILENAME)
    build = {
        "passed": False,
        "c_sha256": _sha256_text(c_source),
        "c_chars": len(c_source),
    }
    report["stages"]["BUILD"] = build

    try:
        completed = subprocess.run(
            ["gcc", *GCC_FLAGS, c_path, "-o", artifact_path],
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=GCC_TIMEOUT_SECONDS,
            check=False,
        )
    except BaseException as exc:
        return fail("BUILD", exc)

    build["gcc_returncode"] = completed.returncode
    build["gcc_stderr"] = completed.stderr[-8000:]
    if completed.returncode != 0:
        build["error_type"] = "GccError"
        build["error_message"] = (completed.stderr.strip() or f"gcc exited {completed.returncode}")[-2000:]
        report["failed_stage"] = "BUILD"
        _write_result(workdir, report)
        return 1

    try:
        size = os.path.getsize(artifact_path)
        with open(artifact_path, "rb") as handle:
            magic = handle.read(4)
    except OSError as exc:
        return fail("BUILD", exc)
    build["artifact_name"] = ARTIFACT_FILENAME
    build["artifact_bytes"] = size
    if size == 0 or magic != b"\x7fELF":
        build["error_type"] = "ArtifactInvalid"
        build["error_message"] = f"size={size} magic={magic!r}"
        report["failed_stage"] = "BUILD"
        _write_result(workdir, report)
        return 1

    build["passed"] = True
    with open(artifact_path, "rb") as handle:
        build["artifact_sha256"] = hashlib.sha256(handle.read()).hexdigest()
    _write_result(workdir, report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
