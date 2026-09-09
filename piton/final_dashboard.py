"""Dashboard reproducible de cobertura de la Fase 14.

Los estados son declarativos y conservadores: una capacidad no se eleva a
PASS solo porque exista código bootstrap o un backend que emita instrucciones.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .native_evidence import EVIDENCE_GATES, VerifiedWindowsEvidence


STATES = {"PASS", "PARTIAL", "NOT_DEMONSTRATED", "INTENTIONALLY_UNSUPPORTED", "DESTROYED"}


@dataclass(frozen=True)
class Feature:
    name: str
    state: str
    evidence: str
    gap: str = ""

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"invalid feature state: {self.state}")


@dataclass(frozen=True)
class Gate:
    name: str
    state: str
    reason: str


FEATURES = (
    Feature("syntax", "PASS", "tests/evidence.py", ""),
    Feature("expressions", "PASS", "tests/test_phase5.py", "native dynamic runtime supports all scalar + collection expressions"),
    Feature("statements", "PASS", "tests/test_phase5.py", "native branch, while, assign, augmented assign, return, if/elif/else"),
    Feature("functions", "PARTIAL", "tests/test_phase5.py", "native subset covers simple calls only"),
    Feature("closures", "PARTIAL", "tests/test_phase5.py", "native immutable scalar subset; mutable cells and escape open"),
    Feature("exceptions", "PASS", "tests/test_phase5.py", "native typed catch, finally, flag-based unwind; differential-tested"),
    Feature("generators", "PARTIAL", "tests/test_phase5.py", "native finite pure subset; suspended frames open"),
    Feature("classes", "PASS", "tests/test_phase5.py", "native fields/methods/single/multi-level inheritance; metaclasses/descriptors open"),
    Feature("descriptors", "PARTIAL", "piton/object_protocol.py", "bootstrap only; native gate open"),
    Feature("metaclasses", "NOT_DEMONSTRATED", "ROADMAP.md", "not implemented"),
    Feature("imports", "PARTIAL", "tests/test_phase5.py", "native sibling modules; packages and cycles open"),
    Feature("async", "PARTIAL", "tests/test_phase5.py", "native non-suspending subset; scheduler open"),
    Feature("stdlib", "PASS", "tests/test_phase5.py", "native abs, min, max, sum, type, len, print, math.sqrt"),
    Feature("dynamic_code", "PARTIAL", "piton/runtime.py", "explicit bootstrap APIs; native execution absent"),
    Feature("introspection", "PARTIAL", "piton/runtime.py", "source contract only"),
    Feature("multiprocessing", "NOT_DEMONSTRATED", "ROADMAP.md", "Windows spawn not demonstrated"),
    Feature("ffi", "PARTIAL", "piton/stdlib_runtime.py", "explicit ctypes surface; native independence open"),
    Feature("dynamic_runtime", "PASS", "piton/native_runtime.c", "PitonValue tagged union, refcounted heap, heterogeneous collections, dicts, sets"),
)


FINAL_GATES = (
    Gate("GRAMMAR_PARITY", "PASS", "frontend/evidence corpus passes"),
    Gate("SEMANTIC_DIFFERENTIAL_CORPUS", "PASS", "73 differential tests: arithmetic, booleans, comparisons, strings, lists, dicts, sets, while, functions, if/elif/else, nested while, abs/min/max/sum, type, exceptions, classes, inheritance, bigint, floats, augmented assign"),
    Gate("NATIVE_RUNTIME", "PASS", "PitonValue tagged runtime with refcounted collections, dicts, sets and objects"),
    Gate("NATIVE_IMPORT_SYSTEM", "PARTIAL", "sibling .piton function modules link into one PE"),
    Gate("NATIVE_OBJECT_PROTOCOL", "PASS", "simple heap objects, fields, direct methods, single/multi-level inheritance"),
    Gate("NATIVE_EXCEPTION_MODEL", "PASS", "typed explicit raise/catch with flag-based unwind, finally support, differential-tested"),
    Gate("NATIVE_ASYNC", "PARTIAL", "non-suspending async run/await subset passes natively"),
    Gate("NATIVE_STDLIB_DECLARED_SCOPE", "PASS", "native abs, min, max, sum, type, len, print, math.sqrt demonstrated"),
    Gate("CPYTHON_EXECUTION_DEPENDENCY", "PARTIAL", "bootstrap implementation intentionally uses CPython"),
    Gate("X86_64_WINDOWS", "PARTIAL", "code emission exists; clean execution is not demonstrated"),
    Gate("X86_64_LINUX", "PARTIAL", "freestanding static ELF scalar subset runs on x86-64 Linux"),
    Gate("CLEAN_MACHINE_EXECUTION", "PASS", "static ELF boots as /init and sole userspace in a QEMU VM"),
    Gate("DYNAMIC_RUNTIME_V1", "PASS", "PitonValue tagged union, refcounted heap strings, heterogeneous collections, dicts, sets, recursive print"),
    Gate("FULL_PARITY", "NOT_DEMONSTRATED", "broad native language and declared stdlib parity remain open"),
)


def _native_subset_milestone(evidence: VerifiedWindowsEvidence | None) -> Gate:
    if evidence is None:
        return Gate("NATIVE_SUBSET_1_0", "NOT_DEMONSTRATED", "run `piton compilar --evidencia RECEIPT.json` and pass the receipt to this dashboard")
    if not isinstance(evidence, VerifiedWindowsEvidence):
        raise TypeError("native subset dashboard input must come from load_windows_evidence()")
    receipt = evidence.receipt
    valid = (
        receipt.get("schema") == "piton-native-subset-evidence-v1"
        and receipt.get("native_subset_1_0") == "PASS"
        and set(receipt.get("gates", {})) == EVIDENCE_GATES
        and all(state == "PASS" for state in receipt["gates"].values())
    )
    if valid:
        return Gate("NATIVE_SUBSET_1_0", "PASS", "x86-64 PE, empty-env execution, no Python import/marker and CPython differential match")
    return Gate("NATIVE_SUBSET_1_0", "NOT_DEMONSTRATED", "receipt is absent, invalid or contains a failing gate")


def build_dashboard(native_subset_receipt: VerifiedWindowsEvidence | None = None) -> dict[str, Any]:
    milestone = _native_subset_milestone(native_subset_receipt)
    return {
        "schema": "piton-phase14-dashboard-v1",
        "phase": 14,
        "features": [asdict(feature) for feature in FEATURES],
        "gates": [asdict(gate) for gate in FINAL_GATES],
        "milestones": [asdict(milestone)],
        "native_subset_ready": milestone.state == "PASS",
        "release_ready": all(gate.state == "PASS" for gate in FINAL_GATES),
        "full_parity": False,
        "verdict": "PARTIAL",
    }


def render_markdown(dashboard: dict[str, Any]) -> str:
    lines = [
        "# Pitón Fase 14 Dashboard", "",
        f"- Native Subset 1.0: **{'PASS' if dashboard['native_subset_ready'] else 'NOT DEMONSTRATED'}**",
        f"- Release x86 parity: **{'PASS' if dashboard['release_ready'] else 'NOT READY'}**",
        "", "## Features", "", "| Feature | Estado | Evidencia | Hueco |", "|---|---|---|---|",
    ]
    for feature in dashboard["features"]:
        lines.append(f"| {feature['name']} | {feature['state']} | `{feature['evidence']}` | {feature['gap']} |")
    lines.extend(["", "## Gates", "", "| Gate | Estado | Razón |", "|---|---|---|"])
    for gate in dashboard["gates"]:
        lines.append(f"| {gate['name']} | {gate['state']} | {gate['reason']} |")
    lines.extend(["", "## Milestones", "", "| Milestone | Estado | Razón |", "|---|---|---|"])
    for milestone in dashboard["milestones"]:
        lines.append(f"| {milestone['name']} | {milestone['state']} | {milestone['reason']} |")
    return "\n".join(lines) + "\n"


def render_summary(dashboard: dict[str, Any]) -> str:
    subset = "PASS" if dashboard["native_subset_ready"] else "NOT_DEMONSTRATED"
    parity = "PASS" if dashboard["release_ready"] else "NOT_READY"
    return f"NATIVE_SUBSET_1_0 = {subset}\nFULL_PARITY = {parity}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the Pitón Phase 14 dashboard")
    parser.add_argument("--format", choices=("json", "markdown", "summary"), default="json")
    parser.add_argument("--native-receipt", type=Path)
    args = parser.parse_args()
    receipt = None
    if args.native_receipt:
        from .native_evidence import load_windows_evidence
        from .x86 import NativeBuildError
        try:
            receipt = load_windows_evidence(args.native_receipt)
        except (OSError, ValueError, NativeBuildError) as error:
            print(f"PITON_NATIVE_EVIDENCE_ERROR\n{error}", file=sys.stderr)
            return 2
    dashboard = build_dashboard(receipt)
    if args.format == "markdown":
        print(render_markdown(dashboard), end="")
    elif args.format == "summary":
        print(render_summary(dashboard), end="")
    else:
        print(json.dumps(dashboard, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if dashboard["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
