# SPEC.md — Especificación de sintaxis y traducción de Pitón

**Versión:** 1.0.0
**Oracle:** CPython 3.12.4
**Fecha:** 2026-09-07
**Estado:** CONGELADO para v1.x

---

## 1. Visión general

Pitón es un **frontend auditable** para Python. Traduce código fuente `.piton` (palabras reservadas y builtins en español de México) a Python ordinario mediante un pipeline determinístico:

```
.piton → tokenize (stdlib) → clasificación contextual → untokenize → ast.parse → CPython
```

**No inventa semántica.** La semántica de referencia es la de CPython 3.12.4. El Python generado es siempre inspeccionable.

---

## 2. Codificación y fuente

- Archivos `.piton`: UTF-8 (con o sin BOM). `tokenize.open()` maneja coding cookie PEP 263.
- Fin de línea: `\n` (LF) o `\r\n` (CRLF) — se conservan 1:1 en la traducción.
- Shebang: permitido en línea 1, se copia tal cual.

---

## 3. Léxico — tokens NAME y clasificación

El traductor opera **solo sobre tokens `NAME`** completos producidos por `tokenize.generate_tokens()`. Nunca usa `str.replace()` ni regex sobre texto crudo.

### 3.1 Categorías de traducción

| Categoría | Qué traduce | Cuándo |
|-----------|-------------|--------|
| `keyword` | Hard keywords (siempre reservadas) | Token NAME exacto en posición de statement o expresión |
| `soft-keyword` | `segun`, `caso`, `tipo` | Solo en contexto sintáctico válido (ver §3.3) |
| `builtin-call` | Aliases de builtins en llamada directa | `imprimir(...)` → `print(...)` |
| `builtin-load` | Aliases de builtins como carga de nombre | `alias = imprimir` → `alias = print` |
| `conserva` | Todo lo demás | Atributos, strings, comentarios, definiciones, parámetros, etc. |

### 3.2 Hard keywords (siempre se traducen)

```
si → if          sino_si → elif     sino → else
para → for       mientras → while   en → in
funcion → def    devolver → return  producir → yield
clase → class    intentar → try     excepto → except
finalmente → finally  lanzar → raise  con → with
como → as        importar → import   desde → from
Verdadero → True Falso → False       Nada → None
y → and          o → or             no → not
romper → break   continuar → continue  pasar → pass
asincrono → async esperar → await   afirmar → assert
borrar → del     no_local → nonlocal es → is
global → global  lambda → lambda
```

**Regla:** Se traducen **siempre** que aparezcan como token `NAME` completo y no sean:
- Parte de un atributo (`objeto.si` → se conserva)
- Parte de un string o comentario
- Parte de un identificador compuesto (`sistema`, `parasol`)

### 3.3 Soft keywords (contexto-dependent)

| Pitón | Python | Contexto válido |
|-------|--------|-----------------|
| `segun` | `match` | Inicio de statement, seguido de expresión y `:` (no `=` ni `(`) |
| `caso` | `case` | Dentro de bloque `match` (depth > 0), seguido de patrón y `:` (no `=`) |
| `tipo` | `type` | Inicio de statement, seguido de `NAME` (type alias) |

**Fuera de contexto:** se conservan como identificadores normales.

**Modo estricto (`--estricto` / `estricto=True`):** Rechaza `segun`/`caso`/`tipo` usados como identificadores con `PitonStrictError`.

### 3.4 Aliases de builtins (builtins conscientes de scope)

| Pitón | Python | Categoría |
|-------|--------|-----------|
| `imprimir` | `print` | `builtin-call` / `builtin-load` |
| `entrada` | `input` | `builtin-call` / `builtin-load` |
| `rango` | `range` | `builtin-call` / `builtin-load` |
| `longitud` | `len` | `builtin-call` / `builtin-load` |
| `enumerar` | `enumerate` | `builtin-call` / `builtin-load` |
| `lista` | `list` | `builtin-call` / `builtin-load` |
| `diccionario` | `dict` | `builtin-call` / `builtin-load` |
| `conjunto` | `set` | `builtin-call` / `builtin-load` |
| `tupla` | `tuple` | `builtin-call` / `builtin-load` |
| `entero` | `int` | `builtin-call` / `builtin-load` |
| `decimal` | `float` | `builtin-call` / `builtin-load` |
| `texto` | `str` | `builtin-call` / `builtin-load` |
| `booleano` | `bool` | `builtin-call` / `builtin-load` |
| `abrir` | `open` | `builtin-call` / `builtin-load` |
| `ordenar` | `sorted` | `builtin-call` / `builtin-load` |

**Regla de traducción:**
1. **NO traducir** si es atributo: `objeto.imprimir` → `objeto.imprimir`
2. **NO traducir** si es definición: `funcion imprimir():` → `def imprimir():`
3. **NO traducir** si está en import: `importar imprimir` → `import imprimir`
4. **NO traducir** si hay **shadowing local** (asignación en el mismo scope): `imprimir = 42` → posteriores `imprimir` se conservan
5. **SÍ traducir** en llamada directa: `imprimir("hola")` → `print("hola")`
6. **SÍ traducir** como load: `alias = imprimir` → `alias = print`

**Detección de shadowing:** Pre-scan del archivo busca nombres en lado izquierdo de `=` y declaraciones `global`/`no_local`. Es a nivel de módulo (no anidado en v1.0).

### 3.5 F-strings (v0.5+)

- El **texto** de la f-string NUNCA se toca.
- Las **expresiones** entre `{...}` SÍ se traducen (keywords, builtins).
- Llaves escapadas `{{` y `}}` se conservan.
- Conversiones `!r`, `!s`, `!a` y format specs se conservan.

