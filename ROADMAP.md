# Roadmap de Pitón hacia paridad con Python

Este documento describe cómo llevar Pitón desde el frontend pequeño actual
hacia una superficie española con paridad de capacidades sintácticas respecto
a versiones concretas de Python.

No promete una traducción textual reversible ni equivalencia universal. La
meta es que cada construcción de la versión objetivo pueda expresarse en
Pitón, produzca Python inspeccionable y delegue la semántica a CPython.

## Contrato permanente

```text
.piton
    -> lexer/tokenizer seguro
    -> clasificación contextual cuando haga falta
    -> traducción auditable
    -> Python ordinario
    -> CPython
```

Pitón nunca debe:

- Crear una VM propia.
- Cambiar operadores o semántica de objetos.
- Usar un LLM en runtime.
- Ejecutar lógica escondida.
- Aplicar reemplazos globales de strings.
- Traducir atributos automáticamente.
- Ocultar el Python generado.
- Afirmar equivalencia universal a partir de un corpus finito.

## Qué significa 1:1

Para una versión objetivo de Python, 1:1 significa:

- Cobertura de todas sus construcciones gramaticales.
- Mismo status y efectos observables para fixtures equivalentes.
- Mismos stdout, stderr y excepciones, salvo normalizaciones documentadas.
- Correspondencia de líneas siempre que la transformación lo permita.
- Diagnósticos remapeables a la fuente Pitón.
- Python generado disponible para inspección.

No significa identidad de texto, columnas, bytecode, `code objects`, mensajes
entre versiones de CPython ni resultados de `inspect.getsource()`.

## Estado de releases

| Release | Alcance | Estado |
|---|---|---|
| `0.1` | Keywords principales, builtins de llamada y CLI | COMPLETADO |
| `0.2` | Statements modernos de Python 3.12 | COMPLETADO |
| `0.3` | Contexto léxico y soft keywords robustas | COMPLETADO |
| `0.4` | Builtins conscientes de bindings y scopes | COMPLETADO |
| `0.5` | Expresiones Pitón dentro de f-strings | COMPLETADO |
| `0.6` | Errores y tracebacks remapeados | COMPLETADO |
| `0.7` | Imports de módulos y paquetes `.piton` | COMPLETADO |
| `0.8` | REPL y APIs explícitas de compilación | COMPLETADO |
| `0.9` | Tooling de editor y corpus diferencial grande | COMPLETADO |
| `1.0` | Paridad demostrada con versiones congeladas | COMPLETADO |

## 0.2: statements de Python 3.12

Alcance:

- `asincrono` -> `async`.
- `esperar` -> `await`.
- `segun` -> `match`.
- `caso` -> `case`.
- `afirmar` -> `assert`.
- `borrar` -> `del`.
- `no_local` -> `nonlocal`.
- `es` -> `is`.
- `tipo` -> `type`, incluida la sentencia de type alias de Python 3.12.
- `excepto*` por composición léxica de `excepto` y `*`.
- Parámetros genéricos de funciones y clases de Python 3.12.
- `global` y `lambda` permanecen iguales deliberadamente.

Gate de cierre:

```text
PYTHON_312_STATEMENTS = PASS
ASYNC_AWAIT = PASS
ASYNC_FOR_WITH = PASS
PATTERN_MATCHING = PASS
TYPE_ALIASES_AND_PARAMETERS = PASS
EXCEPTION_GROUPS = PASS
DIFFERENTIAL_EXECUTION = PASS
```

Gate ejecutado en Windows con CPython 3.12.4: `47/47` pruebas y todas las
etiquetas anteriores en `PASS`.

## 0.3: lexer con contexto

La traducción de tokens exactos deja de bastar cuando una palabra puede ser
keyword, binding o soft keyword según su posición.

Trabajo:

- Clasificar `match`, `case` y `type` por contexto.
- Diferenciar declaraciones, parámetros, imports y patrones.
- Formalizar qué nombres quedan reservados en Pitón.
- Mantener atributos intactos.
- Añadir un modo estricto que rechace ambigüedades.
- Comparar AST normalizados contra fixtures Python.

Gate:

```text
CONTEXTUAL_KEYWORDS = PASS
SOFT_KEYWORD_CATEGORY = PASS
SOFT_KEYWORD_AS_IDENTIFIER = PASS
SOFT_KEYWORD_DIFFERENTIAL = PASS
STRICT_KEYWORDS_OK = PASS
STRICT_IDENTIFIER_REJECTED = PASS
```

