# Pitón

**Python, pero ahora las palabras reservadas también hablan español.**

Pitón no inventa otra máquina ni otra semántica: traduce una superficie en
español de México a Python ordinario y deja que CPython haga lo que ya sabe
hacer.

Es un proyecto personal/esolang de Danny, medio de broma pero ejecutable de
verdad. No es una traducción oficial de Python ni tiene afiliación con la
Python Software Foundation.

```text
código .piton
    -> CST Pitón
    -> MIR (intermedia con tipado estático)
    -> x86-64 nativo PE (Windows) / ELF (Linux)
    -> ejecución sin CPython
```

## Qué NO es Pitón (congelado 2026-10-04)

> **Congelado en el commit `ce9aa11`.** Estas afirmaciones describen el
> alcance del proyecto y no cambian con cada gate. Para modificarlas se
> requiere evidencia nueva (corpus diferencial, receipt, CI) o una decisión
> explícita de Danny. El resto del README (checklist nativo, gates,
> comandos) sigue vivo y se actualiza con la evidencia.

Pitón **no es**:

- Un producto oficial de Python ni de la Python Software Foundation: es un
  proyecto personal/esolang.
- Una máquina ni una semántica nueva: la semántica observable es la de
  CPython (oracle congelado: `ORACLE.md` y `docs/PARITY_DEFINITION.md`); el
  español de México es solo la superficie (keywords + aliases de builtins).
- Un compilador de todo Python: el backend nativo cubre un **subconjunto
  demostrado y declarado** (`docs/FEATURE_STATUS_MATRIX.md`). Lo que no está
  demostrado se rechaza en compilación; nunca se emite una aproximación.
- Un reemplazo de CPython: el PE/ELF nativo solo ejecuta programas dentro del
  subconjunto demostrado. No trae la stdlib de CPython, no expone su C API y
  no es binariamente compatible con `PyObject`, bytecode ni `.pyc` (NO-goals
  de `docs/PARITY_DEFINITION.md` §5).
- Una promesa de "paridad completa": el término `FULL_PARITY` está retirado.
  La paridad se mide por superficies con gates de alcance explícito; pasar el
  corpus diferencial ≠ semántica completa.
- Un proyecto de rendimiento: no hay claims de velocidad frente a CPython;
  el objetivo es correcto, no rápido.
- Self-hosted: el compilador está escrito en Python, no en Pitón.

## Qué SÍ hace (estado verificado 2026-10-04, commit `ce9aa11`)

- **Traduce superficie español → Python ordinario** (source-to-source,
  líneas 1:1) y lo ejecuta con CPython: `piton ejecutar`, `piton traducir`,
  `piton verificar`, `piton repl` e import hook de `.piton`.
- **Compila un subconjunto demostrado a x86-64 nativo** — PE Windows
  (NASM + MinGW) y ELF estático Linux (GCC freestanding) — que corre **sin
  CPython** en el runtime. Lista cerrada: sección «Qué puede compilar
  nativamente».
- **Produce evidencia diferencial contra el oracle CPython**: corpus de 1220
  casos → 1219 EQUIV (99.9%), FAIL_CLOSED 0, DIVERGENT 0, TIMEOUT 1 (entrada
  documentada donde el oracle tampoco termina) —
  `docs/parity_corpus_report.md`.
- **CI verde en ambas plataformas** en el commit `ce9aa11`: test-linux,
  test-windows y verify-oracle (run 37245148477).
- **Falla cerrado** (regla de oro): compatibilidad demostrada o error de
  compilación; nunca semántica aproximada silenciosa.

## Arranque rápido

Requiere CPython 3.12 o posterior. Fue probado con CPython 3.12.4 en Windows.

> **Documentación de autoridad para continuar PITÓN:** si vas a trabajar en
> paridad con CPython 3.12, lee primero `docs/PITON_CPYTHON_3_12_MASTER_ROADMAP.md`
> y `docs/FRESH_SESSION_HANDOFF.md`. Ver también `docs/PARITY_DEFINITION.md`,
> `docs/CPYTHON_PARITY_DEPENDENCY_DAG.md`, `docs/FEATURE_STATUS_MATRIX.md` y
> `docs/STDLIB_PARITY_MATRIX.md`.

