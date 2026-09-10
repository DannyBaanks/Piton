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
| Clases | PASS | `tests/test_phase5.py` + `test_phase10_linux.py` | OBJECT_MODEL_RICH_V1 PASS 2026-09-09 (Win 16 + Linux 11): MRO C3 con bases múltiples, dispatch de métodos por MRO en ambos backends, `super().método(...)` (cero args, resolución estática tras la clase actual) en cadenas de herencia y constructores, y llamadas a métodos sobre `self` (tipo estático de `self` propagado). Fail-closed: conflicto C3, `super()` fuera de método, `super().attr` como valor, atributo inexistente en MRO. Quedan (PARTIAL): cooperativo en diamante con `self` de tipo subtipo (difiere de CPython), `super(X, obj)`, metaclasses |
| Descriptors | PASS | `tests/test_phase5.py` + `test_phase10_linux.py` | DESCRIPTORS_V1 PASS 2026-09-09 (Win 12 + Linux 9): data-descriptor `property` estático — `@property` (getter), `@<name>.setter`, `@<name>.deleter` sobre métodos de clases nativas. Routing de `get_attr`/`set_attr`/`del_attr` a getter/setter/deleter resuelto por MRO, símbolos `{Class}__{prop}` (+`__setter`/`__deleter`), names de property NO registrados como métodos (`obj.x()` fail-closed). Fail-closed espejo CPython: `property 'x' of 'P' object has no setter`/`has no deleter`, `not a method`, `requires the property getter`, `only @property`, `duplicate`, `is not a property`. KNOWN_GAP: clases descriptor de usuario (`__get__`/`__set__`/`__set_name__`) |
| Imports | PASS | `tests/test_phase5.py` + `test_phase10_linux.py` | IMPORT_PACKAGE_V1 + MODULE_METADATA_V1 + IMPORT_RELATIVE_V1 + IMPORT_STAR_V1 + IMPORT_CYCLIC_V1 PASS 2026-09-09 (Win 22 + Linux 10): paquetes con `__init__.piton`, `pkg.fn()` via module-attr, `desde pkg importar fn`, submódulos `desde pkg.sub importar fn`, `__name__`/`__package__`/`__file__`, `sys.modules["x"]` byte-idénticos vs CPython; más `importar pkg.sub` (top bound; `como P` → módulo más profundo), relative `desde . importar x`/`desde .mod importar f`, cadenas `pkg.sub.fn()`, `desde pkg importar *` (funciones públicas; `_privada` excluida) y ciclos de imports con orden de inicialización CPython (scope de nombres por módulo en MIR; from-import vs módulo parcial → ImportError espejo fail-closed; `importar X` en ciclo = no-op). `desde ..` >1 nivel y cache |
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
