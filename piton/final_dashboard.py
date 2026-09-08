"""Dashboard reproducible de cobertura de la Fase 14.

Los estados son declarativos y conservadores: una capacidad no se eleva a
PASS solo porque exista código bootstrap o un backend que emita instrucciones.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from typing import Any


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
    Feature("expressions", "PARTIAL", "tests/test_phase5.py", "native subset excludes floats and dynamic objects"),
    Feature("statements", "PARTIAL", "tests/test_phase5.py", "native subset covers branch and while"),
    Feature("functions", "PARTIAL", "tests/test_phase5.py", "native subset covers simple calls only"),
    Feature("closures", "PARTIAL", "tests/test_phase5.py", "native immutable scalar subset; mutable cells and escape open"),
    Feature("exceptions", "PARTIAL", "tests/test_phase5.py", "native typed catch subset; unwind/finally open"),
    Feature("generators", "PARTIAL", "tests/test_phase5.py", "native finite pure subset; suspended frames open"),
    Feature("classes", "PARTIAL", "tests/test_phase5.py", "native fields/methods subset; inheritance open"),
    Feature("descriptors", "PARTIAL", "piton/object_protocol.py", "bootstrap only; native gate open"),
    Feature("metaclasses", "NOT_DEMONSTRATED", "ROADMAP.md", "not implemented"),
    Feature("imports", "PARTIAL", "tests/test_phase5.py", "native sibling modules; packages and cycles open"),
    Feature("async", "PARTIAL", "tests/test_phase5.py", "native non-suspending subset; scheduler open"),
    Feature("stdlib", "PARTIAL", "tests/test_phase5.py", "native math.sqrt plus bootstrap modules"),
    Feature("dynamic_code", "PARTIAL", "piton/runtime.py", "explicit bootstrap APIs; native execution absent"),
    Feature("introspection", "PARTIAL", "piton/runtime.py", "source contract only"),
    Feature("multiprocessing", "NOT_DEMONSTRATED", "ROADMAP.md", "Windows spawn not demonstrated"),
    Feature("ffi", "PARTIAL", "piton/stdlib_runtime.py", "explicit ctypes surface; native independence open"),
)


FINAL_GATES = (
    Gate("GRAMMAR_PARITY", "PASS", "frontend/evidence corpus passes"),
    Gate("SEMANTIC_DIFFERENTIAL_CORPUS", "PARTIAL", "native subset corpus passes; broad native corpus remains open"),
    Gate("NATIVE_RUNTIME", "PARTIAL", "linked C11 collection runtime passes a native differential subset"),
    Gate("NATIVE_IMPORT_SYSTEM", "PARTIAL", "sibling .piton function modules link into one PE"),
    Gate("NATIVE_OBJECT_PROTOCOL", "PARTIAL", "simple heap objects, fields and direct methods pass natively"),
    Gate("NATIVE_EXCEPTION_MODEL", "PARTIAL", "typed explicit raise/catch subset passes; unwind remains incomplete"),
    Gate("NATIVE_ASYNC", "PARTIAL", "non-suspending async run/await subset passes natively"),
    Gate("NATIVE_STDLIB_DECLARED_SCOPE", "PARTIAL", "native math.sqrt is demonstrated; broader stdlib remains open"),
    Gate("CPYTHON_EXECUTION_DEPENDENCY", "PARTIAL", "bootstrap implementation intentionally uses CPython"),
    Gate("X86_64_WINDOWS", "PARTIAL", "code emission exists; clean execution is not demonstrated"),
    Gate("X86_64_LINUX", "PARTIAL", "freestanding static ELF scalar subset runs on x86-64 Linux"),
    Gate("CLEAN_MACHINE_EXECUTION", "PASS", "static ELF boots as /init and sole userspace in a QEMU VM"),
)


def build_dashboard() -> dict[str, Any]:
    return {
        "schema": "piton-phase14-dashboard-v1",
        "phase": 14,
        "features": [asdict(feature) for feature in FEATURES],
        "gates": [asdict(gate) for gate in FINAL_GATES],
        "release_ready": all(gate.state == "PASS" for gate in FINAL_GATES),
        "full_parity": False,
        "verdict": "PARTIAL",
    }


def render_markdown(dashboard: dict[str, Any]) -> str:
    lines = ["# Pitón Fase 14 Dashboard", "", f"- Release x86 parity: **{'PASS' if dashboard['release_ready'] else 'NOT READY'}**", "", "## Features", "", "| Feature | Estado | Evidencia | Hueco |", "|---|---|---|---|"]
    for feature in dashboard["features"]:
        lines.append(f"| {feature['name']} | {feature['state']} | `{feature['evidence']}` | {feature['gap']} |")
    lines.extend(["", "## Gates", "", "| Gate | Estado | Razón |", "|---|---|---|"])
    for gate in dashboard["gates"]:
        lines.append(f"| {gate['name']} | {gate['state']} | {gate['reason']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the Pitón Phase 14 dashboard")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    dashboard = build_dashboard()
    if args.format == "markdown":
        print(render_markdown(dashboard), end="")
    else:
        print(json.dumps(dashboard, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if dashboard["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
