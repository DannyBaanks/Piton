"""Rule-based synthesis of modules PITON does not have, measured differentially.

V0 answers "where does PITON diverge from CPython". This component asks the
inverse question: can AutoPiton *write* part of what PITON still lacks, and can
the oracle tell the difference between "compiles" and "is correct"?

The writing is a translation rule, not a guess and not a hand-authored body:
the missing module is read from CPython's own source and put through the
project's own ADAPT surface (``tools.parity_corpus_v4.python_to_piton``), the
same translator the parity corpus uses. So a PASS measures the pipeline, not
the author.

Everything stays out of tree. Candidates are returned as text and, when saved,
land in ``autopiton/candidates/``; the measured build registers them through
the sandbox overlay (``piton_worker.apply_overlay``). ``piton/`` is never
written, and a candidate that needs a construct the native subset rejects is
reported as such instead of being quietly worked around.
"""

from __future__ import annotations

import ast
import hashlib
import io
import random
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .comparator import Classification, DifferentialComparator
from .cpython_oracle import CPythonOracle, PYTHON_EXECUTABLE
from .generator import GeneratedProgram, ProgramFamily
from .piton_runner import PitonRunner
from .sandbox import sandbox_available

# The stdlib AutoPiton reads from: the same oracle CPython it executes against.
# STDLIB_FROM_ORACLE_V1: no se hardcodea /usr/lib/python3.12. El candidato se
# traduce del source del intérprete que hace de oráculo, así que la ruta se
# deriva de ese intérprete; en runners donde la stdlib vive en el toolcache el
# path fijo daba FileNotFoundError y el candidato se medía contra un CPython
# distinto del que tradujo.
_STDLIB_CACHE: Dict[str, Path] = {}


