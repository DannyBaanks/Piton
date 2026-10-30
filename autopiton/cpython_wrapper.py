"""Structured CPython runner executed inside the AutoPiton sandbox.

This file is both importable (to expose ``WRAPPER_SOURCE``) and executable
inside the sandbox. It compiles the target source exactly once, executes it
with ``__name__ == "__main__"``, and writes a JSON report describing how the
process ended. Process stdout/stderr are captured separately by the sandbox;
the report adds structured exception information that a traceback alone does
not give reliably.
"""

import json
import sys
import traceback

RESULT_FILENAME = "autopiton_cpython_result.json"

_open = open
_dumps = json.dumps


def _safe_str(value) -> str:
    try:
        return str(value)
    except BaseException:
        return "<unprintable>"


def _exception_payload(exc: BaseException) -> dict:
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    payload = {
        "type": type(exc).__name__,
        "module": type(exc).__module__,
        "message": _safe_str(exc),
        "traceback": text.replace("/work/", ""),
    }
    cause = exc.__cause__
    if cause is not None:
        payload["cause"] = {
            "type": type(cause).__name__,
            "message": _safe_str(cause),
        }
    elif exc.__context__ is not None and not exc.__suppress_context__:
        payload["context"] = {
            "type": type(exc.__context__).__name__,
            "message": _safe_str(exc.__context__),
        }
    return payload


def _write_report(path: str, report: dict) -> None:
    with _open(path, "w", encoding="utf-8") as handle:
        handle.write(_dumps(report, sort_keys=True))


def main(argv: list) -> int:
    if len(argv) != 3:
        sys.stderr.write("usage: cpython_wrapper.py <source> <report> <display_name>\n")
        return 2
    source_path, report_path, display_name = argv
    try:
        with _open(source_path, "r", encoding="utf-8") as handle:
            source = handle.read()
    except OSError as exc:
        _write_report(report_path, {
            "status": "harness_error",
            "exit_code": 2,
            "type": type(exc).__name__,
            "message": _safe_str(exc),
        })
        return 2

    namespace = {
        "__name__": "__main__",
        "__file__": display_name,
        "__builtins__": __builtins__,
    }
    report = {\n        "display_name": display_name,\n        "status": "ok",\n        "exit_code": 0,\n        "python_version": sys.version.split()[0],\n        "python_executable": sys.executable,\n    }

    try:
        code = compile(source, display_name, "exec")
    except BaseException as exc:
        info = _exception_payload(exc)
        report.update(status="compile_error", exit_code=1, **info)
        _write_report(report_path, report)
        sys.stderr.write(info["traceback"])
        return 1

    exit_code = 0
    try:
        exec(code, namespace)
    except SystemExit as exc:
        value = exc.code
        if value is None:
            exit_code = 0
        elif isinstance(value, int):
            exit_code = value
        else:
            sys.stderr.write(f"{_safe_str(value)}\n")
            exit_code = 1
        report.update(status="system_exit", exit_code=exit_code)
    except BaseException as exc:
        info = _exception_payload(exc)
        report.update(status="exception", exit_code=1, **info)
        _write_report(report_path, report)
        sys.stderr.write(info["traceback"])
        return 1

    report["exit_code"] = exit_code
    _write_report(report_path, report)
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
else:
    from pathlib import Path

    WRAPPER_SOURCE = Path(__file__).read_text(encoding="utf-8")
