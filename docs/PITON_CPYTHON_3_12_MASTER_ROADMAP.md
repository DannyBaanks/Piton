# PITÓN — CPython 3.12 Master Roadmap

> **Autoridad de trabajo.** Un agente sin contexto debe poder leer este archivo,
> tomar el siguiente gate y avanzar test por test. Complementa (no reemplaza)
> `ROADMAP.md` (historial de fases), `PARITY_DEFINITION.md`,
> `CPYTHON_PARITY_DEPENDENCY_DAG.md`, `FEATURE_STATUS_MATRIX.md` y
> `FRESH_SESSION_HANDOFF.md`.

Modelo: `ROADMAP = AUTORIDAD · TESTS = JUEZ · ARTEFACTOS = EVIDENCIA`.

---

## 1. Resumen ejecutivo

PITÓN traduce español de México → semántica CPython 3.12 → x86-64 nativo
(Windows PE y Linux ELF) que corre sin CPython. Hoy, el **subset nativo
declarado** tiene paridad en ambos backends (117/117 tests, 15 gates PASS).
Este roadmap descompone el camino hacia **CPython 3.12 language parity** en
gates pequeños, un gate = una afirmación acotada = un corpus = un veredicto.
`FULL_PARITY` está retirado como término; ver `PARITY_DEFINITION.md`.

## 2. Estado verificado actual (baseline)

- Commit `fa071bf`; **117/117 tests** (85 Windows + 32 Linux).
- Backends Windows y Linux ejecutan el subset rico byte-idéntico vs CPython
  3.12.4: floats, list/tuple/dict/set, objetos, herencia (simple y multinivel),
  excepciones (raise/catch/finally), stdlib (abs/min/max/sum/type/len),
  `math.sqrt`, augmented assign, while anidado, ternario, multifunción.
- Ejecutables PE/ELF sin CPython ni libc (Linux freestanding).
- Linux: ejecución en entorno vacío, chroot vacío y QEMU como único `/init`.

## 3. Terminología

- **Gate**: unidad mínima de trabajo con una afirmación acotada y veredicto.
- **Subset nativo declarado**: lo que el backend nativo afirma soportar hoy.
- **Paridad**: igualdad de comportamiento observable declarado vs oracle, no
  identidad interna.
- Estados de gate: `PASS`, `PARTIAL`, `NOT_DEMONSTRATED`, `BLOCKED`, `FUTURE`.

## 4. Definición de paridad

Ver `PARITY_DEFINITION.md`. Gates de alto nivel: `PITON_NATIVE_SUBSET_PARITY`,
`CPYTHON_3_12_LANGUAGE_PARITY`, `CPYTHON_3_12_STDLIB_PARITY`,
`CPYTHON_3_12_RUNTIME_PARITY`, `CPYTHON_3_12_PLATFORM_PARITY`,
`CPYTHON_3_12_COMPLETE_PARITY`. `FULL_PARITY` queda retirado.

## 5. Arquitectura actual

```
.piton → lexer/CST → AST → HIR → MIR → backends
                                        ├─ x86.py   (Windows PE, NASM + MinGW)
                                        └─ linux_x86.py (ELF freestanding, gcc via WSL)
runtime: native_runtime.c (Windows), runtime C inline (Linux)
```

## 6. Autoridad de tests

`piton.native_differential.compare_native_to_cpython` compara exit code +
stdout + stderr. Oracle CPython 3.12.4. Reglas de normalización documentadas
en cada corpus; sin normalización oculta.

## 7. Grafo de dependencias

Ver `CPYTHON_PARITY_DEPENDENCY_DAG.md`. Resumen de cadenas críticas:
`FRAME_MODEL → CLOSURES → GENERATORS → COROUTINES`;
`OBJECT_PROTOCOL → DESCRIPTORS → METACLASSES`;
`IMPORT_CORE → PACKAGES → CYCLIC`.

## 8. Hitos ordenados

