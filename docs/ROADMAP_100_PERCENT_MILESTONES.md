# PITON — Roadmap al 100% por Milestones

> Plan operativo para avanzar desde el subset nativo actual hasta la paridad
> declarada con CPython 3.12.4. Complementa `PITON_CPYTHON_3_12_MASTER_ROADMAP.md`
> y respeta la definición de `PARITY_DEFINITION.md`.

## 0. Qué significa 100%

**100% significa paridad de capacidad, no paridad de comportamiento.** Todo lo
que se puede expresar en CPython se puede expresar en Pitón; nada queda
prohibido. Pero Pitón no se compromete a hacerlo *de la misma manera* cuando la
manera de CPython es un accidente de implementación y no un contrato del
lenguaje.

Qué se hereda y qué no está decidido en `HERENCIA_Y_DIVERGENCIA.md` (Ledger A =
contrato, Ledger B = accidentes). Ese documento es precondición de este: sin él,
"100%" no es una meta falsable sino una promesa de clonar accidentes.

El objetivo es `CPYTHON_3_12_COMPLETE_PARITY = PASS` para el alcance declarado:

1. Semántica del lenguaje: sintaxis, valores, object model, funciones/frames,
   excepciones, imports, async, iteradores y context managers.
2. Runtime observable: lifetime, ciclos, GC, finalizadores y weakrefs cuando
   sean observables desde Pitón.
3. Stdlib declarada, con cada módulo clasificado como `NATIVE`,
   `REIMPLEMENTED`, `BRIDGED`, `COMPAT_LAYER` u `OUT_OF_SCOPE` explícito.
4. Windows x86-64 y Linux x86-64, incluyendo ejecución limpia y evidencia de
   ausencia de CPython en los ejecutables.

No incluye compatibilidad binaria con `PyObject`, bytecode `.pyc` ni la C API
de CPython como requisito de lenguaje. FFI sí tiene un milestone separado.

Tampoco incluye las divergencias declaradas en el Ledger B: destrucción
determinista por refcount, atomicidad estilo GIL, `locals()` como espejo vivo del
frame, interning de inmutables, `id()` como dirección. Cada una lleva estado
`INTENTIONALLY_DIVERGENT` y un test que la fija.

## 1. Reglas de ejecución

- Un milestone no se cierra por cantidad de tests, sino por su gate de salida.
- Cada gate tiene afirmación, corpus, oracle CPython 3.12.4, controles,
  negativos, Windows, Linux, evidencia con SHA-256 y actualización documental.
- Un fallo de arquitectura detiene el milestone: no se parchea el backend para
  ocultar un problema de frame model, ABI, object layout o calling convention.
- Todo feature cross-platform debe pasar en ambos backends o declararse parcial.
- Los errores no soportados deben ser fail-closed y tener un test negativo.
- Una divergencia deliberada requiere entrada en el Ledger B **antes** de
  implementarla, más un test que la fija. Declarar una divergencia es gratis;
  revertir una divergencia ya implementada es un rewrite.
- Una op de MIR nueva sin clasificación de efecto es fail-closed (ver ME).
- Después de cada milestone: suite propia, regresión completa, dashboard,
  matriz y commit separado.

## 2. Estado inicial

`M0_BASELINE` está operativo. Actualmente están demostrados el runtime nativo,
colecciones, floats, funciones del subset, closures, excepciones básicas y
custom, clases/MRO/super, `property`, imports del subset y parte de async.

El dashboard `release_ready` no equivale a 100% de CPython: es la salida del
subset nativo declarado.

## 3. Milestones

### M0 — Baseline reproducible

**Objetivo:** congelar la línea base antes de ampliar semántica.

**Gates de salida:** `BASELINE_REPRODUCIBLE_V1 = PASS` y
`PITON_NATIVE_SUBSET_PARITY = PASS`.

**Entregables:** oracle 3.12.4 fijado, inventario de tests/gates, runner
diferencial único Win/Linux y receipt con comandos, salidas, códigos, entorno
y hashes.

**No avanzar si:** el baseline no reproduce exactamente el resultado registrado.

