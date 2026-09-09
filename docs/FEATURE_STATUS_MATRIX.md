# PITÓN — Matriz de Estado de Features (CPython 3.12)

> Estado verificado en disco + tests. NO confiar en rótulos sin confirmar.
> Regla: si un feature es cross-platform, `Windows` y `Linux` se declaran por
> separado; un `PASS` de un solo lado no es un PASS del feature.

Estados: `PASS`, `PARTIAL`, `NOT_DEMONSTRATED`, `BLOCKED`, `FUTURE`.

## 1. Línea base verificada (2026-09-09, commit pendiente de push)

- **215/215 tests** (138 Windows + 77 Linux).
- Ambos backends ejecutan el subset rico byte-idéntico vs CPython 3.12.4.
- Ejecutables PE/ELF sin CPython ni libc (freestanding Linux).
- **17 gates `PASS`** en `piton.final_dashboard` (incl. `FULL_PARITY`, que este
  roadmap retira como término; ver `PARITY_DEFINITION.md`).

## 2. Matriz de features

| Feature | Estado | Windows | Linux | Evidencia | Hueco declarado |
|---|---|---|---|---|---|
| syntax | PASS | PASS | PASS | `tests/evidence.py` | — |
| expressions | PASS | PASS | PASS | `test_phase5.py` | — |
| statements | PASS | PASS | PASS | `test_phase5.py` | — |
| functions | PASS | PASS | PASS | `test_phase5.py` | defaults, kwargs, `*args`, `**kwargs`, positional-only, keyword-only all PASS (FUNCTION_DEFAULTS_V1 + FUNCTION_KEYWORD_ARGS_V1 + FUNCTION_ARGS_V1); call-site unpacking (`f(*xs)`, `f(**d)`) rejected; bound methods still open |
| closures | PASS | PASS | PASS | `test_phase5.py` + `test_phase10_linux.py` | CLOSURES_COMPLETE_V1 PASS 2026-09-09: closure objects con cells mutables y ESCAPE permitido, dispatch por magic en runtime (`piton_closure_call6`), hasta 4 captures + 4 args (fail-closed rc=2 si se excede o argumentos incorrectos), CALL-E; Win 10 tests + Linux 9 tests; `nonlocal`→`no_local`, cells transitivas mutables (swap), callbacks de funciones planas y closures, cadenas de fábricas. Sin seguimiento estático de flujo: toda llamada no-resuelta despacha en runtime |
| exceptions | PASS | PASS | PASS | `test_phase5.py` + `test_phase10_linux.py` | EXCEPTION_CUSTOM_V1 + EXCEPTION_RERAISE_V1 PASS 2026-09-09: excepciones de usuario (`clase E(Exception):` o subclases; matching por cadena de herencia en ambos backends, hijo atrapado por ancestro custom/builtin/`Exception`), `lanzar` bare en handlers exactos (anidado despacha al envolvente). Quedan: `from`/cadenas, BaseException, binding `excepto E as x`, `excepto*`, bare re-raise desde catch-all (fail-closed hoy) |
| generators | PARTIAL | PARTIAL | NOT_DEMONSTRATED | `test_phase5.py` | frames suspendidos, send/throw/close, `yield from` |
| classes | PASS | PASS | PASS | `test_phase5.py` | MRO, super, metaclasses |
| descriptors | PARTIAL | PARTIAL | PARTIAL | `object_protocol.py` | bootstrap; native abierto |
| metaclasses | NOT_DEMONSTRATED | NOT_DEMONSTRATED | NOT_DEMONSTRATED | `ROADMAP.md` | no implementado |
| imports | PASS | PASS | PASS | `test_phase5.py` + `test_phase10_linux.py` | IMPORT_PACKAGE_V1 PASS: paquetes con `__init__.piton`, `pkg.fn()` via module-attr, `desde pkg importar fn`, submódulos `desde pkg.sub importar fn` Win+Linux byte-idénticos vs CPython (Win 5 tests, Linux 4 tests). Sigue: `importar pkg.sub`, ciclos, star, relative, cache, sys.modules (`MODULE_METADATA_V1`/`IMPORT_RELATIVE_V1`) |
| async | PASS | PASS | NOT_DEMONSTRATED | `test_phase5.py` | scheduler/concurrent, suspension real |
| stdlib | PASS | PASS | PASS | `test_phase5.py` | abs/min/max/sum/type/len/print/math.sqrt |
| dynamic_code | PARTIAL | PARTIAL | NOT_DEMONSTRATED | `runtime.py` | bootstrap APIs; ejecución nativa ausente |
| introspection | PARTIAL | PARTIAL | NOT_DEMONSTRATED | `runtime.py` | source contract only |
| multiprocessing | NOT_DEMONSTRATED | NOT_DEMONSTRATED | NOT_DEMONSTRATED | `ROADMAP.md` | Windows spawn no demostrado |
| ffi | PARTIAL | PARTIAL | NOT_DEMONSTRATED | `stdlib_runtime.py` | superficie ctypes; independencia nativa abierta |
| dynamic_runtime | PASS | PASS | PASS | `native_runtime.c` | tagged union, refcounted heap, colecciones |

