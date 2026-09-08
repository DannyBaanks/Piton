PITÓN — ROADMAP 1.0 → x86-64

“Empezó porque else daba hueva. Termina emitiendo x86.”

NORTE

Pitón 1.0 sigue siendo un frontend auditable de Python: la superficie humana está en español de México, la semántica de referencia pertenece a una versión congelada de CPython y el Python generado siempre puede inspeccionarse.

El camino a x86 no consiste en reescribir Python entero de golpe. Consiste en jubilar a CPython una responsabilidad a la vez, manteniéndolo como oracle diferencial hasta que cada capa nueva demuestre equivalencia para el subconjunto que afirma soportar.

Regla central:  
NO quitar una capa hasta que la capa Pitón que la sustituye tenga evidencia diferencial suficiente.

META DOBLE

Meta A — PITON\_NATIVE\_X86  
Un subconjunto útil de Pitón compila a un ejecutable x86-64 real que arranca y corre sin python.exe, python3, libpython ni una VM de bytecode de Pitón.

Meta B — PITON\_X86\_PARITY  
El backend nativo cubre la semántica declarada de la versión congelada de Python que Pitón tomó como referencia.

Meta A puede alcanzarse mucho antes que Meta B. No se debe fingir que “genera x86” significa automáticamente “es compatible con todo Python”.

PUNTO DE PARTIDA: PITÓN 1.0

Contrato de 1.0:  
• Paridad sintáctica declarada con una versión concreta de CPython.  
• CST/AST o clasificación contextual suficiente para soft keywords, bindings, f-strings, scopes y builtins.  
• Python generado visible.  
• Import system .piton funcional.  
• Errores y tracebacks remapeados.  
• Corpus diferencial grande.  
• Windows, Linux y macOS probados.  
• Sin LLM en runtime.  
• Sin monkey patching de CPython.  
• Sin traducciones ocultas.

Oracle inicial recomendado:  
• CPython 3.12.4, porque es la versión ya demostrada en el V0 actual.  
• Cambiar de oracle sólo mediante una decisión explícita y un rebaseline del corpus.

DEUDA DE DEPENDENCIA

1.0  
.piton → frontend Pitón → Python → CPython

2.0  
.piton → frontend Pitón → HIR/MIR Pitón → backend Python → CPython

3.0  
.piton → frontend Pitón → HIR/MIR Pitón  
├─→ backend Python → CPython \[oracle\]  
└─→ backend x86-64 → ejecutable \[experimental\]

4.0+  
.piton → frontend Pitón → HIR/MIR Pitón → backend x86-64 → runtime Pitón → CPU

Al final, CPython deja de ser dependencia de ejecución y queda como referencia histórica/diferencial.

FASE 1 — 1.0 → 1.2: CONGELAR LA SEMÁNTICA

Objetivo:  
Antes de escribir ensamblador, definir exactamente qué significa “hacer lo mismo que Python”.

Entregables:  
• SPEC.md: sintaxis Pitón y reglas de traducción.  
• SEMANTICS.md: contrato observable de equivalencia.  
• NATIVE\_SCOPE.md: qué entra en el backend nativo y qué no.  
• ORACLE.md: versión exacta de CPython, flags y plataforma de referencia.  
• Corpus diferencial versionado por feature.  
• Formato estable de resultados observables.

Comparar, según aplique:  
• exit code;  
• stdout;  
• stderr normalizado;  
• valor retornado serializable;  
• tipo y mensaje de excepción;  
• cadena causal;  
• cambios de filesystem dentro de sandbox;  
• imports cargados;  
• orden observable cuando el lenguaje lo garantiza.

No prometer identidad de bytecode CPython, layout interno de objetos, direcciones, inspect internals no declarados ni detalles que no formen parte del contrato observable.

Gate:  
SPEC\_FROZEN \= PASS  
ORACLE\_PINNED \= PASS  
DIFFERENTIAL\_CONTRACT \= PASS  
CORPUS\_BASELINE \= PASS

FASE 2 — 1.2 → 1.5: FRONTEND PROPIO COMPLETO

Objetivo:  
Que Pitón deje de depender conceptualmente de “traducir tokens y esperar que Python nos diga qué quisimos decir”.

Pipeline:  
fuente .piton → lexer/tokenizer → CST Pitón → AST Pitón → análisis de scopes → HIR

El backend Python se conserva, pero ahora es sólo un backend más.

Debe resolver:  
• keywords y soft keywords;  
• f-strings PEP 701;  
• pattern matching;  
• async/await;  
• scopes y shadowing;  
• decorators;  
• annotations;  
• comprehensions;  
• imports;  
• bindings de pattern matching;  
• global/nonlocal;  
• errores de sintaxis con ubicación propia.