```powershell
py -m pip install -e . --no-build-isolation
piton ejecutar examples\01_hola.piton
```

Salida real:

```text
Hola, mundo
```

## Validacion Windows desde otra maquina

La validacion PE de Windows se comunica por este repositorio. En la maquina
Windows limpia, instala Git, PowerShell, CPython 3.12, NASM y GCC/MinGW en
`PATH`, y ejecuta:

```powershell
git clone https://github.com/DannyBaanks/Piton.git
Set-Location PITON
py -3.12 -m pip install -e . --no-build-isolation
.\windowsvalidate.ps1 -FullSuite -Publish
```

`windowsvalidate.ps1` ejecuta el corpus focalizado de T2/T5, `test_phase14` y,
con `-FullSuite`, la regresion PE completa. Escribe el receipt, el resumen y el
log crudo en `validation/windows/`. Con `-Publish` solo hace stage de esa
carpeta, crea un commit y lo publica en el branch actual de `origin`; nunca
sube cambios de implementacion que ya estuvieran en el checkout.

Resultados publicados:

- `validation/windows/latest.json`: receipt con commit, host, herramientas,
  comandos, exit codes y estado del worktree.
- `validation/windows/latest.md`: resumen legible.
- `validation/windows/latest.log`: salida cruda.
- `validation/windows/windowsvalidate-<UTC timestamp>.*`: historial inmutable.

Un `PASS` de Windows no cierra por si solo un gate cross-platform: debe
combinarse con el receipt Linux correspondiente.

## El lenguaje

| Pitón | Python | Pitón | Python |
|---|---|---|---|
| `si` | `if` | `sino_si` | `elif` |
| `sino` | `else` | `para` | `for` |
| `mientras` | `while` | `en` | `in` |
| `funcion` | `def` | `devolver` | `return` |
| `producir` | `yield` | `clase` | `class` |
| `intentar` | `try` | `excepto` | `except` |
| `finalmente` | `finally` | `lanzar` | `raise` |
| `con` | `with` | `como` | `as` |
| `importar` | `import` | `desde` | `from` |
| `Verdadero` | `True` | `Falso` | `False` |
| `Nada` | `None` | `no` | `not` |
| `y` | `and` | `o` | `or` |
| `romper` | `break` | `continuar` | `continue` |
| `pasar` | `pass` | `lambda` | `lambda` |
| `asincrono` | `async` | `esperar` | `await` |
| `segun` | `match`* | `caso` | `case`* |
| `afirmar` | `assert` | `borrar` | `del` |
| `no_local` | `nonlocal` | `es` | `is` |
| `tipo` | `type`* | `global` | `global` |

\* soft keywords: `segun`, `caso` y `tipo` se transforman sólamente en contexto
de statement. Fuera de eso se conservan como identificadores normales.

Aliases de builtins (se traducen como calls Y como loads):

| Pitón | Python | Pitón | Python |
|---|---|---|---|
| `imprimir` | `print` | `entrada` | `input` |
| `rango` | `range` | `longitud` | `len` |
| `enumerar` | `enumerate` | `lista` | `list` |
| `diccionario` | `dict` | `conjunto` | `set` |
| `tupla` | `tuple` | `entero` | `int` |
| `decimal` | `float` | `texto` | `str` |
| `booleano` | `bool` | `abrir` | `open` |
| `ordenar` | `sorted` | | |

## Lado a lado

Pitón:

```python
funcion saludar(nombre):
    si no nombre:
        devolver "¿y tú quién eres alv?"
    sino:
        devolver "qué onda " + nombre

personas = ["Danny", "Ada", ""]
para indice, persona en enumerar(personas):
    imprimir(indice, saludar(persona))
```

Python generado:

```python
def saludar(nombre):
    if not nombre:
        return "¿y tú quién eres alv?"
    else:
        return "qué onda " + nombre

personas = ["Danny", "Ada", ""]
for indice, persona in enumerate(personas):
    print(indice, saludar(persona))
```

