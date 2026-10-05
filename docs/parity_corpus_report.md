# Parity corpus report (enumerativo)

- backend medido: **linux_x86 (Linux native)** (instrumento: `tools/parity_corpus.py`)
- oracle: CPython 3.12.3
- casos: **1220** | EQUIV **99.9%** | comportamiento honesto (EQUIV+RAISE_EQ+FAIL_CLOSED) **99.9%** | DIVERGENT **0.0%**

Este reporte NO es un gate. Mide el subconjecto declarado en `NATIVE_COMPATIBILITY.md` mediante matrices enumeradas, no casos elegidos a mano.

## Buckets

| bucket | n |
|---|---|
| DIVERGENT_CRASH | 0 |
| DIVERGENT_MISMATCH | 0 |
| TIMEOUT | 1 |
| FAIL_CLOSED | 0 |
| RAISE_EQ | 0 |
| EQUIV | 1219 |

## Por area

| area | EQUIV | RAISE_EQ | FAIL_CLOSED | DIVERGENT | TIMEOUT |
|---|---|---|---|---|---|
| literals | 16 | 0 | 0 | 0 | 0 |
| arithmetic | 133 | 0 | 0 | 0 | 0 |
| associativity | 25 | 0 | 0 | 0 | 0 |
| branch_truthiness | 19 | 0 | 0 | 0 | 0 |
| comparisons | 111 | 0 | 0 | 0 | 0 |
| truthiness | 75 | 0 | 0 | 0 | 0 |
| ranges | 44 | 0 | 0 | 0 | 0 |
| bigint | 33 | 0 | 0 | 0 | 0 |
| builtins | 82 | 0 | 0 | 0 | 0 |
| strings | 42 | 0 | 0 | 0 | 0 |
| formatting | 49 | 0 | 0 | 0 | 0 |
| slices | 394 | 0 | 0 | 0 | 0 |
| collections | 34 | 0 | 0 | 0 | 0 |
| dicts | 20 | 0 | 0 | 0 | 0 |
| sets | 9 | 0 | 0 | 0 | 0 |
| comprehensions | 27 | 0 | 0 | 0 | 0 |
| control_flow | 20 | 0 | 0 | 0 | 0 |
| functions | 21 | 0 | 0 | 0 | 0 |
| exceptions | 21 | 0 | 0 | 0 | 0 |
| classes | 21 | 0 | 0 | 0 | 0 |
| generators | 7 | 0 | 0 | 0 | 1 |
| controls_closed | 16 | 0 | 0 | 0 | 0 |

## Divergencias y timeouts (1)

| area | caso | native rc | oracle rc | detalle |
|---|---|---|---|---|
| generators | class-iter-self | - | - | oracle did not terminate (case is a bad corpus entry) |

## Casos DIVERGENT (fuente)

```piton
clase G:
    funcion __iter__(self):
        devolver self
    funcion __next__(self):
        devolver 1
imprimir(lista(G()))
```
