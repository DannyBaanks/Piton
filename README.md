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
> requieren evidencia nueva (corpus diferencial, receipt, CI) o una decisión
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

## Qué SÍ hace (estado verificado 2026-10-24)

- **Traduce superficie español → Python ordinario** (source-to-source,
  líneas 1:1) y lo ejecuta con CPython: `piton ejecutar`, `piton traducir`,
  `piton verificar`, `piton repl` e import hook de `.piton`.
- **Compila un subconjunto demostrado a x86-64 nativo** — PE Windows
  (NASM + MinGW) y ELF estático Linux (GCC freestanding) — que corre **sin
  CPython** en el runtime. Lista cerrada: sección «Qué puede compilar
  nativamente».
- **Produce evidencia diferencial contra el oracle CPython**:
  - corpus v1: 1220 casos → 1219 EQUIV (99.9%), FAIL_CLOSED 0, DIVERGENT 0,
    TIMEOUT 1 (entrada documentada donde el oracle tampoco termina) —
    `docs/parity_corpus_report.md`.
  - corpus v2: 221/221 EQUIV (100%) —
    `docs/parity_corpus_v2_report_2026-10-10.md`.
  - corpus v3: 235/235 EQUIV (100%) sobre 12 áreas avanzadas (str/set/frozenset
    methods, memoryview, slices con paso, complejos profundos, excepciones
    multi-arg, numerics, object/callable protocol) —
    `docs/parity_corpus_v3_report_2026-10-10.md`.
  - **corpus v4 (stdlib real de CPython 3.12)**: embudo sobre 167 archivos de
    `/usr/lib/python3.12/` — adapt 167/167 (100%), parse 167/167 (100%),
    MIR 28/167 (16.8%), build 23/167 (13.8%); `MIR_FAIL` 139, `BUILD_FAIL` 5.
    El cuello restante, medido el 2026-10-24: 64 de los 139 MIR_FAIL siguen
    siendo **módulos de la stdlib ausentes** y 5 archivos ya alcanzan el build
    y fallan por gaps del emisor que el muro de imports ocultaba
    (`conjunto()` de entero en `bdb`, `input()` con prompt no-str en `cmd`,
    `pickle()` con un parámetro usado como str en `copyreg`, `tupla()` de un
    `map` en `operator`). Reporte vivo:
    `docs/parity_corpus_v4_report_2026-10-24.{json,md}`
    (re-ejecutable: `python3 tools/parity_corpus_v4.py run --tier a`).
  - **corpus v4 recursivo** (`--recursive`, stdlib completa con subpaquetes,
    588 archivos): adapt 99.8%, parse 97.8%, **mir 82 (13.9%), build 71
    (12.1%)**; `MIR_FAIL` 493, `PARSE_FAIL` 12 (anotaciones `match/case`,
    star-unpacking), `BUILD_FAIL` 11, `CRASH` 1 (`pydoc_data/topics.py`:
    el compilador agota memoria — el único CRASH). 261 MIR_FAIL son módulos
    ausentes. Medición y palanca: `docs/corpus-recursivo-2026-10-24.md`
    (re-ejecutable: `python3 tools/parity_corpus_v4.py run --tier a
    --recursive --workers 1 --isolate` — **`--isolate` es obligatorio**: sin
    él un archivo mata el worker del pool y la corrida muere siempre).

- **Biblioteca nativa empaquetada** (`piton/stdlib_piton/`, 60 módulos):
  módulos escritos **en Pitón** que se compilan e inlinean cuando el
  programa los importa (el corpus es aislado: un módulo importado tiene que
  estar en el binario). Dos clases, y la distinción es explícita:
  - **Reales** (verificados contra CPython por gates): `posixpath`
    (join/split/splitext/normpath/splitdrive), `string` (constantes +
    capwords), `re` (escape real, flags con los valores de CPython), `io`
    (open→abrir, SEEK_*, DEFAULT_BUFFER_SIZE).
  - **Stubs fail-closed**: compilan y, al ejecutarse, levantan
    `NotImplementedError` — nunca divergen en silencio. `re` (no hay motor
    de regex), `time` (sin syscall de reloj), `abc` y los módulos de
    plataforma (`socket`, `struct`, `collections`, `types`, `builtins`,
    `hashlib`, `itertools`, `importlib.util`...).
  Aparte, `None` tiene identidad propia (`NONE_SINGLETON_V2`: un valor
  distinguible de `0`, que era el muro de `netrc.py` y `warnings.py`, los
  dos compilan punta a punta).
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

