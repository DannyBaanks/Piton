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
    -> tokenize de Python
    -> traducción de tokens NAME
    -> Python inspeccionable
    -> ast.parse / CPython
```

Ahhh, estos cabrones nomás estaban hablando inglés.

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

También se puede usar sin instalar el comando:

```powershell
py -m piton ejecutar examples\01_hola.piton
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
Ran 67 tests

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
|   |-- runtime.py
|   |-- translator.py
|   |-- import_hook.py
|   `-- repl.py
|-- examples/
|   |-- equivalentes/
|   |-- 01_hola.piton ... 09_soft_keywords.piton
|   `-- programa_completo.piton
|-- tests/
|   |-- fixtures/
|   |-- evidence.py
|   |-- test_cli_and_corpus.py
|   `-- test_translator.py
|-- pyproject.toml
|-- ROADMAP.md
`-- README.md
```

## Licencia

MIT. Ver `LICENSE`.
