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

## PR #51: clasificación del rojo Windows (2026-11-17)

La corrida que abrió el trabajo fue `44050330427`, job `test-windows (3.12)`
`133926234972`, sobre `3110c14881b768990ccaac3f662c2caad0802393`. Su salida
real terminó en:

```text
706 failed, 953 passed, 80 skipped, 224 xfailed, 5 xpassed,
67 errors, 170 subtests passed in 3113.21s (0:51:53)
```

Las **773 filas rojas** (706 `failed` + 67 `errors`) se separaron por causa;
no se ocultaron por comodidad ni se metieron todas en la cuarentena:

| causa | filas | tratamiento en este branch |
|---|---:|---|
| backend Linux-ELF sin WSL en el runner Windows | 463 | `pytest.skip` explícito solo en Windows; el backend ELF real sigue ejecutándose en Linux |
| fixture `glob` con nombres Windows inválidos | 67 | fixture portable; conserva la cobertura de wildcards y newline en POSIX |
| deuda real del backend PE `piton/x86.py` | 243 | `xfail(strict=False)` con nodeid exacto; el test se ejecuta y sigue reportando |

La partición se reconstruyó desde el log guardado del job: los 243 nodeids PE
coinciden uno a uno con las filas `FAILED`, y las 463 restantes corresponden al
mismo camino de prueba Linux-ELF (`compile_native_linux`); los 67 `ERROR` son
todos `OSError: [Errno 22] Invalid argument` al crear `star*name.md` en Windows.

### Cómo se divide la parte PE

Los 243 fallos PE reales se clasifican así:

| excepción observada | nodeids | naturaleza |
|---|---:|---|
| `ValueError` de aridad/MIR (`closure_new` con 6 campos frente a unpacking de 4) | 197 | el emitter Windows no acompaña el IR actual |
| `piton.x86.NativeBuildError` | 25 | gates PE fail-closed todavía no portados |
| `AssertionError` de divergencia semántica | 21 | resultado PE distinto de CPython |

La lista final de `tests/quarantine_windows.txt` queda en **469 nodeids
únicos**. La reducción desde la lista histórica es auditable:

```text
233 historicos - 2 NO_RECOLECTADOS - 5 XPASS + 243 PE nuevos = 469
```

Los dos IDs históricos que ya no se recolectaban se retiraron:

- `tests/test_grammar_parity.py::BundledStdlibGatesV2::test_stub_fails_closed_at_runtime`
- `tests/test_grammar_parity.py::M6SeqDefaultsV1::test_scalar_arg_to_collection_default_fails_closed`

Los cinco XPASS medidos también se retiraron (un XPASS es evidencia de que el
backend ya no falla y debe quedar fuera de la cuarentena):

- `tests/test_grammar_parity.py::RaiseValueGatesV1::test_raise_instance_is_caught`
- `tests/test_metaclasses.py::MetaclassesGate::test_metaclass_conflict_fails_closed`
- `tests/test_p0_parity.py::BuiltinMarkerV1::test_store_builtin_fail_closed`
- `tests/test_phase5.py::Phase5Gates::test_descriptors_unknown_class_decorator_fails_closed`
- `tests/test_phase5.py::Phase5Gates::test_x86_module_attribute_value_access_fails_closed`

### Correcciones portables, no reservadas a PE

- `tests/native_linux.py` centraliza el acceso al backend ELF. En Linux llama
  al backend real sin cambiar su comportamiento; en Windows omite solo las
  pruebas que compilan un ELF. `PITON_RUN_LINUX_NATIVE_ON_WINDOWS=1` permite
  activar esa vía cuando WSL y su toolchain están configurados.
- La detección de WSL del CLI E2E comprueba además que exista `gcc`; tener
  una distro no basta para compilar el ELF.
- El fixture `glob` conserva `star*name.md` y `q?.log` en POSIX y los omite en
  Windows, donde el sistema de ficheros prohibe esos nombres. El caso con
  newline se omite solo en Windows. La matriz POSIX no pierde cobertura.
- El oráculo CPython vivo usa `os.linesep`; los cuatro módulos `test_falsify_*`
  usan `sys.executable`; `test_parity_formato.py` hace el skip antes de que
  `_stage_impl` intente invocar el toolchain Linux.