La validacion PE de Windows se comunica por este repositorio: en la maquina
Windows limpia (Git, PowerShell, CPython 3.12, NASM y GCC/MinGW en `PATH`):

```powershell
git clone https://github.com/DannyBaanks/Piton.git
Set-Location PITON
py -3.12 -m pip install -e . --no-build-isolation
.\windowsvalidate.ps1 -FullSuite -Publish
```

`windowsvalidate.ps1` ejecuta el corpus focalizado de T2/T5, `test_phase14` y,
con `-FullSuite`, la regresion PE completa. Escribe el receipt, el resumen y el
log crudo en `validation/windows/` (`latest.json`, `latest.md`, `latest.log` e
historial inmutable `windowsvalidate-<UTC timestamp>.*`). Con `-Publish` solo
hace stage de esa carpeta: nunca sube cambios de implementacion.

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
| `ordenar` | `sorted` | `octetos` | `bytes` |
| `arreglo_bytes` | `bytearray` | | |

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

Entry points: `pi` (recomendado), `piton`, `pitn` o `python -m piton`.

```text
pi --help                                   # descubre toda la superficie
pi run hola.piton                           # ejecuta con CPython
pi run hola.piton --engine native           # compila a nativo y ejecuta
pi check hola.piton                         # sintaxis + lowering a MIR, sin emitir
pi build hola.piton                         # nativo para el host (PE Windows / ELF Linux)
pi build hola.piton --target linux-x86_64 -o hola
pi emit python hola.piton                   # Python generado (stdout, pipelineable)
pi emit ast hola.piton                      # AST del Python generado
pi emit tokens hola.piton                   # tokens y transformaciones
pi emit mir hola.piton                      # MIR JSON determinista
pi targets                                  # targets y toolchain real del host
pi doctor                                   # diagnostica el entorno del compilador
pi evidence --format summary                # evidencia verificada del compilador
pi test tests/test_phase14.py -q            # pruebas
pi corpus --areas literals --limit 4        # corpus diferencial focalizado
pi version / pi --version / pi -V           # la misma fuente: piton.__version__
```

Los aliases históricos siguen funcionando sin warning: `ejecutar`,
`traducir`, `verificar`, `tokens`, `ast`, `compilar`. El contrato completo
de la superficie vive en `docs/CLI_CONTRACT.md`.

## Compilación nativa x86-64

Pitón compila un subconjunto cada vez mayor a ejecutables nativos que **no
dependen de CPython**. Hay dos backends:

| Backend | Formato | Cadena de herramientas |
|---|---|---|
| Windows x86-64 | PE (`.exe`) | NASM + GCC (MinGW) |
| Linux x86-64 | ELF estático | GCC (freestanding) |

### Qué puede compilar nativamente (subconjunto demostrado)