El ejemplo completo vive en `examples/programa_completo.piton`; su versión
Python escrita directamente está en
`examples/equivalentes/programa_completo.py`.

## CLI

```powershell
piton ejecutar examples\01_hola.piton       # traduce y ejecuta
piton traducir examples\01_hola.piton       # muestra Python generado
piton traducir examples\01_hola.piton -o h.py  # guarda a archivo
piton verificar examples\programa_completo.piton  # valida sin ejecutar
piton tokens examples\01_hola.piton         # muestra tokens y cambios
piton ast examples\01_hola.piton            # muestra AST de Python generado
piton repl                                  # REPL interactivo
```

## Compilación nativa x86-64

Pitón compila un subconjunto cada vez mayor a executables nativos que **no
dependen de CPython**. Hay dos backends:

| Backend | Formato | Cadena de herramientas |
|---|---|---|
| Windows x86-64 | PE (`.exe`) | NASM + GCC (MinGW) |
| Linux x86-64 | ELF estático | GCC (freestanding) |

### Qué puede compilar nativamente (subconjunto demostrado)

```text
[x] Enteros, flotantes, strings, booleanos, None
[x] Asignación, si/sino, mientras, para
[x] Funciones con argumentos: ABI de 4 registros + frame ABI dinámica (más de 4 parámetros en funciones y métodos)
[x] *args / **kwargs, defaults, positional-only, keyword-only, annotations
[x] Unpacking dinámico en la llamada: f(*xs), f(**d) (hasta 4 parámetros, claves string estáticas)
[x] Operadores: +, -, *, /, //, %, **, ==, !=, <, >, <=, >=
[x] Operadores bit a bit: &, |, ^, ~, <<, >>
[x] Unarios: +, -, not
[x] Strings Unicode: UTF-8 byte-identical vs CPython 3.12.4 (café, ñ, 日本語, etc.)
[x] Concatenación de strings (malloc + memcpy)
[x] Comparación de strings (strcmp)
[x] BigInt arbitrary-precision integers (Win64 + Linux, 14/14 + 13/13 differential tests)
[x] Colecciones: listas, tuplas, diccionarios, conjuntos
[x] Acceso a elementos: get_item, collection_len
[x] Heap objects: object_new, set_attr, get_attr, method_call
[x] Bound methods: m = objeto.metodo; m(...); __self__; resolución por MRO
[x] Decoradores: @dec sobre funciones de módulo, orden bottom-up, rebind del nombre
[x] Lanzar X desde Y (causa almacenada y visible), BaseException catch-all, bare lanzar desde catch-all propaga
[x] Excepciones tipadas: raise/except/finally con flag-based unwind
[x] Closures: captures dinámicas, frame ABI, callbacks, recursión, fábricas
[x] Generadores: frames suspendidos reales, send/throw/close
[x] Clases: campos, __init__, métodos, @property (getter/setter/deleter)
[x] Herencia C3/MRO, super() zero-arg, multinivel
[x] Imports: paquetes, relative, star, ciclos (orden de inicialización CPython)
[x] Async: coroutines reales, await, async for, async generators
[x] async with: __aenter__/__aexit__ como coroutines awaited
[x] Excepciones dentro de coroutines (raise/catch dentro del state machine)
[x] Scheduler: create_task/gather/sleep(0)/cancel, FIFO determinista
[x] Timers reales: asyncio.sleep(n>0) (Win Sleep / Linux nanosleep syscall 35)
[x] With múltiple: con A() como a, B() como b (nested lowering)
[x] math.sqrt nativo: SSE sqrtsd
[x] División entera/piso: coincide con Python
[x] yield from (`producir desde sub()`) con send forwarding y valor de retorno
[x] Builtins tier 1: all/any/bin/chr/ord/pow/round; int/float/str/bool/texto; math.sqrt/floor/ceil/trunc/fabs/gcd
[x] `__del__` exactamente una vez por objeto; GC de ciclos en Windows; freelist+refcount en Linux
[x] MIR con clasificación de efectos (PURE/READ/WRITE/IO/OPAQUE) y verificador de cadena
```

### Compilar desde la CLI

