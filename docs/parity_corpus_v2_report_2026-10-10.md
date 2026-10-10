# Parity corpus report v2 (enumerativo)

- backend medido: **linux_x86 (Linux native)** (instrumento: `tools/parity_corpus_v2.py`)
- oracle: CPython 3.12.3
- casos: **218** | EQUIV **83.9%** | comportamiento honesto (EQUIV+RAISE_EQ+FAIL_CLOSED) **98.2%** | DIVERGENT **1.8%**

Este reporte NO es un gate. Mide el subconjunto declarado en `NATIVE_COMPATIBILITY.md` mediante matrices enumeradas, no casos elegidos a mano.

## Buckets

| bucket | n |
|---|---|
| DIVERGENT_CRASH | 0 |
| DIVERGENT_MISMATCH | 4 |
| TIMEOUT | 0 |
| FAIL_CLOSED | 31 |
| RAISE_EQ | 0 |
| EQUIV | 183 |

## Por area

| area | EQUIV | RAISE_EQ | FAIL_CLOSED | DIVERGENT | TIMEOUT |
|---|---|---|---|---|---|
| complex | 68 | 0 | 0 | 0 | 0 |
| bytes_bytearray | 63 | 0 | 8 | 0 | 0 |
| call_unpacking | 6 | 0 | 2 | 4 | 0 |
| async_coroutines | 0 | 0 | 10 | 0 | 0 |
| yield_from | 3 | 0 | 1 | 0 | 0 |
| context_managers | 4 | 0 | 0 | 0 | 0 |
| pattern_matching_full | 5 | 0 | 1 | 0 | 0 |
| object_model_advanced | 6 | 0 | 1 | 0 | 0 |
| advanced_decorators | 4 | 0 | 1 | 0 | 0 |
| advanced_exceptions | 4 | 0 | 1 | 0 | 0 |
| import_system | 4 | 0 | 4 | 0 | 0 |
| stdlib_tier1 | 16 | 0 | 2 | 0 | 0 |

## Divergencias y timeouts (4)

| area | caso | native rc | oracle rc | detalle |
|---|---|---|---|---|
| call_unpacking | star-[1, 2] | 0 | 1 | native rc=0 out=[1, 2] err= // oracle rc=1 out= err=Traceback (most recent call last): /   File "<string>", line 2, in <module> /   File "<string>", line 1, in f / TypeError: unsupported operand type( |
| call_unpacking | star-(1, 2) | 0 | 1 | native rc=0 out=(1, 2) err= // oracle rc=1 out= err=Traceback (most recent call last): /   File "<string>", line 2, in <module> /   File "<string>", line 1, in f / TypeError: unsupported operand type( |
| call_unpacking | star-{'a': 1} | 0 | 1 | native rc=0 out={'a': 1} err= // oracle rc=1 out= err=Traceback (most recent call last): /   File "<string>", line 2, in <module> /   File "<string>", line 1, in f / TypeError: unsupported operand typ |
| call_unpacking | star-[1, 2, 3] | 0 | 1 | native rc=0 out=[1, 2, 3] err= // oracle rc=1 out= err=Traceback (most recent call last): /   File "<string>", line 2, in <module> /   File "<string>", line 1, in f / TypeError: unsupported operand ty |

## Casos DIVERGENT (fuente)

```piton
funcion f(*xs): devolver suma(xs)
imprimir(f([1, 2]))
```
```piton
funcion f(*xs): devolver suma(xs)
imprimir(f((1, 2)))
```
```piton
funcion f(*xs): devolver suma(xs)
imprimir(f({'a': 1}))
```
```piton
funcion f(*xs): devolver suma(xs)
imprimir(f([1, 2, 3]))
```
