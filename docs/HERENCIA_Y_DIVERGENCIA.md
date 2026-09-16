# PITÓN — Herencia y Divergencia

> Define qué se hereda de CPython 3.12.4 y qué no, y por qué.
> Es la precondición de `ROADMAP_100_PERCENT_MILESTONES.md`: sin este ledger,
> "100% de paridad" no es una meta falsable, es una promesa de clonar accidentes.
> Complementa `PARITY_DEFINITION.md`.

## 0. El problema que resuelve

El roadmap actual mide una sola cosa: paridad con CPython. Pero "paridad" son
dos afirmaciones distintas que hoy están colapsadas en una:

- **Paridad de capacidad (techo):** todo lo que se puede expresar en CPython se
  puede expresar en Pitón. Nada queda prohibido.
- **Paridad de comportamiento (forma):** Pitón hace lo mismo *de la misma
  manera*, incluyendo cuándo libera memoria, en qué orden evalúa y qué expone
  del frame.

**Pitón declara paridad de capacidad. No declara paridad de comportamiento.**

La razón no es pereza: la lentitud de CPython no es un defecto separable de su
semántica. Es, en buena parte, el precio de su dinamismo. Copiar la forma es
copiar el precio. La única salida es separar qué parte del comportamiento es
**contrato del lenguaje** y qué parte es **accidente de implementación**.

Regla operativa: **un accidente sólo se puede no heredar si se declara antes de
implementarlo.** Declarar una divergencia es gratis. Revertir una divergencia ya
implementada es un rewrite.

## 1. Las dos preguntas por feature

Para cada comportamiento observable de CPython:

1. ¿Un programa Python correcto **puede depender de esto** según la referencia
   del lenguaje? → contrato, **se hereda**.
2. ¿Depende de cómo CPython está construido por dentro? → accidente,
   **se evalúa** contra su costo estructural.

Un accidente se hereda sólo si su costo es bajo. Si su costo es cerrar una
puerta arquitectónica, se declara `INTENTIONALLY_DIVERGENT`.

## 2. Ledger A — SE HEREDA (contrato observable, no negociable)

| # | Comportamiento | Por qué es contrato |
|---|---|---|
| A1 | Orden de evaluación izquierda-a-derecha en expresiones y argumentos | Garantizado por la referencia del lenguaje; observable vía efectos |
| A2 | Cortocircuito de `y` / `o`, evaluación perezosa de operandos | Contrato; el operando derecho puede tener efectos |
| A3 | `int` de precisión arbitraria; `float` IEEE-754 binary64 | Contrato de valores |
| A4 | Floor division y módulo con el signo de Python (no el de C) | Contrato aritmético; difiere de C a propósito |
| A5 | Aliasing y visibilidad inmediata de mutación | Es el modelo de objetos |
| A6 | MRO C3, descriptors data vs non-data, `__getattr__` sólo tras fallo de lookup | Protocolo de atributos |
| A7 | Tipo, mensaje y **punto de lanzamiento** de excepciones relativo a efectos observables | Contrato; define qué salió por stdout antes de fallar |
| A8 | `__cause__`, `__context__`, orden de `finally` y unwind | Causalidad observable |
| A9 | Puntos de suspensión exactos de generadores y corrutinas | Define qué se ejecutó antes de ceder control |
| A10 | Orden de iteración de `dict` = orden de inserción | Contrato desde Python 3.7 |
| A11 | Identidad (`es`) de objetos con identidad real (mutables, instancias) | Contrato |
| A12 | Orden e intercalado de escrituras a stdout/stderr | Es el oracle diferencial |

Todo lo del Ledger A entra en el corpus diferencial y falla el gate si diverge.

## 3. Ledger B — NO SE HEREDA (accidentes con costo estructural)

