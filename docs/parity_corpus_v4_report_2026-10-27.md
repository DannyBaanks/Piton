# Parity corpus report v4 — CODIGO REAL (stdlib + tutorial CPython)

- instrumento: `tools/parity_corpus_v4.py` | backend medido: **linux_x86**
- oracle: CPython 3.12.3 | git: `f85d73c` | fecha: 2026-10-27T09:06:32+00:00
- tier A (stdlib real, estatico, sin ejecucion): **167** archivos
- tier B (tutorial oficial, ejecucional): **11** scripts

## Tier A — embudo sobre la stdlib real (/usr/lib/python3.12/*.py)

Poblacion SIN seleccion: todos los .py de nivel superior. Solo embudo estatico (ningun archivo se ejecuta; side-effects fuera de alcance). Cada fila = archivos que PASAN esa etapa.

| etapa | pasan | % |
|---|---|---|
| adapt | 167 | 100.0% |
| parse | 167 | 100.0% |
| mir | 39 | 23.4% |
| build | 35 | 21.0% |

**Build completo (potencialmente ejecutable): 21.0%**

### Clases de fallo por etapa

| etapa fallida | n |
|---|---|
| MIR_FAIL | 128 |
| BUILD_FAIL | 4 |

De los fallos MIR, **50** son `native module not supplied` (imports de la stdlib real de CPython: el backend nativo solo resuelve modulos propios de PITON).

## Hallazgos verificados (fallos trazados a su linea adaptada)

- Otros fallos puntuales verificados sobre su linea adaptada: walrus `:=` en `mientras`/`si`, tuplas en targets de `para`, `;` entre sentencias, star-unpacking en asignacion, colisiones con palabras clave (`no = []`).

## Tier B — scripts del tutorial oficial de CPython

Cada fila = scripts que PASAN esa etapa.
| etapa | pasan | % |
|---|---|---|
| adapt | 11 | 100.0% |
| parse | 11 | 100.0% |
| mir | 11 | 100.0% |
| build | 11 | 100.0% |

| bucket de ejecucion | n |
|---|---|
| DIVERGENT_MISMATCH | 10 |
| EQUIV | 1 |

EQUIV (byte-identico vs CPython): **9.1%** | salida nativa honesta (EQUIV+RAISE_EQ): **9.1%** | DIVERGENT: **90.9%**

## Lectura honesta

- Los % son DEL POPULACION DECLARADO, no de 'CPython entero'. El
  embudo tier A es la medida mas defendible de gramatica real: es la
  stdlib de produccion sin elegir nada.
- Metodo: PITON tiene gramatica en espanol (si/funcion/para) y no
  admite comentarios `#`; la etapa `adapt` traduce el Python real de
  forma mecanica (tabla de keywords + heuristica de match/case +
  comentarios eliminados; builtins en ingles: el runtime es
  bilingue). adapt no falla en ningun archivo (0 fallos); lo que
  mide el embudo es que tanto codigo Python real sobrevive a la
  gramatica y el back-end de PITON DESPUES de esa traduccion mecanica.
- tier B son ejemplos autorizados por el equipo de CPython (el
  tutorial), convertidos 1:1 (bloques doctest -> script); el estado
  entre ejemplos de una pagina es el de su sesion REPL (cada
  ejemplo en try/except: la sesion no muere en un error, y un
  fallo por un lado solo marca DIVERGENT).

## NOT_DEMONSTRATED

- Windows x86: este instrumento NO lo mide (tiene su propia CI de
  PRs; la paridad Windows se juzga ahi).
- imports en runtime: los scripts que importan stdlib real fallan en
  la etapa de ejecucion; no se afirma soporte de import externo.
- paquetes/anidados de stdlib: fuera del alcance (solo nivel
  superior en tier A).
- side-effects de modulos stdlib: NINGUN archivo tier A se ejecuta.
- corpus PyPI/arbitrario: fuera de alcance de v4 (el tutorial es el
  codigo real mas autorizado que existe; PyPI es el siguiente paso).
- Diagnostico completo de PARSE_FAIL: solo los patrones de la seccion
  Hallazgos estan trazados linea a linea; el resto queda en bruto en
  el JSON del reporte.