```text
[x] Enteros, flotantes, strings, booleanos, None
[x] Asignación, si/sino, mientras, para
[x] Funciones con argumentos: ABI de 4 registros + frame ABI dinámica (más de 4 parámetros)
[x] *args / **kwargs, defaults, positional-only, keyword-only, annotations
[x] Unpacking dinámico en la llamada: f(*xs), f(**d) (hasta 4 parámetros, claves string estáticas)
[x] Operadores: +, -, *, /, //, %, **, ==, !=, <, >, <=, >=
[x] Operadores bit a bit: &, |, ^, ~, <<, >>
[x] Unarios: +, -, not
[x] Strings Unicode: UTF-8 byte-identical vs CPython 3.12.4 (café, ñ, 日本語, etc.)
[x] Concatenación de strings (malloc + memcpy) · Comparación de strings (strcmp)
[x] BigInt arbitrary-precision integers (Win64 + Linux, 14/14 + 13/13 differential tests)
[x] Colecciones: listas, tuplas, diccionarios, conjuntos, bytes, bytearray
[x] Números complejos (Linux): aritmética bit-exacta, repr byte-idéntico, abs, .real/.imag
[x] Operaciones de bytes/bytearray: indexado, asignación, slicing con/sin paso, + , * , métodos
[x] Acceso a elementos: get_item, collection_len
[x] Heap objects: object_new, set_attr, get_attr, method_call
[x] Bound methods: m = objeto.metodo; m(...); __self__; resolución por MRO
[x] Decoradores: @dec sobre funciones de módulo, orden bottom-up, rebind del nombre
[x] Excepciones tipadas: raise/except/finally, multi-arg con e.args, lanzar X desde Y
[x] Closures: captures dinámicas, frame ABI, callbacks, recursión, fábricas
[x] Generadores: frames suspendidos reales, send/throw/close, yield from
[x] Clases: campos, __init__, métodos, @property, herencia C3/MRO, super() zero-arg
[x] Imports: paquetes, relative, star, ciclos (orden de inicialización CPython)
[x] Async: coroutines reales, await, async for, async generators, async with
[x] Scheduler: create_task/gather/sleep(0)/cancel, FIFO determinista
[x] Timers reales: asyncio.sleep(n>0) · With múltiple: con A() como a, B() como b
[x] math.sqrt nativo (SSE sqrtsd) · División entera/piso: coincide con Python
[x] `__del__` exactamente una vez por objeto; GC de ciclos en Windows
[x] MIR con clasificación de efectos (PURE/READ/WRITE/IO/OPAQUE) y verificador de cadena
[x] memoryview: indexado, iteración, tobytes/tolist, slicing con paso, cast("B")
[x] dict: update(**kwargs), fromkeys · str: translate, format_map
[x] `slice.indices(...)`, literales numéricos como primary, .real/.imag de int/float
[x] Clases cargadas como valores (referencia, no instancia), `classmethod` con `cls`
[x] `divmod` con bigint, `bool(objeto)`, asignación compuesta por índice
[x] Tipos deducidos por uso: params de secuencia (list/tuple), elem-kind de
    listas, `getattr(x, 'n', default)` con el tipo del default, tipos de
    atributos de instancia, `cls.__bases__`/`cls.__mro__` como atributos
[x] Emisión en dos pasadas: los tipos de los params se deducen de los
    call-sites (que pueden estar en otra función), así que un atributo de
    instancia o una celda de closure conservan su tipo
[x] Calls a un callable desconocido: f(*args), f(**kw), f(k=v) — el runtime
    arma el frame según el layout del callee
[x] Módulos nativos con constantes: `stat`, `__future__`
[x] Stdlib empaquetada en PITON (piton/stdlib_piton/): el import de un
    módulo empaquetado lo provee, lo baja y lo inlinea en el binario; los
    módulos con estado lo iniciales como globals de nombre cualificado
[x] `print(..., file=X)`, `print(..., end=Y)`, `sys.stdout`/`sys.stderr`
    (descriptores 1 y 2), `sys.version_info`
[x] `os.fspath(x)` (str/bytes pasan; el resto es TypeError con el texto de
    CPython) y `os.PathLike` como clase builtin (`isinstance` la reconoce)
[x] str.format con campos por nombre ({nombre}) y '%r'/'%s' de objetos
[x] Excepciones builtin de CPython (SystemExit, UserWarning, …) por nombre
```

### Compilar desde la CLI

