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
  On Ubuntu 24.04 unprivileged user namespaces are restricted by AppArmor, so
  `sandbox_available()` reports False and every sandbox-gated test skips; the CI
  gate lifts it with
  `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` and then runs
  `doctor`, so a sandbox-less runner turns the job red instead of green.
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
python3 -m autopiton compare --systematic   # deterministic op x kind x surface grid
python3 -m autopiton synthesize --module colorsys --control   # write a missing stdlib module by rule, then measure it
python3 -m autopiton report --latest
python3 -m autopiton minimize <case-id>
python3 -m autopiton replay <case-id>
```

`compare` always persists a run under `reports/runs/<run-id>/` (meta, results
JSONL, summary) and registers every distinct program in the corpus under
`cases/`.

Run the suite from the repository root:

```
python3 -m pytest autopiton/tests -q        # 70 tests (measured 2026-10-31)
```

## Commands

| Command | Purpose |
|---|---|
| `doctor` | Probe sandbox, CPython, PITON repo provenance, generator; exit 2 if no sandbox |
| `generate` | Emit deterministic programs (`--count`, `--seed`, `--family`, `--save`) |
| `compare` | Run programs through both engines and classify (`--canary`, `--systematic`, `--input`, `--count`, `--seed`, `--repeat`) |
| `synthesize` | Write a missing stdlib module by rule (ADAPT translation of CPython's source) and measure baseline vs overlay (`--module`, `--count`, `--seed`, `--control`, `--save-candidate`) |
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
  synthesizer.py     writes a missing stdlib module by rule; baseline vs overlay measurement
  minimizer.py       AST-level divergence-preserving shrinker
  corpus.py          cases + runs + divergence signature index
  sandbox.py         fail-closed bwrap isolation with OS resource limits
  cli.py             the six commands above
  tests/             sandbox canaries, comparator controls, integration
  cases/             corpus: index.json, <case>.json, <case>.py
  candidates/        synthesized module bodies (never inside piton/)
  reports/runs/      per-run meta.json, results.jsonl, summary.json
```

## Documentation

- `ARCHITECTURE.md` — components, data flow, stage semantics.
- `GENERATOR_SPEC.md` — what the generator may and may not emit.
- `ORACLE_CONTRACT.md` — result schemas and the classification contract.
- `SAFETY.md` — the sandbox guarantees and their known limits.
- `RESULTS.md` — measured evidence for V0, with a claim table.

## Status — V0 measured 2026-10-30

Environment of every run below: CPython 3.12.3, bubblewrap 0.9.0, PITON commit
`fd76a88d` **with a dirty working tree** (so each number is a measurement of
that tree, not of a release). The 2026-10-31 addendum in `RESULTS.md` re-measures
the grid, the canaries and the synthesis on a clean tree (`d202e9e`) and records
the same numbers.

- Seeded corpus `run_20261030_163754_generated_seed42` (seed 42): 100/100
  executed, 89 EQUIV, 11 DIVERGENT, 2 unique divergence signatures.
- Systematic grid `run_20261030_170633_systematic` (58 programs produced by the
  generator from one rule — no hand-written programs): 39 EQUIV, 19 DIVERGENT,
  same 2 signatures. The earlier direct-only 29-cell pass, which produced
  29/29 EQUIV and falsified the first grid design, is kept as
  `run_20261030_170447_systematic`.
- Combined corpus: 147 distinct registered cases (`bool->int` bucket 19,
  `float->int` bucket 11).
- Finding the grid produced: **every divergence in the grid fires only when the
  value crosses a callable boundary** (returned from a lambda). Direct `print`
  of the same expression is EQUIV, and bitwise ops are EQUIV on both surfaces.
  That bounds the PITON-side investigation to the callable-return lowering
  path; it is a location, not a diagnosis.
- Suite: 65 tests pass. Re-measured 2026-10-31: **70** collected, 70 passed on
  this machine; the CI gate collects the same 70 and reports 68 passed / 2
  skipped. The two skipped are the integration canaries that assert PITON
  *rejection* behaviour, gated on PITON commit `fd76a88` being an ancestor
  of `HEAD`, so they skip — visibly, with a reason — on a base that predates
  that work instead of reporting a false failure or a false pass.

### Synthesis — can AutoPiton write what PITON lacks?

Measured 2026-10-30 on the same environment and tree. `synthesize` reads a
missing stdlib module's CPython source, translates it with the project's own
ADAPT stage (`tools.parity_corpus_v4.python_to_piton`), registers the result
through the sandbox overlay (in-process only — `piton/` is never written), and
classifies every generated API case twice: against stock PITON and with the
candidate registered. The baseline pass is what makes the result attributable.

- `run_20261030_193229_synthesis_seed7` (`colorsys`, 12 API cases, unmutated
  candidate): baseline 0/12 EQUIV (12 DIVERGENT — stock PITON cannot resolve
  the module at all); with the candidate 1/12 EQUIV, 3 INVALID_REFERENCE, 8
  DIVERGENT. The EQUIV case is `hsv_to_rgb(1, 2, 7)`: a rule-written body
  *can* reach and satisfy the oracle, so the pipeline is not the limit — the
  runtime is.
- Control: `run_20261030_193704_synthesis_seed7` repeats the run with one
  comparison flipped per public function (`hls_to_rgb`, `hsv_to_rgb`,
  `rgb_to_hls`, `rgb_to_hsv`, `yiq_to_rgb`): 0/12 EQUIV, the former EQUIV case
  becomes DIVERGENT and another becomes INVALID_ACCEPTED. The oracle detects a
  wrong candidate on this sample, so the 1/12 is not the comparator being
  lenient. (The first control attempt, `run_20261030_193417_synthesis_seed7`,
  flipped a comparison in a function no case exercised and was therefore
  inert — kept as a falsified design.)
- Why the other 11/12: the blockers are PITON-side, attributed per module.
  `colorsys` — float arguments crossing a call boundary are reinterpreted as
  int64 bit patterns (`add(0.2, 0.5)` prints `9199052598866975130`);
  `nturl2path` — lazy dotted imports lose
  the name binding (`NameError: name urllib is not defined`); `glob`,
  `statistics`, `ipaddress`, `uuid`, `configparser` — MIR rejects the body;
  `stringprep` — missing `unicodedata` table; `quopri` — bytes parameters;
  `ntpath` — ADAPT list concatenation; `csv` — MiniBuildError. This is the
  same boundary the systematic grid found: what breaks is the callable
  boundary, not the module text.

  (This section earlier also claimed two overlay builds segfaulted with exit
  139. That claim is not supported by any committed artifact and the
  re-measurement on a clean tree records zero crashes — see the 2026-10-31
  addendum in `RESULTS.md`.)

Run records store the PITON checkout's basename only, never its absolute path,
so committed metadata cannot leak an operator's filesystem layout into public
history.

## Non-goals

AutoPiton does not auto-fix PITON, does not touch the compiler, does not depend
on any LLM, and does not push to any remote. A divergence is a *finding*, not a
task to silently resolve.
