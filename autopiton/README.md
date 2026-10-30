# AutoPiton

Differential fuzzer and semantic oracle for **PITON**. AutoPiton generates
deterministic Python programs, runs each one under **two engines** — CPython
(the reference oracle) and the PITON native pipeline — inside a fail-closed
bubblewrap sandbox, compares the observable behavior, classifies the outcome,
minimizes divergences, and persists every result as hashed evidence.

It never modifies PITON semantics. Its only job is to *measure* where PITON's
native backend diverges from CPython and to record that divergence so it can be
reproduced and reasoned about.

## Requirements

- Linux with **bubblewrap** (`bwrap`) and usable user namespaces. Without a
  working sandbox AutoPiton refuses to execute anything (fail closed).
- CPython `python3` on `PATH` (the reference engine; measured against 3.12.3).
- A PITON checkout at the repository root (measured against commit
  `fd76a88d`). `gcc` must be available to PITON for the native BUILD stage.

## Quick start

Run from the PITON repository root:

```
python3 -m autopiton doctor                 # environment + sandbox check
python3 -m autopiton generate --count 20 --seed 42
python3 -m autopiton compare --canary       # the 10 mandatory canary cases
python3 -m autopiton compare --count 100 --seed 42
python3 -m autopiton report --latest
python3 -m autopiton minimize <case-id>
python3 -m autopiton replay <case-id>
```

`compare` always persists a run under `reports/runs/<run-id>/` (meta, results
JSONL, summary) and registers every distinct program in the corpus under
`cases/`.

## Commands

| Command | Purpose |
|---|---|
| `doctor` | Probe sandbox, CPython, PITON repo provenance, generator; exit 2 if no sandbox |
| `generate` | Emit deterministic programs (`--count`, `--seed`, `--family`, `--save`) |
| `compare` | Run programs through both engines and classify (`--canary`, `--input`, `--count`, `--seed`, `--repeat`) |
| `minimize` | Shrink a stored divergent case while preserving its divergence kind |
| `replay` | Re-execute a stored case, check fingerprint stability and classification drift |
| `report` | Summarize one run (`--run` / `--latest`) or the whole corpus |

## Layout

```
autopiton/
  generator.py       deterministic seeded program generator
  cpython_oracle.py  CPython reference engine (structured result)
  piton_runner.py    PITON native pipeline: ADAPT/PARSE/MIR/BUILD/EXECUTE
  comparator.py      classifies one CPython/PITON pair per the contract
  minimizer.py       AST-level divergence-preserving shrinker
  corpus.py          cases + runs + divergence signature index
  sandbox.py         fail-closed bwrap isolation with OS resource limits
  cli.py             the six commands above
  tests/             sandbox canaries, comparator controls, integration
  cases/             corpus: index.json, <case>.json, <case>.py
  reports/runs/      per-run meta.json, results.jsonl, summary.json
```

## Documentation

- `ARCHITECTURE.md` — components, data flow, stage semantics.
- `GENERATOR_SPEC.md` — what the generator may and may not emit.
- `ORACLE_CONTRACT.md` — result schemas and the classification contract.
- `SAFETY.md` — the sandbox guarantees and their known limits.
- `RESULTS.md` — measured evidence for V0, with a claim table.

## Non-goals

AutoPiton does not auto-fix PITON, does not touch the compiler, does not depend
on any LLM, and does not push to any remote. A divergence is a *finding*, not a
task to silently resolve.
