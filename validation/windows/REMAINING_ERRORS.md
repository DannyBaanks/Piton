# 8 Remaining Regression Errors (All MIR-Level)

All 8 remaining errors require changes to `piton/mir.py` which is outside the scope of x86-only fixes.

## Group 1: Bare re-raise from catch-all handler (2 errors)

- `test_x86_bare_reraise_from_catchall_rejected`
- `test_bare_reraise_from_catchall_handler`

**MIR error:** `"native bare re-raise from a catch-all (excepto Exception) handler is not supported yet"`

**Root cause:** `mir.py` `_lower_reraise()` rejects bare `lanzar` inside a catch-all `excepto Exception` handler. The test expects the re-raise to propagate with the runtime type.

**Fix needed:** In `mir.py` `_lower_reraise()`, emit `raise_active_dynamic` when `reraise_type` is `None` or `"Exception"`.

## Group 2: iter(callable, sentinel) (1 error)

- `test_x86_iter_callable_sentinel`

**MIR error:** `"native iter requires one positional argument"`

**Root cause:** `mir.py` `_lower_expr()` only accepts `iter(x)` (1 arg). The 2-arg form `iter(f, 0)` is not implemented.

**Fix needed:** Add `builtin_iter_new("calliter", ...)` opcode emission for `iter(callable, sentinel)`.

## Group 3: math.trunc (1 error)

- `test_x86_math_tier1_v1`

**MIR error:** `"native math.trunc is not supported or requires one positional argument"`

**Root cause:** `mir.py` `_MATH_ATTRS` dict maps `sqrt`, `floor`, `ceil`, `sin`, `cos`, `log` but not `trunc`, `fabs`, or `gcd`.

**Fix needed:** Add `trunc`, `fabs`, `gcd` to `_MATH_ATTRS` and corresponding MIR opcodes + x86 handlers.

## Group 4: next(it, default) (1 error)

- `test_x86_next_with_default`

**MIR error:** `"native next requires one positional argument"`

**Root cause:** `mir.py` `_lower_expr()` only accepts `next(x)` (1 arg). The 2-arg form `next(it, -1)` is not implemented.

**Fix needed:** Add `try_push/iter_next/try_pop` pattern to catch `StopIteration` and bind the default value.

## Group 5: with multiple context managers (2 errors)

- `test_with_multiple_items_fails_closed`
- `test_with_multiple_suppress_and_propagate`

**MIR error:** `"native with supports a single context manager for now"`

**Root cause:** `mir.py` `_lower_with()` rejects `with` statements with multiple `as` items.

**Fix needed:** Implement nested `With` lowering (inner `__exit__` runs first, matching CPython).

## Group 6: relative import with variable in imported module (1 error)

- `test_x86_relative_two_levels_up_supported`

**MIR error:** `"native imported modules currently support functions only"`

**Root cause:** After resolving the relative import (now working in x86.py), the imported module `pkg.comun` contains a variable assignment (`comun = 103`), not a function. MIR only supports function definitions in imported modules.

**Fix needed:** Add variable/import handling to MIR's module lifting phase.

## Summary

| Group | Tests | MIR Component | Feature |
|---|---|---|---|
| 1 | 2 | `_lower_reraise` | RERAISE_COMPLETE_V1 |
| 2 | 1 | `_lower_expr` | `iter(callable, sentinel)` |
| 3 | 1 | `_MATH_ATTRS` | `math.trunc/fabs/gcd` |
| 4 | 1 | `_lower_expr` | `next(it, default)` |
| 5 | 2 | `_lower_with` | WITH_MULTIPLE_V1 |
| 6 | 1 | module lifting | Variables in imported modules |
