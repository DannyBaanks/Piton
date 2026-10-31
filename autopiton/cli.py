"""
AutoPiton CLI - Command-line interface for the differential fuzzer.

Commands: doctor, generate, compare, minimize, replay, report.
Every execution happens inside the fail-closed bubblewrap sandbox.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .generator import GeneratedProgram, ProgramGenerator, ProgramFamily
from .cpython_oracle import CPythonOracle
from .piton_runner import PitonRunner
from .comparator import Classification, ComparisonResult, DifferentialComparator
from .sandbox import find_bwrap, sandbox_available
from .corpus import (
    CorpusManager,
    divergence_signature,
    utc_now,
)
from .minimizer import FailureMinimizer


# Global instances
_generator: Optional[ProgramGenerator] = None
_oracle: Optional[CPythonOracle] = None
_runner: Optional[PitonRunner] = None
_comparator: Optional[DifferentialComparator] = None
_corpus: Optional[CorpusManager] = None


def get_generator() -> ProgramGenerator:
    global _generator
    if _generator is None:
        _generator = ProgramGenerator()
    return _generator


def get_oracle() -> CPythonOracle:
    global _oracle
    if _oracle is None:
        _oracle = CPythonOracle()
    return _oracle


def get_runner() -> PitonRunner:
    global _runner
    if _runner is None:
        _runner = PitonRunner()
    return _runner


def get_comparator() -> DifferentialComparator:
    global _comparator
    if _comparator is None:
        _comparator = DifferentialComparator()
    return _comparator


def get_corpus() -> CorpusManager:
    global _corpus
    if _corpus is None:
        _corpus = CorpusManager(repo_root=get_runner().repo_root)
    return _corpus


def _require_sandbox() -> None:
    if not sandbox_available():
        print(
            "ERROR: bubblewrap is unavailable; AutoPiton refuses to execute "
            "programs without isolation.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _execute_pair(source: str, filename: str = "program.py") -> ComparisonResult:
    cpython_result = get_oracle().execute(source, filename)
    piton_result = get_runner().execute(source, filename)
    return get_comparator().compare(source, cpython_result, piton_result)


def _load_programs(path: Path) -> List[GeneratedProgram]:
    programs: List[GeneratedProgram] = []
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("input JSON must be a list of program objects")
        for item in data:
            try:
                family = ProgramFamily(item.get("family", ProgramFamily.ARITHMETIC.value))
            except ValueError:
                family = ProgramFamily.ARITHMETIC
            programs.append(GeneratedProgram(
                code=item["code"],
                family=family,
                seed=int(item.get("seed", 0)),
                complexity=int(item.get("complexity", 1)),
                description=item.get("description", ""),
                is_valid=bool(item.get("is_valid", True)),
                expected_exception=item.get("expected_exception"),
            ))
    else:
        for file in sorted(path.glob("*.py")):
            programs.append(GeneratedProgram(
                code=file.read_text(encoding="utf-8"),
                family=ProgramFamily.ARITHMETIC,
                seed=0,
                complexity=1,
                description=file.name,
            ))
    return programs


def _print_classification_counts(counts: Dict[str, int], total: int) -> None:
    for classification in Classification:
        count = counts.get(classification.value, 0)
        if not count:
            continue
        share = f" ({count / total * 100:.1f}% of executed)" if total else ""
        print(f"  {classification.value:<20} {count:>4}{share}")



def doctor_command(args):
    """Check AutoPiton environment and fail closed if isolation is missing."""
    corpus = get_corpus()
    print(f"AutoPiton {__version__} doctor")
    print("=" * 60)

    sandbox_ok = sandbox_available()
    print(f"sandbox      : {'bwrap available' if sandbox_ok else 'UNAVAILABLE (execution refused)'}")
    print(f"bwrap        : {find_bwrap()}")
    oracle = get_oracle()
    print(f"CPython      : {oracle.get_version()} on {oracle.get_platform()}")

    env = corpus.environment()
    piton = env["piton"]
    print(f"PITON repo   : {piton['repo']}")
    print(f"PITON commit : {piton['commit']} (dirty={piton['dirty']})")
    worker = corpus.repo_root / "autopiton" / "piton_worker.py"
    print(f"worker       : {worker} exists={worker.is_file()}")

    sample = get_generator().generate(count=5)
    invalid = sum(1 for program in sample if not program.is_valid)
    print(f"generator    : 5 samples, invalid={invalid}")

    print(f"cases dir    : {corpus.cases_dir}")
    print(f"runs dir     : {corpus.runs_dir}")
    counts = corpus.case_counts()
    if counts:
        print("corpus       : " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    else:
        print("corpus       : empty")
    latest = corpus.latest_run()
    print(f"latest run   : {latest.name if latest else '-'}")

    if not sandbox_ok:
        print("\nFAIL: sandbox unavailable; compare/minimize/replay are disabled.")
        sys.exit(2)
    print("\nAll checks passed.")


def generate_command(args):
    """Generate deterministic test programs."""
    generator = get_generator()
    if args.seed is not None:
        generator.seed(args.seed)

    if args.family:
        families = [ProgramFamily(name) for name in args.family]
        programs: List[GeneratedProgram] = []
        per_family = max(1, args.count // len(families))
        for family in families:
            programs.extend(generator.generate(family, per_family))
        programs = programs[: args.count]
    else:
        programs = generator.generate(count=args.count)

    families_used = sorted({program.family.value for program in programs})
    invalid = sum(1 for program in programs if not program.is_valid)
    print(
        f"Generated {len(programs)} programs "
        f"(seed={args.seed}, families={','.join(families_used)}, invalid={invalid})"
    )

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps([program.to_dict() for program in programs], indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {output_path}")
    elif not args.quiet:
        for index, program in enumerate(programs, start=1):
            print(f"\n--- {index}/{len(programs)} [{program.family.value}] seed={program.seed} ---")
            print(program.code)

    if args.save:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        batch_dir = get_corpus().cases_dir / "generated" / f"{stamp}_seed{args.seed}"
        batch_dir.mkdir(parents=True, exist_ok=False)
        manifest = {
            "created_at": utc_now(),
            "seed": args.seed,
            "count": len(programs),
            "environment": get_corpus().environment(),
            "programs": [program.to_dict() for program in programs],
        }
        (batch_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        for index, program in enumerate(programs):
            filename = f"{index:04d}_{program.family.value}_{program.seed}.py"
            (batch_dir / filename).write_text(program.code + "\n", encoding="utf-8")
        print(f"Saved {len(programs)} programs to {batch_dir}")


def compare_command(args):
    """Compare CPython and PITON on canaries, generated programs, or an input set."""
    _require_sandbox()
    generator = get_generator()

    if args.canary:
        mode = "canary"
        seed = None
        programs = generator.generate_canary_cases()
    elif getattr(args, "systematic", False):
        mode = "systematic"
        seed = None
        programs = generator.systematic_corpus()
    elif args.input:
        mode = "input"
        seed = None
        programs = _load_programs(Path(args.input))
    else:
        mode = "generated"
        seed = args.seed if args.seed is not None else 0
        generator.seed(seed)
        programs = generator.generate(count=args.count)

    if args.limit is not None:
        programs = programs[: args.limit]
    if not programs:
        print("No programs to compare.")
        return

    corpus = get_corpus()
    writer = corpus.create_run(
        mode=mode,
        seed=seed,
        config={
            "count": len(programs),
            "repeat": args.repeat,
            "oracle_timeout_s": get_oracle().timeout,
            "runner_timeout_s": get_runner().timeout,
            "build_timeout_s": get_runner().build_timeout,
            "memory_limit_mb": get_runner().memory_limit_mb,
        },
        notes=f"compare {mode}",
    )
    print(f"Run: {writer.run_id} ({len(programs)} programs, repeat={args.repeat})")
    print(f"CPython {get_oracle().get_version()}; sandbox=bwrap")

    records: List[Dict[str, Any]] = []
    registered: List[str] = []
    signatures: set[str] = set()

    for index, program in enumerate(programs, start=1):
        label = program.description or program.family.value
        try:
            comparison = _execute_pair(program.code, f"program_{index:04d}.py")
        except Exception as exc:
            print(f"[{index}/{len(programs)}] ERROR {type(exc).__name__}: {exc}")
            writer.append({
                "index": index,
                "family": program.family.value,
                "seed": program.seed,
                "description": program.description,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        fingerprints = [comparison.observable_fingerprint()]
        for _ in range(max(0, args.repeat - 1)):
            fingerprints.append(
                _execute_pair(program.code, f"program_{index:04d}.py").observable_fingerprint()
            )
        if len(set(fingerprints)) > 1:
            comparison = ComparisonResult(
                source_code=comparison.source_code,
                cpython=comparison.cpython,
                piton=comparison.piton,
                classification=Classification.NONDETERMINISTIC,
                reason=f"{args.repeat} repetitions produced different observables",
                details={"fingerprints": fingerprints},
            )

        case_id = corpus.register(
            comparison,
            family=program.family.value,
            seed=program.seed,
            description=program.description,
            run_id=writer.run_id,
            is_valid=program.is_valid,
            expected_exception=program.expected_exception,
            generator={"complexity": program.complexity, "code_hash": program.code_hash},
        )
        signature = divergence_signature(comparison)
        if signature:
            signatures.add(signature)

        record = {
            "index": index,
            "case_id": case_id,
            "source": program.code,
            "source_hash": comparison.source_hash,
            "family": program.family.value,
            "seed": program.seed,
            "description": program.description,
            "classification": comparison.classification.value,
            "reason": comparison.reason,
            "signature": signature,
            "fingerprint": comparison.observable_fingerprint(),
            "fingerprints": fingerprints,
            "cpython": comparison.cpython.to_dict() if comparison.cpython else None,
            "piton": comparison.piton.to_dict() if comparison.piton else None,
            "duration_ms": round(
                (comparison.cpython.duration_ms if comparison.cpython else 0.0)
                + (
                    comparison.piton.build_ms + comparison.piton.execute_ms
                    if comparison.piton else 0.0
                ),
                1,
            ),
        }
        writer.append(record)
        records.append(record)
        registered.append(case_id)

        suffix = ""
        if comparison.classification != Classification.EQUIV:
            suffix = f"  <- {comparison.reason}"
        print(f"[{index}/{len(programs)}] {label}: {comparison.classification.value}{suffix}")

    counts: Dict[str, int] = {classification.value: 0 for classification in Classification}
    for record in records:
        counts[record["classification"]] += 1
    total = len(records)
    summary = writer.finalize({
        "classification_counts": counts,
        "programs_requested": len(programs),
        "programs_executed": total,
        "harness_errors": len(programs) - total,
        "unique_divergences": len(signatures),
        "unique_signatures": sorted(signatures),
        "cases_registered": len(set(registered)),
    })

    print("\n" + "=" * 60)
    print(f"Run {writer.run_id} finished")
    print(f"Programs executed: {total} / {len(programs)} requested")
    _print_classification_counts(counts, total)
    print(f"Unique divergence signatures: {len(signatures)}")
    if total:
        print(
            f"\nEQUIV rate is {counts['EQUIV']}/{total} on this sample; "
            "it is not a global Python-compatibility percentage."
        )
    print(f"Artifacts: {writer.path}")
    print(f"Cases:     {corpus.cases_dir}")


def synthesize_command(args):
    """Write a missing module by rule, then measure whether it is correct.

    Every case runs twice: baseline with the stock compiler and overlay with
    the candidate registered. A candidate only counts as *written* when the
    overlay pass is EQUIV and the baseline pass was not, so a module PITON
    already had can never be mistaken for a synthesis success.
    """
    _require_sandbox()
    from .synthesizer import ModuleSynthesizer, save_candidate, synthesize_module

    synthesizer = ModuleSynthesizer(
        oracle_timeout=get_oracle().timeout,
        runner_timeout=get_runner().timeout,
        build_timeout=get_runner().build_timeout,
    )
    corpus = get_corpus()
    notes = f"synthesize {args.module}" + (" (mutated control)" if args.control else "")
    writer = corpus.create_run(
        mode="synthesis",
        seed=args.seed,
        config={"module": args.module, "count": args.count, "mutated": bool(args.control)},
        notes=notes,
    )
    print(f"Run: {writer.run_id}")
    print(f"Writing '{args.module}' from the CPython oracle source via the ADAPT rule.")

    summary = synthesizer.run(
        args.module, count=args.count, seed=args.seed, mutate=bool(args.control)
    )
    for index, case in enumerate(summary.cases, start=1):
        writer.append({"index": index, **case})

    candidate_path = None
    if args.save_candidate:
        candidate = synthesize_module(args.module, mutate=bool(args.control))
        saved = save_candidate(candidate, Path(__file__).resolve().parent / "candidates")
        candidate_path = saved.relative_to(Path(__file__).resolve().parents[1])

    payload = summary.to_dict()
    payload.pop("cases", None)
    writer.finalize({
        "classification_counts": summary.with_overlay,
        "synthesis": payload,
        "candidate_path": str(candidate_path) if candidate_path else None,
    })

    print(f"Candidate: {summary.candidate['piton_lines']} lines, "
          f"sha256 {summary.candidate['piton_sha256'][:16]}...")
    if summary.candidate.get("mutation"):
        print(f"Control mutation: {summary.candidate['mutation']}")
    print(f"Baseline (stock compiler):   {summary.baseline}")
    print(f"With candidate registered:   {summary.with_overlay}")
    print(f"Rejected by compiler anyway: {summary.candidate_rejected}/{summary.programs}")
    if summary.crashes:
        print(f"Crashed programs by pass: {dict(sorted(summary.crashes.items()))}")
    print(f"\nCorrect (EQUIV) with candidate: {summary.overlay_equiv}/{summary.programs}; "
          f"without candidate: {summary.baseline_equiv}/{summary.programs}")
    print("EQUIV is counted over every generated case; INVALID_REFERENCE and "
          "INVALID_ACCEPTED are reported separately, not folded in.")
    print(f"Artifacts: {writer.path}")


def minimize_command(args):
    """Structurally minimize a divergent case, preserving the divergence kind."""
    _require_sandbox()
    corpus = get_corpus()
    case = corpus.load_case(args.case)
    case_id = case.get("case_id") or corpus.case_id_for(case["source"])
    original = case["source"]
    stored = (case.get("result") or {}).get("classification", "?")

    writer = corpus.create_run(
        mode="minimize",
        seed=case.get("seed"),
        config={"case_id": case_id, "max_evaluations": args.max_evaluations},
        notes=f"minimize {case_id}",
    )
    print(f"Run: {writer.run_id}")
    print(f"Case: {case_id} (stored classification: {stored}, {len(original)} bytes)")

    cached: Dict[str, ComparisonResult] = {}

    def run_fn(source: str) -> ComparisonResult:
        if source not in cached:
            cached[source] = _execute_pair(source, "candidate.py")
        return cached[source]

    def progress(entry: Dict[str, Any]) -> None:
        if entry["accepted"]:
            print(f"  accepted #{entry['attempt']}: {entry['reduction']} -> {entry['classification']}")

    minimizer = FailureMinimizer(run_fn, max_evaluations=args.max_evaluations)
    result = minimizer.minimize(original, progress=progress)
    baseline = cached[original]
    signature = divergence_signature(baseline)

    minimized_case_id: Optional[str] = None
    if result.changed:
        final_comparison = run_fn(result.minimized_source)
        minimized_case_id = corpus.register(
            final_comparison,
            family=case.get("family", "manual"),
            seed=int(case.get("seed", 0)),
            description=f"minimized from {case_id}",
            run_id=writer.run_id,
            is_valid=bool(case.get("is_valid", True)),
            expected_exception=case.get("expected_exception"),
            minimized_from=case_id,
            generator={"minimizer": "structural-v0", "original_case": case_id},
        )

    payload = {
        "case_id": case_id,
        "minimized_case_id": minimized_case_id,
        "created_at": utc_now(),
        "run_id": writer.run_id,
        **result.to_dict(),
    }
    report_path = corpus.record_minimization(case_id, payload)
    writer.append({
        "index": 1,
        "case_id": case_id,
        "minimized_case_id": minimized_case_id,
        "classification": result.minimized_classification,
        "classification_before": result.original_classification,
        "attempts": result.attempts,
        "accepted": result.accepted,
        "changed": result.changed,
        "stopped_reason": result.stopped_reason,
    })
    writer.finalize({
        "classification_counts": {result.minimized_classification: 1},
        "programs_requested": 1,
        "programs_executed": 1,
        "harness_errors": 0,
        "unique_divergences": 1 if signature else 0,
        "unique_signatures": [signature] if signature else [],
        "cases_registered": 1 if minimized_case_id else 0,
    })

    print(f"\nAttempts: {result.attempts}, accepted reductions: {result.accepted}")
    print(f"Stopped: {result.stopped_reason}")
    print(f"Classification preserved: {result.minimized_classification} ({result.preserved_kind})")
    print(f"Size: {len(original)} -> {len(result.minimized_source)} bytes")
    if result.changed:
        print(f"Minimized case: {minimized_case_id}")
        print("\n--- minimized source ---")
        print(result.minimized_source)
    else:
        print("No reduction preserved the divergence; original source retained.")
    print(f"Report: {report_path}")


def replay_command(args):
    """Replay a stored case and report any classification drift."""
    _require_sandbox()
    corpus = get_corpus()
    case = corpus.load_case(args.case)
    case_id = case.get("case_id") or corpus.case_id_for(case["source"])
    source = case["source"]
    stored = (case.get("result") or {}).get("classification")

    writer = corpus.create_run(
        mode="replay",
        seed=case.get("seed"),
        config={"case_id": case_id, "repeat": args.repeat},
        notes=f"replay {case_id}",
    )
    print(f"Run: {writer.run_id}")
    print(f"Case: {case_id} (stored classification: {stored})")
    print(f"Source:\n{source}\n")

    comparison = _execute_pair(source, "replay.py")
    fingerprints = [comparison.observable_fingerprint()]
    for _ in range(max(0, args.repeat - 1)):
        fingerprints.append(_execute_pair(source, "replay.py").observable_fingerprint())
    stable = len(set(fingerprints)) == 1
    if not stable:
        comparison = ComparisonResult(
            source_code=source,
            cpython=comparison.cpython,
            piton=comparison.piton,
            classification=Classification.NONDETERMINISTIC,
            reason=f"{args.repeat} repetitions produced different observables",
            details={"fingerprints": fingerprints},
        )

    cpython = comparison.cpython
    piton = comparison.piton
    print("=" * 60)
    print("CPython")
    print("=" * 60)
    if cpython is not None:
        print(f"status={cpython.status} exit={cpython.exit_code} duration_ms={cpython.duration_ms:.1f}")
        print(f"exception={cpython.exception_type}: {cpython.exception_message}")
        print(f"stdout={cpython.stdout!r}")
        print(f"stderr={cpython.stderr!r}")
    print("\n" + "=" * 60)
    print("PITON")
    print("=" * 60)
    if piton is not None:
        stage = piton.failed_stage.value if piton.failed_stage else piton.stage_reached.value
        print(
            f"stage={stage} executed={piton.executed} exit={piton.exit_code} "
            f"duration_ms={piton.build_ms + piton.execute_ms:.1f}"
        )
        print(f"exception={piton.exception_type}: {piton.exception_message}")
        print(f"stdout={piton.stdout!r}")
        print(f"stderr={piton.stderr[-2000:]!r}")
        print(f"artifact_sha256={piton.artifact_sha256}")

    print("\n" + "=" * 60)
    print("Comparison")
    print("=" * 60)
    print(f"classification={comparison.classification.value}")
    print(f"reason={comparison.reason}")
    print(f"stable={stable} ({len(set(fingerprints))} distinct fingerprints)")
    drift = bool(stored) and comparison.classification.value != stored
    if drift:
        print(f"DRIFT: stored={stored}, replay={comparison.classification.value}")
    else:
        print("No drift against the stored classification.")

    corpus.record_replay(
        case_id,
        writer.run_id,
        comparison,
        command=f"python3 -m autopiton replay {case_id}",
    )
    signature = divergence_signature(comparison)
    writer.append({
        "index": 1,
        "case_id": case_id,
        "classification": comparison.classification.value,
        "stored_classification": stored,
        "drift": drift,
        "stable": stable,
        "fingerprint": comparison.observable_fingerprint(),
        "fingerprints": fingerprints,
        "signature": signature,
        "reason": comparison.reason,
        "cpython": cpython.to_dict() if cpython else None,
        "piton": piton.to_dict() if piton else None,
    })
    writer.finalize({
        "classification_counts": {comparison.classification.value: 1},
        "programs_requested": 1,
        "programs_executed": 1,
        "harness_errors": 0,
        "unique_divergences": 1 if signature else 0,
        "unique_signatures": [signature] if signature else [],
        "cases_registered": 0,
        "drift": drift,
    })


def report_command(args):
    """Report a stored run (--run/--latest) or the whole persisted corpus."""
    corpus = get_corpus()

    if args.run or args.latest:
        run = corpus.load_run(args.run)
        if args.json:
            print(json.dumps(run, indent=2, sort_keys=True))
            return
        meta = run["meta"]
        summary = run["summary"] or {}
        print(f"AutoPiton run report: {meta['run_id']}")
        print("=" * 60)
        print(f"mode={meta.get('mode')} seed={meta.get('seed')} started={meta.get('started_at')}")
        env = meta.get("environment", {})
        piton = env.get("piton", {})
        print(
            f"CPython {env.get('cpython_version')} | PITON commit {piton.get('commit')} "
            f"(dirty={piton.get('dirty')}) | sandbox={env.get('sandbox_backend')}"
        )
        print(f"config={json.dumps(meta.get('config', {}), sort_keys=True)}")
        executed = summary.get("programs_executed", len(run["records"]))
        print(f"Programs requested: {summary.get('programs_requested', executed)}")
        print(f"Programs executed: {executed}")
        print(f"Harness errors: {summary.get('harness_errors', 0)}")
        if summary.get("duration_ms") is not None:
            print(f"Duration: {summary['duration_ms']} ms")
        _print_classification_counts(summary.get("classification_counts") or {}, executed)
        print(f"Unique divergence signatures: {summary.get('unique_divergences', 0)}")
        for signature in summary.get("unique_signatures", []):
            print(f"  {signature}")
        if not summary:
            print("(summary.json missing; run did not finish)")
        interesting = [
            record for record in run["records"]
            if record.get("classification") and record["classification"] != "EQUIV"
        ]
        if interesting:
            print("\nNon-EQUIV cases in this run:")
            for record in interesting:
                print(
                    f"  {record['classification']:<20} "
                    f"{record.get('case_id', '-'):<24} {record.get('description', '')}"
                )
        return

    index = corpus.load_index()
    cases = index.get("cases", {})
    counts = corpus.case_counts()
    if args.json:
        print(json.dumps({
            "cases": len(cases),
            "classification_counts": counts,
            "signatures": index.get("signatures", {}),
            "updated_at": index.get("updated_at"),
        }, indent=2, sort_keys=True))
        return
    print("AutoPiton corpus report")
    print("=" * 60)
    print(f"Cases: {len(cases)}")
    for name, count in sorted(counts.items()):
        print(f"  {name:<20} {count:>4}")
    signatures = index.get("signatures", {})
    print(f"Unique divergence signatures: {len(signatures)}")
    for signature, case_ids in sorted(signatures.items()):
        suffix = " ..." if len(case_ids) > 5 else ""
        print(f"  {signature}")
        print(f"    cases: {', '.join(case_ids[:5])}{suffix}")
    latest = corpus.latest_run()
    print(f"Latest run: {latest.name if latest else '-'}")
    if latest is not None and (latest / "summary.json").is_file():
        summary = json.loads((latest / "summary.json").read_text(encoding="utf-8"))
        print(
            f"  executed={summary.get('programs_executed', 0)} "
            f"signatures={summary.get('unique_divergences', 0)}"
        )


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="autopiton",
        description="AutoPiton - Differential Fuzzer & Semantic Oracle for PITON",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    doctor_parser = subparsers.add_parser("doctor", help="Check environment and sandbox")
    doctor_parser.set_defaults(func=doctor_command)

    gen_parser = subparsers.add_parser("generate", help="Generate deterministic test programs")
    gen_parser.add_argument("-c", "--count", type=int, default=10, help="Number of programs")
    gen_parser.add_argument("-f", "--family", nargs="+", help="Program families to generate")
    gen_parser.add_argument("--seed", type=int, help="Random seed")
    gen_parser.add_argument("-o", "--output", help="Output file (JSON)")
    gen_parser.add_argument("-s", "--save", action="store_true", help="Save batch under cases/generated/")
    gen_parser.add_argument("-q", "--quiet", action="store_true", help="Do not print sources")
    gen_parser.set_defaults(func=generate_command)

    cmp_parser = subparsers.add_parser("compare", help="Compare CPython and PITON")
    cmp_parser.add_argument("-i", "--input", help="Input JSON list or directory of .py files")
    cmp_parser.add_argument("-c", "--count", type=int, default=10, help="Number of generated programs")
    cmp_parser.add_argument("--seed", type=int, help="Random seed (default 0)")
    cmp_parser.add_argument("--canary", action="store_true", help="Use the mandatory canary cases")
    cmp_parser.add_argument("--systematic", action="store_true", help="Use the deterministic systematic grid")
    cmp_parser.add_argument("--repeat", type=int, default=1, help="Repetitions per program for stability")
    cmp_parser.add_argument("--limit", type=int, help="Only run the first N programs")
    cmp_parser.set_defaults(func=compare_command)

    syn_parser = subparsers.add_parser(
        "synthesize", help="Write a missing stdlib module by rule and measure it"
    )
    syn_parser.add_argument("--module", required=True, help="Module name (CPython source must exist)")
    syn_parser.add_argument("-c", "--count", type=int, default=12, help="Generated API cases")
    syn_parser.add_argument("--seed", type=int, default=7, help="Argument-grammar seed")
    syn_parser.add_argument("--control", action="store_true", help="Flip one comparison (anti-fake control)")
    syn_parser.add_argument(
        "--save-candidate", action="store_true", help="Save candidate under autopiton/candidates/"
    )
    syn_parser.set_defaults(func=synthesize_command)

    min_parser = subparsers.add_parser("minimize", help="Minimize a divergent case")
    min_parser.add_argument("case", help="Case id or case JSON path")
    min_parser.add_argument("--max-evaluations", type=int, default=120, help="Candidate execution budget")
    min_parser.set_defaults(func=minimize_command)

    replay_parser = subparsers.add_parser("replay", help="Replay a stored case")
    replay_parser.add_argument("case", help="Case id or case JSON path")
    replay_parser.add_argument("--repeat", type=int, default=1, help="Repetitions for stability")
    replay_parser.set_defaults(func=replay_command)

    report_parser = subparsers.add_parser("report", help="Report a run or the corpus")
    report_parser.add_argument("--run", help="Run id under reports/runs/")
    report_parser.add_argument("--latest", action="store_true", help="Use the latest run")
    report_parser.add_argument("--json", action="store_true", help="Emit JSON")
    report_parser.set_defaults(func=report_command)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