| Hito | Contenido | Gate de salida |
|---|---|---|
| M0 | Línea base verificada actual | `PITON_NATIVE_SUBSET_PARITY = PASS` |
| M1 | Modelo completo de funciones/frames | `FRAME_MODEL_V1 = PASS` |
| M2 | Paridad cross-platform del subset nativo | `PITON_NATIVE_SUBSET_PARITY` en Win+Linux |
| M3 | Object model rico (MRO/super/descriptors/properties) | `OBJECT_MODEL_RICH_V1 = PASS` |
| M4 | Closures completas (cells mutables, escape) | `CLOSURES_COMPLETE_V1 = PASS` |
| M5 | Semántica de generadores | `GENERATORS_COMPLETE_V1 = PASS` |
| M6 | Excepciones avanzadas (re-raise, `from`, custom, BaseException) | `EXCEPTIONS_ADVANCED_V1 = PASS` |
| M7 | Imports/packages (init, relative, cyclic, cache, sys.modules) | `IMPORT_SYSTEM_V2 = PASS` |
| M8 | Async language (coroutines reales, async for/with/gen) | `ASYNC_LANG_V1 = PASS` |
| M9 | Dinamismo (eval/exec/compile/introspection) | `DYNAMIC_LANG_V1 = PASS` |
| M10 | Builtins core + stdlib Tier 1 | `STDLIB_TIER1_V1 = PASS` |
| M11 | IO/OS parity | `IO_OS_PARITY_V1 = PASS` |
| M12 | Concurrencia (threading/multiprocessing según diseño) | `CONCURRENCY_V1 = PASS` |
| M13 | FFI / interop nativa | `C_FFI_V1 = PASS` |
| M14 | Paridad amplia de lenguaje CPython 3.12 | `CPYTHON_3_12_LANGUAGE_PARITY = PASS` |
| M15 | Stdlib declarada | `CPYTHON_3_12_STDLIB_PARITY = PASS` |
| M16 | Paridad completa objetivo | `CPYTHON_3_12_COMPLETE_PARITY = PASS` |

## 9. Esquema de un gate

Todo gate declara: GATE_ID · TITLE · CATEGORY · CURRENT_STATUS · PURPOSE ·
CPYTHON_BEHAVIOR · PITON_CURRENT_BEHAVIOR · KNOWN_GAP · DEPENDENCIES ·
UNLOCKS · IMPLEMENTATION_SURFACES · DO_NOT_TOUCH · REFERENCE_ORACLE ·
POSITIVE_TESTS · NEGATIVE_TESTS · DIFFERENTIAL_TESTS · EDGE_CASES ·
PLATFORM_TESTS · CLEAN_MACHINE_TEST · EXPECTED_FAILURES_BEFORE_IMPLEMENTATION ·
PASS_CRITERIA · EVIDENCE_TO_SAVE · DASHBOARD_UPDATE · DOCUMENTATION_UPDATE ·
ESTIMATED_COMPLEXITY (LOW/MEDIUM/HIGH/ARCHITECTURAL).

## 10. Windows/Linux

Todo feature cross-platform define `X_WINDOWS`, `X_LINUX` y la paridad
`X_CROSS_PLATFORM_PARITY`, o declara alcance explícito Windows-only/Linux-only.
Linux debe correr el MISMO corpus semántico que Windows para gates
cross-platform.

## 11–22. Roadmaps por subsistema

Los bloques completos de gates por subsistema viven aquí abajo. Se listan los
gates de mayor prioridad con el esquema completo; el resto se declara con su
afirmación y criterio (los agentes pueden expandirlos siguiendo el esquema de la
§9 antes de implementar).

### 11. Runtime (M1, M5, M8)

`FRAME_MODEL_V1` — Base de closures y generadores.
- **CURRENT_STATUS**: PARTIAL (frames presentes en bootstrap `call_runtime`).
- **PURPOSE**: frames con params, locals, cells y control no local.
- **KNOWN_GAP**: células mutables; escape; recursion no probada nativamente.
- **DEPENDENCIES**: ninguno.
- **UNLOCKS**: `CLOSURES_COMPLETE_V1`, `GENERATOR_SUSPEND_FRAME_V1`,
  `COROUTINE_V1`.