- No se modificó `.github/workflows/ci.yml`: el job conserva los mismos
  comandos y cobertura; solo cambia cómo la suite distingue su entorno.

### Evidencia local

- Corrida completa Windows (mismos argumentos que el job CI):

  ```text
  py -m pytest tests/ -v --tb=short --ignore=tests/test_windows_validate.py

  lista=469 aplicados=469 XFAIL=449 XPASS=0 SKIP=20 SIN_REPORTE=0
  FUERA_DE_LA_LISTA=0
  947 passed, 639 skipped, 449 xfailed, 170 subtests passed in 7809.25s
  (2:10:09)
  ```

  Los 243 nodeids PE nuevos se ejecutaron y quedaron `XFAIL`; ninguno fue
  `XPASS`. Los 20 `SKIP` restantes son IDs históricos que hoy se omiten por
  el toolchain Linux-ELF o por una precondición interna: también siguen
  visibles en el resumen, no se presentan como fallos PE resueltos.
- Focales Windows: `test_adv_objetos.py` = 2 passed / 30 skipped;
  `test_cli_e2e.py` = 25 passed / 3 skipped; los cuatro `test_falsify_*` =
  21 passed; `test_parity_formato.py` = 4 passed / 1 skipped.
- Tranches locales: P0-P9 = 341 passed / 56 skipped / 152 xfailed;
  `test_phase5.py` = 223 passed / 1 skipped / 82 xfailed; translator = 63
  passed. No hubo fallos fuera de la cuarentena.
- `py -m compileall -q tests` terminó con exit 0 y `git diff --check` no
  encontró errores.
- La lista tiene 469 entradas, 469 únicas; contra la recolección actual de
  2.034 tests quedaron 0 nodeids ausentes.

Not evaluated here / **NOT_DEMONSTRATED**: portar `closure_new` de 6 campos y
los gates PE al backend Windows, y corregir las divergencias semánticas PE —
eso es trabajo de backend, no de CI, y sigue siendo la deuda que hay que
saldar para volver a exigir Windows verde sin cuarentena. La reducción de la
cuarentena no declara resuelto ninguno de esos fallos: solo separa el hueco
del backend de los problemas de entorno y de los bugs portables del fixture.


## Follow-up post-merge de PR #51 (2026-11-18)

Este follow-up no cambia `.github/workflows/ci.yml`. Corrige tres defectos que
la merge anterior dejó visibles solo al repetir la medición.

### 1. La cuarentena ya no confunde SKIP con fallo PE

La corrida Windows completa dejó 20 nodeids de la lista en `SKIP`: todos
compilan un ELF Linux y se omiten por `tests/native_linux.py`; ninguno es un
fallo del backend PE. Esos 20 IDs se retiraron de
`tests/quarantine_windows.txt`. La cuenta vigente es:

```text
233 historicos - 2 NO_RECOLECTADOS - 5 XPASS - 20 SKIP ELF + 243 PE = 449
```

La lista tiene **449 entradas y 449 nodeids únicos**. La recolección actual
sigue siendo de 2.034 tests y los 449 nodeids de la lista se recolectan todos
(`aplicados=449`, `NO_RECOLECTADOS=0`).

### 2. Las comparaciones del intérprete ya no quedan detrás del skip ELF

En diez módulos, la comparación CPython ↔ `piton ejecutar` se evaluaba después
de compilar el ELF. Como `pytest.skip` es una `BaseException`, el skip de
Windows abortaba el test antes de que se observara una divergencia real del
intérprete. Ahora el orden es:

1. ejecutar CPython;
2. ejecutar `piton ejecutar` y comparar byte a byte + exit code;
3. solo después invocar el helper Linux-ELF.

La recolección actual de esos módulos contiene **194 nodeids con comprobación
de paridad del intérprete** (65 en `test_adv_glob_v1.py`, uno en
`test_adv_fs_tempfile_v1.py` y 128 en los otros ocho). El inventario previo
estimó 196; la recolección medida es la cuenta canónica. Los dos tests
nativo-only restantes de `test_adv_glob_v1.py` no tienen una pierna del
intérprete que reordenar.

Control de orden: al sustituir temporalmente `_run_ejecutar` por una salida
divergente, `wave10`, `wave5` y `glob` levantaron `AssertionError` antes de
intentar el ELF, en vez de terminar como `SKIP`.