Gate:  
PITON\_PARSE\_WITHOUT\_CPYTHON \= PASS  
PITON\_SCOPE\_ANALYSIS \= PASS  
PITON\_AST\_STABLE \= PASS  
PYTHON\_BACKEND\_FROM\_PITON\_AST \= PASS  
DIFFERENTIAL\_CORPUS \= PASS

Estado verificado el 2026-09-08:

```text
FASE_2_GATES = PASS (7/7)
PITON_PARSE_WITHOUT_CPYTHON = PASS
PITON_SCOPE_ANALYSIS = PASS
PITON_AST_STABLE = PASS
PYTHON_BACKEND_FROM_PITON_AST = PASS
HIR_LOWERING_AND_BACKEND = PASS
PHASE2_SURFACE_FEATURES = PASS
DIFFERENTIAL_CORPUS = PASS
Comando: py -m unittest tests.test_phase2 -v
Resultado: Ran 7 tests ... OK
Suite completa: Ran 74 tests ... OK
```

El alcance demostrado cubre assignments, scopes, funciones, control de flujo,
imports, async/await, pattern matching, decoradores, annotations,
comprehensions y f-strings con expresiones. La traducción textual no se exige
como identidad: el gate compara AST válido y comportamiento observable del
corpus. F-strings PEP 701 completo y semántica dinámica avanzada permanecen
como ampliaciones de cobertura, no como una afirmación universal de paridad.

FASE 3 — 1.5 → 2.0: HIR Y MIR CANÓNICOS

Objetivo:  
Separar por completo “qué significa el programa” de “cómo lo ejecuta Python o x86”.

HIR representa intención de alto nivel: funciones, closures, clases, llamadas, atributos, iteración, excepciones, comprehensions, generators, async y pattern matching.

MIR representa operaciones ejecutables más pequeñas: constantes, loads/stores, bloques básicos, branch/jump, call/return, compare, box/unbox, get/set attribute, get/set item, iterator next, raise/unwind, allocation y runtime calls.

Regla:  
No meter detalles x86 en HIR. No meter sintaxis Pitón en MIR.

Crear un MIR evaluator mínimo sólo para pruebas y trazas. No convertirlo en una VM de producción ni en un nuevo runtime principal.

Gate:  
SOURCE\_TO\_HIR \= PASS  
HIR\_TO\_MIR \= PASS  
MIR\_DETERMINISTIC \= PASS  
MIR\_TRACE\_STABLE \= PASS  
MIR\_TO\_PYTHON\_ORACLE\_EQUIVALENCE \= PASS

Estado verificado el 2026-09-08:

```text
FASE_3_GATES = PASS (5/5)
SOURCE_TO_HIR = PASS
HIR_TO_MIR = PASS
MIR_DETERMINISTIC = PASS
MIR_TRACE_STABLE = PASS
MIR_TO_PYTHON_ORACLE_EQUIVALENCE = PASS
Comando: py -m unittest tests.test_phase2 -v
Resultado: Ran 10 tests ... OK
```

El MIR demostrado es un evaluator de pruebas, no una VM de producción. Incluye
temporales, constantes, loads/stores, operaciones, comparaciones, llamadas,
bloques, branch/jump y return, con serialización JSON y trazas estables.

FASE 4 — 2.0 → 2.2: ABI Y REPRESENTACIÓN DE VALORES

Objetivo:  
Diseñar el suelo sobre el que correrá el backend nativo antes de emitir programas grandes.

Decisiones obligatorias:  
• Windows x64 como primer ABI nativo por ser el entorno principal de desarrollo.  
• SysV AMD64 como segundo ABI.  
• Stack alignment.  
• Convención de llamada interna de Pitón.  
• Registros caller/callee-saved.  
• Representación de Value.  
• Convención de error/excepción.  
• Ownership de heap objects.  
• Interfaz entre código generado y runtime.

Representación recomendada para arrancar:  
• tagged values o NaN-boxing sólo si el diseño queda medido y entendible;  
• small ints inmediatos;  
• bool y Nada inmediatos;  
• punteros etiquetados para objetos de heap;  
• big integers en heap;  
• floats IEEE-754;  
• strings y contenedores como objetos del runtime.

No intentar copiar el layout binario de PyObject salvo que exista una razón explícita. Compatibilidad semántica no exige compatibilidad ABI con CPython.

Gate:  
PITON\_VALUE\_MODEL \= FROZEN  
WINDOWS\_X64\_ABI \= PASS  
SYSV\_AMD64\_ABI \= PASS\_LATER\_ALLOWED  
STACK\_ALIGNMENT \= PASS  
CALL\_CONVENTION\_TESTS \= PASS

