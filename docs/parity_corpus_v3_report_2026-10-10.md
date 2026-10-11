# Parity corpus report v3 (enumerativo)

- backend medido: **linux_x86 (Linux native)** (instrumento: `tools/parity_corpus_v3.py`)
- oracle: CPython 3.12.3
- casos: **234** | EQUIV **68.8%** | comportamiento honesto (EQUIV+RAISE_EQ+FAIL_CLOSED) **94.0%** | DIVERGENT **6.0%**

Este reporte NO es un gate. Mide el subconjunto declarado en `NATIVE_COMPATIBILITY.md` mediante matrices enumeradas, no casos elegidos a mano.

## Buckets

| bucket | n |
|---|---|
| DIVERGENT_CRASH | 0 |
| DIVERGENT_MISMATCH | 14 |
| TIMEOUT | 0 |
| FAIL_CLOSED | 59 |
| RAISE_EQ | 0 |
| EQUIV | 161 |

## Por area

| area | EQUIV | RAISE_EQ | FAIL_CLOSED | DIVERGENT | TIMEOUT |
|---|---|---|---|---|---|
| str_methods_more | 15 | 0 | 8 | 0 | 0 |
| dict_ops_more | 15 | 0 | 3 | 1 | 0 |
| set_ops | 23 | 0 | 3 | 2 | 0 |
| frozenset | 12 | 0 | 2 | 1 | 0 |
| memoryview | 0 | 0 | 14 | 0 | 0 |
| slice_value | 0 | 0 | 10 | 0 | 0 |
| complex_deep | 21 | 0 | 1 | 0 | 0 |
| builtins_more | 17 | 0 | 8 | 3 | 0 |
| exceptions_deep | 8 | 0 | 5 | 1 | 0 |
| numeric_semantics | 29 | 0 | 2 | 0 | 0 |
| object_protocol3 | 9 | 0 | 2 | 5 | 0 |
| callable_protocol | 12 | 0 | 1 | 1 | 0 |

## Divergencias y timeouts (14)

| area | caso | native rc | oracle rc | detalle |
|---|---|---|---|---|
| dict_ops_more | eq | 0 | 0 | native rc=0 out=False err= // oracle rc=0 out=True err= |
| set_ops | pop | 0 | 0 | native rc=0 out=1 / {} err= // oracle rc=0 out=1 / set() err= |
| set_ops | clear | 0 | 0 | native rc=0 out={} err= // oracle rc=0 out=set() err= |
| frozenset | hash-eq | 1 | 0 | native rc=1 out= err=TypeError: unhashable type: 'set:frozen' // oracle rc=0 out=True err= |
| builtins_more | hash-none-err | 0 | 0 | native rc=0 out=0 err= // oracle rc=0 out=4238894112 err= |
| builtins_more | sorted-key-rev | 0 | 0 | native rc=0 out=[3, 2, 1] err= // oracle rc=0 out=[1, 2, 3] err= |
| builtins_more | pow-mod | 1 | 0 | native rc=1 out= err=TypeError: pow expected at least 2 arguments, got 3 // oracle rc=0 out=24 err= |
| exceptions_deep | base-error | 1 | 0 | native rc=1 out= err=KeyError // oracle rc=0 out=e err= |
| object_protocol3 | getattribute | 1 | 0 | native rc=1 out= err=AttributeError // oracle rc=0 out=42 err= |
| object_protocol3 | repr-str | 0 | 0 | native rc=0 out=soy P / <object> err= // oracle rc=0 out=soy P / P() err= |
| object_protocol3 | hash-eq | 1 | 0 | native rc=1 out= err=TypeError: unhashable type: 'object:P' // oracle rc=0 out=True err= |
| object_protocol3 | neg | 0 | 0 | native rc=0 out=-8410992 err= // oracle rc=0 out=1 err= |
| object_protocol3 | getitem-missing | 0 | 0 | native rc=0 out= err= // oracle rc=0 out=ok err= |
| callable_protocol | mutual | 0 | 0 | native rc=0 out=1 err= // oracle rc=0 out=True err= |

## Casos DIVERGENT (fuente)

```piton
imprimir({'a': 1} == {'a': 1})
```
```piton
s = {1}
imprimir(s.pop())
imprimir(s)
```
```piton
s = {1}
s.clear()
imprimir(s)
```
```piton
imprimir(hash(frozenset([1, 2])) == hash(frozenset([2, 1])))
```
```piton
imprimir(hash(Nada))
```
```piton
imprimir(ordenar([3, 1, 2], key=lambda x: -x, reversa=Verdadero))
```
```piton
imprimir(pow(2, 10, 100))
```
```piton
intentar:
    d = {}
    d['k']
excepto Exception:
    imprimir('e')
```
```piton
clase P:
    funcion __getattribute__(self, n):
        devolver 42
imprimir(P().x)
```
```piton
clase P:
    funcion __repr__(self):
        devolver 'P()'
    funcion __str__(self):
        devolver 'soy P'
imprimir(P())
imprimir(repr(P()))
```
```piton
clase P:
    funcion __init__(self, x): self.x = x
    funcion __hash__(self):
        devolver self.x
imprimir(hash(P(3)) == 3)
```
```piton
clase P:
    funcion __neg__(self):
        devolver 1
imprimir(-P())
```
```piton
clase P:
    funcion __getitem__(self, i):
        lanzar KeyError('k')
intentar:
    P()[1]
excepto KeyError:
    imprimir('ok')
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