### 3. El helper ejecuta el ELF dentro de WSL

`tests/native_linux.py::run_native_linux` centraliza la ejecución. En Linux
ejecuta el ELF directamente. En Windows:

1. mapea la ruta con `wslpath` de la distro seleccionada;
2. aplica `chmod +x` dentro de WSL;
3. ejecuta el binario con `wsl.exe /usr/bin/env -i`; nunca entrega el ELF a
   `CreateProcess`.

Se enrutaron por este helper **55 llamadas en 41 módulos de test**. El único
módulo con ejecuciones directas que queda es `tests/test_phase10_linux.py`,
que se omite entero fuera de Linux y ya tenía su rama WSL explícita.

Sonda local de la vía de ejecución, con un ELF de prueba compilado para Linux
por Zig (sha256
`1E63190CFF66CC1BC9847F1EE449B86D01A6A073E3065642FAC82B46EB489801`):

```text
rc=7 stdout='WSL ELF OK\n' stderr=''
```

También se comprobó que `cwd` se traduce al directorio WSL correcto. La otra
media quedó **NOT_DEMONSTRATED**: la distro WSL disponible en esta máquina es
`docker-desktop` y no contiene `gcc`, así que no se pudo demostrar el ciclo
completo *emitir C → compilar ELF con el toolchain WSL → ejecutar el ELF* en
este host. El job de CI Windows los omite de forma explícita: no activa
`PITON_RUN_LINUX_NATIVE_ON_WINDOWS`, así que ninguna ejecución ELF depende de
que el runner tenga una distro WSL.

### Tracker honesto de `FUERA_DE_LA_LISTA`

`tests/conftest.py` ya no cuenta solo reportes `call` de los tests que están
en la lista. Ahora:

- cuenta fallos y errores no incluidos en la cuarentena durante `setup`, `call`
  y `teardown`;
- cuenta errores de colección;
- distingue `XPASS(strict)` propio de un fallo real y fuerza la sesión roja si
  aparece;
- detecta el error que pytest 9 deja absorber por `xfail` en setup/teardown y
  hace fallar la sesión aunque pytest haya reportado `XFAIL`.

Sondas con una suite temporal:

```text
fallo de call + error de setup + error de teardown:
FUERA_DE_LA_LISTA=3
  test_failures.py::test_call_failure [call]
  test_failures.py::test_setup_error [setup]
  test_failures.py::test_teardown_error [teardown]

error de import:
FUERA_DE_LA_LISTA=1 COLECCION=1
  test_collection_error.py [collection] E   RuntimeError: import boom

error de setup en nodeid de cuarentena:
lista=1 aplicados=1 XFAIL=0 XPASS=0 SKIP=0 ERROR=1 SIN_REPORTE=0
FUERA_DE_LA_LISTA=2 ERRORES_CUARENTENA=1 COLECCION=0 XPASS_ESTRICTO=0
PYTEST_EXIT 1

xfail(strict=True) que pasa, aunque la lista este vacia:
FUERA_DE_LA_LISTA=0 ERRORES_CUARENTENA=0 XPASS_ESTRICTO=1
PYTEST_EXIT 1
```

## Olas 17-22 (PR #53) - cuarentena ampliada, 2026-11-18

Origen: run `44305188216`, job `test-windows (3.12)`, head `08568df4`
(merge de `publish/olas-17-22` con `main`; ese merge aporta la lista de 469
nodeids del 2026-11-17). Ventana de la corrida: 2026-11-18T12:34:16Z a
2026-11-18T13:55:21Z. Resultado crudo del step de tests:

```
521 failed, 1001 passed, 669 skipped, 447 xfailed, 1 xpassed, 2 warnings, 170 subtests passed in 4825.04s (1:20:25)
```

`test-linux` (3.12) y `test-autopiton`: SUCCESS. `verify-oracle`: SKIPPED
(`needs` con el job Windows en rojo). Tracker de esa corrida:
`lista=469 aplicados=469 XFAIL=447 XPASS=1 SKIP=21 SIN_REPORTE=0
FUERA_DE_LA_LISTA=0`.