Estado verificado el 2026-09-08:

```text
FASE_4_GATES = PASS (4/4; SysV AMD64 queda PASS_LATER_ALLOWED)
PITON_VALUE_MODEL = FROZEN
WINDOWS_X64_ABI = PASS
STACK_ALIGNMENT = PASS
CALL_CONVENTION_TESTS = PASS
Comando: py -m unittest tests.test_phase4 -v
Resultado: Ran 4 tests ... OK
```

El contrato está documentado en `ABI.md` y validado por `piton.abi`.
Todavía no existe emisión de ensamblador ni ejecutable nativo; eso pertenece a
la Fase 5.

FASE 5 — 2.2 → 2.5: PRIMER x86-64 REAL

Objetivo:  
Producir el primer ejecutable que no necesita Python.

Subconjunto inicial:  
• literales int/bool/Nada;  
• operaciones aritméticas básicas;  
• comparaciones;  
• variables locales;  
• si/sino;  
• mientras;  
• para sobre rango simple;  
• funciones simples;  
• devolver;  
• imprimir básico mediante runtime;  
• strings literales mínimas.

Pipeline:  
.piton → AST/HIR → MIR → x86-64 → objeto COFF → PE executable

Primero se puede emitir ensamblador NASM/MASM/LLVM-compatible si eso acelera la evidencia. La meta es código máquina real, no presumir que “emitir bytes a mano” es más puro.

Prueba reina:  
1\. Compilar hola.piton.  
2\. Mover el ejecutable a una VM limpia sin Python.  
3\. Ejecutarlo.  
4\. Inspeccionar imports del PE.  
5\. Verificar que no dependa de python\*.dll.  
6\. Desensamblar y conservar evidencia.  
7\. Comparar stdout/status contra el oracle CPython.

Gate:  
PITON\_X86\_HELLO \= PASS  
PITON\_X86\_BRANCH \= PASS  
PITON\_X86\_LOOP \= PASS  
PITON\_X86\_FUNCTION \= PASS  
PYTHON\_RUNTIME\_DEPENDENCY \= 0  
PE\_IMPORTS\_PYTHON \= 0  
DIFFERENTIAL\_NATIVE\_SUBSET \= PASS

Estado verificado el 2026-09-08:

```text
FASE_5_GATES = PASS (7/7)
PITON_X86_HELLO = PASS
PITON_X86_BRANCH = PASS
PITON_X86_LOOP = PASS
PITON_X86_FUNCTION = PASS
PYTHON_RUNTIME_DEPENDENCY = 0
PE_IMPORTS_PYTHON = 0
DIFFERENTIAL_NATIVE_SUBSET = PASS
Toolchain: NASM + MinGW GCC, Windows x64
Comando: py -m unittest tests.test_phase5 -v
Resultado: Ran 4 tests ... OK
```

El backend `piton.x86` emite NASM Win64, genera objeto COFF y enlaza un PE
ejecutable mediante el CRT de Windows. El subconjunto demostrado cubre
enteros, strings mínimos, variables locales, arithmetic, comparaciones,
branch, while, funciones simples, return e `imprimir`. Esto demuestra
`PITON_NATIVE_X86` para ese subconjunto, no paridad x86 completa.

Aquí puede declararse:  
PITON\_NATIVE\_X86 \= DEMONSTRATED

Pero todavía NO:  
PITON\_X86\_FULL\_PARITY

FASE 6 — 2.5 → 3.0: RUNTIME DE OBJETOS

Objetivo:  
Dejar de compilar sólo “programas de enteros” y empezar a soportar el modelo dinámico que hace Python reconocible.

Runtime mínimo:  
• Value;  
• object header;  
• type identity;  
• reference counting o GC documentado;  
• strings Unicode;  
• bytes;  
• arbitrary precision integers;  
• float;  
• tuple;  
• list;  
• dict;  
• set;  
• hashing;  
• equality;  
• truthiness;  
• iteration protocol;  
• slicing básico;  
• allocation/deallocation;  
• runtime errors.

Estrategia recomendada:  
Bootstrap del runtime en C, Rust o Zig si acelera la corrección. El objetivo de esta fase es eliminar CPython, no demostrar self-hosting.

Gate:  
NATIVE\_STR \= PASS  
NATIVE\_BIGINT \= PASS  
NATIVE\_LIST \= PASS  
NATIVE\_DICT \= PASS  
NATIVE\_SET \= PASS  
NATIVE\_ITERATION \= PASS  
MEMORY\_LIFETIME\_STRESS \= PASS  
DIFFERENTIAL\_OBJECT\_CORPUS \= PASS

