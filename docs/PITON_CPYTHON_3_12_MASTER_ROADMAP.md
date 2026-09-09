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
declarado** tiene paridad en ambos backends (215/215 tests, 17 gates PASS).
Este roadmap descompone el camino hacia **CPython 3.12 language parity** en
gates pequeños, un gate = una afirmación acotada = un corpus = un veredicto.
Estado actual: **215/215 tests, 17 gates PASS** (2026-09-09).
`FULL_PARITY` está retirado como término; ver `PARITY_DEFINITION.md`.

## 2. Estado verificado actual (baseline)

- Commit `65f825d` (closures) + excepciones custom/re-raise en curso; **215/215
  tests** (138 Windows + 77 Linux).
- Backends Windows y Linux ejecutan el subset rico byte-idéntico vs CPython
  3.12.4: floats, list/tuple/dict/set, objetos, herencia (simple y multinivel),
  closures con escape y cells mutables, excepciones (raise/catch/finally,
  custom por jerarquía, re-raise bare), stdlib (abs/min/max/sum/type/len),
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
- **CURRENT_STATUS**: **PASS** (215 tests total, Win+Linux, byte-idéntico vs CPython).
- **PURPOSE**: frames con params, locals, cells y control no local.
- **IMPLEMENTATION**: captures vía cells heap (16 bytes: valor + type tag) creados
  por `cell_new` al entry de la función definidora (wrapping del valor de params,
  cell vacía para locals) y accedidos por `cell_load`/`cell_store` en todos los
  niveles; nested functions reciben cell pointers como params leading (lambda-
  lifting); MIR `MIRFunction.cell_vars` + `_Builder.cell_params`; autocitación de
  closures registrada (`builder.closures[node.name] = (lifted, cell_vars)`) para
  recursión que repasa cells; free-loads transitivos con bubbling
  (`_nested_free_loads`) para closures anidadas que capturan del abuelo; x86 win64
  `malloc`-based, linux_x86 via `piton_alloc`.
- **KNOWN_GAP**: closure escapada y células mutables (`nonlocal`/`global`)
  rechazadas en MIR — `CLOSURES_COMPLETE_V1`.
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

`FUNCTION_KEYWORD_ARGS_V1` — keyword arguments por nombre.
- **CURRENT_STATUS**: **PASS** (13 tests, Win+Linux, byte-idéntico vs CPython).
- **IMPLEMENTATION**: resolución keyword→posición en lowering MIR usando la
  firma del callee (`function_params`/`function_defaults`); reordenamiento +
  relleno de defaults intermedios; fail-closed en keyword inesperado, arg
  requerido ausente y valor duplicado.
- **KNOWN_GAP**: `**kwargs`, keyword-only, positional-only abiertos.

`FUNCTION_ARGS_V1` — kwargs, defaults, `*args`/`**kwargs`, keyword-only,
positional-only.
- **CURRENT_STATUS**: **PASS** (158 tests total, Win+Linux, byte-idéntico vs CPython).
- **IMPLEMENTATION**: parser acepta `/` (posonly), `*` (bare kwonly marker,
  `*args`), `**kwargs`; MIR trackea `function_posonly`/`function_kwonly`/
  `function_varargs`/`function_kwargs`; `_lower_variadic_call` empaqueta
  extras posicionales en tuple y extras keyword en dict, preservando orden
  ABI: posicionales → `*args` tuple → kwonly → `**kwargs` dict (máx. 4
  parámetros ABI); `starred_args` propagado CST→HIR→MIR; fail-closed en
  unpacking (`f(*xs)`, `f(**mapping)`), keyword positional-only, keyword
  inesperado, arg requerido ausente, valor duplicado.
- **KNOWN_GAP**: call-site unpacking (`f(*xs)`, `f(**mapping)`) rechazado;
  slack de 4 parámetros ABI; más allá fail-closed.
- **COMPLEXITY**: MEDIUM → resuelto.