Evidencia descargada con `gh run view --job 134804797120 --log`; el log
normalizado a UTF-8 son 1.917.542 bytes, sha256
`B1759BF19422E6E6418041A37A85FE00ED7FA43BAAB970BDECB28FC394CABDAC`.

### Buckets medidos (521 casos reportados = 503 FAILED + 18 SUBFAIL)

| bucket | casos | lectura |
|---|---|---|
| `ValueError: too many values to unpack` lanzado desde `piton/x86.py` | 202 | el MIR de la rama emite formas que `x86.py` no desempaqueta (`method_call` con kw y handler). Es un crash interno, no un fail-closed |
| `piton.x86.NativeBuildError` | 158 | builtin o forma que `linux_x86.py` implemento y `x86.py` no: from-import multi-fichero, `len` de coleccion, `dict()` con argumentos, `open` con un argumento, `isinstance` sobre parametro sin tipo, `**`, ... |
| `AssertionError` (stdout/returncode distinto) | 135 | compila y corre, pero el resultado no es el de CPython |
| otros (`DID NOT RAISE`, `Exception not raised`, `TypeError`) | 26 | el gate de fail-closed esperado no dispara, o dispara con otro tipo |

Por ola: w17 130, w18 39, w19 50, w20 84, w21 132, w22 73. Anteriores a w17:
13 (`test_phase5.py` 5, `test_p13_delegators.py` 5,
`test_p5_percent_format.py` 2, `test_mir_resolution.py` 1); esos 13 estaban
verdes en Windows antes de esta rama y ahora ejercitan los mismos huecos por
codigo compartido (`mir.py`, `lower.py`).

La corrida reporto 18 SUBFAIL de `unittest` en 5 tests parametrizados
(`test_w17_pct_d_slot.py` 2, `test_w18_timeit_timer.py` 1,
`test_w20_mixed_none_params.py` 1, `test_w20_objeto_alias.py` 1). Se
declararon esos 5 padres, y con el marker `xfail` puesto el subtest deja de
imprimirse como `SUBFAIL` y el padre sale como **XPASS**, no como XFAIL: los
18 casos siguen ejecutandose y fallando por dentro, pero no suman al rojo.
Por eso esos 5 no se pueden sacar de la lista "porque hacen XPASS": hay que
revisar los subtest antes. Es la unica parte de esta cuarentena donde el
XPASS no significa "ya funciona".

El sexto XPASS de la corrida verde (y el unico que ya venia de la lista de
`main`) es
`tests/test_phase5.py::Phase5Gates::test_x86_super_undefined_attribute_fails_closed`
(candidato real a salir en la siguiente pasada; no se borro en este commit).

### Que se hizo

- `tests/quarantine_windows.txt`: **+508 nodeids** (503 fallos de `call` +
  5 padres de SUBFAIL de `unittest`) sobre los 449 de `main` tras el merge de
  #52, que retiro 20 nodeids de SKIP ELF. Total: **957** nodeids (449 + 508,
  sin interseccion). Ninguno de los 508 estaba ya en la lista.
- `tests/conftest.py`: se adopta el tracker de PR #52 (cuenta fallos y
  errores de setup/call/teardown y errores de coleccion, trata
  `XPASS(strict)` como rojo y fuerza sesion roja). El mismo fichero viaja en
  ambos PRs para que el merge no vuelva a divergir.
- Nada se salta ni se borra: los 508 tests se siguen ejecutando como
  `xfail(strict=False)` y siguen reportando `XPASS` cuando el backend
  Windows los alcance. El job queda rojo si falla cualquier test fuera de la
  lista.

### Lo que NO se hizo (y por que)

Portar a `piton/x86.py` el MIR de las olas 17-22 (`method_call` con kw y
handler, `super()` sobre bases builtin, `yield from`, dispatch de `None`,
...). Es trabajo de backend Windows; el alcance de #53 es el backend Linux.
Los 202 `ValueError` son la deuda mas visible: `x86.py:3283` hace
`explicit_class, method_name, owner, raw_values = args` mientras el MIR puede
emitir 4, 5 o 6 campos, asi que el fallo aparece como traceback y no como
`NativeBuildError`.

### Verificacion