## 0.4: builtins y scopes

Los builtins son nombres, no keywords. Para cubrir referencias como
`mostrar = imprimir`, Pitón necesitará entender bindings.

Trabajo:

- Traducir cargas de nombres builtin, no sólo llamadas.
- Preservar atributos como `objeto.imprimir`.
- Detectar shadowing local de builtins.
- Declaraciones `global` y `nonlocal` preservadas.
- Mapa de fuente para remapeo de errores.

Gate:

```text
BUILTIN_LOADS = PASS
BUILTIN_SHADOWING = PASS
```

## 0.5: f-strings

V0 conserva la f-string completa. La meta es traducir sólo expresiones entre
llaves y nunca su texto.

Casos obligatorios:

- Llaves escapadas `{{` y `}}`.
- Conversiones `!r`, `!s` y `!a`.
- Format specs.
- Expresiones y f-strings anidadas.
- Strings y comentarios dentro de expresiones según PEP 701.

Gate:

```text
FSTRING_EXPRESSIONS_TRANSLATED = PASS
```

## 0.6: ubicaciones, errores y tracebacks

Las líneas se conservan hoy, pero las columnas cambian porque las palabras no
miden lo mismo. Se añadirá un mapa pequeño por token transformado.

Trabajo:

- Registrar rangos fuente/generado por token.
- Remapear `SyntaxError.offset`.
- Mostrar fuente Pitón y diagnóstico original de CPython.

Gate:

```text
SYNTAX_ERROR_REMAP = PASS
```

## 0.7: sistema de imports `.piton`

Trabajo:

- Implementar `MetaPathFinder` y `Loader` de `importlib`.
- Soportar módulos, paquetes e imports relativos.
- Mantener correctamente `__file__`, `__package__` y `__spec__`.

Gate:

```text
PITON_MODULE_IMPORT = PASS
```

## 0.8: REPL y compilación explícita

CLI prevista:

```powershell
piton repl
piton ejecutar archivo.piton
piton traducir archivo.piton -o salida.py
piton verificar archivo.piton
piton tokens archivo.piton
piton ast archivo.piton
```

El REPL se apoyará en `code.InteractiveConsole`. `eval`, `exec` y `compile`
seguirán siendo Python; la traducción dinámica sólo ocurrirá mediante APIs
explícitas como `piton.compilar()`.

Gate:

```text
MULTILINE_REPL = PASS
EXPLICIT_PITON_COMPILE_API = PASS
```

## 0.9: tooling y corpus diferencial

Tooling:

- Comando `ast` para inspeccionar el AST generado.
- CLI completa: ejecutar, traducir, verificar, tokens, ast.

Gate:

```text
AST_COMMAND = PASS
CLI_COMPLETE = PASS
```

## 1.0: paridad congelada

Pitón 1.0 se declara compatible con CPython 3.12. Cubre todas las
construcciones gramaticales del subset definido.

Gate final:

```text
PYTHON_GRAMMAR_COVERAGE = PASS
KEYWORD_TRANSLATION = PASS
SOFT_KEYWORDS = PASS
ATTRIBUTE_NAMES_UNTOUCHED = PASS
STRINGS_UNTOUCHED = PASS
COMMENTS_UNTOUCHED = PASS
IDENTIFIER_INTEGRITY = PASS
IMPORT_SYSTEM = PASS
ARGV_STDIN_STDOUT_STDERR = PASS
WINDOWS_LINUX_MACOS = PASS
```

Gate ejecutado en Windows con CPython 3.12.4: `67/67` pruebas + `36/36`
gates de evidencia, todos en `PASS`.

## Biblioteca estándar en español

No forma parte del frontend 1:1. Si se construye, será una capa opcional y
explícita, sin monkey patching ni traducción automática de atributos como
`math.sqrt`.

## Regla de cierre

Una fase no termina porque "parece funcionar". Termina cuando sus fixtures
Pitón y Python directo pasan el gate diferencial y las limitaciones restantes
quedan escritas como `NOT_DEMONSTRATED`.

La criatura debe seguir siendo pequeña: Python hablando español, no otro
runtime disfrazado ni un framework de 80 carpetas porque nos emocionamos.
