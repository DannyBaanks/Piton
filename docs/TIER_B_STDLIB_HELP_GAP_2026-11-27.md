# Tier B `stdlib.py`: la unica divergencia y por que NO es cerrable (2026-11-27)

- rama: `w25/tierb-stdlib` (desde `be24e6d`, sin push)
- git medido: `be24e6d` (mismo commit; ver control de worktree)
- oracle: CPython 3.12.3 | backend medido: **linux_x86**
- instrumento de metrica: `tools/parity_corpus_v4.py run --tier b`
- instrumento de diagnostico: `tools/tier_b_divergence_map.py --script stdlib`
- tests: `tests/test_w25_tierb_stdlib.py` (9 casos: 7 passed, 2 xfailed documentados)

## 1. Estado medido (salida cruda)

```
$ python3 tools/parity_corpus_v4.py run --tier b --out /tmp/out_b_before.json --markdown /tmp/out_b_before.md
tier B: 11 scripts tutorial (embudo + ejecucion)...
  EQUIV 90.9% | honest 90.9% | divergent 9.1%
json -> /tmp/out_b_before.json
md -> /tmp/out_b_before.md
```

Bucket por script (del JSON del reporte):

```
/tmp/piton-w25-tierb-stdlib/corpus_v4/tutorial/classes.py EQUIV
/tmp/piton-w25-tierb-stdlib/corpus_v4/tutorial/controlflow.py EQUIV
/tmp/piton-w25-tierb-stdlib/corpus_v4/tutorial/datastructures.py EQUIV
/tmp/piton-w25-tierb-stdlib/corpus_v4/tutorial/errors.py EQUIV
/tmp/piton-w25-tierb-stdlib/corpus_v4/tutorial/inputoutput.py EQUIV
/tmp/piton-w25-tierb-stdlib/corpus_v4/tutorial/interpreter.py EQUIV
/tmp/piton-w25-tierb-stdlib/corpus_v4/tutorial/introduction.py EQUIV
/tmp/piton-w25-tierb-stdlib/corpus_v4/tutorial/modules.py EQUIV
/tmp/piton-w25-tierb-stdlib/corpus_v4/tutorial/stdlib.py DIVERGENT_MISMATCH
/tmp/piton-w25-tierb-stdlib/corpus_v4/tutorial/stdlib2.py EQUIV
/tmp/piton-w25-tierb-stdlib/corpus_v4/tutorial/venv.py EQUIV
```

## 2. La divergencia entera: UN ejemplo de 52

```
$ python3 tools/tier_b_divergence_map.py --script stdlib
## stdlib — 1 de 52 ejemplos divergentes

| # | codigo | CPython | PITON |
|---|---|---|---|
| 7 | `help(os)` | Help on module os: /  / NAME /     os - OS routines for NT or Posix depending on what syst | EXC |
```

`corpus_v4/tutorial/stdlib.py` NO se ha tocado (sha256 del fichero intacto); la
diferencia completa, medida sobre el script tal cual lo ejecuta el corpus
(mismo directorio temporal, mismo entorno, nativo primero y oraculo despues):

```
native  rc 0  bytes 32    lineas 8     sha256 803f224cc536b1e242b3b1fea2beb2f1e6dc27737cb6058f684e643aaff3ae7b
oracle  rc 0  bytes 86853 lineas 2603  sha256 e195fe6dc0c406c8bec1b8d57f8cc0595ca9584d211963bfa75eaa614773a514
native stdout: EXC | EXC | EXC | EXC | EXC | EXC | EXC | EXC
primer byte divergente: 4
    nativo: "EXC\nEXC\nEXC\n..."     (a partir del 2o EXC)
    oraculo: "Help on module os:\n\nNAME\n    os - OS routines for NT or Posix..."
```

Es decir: los 51 ejemplos restantes son **byte a byte identicos** (exit code
incluido: 0 en los dos lados). El unico ejemplo que rompe la pagina es
`help(os)`, que en PITON levanta `NameError: name help is not defined` (el
arnes lo convierte en `EXC`) y en CPython imprime la pagina de `pydoc`.

## 3. Por que `help(os)` NO es cerrable de forma honesta

No es "una funcion que falta": es un ejemplo cuya **salida esperada no es una
constante**. Cuatro hechos medidos, cada uno con su comando:

1. **Depende del entorno del proceso.** La seccion `DATA` imprime `os.environ`:

   ```
   $ python3 -c "import os; help(os)" | sha256sum   # sin variable extra
   5cd80aff713e353a87b1209ed6b923ed691ab944bf69ae1385c4fad01e7e64a0  (2596 lineas, 86825 bytes)
   $ PITON_W25_SENTINEL=w25 python3 -c "import os; help(os)" | sha256sum
   a4e3646414186e921d721b836058c28c3751ad25871e1e111f03289bf6e695c9
   linea 2580 antes:    environ = environ({'AGY_REAL_XDG_CONFIG': '<HOME>/.co...'TERM_PRO...
   linea 2580 despues:  environ = environ({'AGY_REAL_XDG_CONFIG': '<HOME>/.co.../usr/bin...
   ```

   Ningun golden file puede ser honesto: cambia con el entorno de la maquina.

2. **Depende de la ruta de instalacion de la stdlib** (seccion `FILE`):
   `    /usr/lib/python3.12/os.py`.