Estado parcial verificado el 2026-09-08:

```text
OBJECT_MODEL_BOOTSTRAP = PASS
UNICODE_STR = PASS (byte-identical UTF-8 vs CPython oracle, 9/9 tests)
ARBITRARY_PRECISION_INT = PASS (14/14 Win64 + 13/13 Linux)
LIST_DICT_SET_TUPLE = PASS
HASH_EQUALITY_TRUTHINESS_ITERATION = PASS
REFERENCE_COUNT_LIFETIME = PASS
NATIVE_OBJECT_GATES = NOT_DEMONSTRATED
```

`piton.object_runtime` conserva el modelo y sus invariantes en Python para
pruebas. Los gates `NATIVE_*` y `DIFFERENTIAL_OBJECT_CORPUS` permanecen
abiertos hasta conectar estos objetos al backend x86 y al runtime nativo.

FASE 7 — 3.0 → 3.4: FUNCIONES REALES, CLOSURES Y EXCEPCIONES

Objetivo:  
Construir frames y control no local suficiente para programas serios.

Agregar:  
• parámetros posicionales;  
• keyword args;  
• defaults;  
• \*args;  
• \*\*kwargs;  
• closures;  
• cells;  
• recursion;  
• lambdas;  
• decorators;  
• exceptions;  
• try/except/finally;  
• raise;  
• chaining;  
• context managers;  
• generators;  
• yield/yield from;  
• iterators propios.

Necesidades de runtime:  
• frame objects o estructura equivalente;  
• stack maps si el GC los requiere;  
• unwind strategy;  
• traceback mapping a .piton.

Gate:  
NATIVE\_CALL\_SEMANTICS \= PASS  
NATIVE\_CLOSURES \= PASS  
NATIVE\_RECURSION \= PASS  
NATIVE\_EXCEPTIONS \= PASS  
NATIVE\_CONTEXT\_MANAGERS \= PASS  
NATIVE\_GENERATORS \= PASS  
TRACEBACK\_TO\_PITON \= PASS

Estado parcial verificado el 2026-09-08:

```text
BOOTSTRAP_CALL_BINDING = PASS
BOOTSTRAP_FRAMES_CELLS = PASS
BOOTSTRAP_RECURSION_SHAPE = PASS
BOOTSTRAP_CONTEXT_MANAGERS = PASS
BOOTSTRAP_GENERATORS = PASS
NATIVE_FUNCTION_GATES = NOT_DEMONSTRATED
```

`piton.call_runtime` implementa frames, defaults, keyword args, `*args`,
`**kwargs`, cells, binding de closures, context managers y generadores como
modelo bootstrap. Los gates `NATIVE_*` requieren conectar frames, unwind y
generadores al runtime x86; siguen abiertos.

FASE 8 — 3.4 → 3.8: CLASES Y PROTOCOLO DE OBJETOS

Objetivo:  
Cruzar uno de los jefes finales reales de la semántica Python.

Orden:  
1\. clases básicas;  
2\. instance dictionaries/slots equivalentes;  
3\. method binding;  
4\. attribute lookup;  
5\. inheritance;  
6\. MRO;  
7\. super();  
8\. properties;  
9\. descriptors;  
10\. classmethod/staticmethod;  
11\. \_\_getattr\_\_;  
12\. \_\_getattribute\_\_;  
13\. metaclasses.

No brincar directo a metaclasses.

Pruebas adversariales:  
• override de \_\_getattribute\_\_;  
• descriptor data vs non-data;  
• diamonds de herencia;  
• super() cooperativo;  
• shadowing de atributos;  
• properties que lanzan excepciones;  
• metaclass hooks.

Gate:  
NATIVE\_CLASSES \= PASS  
ATTRIBUTE\_LOOKUP \= PASS  
METHOD\_BINDING \= PASS  
MRO \= PASS  
DESCRIPTORS \= PASS  
GETATTRIBUTE \= PASS  
METACLASSES \= PASS\_LATER\_ALLOWED  
DIFFERENTIAL\_OBJECT\_PROTOCOL \= PASS

Estado parcial verificado el 2026-09-08:

```text
BOOTSTRAP_CLASSES = PASS
BOOTSTRAP_MRO_SUPER = PASS
BOOTSTRAP_METHOD_BINDING = PASS
BOOTSTRAP_DESCRIPTORS_PROPERTIES = PASS
NATIVE_OBJECT_PROTOCOL = NOT_DEMONSTRATED
```

