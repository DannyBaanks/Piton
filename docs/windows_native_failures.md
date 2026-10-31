# Windows native (x86.py) — fallos conocidos P36–P41

Origen: correr `python -m pytest tests/ --ignore=tests/test_windows_validate.py` en CI
Windows (PE). Recorrido 2026-10-03 sobre PR #39. La suite verde es en Linux/x86.

Clasificación por bucket, sin borrar nada: cada grupo existe o NO antes de P36?
El campo **estado** distingue: heredado antes de P36 / aceptado como fail-closed /
feature-gap conocido / **REGRESIÓN candidata** (a investigar).

## Heredado antes de P36 (ya documentado)
- `CollMethodsV1::test_dict_get_missing_key_prints_none` (Linux ya imprime None; Windows sigue fallando)
- `DivergentCluster29V1` (4 tests): subscript/get, keys-sorted, sum sobre bigint, raises tipados
- `SumElementTypeV1` (3): str en sum, listas con elementos str
- `ChrNulV1` (2): chr multibyte, chr(0)
- `WithProtocolNativeV1` (3): propagación de excepción en `con`, supresión, unhandled
- `Phase5Gates::test_x86_bare_reraise_from_catchall_rejected`: access violation 3221225477 (KNOWN: requiere CALL_PROPAGATE/FINALBODY_UNWIND portar a PE)
- `OrderingMixedTypesV1::test_orderable_pairs_still_work`, `NonCallableCallV1` (crash AV), `test_dict_get_missing_key_prints_none`

## Fail-closed / gap declarado
- `DictKeyTypeV1::bool_and_float_keys` — x86 exige key iterable estática
- `ContainsV1::contains_positive_form` — needle int/bool requerido en dict
- `FormatSpecV1::test_float_presentations`, `PercentFormatV1::test_float_spec`, `FStringsV1::test_format_spec` — `%f` / format specs no portados
- `StripV1::test_strip_with_chars` — strip con chars no implementado
- `SumElementTypeV1::test_non_int_elements_fail_closed` — exige rechazo explícito
- `NativeSubsetEvidenceTests::test_cli_writes_passing_native_subset_receipt` — build evidence en PE roto

## P51: Windows CI verde (2026-10-04)

Run `37186362650` (`7fa956a`): **0 failed / 855 passed / 24 skipped / 157 subtests**.

- `ORDER_SEQ_V1`: list/tuple `< > <= >=` compara elemento a elemento
  (INT/BOOL/FLOAT/STR) y luego por longitud; deja de comparar punteros.
- `SET_STR_V1`: sets con elementos str se guardan taggeados y `x en set`
  compara por contenido (`piton_set_add_str` / `piton_set_contains_tagged`).
- `MODULE_METADATA_V1`: `sys.modules[...]` usa get taggeado con decode de
  puntero a modulo; antes el get historico comparaba INT vs OBJECT y
  devolvía `None` → `(null)` al imprimir `__name__`/`__package__`.
- `TEST`: normalizar CRLF en `dict_get_missing_key` (Windows text mode).
- `native_evidence` era downstream del corpus; con Phase 5 verde vuelve solo.

## P45–P50: resuelto en CI/local (2026-10-03)
Verificado PE real via Wine+mingw local (oraculo CI):
- Dicts taggeados punta a punta (subscript/get/in/iter/comp-index/store
  unificado plain/tagged) + unbox aritmetico de slots (SLOT_ARITH_V1).
- NONCALLABLE por trazado a const + FUNCTION_TEMP_V1 (escalares -> TypeError
  rc 1; funciones/closures/params al runtime; AV 3221225477 cerrado).
- WITH_CLEAR_V1 (mir compartido) + RERAISE_STALE_V1: with-handler x3 y
  bare-reraise verificados byte a byte.
- %f/%e/%g + :.Nf/:.Ne/:.Ng/:.N% con redondeo decimal real.
- chr(0) via variable (SET_HYGIENE_V1), oracle UTF-8 (ORACLE_UTF8_V1).
- Quedan fuera (buckets heredados OK en CI, divergencia solo-Wine en
  celdas nonlocal y multifile-CWD: no tocar lo que CI acepta).

