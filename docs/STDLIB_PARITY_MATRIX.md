# PITÓN — Matriz de Paridad de Standard Library (CPython 3.12)

> Política y estado por módulo. Cada módulo se clasifica con un modo de
> implementación. No implicar implementación nativa si se usa CPython en
> secreto. Esta matriz es el contrato de `CPYTHON_3_12_STDLIB_PARITY`.

Estados y modos: `NATIVE` (runtime propio), `REIMPLEMENTED` (lógica propia),
`BRIDGED` (usa una implementación existente/no-CPython), `COMPAT_LAYER`,
`FUTURE`, `OUT_OF_SCOPE`.

## Tiendas (tiers)

- **Tier 0** — builtins fundamentales.
- **Tier 1** — soporte core del lenguaje.
- **Tier 2** — módulos comunes compatibles pure-Python.
- **Tier 3** — OS/runtime.
- **Tier 4** — pesados de extensiones nativas.

## Matriz

| Módulo | Tier | Nivel objetivo | Estado actual | Modo | Deps | Win | Linux |
|---|---|---|---|---|---|---|---|
| builtins (print/len/type/abs/min/max/sum/int/str/float/bool/list/tuple/dict/set/range) | 0 | completo | PARTIAL | NATIVE | — | PASS | PASS |
| math | 1 | Tier1 | PASS (sqrt) | NATIVE | floats | PASS | PASS |
| sys | 1 | Tier1 | NOT_DEMONSTRATED | REIMPLEMENTED | argv/env | ND | ND |
| os | 3 | Tier3 | NOT_DEMONSTRATED | BRIDGED | fs | ND | ND |
| time | 3 | Tier3 | NOT_DEMONSTRATED | REIMPLEMENTED | syscalls | ND | ND |
| json | 2 | Tier2 | NOT_DEMONSTRATED | REIMPLEMENTED | strings/dict | ND | ND |
| collections | 1 | Tier1 | NOT_DEMONSTRATED | REIMPLEMENTED | containers | ND | ND |
| io | 3 | Tier3 | NOT_DEMONSTRATED | BRIDGED | files | ND | ND |
| pathlib | 3 | Tier3 | NOT_DEMONSTRATED | REIMPLEMENTED | os | ND | ND |
| subprocess | 3 | Tier3 | NOT_DEMONSTRATED | BRIDGED | os/process | ND | ND |
| sockets | 3 | Tier3 | NOT_DEMONSTRATED | BRIDGED | net | ND | ND |
| threading | 3 | Tier3 | NOT_DEMONSTRATED | REIMPLEMENTED | runtime | ND | ND |
| multiprocessing | 3 | Tier3 | NOT_DEMONSTRATED | REIMPLEMENTED | process | ND | ND |
| asyncio | 3 | según diseño | NOT_DEMONSTRATED | REIMPLEMENTED | async | ND | ND |
| ctypes / FFI | 4 | carril separado | PARTIAL | COMPAT_LAYER | — | ND | ND |

`ND` = NOT_DEMONSTRATED. Un `PASS` en una celda debe tener su corpus diferencial
y su recibo.

## Reglas

- No traducir nombres de stdlib al español automáticamente (capa explícita).
- Compatibilidad con extensiones CPython = carril separado, no requisito de
  `CPYTHON_3_12_LANGUAGE_PARITY`.
- Antes de marcar un módulo `PASS`, decidir explícitamente su modo y registrar
  el corpus.