### M1 — Contrato de valores y memoria

**Objetivo:** cerrar las bases usadas por todos los milestones posteriores.

**Gates:** `VALUES_COMPLETE_V1` (None, bool, int, bigint, float, complex, str,
bytes, bytearray, memoryview, tuple, list, dict, set, frozenset, range, slice),
`NUMERIC_SEMANTICS_V1` (overflow, negativos, floor division, modulo,
conversiones, NaN, infinitos, hash y comparaciones) y `MEMORY_LIFETIME_V1`
(ownership, refcount, ciclos y liberación).

**Tests:** tablas de valores, operaciones mixtas, aliasing, mutación, errores de
tipo/división, stress heterogéneo y controles de fugas.

**Dependencias:** M0.

### ME — Effect lattice en MIR

**Objetivo:** clasificar el efecto de cada op del MIR y encadenar el effect
token. No cambia semántica: es un anotador. Es la base estructural del sello
dataflow y es barato **hoy**; después de M20 es reescribir todos los pases y
ambos backends.

**Gates:** `EFFECT_CLASSIFICATION_V1` (toda op clasificada `PURE` / `READ` /
`WRITE` / `IO` / `OPAQUE`; op sin clasificar = fail-closed),
`EFFECT_TOKEN_CHAIN_V1`, `EFFECT_NEUTRALITY_V1` (suite diferencial completa
idéntica antes y después: mismo stdout, mismo exit code) y
`MIR_HASH_REBASELINE_V1`.

**Claim falsable:** anotar efectos no cambia ni un byte de salida.

**Nota de implementación:** `MIRInstruction` es un dataclass frozen de
`(op, args, result)`; el campo `effects` entra con valor por defecto sin romper
backends. Pero `to_dict()` alimenta `MIR_DETERMINISTIC` y `MIR_TRACE_STABLE`, así
que el hash del MIR cambia y requiere rebaseline explícito.

**Dependencias:** M1. Va **antes** de M2, para que cada op nueva nazca
clasificada. Detalle en `HERENCIA_Y_DIVERGENCIA.md` §5.1 y §6.

### M2 — Frames y llamadas completas

**Objetivo:** que funciones y frames sean una base CPython-like, no solo ABI de
llamadas simples.

**Gates:** `FRAME_MODEL_V1` (locals, globals, retorno, excepción y unwind),
`FUNCTION_SIGNATURES_V2` (positional-only, keyword-only, defaults, `*args`,
`**kwargs`, annotations), `CALL_UNPACKING_V1` (`f(*xs)`, `f(**mapping)` y
orden), `BOUND_METHODS_V1` (binding, `__self__`, shadowing) y
`DECORATOR_SEMANTICS_V1` (decoradores y orden de aplicación).

**No avanzar si:** una llamada puede saltarse validación de firma o deja un frame
parcialmente vivo después de una excepción.

**Diseño ABI:** `docs/CALL_UNPACKING_ABI.md`. El subgate
`CALL_UNPACKING_LITERAL_V1` está PASS; el unpacking dinámico requiere
`CALL_UNPACKING_DYNAMIC4_V1` y finalmente `CALL_FRAME_ABI_V1`.

**Dependencias:** M1.

### M3 — Closures y cells sin límites artificiales

**Objetivo:** eliminar los límites actuales de captures/args o documentar una
decisión formal de alcance.

**Gates:** `CLOSURES_COMPLETE_V2`, `CELLS_MUTABLE_V2` (`nonlocal`, aliasing y
cells transitivas), `CLOSURE_RECURSION_V1` y `CALLABLE_PROTOCOL_V1` para
funciones, closures, bound methods y objetos llamables.

**Tests:** fábricas anidadas, callbacks, recursión, captura en loops, mutación,
excepciones y destrucción de referencias.

**Dependencias:** M2.

### M4 — Iterables e iteradores

**Objetivo:** cerrar el protocolo compartido por loops, comprehensions, async y
stdlib.

