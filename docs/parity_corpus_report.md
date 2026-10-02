# Parity corpus report (enumerativo)

- backend medido: **linux_x86 (Linux native)** (instrumento: `tools/parity_corpus.py`)
- oracle: CPython 3.12.3
- casos: **1223** | EQUIV **87.7%** | comportamiento honesto (EQUIV+RAISE_EQ+FAIL_CLOSED) **100.0%** | DIVERGENT **0.0%**

Este reporte NO es un gate. Mide el subconjecto declarado en `NATIVE_COMPATIBILITY.md` mediante matrices enumeradas, no casos elegidos a mano.

## Buckets

| bucket | n |
|---|---|
| DIVERGENT_CRASH | 0 |
| DIVERGENT_MISMATCH | 0 |
| TIMEOUT | 0 |
| FAIL_CLOSED | 149 |
| RAISE_EQ | 1 |
| EQUIV | 1073 |

## Por area

| area | EQUIV | RAISE_EQ | FAIL_CLOSED | DIVERGENT | TIMEOUT |
|---|---|---|---|---|---|
| literals | 16 | 0 | 0 | 0 | 0 |
| arithmetic | 133 | 0 | 0 | 0 | 0 |
| associativity | 25 | 0 | 0 | 0 | 0 |
| branch_truthiness | 19 | 0 | 0 | 0 | 0 |
| comparisons | 99 | 0 | 12 | 0 | 0 |
| truthiness | 66 | 0 | 9 | 0 | 0 |
| ranges | 43 | 0 | 1 | 0 | 0 |
| bigint | 32 | 0 | 1 | 0 | 0 |
| builtins | 69 | 0 | 16 | 0 | 0 |
| strings | 20 | 0 | 22 | 0 | 0 |
| formatting | 32 | 0 | 17 | 0 | 0 |
| slices | 389 | 0 | 5 | 0 | 0 |
| collections | 27 | 0 | 7 | 0 | 0 |
| dicts | 9 | 0 | 11 | 0 | 0 |
| sets | 7 | 0 | 2 | 0 | 0 |
| comprehensions | 19 | 0 | 8 | 0 | 0 |
| control_flow | 16 | 0 | 4 | 0 | 0 |
| functions | 16 | 0 | 5 | 0 | 0 |
| exceptions | 18 | 1 | 2 | 0 | 0 |
| classes | 14 | 0 | 7 | 0 | 0 |
| generators | 2 | 0 | 6 | 0 | 0 |
| controls_closed | 2 | 0 | 14 | 0 | 0 |

## Divergencias y timeouts (0)

Ninguna: no hay codigo nativo que corra y discrepe del oracle en este corpus.

## Casos DIVERGENT (fuente)