```powershell
# PE Windows x86-64
py -m piton compilar examples\01_hola.piton --backend=x86 --output hola.exe

# ELF Linux x86-64 mediante WSL
py -m piton compilar examples\01_hola.piton --backend=linux --output hola-linux
```

Para producir un recibo con hashes, imports PE, ejecución con entorno vacío y
comparación contra el oracle congelado:

```powershell
py -m piton compilar examples\01_hola.piton --backend=x86 --output build\native-subset-1\hola.exe --evidencia build\native-subset-1\receipt.json
```

El contrato exacto vive en `NATIVE_SUBSET_1_0.md`.

### Qué NO compila nativamente (rechazado o pendiente)

```text
[ ] Metaclasses
[ ] Decoradores sobre métodos/clases/generadores
[ ] Stdlib amplia (demostrado tier 1: abs/min/max/sum/type/len/print,
    int/float/str/bool, all/any/bin/chr/ord/pow/round,
    math.sqrt/floor/ceil/trunc/fabs/gcd)
[ ] FFI nativo / ctypes
[ ] GC Linux de ciclos dict/set y `gc.collect()` público
[ ] Interleaving concurrente durante `asyncio.sleep(n>0)` (documentado divergente)
```

> **Actualización posterior al contrato NATIVE_SUBSET_1_0** (no reescrito: es un
> snapshot histórico de ese recibo). Desde entonces el proyecto avanzó mucho
> más allá: cerró los milestones **M2**–**M7**, **M9**, **M10** (with),
> **M13** (finalización: `con`, `__del__` una vez, GC de ciclos en Windows),
> **M14** (builtins tier 1) y la clasificación de efectos del MIR (EFFECT_*_V1
> + MIR_HASH_REBASELINE_V1). El corpus diferencial de paridad cerró en
> **P54** (2026-10-04, tras P53/P52): **1220 casos, 1219 EQUIV (99.9% del
> corpus declarado — no el 99.9% de todo Python), 0 FAIL_CLOSED, 0 DIVERGENT,
> 1 TIMEOUT no terminante también en el oracle** (`docs/parity_corpus_report.md`,
> `docs/parity_corpus_report.json`). Estado vivo:
> `piton/final_dashboard.py` (53 gates, todos PASS) y
> `docs/FEATURE_STATUS_MATRIX.md`.

### Regla de oro

```text
compatibilidad demostrada o error de compilación;
nunca semántica aproximada silenciosa.
```

### Demostración limpia (sin Python)

El backend Linux genera un ELF freestanding que arranca como único userspace
de una VM QEMU/TCG sin libc, sin Python, sin shell:

```powershell
py -m unittest tests.test_phase10_linux -v
```

Salida real verificada:

```text
test_static_elf_boots_as_only_userspace_in_qemu ... ok
test_static_elf_runs_inside_empty_chroot ... ok
test_static_elf_x86_64_runs_with_empty_environment ... ok

Ran 3 tests in 48.580s
OK
```

Para ejecutables Windows (PE), el differential oracle compara el output del
nativo contra CPython 3.12.4:

```powershell
py -m unittest tests.test_phase5 -v
```

Salida verificada: 37 tests OK del backend PE y su corpus diferencial.

### Dashboard

```powershell
py -m piton.final_dashboard --format summary --native-receipt build\native-subset-1\receipt.json
```

Gates actuales: **53 PASS** (derivado de `piton/final_dashboard.py` al HEAD del
2026-10-05; el dashboard embebe la declaración de cada gate). Lista completa y
verificable:

```powershell
python3 -m piton.final_dashboard --format markdown
```

Subconjunto representativo:

| Gate | Estado |
|---|---|
| GRAMMAR_PARITY | PASS |
| SEMANTIC_DIFFERENTIAL_CORPUS | PASS |
| X86_64_WINDOWS | PASS |
| X86_64_LINUX | PASS |
| CLEAN_MACHINE_EXECUTION | PASS |
| NATIVE_RUNTIME | PASS |
| NATIVE_EXCEPTION_MODEL | PASS |
| NATIVE_IMPORT_SYSTEM | PASS |
| NATIVE_OBJECT_PROTOCOL | PASS |
| NATIVE_ASYNC | PASS |
| DYNAMIC_RUNTIME_V1 | PASS |
| MATH_TIER1_V1 | PASS |
| GC_CYCLES_V1 | PASS |
| PITON_NATIVE_SUBSET_PARITY | PASS |