- **IMPLEMENTATION_SURFACES**: `piton/mir.py`, `piton/x86.py`,
  `piton/linux_x86.py`, runtime C.
- **DO_NOT_TOUCH**: la paridad escalar ya demostrada.
- **POSITIVE**: closures que capturan y devuelven; recursion.
- **NEGATIVE**: closure escapada → NativeBuildError (regresión existente).
- **PASS**: corpus diferencial de closures no escapadas y recursion, Win+Linux.
- **COMPLEXITY**: ARCHITECTURAL (afecta a generadores/coroutines).

`GENERATOR_SUSPEND_FRAME_V1` — frames suspendidos en yield.
- **CURRENT_STATUS**: NOT_DEMONSTRATED (solo subset finito inline).
- **DEPENDENCIES**: `FRAME_MODEL_V1`.
- **UNLOCKS**: `GENERATOR_SEND_V1`, `COROUTINE_V1`.
- **PASS**: `next()`/`yield` con estado preservado.
- **COMPLEXITY**: HIGH.

`COROUTINE_V1` — coroutine objects y await con suspensión real.
- **DEPENDENCIES**: `GENERATOR_SUSPEND_FRAME_V1`.
- **COMPLEXITY**: HIGH.

### 12. Object model (M3)

`OBJECT_MODEL_RICH_V1` — MRO + super + attribute lookup dinámico.
- **CURRENT_STATUS**: PARTIAL (herencia lineal demostrada; MRO no).
- **DEPENDENCIES**: `OBJECT_PROTOCOL` (PASS).
- **UNLOCKS**: `DESCRIPTORS_V1`, `METACLASSES_V1`.
- **PASS**: diamonds, super cooperativo, shadowing.
- **COMPLEXITY**: HIGH.

`DESCRIPTORS_V1` — `__get__/__set__/__delete__/__set_name__`.
- **DEPENDENCIES**: `OBJECT_MODEL_RICH_V1`.
- **COMPLEXITY**: HIGH.

### 13. Funciones (M1, M4)

`FUNCTION_DEFAULTS_V1` — valores por defecto constantes en params.
- **CURRENT_STATUS**: **PASS** (8 tests, Win+Linux, byte-idéntico vs CPython).
- **IMPLEMENTATION**: defaults capturados en `MIRFunction.defaults`; rellenados
  en el call site por el emisor (caller sabe cuántos args pasa).
- **KNOWN_GAP**: defaults no-constante rechazados (fail-closed); string params
  aún no comparables (limitación de type-tracking, ortogonal).
- **NEXT**: `FUNCTION_KEYWORD_ARGS_V1`.

`FUNCTION_ARGS_V1` — kwargs, defaults, `*args`/`**kwargs`, keyword-only,
positional-only.
- **CURRENT_STATUS**: PARTIAL (defaults `FUNCTION_DEFAULTS_V1` cerrado; resto abierto).
- **COMPLEXITY**: MEDIUM.

`CLOSURES_COMPLETE_V1` — cells mutables y escape.
- **DEPENDENCIES**: `FRAME_MODEL_V1`.
- **COMPLEXITY**: HIGH.

### 14. Generadores (M5)

`GENERATOR_SEND_V1`, `GENERATOR_THROW_V1`, `GENERATOR_CLOSE_V1`,
`YIELD_FROM_V1`, `GENERATOREXIT_V1`. Todos `NOT_DEMONSTRATED`,
dependen de `GENERATOR_SUSPEND_FRAME_V1`. COMPLEXITY: HIGH cada uno.

### 15. Excepciones (M6)