| # | Accidente de CPython | Qué cuesta heredarlo | Decisión |
|---|---|---|---|
| B1 | **Destrucción determinista por refcount:** `__del__` corre en el instante exacto del último decref | **Convierte el último uso de todo objeto en un efecto observable.** Con eso ninguna operación puede reordenarse ni eliminarse. Cierra dataflow, DCE, escape analysis y unboxing de forma permanente | `INTENTIONALLY_DIVERGENT` |
| B2 | GIL y atomicidad implícita de operaciones de bytecode (`lista.append` atómico entre hilos) | Fija un modelo de concurrencia que Pitón no necesita y que ni CPython 3.13+ garantiza bajo free-threading | `INTENTIONALLY_DIVERGENT` |
| B3 | `locals()` como espejo vivo y escribible del frame | Cada escritura a una variable local se vuelve observable → sin registros, sin SSA útil, sin scheduling | `INTENTIONALLY_DIVERGENT` (ver B3-bis) |
| B4 | `sys._getframe()` universal sobre cualquier función | Obliga a materializar todo frame siempre, aunque nadie lo pida | `GUARDED` — ver §5.2 |
| B5 | Interning de enteros pequeños y strings (`5 es 5` → Verdadero) | La referencia del lenguaje dice explícitamente que no se debe depender de esto | `INTENTIONALLY_DIVERGENT` |
| B6 | `id()` como dirección de memoria | Impide mover objetos; cierra cualquier GC compactador a futuro | `INTENTIONALLY_DIVERGENT` — `id()` es identidad opaca estable |
| B7 | Tracebacks que mantienen frames vivos indefinidamente | Extensión de lifetime observable; interactúa con B1 | `INTENTIONALLY_DIVERGENT` |
| B8 | Layout de `PyObject`, bytecode, `.pyc`, C-API | Ya declarado fuera de alcance en el roadmap actual | `OUT_OF_SCOPE` (sin cambio) |

**B3-bis — la sustitución de `locals()`:** devuelve un *snapshot* en el momento
de la llamada; escribir en el dict no modifica el frame. Es precisamente lo que
CPython adoptó en PEP 667, así que esta divergencia contra 3.12.4 converge con
CPython moderno en vez de alejarse de él.

**B1-bis — la sustitución de `__del__`:** el contrato pasa a ser *"`__del__` se
ejecuta exactamente una vez antes de que el programa termine"*, no *"en el punto
del último decref"*. La limpieza determinista se obtiene con `con` (context
managers), que sí es contrato y que la documentación de CPython ya recomienda
para esto. Es la postura de PyPy, y es defendible porque el propio CPython
documenta el refcounting como detalle de implementación.

## 4. Estado nuevo del vocabulario

A los estados existentes (`PASS`, `PARTIAL`, `NOT_DEMONSTRATED`,
`INTENTIONALLY_UNSUPPORTED`, `DESTROYED`) se agrega:

- **`INTENTIONALLY_DIVERGENT`** — implementado, funcional, y *deliberadamente
  distinto* a CPython en un punto declarado en el Ledger B. Requiere: entrada en
  este documento, test que **fija la divergencia** (falla si accidentalmente
  converge, o si diverge de forma distinta a la declarada) y justificación
  estructural.

Una divergencia sin test que la fije es un bug, no una decisión.

## 5. La arquitectura que esto habilita

El Ledger B no es una lista de renuncias: es el permiso arquitectónico para el
sello de Pitón.

### 5.1 Effect token

El MIR se ejecuta como **grafo de dependencias**, no como lista de instrucciones
en orden de programa. Dos tipos de arista:

- **Aristas de datos** — conectan productor y consumidor de un valor. Los nodos
  puros flotan: se reordenan, se hunden, se eliminan si nadie los usa, se
  paralelizan.
- **Arista de efecto** — un token único encadenado por cada nodo con efecto
  observable (`imprimir`, mutación, `raise`, llamada opaca, I/O). Ese hilo impone
  orden secuencial **sólo donde el Ledger A lo exige**.

Con esto la equivalencia observable con CPython sale **por construcción**: el
grafo no puede reordenar un efecto porque el token lo encadena. El corpus
diferencial deja de ser lo que *descubre* divergencias y pasa a ser lo que
*confirma* una propiedad estructural.

Sin B1, este mecanismo no existe: con destrucción por refcount *todo* nodo toca
el token, y el grafo degenera exactamente en la lista secuencial original.

### 5.2 Guards y deoptimización

El Ledger A conserva el dinamismo completo, así que el grafo no puede ser
estático: cualquier atributo puede ser reemplazado en runtime.

