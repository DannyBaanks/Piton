# VERDICT — PITON × AUTOPITON Parity Attack Cycle V1

## Resumen Ejecutivo (`[DEMONSTRATED]`)

El ciclo **PARITY ATTACK CYCLE V1** se completó cumpliendo todos los gates técnicos y científicos (Gates 0 a 5):

1. **Gate 0 (Reconciliación real):** Los 58 casos no-`EQUIV` históricos de `autopiton/cases/index.json` se reconciliaron mediante `replay` real antes de editar código (`50 EQUIV`, `3 INVALID_REFERENCE`, `5` contraejemplos activos).
2. **Gate 1 (Cinco casos históricos resueltos por causa raíz):**
   - **A1 (`case-228d90a490532bbb`, `{**a, **b}`):** `UNSUPPORTED` → **`EQUIV`**.
   - **A2 (`case-52ec2e43a5d86c64`, `zip()` 3+ iterables):** `UNSUPPORTED` → **`EQUIV`**.
   - **A3 (`case-efd722252f2ec8ff`, `super().f(*args)`):** `UNSUPPORTED` → **`EQUIV`**.
   - **A4 (`case-4d25af23ed64826d`, `class A: pass; A(0)`):** `INVALID_ACCEPTED` → **`EQUIV`** (`TypeError: A() takes no arguments`).
   - **A5 (`case-707d1042403c5de2`, `class A: pass; A(0)`):** `INVALID_ACCEPTED` → **`EQUIV`** (`TypeError: A() takes no arguments`).
   - Las 10 variantes de generalización y controles negativos de A1–A5 clasificaron **`10/10 EQUIV`**.
3. **Gate 2 (Expansión diferencial real):** Se añadieron 5 plantillas acotadas de alto valor semántico en `autopiton/generator.py` (`mutable_container_unpack`, `generator_yield_try`, `dunder_operator_class`, `nonlocal_mutable_closure`, `try_finally_return_flow`). Las campañas descubrieron 4 nuevas divergencias reales de propagación de tipos en closures y `@property`, que fueron diagnosticadas y corregidas hasta obtener:
   - `seed=11` (30 casos): **`30/30 EQUIV`**
   - `seed=23` (40 casos): **`40/40 EQUIV`**
   - `seed=99` (30 casos): **`30/30 EQUIV`**
   - `seed=77` (30 casos, semilla no vista): **`30/30 EQUIV`**
   - Reconciliación final de los 62 casos no-`EQUIV` registrados en `index.json`: **`59 EQUIV` + `3 INVALID_REFERENCE` (`0` fallos pendientes)**.
4. **Gate 3 (Antifake y Controles de Mutación):** Los 5 controles de mutación sobre A1–A5 y el control mutado de síntesis sobre `bisect` (`--control`) fueron detectados por el oráculo (`MUTATION_CONTROLS.json`).
5. **Gate 4 (Stdlib honesta):** Se documentó el estado etapa por etapa (`ADAPT`, `PARSE`, `MIR`, `BUILD`, `EXECUTE`) sobre `bisect`, `heapq`, `collections`, `itertools`, `functools`, `difflib`, `textwrap` y `turtle` en `STDLIB_DELTA.md`, logrando **`8/10 EQUIV`** en síntesis con overlay sobre `bisect.py` real de CPython 3.12.3 y `EQUIV` en workloads directos de `itertools` y `functools.reduce`.
6. **Gate 5 (Reproducibilidad e Integridad):** Todas las suites de regresión pasan (`73 passed` en `autopiton/tests/`, `10 passed` en `tests/test_p15_decorators.py`, `38 passed` en los gates focalizados de `tests/test_grammar_parity.py`) y todos los entregables cuentan con sumas criptográficas verificables en `SHA256SUMS.txt`.
