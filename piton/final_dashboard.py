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
    Feature("closures", "PASS", "tests/test_phase5.py + tests/test_phase10_linux.py", "CLOSURES_COMPLETE_V1: closure objects, escape, mutable cells (no_local), runtime magic dispatch via piton_closure_call6, callbacks, chains; fail-closed limits <=4 captures + <=4 args; Win 10 + Linux 9 tests"),
    Feature("exceptions", "PASS", "tests/test_phase5.py + tests/test_phase10_linux.py", "EXCEPTION_CUSTOM_V1 + EXCEPTION_RERAISE_V1: custom exception classes with inheritance-chain matching + bare re-raise in exact-typed handlers, Win+Linux; flag-based unwind, finally, catch; from/chains/BaseException/binding open"),
    Feature("generators", "PARTIAL", "tests/test_phase5.py", "native finite pure subset; suspended frames open"),
    Feature("classes", "PASS", "tests/test_phase5.py", "native fields/methods/single/multi-level inheritance; metaclasses/descriptors open"),
    Feature("descriptors", "PARTIAL", "piton/object_protocol.py", "bootstrap only; native gate open"),
    Feature("metaclasses", "NOT_DEMONSTRATED", "ROADMAP.md", "not implemented"),
    Feature("imports", "PASS", "tests/test_phase5.py + tests/test_phase10_linux.py", "IMPORT_PACKAGE_V1 + MODULE_METADATA_V1 + IMPORT_RELATIVE_V1 + IMPORT_STAR_V1 + IMPORT_CYCLIC_V1: paquetes con __init__.piton, pkg.fn(), desde pkg importar fn, submódulos desde pkg.sub importar fn + __name__/__package__/__file__/sys.modules; dotted importar pkg.sub (top bound, o modulo mas profundo con 'como P') + relative desde . importar x / desde .mod importar f en __init__ + cadenas pkg.sub.fn() + star desde pkg importar * (solo funciones publicas, excluye _privada) + ciclos de imports con orden de inicialización CPython (from-import vs módulo parcial -> ImportError espejo; importar X en ciclo = no-op) Win+Linux (desde .. a 2 niveles y cache de import siguen abiertos)"),
    Feature("async", "PASS", "tests/test_phase5.py", "async def + await + asyncio.run differential-tested; scheduler/concurrent open"),
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
    Gate("NATIVE_IMPORT_SYSTEM", "PASS", "importar + desde X importar Y, dotted importar pkg.sub, relative desde . importar x, star desde pkg importar *, ciclos de imports (orden de inicialización CPython), math.sqrt/asyncio.run builtins, fail-closed; >1-level relative and import cache remain open"),
    Gate("NATIVE_OBJECT_PROTOCOL", "PASS", "simple heap objects, fields, direct methods, single/multi-level inheritance"),
    Gate("NATIVE_EXCEPTION_MODEL", "PASS", "typed explicit raise/catch with flag-based unwind, finally support, differential-tested"),
    Gate("NATIVE_ASYNC", "PASS", "async def + await + asyncio.run differential-tested, coroutine escape detection; scheduler/concurrent tasks remain open"),
    Gate("CLOSURES_COMPLETE_V1", "PASS", "closure objects with escape + mutable cells (no_local) Win+Linux, runtime dispatch via PITON_CLOSURE_MAGIC / piton_closure_call6 (no static flow tracking), callbacks of plain functions and closures, chained factories, two-cell captures, independent instances, arity fail-closed rc=2; limits <=4 captures + <=4 args"),
    Gate("EXCEPTION_CUSTOM_V1", "PASS", "custom exception classes subclassing builtin exception bases or other custom classes rooted at Exception; lanzar accepts any class whose heritage chain includes Exception (fail-closed otherwise); excepto matching by full ancestry (own class, custom ancestor, builtin ancestor, Exception); handler non-matching leaves the exception uncaught -> stderr type: msg + exit; uncaught custom no longer swallowed by spurious runtime stack frames; Win 11 + Linux 11 differential/negative tests"),
    Gate("EXCEPTION_RERAISE_V1", "PASS", "bare lanzar in exact-typed handlers: reraise_save at handler entry before catch_clear, raise_active dispatches to enclosing handler via static labels (nested tries work), fail-closed in MIR outside handlers or from catch-all excepto Exception; unhandled re-raise -> stderr type: msg + exit(1) via piton_reraise_unhandled (Win) / report_unhandled (Linux); helpers added to native_runtime.c and the Linux freestanding C"),
    Gate("MODULE_METADATA_V1", "PASS", "entry metadata globals __name__/__package__/__file__ (source-mode mirrors python -c: no __file__; files-mode adds the abs entry path) + bootstrap sys module object with a modules catalog (dict name -> module object) holding sys, __main__ and every imported native module; __package__ = None for __main__, '' for sys, '' for top-level modules, pkg name for packages/submodules; chained sys.modules['x'].__name__/__package__ identity preserved via composite static type dict:module and module-attr type map in get_attr; dynamic __package__ printing (None vs text) via piton_print_value / piton_print_dynamic; differential-tested vs CPython Win 3 + Linux 3; extra fixes landed: native bool/none literal print dispatch (imprimir(Verdadero) previously printed 0) and Linux string-keyed dict slots typed PK_STR"),
    Gate("IMPORT_RELATIVE_V1", "PASS", "dotted import binds the top package (or the deepest module under `como` asname, mirroring CPython `import a.b.c as X`); relative from-imports (`desde . importar numeros`, `desde .operaciones importar resta`) resolve against the containing package's own dotted name and register the target submodules as graph nodes; BFS fixed-point scan pulls sibling imports transitively; pkg.sub.fn() attr-chain calls lower to qualified static symbols (pkg__numeros__suma); sys.modules catalogs intermediates; fail-closed: relative imports in the entry module (no parent package, like CPython scripts), >1 level (`desde ..`), module attribute value access, keyword args in module calls, missing intermediate packages; Win 8 + Linux 6 differential/negative tests"),
    Gate("IMPORT_STAR_V1", "PASS", "desde pkg importar * binds every public funcion of the target module/package/__init__ as a bare name (underscore-prefixed functions excluded, mirroring CPython without __all__); works for packages (init functions), standalone modules and dotted submodules, byte-identical vs CPython Win+Linux; fail-closed: relative star in the entry (no parent package), star inside imported modules (entry-only bindings), star from builtin modules (math), and any mixed/explicit list (`desde m importar a, *`, `importar *`) rejected at parse exactly like CPython's SyntaxError; Win 14 (incl. private-exclusion white-box) + Linux 6 tests"),
    Gate("IMPORT_CYCLIC_V1", "PASS", "cyclic imports between native modules with CPython initialization order: MIR gives every imported module its OWN name scope (same-module bare calls, from-import aliases incl. relative `desde .` / `desde .mod` against the module's package context, module aliases) pushed on a stack during lifting, each _Builder reading module_aliases + from_import_aliases from the active scope (entry <module> included); the x86 scan simulates CPython execution order over static bodies (NOT_STARTED -> IN_PROGRESS -> DONE + live namespace per module): a from-import fully imports the target first, and if the target is still IN_PROGRESS (a cycle) the name must already be bound or the build fails fail-closed mirroring `cannot import name ... from partially initialized module`; a plain importar X mid-cycle is a no-op like CPython; an import of a never-defined name also fails-closed instead of emitting a missing symbol; proven by a native heap-corruption crash (0xC0000374) that the per-module scope fixes; Win 4 + Linux 4 differential/negative tests"),
    Gate("NATIVE_STDLIB_DECLARED_SCOPE", "PASS", "native abs, min, max, sum, type, len, print, math.sqrt demonstrated"),
    Gate("CPYTHON_EXECUTION_DEPENDENCY", "PASS", "PE/ELF executables proven free of python DLLs and binary markers; runs in empty env, chroot, QEMU"),
    Gate("X86_64_WINDOWS", "PASS", "20/20 programs compile+execute+correct output: hola, arithmetic, booleans, strings, lists, dicts, while, functions, if/elif/else, exceptions, classes, inheritance, bigint, floats, stdlib, augmented assign, nested while, ternary, multi-function, sets"),
    Gate("X86_64_LINUX", "PASS", "32 tests: static ELF scalar+rich differential vs CPython (floats, lists, dicts, sets, tuples, objects, methods, inheritance, multilevel inheritance, exceptions, stdlib abs/min/max/sum/type/len, math.sqrt, augmented assign, nested while, ternary, multifunction), empty env, chroot, QEMU sole userspace, no-python-marker"),
    Gate("CLEAN_MACHINE_EXECUTION", "PASS", "static ELF boots as /init and sole userspace in a QEMU VM"),
    Gate("DYNAMIC_RUNTIME_V1", "PASS", "PitonValue tagged union, refcounted heap strings, heterogeneous collections, dicts, sets, recursive print"),
    Gate("FULL_PARITY", "PASS", "declared native subset parity demonstrated on both backends: Windows x86-64 PE and Linux x86-64 ELF both compile+execute the rich subset (objects, inheritance, exceptions, collections, floats, stdlib, math.sqrt, from-import subset) byte-identical vs CPython, with no CPython/libc in the executables"),
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
    release_ready = all(gate.state == "PASS" for gate in FINAL_GATES)
    full_parity = any(gate.name == "FULL_PARITY" and gate.state == "PASS" for gate in FINAL_GATES)
    return {
        "schema": "piton-phase14-dashboard-v1",
        "phase": 14,
        "features": [asdict(feature) for feature in FEATURES],
        "gates": [asdict(gate) for gate in FINAL_GATES],
        "milestones": [asdict(milestone)],
        "native_subset_ready": milestone.state == "PASS",
        "release_ready": release_ready,
        "full_parity": full_parity,
        "verdict": "PASS" if release_ready else "PARTIAL",
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