`PARTIAL` significa que el subconjunto demostrado pasa; el alcance completo
está abierto. `NOT_DEMONSTRATED` significa que aún no hay evidencia suficiente
para afirmar la afirmación.

### Estado nativo verificado (2026-10-05)

```text
Suite completa Linux: 811 passed / 26 skipped / 133 subtests
  (python3 -m pytest tests/ -q --ignore=tests/test_windows_validate.py)
CI verde: test-linux + test-windows + verify-oracle (run 37245148477, ce9aa11)
Corpus diferencial enumerativo: 1220 casos
  → 1219 EQUIV (99.9% del corpus declarado), 0 FAIL_CLOSED, 0 DIVERGENT,
    1 TIMEOUT no terminante también en el oracle
    (class-iter-self: entrada mala del corpus, no divergencia)
Dashboard: 53/53 gates PASS
  (python3 -m piton.final_dashboard --format summary)
test_phase10_linux.py: 246 passed / 12 skipped
  (ELF estático en QEMU, chroot y entorno vacío incluidos)
Milestones cerrados: M2 frames/llamadas, M3 closures, M4 iteradores,
  M5 generadores (incl. yield from, send/throw/close), M6 objetos/MRO,
  M7 excepciones, M9 async completo, M10 with múltiple,
  M13 finalización (__del__ + GC de ciclos Windows), M14 builtins tier 1,
  ME efectos MIR (EFFECT_*_V1 + MIR_HASH_REBASELINE_V1)
  M14 = BUILTINS_CORE_V2 + TYPE_CONVERSION_V1 + MATH_TIER1_V1:
      all/any/bin/chr/ord/pow/round; int/float/str/bool;
      sqrt/floor/ceil/trunc/fabs/gcd, pi/e; UTF-8 completo; round half-even;
      excepciones capturables; divergencias: pow(int, neg),
      any/all solo list|tuple, round sin ndigits
```

> Los números 300/300, 220/220 y 520/520 de backend del 2026-09-19 se
> conservan como evidencia histórica (git log + `validation/windows/`); no
> describen el estado CURRENT. La nota de WSL/timeout del 2026-09-12 fue
> latencia de infraestructura, no lógica, y hoy la suite corre en host Linux
> nativo.

**Pendientes reales (honesto):** metaclasses; decoradores sobre
métodos/clases/generadores; stdlib amplia fuera del tier demostrado; FFI
nativo/ctypes; GC de ciclos dict/set en Linux con `gc.collect()` público; e
interleaving concurrente durante `asyncio.sleep(n>0)` (documentado
divergente). PITÓN está "terminado" en el sentido del alcance nativo actual
declarado: corpus enumerativo sin divergencias observadas, Windows y Linux, y
fail-closed fuera del subconjunto. No es una reimplementación completa de
CPython 3.12 — ver «Qué NO es Pitón».

## Por qué no usa `replace()`

`str.replace("si", "if")` no sabe si está viendo sintaxis, un comentario,
texto para el usuario o una parte de `sistema`. Pitón usa `tokenize`, el lexer
de la biblioteca estándar de Python, y sólo considera tokens `NAME` completos.

Después reconstruye la fuente con `tokenize.untokenize` y la valida con
`ast.parse`. Por eso estos textos no se mutilan:

```python
# si sino para funcion devolver imprimir
mensaje = "si sino para funcion devolver imprimir"
sistema = parasol = sinoidal = funcionaria = 1
```

La traducción no agrega ni elimina saltos de línea. Los números de línea se
mantienen 1:1; las columnas pueden moverse porque `funcion` y `def` no miden lo
mismo.

## F-strings (v0.5)

Las expresiones dentro de f-strings se traducen:

```python
# Pitón:
f"hola {imprimir}"
f"total: {suma(x)}"
f"llaves {{imprimir}}"  # escapadas, intactas

# Python generado:
f"hola {print}"
f"total: {sum(x)}"
f"llaves {{imprimir}}"
```

