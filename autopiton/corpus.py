"""
Corpus Manager Module for AutoPiton

Persists programs, structured comparison results, divergence signatures,
run metadata and replay history. Nothing here interprets PITON: it only
records what the oracle and the runner actually produced.

Layout (under the ``autopiton`` package directory):

    cases/index.json                    case index + signature index
    cases/<case-id>.json                metadata + full comparison result
    cases/<case-id>.py                  exact source (regression artifact)
    reports/runs/<run-id>/meta.json     run configuration + environment
    reports/runs/<run-id>/results.jsonl one JSON record per executed program
    reports/runs/<run-id>/summary.json  final counts and denominators
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime
from itertools import zip_longest
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .comparator import Classification, ComparisonResult

CORPUS_VERSION = 1

DIVERGENCE_CLASSES = {
    Classification.DIVERGENT.value,
    Classification.UNSUPPORTED.value,
    Classification.INVALID_ACCEPTED.value,
    Classification.CRASH.value,
    Classification.TIMEOUT.value,
    Classification.NONDETERMINISTIC.value,
}


def utc_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def is_divergence(classification: Classification) -> bool:
    return classification.value in DIVERGENCE_CLASSES


_BOOL_LITERAL = {"True", "False"}
_NONE_LITERAL = {"None"}
_INT_LITERAL = re.compile(r"[+-]?\d+")
_FLOAT_LITERAL = re.compile(
    r"[+-]?(?:(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+)"
)


def _value_kind(text: str) -> str:
    """Coarse kind of one printed line, never its literal value."""
    value = text.strip()
    if not value:
        return "empty"
    if value in _BOOL_LITERAL:
        return "bool"
    if value in _NONE_LITERAL:
        return "none"
    if _INT_LITERAL.fullmatch(value):
        return "int"
    if _FLOAT_LITERAL.fullmatch(value):
        return "float"
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return "str"
    return "other"


def _first_difference_shape(
    cpython_stdout: str, piton_stdout: str
) -> Tuple[str, str]:
    """Kinds of the first differing output line pair.

    Grouping output-only divergences by value kind (e.g. ``float->int``)
    separates distinct defect classes without hashing exact literals, which
    would fragment every program into its own signature.
    """
    def lines(text: str) -> List[str]:
        return text.replace("\r\n", "\n").replace("\r", "\n").splitlines()

    def kind(line: Optional[str]) -> str:
        return "absent" if line is None else _value_kind(line)

    for cpython_line, piton_line in zip_longest(
        lines(cpython_stdout), lines(piton_stdout)
    ):
        if cpython_line != piton_line:
            return kind(cpython_line), kind(piton_line)
    return "other", "other"


def divergence_signature(comparison: ComparisonResult) -> Optional[str]:
    """Stable signature of the *kind* of divergence, independent of source.

    Non-divergences return None and are deduplicated by source hash alone.
    The signature is deliberately coarse: classification plus the stage or
    exception that separates CPython from PITON, and for output-only
    divergences the value kinds of the first differing line pair. Exact
    outputs stay in the case result, not in the signature.
    """
    if comparison.classification.value not in DIVERGENCE_CLASSES:
        return None
    cpython = comparison.cpython
    piton = comparison.piton
    parts: List[str] = [comparison.classification.value]
    if cpython is not None:
        parts.append(f"cpython:{cpython.status}:{cpython.exception_type or '-'}")
    if piton is not None:
        if piton.executed:
            parts.append(f"piton:EXECUTE:{piton.exception_type or '-'}")
        else:
            stage = piton.failed_stage.value if piton.failed_stage else "-"
            parts.append(f"piton:{stage}:{piton.exception_type or '-'}")
    if comparison.classification == Classification.DIVERGENT:
        details = comparison.details or {}
        if "cpython_stdout" in details and "piton_stdout" in details:
            cpython_out = details.get("cpython_stdout") or ""
            piton_out = details.get("piton_stdout") or ""
            if cpython_out != piton_out:
                cpython_kind, piton_kind = _first_difference_shape(
                    cpython_out, piton_out
                )
                parts.append(f"stdout:{cpython_kind}->{piton_kind}")
            else:
                parts.append("stream")
        elif "cpython_exception" in details:
            parts.append("invalid-accepted")
        else:
            parts.append("failure")
    return "|".join(parts)


def git_provenance(repo_root: Optional[Path]) -> Dict[str, Any]:
    """Commit and dirty state of the PITON checkout under test.

    Only the repo's basename is stored in the run record; the absolute path
    would leak the operator's workstation layout into the public git history
    whenever a run's meta.json is committed.
    """
    info: Dict[str, Any] = {"repo": Path(repo_root).name if repo_root else None, "commit": None, "dirty": None}
    if repo_root is None or not Path(repo_root).is_dir():
        return info
    try:
        commit = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
        dirty = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=15,
        )
        if commit.returncode == 0:
            info["commit"] = commit.stdout.strip() or None
        if dirty.returncode == 0:
            lines = [line for line in dirty.stdout.splitlines() if line.strip()]
            info["dirty"] = bool(lines)
            info["dirty_paths"] = lines[:50]
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class RunWriter:
    """Append-only JSONL writer for one comparison run."""

    def __init__(self, path: Path, meta: Dict[str, Any]):
        self.path = path
        self.meta = meta
        self.results_path = path / "results.jsonl"
        self._started = time.monotonic()
        path.mkdir(parents=True, exist_ok=True)
        _write_json(path / "meta.json", meta)
        self.results_path.write_text("", encoding="utf-8")

    @property
    def run_id(self) -> str:
        return self.meta["run_id"]

    def append(self, record: Dict[str, Any]) -> None:
        with self.results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()

    def finalize(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        counts = summary.get("classification_counts", {})
        payload = {
            "run_id": self.run_id,
            "finished_at": utc_now(),
            "duration_ms": round((time.monotonic() - self._started) * 1000, 1),
            **summary,
            "classification_counts": counts,
            "programs_executed": sum(counts.values()),
        }
        _write_json(self.path / "summary.json", payload)
        return payload


class CorpusManager:
    """Disk-backed corpus of programs and comparison results."""

    def __init__(self, root: Optional[Path] = None, repo_root: Optional[Path] = None):
        self.root = Path(root) if root else Path(__file__).resolve().parent
        self.repo_root = Path(repo_root) if repo_root else self.root.parent
        self.cases_dir = self.root / "cases"
        self.runs_dir = self.root / "reports" / "runs"
        self.index_path = self.cases_dir / "index.json"

    # ------------------------------------------------------------------ index

    def load_index(self) -> Dict[str, Any]:
        if not self.index_path.is_file():
            return {"version": CORPUS_VERSION, "cases": {}, "signatures": {}, "updated_at": utc_now()}
        try:
            index = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": CORPUS_VERSION, "cases": {}, "signatures": {}, "updated_at": utc_now()}
        index.setdefault("version", CORPUS_VERSION)
        index.setdefault("cases", {})
        index.setdefault("signatures", {})
        return index

    def save_index(self, index: Dict[str, Any]) -> None:
        index["updated_at"] = utc_now()
        _write_json(self.index_path, index)

    # ----------------------------------------------------------- environment

    def environment(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        from .sandbox import find_bwrap, sandbox_available

        info: Dict[str, Any] = {
            "cpython_version": sys.version.split()[0],
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "sandbox_backend": "bwrap" if sandbox_available() else "unavailable",
            "bwrap": find_bwrap(),
            "piton": git_provenance(self.repo_root),
        }
        if extra:
            info.update(extra)
        return info

    # -------------------------------------------------------------- run dirs

    def _new_run_id(self, mode: str, seed: Optional[int]) -> str:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        seed_part = f"_seed{seed}" if seed is not None else ""
        base = f"run_{stamp}_{mode}{seed_part}"
        run_id, counter = base, 1
        while (self.runs_dir / run_id).exists():
            counter += 1
            run_id = f"{base}_{counter:02d}"
        return run_id

    def create_run(
        self,
        mode: str,
        seed: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
        notes: str = "",
    ) -> RunWriter:
        run_id = self._new_run_id(mode, seed)
        meta = {
            "run_id": run_id,
            "mode": mode,
            "seed": seed,
            "started_at": utc_now(),
            "config": config or {},
            "environment": self.environment(),
            "notes": notes,
            "corpus_version": CORPUS_VERSION,
        }
        return RunWriter(self.runs_dir / run_id, meta)

    # ------------------------------------------------------------------ cases

    def case_id_for(self, source: str) -> str:
        return f"case-{source_hash(source)[:16]}"

    def case_path(self, case_id: str) -> Path:
        return self.cases_dir / f"{case_id}.json"

    def register(
        self,
        comparison: ComparisonResult,
        *,
        family: str,
        seed: int,
        description: str,
        run_id: str,
        is_valid: bool = True,
        expected_exception: Optional[str] = None,
        minimized_from: Optional[str] = None,
        generator: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Persist one comparison. Deduplicates by source hash first."""
        index = self.load_index()
        case_id = self.case_id_for(comparison.source_code)
        signature = divergence_signature(comparison)
        now = utc_now()
        entry = index["cases"].get(case_id)

        if entry is None:
            entry = {
                "case_id": case_id,
                "source_hash": comparison.source_hash,
                "classification": comparison.classification.value,
                "classifications": [comparison.classification.value],
                "signature": signature,
                "family": family,
                "seed": seed,
                "description": description,
                "is_valid": is_valid,
                "expected_exception": expected_exception,
                "minimized_from": minimized_from,
                "run_ids": [run_id],
                "replay_history": [],
                "created_at": now,
                "last_seen_at": now,
                "nondeterministic": False,
            }
            index["cases"][case_id] = entry
            _write_json(self.case_path(case_id), {
                "case_id": case_id,
                "source": comparison.source_code,
                "source_hash": comparison.source_hash,
                "family": family,
                "seed": seed,
                "description": description,
                "is_valid": is_valid,
                "expected_exception": expected_exception,
                "minimized_from": minimized_from,
                "generator": generator,
                "run_id": run_id,
                "created_at": now,
                "result": comparison.to_dict(),
            })
            (self.cases_dir / f"{case_id}.py").write_text(
                comparison.source_code + "\n", encoding="utf-8"
            )
        else:
            entry["last_seen_at"] = now
            if run_id not in entry["run_ids"]:
                entry["run_ids"].append(run_id)
            if comparison.classification.value not in entry["classifications"]:
                entry["classifications"].append(comparison.classification.value)
                if len(entry["classifications"]) > 1:
                    entry["nondeterministic"] = True
            previous = entry.get("signature")
            if signature and previous != signature:
                if previous:
                    old_bucket = index["signatures"].get(previous)
                    if old_bucket and case_id in old_bucket:
                        old_bucket.remove(case_id)
                        if not old_bucket:
                            del index["signatures"][previous]
                entry["signature"] = signature

        if signature:
            bucket = index["signatures"].setdefault(signature, [])
            if case_id not in bucket:
                bucket.append(case_id)

        self.save_index(index)
        return case_id

    def load_case(self, case_id_or_path: str) -> Dict[str, Any]:
        path = Path(case_id_or_path)
        if not path.is_file():
            path = self.case_path(case_id_or_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if "source" not in data:
            raise ValueError(f"case file has no source: {path}")
        return data

    def record_replay(
        self,
        case_id: str,
        run_id: str,
        comparison: ComparisonResult,
        command: str,
    ) -> None:
        index = self.load_index()
        entry = index["cases"].get(case_id)
        if entry is None:
            raise KeyError(f"unknown case: {case_id}")
        entry.setdefault("replay_history", []).append({
            "run_id": run_id,
            "timestamp": utc_now(),
            "classification": comparison.classification.value,
            "fingerprint": comparison.observable_fingerprint(),
            "command": command,
        })
        entry["last_seen_at"] = utc_now()
        self.save_index(index)

    def record_minimization(self, case_id: str, payload: Dict[str, Any]) -> Path:
        path = self.cases_dir / f"{case_id}.minimization.json"
        _write_json(path, payload)
        index = self.load_index()
        entry = index["cases"].get(case_id)
        if entry is not None:
            entry["minimization"] = {
                "minimized_case_id": payload.get("minimized_case_id"),
                "attempts": payload.get("attempts"),
                "accepted": payload.get("accepted"),
                "stopped_reason": payload.get("stopped_reason"),
                "recorded_at": utc_now(),
            }
            self.save_index(index)
        return path

    # ---------------------------------------------------------------- reports

    def unique_divergences(self) -> Dict[str, List[str]]:
        return self.load_index().get("signatures", {})

    def case_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for entry in self.load_index().get("cases", {}).values():
            key = entry.get("classification", "UNKNOWN")
            counts[key] = counts.get(key, 0) + 1
        return counts

    def latest_run(self) -> Optional[Path]:
        if not self.runs_dir.is_dir():
            return None
        runs = sorted(
            (path for path in self.runs_dir.iterdir() if (path / "meta.json").is_file()),
            key=lambda path: path.name,
        )
        return runs[-1] if runs else None

    def load_run(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        path = self.runs_dir / run_id if run_id else self.latest_run()
        if path is None or not path.is_dir():
            raise FileNotFoundError(f"run not found: {run_id or '(latest)'}")
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        records = []
        results = path / "results.jsonl"
        if results.is_file():
            for line in results.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(json.loads(line))
        summary_path = path / "summary.json"
        summary = None
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return {"path": str(path), "meta": meta, "records": records, "summary": summary}