**Progreso actual:** `LOOP_COLLECTION_LITERAL_V1` PASS en Windows/Linux para
`for` sobre listas y tuplas nativas. El lowering reutiliza el recorrido
indexado existente. `ITER_PROTOCOL_V1` básico también PASS en Windows/Linux para
`iter(list|tuple|dict|set)` y `next(iterator)` con estado independiente.
`StopIteration` se propaga por la bandera de excepción existente y puede ser
capturado por `excepto StopIteration` o `excepto Exception`. El dispatch básico
de iteradores definidos por usuario (`__iter__`/`__next__`) y los adaptadores
nativos `enumerate`, `reversed`, `zip` (dos iterables), `map` y `filter` están
PASS en su subset nativo; `map/filter` aceptan callback nombrado unario,
closures con captures y lambdas. `LOOP_PROTOCOL_V2` PASS: `break`, `continue`
y `else` en `for` y `while`, con semantics correctas de for-else (flag de
break). `COMPREHENSIONS_V2` está PASS en el subset nativo verificado para list,
set y dict comprehensions con filters y `for` encadenado; también cubre
generator expressions (eager, list-backed, con `iter`/`next` semantics),
elementos tuple y colecciones anidadas en Windows/Linux. El subset actual
requiere elementos/llaves/valores enteros. Siguen pendientes callbacks
dinámicos, otros builtins iterable y el protocolo completo con generadores.

**Gates:** `ITER_PROTOCOL_V1` (`__iter__`, `__next__`, `iter`, `next`, sentinel),
`ITER_BUILTINS_V1` (`enumerate`, `zip`, `map`, `filter`, `reversed`),
`LOOP_PROTOCOL_V2` (`for`, `break`, `continue`, `else`) = **PASS** y
`COMPREHENSIONS_V2` (list/set/dict/generator, scopes y condiciones): **PASS**;
list/set/dict comprehensions y genexpr PASS en el subset nativo verificado
(224 Windows + 153 Linux).

**Dependencias:** M2 y M3.

### M5 — Generadores completos

**Objetivo:** implementar suspensión real de frames.

**Progreso actual (2026-09-12):** `GENERATOR_FRAME_V1` PASS en Windows/Linux
para el subset escalar: `MIRFunction.is_generator` + layout de slots
persistidos compartido por ambos backends; cuerpos generadores como máquinas
de estados sobre `PitonGenerator` heap (`state`/`finished`/slots,
`piton_gen_new/next/free/collect`); `gen_init` ya no ejecuta el cuerpo
(lazy); `next`, params, locals preservados, `StopIteration` en la llamada que
agota (también en `MIREvaluator`), instancias independientes; retorno con
valor, defaults, closures/métodos generadores y `*args`/`**kwargs` son
fail-closed. `for` sobre generadores puros sigue por tupla compile-time.
`GENERATOR_SEND_V1` PASS (`send`/`enviar`, TypeError si se envía no-None a un
generador recién creado, Win+Linux). `GENERATOR_THROW_V1` PASS para el subset
sin catch interno (los generadores no pueden atrapar porque `yield` dentro de
`try` es fail-closed): `throw()`/`arrojar()` marca el generador como agotado y
la excepción se eleva en el handler del llamador, espejo de CPython cuando el
generador no la captura. `GENERATOR_CLOSE_V1` PASS: `close()`/`cerrar()` marca
`finished`, idempotente, `next()` posterior eleva `StopIteration`.

**Gates:** `GENERATOR_FRAME_V1` = **PASS** (subset escalar);
`GENERATOR_SEND_V1` = **PASS**; `GENERATOR_THROW_V1` = **PASS** (sin catch
interno); `GENERATOR_CLOSE_V1` = **PASS**. `YIELD_FROM_V1` = fail-closed
(`producir desde` rechazado en MIR hasta implementar delegación con propagación
de `send`/`throw`); `PEP479_V1` = espera a `devolver <valor>` en generadores
(también fail-closed hoy).

**Tests:** `next`, `send`, `throw`, `close`, `GeneratorExit`, `yield from`,
valores finales, finally, generators anidados y errores.

**Dependencias:** M2, M3 y M4.

### M6 — Object model completo

