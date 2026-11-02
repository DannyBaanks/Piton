# STDLIB_DELTA — Evaluación de Paridad sobre Módulos de la Stdlib (Fase C)

## 1. Embudo Estático (`ADAPT -> PARSE -> MIR -> BUILD`) sobre Módulos Prioritarios (`[DEMONSTRATED]`)

Medición ejecutada mediante `tools.parity_corpus_v4.stage_file` sobre `/usr/lib/python3.12/`:

| Módulo Stdlib | ADAPT | PARSE | MIR | BUILD | Detalle de Etapa / Bloqueador |
|---|---|---|---|---|---|
| `bisect.py` | PASS | PASS | PASS | **PASS** (`0.097s`) | Compila a ELF nativo completo sin errores |
| `heapq.py` | PASS | PASS | PASS | **PASS** (`0.221s`) | Compila a ELF nativo completo sin errores |
| `functools.py` | PASS | PASS | FAIL (`MIR_FAIL`) | — | `MIRLoweringError: native dynamic call supports one *args sequence` |
| `difflib.py` | PASS | PASS | PASS | FAIL (`BUILD_FAIL`) | `NativeBuildError: Linux + with None is not supported` (en `Differ._fancy_replace` tras restaurar gates fail-closed en `e60beb8`) |
| `textwrap.py` | PASS | PASS | PASS | FAIL (`BUILD_FAIL`) | `NativeBuildError: native map requires a user function callback, not a builtin name` |
| `collections/__init__.py` | PASS | PASS | FAIL (`MIR_FAIL`) | — | `MIRLoweringError: native class methods support only @property, @staticmethod, @classmethod` |
| `turtle.py` (impacto directo de A4/A5) | PASS | PASS | FAIL (`MIR_FAIL` más adelante) | — | **Desbloqueado del error previo** (`MIRLoweringError: native class without __init__ takes no arguments`); ahora avanza hasta `native math.radians is not supported` |

---

## 2. Evaluación Diferencial con `--overlay` (`autopiton synthesize`) (`[DEMONSTRATED]`)

### 2.1 `bisect` (`run_20261101_161138_synthesis_seed7`, 10 programas, `seed=7`)
- **Candidato:** `/usr/lib/python3.12/bisect.py` traducido vía `ADAPT` (118 líneas, `sha256: 4fa9ac9c39078f83...`).
- **Sin `--overlay` (baseline stock compiler):** `0/10 EQUIV` (`7 INVALID_ACCEPTED`, `3 DIVERGENT`).
- **Con `--overlay` (`bisect` registrado):** **`8/10 EQUIV`**, `2 DIVERGENT`, `0/10` rechazados por el compilador.
- **Control mutado (`--control`, `run_20261101_161151_synthesis_seed7`):** mutación `bisect_left line 94: < -> >=; bisect_right line 41: < -> >=` (`sha256: eaa047cfcfcd39d7...`) reduce `EQUIV` de **`8/10` a `1/10`** (`5 DIVERGENT`, `4 INVALID_ACCEPTED`), demostrando que el oráculo detecta fallos semánticos reales en la stdlib.

### 2.2 `heapq` (`run_20261101_161203_synthesis_seed7`, 10 programas, `seed=7`)
- **Candidato:** `/usr/lib/python3.12/heapq.py` traducido vía `ADAPT` (602 líneas, `sha256: e2b7a698a210d9e7...`).
- **Con `--overlay`:** `5 INVALID_REFERENCE`, `5 UNSUPPORTED` (`0/10 EQUIV`, `10/10` rechazados en compilación cuando se invoca la superficie completa con comparaciones de tuplas/callbacks de `heapq`).

---

## 3. Workloads Diferenciales Directos sobre Capacidades Bundled de la Stdlib (`[DEMONSTRATED]`)

| Módulo | Workload | Clasificación | Etapa Piton | Observación |
|---|---|---|---|---|
| `itertools` | `itertools.chain([1, 2], [3])` + `itertools.islice([10, 20, 30, 40], 1, 3)` | **`EQUIV`** | `EXECUTE` | `stdout="[1, 2, 3]\n[20, 30]\n"` idéntico a CPython |
| `functools` | `functools.reduce(lambda a, b: a + b, [1, 2, 3, 4], 0)` | **`EQUIV`** | `EXECUTE` | `stdout="10\n"` idéntico a CPython |
| `bisect` (con `--overlay`) | `bisect_left`, `bisect_right`, `insort_left`, `insort_right` (10 casos `seed=7`) | **`8/10 EQUIV`** | `EXECUTE` | 8 de 10 llamadas públicas equivalentes a CPython |
| `collections` (sin overlay) | `deque.appendleft` + `Counter` | `DIVERGENT` | `EXECUTE` | Pendiente soporte completo en runtime nativo sin overlay |
