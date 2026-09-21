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
git clone https://github.com/DannyBaanks/PITON.git
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
[ ] yield from (producir desde) — fail-closed en MIR
[ ] Decoradores sobre métodos/clases/generadores
[ ] Stdlib amplia (solo math.sqrt demostrado)
[ ] FFI nativo / ctypes
```

> **Actualización posterior al contrato NATIVE_SUBSET_1_0** (no reescrito: es un
> snapshot histórico de ese recibo). Desde entonces el proyecto pasó a Fase 14 y
> cerró los milestones **M2** (frames/llamadas: unpacking dinámico, bound methods,
> decoradores, frame ABI >4 params) y **M9** (async completo: `asincrono con`,
> excepciones en coroutines y timers reales) — véase
> `piton/final_dashboard.py` (38 gates) y `docs/FEATURE_STATUS_MATRIX.md` para
> el estado CURRENT. Siguen pendientes: metaclasses, `yield from`,
> decoradores generalizados, divergencia documentada: `asyncio.sleep(n>0)`
> bloquea dentro del paso del scheduler (sin interleaving concurrente).

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

Gates activos:

| Gate | Estado |
|---|---|
| NATIVE_SUBSET_1_0 | PASS |
| GRAMMAR_PARITY | PASS |
| SEMANTIC_DIFFERENTIAL_CORPUS | PASS |
| CLEAN_MACHINE_EXECUTION | PASS |
| X86_64_LINUX | PASS |
| X86_64_WINDOWS | PASS |
| NATIVE_RUNTIME | PASS |
| NATIVE_EXCEPTION_MODEL | PASS |
| NATIVE_IMPORT_SYSTEM | PASS |
| NATIVE_OBJECT_PROTOCOL | PASS |
| NATIVE_ASYNC | PASS |
| ASYNC_WITH_V1 | PASS |
| ASYNC_EXCEPTION_V1 | PASS |
| TASK_SCHEDULER_V2 | PASS |
| NATIVE_STDLIB_DECLARED_SCOPE | PASS |
| CPYTHON_EXECUTION_DEPENDENCY | PASS |
| DYNAMIC_RUNTIME_V1 | PASS |
| FULL_PARITY | PASS |

`PARTIAL` significa que el subconjunto demostrado pasa; el alcance completo
está abierto. `NOT_DEMONSTRATED` significa que aún no hay evidencia suficiente
para afirmar la afirmación.

### Estado nativo verificado (2026-09-19)

```text
300/300 tests pass (test_phase5.py, Windows PE)
220/220 tests pass (test_phase10_linux.py, Linux ELF)
520/520 backend tests pass (Win + Linux, incluyendo M14)
M2 (frames/llamadas): PASS — unpacking dinámico, bound methods, decoradores, frame ABI >4
M3 (closures): PASS — variadic closures (pack de *resto en runtime dispatch)
M4 (iteradores): PASS — iter(callable, sentinel), next(it, default)
M5 (generadores): PASS — yield from, devolver v almacenado
M6 (modelo de objetos): PASS — __getattr__/__setattr__/__delattr__, __call__, __eq__/__len__/__str__ por MRO, identidad `es`
M7 (excepciones): PASS — raise from, BaseException, reraise desde catch-all
M9 (async completo): PASS — async with, excepciones en coroutines, timers reales
M10 (with): PASS — con A(), B() multi-item anidado
M14 (builtins tier 1): PASS — BUILTINS_CORE_V2 + TYPE_CONVERSION_V1 + MATH_TIER1_V1
      (all/any/bin/chr/ord/pow/round; int/float/str/bool; sqrt/floor/ceil/trunc/fabs/gcd,
      pi/e; UTF-8 completo, round half-even, excepciones capturables; divergencias:
      pow(int, neg), any/all solo list|tuple, round sin ndigits)
3/3 Linux ELF gates (empty env, chroot, QEMU)
```

Nota de regresión: las suites backend están verificadas 520/520. Algunas pruebas
CLI/evidence compilan artefactos externos y pueden superar el timeout del host
(WSL/toolchain); no se cuentan como PASS hasta completar esa ejecución.

Nota de infraestructura: los tests Linux usan WSL; un arranque frío de la VM
puede superar el timeout de 10 s por ejecución, así que la suite completa
requiere WSL caliente (los 3 fallos del 2026-09-12 eran latencia de WSL, no
lógica — re-ejecutados pasan aislados).

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
