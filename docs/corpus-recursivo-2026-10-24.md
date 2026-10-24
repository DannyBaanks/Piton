# Corpus recursivo 2026-10-24 — medición con subpaquetes de la stdlib

Reporte: `parity_corpus_v4_recursive_2026-10-24.{json,md}`
(reproducible: `python3 tools/parity_corpus_v4.py run --tier a --recursive --workers 1 --isolate`).

## Por qué `--isolate`

Sin `--isolate` el pool de workers muere siempre (`BrokenProcessPool`): **un
archivo mata el proceso del worker**. Con `--isolate` cada archivo compila en
un proceso hijo y el culpable queda identificado: **`pydoc_data/topics.py`,
`exit=-9`** (SIGKILL, memoria). Es el archivo más grande de la stdlib y el
único CRASH de los 588. Queda como caso de tanda propia: no es un fallo de
semántica (nunca llega a divergir), es el compilador agotando memoria sobre
ese archivo concreto.

## Resultado (588 archivos = stdlib completa con subpaquetos)

| etapa | pasan | % |
|---|---|---|
| adapt | 588 | 99.8% |
| parse | 575 | 97.8% |
| mir | 75 | 12.8% |
| build | 66 | 11.2% |

`MIR_FAIL` 500 · `PARSE_FAIL` 12 · `BUILD_FAIL` 9 · `CRASH` 1.
De los MIR_FAIL, **313 son `native module not supplied`**.

## PARSE_FAIL (12) — patrones, no casos aislados

- `COLON` ×9: anotaciones con valores por defecto y `match/case` (el
  diccionario adapt no las traduce; el parser no las acepta).
- `STAR` ×2: `star-unpacking` en posiciones que el adapt no cubre.
- `EQUAL` ×1: walrus en contexto no cubierto.

## Palanca medida (módulos ausentes, recursivo)

130 archivos fallan por **un solo** módulo; 75 por dos.

| módulo | archivos | símbolos que piden (medido) |
|---|---|---|
| itertools | 40 | chain 25, count 15, islice 9, repeat 6, filterfalse 6, starmap 3, groupby 3, zip_longest, tee |
| email | 31 | submódulos punteados (message/utils/generator...) — el bundled es flat |
| _multibytecodec | 24 | (módulo C, sin uso observable) |
| typing | 22 | Optional, Any, Union, List, Iterable, Tuple, Protocol, TypeVar, Callable, cast |
| stat | 19 | **S_ISDIR/S_ISLNK/S_ISSOCK/S_ISFIFO/S_ISCHR/S_ISREG** — las constantes ya están en `_NATIVE_MODULE_CONSTS`, pero las FUNCIONES no tienen camino por esa vía |
| subprocess | 19 | PIPE/DEVNULL/STDOUT, Popen, run, check_output, CalledProcessError |
| importlib | 18 | machinery/util (submódulos), import_module |
| test | 18 | support |
| weakref | 17 | ref, WeakKeyDictionary, WeakSet, WeakValueDictionary |
| signal | 16 | constantes SIG*, set_wakeup_fd, getsignal, signal, pthread_sigmask |
| urllib | 15 | parse/request (submódulos) |
| unittest | 14 | SkipTest, skip@If/Unless, TestCase, TestSuite |
| pathlib/queue/xml/random/shutil | 9-8 | Path, Queue, dom (submódulo), Random, which |

Los submódulos punteados (`email.*`, `xml.*`, `importlib.machinery`,
`concurrent.futures`) no los cubre el mecanismo flat de `stdlib_piton/`: haría
falta resolver paquetes (registrar un .piton por submódulo), que es el
siguiente diseño.

Estado de la banda: `git b1690ae` (sesión 2026-10-24) sobre el corpus
recursivo. Suite verde: 1276 passed.