**Objetivo:** terminar la semántica más allá de clases/MRO/property.

**Gates:** `OBJECT_PROTOCOL_V2` (object/type, identity, equality, construcción),
`ATTRIBUTE_LOOKUP_V2` (`__getattribute__`, `__getattr__`, `__setattr__`,
`__delattr__`, shadowing), `DESCRIPTORS_USER_V1` (`__get__`, `__set__`,
`__delete__`, `__set_name__`), `SPECIAL_METHOD_LOOKUP_V1`, `SLOTS_V1` y
`METACLASSES_V1`.

**Nota:** `DESCRIPTORS_V1` ya cerró `property` estático; este milestone cubre
descriptors definidos por usuario y metaclasses.

**Dependencias:** M1, M2, M3 y `DESCRIPTORS_V1`.

### M7 — Excepciones avanzadas

**Objetivo:** completar causalidad y exposición de excepciones.

**Gates:** `EXCEPTION_CHAINING_V1` (`from`, `__cause__`, `__context__`),
`EXCEPTION_BINDING_V1` (`excepto E as x`), `BASE_EXCEPTION_V1`,
`TRACEBACK_MODEL_V1`, `EXCEPTION_GROUP_V1` si permanece en alcance y
`RERAISE_COMPLETE_V1`.

**Dependencias:** M2, M3 y M6.

### M8 — Imports y módulos completos

**Objetivo:** cerrar resolución, identidad y cache de módulos.

**Gates:** `IMPORT_CORE_V2`, `IMPORT_CACHE_V1`, `IMPORT_RELATIVE_V2` (niveles
arbitrarios), `IMPORT_NAMESPACE_PACKAGE_V1`, `IMPORT_HOOKS_V1` si se declara y
`IMPORT_CYCLE_V2`.

**Dependencias:** M2, M6 y M7.

### M9 — Async language completo

**Objetivo:** separar semántica async de la paridad de `asyncio`.

**Progreso actual (2026-09-12):** `COROUTINE_OBJECT_V1` + `AWAIT_PROTOCOL_V1` +
`ASYNC_GENERATOR_V1` + `ASYNC_FOR_V1` PASS en Windows/Linux. `async def` produce un
objeto coroutine real: reutiliza la máquina de estados suspendible de
`PitonGenerator` (`MIRFunction.is_coroutine`); una llamada `val()/inner()` dentro
de `await`/`asyncio.run` crea el objeto coroutine vía `gen_init` en vez de
incrustar su cuerpo (la llamada fuera de estos contextos sigue fail-closed:
`must be awaited`). `await X` suspende el coroutine sobre X (`gen_yield` del
coroutine esperado); `asyncio.run(coro())` emite `coro_run`, que ejecuta el
coroutine depth-first (`piton_coro_run`): corre cada coroutine esperado hasta
completarse y retroalimenta su valor de retorno a través de `sent_value` en el
punto de reanudación. `devolver v` en un coroutine expone su resultado al
await-er. **Async generators (`asincrono funcion` con `producir`):** la llamada
crea el objeto en cualquier contexto (sin `must be awaited`); los yields de datos
bajan a `agen_emit` y los awaits internos a `gen_yield`, ambos suspendiendo en la
misma máquina de estados; un slot marcador dedicado (`PitonGenerator.slots[63]`,
reservado por el layout guard) distingue "yield cuyo valor es un coroutine a
ejecutar" de "yield de datos"; `piton_agen_next` (Win y Linux) driver el
generador: corre los coroutines esperados vía `piton_coro_run`, retroalimenta sus
resultados por `sent_value`, devuelve los yields de datos y señala
StopAsyncIteration al terminar. **`asincrono para x en <async-gen-call>`:**
`gen_init` + bucle `agen_next`/`agen_done`/branch con `sino:` for-else (flag
per-loop `@for_else_N`); fail-closed: await de un async gen (`cannot await an
async generator object`), async for de un no-async-gen, async for fuera de
función async. Verificado byte-idéntico vs CPython Win 14 + Linux 6 (incluye
cadena anidada de 3 niveles con parámetros y async-gen con await interno +
for-else con y sin `romper`). **`TASK_SCHEDULER_V1` PASS el mismo día** (Win 12 +
Linux 10): scheduler round-robin cooperativo en ambos runtimes
(`piton_event_run` + `piton_step_task`, cola FIFO ready; `PitonTask` guarda la
cadena completa de awaits inline `chain[64]` + `chain_depth`, así un `esperar
coro()` dentro de un coroutine conserva sus padres a través de suspensiones).
`asyncio.create_task(coro())` envuelve el coroutine en un task; `asyncio.gather`
acepta llamadas directas a coroutine o variables task (miembros ya terminados →
lista de resultados inmediata; miembro cancelado → el gather aborta con
`CancelledError`); `asyncio.sleep(0)` baja a `sleep0` → `PITON_SLEEP0_MAGIC`
(re-queue cooperativo; `sleep(n>0)` fail-closed `sleep(0) only`);
`task.cancel()` marca `cancel_requested` → el task se descarta en su siguiente
pop, sus waiters directos y los waiters de su gather se cancelan en cascada, y un
root cancelado aparece como `CancelledError` no manejado (exit 1 Win / exit 2
Linux). Guards pointer-floor en el dispatch coro/task/gather: await de un
no-awaitable, cancel de un no-task y gather de un no-task fallan fail-closed con
TypeError (`object is not awaitable` / `object has no attribute 'cancel'` /
`gather requires tasks`) en vez de crashear (se corrigió un ACCESS_VIOLATION real
en `piton_gather_add` con `gather(5)`). Las listas de resultados de gather son
owned por la coroutine awaiting (registradas en `owned_slots` y liberadas en el
cleanup del gen) — el tripwire de live-count del teardown (exit code =
colecciones vivas) se mantiene en 0. Orden FIFO determinista byte-idéntico vs
CPython para el subset probado.