## P42–P44: resuelto en CI (2026-10-03)
Verificado en run CI Windows: 43 -> 31 -> **22 failed** (839 passed).
- Conversiones `lista/tupla/conjunto/diccionario` como valores (CONV_*_V1).
- `y/o` mixto via slots taggeados (BOOL_SLOT_V1 Windows).
- Literales con str boxeados (STR_BOXED_V1) + `sum` no-int rechazado en build.
- `join` con boxeados + `%s/%d/%r` con slots.
- CRLF normalizado en expectativas hardcodeadas (range_as_value, conversion).
- Queda `test_join_literal_raises_on_windows` ELIMINADO (el join ahora funciona).

## REGRESIÓN candidata introducida por P36–P41 (investigar)
- `BuiltinMarkerV1::test_conversion_builtins_as_values` (~13 subtests) y `test_converted_collection_keeps_element_type` (~3): `piton/x86.py` no ramifica antes del chequeo `BUILTIN_MARKER_V1`, así que `lista/tupla/conjunto/diccionario/rango` en posición de valor muere.
- `BoolShortV1::test_dynamic_mixed_types`: `y/o` con operandos mixtos — P41 cubre Linux, falta portarlo al branch Windows.
- `BuiltinMarkerV1::test_range_as_value` (5): solo CRLF EOL (`\r\n` vs `\n`) — probablemente beda de tests y no del emitter.

## Cuarentena CI 2026-10-31 — 233 nodeids, hueco declarado

Estado medido hoy en CI (runner `ubuntu-26.04` / `windows-2025-vs2026`, los 4 PR
abiertos; corrida de cada fila = la ultima del dia):

| PR | head | run | test-linux | test-windows | test-autopiton |
|---|---|---|---|---|---|
| #45 | feat/synthesize-stdlib | 41173086497 | 1033 failed | 232 failed | pass |
| #46 | fix/ci-gcc15 | 41176496891 | 68 failed | 233 failed | pass |
| #47 | fix/restore-format | 41193010921 | **0 failed** | 226 failed | pass |
| #48 | feat/autopiton-polish | 41229530568 | 1033 failed | 232 failed | pass |

Ultima corrida verde completa de `main`: run `38966307595` (2026-10-16T21:00Z,
0 failed en Linux y Windows). Desde ahi hay 232 commits en `main`.

### Linux: resuelto en este branch (no es el hueco)