`EXCEPTIONS_ADVANCED_V1` — re-raise, `from`/`__cause__`, custom exceptions,
BaseException, context, chaining.
- **CURRENT_STATUS**: PARTIAL (raise/catch/finally tipados demostrados).
- **COMPLEXITY**: MEDIUM.
Gates de detalle: `EXCEPTION_RERAISE_V1`, `EXCEPTION_FROM_V1`,
`EXCEPTION_CUSTOM_V1`, `BASE_EXCEPTION_V1`, `EXCEPTION_CHAIN_V1`.

### 16. Imports (M7)

`IMPORT_PACKAGE_V1` — paquetes con `__init__` y `__path__`.
- **CURRENT_STATUS**: NOT_DEMONSTRATED (solo hermanos + from-import).
- **COMPLEXITY**: MEDIUM.
`IMPORT_RELATIVE_V1`, `IMPORT_STAR_V1`, `IMPORT_CYCLIC_V1`,
`MODULE_METADATA_V1` (`__name__`/`__file__`/`__package__`/`sys.modules`).

### 17. Async (M8)

`ASYNC_FOR_V1`, `ASYNC_WITH_V1`, `ASYNC_GENERATOR_V1`, `ASYNC_TASK_V1`,
`ASYNC_CANCEL_V1`. Dependen de `COROUTINE_V1` + scheduler. Separar semántica
de lenguaje de stdlib asyncio.

### 18. Dinamismo (M9)

`EXPLICIT_DYNAMIC_COMPILE_V1` (eval_piton/exec_piton/compile_piton) —
bootstrap existe; ejecución nativa ausente. COMPLEXITY: HIGH.
`INTROSPECTION_V1` (type/isinstance/issubclass/getattr/setattr/hasattr/dir).

### 19. Stdlib (M10, M15)

Política por módulo: NATIVE / REIMPLEMENTED / BRIDGED / COMPAT_LAYER / FUTURE /
OUT_OF_SCOPE. Matriz completa en `STDLIB_PARITY_MATRIX.md` (a crear).
Prioridad Tier 1: math, sys, os, time, json, collections, io.

### 20. IO/OS (M11)

`FILE_IO_V1` (text/binary/buffering/encoding), `OS_PROCESS_V1`,
`SUBPROCESS_V1`, `ENV_ARGS_V1` (argv/environ/exit).

### 21. Concurrencia (M12)

`THREADING_V1`, `LOCKS_V1`, `QUEUES_V1`, `MULTIPROCESSING_V1`
(`WINDOWS_SPAWN` es hueco explícito). COMPLEXITY: ARCHITECTURAL.

### 22. FFI (M13)

`C_FFI_V1` — interop con librerías nativas; separado de compatibilidad
C-extension CPython (no-goal de lenguaje).

## 23. Optimización

Capa posterior a paridad. `OPT_LEVEL_EQUIVALENCE`, `MISCOMPILATION_CORPUS=0`,
`PERF_BASELINE_RECORDED`. Cada optimización debe poder desactivarse y
`--opt=0`/`--opt=2` deben conservar semántica. **Rendimiento ≠ semántica**:
un resultado lento pero correcto puede pasar gates semánticos.

## 24. Corpus de integración

Progresión: UNIT PROPERTY → PAIRWISE → SUBSYSTEM → INTEGRATION → REAL PROGRAM.
Niveles L0..L4. Cada nivel declara las superficies de las que depende.

## 25. Protocolo de sesión nueva

Ver `FRESH_SESSION_HANDOFF.md`.

## 26. Evidencia / recibos

Formato máquina-consistente por gate:

```json
{
  "gate": "GATE_ID",
  "oracle": "CPython 3.12.4",
  "platform": "win32 | linux",
  "tests": {"passed": N, "failed": N},
  "commands": ["..."],
  "artifacts": ["..."],
  "commit": "...",
  "verdict": "PASS"
}
```

Las claims deben reconstruirse desde los recibos.

## 27. Bloqueadores actuales

- Ninguno bloquea M1. Los gates de alto riesgo (frame model, GC, object-layout,
  ABI) exigen revisión arquitectónica antes de implementarse.