**Gates:** `COROUTINE_OBJECT_V1` = **PASS**; `AWAIT_PROTOCOL_V1` = **PASS**;
`ASYNC_GENERATOR_V1` = **PASS**; `ASYNC_FOR_V1` = **PASS**;
`TASK_SCHEDULER_V1` = **PASS**.
`ASYNC_WITH_V1`, `ASYNC_EXCEPTION_V1` siguen pendientes, y
`TASK_SCHEDULER_V2` (timers reales / `asyncio.sleep(n>0)` / excepción inyectada
en tasks) queda como extensión del scheduler cooperativo. El scheduler actual es
FIFO cooperativo puro — timeout y concurrencia dependiente de timers reales
requieren un loop de eventos con reloj, no el trampolín actual.

### M10 — Context managers y control no local

**Objetivo:** completar `with` y sus interacciones con excepciones.

**Gates:** `WITH_PROTOCOL_V1` (`__enter__`, `__exit__`, `as`),
`WITH_MULTIPLE_V1`, `WITH_SUPPRESSION_V1`, `WITH_EXCEPTION_UNWIND_V1` y
`ASYNC_WITH_V1` si no quedó en M9.

**Dependencias:** M5, M7 y M9.

### M11 — Sintaxis y gramática restante

**Objetivo:** cerrar formas válidas todavía sin gate.

**Gates:** `GRAMMAR_ASSIGNMENT_V2` (annotations, chained, starred targets),
`GRAMMAR_PATTERN_MATCHING_V1`, `GRAMMAR_WALRUS_V1`, `GRAMMAR_FSTRINGS_V1`
(PEP 701), `GRAMMAR_DECORATORS_V2` y `GRAMMAR_CONTEXT_V1`.

**Dependencias:** M2, M4, M6 y M10.

### M12 — Introspección y código dinámico

**Objetivo:** implementar lo observable sin prometer introspección interna.

**Gates:** `INTROSPECTION_OBJECTS_V1` (`type`, `isinstance`, `issubclass`),
`INTROSPECTION_ATTRS_V1` (`getattr`, `setattr`, `hasattr`, `dir`, `vars`),
`INTROSPECTION_CALLABLE_V1`, `GLOBALS_LOCALS_V1`, `EVAL_V1`, `EXEC_V1` y
`COMPILE_V1`.