Ejemplo:
```
f"hola {imprimir}"        → f"hola {print}"
f"total: {suma(x)}"       → f"total: {sum(x)}"
f"llaves {{imprimir}}"    → f"llaves {{imprimir}}"
```

---

## 4. Sintaxis soportada (paridad con CPython 3.12)

### 4.1 Statements
- `si` / `sino_si` / `sino`
- `para` (incluye `else` en bucles)
- `mientras` (incluye `else`)
- `funcion` (con parámetros genéricos `[T]`, defaults, `*args`, `**kwargs`)
- `clase` (con parámetros genéricos, herencia, metaclass)
- `intentar` / `excepto` / `excepto*` / `finalmente`
- `con` / `como`
- `con` asincrono (`asincrono con`)
- `para` asincrono (`asincrono para`)
- `devolver`, `producir`, `lanzar`, `afirmar`, `borrar`
- `global`, `no_local`, `pasar`, `romper`, `continuar`
- `segun` / `caso` (pattern matching completo: literales, captura, `as`, `|`, `_`, class patterns, mapping patterns, sequence patterns)
- `tipo` (type alias statement `tipo Nombre = tipo`)
- `importar` / `desde` ... `importar` (relativos incluidos)

### 4.2 Expresiones
- Operadores: `y`, `o`, `no`, `es`, `en`, comparaciones encadenadas
- Await: `esperar expr`
- Lambdas: `lambda` (palabra conservada)
- Comprehensions: lista, dict, set, gen
- Ternario: `a si condicion sino b` → `a if condicion else b` (NO soportado en v1.0 — requiere parser propio)

### 4.3 No soportado en v1.0 (requiere parser propio Fase 2)
- `match`/`case` como expresión (PEP 634 solo statement)
- `case _:` con guarda `si` (pattern guards)
- Type hints complejos (`Union`, `Optional`, `Callable` — requieren `from typing import ...`)
- `match` statement con `case` guards
- `try`/`except`/`else` (solo `finally`)

---

## 5. Pipeline de traducción

```python
def traducir_fuente(fuente: str, archivo: str, estricto: bool = False) -> str:
    1. tokens = list(tokenize.generate_tokens(io.StringIO(fuente).readline))
    2. asignados = pre_scan_shadowing(tokens)           # O(1) pass
    3. salida, cambios, mapa = clasificar_y_traducir(tokens, asignados, estricto)
    4. traducido = tokenize.untokenize(salida)
    5. ast.parse(traducido, filename=archivo)           # validación obligatoria
    6. return traducido
```

**Invariante:** `ast.parse()` debe pasar. Si falla, se lanza `PitonSyntaxError` con ubicación remapeada al `.piton` original (ver §6).

---

## 6. Source map y remapeo de errores

Cada token transformado registra: `(línea_gen, col_gen) → (línea_orig, col_orig)`.

Al capturar `SyntaxError` de `ast.parse()`:
1. Se toma `error.lineno`, `error.offset`
2. Se busca en `mapa.original` la ubicación original
3. Se relanza `PitonSyntaxError` con `linea`, `columna`, `texto` del fuente `.piton`

**Formato de error:**
```
PITON_SYNTAX_ERROR
archivo: examples/hola.piton
linea: 3
columna: 8

Python rechazó la traducción:
expected ':'
```

---

## 7. CLI

| Comando | Descripción |
|---------|-------------|
| `piton ejecutar archivo.piton [args...]` | Traduce, compila, ejecuta con `sys.argv` preservado |
| `piton traducir archivo.piton [-o out.py]` | Emite Python a stdout o archivo |
| `piton verificar archivo.piton` | Traduce y valida sin ejecutar |
| `piton tokens archivo.piton` | Muestra tokens y categoría de cada cambio |
| `piton ast archivo.piton` | Muestra `ast.dump()` del Python generado |
| `piton repl` | REPL interactivo (multi-linea) |

Flags globales:
- `-x` / `--estricto` — rechaza soft keywords como identificadores

---

## 8. API pública (`import piton`)

```python
from piton import (
    traducir_fuente, traducir_archivo, traducir_fuente_con_mapa,
    ejecutar_archivo, compilar, compilar_piton, ejecutar_repl,
    instalar_hook, desinstalar_hook,
    PitonSyntaxError, PitonStrictError, MapaFuente,
)

# Import hook para importar .piton directamente
instalar_hook()
import mi_modulo  # busca mi_modulo.piton
```

---

## 9. Tests y evidencia

- **Unit tests:** `py -m unittest discover -s tests -v` (67 tests)
- **Evidencia diferencial:** `py tests/evidence.py` (36 gates)
- **Corpus:** `examples/*.piton` ↔ `examples/equivalentes/*.py`
- **Compilación:** `py -m compileall piton -q` (debe pasar sin warnings)

---

## 10. Límites declarados de v1.0

| Límite | Estado | Plan |
|--------|--------|------|
| Parser propio (CST/AST/HIR) | NOT_DEMONSTRATED | Fase 2 |
| Backend x86-64 | NOT_DEMONSTRATED | Fase 5+ |
| GC / runtime propio | NOT_DEMONSTRATED | Fase 6+ |
| Self-hosting | INTENTIONALLY_UNSUPPORTED | Opcional, mucho después |
| Paridad completa CPython | NOT_DEMONSTRATED | Fase 14+ |
| Extensiones C-API | INTENTIONALLY_UNSUPPORTED | Carril separado |

---

## 11. Versionado y congelación

- `SPEC.md` se congela en cada release `1.x`.
- Cambios breaking → `2.0`.
- Cambios non-breaking → `1.x+1`.
- `ORACLE.md` define versión exacta de CPython (hoy 3.12.4).
- Rebaseline del corpus = decisión explícita + `ORACLE.md` actualizado.