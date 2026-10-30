# AutoPiton Architecture

AutoPiton is a pipeline of six components around one invariant: **every program
is executed twice, in isolation, and the two observable results are compared by
a fixed contract.** No component decides semantics; they only produce, execute,
compare, shrink, and record.

```
                 ┌────────────┐
   seed/family ─►│ Generator  │─► GeneratedProgram (source + metadata)
                 └────────────┘            │
                                            ▼
                    ┌───────────────────────────────────┐
                    │            Sandbox                │  fail-closed bwrap
                    │  ┌──────────────┐ ┌─────────────┐ │
                    │  │CPythonOracle │ │ PitonRunner │ │
                    │  │ (reference)  │ │(ADAPT→EXEC) │ │
                    │  └──────┬───────┘ └──────┬──────┘ │
                    └─────────┼────────────────┼────────┘
                              ▼                ▼
                        CPythonResult     PitonResult
                              └──────┬───────┘
                                     ▼
                            ┌────────────────┐
                            │  Comparator    │─► Classification + reason + details
                            └────────────────┘
                                     ▼
              ┌───────────────────────────────────────────┐
              │  Corpus  (signatures, cases, runs, replays)│
              │  Minimizer (shrinks divergences)           │
              └───────────────────────────────────────────┘
```

## Components

### Generator (`generator.py`)
Produces `GeneratedProgram` records from a seeded RNG. Determinism is a hard
requirement: the same `(seed, count)` yields byte-identical sources. It also
holds the ten mandatory canary cases. See `GENERATOR_SPEC.md`.

### Sandbox (`sandbox.py`)
One class, one job: execute a command inside bubblewrap with a private network
namespace, a read-only system view, a writable temp workdir mounted at `/work`,
and OS resource limits applied *inside* the namespace via `prlimit`. A single
`Sandbox` instance backs both engines; the native payload runs in a separate
minimal root. See `SAFETY.md`.

### CPythonOracle (`cpython_oracle.py`)
Runs the *original* source under isolated CPython through a structured wrapper
(`cpython_wrapper.py`) that separates compile errors from runtime exceptions and
emits a JSON report. Produces `CPythonResult`. It never edits the program.

### PitonRunner (`piton_runner.py` + `piton_worker.py`)
Builds the source through the **real PITON native pipeline** inside the sandbox
worker and then executes the resulting static ELF in a minimal root. Each stage
is recorded separately:

| Stage | Meaning | Accepted when |
|---|---|---|
| `ADAPT` | Python→PITON surface transform | transform produced code |
| `PARSE` | `piton.parser.parse` | CST produced |
| `MIR` | lower CST→HIR→MIR | MIR produced |
| `BUILD` | C emit + `gcc` | gcc succeeds **and** artifact is a non-empty ELF |
| `EXECUTE` | run the static ELF | process returns (result recorded regardless of exit) |

`piton run` (the CPython backend) is deliberately **not** used — a build is only
accepted when a genuine native artifact exists. A failure before `EXECUTE` means
PITON *rejected* the program; a `HARNESS` failure means AutoPiton itself could
not obtain a verdict (recorded as `INCONCLUSIVE`, never as a divergence).

### Comparator (`comparator.py`)
Classifies one CPython/PITON pair. Precedence: `TIMEOUT` → `CRASH` → harness
(`INCONCLUSIVE`) → PITON non-execution (`UNSUPPORTED` / `INVALID_REFERENCE`) →
executed outcomes (`EQUIV` / `DIVERGENT` / `INVALID_ACCEPTED`). `HARNESS`
failures short-circuit to `INCONCLUSIVE`. The full contract and normalization
rules are in `ORACLE_CONTRACT.md`.

### Corpus (`corpus.py`)
Disk-backed store. Programs dedupe by source hash into `cases/<id>.json` +
`cases/<id>.py`; runs are append-only JSONL under `reports/runs/<run-id>/`; a
signature index groups distinct divergences so the same defect reported by many
programs collapses to one entry.

### Minimizer (`minimizer.py`)
AST-level shrinker that removes/normalizes statements, call arguments,
decorators, and constants, and only accepts a candidate that **preserves the
divergence kind** (classification + CPython status + PITON stage + exception
type). Bounded by an evaluation budget.

## Data flow per `compare`

1. Generate (canary or seeded).
2. For each program: execute in both engines, compare, compute signature +
   observable fingerprint.
3. `--repeat` re-executes and any fingerprint that moves across repeats is
   flagged `NONDETERMINISTIC`.
4. Register the case, append the run record, finalize the summary.

## Determinism & reproducibility

The RNG is seeded per program (counters and variable scope are reset each time).
Every run's `meta.json` pins CPython version, PITON git commit and dirty state,
bwrap path and the full config, so any `results.jsonl` line can be replayed with
`replay <case-id>`.