**Divergencia declarada (Ledger B3):** `locals()` devuelve un *snapshot*;
escribir en el dict no modifica el frame. Es lo que CPython adoptó en PEP 667,
así que la divergencia contra 3.12.4 converge con CPython moderno.
`GLOBALS_LOCALS_V1` se marca `INTENTIONALLY_DIVERGENT` y lleva test que la fija.

**Divergencia declarada (Ledger B4):** `sys._getframe` es `GUARDED`, no
divergente: una función que lo alcanza deoptimiza y materializa su frame.
Capacidad intacta, costo localizado en quien lo usa. Depende de MG.

**Dependencias:** M2, M6, M8 y M11.

### M13 — Runtime observable y GC

**Objetivo:** cubrir memoria donde CPython la expone semánticamente, **sin
heredar el momento exacto de destrucción**.

**Conflicto resuelto.** `LIFETIME_OBSERVABILITY_V1`, como estaba escrito, es el
Ledger B1: prometer que `__del__` corre en el instante del último decref
convierte el último uso de todo objeto en un efecto observable. Con eso ninguna
operación puede reordenarse ni eliminarse, y quedan permanentemente cerrados
dataflow, DCE, escape analysis y unboxing. Se retira ese gate.

**Gates:** `GC_CYCLES_V1`, `FINALIZERS_V1` (`__del__` corre **exactamente una
vez** antes de que el programa termine), `WEAKREFS_V1`, `LIFETIME_CONTRACT_V1`
(`con` / context managers es el mecanismo determinista de limpieza; el momento de
`__del__` no es contrato) y `GC_MODULE_SURFACE_V1` para APIs declaradas.

`LIFETIME_CONTRACT_V1` se marca `INTENTIONALLY_DIVERGENT`. Es la postura de
PyPy, y es defendible porque el propio CPython documenta el refcounting como
detalle de implementación y recomienda `with` para limpieza determinista.

**La declaración es hoy; la implementación es aquí.** Windows registra
objetos/listas/dicts/sets capaces de formar ciclos y, al cierre, protege un
snapshot, corta aristas y reclama los shells. Hay evidencia directa C de una
lista autorreferenciada y de un ciclo objeto→lista→objeto bajando el contador
total a 0; el pipeline PITÓN cubre ciclos mutuos de objetos sin corrupción.
Linux ahora tiene freelist heap + refcounting + GC idéntico; el C-harness
`CycleGCLinuxV1` demuestra recolección de ciclos en ambos backends.
`GC_CYCLES_V1` es `PASS` cross-platform. `FINALIZERS_V1` ya
está `PASS`: `__del__(self)` se liga por MRO y corre exactamente una vez antes
de salir en Win (refcount o colector de ciclos) y en Linux (registro de
objetos al final de `main`); excepciones/resurrección dentro de `__del__` no
están aún especificadas. El gate retirado no se puede reintroducir sin cerrar
MD.

**Dependencias:** M1, M3, M6 y M12.

### MG — Especulación, guards y deoptimización

**Objetivo:** conservar el techo dinámico del Ledger A sin pagarlo siempre. El
código se compila bajo supuestos explícitos ("este valor es int inmediato",
"esta clase no fue parchada", "esta función no pide su frame"); un nodo guard los
verifica barato y, si falla, deoptimiza al camino genérico.

Esto es lo que hace que nada quede prohibido: si el programa hace la locura
dinámica, el guard falla y cae al slow path, que funciona.

**Gates:** `GUARD_NODE_V1`, `DEOPT_CORRECTNESS_V1` (deoptimizar en cualquier
punto produce el resultado del camino genérico), `TYPE_FEEDBACK_V1`,
`INLINE_CACHE_V1` y `GUARD_ADVERSARIAL_V1` (corpus que **fuerza** el fallo de
cada guard: monkeypatching, `__getattribute__` sobrescrito, tipos mixtos en el
mismo sitio de llamada).

**Claim falsable:** para todo programa del corpus, resultado con guards =
resultado con `--opt=0`.