`piton.object_protocol` cubre clases, instance dictionaries, binding, MRO,
`super`, properties y descriptors como modelo bootstrap. Metaclasses y la
integración nativa permanecen abiertos.

FASE 9 — 3.8 → 4.1: MÓDULOS, PAQUETES E IMPORTS NATIVOS

Objetivo:  
Que un proyecto Pitón completo pueda ejecutarse sin pasar por importlib de CPython.

Agregar:  
• módulos .piton;  
• paquetes;  
• relative imports;  
• circular imports;  
• \_\_name\_\_;  
• \_\_file\_\_;  
• \_\_package\_\_;  
• \_\_spec\_\_ equivalente donde aplique;  
• cache de módulos;  
• invalidación;  
• compilación incremental;  
• recursos del paquete.

Estrategia stdlib:  
• módulos puros compatibles pueden compilarse al IR Pitón;  
• funciones de sistema pueden vivir en runtime/native modules;  
• no intentar compatibilidad CPython C-API en la primera versión nativa;  
• FFI C explícita como carril separado.

Gate:  
NATIVE\_IMPORTS \= PASS  
NATIVE\_PACKAGES \= PASS  
CIRCULAR\_IMPORTS \= PASS  
MODULE\_CACHE \= PASS  
MULTIFILE\_NATIVE\_BUILD \= PASS

Estado parcial verificado el 2026-09-08:

```text
BOOTSTRAP_MODULE_CACHE = PASS
BOOTSTRAP_PACKAGE_METADATA = PASS
NATIVE_IMPORTS = NOT_DEMONSTRATED
MULTIFILE_NATIVE_BUILD = NOT_DEMONSTRATED
```

`piton.module_runtime` añade cache, invalidación y metadata de módulos para
pruebas. La ejecución nativa multifile aún no está implementada.

FASE 10 — 4.1 → 4.5: ASYNC, AWAIT Y CONCURRENCIA

Objetivo:  
Cubrir semántica asíncrona sin copiar por accidente decisiones internas de CPython que no son parte del contrato.

Agregar async def, await, async for, async with, async generators, coroutine objects, cancellation y propagación de excepciones.

Separar semántica del lenguaje, política de scheduler, threads y GIL/no-GIL. No prometer “igual a CPython” en detalles de implementación que Pitón no necesite copiar.

Gate:  
NATIVE\_ASYNC\_AWAIT \= PASS  
ASYNC\_ITERATION \= PASS  
ASYNC\_CONTEXT \= PASS  
CANCELLATION \= PASS  
DIFFERENTIAL\_ASYNC\_CORPUS \= PASS

Estado parcial verificado el 2026-09-08:

```text
BOOTSTRAP_ASYNC_ITERATION = PASS
BOOTSTRAP_ASYNC_CONTEXT = PASS
BOOTSTRAP_CANCELLATION = PASS
NATIVE_ASYNC_GATES = NOT_DEMONSTRATED
```

`piton.async_runtime` separa coroutine execution, async iteration, async
context managers y cancellation de las decisiones del scheduler. La conexión
al runtime x86 queda abierta.

FASE 11 — 4.5 → 5.0: DINAMISMO DIFÍCIL

Objetivo:  
Atacar las superficies que vuelven a Python muy dinámico y muy difícil de clonar.

Casos:  
• eval;  
• exec;  
• compile;  
• dynamic imports;  
• inspect;  
• reload;  
• pickle;  
• multiprocessing;  
• spawn en Windows;  
• traceback;  
• source retrieval;  
• code objects;  
• annotations dinámicas.

Regla:  
No interceptar cadenas arbitrarias como Pitón silenciosamente.

APIs explícitas:  
• piton.eval\_piton();  
• piton.compile\_piton();  
• piton.exec\_piton().

Gate:  
EXPLICIT\_DYNAMIC\_COMPILE \= PASS  
NO\_HIDDEN\_TRANSLATION \= PASS  
WINDOWS\_SPAWN \= PASS  
SOURCE\_INSPECTION\_CONTRACT \= PASS  
DYNAMIC\_CORPUS \= PASS

Estado parcial verificado el 2026-09-08:

```text
EXPLICIT_DYNAMIC_COMPILE = PASS
NO_HIDDEN_TRANSLATION = PASS
SOURCE_INSPECTION_CONTRACT = PASS
BOOTSTRAP_DYNAMIC_IMPORTS = PASS
WINDOWS_SPAWN = NOT_DEMONSTRATED
PICKLE_MULTIPROCESSING = NOT_DEMONSTRATED
```

Las APIs explícitas viven en `piton.runtime`: `compile_piton`, `eval_piton`,
`exec_piton` e `inspect_piton_source`. No se añadió traducción implícita de
cadenas arbitrarias.

