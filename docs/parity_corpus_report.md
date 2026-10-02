# Parity corpus report (enumerativo)

- backend medido: **linux_x86 (Linux native)** (instrumento: `tools/parity_corpus.py`)
- oracle: CPython 3.12.3
- casos: **1224** | EQUIV **87.5%** | comportamiento honesto (EQUIV+RAISE_EQ+FAIL_CLOSED) **99.6%** | DIVERGENT **0.4%**

Este reporte NO es un gate. Mide el subconjecto declarado en `NATIVE_COMPATIBILITY.md` mediante matrices enumeradas, no casos elegidos a mano.

## Buckets

| bucket | n |
|---|---|
| DIVERGENT_CRASH | 0 |
| DIVERGENT_MISMATCH | 5 |
| TIMEOUT | 0 |
| FAIL_CLOSED | 147 |
| RAISE_EQ | 1 |
| EQUIV | 1071 |

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
| builtins | 69 | 0 | 14 | 3 | 0 |
| strings | 20 | 0 | 22 | 0 | 0 |
| formatting | 32 | 0 | 17 | 0 | 0 |
| slices | 389 | 0 | 5 | 0 | 0 |
| collections | 27 | 0 | 6 | 1 | 0 |
| dicts | 8 | 0 | 11 | 1 | 0 |
| sets | 7 | 0 | 2 | 0 | 0 |
| comprehensions | 18 | 0 | 9 | 0 | 0 |
| control_flow | 16 | 0 | 4 | 0 | 0 |
| functions | 16 | 0 | 5 | 0 | 0 |
| exceptions | 18 | 1 | 2 | 0 | 0 |
| classes | 14 | 0 | 7 | 0 | 0 |
| generators | 2 | 0 | 6 | 0 | 0 |
| controls_closed | 2 | 0 | 14 | 0 | 0 |

## Divergencias y timeouts (5)

| area | caso | native rc | oracle rc | detalle |
|---|---|---|---|---|
| builtins | min2 | 1 | 0 | native rc=1 out= err=TypeError: '<>' not supported between instances // oracle rc=0 out=1.5 err= |
| builtins | min3 | 1 | 0 | native rc=1 out= err=TypeError: '<>' not supported between instances // oracle rc=0 out=[1] err= |
| builtins | max2 | 1 | 0 | native rc=1 out= err=TypeError: '<>' not supported between instances // oracle rc=0 out=2 err= |
| collections | for-enumerate | 0 | 0 | native rc=0 out=0 / 4206670 / 1 / 4206672 err= // oracle rc=0 out=0 / a / 1 / b err= |
| dicts | get | 1 | 0 | native rc=1 out=1 err=KeyError // oracle rc=0 out=1 / None err= |

## Casos DIVERGENT (fuente)

```piton
imprimir(min([1.5, 2]))
```
```piton
imprimir(min([[1], [2]]))
```
```piton
imprimir(max([1.5, 2]))
```
```piton
para i, v en enumerar(['a', 'b']):
    imprimir(i)
    imprimir(v)
```
```piton
d = {'a': 1}
imprimir(d.get('a'))
imprimir(d.get('z'))
```