**Dependencias:** ME y M6.

### MD — Scheduler dataflow

**Objetivo:** el sello de Pitón. Ejecutar el MIR como grafo de dependencias de
datos en vez de lista de instrucciones en orden de programa. Los nodos puros
flotan; el effect token impone orden secuencial sólo donde el Ledger A lo exige,
de modo que la equivalencia observable sale **por construcción** y no por
testing.

**Gates:** `DATAFLOW_SCHEDULE_V1`, `DATAFLOW_EQUIVALENCE_V1` (corpus completo
byte-idéntico contra el orden secuencial), `DATAFLOW_DETERMINISM_V1` (misma
entrada → misma salida aunque el orden interno cambie) y
`DATAFLOW_PERF_BASELINE_V1` (número medido, no anécdota).

**Lo que este milestone no promete:** velocidad en operaciones que ya son
llamadas al runtime C (`piton_dict_*`, `piton_str_*`). Esos nodos son `OPAQUE`,
viven en el hilo de efectos y nunca iban a flotar. La ganancia esperada está en
código compute-bound con tipos conocidos y **no tiene número** hasta que
`DATAFLOW_PERF_BASELINE_V1` lo mida. Múltiplos observados en cargas I/O-bound no
se transfieren: ahí el dataflow gana solapando latencia ociosa, mecanismo que no
existe en carga CPU-bound.

**Dependencias:** ME, MG y M13 con `LIFETIME_OBSERVABILITY_V1` retirado.

### M14 — Builtins y stdlib Tier 1

**Objetivo:** ampliar el núcleo usable sin arrastrar todo el sistema.

**Gates:** `BUILTINS_CORE_V2` (`all`, `any`, `bin`, `chr`, `ord`, `pow`, `round`,
`sorted` y protocolos iterables), `TYPE_CONVERSION_V1`, `MATH_TIER1_V1` y,
si se declaran, `RE_V1`, `DATETIME_V1`, `COLLECTIONS_TIER1_V1`.

**Regla:** cada módulo necesita matriz propia de APIs; importar el nombre no es
un PASS.

**Dependencias:** M1, M4, M7, M8 y M12.

### M15 — IO, OS y procesos

**Objetivo:** demostrar efectos externos en sandboxes reproducibles.

**Gates:** `IO_TEXT_V1`, `IO_BINARY_V1`, `PATHLIB_V1`, `OS_ENV_V1`,
`OS_FILESYSTEM_V1`, `SUBPROCESS_V1`, `TIME_V1` y `SIGNALS_V1` si se declara.

**Dependencias:** M8, M10, M12 y M14.

### M16 — Concurrencia

**Objetivo:** cerrar threading y multiprocessing según el modelo demostrable.

**Gates:** `THREADING_V1`, `QUEUE_V1`, `ASYNCIO_STDLIB_V1`, `MULTIPROCESSING_V1`,
`PROCESS_ISOLATION_V1` y `CONCURRENCY_DETERMINISM_V1`.

**Divergencia declarada (Ledger B2):** no se promete atomicidad implícita estilo
GIL (`lista.append` atómico entre hilos). Ni CPython 3.13+ la garantiza bajo
free-threading. `CONCURRENCY_DETERMINISM_V1` se declara sobre el modelo de
concurrencia de Pitón, no sobre el de CPython.

**Dependencias:** M9, M15 y M14.

### M17 — FFI y extensiones

**Objetivo:** cerrar el carril separado de interop nativa.

**Gates:** `C_ABI_CALL_V1`, `POINTER_MEMORY_V1`, `CTYPES_DECLARED_V1`,
`NATIVE_LIBRARY_LOADING_V1`, `FFI_LIFETIME_V1` y `FFI_SECURITY_V1`.

**Dependencias:** M1, M2, M13 y M15.

### M18 — Plataforma y clean machine

**Objetivo:** probar independencia del entorno de desarrollo.

**Gates:** `WINDOWS_CLEAN_MACHINE_V1`, `LINUX_CLEAN_MACHINE_V1`,
`CROSS_PLATFORM_CORPUS_V1`, `ABI_CALLING_CONVENTION_V1`,
`DEPENDENCY_CLOSURE_V1` y `REPRODUCIBLE_BUILD_V1`.