FASE 12 — 5.0 → 5.5: STDLIB Y FFI

Objetivo:  
Hacer que Pitón nativo sea útil fuera de benchmarks.

Prioridad:  
• sys;  
• os;  
• pathlib equivalente;  
• math;  
• time;  
• json;  
• collections básicas;  
• io;  
• filesystem;  
• subprocess con contrato seguro;  
• sockets;  
• threading/concurrency según diseño;  
• FFI C.

No traducir automáticamente nombres de la stdlib al español. Eso debe seguir siendo una capa opcional y explícita.

Compatibilidad con extensiones CPython:  
Carril separado. No es requisito para PITON\_NATIVE\_X86. Una extensión que exige PyObject y CPython C-API puede requerir shim, recompilación o quedar fuera.

Gate:  
NATIVE\_STDLIB\_CORE \= PASS  
FILE\_IO \= PASS  
NETWORK\_IO \= PASS  
C\_FFI \= PASS  
NO\_CPYTHON\_RUNTIME\_LINK \= PASS

Estado parcial verificado el 2026-09-08:

```text
BOOTSTRAP_STDLIB_CORE = PASS
BOOTSTRAP_SAFE_SUBPROCESS = PASS
BOOTSTRAP_EXPLICIT_FFI = PASS
NATIVE_STDLIB_CORE = NOT_DEMONSTRATED
FILE_IO = NOT_DEMONSTRATED
NETWORK_IO = NOT_DEMONSTRATED
C_FFI_NATIVE = NOT_DEMONSTRATED
NO_CPYTHON_RUNTIME_LINK = NOT_DEMONSTRATED
```

`piton.stdlib_runtime` expone módulos seleccionados, subprocess sin shell y
una superficie FFI explícita basada en `ctypes`. Esto no afirma todavía un
runtime nativo independiente de CPython.

FASE 13 — 5.5 → 6.0: OPTIMIZACIÓN SIN CAMBIAR SEMÁNTICA

Objetivo:  
Después de ser correcto, dejar de ser lento.

Posibles capas:  
• SSA;  
• constant folding;  
• dead-code elimination;  
• basic-block simplification;  
• inlining;  
• escape analysis;  
• unboxing especulativo;  
• tagged integer fast paths;  
• inline caches de atributos;  
• polymorphic inline caches;  
• specialized calls;  
• allocation sinking;  
• string fast paths.

Regla:  
Cada optimización debe poder apagarse y \--opt=0 / \--opt=2 deben conservar semántica dentro del contrato.

Gate:  
OPT\_LEVEL\_EQUIVALENCE \= PASS  
MISCOMPILATION\_CORPUS \= 0  
PERF\_BASELINE\_RECORDED \= PASS

Estado parcial verificado el 2026-09-08:

```text
BOOTSTRAP_OPT_LEVEL_EQUIVALENCE = PASS
BOOTSTRAP_CONSTANT_FOLDING = PASS
OPT_LEVEL_EQUIVALENCE = NOT_DEMONSTRATED
MISCOMPILATION_CORPUS = NOT_DEMONSTRATED
PERF_BASELINE_RECORDED = NOT_DEMONSTRATED
```

`piton.optimizer.optimize_mir` soporta niveles 0, 1 y 2. El nivel 0 clona
sin optimizar y los niveles superiores aplican constant folding; todavía no
es un optimizador nativo completo ni tiene corpus de rendimiento.

FASE 14 — 6.0+: PARIDAD NATIVA AMPLIA

Objetivo:  
Convertir la diferencia entre backend Python y backend x86 en una lista finita de huecos documentados.

Dashboard por feature:  
syntax, expressions, statements, functions, closures, exceptions, generators, classes, descriptors, metaclasses, imports, async, stdlib, dynamic code, introspection, multiprocessing y FFI.

Estados:  
PASS  
PARTIAL  
NOT\_DEMONSTRATED  
INTENTIONALLY\_UNSUPPORTED  
DESTROYED

No esconder huecos.

Gate final para una release “x86 parity”:  
GRAMMAR\_PARITY \= PASS  
SEMANTIC\_DIFFERENTIAL\_CORPUS \= PASS  
NATIVE\_RUNTIME \= PASS  
NATIVE\_IMPORT\_SYSTEM \= PASS  
NATIVE\_OBJECT\_PROTOCOL \= PASS  
NATIVE\_EXCEPTION\_MODEL \= PASS  
NATIVE\_ASYNC \= PASS  
NATIVE\_STDLIB\_DECLARED\_SCOPE \= PASS  
CPYTHON\_EXECUTION\_DEPENDENCY \= 0  
X86\_64\_WINDOWS \= PASS  
X86\_64\_LINUX \= PASS  
CLEAN\_MACHINE\_EXECUTION \= PASS

