"""Fail-closed process sandbox for AutoPiton.

Every program executed by AutoPiton runs inside bubblewrap (`bwrap`) with a
private network namespace, a read-only system view, a writable temporary
workdir, and OS resource limits. If bwrap cannot be used, execution is refused.

Only Linux is supported. ``sandbox_available()`` is the single gate used by the
CLI and by tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


class SandboxUnavailable(RuntimeError):
    """Raised when no verified isolation primitive is available."""


_BWRAP = None
_BWRAP_CHECKED = False
PRLIMIT = shutil.which("prlimit") or "/usr/bin/prlimit"


def find_bwrap() -> Optional[str]:
    return shutil.which("bwrap")


def sandbox_available(timeout: float = 5.0) -> bool:
    """Return True only if bwrap can actually start a sandbox here."""
    global _BWRAP, _BWRAP_CHECKED
    if _BWRAP_CHECKED:
        return _BWRAP is not None
    _BWRAP_CHECKED = True
    bwrap = find_bwrap()
    if not bwrap or not sys.platform.startswith("linux"):
        _BWRAP = None
        return False
    cmd = _bwrap_system_root(bwrap) + ["--clearenv", "/usr/bin/true"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception:
        _BWRAP = None
        return False
    _BWRAP = bwrap if proc.returncode == 0 else None
    return _BWRAP is not None


def _bwrap_system_root(bwrap: str) -> List[str]:
    args = [bwrap, "--unshare-all", "--die-with-parent", "--new-session"]
    for path in ("/usr", "/lib", "/lib64"):
        if os.path.exists(path):
            args += ["--ro-bind", path, path]
    args += ["--proc", "/proc", "--dev", "/dev"]
    return args


def _bwrap_python_root(bwrap: str, workdir: Path) -> List[str]:
    args = _bwrap_system_root(bwrap)
    args += [
        "--tmpfs", "/tmp",
        "--bind", str(workdir), "/work",
        "--chdir", "/work",
        "--clearenv",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/work",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "LC_ALL", "C.UTF-8",
    ]
    return args


def _bwrap_native_root(bwrap: str, workdir: Path) -> List[str]:
    # PITON emits static ELF binaries (-static in its gcc flags), so the
    # payload needs no system libraries and can run in a minimal root. Only
    # prlimit and its dynamic dependencies are exposed, because the limit
    # prefix must execute before the payload.
    args = [bwrap, "--unshare-all", "--die-with-parent", "--new-session"]
    args += ["--tmpfs", "/", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    if os.path.exists(PRLIMIT):
        args += ["--ro-bind", PRLIMIT, PRLIMIT]
    for path in ("/lib", "/lib64"):
        if os.path.exists(path):
            args += ["--ro-bind", path, path]
    args += [
        "--bind", str(workdir), "/work",
        "--chdir", "/work",
        "--clearenv",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/work",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "LC_ALL", "C.UTF-8",
    ]
    return args


class _Sink:
    """Bounded byte sink fed by a drain thread."""

    __slots__ = ("limit", "data", "truncated")

    def __init__(self, limit: int):
        self.limit = limit
        self.data = bytearray()
        self.truncated = False

    def feed(self, chunk: bytes) -> None:
        room = self.limit - len(self.data)
        if room <= 0:
            self.truncated = True
            return
        if len(chunk) > room:
            self.data.extend(chunk[:room])
            self.truncated = True
        else:
            self.data.extend(chunk)


def _drain(stream, sink: _Sink) -> None:
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            sink.feed(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _limits_prefix(config: "SandboxConfig") -> List[str]:
    """prlimit prefix applied INSIDE the sandbox namespaces.

    Limits cannot be set from the host via preexec_fn: RLIMIT_NPROC would be
    checked against the real user's process count before bwrap creates its
    user namespace, making clone fail with EAGAIN. prlimit runs after the
    namespaces exist, so NPROC counts only sandbox processes.
    """
    cpu = config.cpu_limit_seconds or max(1, int(config.timeout_seconds) + 1)
    return [
        PRLIMIT,
        f"--as={config.memory_limit_mb * 1024 * 1024}",
        f"--cpu={cpu}",
        f"--nofile={config.max_open_files}",
        f"--fsize={config.file_size_limit_bytes}",
        "--core=0",
        f"--nproc={config.max_processes}",
        "--",
    ]


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def _json_dump(payload: Dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True)


@dataclass
class ExecutionResult:
    exit_code: int
    duration_ms: float
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    memory_exceeded: bool = False
    crash: bool = False
    signal_number: Optional[int] = None
    output_truncated: bool = False
    fs_created: List[Dict[str, object]] = field(default_factory=list)
    backend: str = "bwrap"
    workdir: str = ""
    sandbox_error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return (
            self.exit_code == 0
            and not self.timed_out
            and not self.crash
            and self.sandbox_error is None
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "memory_exceeded": self.memory_exceeded,
            "crash": self.crash,
            "signal_number": self.signal_number,
            "output_truncated": self.output_truncated,
            "fs_created": self.fs_created,
            "backend": self.backend,
            "sandbox_error": self.sandbox_error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "ExecutionResult":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SandboxConfig:
    timeout_seconds: float = 10.0
    memory_limit_mb: int = 256
    max_output_bytes: int = 1_000_000
    file_size_limit_bytes: int = 8_000_000
    cpu_limit_seconds: Optional[int] = None
    max_open_files: int = 64
    max_processes: int = 256
    backend: str = "bwrap"
    keep_workdir: bool = False


class Sandbox:
    """
    Isolated execution sandbox for Python programs.
    
    Provides safe execution with resource limits and isolation.
    """
    
    def __init__(self, config: Optional[SandboxConfig] = None):
        self.config = config or SandboxConfig()
        self._temp_dir: Optional[str] = None
        self._excluded_files: set[str] = set()

    def __enter__(self) -> "Sandbox":
        if not sandbox_available():
            raise SandboxUnavailable(
                "bubblewrap is unavailable or cannot create user namespaces; "
                "AutoPiton refuses to execute generated programs without isolation"
            )
        self._temp_dir = tempfile.mkdtemp(prefix="autopiton_", suffix="_sandbox")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._temp_dir and not self.config.keep_workdir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        return False

    @property
    def workdir(self) -> Path:
        if self._temp_dir is None:
            raise RuntimeError("Sandbox used outside its context manager")
        return Path(self._temp_dir)

    def write_file(self, name: str, content: str, exclude: bool = True) -> Path:
        path = self.workdir / name
        path.write_text(content, encoding="utf-8")
        if exclude:
            self._excluded_files.add(name)
        return path
    
    def run_cpython(self, source_code: str, filename: str = "program.py") -> ExecutionResult:
        """Run original Python source under CPython with a structured wrapper."""
        from .cpython_wrapper import WRAPPER_SOURCE, RESULT_FILENAME

        self.write_file(filename, source_code)
        self.write_file(".autopiton_cpython_runner.py", WRAPPER_SOURCE)
        self._excluded_files.add(RESULT_FILENAME)
        return self._run(
            _bwrap_python_root(find_bwrap(), self.workdir)
            + _limits_prefix(self.config)
            + ["/usr/bin/python3", "-I", "-S", "-B",
               "/work/.autopiton_cpython_runner.py", f"/work/{filename}",
               f"/work/{RESULT_FILENAME}", filename],
            timeout=self.config.timeout_seconds,
        )

    def run_native(self, artifact_name: str = "program.bin") -> ExecutionResult:
        artifact = self.workdir / artifact_name
        if not artifact.is_file() or artifact.stat().st_size == 0:
            raise FileNotFoundError(f"native artifact missing: {artifact}")
        os.chmod(artifact, 0o755)
        self._excluded_files.add(artifact_name)
        return self._run(
            _bwrap_native_root(find_bwrap(), self.workdir)
            + _limits_prefix(self.config)
            + [f"/work/{artifact_name}"],
            timeout=self.config.timeout_seconds,
        )

    def run_build_worker(
        self,
        repo_root: Path,
        source_code: str,
        filename: str = "program.py",
        timeout: float = 60.0,
    ) -> ExecutionResult:
        """Run the PITON stage worker (adapt/parse/mir/emit/gcc) in isolation.

        The worker gets a read-only copy of the repository and a writable
        workdir at /work; it cannot touch the host or the network.
        """
        from .piton_worker import (
            ADAPTED_FILENAME,
            ARTIFACT_FILENAME,
            C_FILENAME,
            INPUT_FILENAME,
            RESULT_FILENAME,
            WORKER_FILENAME,
        )

        self.write_file(filename, source_code)
        self.write_file(INPUT_FILENAME, _json_dump({
            "source": source_code,
            "filename": filename,
            "repo": str(repo_root),
        }))
        self._excluded_files.update({
            ADAPTED_FILENAME,
            ARTIFACT_FILENAME,
            C_FILENAME,
            RESULT_FILENAME,
        })

        config = SandboxConfig(
            timeout_seconds=timeout,
            memory_limit_mb=1024,
            max_output_bytes=200_000,
            file_size_limit_bytes=64_000_000,
            cpu_limit_seconds=int(timeout) + 2,
        )
        args = _bwrap_python_root(find_bwrap(), self.workdir)
        idx = args.index("--tmpfs")
        args = args[:idx] + ["--ro-bind", str(repo_root), "/piton-repo"] + args[idx:]
        args += _limits_prefix(config)
        args += [
            "/usr/bin/python3", "-S", "-B", f"/piton-repo/autopiton/{WORKER_FILENAME}",
            "/piton-repo", "/work", f"/work/{INPUT_FILENAME}",
        ]
        old = self.config
        self.config = config
        try:
            return self._run(args, timeout=timeout)
        finally:
            self.config = old
    
    def _run(self, cmd: List[str], timeout: float) -> ExecutionResult:
        start = time.monotonic()
        result = ExecutionResult(exit_code=-1, duration_ms=0.0, backend=self.config.backend,
                                 workdir=str(self.workdir))
        out_sink = _Sink(self.config.max_output_bytes)
        err_sink = _Sink(self.config.max_output_bytes)
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.workdir),
                env=env,
                start_new_session=True,
            )
        except Exception as exc:
            result.duration_ms = (time.monotonic() - start) * 1000
            result.sandbox_error = f"{type(exc).__name__}: {exc}"
            result.exit_code = 127
            return result

        threads = [
            threading.Thread(target=_drain, args=(proc.stdout, out_sink), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, err_sink), daemon=True),
        ]
        for thread in threads:
            thread.start()

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
        finally:
            for thread in threads:
                thread.join(timeout=2.0)
            if proc.poll() is None:
                _kill_group(proc)

        rc = proc.returncode if proc.returncode is not None else -9
        result.duration_ms = (time.monotonic() - start) * 1000
        result.exit_code = rc
        result.timed_out = timed_out
        result.signal_number = -rc if rc < 0 else None
        result.crash = rc < 0
        result.stdout = out_sink.data.decode("utf-8", "replace")
        result.stderr = err_sink.data.decode("utf-8", "replace")
        result.output_truncated = out_sink.truncated or err_sink.truncated
        lowered = (result.stderr + result.stdout).lower()
        result.memory_exceeded = "memoryerror" in lowered or "out of memory" in lowered
        result.fs_created = self._collect_fs_changes()
        return result
    
    def _collect_fs_changes(self) -> List[Dict[str, object]]:
        """Hash every file the program left in the workdir (excluding its inputs)."""
        changes: List[Dict[str, object]] = []
        root = self.workdir
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if rel in self._excluded_files:
                continue
            try:
                st = path.stat()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            changes.append({"path": rel, "size": st.st_size, "sha256": digest})
        return changes


def run_in_sandbox(
    source_code: str,
    filename: str = "program.py",
    timeout: float = 10.0,
) -> ExecutionResult:
    """Run original Python source under isolated CPython."""
    config = SandboxConfig(timeout_seconds=timeout)
    with Sandbox(config) as sandbox:
        return sandbox.run_cpython(source_code, filename)