```powershell
# PE Windows x86-64
py -m piton compilar examples\01_hola.piton --backend=x86 --output hola.exe

# ELF Linux x86-64 mediante WSL
py -m piton compilar examples\01_hola.piton --backend=linux --output hola-linux

# Forma canónica equivalente (CLI 1.x):
pi build examples\01_hola.piton --target windows-x86_64 -o hola.exe
pi build examples\01_hola.piton --target linux-x86_64 -o hola-linux
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
[ ] Complejos en el backend Windows, `complejo('1+2j')`, `complejo ** complejo`,
    paso de complejos como argumentos de función (falla cerrado en build)
[ ] Decoradores sobre métodos/clases/generadores
[ ] Stdlib amplia (demostrado tier 1: abs/min/max/sum/type/len/print,
    int/float/str/bool, all/any/bin/chr/ord/pow/round,
    math.sqrt/floor/ceil/trunc/fabs/gcd)
[ ] FFI nativo / ctypes
[ ] GC Linux de ciclos dict/set y `gc.collect()` público
[ ] Interleaving concurrente durante `asyncio.sleep(n>0)` (documentado divergente)
```

> **Actualización posterior al contrato NATIVE_SUBSET_1_0** (no reescrito: es un
> snapshot histórico de ese recibo). Desde entonces el proyecto cerró los
> milestones **M2**–**M7**, **M9**, **M10**, **M13**, **M14**, la clasificación
> de efectos del MIR (EFFECT_*_V1) y el corpus diferencial en **P54** (2026-10-04):
> **1220 casos, 1219 EQUIV, 0 FAIL_CLOSED, 0 DIVERGENT, 1 TIMEOUT**; después el
> **v2 (221/221)** y el **v3 (235/235)**. Estado vivo:
> `piton/final_dashboard.py` (53 gates) y `docs/FEATURE_STATUS_MATRIX.md`.

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

Salida real verificada: los 3 tests (`boots_as_only_userspace_in_qemu`,
`runs_inside_empty_chroot`, `runs_with_empty_environment`) OK en 48.580s. Para
los PE de Windows, el differential oracle compara el output del nativo contra
CPython 3.12.4: `py -m unittest tests.test_phase5 -v` (37 tests OK del
backend PE y su corpus diferencial).

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

### Estado nativo verificado (2026-10-22)

```text
Suite completa Linux: 1172 passed / 26 skipped / 133 subtests / 0 failed
  (python3 -m pytest tests -q --ignore=tests/test_windows_validate.py,
   CPython 3.12.3; commit df2e534)
Corpus v4 (stdlib real, tier A, 167 archivos): adapt 100% | parse 100%
  | MIR 14.4% | build 11.4%; fail_classes MIR_FAIL 143, BUILD_FAIL 5
  (docs/parity_corpus_v4_report_2026-10-22_after-pct-unknown.{json,md};
   nuevo en build: copyreg.py — '%s' con operando de tipo desconocido aplica
   str() de runtime en vez de fallar cerrado)
Corpus diferencial enumerativo v3: 235 casos (12 áreas)
  -> 235 EQUIV (100.0%), 0 FAIL_CLOSED, 0 DIVERGENT, 0 TIMEOUT
Corpus diferencial v2: 221/221 EQUIV (100%)
Corpus diferencial v1: 1220 casos -> 1219 EQUIV (99.9% del corpus declarado),
  0 FAIL_CLOSED, 0 DIVERGENT, 1 TIMEOUT no terminante también en el oracle
  (class-iter-self: entrada mala del corpus, no divergencia)
Dashboard: 53/53 gates PASS
test_phase10_linux.py: 246 passed / 12 skipped
  (ELF estático en QEMU, chroot y entorno vacío incluidos)
Milestones cerrados: M2 frames/llamadas, M3 closures, M4 iteradores,
  M5 generadores (incl. yield from, send/throw/close), M6 objetos/MRO,
  M7 excepciones, M9 async completo, M10 with múltiple,
  M13 finalización (__del__ + GC de ciclos Windows), M14 builtins tier 1,
  ME efectos MIR (EFFECT_*_V1 + MIR_HASH_REBASELINE_V1)
```

Reejecutables:

```bash
python3 -m pytest tests -q --ignore=tests/test_windows_validate.py
python3 tools/parity_corpus_v4.py run --tier all \
  --out docs/parity_corpus_v4_report_2026-10-20_after-cell-order-v1.json \
  --markdown docs/parity_corpus_v4_report_2026-10-20_after-cell-order-v1.md
python3 tools/parity_corpus_v3.py \
  --out docs/parity_corpus_v3_report_2026-10-10.json \
  --markdown docs/parity_corpus_v3_report_2026-10-10.md
py tests\evidence.py
```

