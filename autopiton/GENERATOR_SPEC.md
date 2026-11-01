# AutoPiton Generator Specification

The generator's contract is narrow and absolute: **the same `(seed, count,
family)` set must always produce byte-identical programs**, and every program
must be one AutoPiton can actually execute and classify. The generator is not a
general Python synthesizer; it is a controlled sampler over a documented
sublanguage.

## Output record

Each program is a `GeneratedProgram`:

| Field | Meaning |
|---|---|
| `source_code` | exact text handed to both engines |
| `family` | which of the 16 families produced it |
| `seed` | the integer seed |
| `complexity` | rough size/depth knob |
| `code_hash` | sha256 of the source |
| `is_valid` / `expected_exception` | validation outcome (see below) |
| `description` | human label used in reports |

## Determinism rules

1. A single `random.Random` is seeded from the CLI `--seed`.
2. Before each program the generator calls `reset()`, clearing the variable
   scope and any mutable counters.
3. Sources are rendered with `ast.unparse`, so formatting never depends on RNG
   iteration order.

Verified: seed 42 → 100 programs, 0 invalid, all 16 families reached; repeated
generation yields an identical digest.

## Expression sublanguage

Generated **expressions** are restricted to a safe, always-defined numeric core.
This is a deliberate design choice: a fuzzer that emits undefined names or
runtime explosions produces divergences that are the *generator's* fault, not
PITON's.

Allowed:

- Literals: `int` in `[-10, 10]`, `float` as `round(random*10, 2)`, `bool`.
- Scoped names only (a variable is read only after it is bound in this program).
- Binary ops via `_safe_binop`:
  - `+ - *` recurse.
  - `/ // %` use a **non-zero** integer right operand (`1..9`) so no
    `ZeroDivisionError` is manufactured.
  - `<< >>` use literal ints (`left ≤ 100`, `right ≤ 8`) to avoid huge values.
  - `& | ^` on literal ints.
- Unary `-`.
- Comparisons `== != < <= > >=` only.

Deliberately **excluded**:

- `**` (can explode), `is`/`is not` on literals (SyntaxWarning noise),
  `in`/`not in` (container coupling), and any `Call`/`Attribute`/`Subscript`
  inside the numeric sublanguage — calls, methods and indexing appear only in
  the family-level statement templates where names are known to be bound.

`str` literals are outside this sampler: they are emitted only by the systematic
grid renderer below, which writes complete programs as text and never touches
the AST expression grammar. Adding a value shape to the grid therefore cannot
loosen the sublanguage contract.

## The systematic grid (`systematic` family)

Not RNG-derived. `SystematicGrid.cells()` enumerates `(kind, op, operands)` and
each cell is rendered on every surface in `SURFACES`, giving exactly one program
per cell × surface in a fixed order (`seed` is ignored, so the corpus is
replayable from the rule alone).

- Surfaces: `direct` prints the expression inline, `lambda` binds it as
  `_v = lambda: <expr>` and prints `_v()`. The surface dimension exists because
  the observable difference appears only when a value crosses a callable
  boundary.
- Kinds: `arith_int`, `arith_float`, `compare_int`, `compare_bool`,
  `bitwise_int`, `arith_str`, `compare_str`. The string kinds exist to bound the
  boundary finding — it was observed for `bool` and `float`, so `str` is the
  third value shape and its verdict says whether the defect follows the crossing
  or the type.
- New cells are **appended**: `systematic_corpus(limit=N)` must return the same
  first N programs it returned before the grid grew, otherwise a truncated run
  stops being comparable with an archived one.
- Size: 37 cells × 2 surfaces = 74 programs (58 before the string kinds).

## Families (16)

```
arithmetic  boolean     variables   conditionals  loops       functions
lambdas     classes     super       exceptions    collections unpacking
decorators  properties  generators  async
```

Family templates bind their own scoped names and pass `allow_names=False` for
call arguments, so a function/class/call body cannot reference an unbound
variable.

## Validation

After construction each source is run through `compile()`. If it does not
compile, the program is marked `is_valid=False` with
`expected_exception="SyntaxError"` and the description is suffixed — CPython
then legitimately rejects it and the comparator records `INVALID_REFERENCE` when
PITON also rejects, rather than silently dropping the case.

## The ten mandatory canaries

Fixed, hand-written, never RNG-derived. They probe the exact places where the
reference and PITON are known or suspected to separate:

| ID | Probes | Observed V0 verdict |
|---|---|---|
| A | lambda (real, valid) | EQUIV |
| B | unexpected keyword argument | INVALID_REFERENCE |
| C | `super()` with multiple args | UNSUPPORTED (BUILD) |
| D | `zip` with three iterables | UNSUPPORTED (MIR) |
| E | bare `raise` re-raise | INVALID_REFERENCE |
| F | constructor with invalid args | INVALID_ACCEPTED |
| G | context manager | EQUIV |
| H | dict merge `|` | UNSUPPORTED (MIR) |
| I | multiple starred args | EQUIV |
| J | invalid exception base | INVALID_REFERENCE |

Canaries are the harness's own health check: if `compare --canary` stops
producing this spread (not all EQUIV, not all the same class), the pipeline —
not PITON — is broken.
