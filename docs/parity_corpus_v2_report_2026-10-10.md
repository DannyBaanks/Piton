# Parity corpus report v2 (enumerativo)

- backend medido: **linux_x86 (Linux native)** (instrumento: `tools/parity_corpus_v2.py`)
- oracle: CPython 3.12.3
- casos: **221** | EQUIV **100.0%** | comportamiento honesto (EQUIV+RAISE_EQ+FAIL_CLOSED) **100.0%** | DIVERGENT **0.0%**

Este reporte NO es un gate. Mide el subconjunto declarado en `NATIVE_COMPATIBILITY.md` mediante matrices enumeradas, no casos elegidos a mano.

## Buckets

| bucket | n |
|---|---|
| DIVERGENT_CRASH | 0 |
| DIVERGENT_MISMATCH | 0 |
| TIMEOUT | 0 |
| FAIL_CLOSED | 0 |
| RAISE_EQ | 0 |
| EQUIV | 221 |

## Por area

| area | EQUIV | RAISE_EQ | FAIL_CLOSED | DIVERGENT | TIMEOUT |
|---|---|---|---|---|---|
| complex | 68 | 0 | 0 | 0 | 0 |
| bytes_bytearray | 73 | 0 | 0 | 0 | 0 |
| call_unpacking | 12 | 0 | 0 | 0 | 0 |
| async_coroutines | 10 | 0 | 0 | 0 | 0 |
| yield_from | 6 | 0 | 0 | 0 | 0 |
| context_managers | 4 | 0 | 0 | 0 | 0 |
| pattern_matching_full | 6 | 0 | 0 | 0 | 0 |
| object_model_advanced | 7 | 0 | 0 | 0 | 0 |
| advanced_decorators | 5 | 0 | 0 | 0 | 0 |
| advanced_exceptions | 5 | 0 | 0 | 0 | 0 |
| import_system | 6 | 0 | 0 | 0 | 0 |
| stdlib_tier1 | 19 | 0 | 0 | 0 | 0 |

## Divergencias y timeouts (0)

Ninguna: no hay codigo nativo que corra y discrepe del oracle en este corpus.

## Casos DIVERGENT (fuente)