`CLOSURES_COMPLETE_V1` — cells mutables y escape.
- **CURRENT_STATUS**: **PASS** (2026-09-09; Win 10 tests + Linux 9 tests, byte-idéntico vs CPython).
- **DEPENDENCIES**: `FRAME_MODEL_V1`.
- **COMPLEXITY**: HIGH → resuelto.
- **IMPLEMENTATION**: sin seguimiento estático de flujo — toda llamada no
  resuelta despacha en runtime. El lowering MIR siempre emite `call`:
  `closure_new` (wrap de param+cells en `PitonClosure` arena) en
  `LOAD`/`RETURN` de funciones que capturan, `call` directo si el callee es un
  nombre de función conocido, y si no el **emisor** genera
  `piton_closure_call6(callee, argc, a0..a3)` que en runtime inspecciona el
  objeto;
  si `callee[0]==PITON_CLOSURE_MAGIC (0x5049544EC10557LL)` valida
  `argc==n_args` y `n_cells+argc<=4`, desplaza `args` y rellena `cells[]` a la
  pila x86-64 y llama vía `c->addr`; si no hay magic, invoca el puntero de
  función plano. Fail-closed: lío de argumentos o límites → rc=2 + stderr
  `TypeError: closure called with wrong number of arguments` (o "too many
  captures"). Keyword Piton `no_local` (no `nonlocal`).
- **O2 Linux gotchas resueltos**: (1) `void _start(void)` en freestanding
  rompía `rsp%16` — el kernel entra con rsp%16==0 pero gcc compila asumiendo
  rsp%16==8 → `movaps` del helper fault; el emisor antepone
  `__asm__("sub $8, %rsp")` a `_start`. (2) El `load` de un nombre de función
  era no-op; al pasarse la función POR VALOR el temp quedaba sin inicializar →
  el emitter materializa `(long)&fn` en el slot.
- **KNOWN_GAP**: límites de 4 captures + 4 args (cells+args<=4, fail-closed);
  `nonlocal` con sombreado de binding en niveles intermedios aún sin validación
  de binding estático (CALL-E); escapada a datos globales (carril G).
- **UNLOCKS**: `GENERATOR_SUSPEND_FRAME_V1`, `COROUTINE_V1`.

### 14. Generadores (M5)

`GENERATOR_SEND_V1`, `GENERATOR_THROW_V1`, `GENERATOR_CLOSE_V1`,
`YIELD_FROM_V1`, `GENERATOREXIT_V1`. Todos `NOT_DEMONSTRATED`,
dependen de `GENERATOR_SUSPEND_FRAME_V1`. COMPLEXITY: HIGH cada uno.

### 15. Excepciones (M6)

`EXCEPTIONS_ADVANCED_V1` — re-raise, `from`/`__cause__`, custom exceptions,
BaseException, context, chaining.
- **CURRENT_STATUS**: **PASS** para custom + re-raise (2026-09-09); siguen
  `from`/cadenas, BaseException, binding `excepto E as x`, `excepto*`.
- **COMPLEXITY**: MEDIUM.
Gates de detalle: `EXCEPTION_CUSTOM_V1` (**PASS**), `EXCEPTION_RERAISE_V1`
(**PASS**), `EXCEPTION_FROM_V1`, `BASE_EXCEPTION_V1`, `EXCEPTION_CHAIN_V1`.

Detalle de los dos gates cerrados (2026-09-09):

- **EXCEPTION_CUSTOM_V1** — excepciones definidas por usuario. `clase E(Exception):`
  (o subclases de `ValueError`/`TypeError`/`RuntimeError`, o de otra clase custom
  que cuelgue de `Exception`) se acepta como base de clase en MIR (las bases
  builtin de excepción quedan exentas del requisito "definir antes"). `lanzar
  ErrorApp("msg")` acepta como constructor cualquier clase cuya cadena de
  herencia incluya `Exception`; una clase sin parent custom se rechaza en build
  ("must subclass Exception"). El matching `excepto` usa la cadena
  (`_exception_chain`: caminata por `class_parents` + mapa builtin
  {ValueError, TypeError, RuntimeError}→Exception): un hijo se atrapa por su
  propia clase, por un ancestro custom, por un builtin ancestro y por
  `Exception`; un handler no coincidente deja la excepción no-atrapada.
- **EXCEPTION_RERAISE_V1** — `lanzar` bare en el body de un handler. El
  entry del handler emite `reraise_save` (snapshot de tipo/mensaje del
  exception activo) ANTES de `catch_clear`; `lanzar` bare emite `raise_active`
  que relanza con el tipo guardado y despacha al handler envolvente por los
  labels estáticos (anidamiento de tries funciona). Fail-closed en MIR: bare
  `lanzar` fuera de handler ("requires an enclosing except handler") o desde un
  handler catch-all `excepto Exception:` ("catch-all ... not supported yet").
  Sin handler envolvente → stderr `tipo: msg` + exit(1) (Win vía
  `piton_reraise_unhandled` sin búsqueda de stack; la pila runtime sigue
  teniendo frames spurios accepted-NULL).

Bugs estructurales encontrados y corregidos durante el cierre:

1. **Win tragaba excepciones no-atrapadas dentro de un try con handler que no
   matcheaba**: `raise_typed` con `handler_label=None` emitía `call piton_raise`,
   cuya búsqueda de stack casa con CUALQUIER frame pusheado (accepted=NULL en
   todos — `piton_try_set_accepted` nunca se emite) → flag activado, ignorado,
   ejecución silenciosa. Fix: con label None se emite `piton_raise_unhandled`
   (print+exit, sin búsqueda). El caso previo `lanzar ValueError(...)` sin try
   funcionaba porque la pila estaba vacía.
2. **Re-raise unhandled en Win también se tragaba**: en el momento del re-raise
   el frame del try actual sigue pusheado (el `jne` al handler ocurre antes del
   `try_pop`), así que `piton_reraise` matcheaba y volvía. Fix: `raise_active`
   con label None emite `piton_reraise_unhandled` directamente.

Backends: Windows (`native_runtime.c`: `piton_raise_unhandled`,
`piton_reraise_save/reraise/reraise_unhandled`; ops en `x86.py`) y Linux
(`linux_x86.py`: helpers C `piton_reraise_save/set`, ops `reraise_save` +
`raise_active` con `goto`/`report_unhandled`+exit). Evidencia: 11 tests Win +
11 tests Linux nuevos (diferenciales byte-idénticos y negativos fail-closed).

### 16. Imports (M7)

`IMPORT_PACKAGE_V1` — paquetes con `__init__` y `__path__`.
- **CURRENT_STATUS**: **PASS** (2026-09-09).
- **DEPENDENCIES**: `IMPORT_CORE` (módulo hermano + from-import).
- **COMPLEXITY**: MEDIUM → resuelto.
- **IMPLEMENTATION**:
  - El parser acepta paths dotted (`importar pkg`, `desde pkg.sub importar fn`)
    — el CSP de `desde . importar` (relative, level) queda intacto.
  - Resolución package-aware `resolve_native_module(root, dotted)`: módulo
    hermano (`x.piton`) primero; si no, paquete (`x/__init__.piton`); para
    `a.b[.c]` exige `a/__init__.piton` en cada nivel y resuelve
    `a/b.piton`. Fail-closed con mensajes ("native module not found",
    "... requires package ... with __init__.piton").
  - `_scan_native_modules(entry)` compartido por los backends: levanta
    hermanos y paquetes a HIR y devuelve `(modules, from_imports)`.
  - Símbolos nativos dot-normalizados en MIR: `pkg.sub.fn` → `pkg__sub__fn`
    (from-import, lifting de funciones y module-attr call).
  - Nuevo `compile_native_linux_files` en el backend ELF (espejo del Win).
  - Suite en el cierre: 175/175 total (118 Win: 110 phase5 + 3 phase14 + 5
    package · 57 Linux: 53 + 4 package). El total actual de la suite (215) creció
    con CLOSURES_COMPLETE_V1 y las excepciones custom/re-raise.
- **KNOWN_GAP**: `importar pkg.sub` (dotted IMPORT) rechazado fail-closed —
  requiere attr-chain `pkg.sub.fn` en MIR; toca `MODULE_METADATA_V1`. Los
  módulos importados todavía no pueden importar a su vez (scan del entry
  solamente, no transitivo).
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

**Completado:** `FUNCTION_DEFAULTS_V1` (defaults constantes),
`FUNCTION_KEYWORD_ARGS_V1` (kwargs por nombre), `FUNCTION_ARGS_V1`
(`*args`/`**kwargs`/keyword-only/positional-only),
`IMPORT_PACKAGE_V1` (paquetes `__init__`/`__path__` + submódulos
`desde pkg.sub importar`), `CLOSURES_COMPLETE_V1`, `EXCEPTION_CUSTOM_V1`
(excepciones de usuario con matching por jerarquía) y `EXCEPTION_RERAISE_V1`
(`lanzar` bare en handlers exactos), todos Win+Linux PASS.

1. `FRAME_MODEL_V1` (ARCHITECTURAL) — base de closures/generadores/coroutines.
2. `IMPORT_PACKAGE_V1` (MEDIUM) — paquetes + `__init__`. **PASS**. Sigue
   `MODULE_METADATA_V1` (objetos módulo/runtime) o `IMPORT_RELATIVE_V1`.
3. `MODULE_METADATA_V1` (MEDIUM) — `__name__`/`__file__`/`sys.modules`.
4. `EXCEPTION_CUSTOM_V1` (LOW) — excepciones definidas por usuario. **PASS.**
5. `EXCEPTION_RERAISE_V1` (LOW) — re-raise bare. **PASS.**
6. `OBJECT_MODEL_RICH_V1` (HIGH) — MRO + super.
7. `GENERATOR_SUSPEND_FRAME_V1` (HIGH) — frames suspendidos (depende de 1).
8. `CLOSURES_COMPLETE_V1` (HIGH) — cells mutables y escape (depende de 1).
   **PASS.**
9. `DESCRIPTORS_V1` (HIGH) — descriptors (depende de 6).

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
`FRAME_MODEL_V1` (PASS, ver §11), `GENERATOR_SUSPEND_FRAME_V1`,
`COROUTINE_V1`, `OBJECT_MODEL_RICH_V1`, `DESCRIPTORS_V1`, `METACLASSES_V1`,
`THREADING_V1`, `MULTIPROCESSING_V1`, y cualquier gate que toque
ABI/calling-convention/GC.

Nota 2026-09-09: `CLOSURES_COMPLETE_V1` cerró sin reconstruir el modelo de
frames ni el layout — añadió un objeto closure al arena existente y un helper
de dispatch (`piton_closure_call6`) sin tocar GC/ABI de llamada, así que no
exigió esta revisión. Lo mismo aplica a `EXCEPTION_CUSTOM_V1` +
`EXCEPTION_RERAISE_V1`: helpers estáticos de runtime (save/set/unhandled)
sobre el flag existente, sin cambiar el modelo de frames ni el ABI.

## 33. Stop conditions

Si el gate exige cambio de frame model, GC, object-layout, ABI o
calling-convention → marca `ARCHITECTURE_REVIEW_REQUIRED` y detente. No
reconstruyas el runtime para cerrar un test pequeño.

## 34. Conteo actual y veredictos (baseline)

Ver `FEATURE_STATUS_MATRIX.md`. 215/215 tests; gates PASS en el dashboard
(incl. `FULL_PARITY`, retirado como término en `PARITY_DEFINITION.md`).
Features PARTIAL: functions, generators, descriptors, dynamic_code,
introspection, ffi. NOT_DEMONSTRATED: metaclasses, multiprocessing.