Las dos corridas de abajo se midieron con la lista de **977** (antes de que el
merge de #52 retirase los 20 SKIP ELF). El arbol que abre este PR trae **957**
y su verificacion es la corrida de CI del commit de merge; el resultado queda
en el comentario de evidencia del PR, no en este fichero (un commit solo de
docs volveria a lanzar CI y no converge).

- Run `44367139094` (head `6b5dd55`, job `test-windows` 135023817253):
  **SUCCESS**. Tracker:
  `lista=977 aplicados=977 XFAIL=950 XPASS=6 SKIP=21 ERROR=0 SIN_REPORTE=0
  FUERA_DE_LA_LISTA=0 ERRORES_CUARENTENA=0 COLECCION=0 XPASS_ESTRICTO=0`.
  Pytest: `996 passed, 669 skipped, 968 xfailed, 6 xpassed, 2 warnings, 170
  subtests passed in 4998.15s (1:23:18)`. `test-linux`, `test-autopiton` y
  `verify-oracle`: SUCCESS. Log normalizado UTF-8: 525.039 bytes, sha256
  `7023A377D859D821314654F9785AFF69FD9CA8EF93765325737F470D15080982`.
- `py -m pytest tests/ --collect-only -q` (Windows 11, py 3.12.10, pytest
  9.1.1, arbol = rama + `main`): 2620 nodeids recolectados. De los 977 de la
  lista, 969 coinciden exactamente; los 8 restantes son parametrizaciones de
  `test_w18_decorator_segfault.py` cuyo ID local es largo
  (`[plain_deco-funcion registra(fn):...]`) y en CI (pytest 9.2.0) es
  `[plain_deco-source0]`; su base existe en ambos.
- `NOT_DEMONSTRATED` en este host: la suite completa con el toolchain del
  runner (NASM + gcc de GitHub) y con pytest 9.2.0. La verificacion real es
  la corrida de CI del commit que introduce esta seccion.


## Wave 23 (CI run 44963686034) - cuarentena ampliada, 2026-11-23

Origen: run `44963686034` (`4034d2e`, merge de `wave23/integration`), job
`test-windows (3.12)` `137027838668`. Ventana: 2026-11-21T23:50:00Z a
2026-11-22T01:19:01Z. Resultado crudo:

```
83 failed, 1010 passed, 690 skipped, 970 xfailed, 5 xpassed, 2 warnings, 170 subtests passed in 5296.29s (1:28:16)
```

`test-linux (3.12)` y `test-autopiton`: SUCCESS. `verify-oracle`: SKIPPED
(`needs`). Tracker de esa corrida:
`lista=957 aplicados=957 XFAIL=952 XPASS=5 SKIP=0 ERROR=0 SIN_REPORTE=0
FUERA_DE_LA_LISTA=83 ERRORES_CUARENTENA=0 COLECCION=0 XPASS_ESTRICTO=0`.

### Buckets medidos (83 casos reportados)

| bucket | casos | lectura |
|---|---:|---|
| `ValueError: too many values to unpack (expected 3/4)` desde `piton/x86.py` | 44 | el MIR emite `method_call` con 6 campos (incluyendo `kw` y `handler`) mientras que `x86.py` desempaqueta 4. El fallo aparece como traceback y no como `NativeBuildError`. Afecta a tests de class_method_decorators, native_object_binary_dunders, none_dyn_method_and_getattr, ntpath_startswith_sysconfig_format, shelve_weakref_cgen_linker, super_mro_base_attr_and_init, tierb_stdlib_and_fractions_trace |
| `piton.x86.NativeBuildError` | 22 | builtin o forma que `linux_x86.py` implemento y `x86.py` no: from-import multi-fichero (`abc`, `itertools`, `types`, `contextlib`, `_io`), `hash` position unsupported, `isinstance` bytes, `piton_conv_seq_to_list` undefined symbol |
| `AssertionError` (stdout/returncode distinto) | 16 | compila y corre pero el resultado no es el de CPython: divergencias de salida en `sysconfig.format_mapping`, regex mismatch en `fromtimestamp` fail-closed, `__isabstractmethod__` writable assertion |
| `DID NOT RAISE NativeBuildError` | 1 | test espera `NativeBuildError` para `unsupported_modulo_without_dunder` pero no se eleva |

Por archivo (según reporte de tarea):
- 16 `tests/test_w23_class_method_decorators.py`
- 14 `tests/test_w23_ntpath_startswith_sysconfig_format.py`
- 9 `tests/test_w23_stdlib_zip_count_datetime_splitroot.py`
- 9 `tests/test_w23_native_object_binary_dunders.py`
- 8 `tests/test_w23_tierb_stdlib_and_fractions_trace.py`
- 8 `tests/test_w23_shelve_weakref_cgen_linker.py`
- 8 `tests/test_w23_none_dyn_method_and_getattr.py`
- 7 `tests/test_w23_super_mro_base_attr_and_init.py`
- 3 `tests/test_w23_super_dynamic_base_mro.py`
- 1 `tests/test_w17_method_decorators.py`

Total: **83 nodeids únicos**. Ninguno se solapaba con la lista anterior (957).

### Que se hizo

- `tests/quarantine_windows.txt`: **+83 nodeids** sobre los 957 existentes. La lista pasa de 957 a **1040** nodeids.
- Los **5 nodeids padres de `unittest` subTest** (que en la corrida CI aparecen como XPASS) se **PRESERVAN** en la lista:
  - `tests/test_w17_pct_d_slot.py::PercentDynamicNumericSlot::test_dynamic_numeric_type_errors_match_cpython`
  - `tests/test_w17_pct_d_slot.py::PercentDynamicNumericSlot::test_integer_spec_non_finite_float_errors_match_cpython`
  - `tests/test_w18_timeit_timer.py::TimeitTimerNativeParity::test_task_reproducers`
  - `tests/test_w20_mixed_none_params.py::TestMixedNoneParamsParity::test_exact_reproducers`
  - `tests/test_w20_objeto_alias.py::TestSpanishBuiltinAliasShadowing::test_exact_reproducers`
  **Motivo**: cada uno es padre de subTests que esconden fallos reales (18 `SUBFAIL` en total en la corrida PR #53). Al marcarlos `xfail`, pytest deja de reportar los `SUBFAIL` y el padre sale como `XPASS`, pero **los 18 fallos internos siguen ocurriendo**. Por tanto no se pueden retirar de la cuarentena "porque hacen XPASS": hay que revisar los subtests antes. Ver `docs/windows_native_failures.md` líneas 411-420 (sección PR #53).
- Cuenta final: `957 + 83 = 1040` nodeids en la cuarentena (sin restar los 5 padres de subTest).
- `docs/windows_native_failures.md`: esta sección documenta el cambio.
- No se modificó `piton/x86.py`: el gap del unpacking sigue sin demostración en Windows PE.

### Lo que NO se hizo (y por qué)

Corregir el unpacking en `piton/x86.py` para absorber los 44 `ValueError: too many values to unpack` requeriría:
1. Cambiar `explicit_class, method_name, owner, raw_values = args` por un unpacking de 6 campos, O
2. Añadir validación de aridad que convierta el crash en `NativeBuildError` (fail-closed).

Ninguna de estas opciones fue realizada porque:
- No se tiene acceso a un runner Windows PE para ejecutar las pruebas (NOT_DEMONSTRATED).
- Modificar `piton/x86.py` podría regrejar tests existentes que pasan en Windows; sin verificación no se puede asegurar.
- El trabajo de portear el backend Windows al estado de `linux_x86.py` corresponde a otro workers/branch.

El gap se documenta aqui como deuda declarada: cada uno de los 44 `ValueError` sigue siendo un traceback innecesario en lugar de un `NativeBuildError` limpio, pero no se arregla en este commit.

### Verificacion local (Linux)

Se verifico en Linux con `PITON_QUARANTINE_FORCE=1` para confirmar que la maquinaria de cuarentena no rompe:

```bash
PITON_QUARANTINE_FORCE=1 python3 -m pytest tests/test_w23_*.py tests/test_w17_method_decorators.py -q
```

**Nota**: Esta corrida solo cubre los módulos con nuevos fallos. La suite completa requiere el script de verificacion documentado en PR #53 (no feasible sin runner Windows).

### Evidencia descargada

Log normalizado a UTF-8 guardado en `/tmp/opencode/w24/job.log`; sha256 disponible bajo demanda mediante `gh api repos/DannyBaanks/Piton/actions/jobs/137027838668/logs`.

### NOT_DEMONSTRATED

- El fix del unpacking en `piton/x86.py` no se ha demostrado seguro: no hay acceso a ejecucion PE Windows.
- La reduccion de la cuarentena no declara resuelto ninguno de los 83 fallos: solo separa el hueco del backend PE de los problemas portables.
- La prueba real de que estos tests fallan en Windows PE sigue siendo el siguiente run de CI en el runner `windows-2025-vs2026`.

## Wave 24 (PR #54, CI run 45335431850) - cuarentena ampliada, 2026-11-24

Origen: PR #54, head `0471d11`, run `45335431850`, job `test-windows (3.12)`
`138246275822`. Resultado crudo del job:

```
34 failed, 1009 passed, 692 skipped, 1053 xfailed, 5 xpassed, 2 warnings, 170 subtests passed in 4205.58s (1:10:05)
```

El tracker de esa corrida fue:

```
lista=1040 aplicados=1040 XFAIL=1035 XPASS=5 SKIP=0 ERROR=0 SIN_REPORTE=0
FUERA_DE_LA_LISTA=34 ERRORES_CUARENTENA=0 COLECCION=0 XPASS_ESTRICTO=0
```

Los 34 nodeids se reprodujeron tambien localmente en Windows 11 con CPython
3.12.10 y pytest 9.1.1, usando el worktree del PR y los mismos tests W24 mas
los dos tests zlib actualizados. El resultado local fue **34 failed, 1 passed**;
el unico test que paso fue
`TestLinuxDynObjects::test_set_attr_uncaught_none_raises_attribute_error`, que
no se anade a la lista.

### Clasificacion medida

| bucket | casos | lectura |
|---|---:|---|
| `ValueError: too many values to unpack` desde `piton/x86.py` | 8 | el MIR de W24 emite formas de `method_call`, `get_attr`, `set_attr` y `closure_new` que el emitter PE no desempaqueta |
| `piton.x86.NativeBuildError` | 12 | from-import multi-fichero (`io`, `datetime`, `itertools`), `complex`/`bytes`/`bytearray` en posicion no soportada, `len` de celda, `isinstance` de parametro sin tipo y payload de excepcion |
| `AssertionError` de salida/returncode | 14 | retornos mixtos impresos como direcciones o `0`, `setattr(None)` con mensaje distinto, zlib `wbits` sin paridad y `open(fileobj=...)` sin el TypeError esperado |

Los 34 IDs se desglosan asi: 15 en `test_w24_linux_dyn_objects.py`, 12 en
`test_w24_mir_and_super.py`, 6 en `test_w24_stdlib_builtins_parity.py` y 1 en
`test_w17_stdlib_kwargs.py` + 1 en `test_w19_zlib_native.py` (los dos tests
zlib actualizados por W24). La suma por archivos es 34; la tabla de buckets se
conserva como clasificacion del log de CI, no como una afirmacion de que el
backend PE haya quedado corregido.

### Que se hizo

- `tests/quarantine_windows.txt`: **+34 nodeids** sobre los 1040 existentes;
  total **1074 IDs unicos**, sin duplicados. Cada test sigue ejecutandose en
  Windows como `xfail(strict=False)` y el tracker sigue fallando ante cualquier
  rojo fuera de la lista.
- `docs/windows_native_failures.md`: esta seccion registra el run, la
  reproduccion local y la diferencia entre el hueco PE y las regresiones Linux.
- `piton/linux_x86.py`: las 22 regresiones Linux de W24 se corrigieron en el
  backend Linux y se verificaron en Ubuntu 24.04 (63/63 en la bateria focalizada
  de regresiones + W24). Ese arreglo no se presenta como soporte PE.

### NOT_DEMONSTRATED

No se portaron a `piton/x86.py` el boxing de retornos mixtos, `setattr`
dinamico, `zlib.deflateInit2/inflateInit2`, `open(fileobj=...)`, el modelo de
`io.BufferedReader`, `itertools.chain`, `os.path.ismount` ni las posiciones de
`bytes`/`bytearray`. La cuarentena declara el hueco medido; no declara esos
features resueltos en Windows. La prueba de aceptacion es el siguiente run de
CI: deben quedar **0 fallos fuera de la lista** y los 34 tests deben aparecer
como `XFAIL`, no desaparecer de la ejecucion.
