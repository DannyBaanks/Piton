# Tablero de bounties — PITON

El tablero no es una lista de deseos: cada bounty tiene **criterio de aceptación
ejecutable**. Si no se puede ejecutar el criterio, el bounty no está cerrado.

Doctrina obligatoria antes de tocar nada: `AGENTS2.md` (lectura única; luego §7).

## Protocolo

- Un bounty = un fichero `bounties/B-<area>.md`. **Nadie edita el de otro.**
- Reclamar: `handshake.py claim piton/bounty/<area> <identidad> --path <dir>`
  y escribir `owner:` en el fichero. El lease es semáforo, no permiso de borrado.
- Estados: `OPEN → CORPUS → ADV+PARITY → IMPL → VERIFIED → CLOSED`.
- Un bounty solo pasa a `IMPL` cuando existen corpus **y** esperados del oráculo.
- Commit path-scoped siempre. Sin commit no hay RESULT. Sin RESULT no se libera.

## Reglas de oro (violarlas invalida el bounty)

1. Esperado capturado de CPython 3.12 **antes** de mirar PITON.
2. Cada ejemplo **imprime un valor de retorno**; no sirven sentencias sueltas.
3. Prohibido normalizar, ignorar exit code, o descartar casos que no compilan.
4. Un test adversarial que no puede fallar no es un test.
5. Si no se demuestra: `NOT_DEMONSTRATED`, no un verde de consolación.

## Reparto por corpus (2 agentes + implementadores)

| rol | qué escribe | qué busca |
|---|---|---|
| **ADV** | `tests/test_adv_<area>.py` | hacer fallar a PITON a propósito, con el esperado de CPython al lado |
| **PARITY** | `tests/test_parity_<area>.py` | que el arnés no pueda mentir: comparador byte a byte + test del test |
| **IMPL** (4-5) | `piton/stdlib_piton/<...>.piton` | un fichero por agente, sólo lo que el corpus prueba |

Recursión: si un corpus genera subáreas, se abren sub-bounties `B-<area>-<sub>`
con el mismo protocolo y los mismos gates.

## Bounties de la ola actual

| id | área | corpus | estado | nota |
|---|---|---|---|---|
| `B-formato` | formato y números | `corpus_t1/formato/` (50) | ADV+PARITY | **50/50 EQUIV** |
| `B-objetos` | objetos y métodos | `corpus_t1/objetos/` (56) | ADV+PARITY+IMPL | **56/56 EQUIV** (cerrado en `bf46426`: clúster excepción custom) |
| `B-funciones` | funciones de librería | `corpus_t1/funciones/` (56) | ADV+PARITY+IMPL | **51/56 EQUIV**; quedan 13 wrap, 30 partial(`**`), 54-56 namedtuple |
| `B-strings` | `str`, `join`, `Template` | — | OPEN | bloqueado por retornos del codegen |
| `B-collections` | list/dict/set/deque | `corpus_sec/stdlib/` (C2) | CORPUS | `deque.rotate` sin enlazar |

Notas medidas (2026-11-14, rama `rescate/wip-2026-11-13`; detalle en
`bounties/notes/sesion-2026-11-14.md`):

- `funciones`: 29/56 → **50/56 EQUIV** (`026406a` textwrap/string/reprlib,
  `cff545c` emisor de dicts + groupby/combinations/Counter, `bf46426`
  excepciones por clase viva + defaultdict). ADV funciones:
  46 passed + 1 xfail (razón escrita: el shape de tuplas no cruza la frontera
  de módulo).
- `objetos`: **56/56** (antes 53/56). Los 3 del clúster excepción custom
  cerrados en `bf46426`.
- `formato`: **50/50**, sin regresión tras los cambios del emisor.
- primario tier B: **8/11** (72.7% byte a byte); `stdlib.py` ya no es TIMEOUT
  (`3a8b21c`: `glob`/`re`/`os` bundled se llamaban a si mismos → stubs
  honestos) → los 3 restantes son DIVERGENT_MISMATCH **con el diff exacto**.
- Suite completa contra HEAD limpio `cff545c`: 1508 passed / 74 failed →
  1522 / 60 (0 fallos nuevos, 14 corregidos).

Notas medidas (2026-11-15, cierre de la ola 3):
- `funciones`: 29/56 → **51/56 EQUIV** (`026406a` textwrap/string/reprlib,
  `cff545c` emisor de dicts + groupby/combinations/Counter, `bf46426`
  excepciones por clase viva + defaultdict, `b867d71` `functools.partial` real
  + suma dinámica con operandos sin tipo).
- Pytest: 1531 passed / 51 failed → **0 fallos nuevos** frente a `b5541fa`
  (60/1522), 9 corregidos; 23 menos fallos que el baseline `cff545c`.
- `objetos` **56/56** y `formato` **50/50**, sin regresiones.

Notas medidas (2026-11-13):
- `formato`: **50/50 EQUIV** tras `9a1a417` (antes 48/50; cerrados `float('1e3')`,
  `float('1_000')`, `#x/#o/#b`, `=+10d`, f-string `#06x`). ADV formato: 22/22 verdes.
- `objetos`: 46/56 EQUIV; las 10 divergencias son el clúster excepciones/`__init__`-str.
- Auditoría U3 (`a79a7df`): el 25/25 de C1 se reproduce y el runner es honesto,
  pero **0/25 cubre retorno de cadena desde dentro de una función**.

## Plantilla `B-<area>.md`

```markdown
# B-<area>
owner:
estado: OPEN
notas: bounties/notes/<area>.md
corpus: corpus_t1/<area>/
esperados: corpus_t1/expected/<area>.json
adv: tests/test_adv_<area>.py
parity: tests/test_parity_<area>.py
aceptación:
  - N ejemplos, stdout byte a byte + exit code
  - los casos del corpus que hoy divergen: lista exacta
  - ningún NotImplementedError nuevo tapando algo implementable
  - pytest dirigido no empeora
```

## Notas compartidas

`bounties/notes/<area>.md`, append-only. Cada agente añade al cerrar:
qué probó, qué falló, qué símbolo/ABI le mordió, y qué deja sin demostrar.
Las notas son la entrada de la siguiente ola: quien llega lee, no redescubre.

## Bloqueos conocidos (no los redescubras)

- Dos ABI: el runtime embebido de `linux_x86.py` es **sin etiquetas**; el de
  `native_runtime.c` es **etiquetado**. No mezclar (AGENTS2 §T1-bis).
- Retornos `str()`/`join`/slice: hueco de inferencia de tipos, no falta de box.
- `*args` vacío llega como `(None, None)`.
- `time.strftime` y `locale.format_string` tienen símbolos del ABI etiquetado
  dentro del runtime embebido: no compilan hasta reescribirlos.
