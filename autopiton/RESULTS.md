# AutoPiton Results (V0)

All numbers below are measured, not asserted. Each run persists
`meta.json` (environment + config + PITON provenance), `results.jsonl` (one
record per program) and `summary.json` under `autopiton/reports/runs/<run-id>/`.
Reproduce any line with `python3 -m autopiton replay <case-id>`.

## Environment

| Item | Value |
|---|---|
| CPython (oracle) | **3.12.3** |
| bubblewrap | 0.9.0 |
| PITON commit under test | `fd76a88d50d6052188f986eed11a4f21d9375b20` |
| PITON working tree | **dirty=True** (uncommitted local changes present) |
| AutoPiton suite | 51 tests, all passing |

The dirty PITON tree is recorded honestly: these divergences are attributed to
that specific uncommitted state, not to a clean tagged release.

## Mandatory canaries — `run_20261030_161309_canary` (repeat 2)

| Canary | Probe | Verdict | PITON stage |
|---|---|---|---|
| A | lambda | EQUIV | EXECUTE |
| B | unexpected keyword arg | INVALID_REFERENCE | rejected at MIR |
| C | `super()` multi-arg | **UNSUPPORTED** | rejected at BUILD |
| D | `zip` three iterables | **UNSUPPORTED** | rejected at MIR |
| E | bare re-raise | INVALID_REFERENCE | rejected at MIR |
| F | constructor invalid args | **INVALID_ACCEPTED** | executed, exit 0 |
| G | context manager | EQUIV | EXECUTE |
| H | dict merge `\|` | **UNSUPPORTED** | rejected at MIR |
| I | multiple starred args | EQUIV | EXECUTE |
| J | invalid exception base | INVALID_REFERENCE | rejected at MIR |

Counts: EQUIV 3, UNSUPPORTED 3, INVALID_REFERENCE 3, INVALID_ACCEPTED 1. Across
`--repeat 2` no canary fingerprint moved, so the harness is deterministic.

## Generated corpus — `run_20261030_163754_generated_seed42` (100 programs, seed 42)

| Classification | Count | % of executed |
|---|---|---|
| EQUIV | 89 | 89.0% |
| DIVERGENT | 11 | 11.0% |
| (all others) | 0 | — |

100/100 executed. **Unique divergence signatures: 2.** All 11 divergences are
output-only (`both completed but observable output differs`), and they collapse
into two genuine defect classes:

### Class 1 — `stdout:bool->int` (7 programs)
PITON prints booleans as `1`/`0`; CPython prints `True`/`False`.

```
v1 = lambda v2: False == -4
print(v1(77 << 0))
CPython: False      PITON: 0
```

### Class 2 — `stdout:float->int` (4 programs)
PITON prints a float value as its raw IEEE-754 bit pattern interpreted as an
int64 — a real numeric-printing defect.

```
class C1:
    def f1(self, v1):
        return v1 / 2
obj = C1()
print(obj.f1(9))
CPython: 4.5        PITON: 4616752568008179712   (= float bits of 4.5 as int64)
```

## Minimization — real divergence preserved

`case-4d25af23ed64826d` (canary F family) was minimized to:

```
case-707d1042403c5de2:  class A:
                            pass
                        A(0)
```

- Verdict held at **INVALID_ACCEPTED** throughout every accepted reduction
  (`divergence_kind` = `('INVALID_ACCEPTED','exception','TypeError','EXECUTE','')`).
- Replayed stable: no classification drift, identical native artifact sha256
  `2c1e46be92758e89ce8c646c8379d23a7dab823964bb6648a8d8abb5f24609fa`.

## Corpus state after these runs

89 distinct cases (deduplicated by source hash). Signature buckets:

| Signature | Cases |
|---|---|
| `DIVERGENT\|...\|stdout:bool->int` | 7 |
| `DIVERGENT\|...\|stdout:float->int` | 4 |
| `INVALID_ACCEPTED\|...\|piton:EXECUTE:-` | 2 |
| `UNSUPPORTED\|...\|piton:BUILD:-` | 1 |
| `UNSUPPORTED\|...\|piton:MIR:-` | 2 |

(The single coarse `…\|stdout` bucket from the earlier 100-program run was
migrated to the two shape buckets by the refined signature; `report` reflects
this.)

## Claim table

| Claim | Status |
|---|---|
| Dual engine execution under fail-closed bwrap sandbox | **DEMONSTRATED** |
| Sandbox isolation guarantees A–J | **DEMONSTRATED** (13 real-sandbox tests) |
| Comparator classification contract with anti-fake controls | **DEMONSTRATED** (15 controls + BrokenComparator) |
| Deterministic seeded generation, 16 families, 10 canaries | **DEMONSTRATED** (51 tests) |
| 100-program corpus run with persisted evidence | **DEMONSTRATED** (`run_...163754_generated_seed42`) |
| Divergence-preserving minimization of a real case | **DEMONSTRATED** (`case-707d1042403c5de2`) |
| Replay stability / drift detection | **DEMONSTRATED** (repeat 2, no drift) |
| Two distinct PITON output defects (bool→int, float→int) | **DEMONSTRATED** on this sample |
| "89% / 11%" is PITON's Python compatibility | **NOT DEMONSTRATED** — a rate on one 100-program seeded sample, nothing more |
| Divergence classes are exhaustive | **NOT DEMONSTRATED** — generator emits a bounded numeric sublanguage; strings/IO/imports are untested |
| Results hold on a clean PITON release | **NOT DEMONSTRATED** — measured against a **dirty** working tree |
| PITON semantics were changed to fix anything | **Explicitly false** — AutoPiton never edits the compiler |

## Limits of these results

- Sample size is 100 seeded programs over a restricted expression sublanguage.
- All divergences so far are output-formatting/numeric-printing, not crashes.
- The PITON tree was dirty; re-measure against a tagged commit before treating
  any defect as canonical in the genealogy.
