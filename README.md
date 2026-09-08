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

```powershell
py -m pip install -e . --no-build-isolation
piton ejecutar examples\01_hola.piton
```

Salida real:

```text
Hola, mundo
```

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
[x] Funciones con argumentos (hasta 4 en Windows)
[x] Operadores: +, -, *, /, //, %, **, ==, !=, <, >, <=, >=
[x] Operadores bit a bit: &, |, ^, ~, <<, >>
[x] Unarios: +, -, not
[x] Concatenación de strings (malloc + memcpy)
[x] Comparación de strings (strcmp)
[x] Colecciones: listas, tuplas, diccionarios, conjuntos
[x] Acceso a elementos: get_item, collection_len
[x] Heap objects: object_new, set_attr, get_attr, method_call
[x] Excepciones tipadas: raise ValueError("x") / except ValueError
[x] Closures inmutables: lambda con capturas por valor
[x] Generadores finitos puros (inline, sin frames suspendidos)
[x] Clases simples: campos escalar + __init__ + métodos directos
[x] Imports multifile: módulos .piton hermanos vinculados en un PE
[x] Async no-suspending: asyncio.run + await como identidad
[x] math.sqrt nativo: SSE sqrtsd
[x] División entera/piso: coincide con Python
```

### Qué NO compila nativamente (rechazado o pendiente)

```text
[ ] Bigint arithmetic (literal como string; cálculo rechazado)
[ ] Closures con celdas mutables / nonlocal
[ ] Generadores con frames suspendidos / yield from
[ ] Clases con herencia / metaclasses
[ ] Paquetes / imports relativos / from-import
[ ] Async con suspensión / cancelación / scheduler
[ ] Stdlib amplia (solo math.sqrt demostrado)
[ ] FFI nativo / ctypes
[ ] Unicode beyond ASCII
```

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

Salida: ~130 tests OK con certeza binaria PE vs CPython.

### Dashboard

```powershell
py -m piton.final_dashboard --format markdown
```

Gates activos:

| Gate | Estado |
|---|---|
| GRAMMAR_PARITY | PASS |
| CLEAN_MACHINE_EXECUTION | PASS |
| X86_64_LINUX | PARTIAL |
| X86_64_WINDOWS | PARTIAL |
| NATIVE_RUNTIME | PARTIAL |
| NATIVE_EXCEPTION_MODEL | PARTIAL |
| CPYTHON_EXECUTION_DEPENDENCY | PARTIAL |
| FULL_PARITY | NOT_DEMONSTRATED |

`PARTIAL` significa que el subconjunto demostrado pasa; el alcance completo
está abierto. `NOT_DEMONSTRATED` significa que aún no hay evidencia suficiente
para afirmar la afirmación.

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
Ran 130 tests

OK
```

Resumen ejecutable de evidencia:

```powershell
py tests\evidence.py
```

Salida real: 36/36 gates PASS.

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
|   `-- test_cli_and_corpus.py
|-- GUIA.md                  # Guia operativa comandos reales
|-- NATIVE_COMPATIBILITY.md  # Matriz de compatibilidad nativa
|-- ABI.md                   # ABI nativa
|-- ROADMAP.md
`-- README.md
```

## Licencia

MIT. Ver `LICENSE`.
