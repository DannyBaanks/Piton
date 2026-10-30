# AutoPiton Oracle Contract

This is the contract the comparator enforces. It defines what "the same
behavior" means, how each result is structured, and every classification
outcome. Anything not observable here is not part of the verdict.

## Result records

### `CPythonResult` (reference)

`status ∈ {ok, exception, compile_error, system_exit, timeout, crash,
harness_error, missing_report}` plus `exit_code`, `stdout`, `stderr`,
`exception_type/message/traceback`, `cause_*`, `context_*`, `timed_out`,
`crash`, `signal_number`, `memory_exceeded`, `output_truncated`, `fs_created`,
`duration_ms`, and the pinned `python_version`/`python_executable`.

Derived properties: `compiled`, `harness_failed`, `succeeded` (status in
`{ok, system_exit}` **and** `exit_code == 0` **and** not timed-out/crashed).

### `PitonResult` (native)

Stage-oriented: `stage_reached`, `failed_stage`, per-stage `stages` map, the
build fields (`build_exit_code`, `build_stderr`, `artifact_sha256`,
`surface_sha256`), and the execute fields (`executed`, `exit_code`, `stdout`,
`stderr`, `timed_out`, `crash`, `signal_number`, `memory_exceeded`,
`output_truncated`, `exception_type/message`).

Derived: `rejected` (no execution **and** a real pipeline stage failed),
`harness_failed` (failed at `HARNESS`), `succeeded` (executed, `exit_code == 0`,
not timed-out/crashed, no exception).

## Classification enum

| Value | Meaning |
|---|---|
| `EQUIV` | Observable behavior is equivalent |
| `DIVERGENT` | Both engines ran, observable behavior differs |
| `UNSUPPORTED` | CPython succeeded; PITON rejected a valid Python feature at a pipeline stage |
| `INVALID_REFERENCE` | CPython rejects the program and PITON also rejects it |
| `INVALID_ACCEPTED` | CPython rejects but PITON executes the program successfully |
| `CRASH` | A process crashed (signal) in either engine |
| `TIMEOUT` | Either engine exceeded the time limit |
| `INCONCLUSIVE` | Harness could not obtain a verdict (missing data / harness failure) |
| `NONDETERMINISTIC` | Fingerprint moved across `--repeat` re-executions |

## Decision precedence

`compare()` applies these in order; the first match wins.

1. Either result missing → `INCONCLUSIVE`.
2. `cpython.timed_out or piton.timed_out` → `TIMEOUT`.
3. `cpython.crash or piton.crash` → `CRASH`.
4. Either `harness_failed` → `INCONCLUSIVE` (never counted as a divergence).
5. PITON not executed:
   - not rejected → `INCONCLUSIVE`;
   - rejected **and** CPython succeeded → `UNSUPPORTED`;
   - rejected **and** CPython failed → `INVALID_REFERENCE`.
6. CPython succeeded:
   - PITON succeeded → `EQUIV` if outputs match, else `DIVERGENT`;
   - PITON failed during execution → `DIVERGENT`.
7. PITON succeeded but CPython failed → `INVALID_ACCEPTED`.
8. Both failed → compare stdout, then exception type, then normalized message;
   `EQUIV` only if all match, else `DIVERGENT`.

## Normalization (applied before comparing)

- Output: CRLF/CR → LF, then right-strip trailing whitespace.
- Exception type: last dotted component (`builtins.ValueError` → `ValueError`).
- Exception message: whitespace normalized, `0x…` addresses → `0xADDR`, sandbox
  path prefix `/work/` removed.

These normalizations suppress only environment noise (addresses, paths, line
endings). They never reconcile a genuinely different value: `4.5` vs
`4616752568008179712`, or `True` vs `1`, stay `DIVERGENT`.

## Divergence signature

A *coarse, source-independent* key that groups programs exhibiting the **same
defect shape**, so a hundred programs with one root cause collapse to one
corpus entry. Format:

```
CLASSIFICATION|cpython:<status>:<exc>|piton:<stage|EXECUTE>:<exc>|<shape>
```

For output-only `DIVERGENT` cases the `<shape>` is `stdout:<cpKind>-><ptKind>`,
where each kind is the coarse type of the first differing output line
(`bool`, `int`, `float`, `none`, `str`, `empty`, `absent`, `other`). Kinds, not
literal values, are used on purpose: hashing exact values would fragment one
defect into one signature per program. Non-divergence classes return `None`
(cases dedupe by source hash alone). The index migrates a case's bucket when a
re-registration changes its signature.

## Anti-fake controls

The test suite includes a `BrokenComparator` that always returns `EQUIV`. Every
synthetic divergence control is asserted **not** to be `EQUIV` and to span at
least three distinct classifications, so a comparator that rubber-stamps
agreement cannot pass the suite. See `tests/test_comparator.py`.