- gcc 15.2.0 del runner `ubuntu-26.04` promueve `-Wint-conversion`,
  `-Wincompatible-pointer-types`, `-Wimplicit-function-declaration` e
  `-Wimplicit-int` a error; ni `-w` ni `-Wno-error` las bajan. Como todo el C
  que emite PITON guarda punteros en `long` (su ABI), **fallaba todo BUILD**
  (1033 failed). Fix: `_GCC_COMPAT_FLAGS` en `piton/linux_x86.py` (PR #46) +
  restaurar el `raise NativeBuildError` que estaba comentado (fail-open: sin
  el, un gcc que fallaba se reportaba build OK).
- Gates que la unificacion dejo fail-open y se restauran (metadatos de modulo,
  import punteado, `os.path`, metaclases, whitelist de decoradores) y tests
  que hardcodeaban `/usr/lib/python3.12` (en CI el Python viene del toolcache
  de `setup-python`) → `_stdlib_file()` con `sysconfig` (PR #47).
- Verificado local 2026-10-31 (Ubuntu 24.04 / gcc 13.3 / Python 3.12.3):
  `python -m pytest tests/ -q --ignore=tests/test_windows_validate.py`
  → en `origin/main`: **65 failed / 1235 passed / 22 skipped** (8m00s);
  → en `origin/fix/restore-format`: **0 failed / 1298 passed / 24 skipped** (6m39s).

### Windows: sigue abierto (el hueco real)

`piton/x86.py` (7.250 lineas) no acompana al MIR ni a `piton/linux_x86.py`
(13.324 lineas). De los 226 fallos del run #47 (233 en la union de los 4 PR):

- **121 `piton.x86.NativeBuildError`** — gates fail-closed que el backend Linux
  ya soporta: `native method receiver class is not statically known` (22),
  `min/max` (8), `len` (8), `unpack` de int (7), `list()` de int (6),
  `slice` de int (5), str `%` format (6), `str.startswith`/`repr`/`zip`/...
  **17 de ellos son `WSL has no installed distributions`** (entorno: el runner
  Windows no trae distro; PR #47 ya los salta con `skipUnless`).
- **67 `AssertionError`** — divergencia real de semantica PE: `d[1,2]=7`
  imprime `0`, `(*head,tail)=t` devuelve tupla en vez de lista, f-strings y
  `str.format` imprimen `None`, y **6 con `returncode 3221225477`
  (ACCESS_VIOLATION)**.
- **29 `ValueError` en `piton/x86.py:3387`**: `lifted_name, n_args, capture_ops,
  has_vararg = args` desempaqueta 4 campos y `mir.py:2273` emite `closure_new`
  con 6 → el emitter Windows no acompana el IR. Afecta a 29 tests, p.ej.
  `LambdaStarV1::test_lambda_plain_still_differential`,
  `DynamicCallGatesV1::test_dynamic_call_with_kwargs`,
  `ModuleStateGatesV1::test_bundled_functools_reduce` y 4 de
  `TestAdvancedDecorators`.
- **7 `PitonSyntaxError`** y **2 `DID NOT RAISE NativeBuildError`**.

### Que se hizo hoy (decision de Danny, 2026-10-31)

Cuarentena explicita en vez de "arreglar 233 fallos de backend en una pasada":

- `tests/quarantine_windows.txt` — los 233 nodeids, con fecha, origen (runs y
  cuentas por PR) y regla de actualizacion.
- `tests/conftest.py` — en `win32` los marca `xfail(strict=False)`: **el test
  se ejecuta igual** y sale `XFAIL` en el log. El job queda en rojo si falla
  cualquier test **fuera** de la lista (un rojo nuevo sigue rompiendo CI), y
  el resumen `CUARENTENA WINDOWS:` enumera `XPASS` (candidatos a salir de la
  lista), `FUERA_DE_LA_LISTA` (rojo real) y `NO_RECOLECTADOS` (nodeid que ya no
  se recolecta).
- Nada se borra: la lista solo se reduce, y cada ID que sale es evidencia de
  avance del backend Windows.

Reproducir la verificacion local de la maquinaria (Linux):

```bash
PITON_QUARANTINE_FORCE=1 python -m pytest tests/ -q -rX \
    --ignore=tests/test_windows_validate.py
# salida real 2026-10-31 (Ubuntu 24.04, py3.12.3):
#   1091 passed, 24 skipped, 207 xpassed, 146 subtests passed in 347.15s
#   lista=233 aplicados=208 XFAIL=0 XPASS=207 SKIP=1 SIN_REPORTE=0
#   FUERA_DE_LA_LISTA=0
#   NO_RECOLECTADOS (25): los windows-only de tests/test_phase5.py, que en
#     Linux no se recolectan (en Windows debe ser 0)
#   SKIP=1: un nodeid de la lista que el propio test salta (skip interno)
PITON_QUARANTINE_DISABLE=1 python -m pytest tests/ -q   # sin cuarentena
```

`XPASS=207` no es un verde de Windows: en Linux esos tests pasan porque ahi
manda `linux_x86.py`. El numero util para reducir la lista es el de Windows;
aqui solo demuestra que la maquinaria no rompe nada donde el codigo si anda.

Not evaluated here / **NOT_DEMONSTRATED**: portar `closure_new` de 6 campos y
los 121 gates al backend Windows, y los 6 ACCESS_VIOLATION — eso es trabajo de
backend, no de CI, y sigue siendo la deuda que hay que saldar para volver a
exigir Windows verde sin cuarentena.
