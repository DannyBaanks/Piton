# Parity corpus report v3 (enumerativo)

- backend medido: **linux_x86 (Linux native)** (instrumento: `tools/parity_corpus_v3.py`)
- oracle: CPython 3.12.3
- casos: **235** | EQUIV **95.7%** | comportamiento honesto (EQUIV+RAISE_EQ+FAIL_CLOSED) **100.0%** | DIVERGENT **0.0%**

Este reporte NO es un gate. Mide el subconjunto declarado en `NATIVE_COMPATIBILITY.md` mediante matrices enumeradas, no casos elegidos a mano.

## Buckets

| bucket | n |
|---|---|
| DIVERGENT_CRASH | 0 |
| DIVERGENT_MISMATCH | 0 |
| TIMEOUT | 0 |
| FAIL_CLOSED | 10 |
| RAISE_EQ | 0 |
| EQUIV | 225 |

## Por area

| area | EQUIV | RAISE_EQ | FAIL_CLOSED | DIVERGENT | TIMEOUT |
|---|---|---|---|---|---|
| str_methods_more | 24 | 0 | 1 | 0 | 0 |
| dict_ops_more | 18 | 0 | 1 | 0 | 0 |
| set_ops | 28 | 0 | 0 | 0 | 0 |
| frozenset | 15 | 0 | 0 | 0 | 0 |
| memoryview | 12 | 0 | 2 | 0 | 0 |
| slice_value | 10 | 0 | 0 | 0 | 0 |
| complex_deep | 22 | 0 | 0 | 0 | 0 |
| builtins_more | 27 | 0 | 0 | 0 | 0 |
| exceptions_deep | 11 | 0 | 3 | 0 | 0 |
| numeric_semantics | 30 | 0 | 1 | 0 | 0 |
| object_protocol3 | 14 | 0 | 2 | 0 | 0 |
| callable_protocol | 14 | 0 | 0 | 0 | 0 |

## Divergencias y timeouts (0)

Ninguna: no hay codigo nativo que corra y discrepe del oracle en este corpus.

## Casos DIVERGENT (fuente)