PRUEBA DE INDEPENDENCIA

Para cualquier milestone nativo importante, conservar evidencia reproducible:  
1\. Build source hash.  
2\. Pitón source hash.  
3\. MIR hash.  
4\. Assembly/object hash.  
5\. Executable hash.  
6\. Toolchain versions.  
7\. PE/ELF import table.  
8\. Disassembly excerpt.  
9\. Run on machine/VM without Python installed.  
10\. Differential run against CPython oracle.  
11\. Exact stdout/stderr/status.  
12\. Test report.

Veredictos separados:  
X86\_CODE\_EMITTED \= PASS  
EXECUTABLE\_LINKED \= PASS  
RUNS\_WITHOUT\_PYTHON \= PASS  
NATIVE\_SEMANTIC\_SUBSET \= PASS  
FULL\_PARITY \= NOT\_DEMONSTRATED hasta que de verdad lo esté.

Estado final de Fase 14 verificado el 2026-09-08:

```text
PHASE14_DASHBOARD = PASS
FEATURE_GAP_LIST = PASS
FAIL_CLOSED_FINAL_GATE = PASS
GRAMMAR_PARITY = PASS
SEMANTIC_DIFFERENTIAL_CORPUS = PARTIAL
NATIVE_RUNTIME = PARTIAL
NATIVE_IMPORT_SYSTEM = PARTIAL
NATIVE_OBJECT_PROTOCOL = PARTIAL
NATIVE_EXCEPTION_MODEL = PARTIAL
NATIVE_ASYNC = PARTIAL
NATIVE_STDLIB_DECLARED_SCOPE = PARTIAL
CPYTHON_EXECUTION_DEPENDENCY = PARTIAL
X86_64_WINDOWS = PARTIAL
X86_64_LINUX = PARTIAL
CLEAN_MACHINE_EXECUTION = PASS
FULL_PARITY = NOT_DEMONSTRATED
```

`piton.final_dashboard` es el cierre reproducible de la fase. Enumera las 17
áreas de compatibilidad, conserva evidencia por área y devuelve código no cero
si la release x86 parity no está demostrada. La Fase 14 queda cerrada como
`PARTIAL`, no como una afirmación falsa de paridad nativa.

Avance nativo verificado el 2026-09-08:

```text
NATIVE_BOOL_NONE_PRINT = PASS
NATIVE_STRING_COMPARISON_SUBSET = PASS
NATIVE_INTEGER_FLOORDIV_MODULO = PASS
NATIVE_UNARY_INTEGER_OPS = PASS
NATIVE_BITWISE_INTEGER_OPS = PASS
NATIVE_DIFFERENTIAL_SUBSET = PASS
NATIVE_TRUE_DIVISION = NOT_DEMONSTRATED
NATIVE_STRING_CONCAT_SUBSET = PASS
NATIVE_INT_COLLECTIONS_SUBSET = PASS
NATIVE_COLLECTION_HEAP_CLEANUP = PASS
NATIVE_FLOAT_ARITHMETIC_SUBSET = PASS
NATIVE_BIGINT_LITERAL_SUBSET = PASS
NATIVE_IMMUTABLE_CLOSURE_SUBSET = PASS
NATIVE_TYPED_RAISE_CATCH_SUBSET = PASS
NATIVE_FINITE_GENERATOR_SUBSET = PASS
NATIVE_SIMPLE_CLASS_SUBSET = PASS
NATIVE_MULTIFILE_MODULE_SUBSET = PASS
NATIVE_NONSUSPENDING_ASYNC_SUBSET = PASS
NATIVE_MATH_SQRT_SUBSET = PASS
NATIVE_UNICODE_UTF8_BYTEIDENTICAL = PASS (9/9 Linux ELF tests, CPython 3.12.4 oracle)
LINUX_X86_64_STATIC_ELF_SUBSET = PASS
LINUX_EMPTY_ENV_EXECUTION = PASS
LINUX_EMPTY_CHROOT_EXECUTION = PASS
QEMU_ONLY_USERSPACE_EXECUTION = PASS
```

`piton.native_differential` compara directamente el PE generado con CPython
3.12.4. El backend ahora rechaza operaciones no implementadas en vez de emitir
semántica aproximada. La matriz viva está en `NATIVE_COMPATIBILITY.md`.

ESTRUCTURA DE REPO RECOMENDADA

