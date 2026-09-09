# Compatibilidad nativa de Pitón

Oracle: CPython 3.12.4. Objetivo: igualdad observable del programa para el
alcance declarado, comparando código de salida, stdout, stderr y efectos
externos controlados. CPython puede participar en el compilador y en las
pruebas como oracle, pero no en el ejecutable generado.

## Estado actual

El milestone reproducible del subconjunto está definido en
`NATIVE_SUBSET_1_0.md`. En este host, su recibo Windows automatizado da `PASS`;
una ejecución dentro de una VM Windows limpia permanece `NOT_DEMONSTRATED`.

| Área | Estado nativo | Evidencia | Brecha principal |
|---|---|---|---|
| Literales int/bool/Nada/string/float | PARTIAL | `tests/test_phase5.py` | float repr completos |
| Aritmética entera | PARTIAL | `tests/test_phase5.py` | Overflow a bigint y zero division |
| Floats | PARTIAL | `tests/test_phase5.py` | división, NaN y repr shortest-roundtrip |
| Bigints | PARTIAL | `tests/test_phase5.py` | aritmética por limbs |
| Comparaciones | PARTIAL | `tests/test_phase5.py` | Protocolo dinámico rico |
| Branch/while | PARTIAL | `tests/test_phase5.py` | `for`, iteradores y control no local |
| Funciones | PARTIAL | `tests/test_phase5.py` | keywords, closures y excepciones |
| Strings | PARTIAL | `tests/test_phase5.py` | Unicode completo verificado byte-a-byte contra CPython; lifetime de heap pendiente |
| Colecciones int | PARTIAL | `tests/test_phase5.py` | nesting, tipos dinámicos, mutación y errores |
| Heap de colecciones | PARTIAL | contador nativo + stress diferencial | ciclos y objetos heterogéneos |
| Excepciones | PARTIAL | `tests/test_phase5.py` | unwind, finally y objetos de excepción |
| Closures | PASS | `tests/test_phase5.py` + `test_phase10_linux.py` | FRAME_MODEL_V1 PASS (Win 6 + Linux 4): cells para captures, recursion nativa, closure recursivo, anidadas con capture transitivo. Escape y cells mutables → `CLOSURES_COMPLETE_V1` |
| Generators | PARTIAL | `tests/test_phase5.py` | frames suspendidos, send/throw/yield from |
| Clases | PARTIAL | `tests/test_phase5.py` | herencia, descriptors, metaclasses y fields heterogéneos |
| Imports | PARTIAL | `tests/test_phase5.py` | paquetes, ciclos, from-import y estado de módulo |
| Async | PARTIAL | `tests/test_phase5.py` | suspensión, cancellation y scheduler |
| Stdlib/FFI | PARTIAL | `tests/test_phase5.py` | solo `math.sqrt`; módulos y ABI restantes |
| Linux x86-64 | PARTIAL | `tests/test_phase10_linux.py` | backend ELF escalar; runtime rico pendiente |
| Ejecución limpia | PASS | QEMU con ELF como único `/init` | ninguna dependencia dinámica ni Python |

## Regla de avance

Cada feature nueva entra primero al corpus de `piton.native_differential`.
Una operación sin semántica nativa correcta debe producir `NativeBuildError`;
nunca se permite emitir código aproximado y contarlo como compatibilidad.

El gate limpio usa un ELF freestanding estático, syscalls Linux directos y un
initramfs que contiene únicamente `/init`. QEMU arranca ese archivo como todo
el userspace de la VM; no se usan Python, libc, shell ni archivos del host.