**Dependencias:** todos los milestones aplicables.

### M19 — Corpus completo e integración

**Objetivo:** encontrar interacciones que no cubren los gates unitarios.

**Gates:** `LANGUAGE_INTERACTION_CORPUS_V1`, `OBJECT_ASYNC_INTERACTION_V1`,
`DYNAMIC_IMPORT_INTERACTION_V1`, `RUNTIME_STRESS_V1`, `STDLIB_INTEGRATION_V1` y
`REGRESSION_ZERO_V1`.

**Dependencias:** M1–M18 según cada corpus.

### M20 — Cierre de paridad 100%

**Objetivo:** componer todas las superficies en un resultado auditable.

**Gates de salida:** `CPYTHON_3_12_LANGUAGE_PARITY = PASS`,
`CPYTHON_3_12_RUNTIME_PARITY = PASS`, `CPYTHON_3_12_STDLIB_PARITY = PASS` para
la matriz declarada, `CPYTHON_3_12_PLATFORM_PARITY = PASS` y
`CPYTHON_3_12_COMPLETE_PARITY = PASS`.

**Checklist:** ningún gate aplicable en `PARTIAL`; toda API ausente marcada
`OUT_OF_SCOPE` o `NOT_DEMONSTRATED`; **toda entrada del Ledger B con test que la
fija**; todo PASS con corpus, raw output, exit status y SHA-256; dashboard,
matrices, roadmap y handoff sincronizados; builds limpios reproducidos en
Windows/Linux; paths de evidencia no reutilizados.

**Afirmación de cierre.** La frase correcta es: *"Pitón tiene paridad de
capacidad con CPython 3.12.4 para el alcance declarado, con N divergencias de
comportamiento documentadas y fijadas por tests."* No: *"Pitón es 100% compatible
con Python."*

## 4. Orden recomendado

`M0 → M1 → ME → M2 → M3 → M4 → M5 → M7 → M10 → M6 → MG → M8 → M9 → M11 →
M12 → M13 → MD → M14 → M15 → M16 → M17 → M18 → M19 → M20`.

ME va inmediatamente después de M1 porque es aditivo y barato ahora, y porque
cada op de MIR que nazca después debe nacer clasificada. MG va después de M6
porque la especulación necesita el object model. MD va después de M13 porque la
retirada de `LIFETIME_OBSERVABILITY_V1` es lo que lo vuelve legal.

No implementar una superficie posterior para esquivar una dependencia anterior.
Si aparece una incompatibilidad de arquitectura, abrir `ARCHITECTURE_REVIEW`
en vez de añadir un parche local.

## 5. Plantilla de sesión

```text
MILESTONE: <id>
GATE: <gate id>
CLAIM: una frase falsable
ANTES: test negativo o fallo controlado
IMPLEMENTACIÓN: parser -> CST/AST -> HIR -> MIR -> Win -> Linux -> runtime
POSITIVOS: corpus diferencial contra CPython 3.12.4
NEGATIVOS: errores esperados y fail-closed
CONTROLES: caso equivalente que no usa la feature
SALIDA: exit code + stdout + stderr + excepción estructurada
EVIDENCIA: raw/, manifest, environment, hashes.json
PASS: ambos backends y regresión completa verdes
DOCS: matrix + master roadmap + dashboard + handoff
COMMIT: <GATE> PASS: <claim corto>
```

## 6. Métrica honesta

Reportar siempre tres números separados:

- `gates_pass / gates_applicables` para el roadmap.
- `assertions_pass / assertions_total` para cada corpus.
- `surfaces_pass / surfaces_declared` para lenguaje, runtime, stdlib y
  plataforma.
- `divergences_pinned / divergences_declared` para el Ledger B.

El 100% solo se puede afirmar cuando las cuatro superficies y la composición
M20 están en PASS. Hasta entonces, el estado correcto es `PARTIAL`, aunque
todas las pruebas del subset actual estén verdes.
