# Parity corpus report v3 (enumerativo)

- backend medido: **linux_x86 (Linux native)** (instrumento: `tools/parity_corpus_v3.py`)
- oracle: CPython 3.12.3
- casos: **234** | EQUIV **72.2%** | comportamiento honesto (EQUIV+RAISE_EQ+FAIL_CLOSED) **97.4%** | DIVERGENT **2.6%**

Este reporte NO es un gate. Mide el subconjunto declarado en `NATIVE_COMPATIBILITY.md` mediante matrices enumeradas, no casos elegidos a mano.

## Buckets

| bucket | n |
|---|---|
| DIVERGENT_CRASH | 0 |
| DIVERGENT_MISMATCH | 6 |
| TIMEOUT | 0 |
| FAIL_CLOSED | 59 |
| RAISE_EQ | 0 |
| EQUIV | 169 |

## Por area

| area | EQUIV | RAISE_EQ | FAIL_CLOSED | DIVERGENT | TIMEOUT |
|---|---|---|---|---|---|
| str_methods_more | 15 | 0 | 8 | 0 | 0 |
| dict_ops_more | 16 | 0 | 3 | 0 | 0 |
| set_ops | 25 | 0 | 3 | 0 | 0 |
| frozenset | 12 | 0 | 2 | 1 | 0 |
| memoryview | 0 | 0 | 14 | 0 | 0 |
| slice_value | 0 | 0 | 10 | 0 | 0 |
| complex_deep | 21 | 0 | 1 | 0 | 0 |
| builtins_more | 18 | 0 | 8 | 2 | 0 |
| exceptions_deep | 9 | 0 | 5 | 0 | 0 |
| numeric_semantics | 29 | 0 | 2 | 0 | 0 |
| object_protocol3 | 12 | 0 | 2 | 2 | 0 |
| callable_protocol | 12 | 0 | 1 | 1 | 0 |

## Divergencias y timeouts (6)

| area | caso | native rc | oracle rc | detalle |
|---|---|---|---|---|
| frozenset | hash-eq | 1 | 0 | native rc=1 out= err=TypeError: unhashable type: 'set:frozen' // oracle rc=0 out=True err= |
| builtins_more | sorted-key-rev | 0 | 0 | native rc=0 out=[3, 2, 1] err= // oracle rc=0 out=[1, 2, 3] err= |
| builtins_more | pow-mod | 1 | 0 | native rc=1 out= err=TypeError: pow expected at least 2 arguments, got 3 // oracle rc=0 out=24 err= |
| object_protocol3 | getattribute | 1 | 0 | native rc=1 out= err=AttributeError // oracle rc=0 out=42 err= |
| object_protocol3 | hash-eq | 1 | 0 | native rc=1 out= err=TypeError: unhashable type: 'object:P' // oracle rc=0 out=True err= |
| callable_protocol | mutual | 0 | 0 | native rc=0 out=1 err= // oracle rc=0 out=True err= |

## Casos DIVERGENT (fuente)

```piton
imprimir(hash(frozenset([1, 2])) == hash(frozenset([2, 1])))
```
```piton
imprimir(ordenar([3, 1, 2], key=lambda x: -x, reversa=Verdadero))
```
```piton
imprimir(pow(2, 10, 100))
```
```piton
clase P:
    funcion __getattribute__(self, n):
        devolver 42
imprimir(P().x)
```
```piton
clase P:
    funcion __init__(self, x): self.x = x
    funcion __hash__(self):
        devolver self.x
imprimir(hash(P(3)) == 3)
```
```piton
funcion par(n):
    si n == 0:
        devolver Verdadero
    devolver impar(n - 1)
funcion impar(n):
    si n == 0:
        devolver Falso
    devolver par(n - 1)
imprimir(par(4))
```