PITON/  
  spec/  
  piton/frontend/  
  piton/cst/  
  piton/ast/  
  piton/analysis/  
  piton/hir/  
  piton/mir/  
  piton/diagnostics/  
  backends/python/  
  backends/x86\_64/windows/  
  backends/x86\_64/sysv/  
  runtime/core/  
  runtime/objects/  
  runtime/memory/  
  runtime/exceptions/  
  runtime/imports/  
  runtime/io/  
  stdlib/  
  tests/syntax/  
  tests/differential/  
  tests/native/  
  tests/adversarial/  
  tests/runtime/  
  tests/x86/  
  tools/disasm/  
  tools/corpus/  
  tools/oracle/  
  examples/  
  evidence/

No crear 80 carpetas desde el día uno. Esta estructura es destino, no plantilla inicial obligatoria.

ORDEN DE IMPLEMENTACIÓN DEL BACKEND x86

1\. return constante.  
2\. arithmetic int.  
3\. compare.  
4\. branch.  
5\. loop.  
6\. locals.  
7\. calls.  
8\. stack frames.  
9\. strings.  
10\. tagged values.  
11\. heap allocation.  
12\. lists/tuples.  
13\. dict/set.  
14\. exceptions.  
15\. closures.  
16\. generators.  
17\. classes.  
18\. attributes.  
19\. descriptors/MRO.  
20\. imports.  
21\. async.  
22\. dynamic code.

Cada peldaño mantiene un corpus diferencial mínimo antes de subir al siguiente.

QUÉ NO CONFUNDIR

Native no significa self-hosted.  
Pitón puede generar x86 aunque el compilador esté escrito en Python, Rust o cualquier otro lenguaje.

Self-hosted no significa compatible.  
Reescribir el compilador en Pitón no demuestra paridad semántica.

x86 no significa rápido.  
Primero evidencia de corrección; después optimización.

Compatible no significa idéntico a CPython internamente.  
El contrato es comportamiento observable declarado, no copiar cada estructura interna de CPython.

“Funciona en mis ejemplos” no significa paridad.  
El corpus diferencial manda.

SELF-HOSTING — OPCIONAL Y MUCHO DESPUÉS

Sólo considerar cuando backend x86, runtime e imports sean estables y el frontend del compilador esté cubierto por el propio lenguaje.

Ruta:  
compilador Pitón escrito en lenguaje host → compila compilador.piton → compilador Pitón nativo → recompila su propia fuente → comparar comportamiento y artefactos bajo un criterio definido.

Gate:  
PITON\_COMPILER\_RUNS\_ON\_PITON \= PASS  
STAGE2\_EQ\_STAGE3 \= PASS bajo criterio definido

No es requisito para declarar PITON\_NATIVE\_X86.

MILESTONES RESUMIDOS

Pitón 1.0 — frontend español con paridad declarada y CPython como runtime.  
Pitón 1.5 — parser/AST/scopes propios; Python ya es backend, no definición del frontend.  
Pitón 2.0 — HIR/MIR canónicos y oracle diferencial estable.  
Pitón 2.5 — primer ejecutable Windows x86-64 sin Python para subconjunto útil.  
Pitón 3.0 — runtime dinámico propio: strings, big ints, listas, dicts, iteración y exceptions.  
Pitón 3.5 — closures, generators y protocolo de objetos avanzado.  
Pitón 4.0 — clases/MRO/descriptors/imports nativos y builds multifile.  
Pitón 4.5 — async y dinamismo difícil.  
Pitón 5.0 — stdlib nativa útil \+ FFI \+ proyectos reales sin CPython.  
Pitón 5.5 — optimización y backend x86 maduro.  
Pitón 6.0+ — cierre progresivo de huecos de paridad con el oracle congelado.

CRITERIO DE CIERRE REAL

Pitón x86 se considera real cuando:  
programa.piton → compilador Pitón → x86-64 → PE/ELF → CPU

y una máquina sin Python puede ejecutar el binario mientras el corpus diferencial demuestra que el subconjunto reclamado conserva la semántica observada del oracle.

La frase correcta será:  
“Pitón tiene un backend x86-64 nativo para este alcance demostrado.”

No:  
“Reimplementamos Python completo.”

hasta que la evidencia lo permita.

ÚLTIMA REGLA

No convertir el roadmap en obligación de terminarlo.

Cada versión debe ser útil por sí misma. Cada capa removida debe dejar un artefacto mejor medido que antes. Si una fase revela que una suposición estaba mal:  
DESTROYED \> narrativa bonita.

Pitón empezó como una forma de leer Python sin pelearte con el inglés. Si termina hablando directamente con x86, que sea porque cada capa intermedia se ganó el derecho de desaparecer.  