Cada caso del corpus compara la salida y el exit del ELF nativo contra
CPython (`piton/native_differential.py`); los resultados viven en
`docs/parity_corpus*_report*.{json,md}`. `py tests\evidence.py` separa
`WINDOWS = PASS` de `LINUX_MACOS = NOT_DEMONSTRATED`: no infiere plataformas no
ejecutadas.

> Evidencia histórica (no re-ejecutada hoy): CI verde en `ce9aa11` (run
> 37245148477) y los números de backend 300/300, 220/220 y 520/520 del
> 2026-09-19 se conservan como registro de git + `validation/windows/`; no
> describen el estado CURRENT. La suite con `py -m unittest discover -s tests
> -v` reportó `Ran 149 tests / OK` en CPython 3.12.4 (registro de git).

**Pendientes reales (honesto):** metaclasses; decoradores sobre
métodos/clases/generadores; stdlib amplia fuera del tier demostrado; FFI
nativo/ctypes; GC de ciclos dict/set en Linux con `gc.collect()` público;
interleaving concurrente durante `asyncio.sleep(n>0)` (documentado divergente);
y en el corpus v4 los 7 archivos "alcanzables" que piden features de runtime
(`globals()` como dict real, clases dinámicas, objetos de archivo, metaclass de
`abc`, `print(file=)`, `except` con excepción de usuario). PITÓN está
"terminado" en el sentido del alcance nativo actual declarado: corpus
enumerativo sin divergencias observadas, Windows y Linux, y fail-closed fuera
del subconjunto. No es una reimplementación completa de CPython 3.12 — ver
«Qué NO es Pitón».

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

## F-strings, errores remapeados, import hook, REPL

- **f-strings (v0.5)**: las expresiones dentro de f-strings se traducen
  (`f"hola {imprimir}"` → `f"hola {print}"`); las llaves escapadas quedan
  intactas.
- **Errores remapeados (v0.6)**: los errores de sintaxis se remapean a las
  ubicaciones originales del `.piton` (`PITON_SYNTAX_ERROR` + archivo/línea/
  columna + el mensaje de Python).
- **Import hook (v0.7)**: los `.piton` se importan directo
  (`import mi_modulo` busca `mi_modulo.piton` en `sys.path`; `piton.instalar_hook()`
  o automáticamente con `piton.ejecutar_archivo()`).
- **REPL (v0.8)**: `piton repl` — REPL en español con bloques multilínea.

## API de compilación

```python
from piton import compilar
codigo = compilar('imprimir("ok")\n')
exec(codigo)
```

## Estructura

```text
PITON/
|-- piton/
|   |-- parser.py, mir.py, lower.py, optimizer.py   # CST -> MIR -> Python
|   |-- x86.py, linux_x86.py                         # backends PE (NASM) y ELF (GCC)
|   |-- native_differential.py, native_evidence.py   # oracle y recibo
|   |-- call_runtime.py, async_runtime.py            # closures, excepciones, async
|   |-- object_protocol.py, module_runtime.py        # objetos, imports
|   |-- stdlib_runtime.py, bundled_stdlib.py         # stdlib declarada y empaquetada
|   |-- stdlib_piton/                                # fuentes PITON de la stdlib
|   |-- cli.py, translator.py, import_hook.py, repl.py, runtime.py
|   `-- final_dashboard.py                           # 53 gates
|-- examples/            # .piton y sus equivalentes .py
|-- tests/               # gates por fase + test_grammar_parity.py
|-- tools/               # parity_corpus_v4.py (stdlib real, embudo + equiv)
|-- validation/windows/  # receipts de la maquina Windows
|-- GUIA.md, NATIVE_COMPATIBILITY.md, NATIVE_SUBSET_1_0.md, ABI.md, ROADMAP.md
`-- README.md
```

## Licencia

MIT. Ver `LICENSE`.
