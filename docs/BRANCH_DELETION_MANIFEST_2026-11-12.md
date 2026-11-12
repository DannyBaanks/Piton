# Manifiesto de borrado de ramas — PITON (2026-11-12)

Autorizacion: Danny ("unifica todo en main y borra todas las branches").
Alcance: repositorio PITON (`/home/danny/Development/ISyCo Git/PITON`), remoto
`origin` = `https://github.com/DannyBaanks/PITON.git`.

## 1. Red de seguridad (previa a cualquier borrado)

| artefacto | valor |
|---|---|
| bundle | `/home/danny/Development/ISyCo Git/piton-backup-2026-11-11.bundle` |
| tamano | 4 121 852 bytes |
| `git bundle verify` | "El bundle registra una historia completa" |
| contenido | las 18 ramas (locales y remotas) + `main` + `origin/HEAD` con sus SHA |

El bundle contiene **todos los tips de rama**, asi que cualquier rama puede
restaurarse con `git fetch <bundle> <ref>:refs/heads/<rama>`.

## 2. Merge: que se integro en `main`

| commit | rama integrada | contenido |
|---|---|---|
| `0ef336d` | `feat/autopiton` | el arbol de `autopiton/` de la rama es un subconjunto del de main (0 case_id exclusivos, 0 ficheros exclusivos): el merge no cambia ni un byte |
| `a285edb` | `preservacion/terminal-sesion-ajena-2026-10-24` | `piton/terminal.py`, `piton/terminal.piton` y la llamada a `instalar_hook()` en `ejecutar_archivo` (el import_hook se importaba pero nunca se invocaba) |
| `58deea4` | `coder/copyreg-builtin-type-value` | 9 commits de backend: tipos builtin como valor, unions `int | str`, iteradores map(repr/len/str), metodos desbound (`str.translate(x, m)`, `int.__new__`), `del obj.attr` como no-op, subscript store con indice str, `%` con plantilla dinamica y N argumentos |
| `6165e06`, `cf2e0d4`, `3f80121` | `feat/build-unlock-stdlib` (FF) | `str.format` con campos nombrados y con `**mapping`, `f.closed/f.name/f.mode`, mapa de divergencias de Tier B |
| `7ac445d`, `eb25eda` | (fixes del merge) | 5 divergencias silenciosas cerradas: `map(int, xs)` como callback, alias espanol del callback, colision `iterator:str`, tracking del atributo de instancia, subscript store con indice desconocido |

Features de `coder/copyreg-builtin-type-value` **no portadas** (main ya las
cubria mejor): `ZIP_DYNAMIC_V1`, `MAP_FILTER_ANY_SOURCE_V1`,
`SORTED_DYNAMIC_V1`, `STR_PREFIX_DYNAMIC_V2`. El motivo esta en el mensaje
del commit `58deea4`.

## 3. Prueba de contencion (previa al borrado)

`git merge-base --is-ancestor <rama> main` devuelve 0 para **las 18 ramas
locales y las 11 ramas remotas**: cada tip es un ancestro de `main`, asi que
borrarlas no pierde ningun commit.

### Ramas locales (18, todas a borrar)

| rama local | SHA | fecha | `merge-base --is-ancestor <rama> main` | worktree que la hospeda | estado del worktree |
|---|---|---|---|---|---|
| `coder/copyreg-builtin-type-value` | `788c67f` | 2026-10-25 | si (exit 0) | — | — |
| `feat/autopiton` | `3369775` | 2026-10-30 | si (exit 0) | — | — |
| `feat/autopiton-grid-boundaries` | `798c055` | 2026-11-01 | si (exit 0) | `/home/danny/piton-grid` | LIMPIO |
| `feat/autopiton-polish` | `495f5b3` | 2026-11-01 | si (exit 0) | `/home/danny/piton-autopiton` | LIMPIO |
| `feat/build-unlock-stdlib` | `3f80121` | 2026-11-11 | si (exit 0) | — | — |
| `feat/frontier-a-dunders` | `5c22092` | 2026-11-02 | si (exit 0) | — | — |
| `feat/frontier-b-generators-exceptions` | `8210219` | 2026-11-02 | si (exit 0) | — | — |
| `feat/frontier-c-exception-objects-contexts` | `a3b1289` | 2026-11-02 | si (exit 0) | — | — |
| `feat/frontier-d-object-model` | `aebd25c` | 2026-11-02 | si (exit 0) | — | — |
| `feat/frontier-e-functional-builtins` | `a6642ab` | 2026-11-02 | si (exit 0) | — | — |
| `feat/frontier-f-stdlib-primitives` | `cfd45df` | 2026-11-02 | si (exit 0) | — | — |
| `feat/mir-resolution-stdlib-unlock` | `fbc4b01` | 2026-11-02 | si (exit 0) | — | — |
| `feat/synthesize-stdlib` | `2525033` | 2026-10-31 | si (exit 0) | `/home/danny/piton-synth` | LIMPIO |
| `fix/ci-gcc15` | `573776b` | 2026-10-31 | si (exit 0) | — | — |
| `fix/ci-green-2026-10-31` | `d143b0e` | 2026-10-31 | si (exit 0) | — | — |
| `fix/restore-format` | `b2ef0ba` | 2026-10-31 | si (exit 0) | `/home/danny/piton-ci-gcc15` | LIMPIO |
| `preservacion/terminal-sesion-ajena-2026-10-24` | `f1665c1` | 2026-10-23 | si (exit 0) | — | — |
| `sync/main-unified` | `7eae28e` | 2026-10-30 | si (exit 0) | — | — |

### Ramas remotas (11, todas a borrar)