Solución: el grafo se compila **bajo supuestos explícitos** ("este valor es int
inmediato", "esta clase no fue parchada", "esta función no pide su frame"). Un
nodo **guard** verifica el supuesto barato; si falla, **deoptimiza** al camino
genérico lento.

Consecuencia exacta de lo que pide el §0: el techo sigue siendo CPython —si el
programa hace la locura dinámica, el guard falla y cae al slow path, que
funciona— pero no se paga el costo cuando la locura no ocurre. Es el mecanismo
de PyPy, LuaJIT y TurboFan.

`sys._getframe` (B4) se resuelve así: es `GUARDED`, no divergente. Una función
que lo alcanza deoptimiza y materializa su frame. Capacidad intacta, costo
localizado en quien lo usa.

### 5.3 Por qué el orden importa

El effect token es **aditivo y barato hoy**: `MIRInstruction` es un dataclass
frozen de `(op, args, result)`; agregar un campo `effects` con valor por defecto
no rompe ningún backend. Hacerlo después de M20 es reescribir todos los pases y
ambos backends.

**Gotcha concreto:** `MIRInstruction.to_dict()` alimenta la serialización JSON de
la que dependen `MIR_DETERMINISTIC` y `MIR_TRACE_STABLE`. Agregar el campo cambia
el hash del MIR. Requiere rebaseline explícito del corpus, no un fix silencioso.

## 6. Milestones nuevos

### ME — Effect lattice en MIR

**Objetivo:** clasificar el efecto de cada op del MIR y encadenar el token. No
cambia semántica: es un anotador.

**Gates:** `EFFECT_CLASSIFICATION_V1` (toda op del MIR clasificada como `PURE`,
`READ`, `WRITE`, `IO` u `OPAQUE`; op nueva sin clasificar = fail-closed),
`EFFECT_TOKEN_CHAIN_V1` (token encadenado y verificable),
`EFFECT_NEUTRALITY_V1` (**la suite diferencial completa pasa idéntica antes y
después: mismo stdout, mismo exit code**), `MIR_HASH_REBASELINE_V1`.

**Claim falsable:** anotar efectos no cambia ni un byte de salida.

**Dependencias:** M1. **Va antes de M2** — así cada op nueva nace clasificada.

### MG — Especulación, guards y deopt

**Objetivo:** conservar el techo dinámico sin pagarlo siempre.

**Gates:** `GUARD_NODE_V1`, `DEOPT_CORRECTNESS_V1` (deoptimizar en cualquier
punto produce el resultado del camino genérico), `TYPE_FEEDBACK_V1`,
`INLINE_CACHE_V1`, `GUARD_ADVERSARIAL_V1` (corpus que **fuerza** el fallo de cada
guard: monkeypatching, `__getattribute__` sobrescrito, tipos mixtos en el mismo
sitio de llamada).

**Claim falsable:** para todo programa del corpus, resultado con guards =
resultado con `--opt=0`.

**Dependencias:** ME, M6.

### MD — Scheduler dataflow

**Objetivo:** el sello. Ejecutar el MIR por dependencias de datos.

**Gates:** `DATAFLOW_SCHEDULE_V1`, `DATAFLOW_EQUIVALENCE_V1` (corpus completo
byte-idéntico contra el orden secuencial), `DATAFLOW_DETERMINISM_V1` (misma
entrada → misma salida, aunque el orden interno de ejecución cambie),
`DATAFLOW_PERF_BASELINE_V1` (número medido, no anécdota).

**Dependencias:** ME, MG, y la resolución de M13 (§7).

## 7. Impacto en milestones existentes

- **M13 — el conflicto crítico.** `LIFETIME_OBSERVABILITY_V1` como está escrito
  *es* B1, y clausura MD de forma permanente. Se reemplaza por
  `LIFETIME_CONTRACT_V1` (`__del__` corre exactamente una vez antes de terminar;
  `con` es el mecanismo determinista) marcado `INTENTIONALLY_DIVERGENT`.
  `GC_CYCLES_V1` y `WEAKREFS_V1` siguen igual.
  **La declaración es hoy; la implementación sigue en M13.**
- **M12.** `GLOBALS_LOCALS_V1` adopta B3-bis (snapshot). `sys._getframe` queda
  `GUARDED`, dependiente de MG.
- **M16.** `CONCURRENCY_DETERMINISM_V1` no puede prometer atomicidad estilo GIL
  (B2). El determinismo se declara sobre el modelo de concurrencia de Pitón.
- **M5 / M9.** Sin cambio: los puntos de suspensión son A9, contrato puro. Los
  nodos de suspensión se clasifican `OPAQUE` en el lattice.
- **M20.** El checklist agrega: *toda entrada del Ledger B tiene test que la
  fija*. Sin eso no hay cierre.

## 8. Qué NO promete este documento

No promete velocidad. El dataflow no acelera lo que ya es una llamada al runtime
C (`piton_dict_*`, `piton_str_*`): esos nodos son `OPAQUE`, viven en el hilo de
efectos y nunca iban a flotar. La ganancia esperada está en código compute-bound
con tipos conocidos, y **no tiene número hasta que `DATAFLOW_PERF_BASELINE_V1` lo
mida**.

Cualquier múltiplo citado antes de ese gate es anécdota. En particular, los
múltiplos observados en cargas I/O-bound **no se transfieren**: ahí el dataflow
gana solapando latencia ociosa, mecanismo que no existe en una carga CPU-bound.

La afirmación honesta al cierre es:

> "Pitón tiene paridad de capacidad con CPython 3.12.4 para el alcance declarado,
> con N divergencias de comportamiento documentadas y fijadas por tests."

No:

> "Pitón es 100% compatible con Python."
