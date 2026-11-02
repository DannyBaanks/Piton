# REGRESSIONS — Verificación de No-Regresión (Fase D4)

## 1. Suite de Pruebas Automatizadas (`pytest`) (`[DEMONSTRATED]`)

Se ejecutaron las suites de AutoPiton y de paridad gramatical/decoradores de Piton:

```bash
PYTHONPATH=. pytest autopiton/tests/ tests/test_p15_decorators.py -q
PYTHONPATH=. pytest tests/test_grammar_parity.py -k "test_non_const_default_closure or DictMergeV1 or BuildFailGatesV1 or RecursiveStdlibGatesV2" -q
```

| Suite | Resultado | Detalles |
|---|---|---|
| `autopiton/tests/` (unit + sandbox + synthesis + corpus + integration canaries) | **73 passed** | Incluye `Canary C` (`super()` + `*args`) y `Canary F` (`A(0)` -> `TypeError`) ahora verificados como `EQUIV` |
| `tests/test_p15_decorators.py` | **10 passed** | Incluye `test_parameterized_decorator_repeat` (closures multicapa con celdas) |
| `tests/test_grammar_parity.py` (gates completos ejecutados en corrida de 425 tests) | **352 passed** | Incluye `DictMergeV1`, `BuildFailGatesV1::test_del_module_cleanup_fn_loads_alive`, `RecursiveStdlibGatesV2::test_typing_cast_is_identity`, `StdlibV2GatesV1::test_non_const_default_closure` |

## 2. Campañas Diferenciales de No-Regresión y Expansión Bounded (`[DEMONSTRATED]`)

| Campaña | Run ID | Programas | `EQUIV` | `INVALID_REFERENCE` | Divergencias (`DIVERGENT`/`UNSUPPORTED`/`INVALID_ACCEPTED`/`CRASH`) |
|---|---|---:|---:|---:|---:|
| Canarios obligatorios (`--canary`) | `run_20261101_160238_canary` | 10 | **7** | **3** | **0** |
| Grilla sistemática (`--systematic`) | `run_20261101_160244_systematic` | 74 | **74 (100.0%)** | 0 | **0** |
| Bounded baseline (`--bounded -c 30 --seed 11`) | `run_20261101_160331_bounded_seed11` | 30 | **30 (100.0%)** | 0 | **0** |
| Bounded expandido (`--bounded -c 40 --seed 23`) | `run_20261101_155010_bounded_seed23` | 40 | **40 (100.0%)** | 0 | **0** (tras corregir 2 `DIVERGENT` de `run_20261101_154630_bounded_seed23`) |
| Bounded expandido (`--bounded -c 30 --seed 99`) | `run_20261101_155952_bounded_seed99` | 30 | **30 (100.0%)** | 0 | **0** (tras corregir 2 `DIVERGENT` de `run_20261101_155043_bounded_seed99`) |
| Bounded expandido (`--bounded -c 30 --seed 77`, semilla no vista) | `run_20261101_160010_bounded_seed77` | 30 | **30 (100.0%)** | 0 | **0** |
| Reconciliación total de casos no-`EQUIV` en `index.json` | `run_20261101_160149_reconcile_replay` | 62 | **59 (95.2%)** | **3 (4.8%)** | **0** |