| rama remota | SHA | contenida en `main` |
|---|---|---|
| `origin/feat/autopiton` | `3369775` | si (exit 0) |
| `origin/feat/autopiton-grid-boundaries` | `798c055` | si (exit 0) |
| `origin/feat/autopiton-polish` | `495f5b3` | si (exit 0) |
| `origin/feat/frontier-b-generators-exceptions` | `8210219` | si (exit 0) |
| `origin/feat/frontier-c-exception-objects-contexts` | `a3b1289` | si (exit 0) |
| `origin/feat/synthesize-stdlib` | `2525033` | si (exit 0) |
| `origin/fix/ci-gcc15` | `573776b` | si (exit 0) |
| `origin/fix/ci-green-2026-10-31` | `d143b0e` | si (exit 0) |
| `origin/fix/restore-format` | `b2ef0ba` | si (exit 0) |
| `origin/preservacion/terminal-sesion-ajena-2026-10-24` | `f1665c1` | si (exit 0) |
| `origin/sync/main-unified` | `7eae28e` | si (exit 0) |

## 4. Worktrees

| worktree | rama | accion |
|---|---|---|
| `/home/danny/Development/ISyCo Git/PITON` | `main` | se conserva (worktree principal) |
| `/home/danny/piton-autopiton` | `feat/autopiton-polish` | remover (limpio) |
| `/home/danny/piton-ci-gcc15` | `fix/restore-format` | remover (limpio) |
| `/home/danny/piton-grid` | `feat/autopiton-grid-boundaries` | remover (limpio) |
| `/home/danny/piton-synth` | `feat/synthesize-stdlib` | remover (limpio) |
| `/home/danny/Development/datavendor-coverage/20261103T220645Z/Piton/snapshot` | (detached `fbc4b01`) | **no tocar**: snapshot de datavendor, no es una rama |
| `/home/danny/Development/datavendor-coverage/20261104T043716Z-piton-update/Piton/snapshot` | (detached `9edddbc`) | **no tocar**: snapshot de datavendor, no es una rama |

Los cuatro worktrees que sostienen ramas estan LIMPIOS (sin cambios sin
commitear): se pueden remover sin perdida.

## 5. Ficheros sin trackear que NO se borran

Untracked != disposable: no hay autorizacion para `git clean` y estos tres
ficheros NO son ramas ni historia de git.

| fichero | que es | por que no se borra |
|---|---|---|
| `piton/stdlib_piton/json.piton` | stub **falso** sin trackear (`dumps` -> `str(obj)`, `loads` -> `s`) | no versionado: borrarlo cambiaria el resultado del gate `test_divergent_examples_are_exactly_these` (ver 7) y no es decision de esta operacion |
| `workfile` | artefacto generado al correr el corpus en la raiz | dato del usuario, no generado por git |
| `re` | PostScript de 11 MB, ajeno al repo | no pertenece a PITON |

## 6. Evidencia de la suite

| commit | arbol | resultado |
|---|---|---|
| `a285edb` (baseline, worktree limpio) | antes del merge de copyreg | **46 failed**, 1290 passed, 25 skipped |
| `eb25eda` (main) | tras los 3 merges + fixes | **43 failed**, 1302 passed, 27 skipped, 146 subtests |

Comparacion de conjuntos (`comm` sobre la lista de `FAILED`):

- **regresiones nuevas: 0** (ningun test que pasara en el baseline falla ahora)
- arreglados por el merge: `test_type_dynamic_class_instance_attribute`,
  `test_type_three_args_creates_class` (el `type()` dinamico de copyreg) y
  `test_divergent_examples_are_exactly_these`

## 7. Salvedad sobre el gate de Tier B

`tests/test_tier_b_divergence_map.py::InputoutputDivergenceV1::test_divergent_examples_are_exactly_these`
fija el conjunto divergente de `inputoutput` en `{39, 41, 61}`. Ese conjunto
**depende del stub sin trackear `piton/stdlib_piton/json.piton`**: en un arbol
limpio (sin ese fichero) `import json` falla y las divergencias son
`{39, 41, 61, 66, 68}`. Verificado ejecutando
`tools/tier_b_divergence_map.py --script inputoutput` en el worktree baseline
(5 de 68) frente al worktree principal (3 de 68). No se ha tocado el gate:
es una decision sobre el stub, no de esta operacion de unificacion.

## 8. Verificacion posterior al borrado (ejecutada 2026-11-12)

| comprobacion | resultado |
|---|---|
| `git branch -a` | `* main` + `remotes/origin/HEAD -> origin/main` + `remotes/origin/main` — ninguna otra rama |
| `main` == `origin/main` | `dd2f9f8f1b6b6e45a3dc45ca25e35edac9bf916d` == `dd2f9f8f1b6b6e45a3dc45ca25e35edac9bf916d` |
| `git ls-remote --heads origin` (estado REAL del remoto) | una sola ref: `refs/heads/main` -> `dd2f9f8` |
| ramas locales borradas | 18/18 con `git branch -d` (git confirmo "fusionada" en cada una) |
| ramas remotas borradas | 11/11 con `git push origin --delete` (GitHub confirmo `[deleted]` en cada una) |
| `git worktree list` | solo el worktree principal (`main`) + los 2 snapshots de datavendor (detached, intactos) |
| `git bundle verify` | "El bundle registra una historia completa" — 19 refs recuperables |
| restauracion real desde el bundle | `git fetch <bundle> refs/heads/coder/copyreg-builtin-type-value:refs/tmp/restore-test` -> `788c67f`, contenido en `main`; ref temporal borrada |
| `git status --short` | solo los 3 ficheros sin trackear de la seccion 5; **0 cambios sin commitear** |

Nota: el remoto se movio de `DannyBaanks/PITON.git` a `DannyBaanks/Piton.git`
(aviso de GitHub); la URL de `origin` se actualizo.