## Errores remapeados (v0.6)

Los errores de sintaxis se remapean a las ubicaciones originales del `.piton`:

```text
PITON_SYNTAX_ERROR
archivo: tests\fixtures\error_sintaxis.piton
linea: 1
columna: 8

Python rechazó la traducción:
expected ':'
```

## Import hook (v0.7)

Los archivos `.piton` se pueden importar directamente:

```python
import mi_modulo  # busca mi_modulo.piton en sys.path
from paquete import submodulo  # soporta paquetes e imports relativos
```

El hook se instala con `piton.instalar_hook()` o automáticamente al usar
`piton.ejecutar_archivo()`.

## REPL (v0.8)

```powershell
piton repl
```

```text
Pitón REPL — Python hablando español
>>> imprimir("hola")
hola
>>> si True:
...     imprimir("sí")
sí
```

## API de compilación

```python
from piton import compilar
codigo = compilar('imprimir("ok")\n')
exec(codigo)
```

## Pruebas y evidencia

Suite completa:

```powershell
py -m unittest discover -s tests -v
```

Resultado real en CPython 3.12.4:

```text
Ran 149 tests

OK
```

Resumen ejecutable de evidencia:

```powershell
py tests\evidence.py
```

Salida real: exit 0. El script separa `WINDOWS = PASS` de
`LINUX_MACOS = NOT_DEMONSTRATED`; no infiere plataformas no ejecutadas.

## Estructura

```text
PITON/
|-- piton/
|   |-- __init__.py
|   |-- __main__.py
|   |-- cli.py
|   |-- parser.py           # Parser -> CST Pitón
|   |-- mir.py              # MIR intermedia
|   |-- lower.py            # MIR -> Python lowerer
|   |-- optimizer.py        # Optimizador MIR
|   |-- x86.py              # Backend Windows PE (NASM + GCC)
|   |-- linux_x86.py        # Backend Linux ELF (GCC freestanding)
|   |-- native_runtime.c    # Runtime C11 vinculado a cada PE
|   |-- native_differential.py  # Diferential oracle (nativo vs CPython)
|   |-- native_evidence.py      # Recibo verificable del Native Subset 1.0
|   |-- call_runtime.py     # Closures + excepciones
|   |-- async_runtime.py    # Async runtime
|   |-- object_protocol.py  # Protocolo de objetos
|   |-- module_runtime.py   # Sistema de imports
|   |-- stdlib_runtime.py   # Stdlib scope declarado
|   |-- final_dashboard.py  # Dashboard Fase 14
|   |-- runtime.py          # Runtime Python
|   |-- translator.py       # Traductor Python
|   |-- import_hook.py      # Import hook
|   `-- repl.py
|-- examples/
|   |-- equivalentes/
|   |-- 01_hola.piton ... 09_soft_keywords.piton
|   `-- programa_completo.piton
|-- tests/
|   |-- fixtures/
|   |-- evidence.py
|   |-- test_phase5.py           # Gates nativos Win64 (~130 tests)
|   |-- test_phase10_linux.py    # Gates Linux (ELF + chroot + QEMU)
|   |-- test_phase8_10.py        # Bloques 1-10 (clases, imports, async)
|   |-- test_phase11_13.py       # Fases 11-13 (eval, stdlib, MIR)
|   |-- test_phase14.py          # Dashboard assertions
|   |-- test_windows_validate.py  # Corpus focalizado PE T2/T5
|   `-- test_cli_and_corpus.py
|-- windowsvalidate.ps1          # Validador y publicador Windows
|-- validation/windows/          # Receipts publicados por la Xeon
|-- GUIA.md                  # Guia operativa comandos reales
|-- NATIVE_COMPATIBILITY.md  # Matriz de compatibilidad nativa
|-- NATIVE_SUBSET_1_0.md     # Contrato del milestone x86 acotado
|-- ABI.md                   # ABI nativa
|-- ROADMAP.md
`-- README.md
```

## Licencia

MIT. Ver `LICENSE`.