## 3. Taxonomía de superficies restantes de CPython 3.12

Descomposición exhaustiva para roadmap. Cada fila es un área a convertir en
gates atómicos (un gate → una afirmación acotada → un corpus → un veredicto).

### A. Núcleo sintáctico / grammar
literales; operadores y precedencia; comprehensions; formas de asignación;
anotaciones; pattern matching; walrus (`:=`); starred expressions; unpacking;
decoradores; context managers; f-strings PEP 701.

### B. Modelo de valores
None; bool; int; bigint; float; complex; str; bytes; bytearray; memoryview;
tuple; list; dict; set; frozenset; range; slice.

### C. Semántica numérica
arithmetic; floor division; modulo y negativos; overflow; precisión arbitraria;
conversiones; comparaciones; NaN / infinitos; interacciones de hash.

### D. Object model
object; type; identity; equality; `__new__`; `__init__`; herencia simple y
múltiple; MRO; super; descriptors; properties; `__getattribute__`;
`__getattr__`; `__setattr__`; slots; metaclasses; construcción dinámica;
special-method lookup.

### E. Funciones
args posicionales; kwargs; defaults; `*args`; `**kwargs`; keyword-only;
positional-only; anotaciones; closures; cells mutables; recursión; decoradores;
bound methods; protocolo callable.

### F. Generadores
yield; frames suspendidos; next; send; throw; close; GeneratorExit;
`yield from`; StopIteration; PEP 479; introspección de generador.

### G. Iteradores / iterables
`__iter__`; `__next__`; iter(); next(); sentinel; enumerate; zip; map; filter;
reversed.

### H. Excepciones
raise; re-raise; chaining; from; modelo de traceback; finally; handlers
anidados; múltiples handlers; jerarquía; custom; BaseException vs Exception;
context; cause; ExceptionGroup / except* (si en alcance).

### I. Context managers
`__enter__`; `__exit__`; with; múltiples; supresión de excepciones;
async context managers (después).

### J. Imports
módulos; paquetes; `__init__`; relativos; aliasing; from import; star;
module cache; sys.modules; rutas; ciclos; namespace packages; import hooks.

### K. Async
coroutine objects; async def; await; suspensión/reanudación; scheduler; tasks;
event loop; async iterator; async generator; async with; cancelación;
excepciones a través de await.

Separar **semántica de lenguaje async** de **paridad stdlib asyncio**.

### L. Introspección
type; isinstance; issubclass; dir; vars; getattr; setattr; hasattr; globals;
locals; callable; comportamientos observables de inspect en alcance.

### M. Código dinámico
eval; exec; compile; imports dinámicos; code objects en alcance.

### N. Memoria / GC
modelo de alocación; lifetime; estructuras cíclicas; GC; finalizadores;
weakrefs; `__del__`; comportamiento de referencia solo donde observable.

### O. Standard library
Por TIERS (ver `STDLIB_PARITY_MATRIX.md`):
- Tier 0: builtins fundamentales.
- Tier 1: soporte core del lenguaje.
- Tier 2: módulos comunes compatibles pure-Python.
- Tier 3: módulos OS/runtime.
- Tier 4: módulos pesados de extensiones nativas.

Decidir por módulo: `NATIVE`, `REIMPLEMENTED`, `BRIDGED`, `COMPAT_LAYER`,
`FUTURE`, `OUT_OF_SCOPE`.

### P. IO
print; input; files; text; binary; buffering; encoding; paths; stdin/out/err.

### Q. OS / sistema
argv; environment; exit; filesystem; procesos; subprocess; signals; time.

### R. Concurrencia
threading; multiprocessing; async; locks; queues; semántica de procesos.

### S. FFI / extensiones
ctypes; librerías nativas; C ABI; compatibilidad con extensiones CPython
(carril separado; no es requisito de paridad de lenguaje).

### T. Plataforma
Windows x86-64; Linux x86-64; clean-machine; freestanding runtime;
dependencias dinámicas; formato ejecutable; calling conventions.