## 28. Futuros / no-goals

- layout binario de PyObject; bytecode/.pyc; C API de CPython como requisito de
  lenguaje; refcounting/GC timing exacto; introspección interna de CPython;
  self-hosting (opcional, mucho después). Ver `PARITY_DEFINITION.md`.

## 29. Definición de cierre

`CPYTHON_3_12_COMPLETE_PARITY = PASS` solo cuando TODAS las superficies
aplicables de la composición definida en `PARITY_DEFINITION.md` §3 cierren,
con sus tests de integración. Nunca por suma automática de partes.

## 30. Apéndice — primeros 10 gates recomendados (orden de trabajo)

**Completado:** `FUNCTION_DEFAULTS_V1` (defaults constantes, Win+Linux PASS).

1. `FUNCTION_KEYWORD_ARGS_V1` (MEDIUM) — kwargs por nombre; requiere resolver la
   firma del callee en el call site.
2. `FRAME_MODEL_V1` (ARCHITECTURAL) — base de closures/generadores/coroutines.
3. `IMPORT_PACKAGE_V1` (MEDIUM) — paquetes + `__init__`.
4. `MODULE_METADATA_V1` (MEDIUM) — `__name__`/`__file__`/`sys.modules`.
5. `EXCEPTION_CUSTOM_V1` (LOW) — excepciones definidas por usuario.
6. `EXCEPTION_RERAISE_V1` (LOW) — re-raise bare.
7. `OBJECT_MODEL_RICH_V1` (HIGH) — MRO + super.
8. `GENERATOR_SUSPEND_FRAME_V1` (HIGH) — frames suspendidos (depende de 2).
9. `CLOSURES_COMPLETE_V1` (HIGH) — cells mutables y escape (depende de 2).
10. `DESCRIPTORS_V1` (HIGH) — descriptors (depende de 7).

## 31. Gates paralelizables

`PARALLEL_SAFE = YES` y archivos de riesgo compartido:

| Agente A | Agente B | Independientes |
|---|---|---|
| `FUNCTION_ARGS_V1` | `IMPORT_PACKAGE_V1` | Sí (distintas superficies) |
| `EXCEPTION_CUSTOM_V1` | `MODULE_METADATA_V1` | Sí |
| `OBJECT_MODEL_RICH_V1` | `FUNCTION_ARGS_V1` | Sí (bajo riesgo, revisar) |

`PARALLEL_SAFE = NO` (colisionan):
- `FRAME_MODEL_V1` vs `GENERATOR_SUSPEND_FRAME_V1` (mismo runtime de frames).
- `OBJECT_MODEL_RICH_V1` vs `DESCRIPTORS_V1` (mismo attribute lookup).

## 32. Gates arquitectónicos

Exigen `ARCHITECTURE_REVIEW_REQUIRED` y STOP antes de implementar:
`FRAME_MODEL_V1`, `GENERATOR_SUSPEND_FRAME_V1`, `COROUTINE_V1`,
`OBJECT_MODEL_RICH_V1`, `DESCRIPTORS_V1`, `METACLASSES_V1`, `THREADING_V1`,
`MULTIPROCESSING_V1`, y cualquier gate que toque ABI/calling-convention/GC.

## 33. Stop conditions

Si el gate exige cambio de frame model, GC, object-layout, ABI o
calling-convention → marca `ARCHITECTURE_REVIEW_REQUIRED` y detente. No
reconstruyas el runtime para cerrar un test pequeño.

## 34. Conteo actual y veredictos (baseline)

Ver `FEATURE_STATUS_MATRIX.md`. 117/117 tests; 15 gates PASS en el dashboard
(incl. `FULL_PARITY`, retirado como término en `PARITY_DEFINITION.md`).
Features PARTIAL: functions, closures, generators, descriptors, dynamic_code,
introspection, ffi. NOT_DEMONSTRATED: metaclasses, multiprocessing.