def oracle_stdlib_dir(executable: str = PYTHON_EXECUTABLE) -> Path:
    """Ruta de la stdlib del oráculo, resuelta preguntándole a ese binario."""
    if executable in _STDLIB_CACHE:
        return _STDLIB_CACHE[executable]
    import subprocess
    import sysconfig as _sysconfig

    resolved: Optional[Path] = None
    try:
        probe = subprocess.run(
            [executable, "-c", "import sysconfig; print(sysconfig.get_path('stdlib'))"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if probe.returncode == 0:
            candidate = Path(probe.stdout.strip())
            if candidate.is_dir():
                resolved = candidate
    except (OSError, subprocess.TimeoutExpired):
        resolved = None
    if resolved is None:
        resolved = Path(_sysconfig.get_path("stdlib"))
    _STDLIB_CACHE[executable] = resolved
    return resolved

# A bounded, deterministic argument grammar — the V0 discipline kept: the
# generator never emits a value it cannot reproduce.
VALUE_GRAMMAR: Tuple[str, ...] = (
    "0",
    "1",
    "2",
    "7",
    "-3",
    "3.5",
    "True",
    "False",
    "'abc'",
    "'a,b'",
    "''",
    "[1, 2, 3]",
    "[3, 1, 2]",
    "[]",
    "(1, 2)",
    "{1: 'a'}",
)
_LIST_VALUES = ("[1, 2, 3]", "[3, 1, 2]", "[]", "[10, 4, 7, 1]")

# Comparison flip used by the falsification control: a candidate that compiles
# and runs but decides differently must not keep the oracle quiet. Each swap is
# the logical complement, so the mutated decision always differs for some input.
_COMP_SWAP = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "==": "!=", "!=": "=="}
# Loop conditions run on every call, so a flip there always changes behavior;
# branches and returns are the next best.
_MARKER_TIERS = (
    ("mientras ", "while "),
    ("si ", "if ", "retorna ", "return "),
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class CandidateModule:
    """A module body AutoPiton wrote by translation."""

    name: str
    reference_source: str
    piton_source: str
    reference_sha256: str
    piton_sha256: str
    mutated: bool = False
    mutation: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "reference_source": self.reference_source,
            "reference_sha256": self.reference_sha256,
            "piton_sha256": self.piton_sha256,
            "piton_chars": len(self.piton_source),
            "piton_lines": len(self.piton_source.splitlines()),
            "mutated": self.mutated,
            "mutation": self.mutation,
        }


def flip_first_comparison(text: str, function: Optional[str] = None) -> Tuple[str, str]:
    """Change one decision-bearing comparison; return (text, description).

    Operators are located with ``tokenize``, so a comparison inside a docstring
    or a comment is never mistaken for code. Preferring a loop condition, then
    a branch or return, keeps the control from passing by mutating something
    inert. With *function*, only that function's body is eligible. Raises
    ValueError when there is nothing flippable.
    """
    lines = text.splitlines(keepends=True)
    sites: List[Tuple[int, int, str, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.OP and token.string in _COMP_SWAP:
            sites.append((token.start[0], token.start[1], token.string, token.line))
    if function is not None:
        pattern = re.compile(rf"^funcion\s+{re.escape(function)}\s*\(")
        start = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
        if start is None:
            raise ValueError(f"function {function!r} not found in the surface")
        end = next(
            (j for j in range(start + 1, len(lines)) if lines[j].startswith("funcion ")),
            len(lines),
        )
        sites = [site for site in sites if start < site[0] <= end]
    if not sites:
        raise ValueError("no comparison operator available to mutate")

    def tier(line: str) -> Optional[int]:
        for level, markers in enumerate(_MARKER_TIERS):
            if any(marker in line for marker in markers):
                return level
        return None

    chosen = min(
        enumerate(sites),
        key=lambda item: (
            tier(item[1][3]) is None,
            tier(item[1][3]) or 0,
            item[0],
        ),
    )[1]
    row, column, old, _ = chosen
    new = _COMP_SWAP[old]
    line = lines[row - 1]
    mutated_line = line[:column] + new + line[column + len(old) :]
    mutated = "".join(lines[: row - 1]) + mutated_line + "".join(lines[row:])
    return mutated, f"line {row}: {old} -> {new}"


def synthesize_module(
    name: str,
    stdlib_dir: Optional[Path] = None,
    mutate: bool = False,
) -> CandidateModule:
    """Write a PITON module body for *name* by translating CPython's source.

    ``python_to_piton`` is the project's own ADAPT stage, so the candidate is
    what the existing toolchain already accepts, not a new dialect.
    """
    from tools.parity_corpus_v4 import python_to_piton

    stdlib_dir = Path(stdlib_dir) if stdlib_dir is not None else oracle_stdlib_dir()
    reference = (Path(stdlib_dir) / f"{name}.py").read_text(encoding="utf-8")
    piton_source = python_to_piton(reference)
    mutation = None
    if mutate:
        # One flip per public function: every generated case exercises some
        # public function, so the control cannot pass by mutating a body that
        # never runs.
        descriptions: List[str] = []
        for fn in module_functions(reference):
            try:
                piton_source, description = flip_first_comparison(piton_source, function=fn.name)
            except ValueError:
                continue
            descriptions.append(f"{fn.name} {description}")
        if not descriptions:
            raise ValueError("module has no flippable comparison")
        mutation = "; ".join(descriptions)
    return CandidateModule(
        name=name,
        # Se guarda la stdlib por su nombre, no por su ruta absoluta: same
        # convention as git_provenance(), para que un commit de evidencia no
        # publique el layout del equipo de quien corre el run.
        reference_source=f"{stdlib_dir.name}/{name}.py",
        piton_source=piton_source,
        reference_sha256=_sha256_text(reference),
        piton_sha256=_sha256_text(piton_source),
        mutated=mutate,
        mutation=mutation,
    )


def save_candidate(candidate: CandidateModule, directory: Path) -> Path:
    """Write the candidate outside the compiler tree (``autopiton/candidates``)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{candidate.name}.piton"
    path.write_text(candidate.piton_source, encoding="utf-8")
    return path


@dataclass
class PublicFunction:
    """One callable the case generator may exercise."""

    name: str
    required: int
    optional: int
    mentions_sequence: bool

    @property
    def max_arity(self) -> int:
        return self.required + self.optional


def module_functions(source: str) -> List[PublicFunction]:
    """Enumerate top-level public callables of a module from its AST."""
    tree = ast.parse(source)
    found: List[PublicFunction] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        args = node.args
        positional = list(args.posonlyargs) + list(args.args)
        defaulted = len(args.defaults)
        required = max(0, len(positional) - defaulted)
        dump = ast.dump(node)
        found.append(
            PublicFunction(
                name=node.name,
                required=required,
                optional=defaulted,
                mentions_sequence="List" in dump or "Subscript" in dump,
            )
        )
    return sorted(found, key=lambda fn: fn.name)


class ModuleCaseGenerator:
    """Deterministic programs that exercise a module's public API.

    The generator knows arity, not parameter types, so some calls are simply
    wrong. That is not hidden: CPython is the arbiter and the comparator labels
    those INVALID_REFERENCE. The question this answers is how many *valid*
    calls agree between engines, so the honest denominator is the valid ones.
    """

    def __init__(self, name: str, seed: int = 7, stdlib_dir: Optional[Path] = None):
        self.name = name
        self.seed = seed
        self.stdlib_dir = Path(stdlib_dir) if stdlib_dir is not None else oracle_stdlib_dir()
        source = (self.stdlib_dir / f"{name}.py").read_text(encoding="utf-8")
        self.functions = module_functions(source)

    def _values_for(self, fn: PublicFunction, rng: random.Random) -> List[str]:
        take_optional = bool(fn.optional) and rng.random() < 0.5
        count = fn.required + (1 if take_optional else 0)
        values: List[str] = []
        for position in range(count):
            # Sequence APIs take the container first (bisect, heapq, ...);
            # later positions are scalars far more often than containers.
            if position == 0 and fn.mentions_sequence:
                values.append(rng.choice(_LIST_VALUES))
            else:
                values.append(rng.choice(VALUE_GRAMMAR))
        return values

    def _bindings(self, values: List[str]) -> Dict[str, str]:
        """Bind repeated list literals to variables so mutation is observable."""
        bindings: Dict[str, str] = {}
        for value in values:
            if value in _LIST_VALUES and value not in bindings:
                bindings[value] = f"_l{len(bindings)}"
        return bindings

    def _render(self, fn: PublicFunction, values: List[str], bindings: Dict[str, str]) -> str:
        args = ", ".join(bindings.get(value, value) for value in values)
        call = f"{self.name}.{fn.name}({args})"
        lines = [f"import {self.name}"]
        for value, variable in bindings.items():
            lines.append(f"{variable} = {value}")
        lines.append(f"print({call})")
        for variable in bindings.values():
            # heappush/insort return None; the container is the only evidence.
            lines.append(f"print({variable})")
        return "\n".join(lines) + "\n"

    def generate(self, count: int = 20) -> List[GeneratedProgram]:
        rng = random.Random(self.seed)
        programs: List[GeneratedProgram] = []
        if not self.functions:
            return programs
        for step in range(count):
            fn = self.functions[step % len(self.functions)]
            values = self._values_for(fn, rng)
            bindings = self._bindings(values)
            code = self._render(fn, values, bindings)
            description = f"module[{self.name}] {fn.name}({', '.join(values)})"
            program = GeneratedProgram(
                code=code,
                family=ProgramFamily.MODULE,
                seed=self.seed,
                complexity=1 + min(3, len(values)),
                description=description,
            )
            try:
                compile(code, "<autopiton-synth>", "exec")
            except SyntaxError as exc:  # a broken template is labelled, not hidden
                program.is_valid = False
                program.expected_exception = "SyntaxError"
                program.description = f"{description} [auto-invalid: {exc.msg}]"
            programs.append(program)
        return programs


@dataclass
class SynthesisSummary:
    """One synthesis experiment: baseline vs candidate, per classification."""

    module: str
    candidate: Dict[str, Any]
    programs: int
    sandbox_available: bool
    baseline: Dict[str, int] = field(default_factory=dict)
    with_overlay: Dict[str, int] = field(default_factory=dict)
    baseline_stages: Dict[str, int] = field(default_factory=dict)
    overlay_stages: Dict[str, int] = field(default_factory=dict)
    crashes: Dict[str, int] = field(default_factory=dict)
    candidate_sha_seen: List[str] = field(default_factory=list)
    cases: List[Dict[str, Any]] = field(default_factory=list)

    def _count(self, table: Dict[str, int], key: str) -> int:
        return table.get(key, 0)

    @property
    def overlay_equiv(self) -> int:
        return self._count(self.with_overlay, Classification.EQUIV.value)

    @property
    def baseline_equiv(self) -> int:
        return self._count(self.baseline, Classification.EQUIV.value)

    @property
    def candidate_rejected(self) -> int:
        """Cases PITON refused even with the candidate registered."""
        return (
            self._count(self.overlay_stages, PitonStageKey.MIR)
            + self._count(self.overlay_stages, PitonStageKey.BUILD)
            + self._count(self.overlay_stages, PitonStageKey.PARSE)
            + self._count(self.overlay_stages, PitonStageKey.ADAPT)
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module,
            "candidate": self.candidate,
            "programs": self.programs,
            "sandbox_available": self.sandbox_available,
            "baseline": self.baseline,
            "with_overlay": self.with_overlay,
            "baseline_stages": self.baseline_stages,
            "overlay_stages": self.overlay_stages,
            "crashes": dict(self.crashes),
            "baseline_equiv": self.baseline_equiv,
            "overlay_equiv": self.overlay_equiv,
            "candidate_rejected": self.candidate_rejected,
            "candidate_sha_seen": sorted(set(self.candidate_sha_seen)),
            "cases": self.cases,
        }


class PitonStageKey:
    """Stage names as they appear in run records (kept as plain strings)."""

    ADAPT = "ADAPT"
    PARSE = "PARSE"
    MIR = "MIR"
    BUILD = "BUILD"
    EXECUTE = "EXECUTE"
    HARNESS = "HARNESS"


class ModuleSynthesizer:
    """Write a missing module, then measure it against the oracle."""

    def __init__(
        self,
        stdlib_dir: Optional[Path] = None,
        oracle_timeout: float = 10.0,
        runner_timeout: float = 10.0,
        build_timeout: float = 90.0,
    ):
        self.stdlib_dir = Path(stdlib_dir) if stdlib_dir is not None else oracle_stdlib_dir()
        self.oracle = CPythonOracle(timeout=oracle_timeout)
        self.runner = PitonRunner(timeout=runner_timeout, build_timeout=build_timeout)
        self.comparator = DifferentialComparator()

    @staticmethod
    def _bump(table: Dict[str, int], key: str) -> None:
        table[key] = table.get(key, 0) + 1

    def run(self, name: str, count: int = 20, seed: int = 7, mutate: bool = False) -> SynthesisSummary:
        """Synthesize *name* and classify every generated case twice over.

        The baseline pass (no overlay) is what makes a result attributable: if
        PITON already accepted the module without the candidate, the candidate
        proved nothing.
        """
        candidate = synthesize_module(name, self.stdlib_dir, mutate=mutate)
        programs = ModuleCaseGenerator(name, seed=seed, stdlib_dir=self.stdlib_dir).generate(count)
        summary = SynthesisSummary(
            module=name,
            candidate=candidate.to_dict(),
            programs=len(programs),
            sandbox_available=sandbox_available(),
        )
        if not summary.sandbox_available:
            return summary
        overlay = {candidate.name: candidate.piton_source}

        for program in programs:
            case: Dict[str, Any] = {
                "code": program.code,
                "description": program.description,
                "generator_valid": program.is_valid,
            }
            cpython = self.oracle.execute(program.code, "case.py")

            baseline = self.runner.execute(program.code, "case.py")
            baseline_verdict = self.comparator.compare(program.code, cpython, baseline)
            case["baseline"] = baseline_verdict.classification.value
            case["baseline_exit"] = baseline.exit_code
            case["baseline_crash"] = baseline.crash
            case["baseline_signal"] = baseline.signal_number
            self._bump(summary.baseline, baseline_verdict.classification.value)
            self._bump(
                summary.baseline_stages,
                baseline.failed_stage.value if baseline.failed_stage else "none",
            )
            if baseline.crash:
                summary.crashes["baseline"] = summary.crashes.get("baseline", 0) + 1

            with_overlay = self.runner.execute(program.code, "case.py", overlay_sources=overlay)
            overlay_verdict = self.comparator.compare(program.code, cpython, with_overlay)
            case["overlay"] = overlay_verdict.classification.value
            case["overlay_reject_stage"] = (
                with_overlay.failed_stage.value if with_overlay.failed_stage else "none"
            )
            case["overlay_sha256"] = with_overlay.overlay_sha256.get(candidate.name)
            # OBSERVABLE_OUTCOME_V1: el crashed/exito van al registro. Sin
            # ellos un SIGSEGV del candidato es indistinguible de un rechazo
            # limpio, y ningun artefacto puede sustentar despues la afirmacion
            # de que "el build hace segfault".
            case["overlay_exit"] = with_overlay.exit_code
            case["overlay_crash"] = with_overlay.crash
            case["overlay_signal"] = with_overlay.signal_number
            self._bump(summary.with_overlay, overlay_verdict.classification.value)
            self._bump(
                summary.overlay_stages,
                with_overlay.failed_stage.value if with_overlay.failed_stage else "none",
            )
            if with_overlay.crash:
                summary.crashes["overlay"] = summary.crashes.get("overlay", 0) + 1
            if case["overlay_sha256"]:
                summary.candidate_sha_seen.append(case["overlay_sha256"])
            summary.cases.append(case)
        return summary