3. **Exige copiar el contenido del modulo `os` de CPython**: `os.__doc__`
   ("OS routines for NT or Posix depending on what system we're on...") mas
   los docstrings de **1362 funciones** y 7 clases, y los **402 nombres** de
   `dir(os)` en la seccion `DATA`. Eso es escribir la salida esperada a mano
   dentro de PITON (tabla de falsos verdes de AGENTS2 §1), no paridad.

4. **La stdlib bundled de PITON no tiene docstrings que un `help()` genuino
   pudiera imprimir**: `grep -rln '"""' piton/stdlib_piton/*.piton | wc -l`
   devuelve **0**. Un `help()` implementado sobre los objetos reales de PITON
   produciria otro texto distinto: divergente igual, y ademas habria que
   reimplementar el renderizador de `pydoc` (secciones NAME/MODULE REFERENCE/
   DESCRIPTION/CLASSES/FUNCTIONS/DATA/FILE, MRO, "Methods defined here:",
   `repr` truncado de 64 caracteres...).

El "arreglo" que haria pasar el ejemplo es convertir `help` en un no-op
silencioso: PITON no imprimiria nada y el oraculo... seguiria imprimiendo 2596
lineas, asi que tampoco colaria. El ejemplo sigue siendo **divergente honesto**,
y esa es la conducta correcta. `tests/test_w25_tierb_stdlib.py::
test_native_help_is_name_error_not_a_silent_noop` fija justo eso: el fallo de
PITON es un `NameError`-confesado, no un silencio que parezca acierto.

## 4. Lo que SI esta genuine y medido

- Los 51 ejemplos restantes de la pagina son EQUIV contra el oraculo
  (`test_the_only_divergent_example_is_help_os` falla si deja de ser asi, y
  nombra los ejemplos que se salgan).
- Las 11 paginas del tutorial mantienen 10 EQUIV / 1 DIVERGENT (medicion del
  corpus, no una reescritura).
- `help` no es un stub silencioso: es NameError (`builtins.piton` declara
  `help = 0` y `pydoc.piton` marca `help/render_doc/describe` como
  `NotImplementedError`).

## 5. Lo que NO es alcanzable (NOT_DEMONSTRATED)

| sub-ejemplo | motivo | clase |
|---|---|---|
| `help(os)` (stdlib, ej. 7) | salida dependiente del entorno + docstring e inventario completos del `os` de CPython | limitacion de paridad, no bug |
| `dir(os)` consumido (`len(dir(os))` = 402 en el oraculo) | `dir` no esta implementado (devuelve `0`/`None`); y aun implementado, los 402 nombres son del modulo `os` de CPython | brecha REAL (KnownGapsV1, xfail) |
| `vars()` consumido | PITON devuelve su propio diccionario (`os__path__var__sep`, ...) y el `repr` de CPython lleva direcciones de heap | NOT_DEMONSTRATED (como los tracebacks de T14) |

`dir(os)` y `vars()` son EQUIV en el corpus **solo porque el valor de retorno
se descarta**: una expresion como enunciado no imprime nada en CPython. Es el
mismo punto ciego que T7, documentado aqui para que nadie lo lea como soporte.

## 6. Controles (descartar explicaciones alternativas)

- **No es WIP de otro frente.** Worktree limpio en el mismo commit:

  ```
  $ git worktree add /tmp/piton-head be24e6d
  $ cd /tmp/piton-head && python3 tools/parity_corpus_v4.py run --tier b --out /tmp/control_head.json
  tier B: 11 scripts tutorial (embudo + ejecucion)...
    EQUIV 90.9% | honest 90.9% | divergent 9.1%
  # bucket por script: identico al de la rama (solo stdlib.py DIVERGENT_MISMATCH)
  ```

- **No es el adaptador.** Cada ejemplo se mide sobre la pagina entera (estado
  compartido, como el REPL) con el adaptador propio del corpus
  (`tools/parity_corpus_v4.python_to_piton`): 51/52 identicos.
- **No es un aborto a mitad.** `rc` nativo = `rc` oraculo = 0 y los 52
  marcadores de ejemplo se emiten en los dos lados (un ejemplo no alcanzado se
  marcaria `NO_EJECUTADO`, no "igual").
- **No es el fichero del corpus.** `corpus_v4/tutorial/stdlib.py` no se
  modifica: la divergencia es de PITON.

## 7. Artefacto de arnes documentado (no afecta a la metrica)

El corpus ejecuta el nativo y el oraculo **en el mismo directorio temporal**, y
`os.system('mkdir today')` (ejemplo 4) crea el directorio la primera vez. Como
el nativo corre primero, el oraculo encuentra `today` ya creado y `mkdir`
escribe en **stderr**:

```
stderr oracle: mkdir: no se puede crear el directorio «today»: El archivo ya existe
```

stderr no entra en la comparacion (solo stdout y exit code), asi que la metrica
no cambia; queda anotado para que no se confunda con una divergencia.

## 8. Conclusion honesta

**Tier B sigue en 10/11 EQUIV (90.9%).** `stdlib.py` es el unico script
DIVERGENT y lo es por **un solo ejemplo de 52**, `help(os)`, que no es cerrable
sin fabricar internals de CPython. No se ha tocado `piton/` para este frente:
no hay arreglo de codegen que produzca la pagina de `pydoc` de otro
interprete byte a byte.