# PITÓN — Definición de Paridad (CPython 3.12)

> Autoridad de definición. Si un gate o afirmación contradice este archivo,
> este archivo manda. Versión del oracle: **CPython 3.12.4** (ver `ORACLE.md`).

## 1. Por qué existe este documento

El antiguo rótulo `FULL_PARITY` era ambiguo y accidentalmente confundía dos
cosas distintas:

- A. paridad **completa dentro del subconjunto nativo declarado** de PITÓN, y
- B. paridad **semántica completa con CPython 3.12**.

`FULL_PARITY` queda **retirado como término**. No vuelve a usarse como gate.
En su lugar se definen gates de paridad con alcance explícito.

## 2. Superficies de paridad (gates de alto nivel)

| Gate | Alcance |
|---|---|
| `PITON_NATIVE_SUBSET_PARITY` | El subconjunto nativo **declarado** se comporta idéntico al oracle, en Windows y Linux, sin CPython en el ejecutable. |
| `CPYTHON_3_12_LANGUAGE_PARITY` | Semántica del lenguaje Python 3.12 (sintaxis + modelo de valores + object model + funciones/frames + excepciones + imports + async) según la definición de la §3. |
| `CPYTHON_3_12_STDLIB_PARITY` | Módulos de la stdlib que PITÓN declara soportar (matriz en `STDLIB_PARITY_MATRIX.md`). |
| `CPYTHON_3_12_RUNTIME_PARITY` | Comportamiento de runtime observable: alocación, lifetime, GC/finalizadores/weakrefs donde sean semánticamente observables. |
| `CPYTHON_3_12_PLATFORM_PARITY` | Windows x86-64 y Linux x86-64: ejecución limpia, formato ejecutable, calling convention, sin dependencias dinámicas de CPython. |
| `CPYTHON_3_12_COMPLETE_PARITY` | Composición explícita (definida en §3) de todas las superficies aplicables. **No** es un gate independiente oculto. |

Regla general:

```
GENERATORS_V1 = PASS   NO implica   CPYTHON_GENERATORS_COMPLETE = PASS
```

Toda afirmación de paridad declara su superficie. Ver `FEATURE_STATUS_MATRIX.md`
para el mapeo feature → gate → plataforma → estado.

## 3. Definición operativa de `CPYTHON_3_12_COMPLETE_PARITY`

Sería `PASS` **si y sólo si** todas las superficies aplicables cierran:

1. **LANGUAGE_SEMANTICS** — el corpus diferencial de lenguaje (grammar, valores,
   object model, funciones/frames, excepciones, imports, async) coincide con el
   oracle en comportamiento observable.
2. **STANDARD_LIBRARY** — cada módulo de la stdlib declarado en
   `STDLIB_PARITY_MATRIX.md` pasa su corpus diferencial.
3. **RUNTIME_BEHAVIOR** — comportamiento de memoria/GC observado desde el
   programa coincide donde es semánticamente observable.
4. **PLATFORM_SUPPORT** — Windows y Linux (según lo declarado por cada gate).
5. **C_EXTENSION_COMPATIBILITY** — **carril separado**; no es requisito para
   paridad de lenguaje. Ver §5.
6. **IMPLEMENTATION_SPECIFIC_DETAILS** — explícitamente NO-goals (§5) salvo que
   un gate lo declare.

Cada superficie de la composición es un gate con sus propios tests. La
composición **no** se aprueba automáticamente por la suma de sus partes; exige
sus propios tests de integración.

## 4. Reglas de composición (fail-closed)

- `A=PASS` y `B=PASS` **no** implican `C=composition(A,B)=PASS`. `C` requiere
  sus propios tests de interacción.
- El dashboard deriva todo estado agregado de gates declarados; nunca promueve
  un gate amplio porque muchos tests estrechos están verdes.
- Ejemplo: `GENERATOR_NEXT=PASS`, `GENERATOR_SEND=PASS`, `GENERATOR_THROW=PARTIAL`
  ⇒ `GENERATORS_COMPLETE=PARTIAL`, no PASS.

## 5. NO-goals explícitos (no son requisito de paridad)

Para no hacer "paridad completa" matemáticamente imposible, **no** se exige:

- layout binario idéntico a `PyObject`;
- compatibilidad de bytecode CPython;
- compatibilidad de `.pyc`;
- CPython C API / extensión ABI como requisito de lenguaje;
- cadenas de error byte-idénticas a CPython (se compara semántica estructurada
  salvo que un gate declare texto);
- comportamiento exacto de refcounting / GC timing;
- introspección de implementación interna de CPython.

## 6. Qué significa "idéntico" en un test diferencial

Se comparan, según aplique y como declare cada gate:

- exit code;
- stdout;
- stderr normalizado (con reglas de normalización documentadas, sin que la
  normalización haga pasar algo que falla);
- tipo y mensaje de excepción;
- cadena causal (`from`/`__cause__`);
- valor observable serializable;
- efectos externos controlados (filesystem en sandbox, imports cargados);
- orden observable cuando el lenguaje lo garantiza.

**No hay normalización oculta.** Toda regla de normalización se documenta en el
corpus correspondiente.

## 7. Oracle

- Oracle congelado: **CPython 3.12.4** (Windows 10/11 x64; Linux WSL).
- Cambiar de oracle requiere decisión explícita y rebaseline completo del corpus.
- Si el comportamiento difiere entre patch releases de 3.12, se registra por
  separado. No se prueba contra "cualquier `python` del PATH" silenciosamente.