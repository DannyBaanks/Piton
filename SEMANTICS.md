# SEMANTICS.md — Contrato observable de equivalencia

**Versión:** 1.0.0
**Oracle:** CPython 3.12.4
**Fecha:** 2026-09-07
**Estado:** CONGELADO para v1.x

---

## 1. Principio rector

Dos programas son **equivalentes observacionalmente** si, dados los mismos inputs, producen los mismos **efectos observables** definidos en este contrato.

**No se requiere:** identidad de bytecode, layout de objetos, direcciones de memoria, `inspect.getsource()`, `id()` de objetos internos, timing exacto, orden de keys en dict (salvo que Python lo garantice), mensajes de error palabra por palabra.

---

## 2. Efectos observables medidos

| Efecto | Cómo se compara | Normalización |
|--------|-----------------|---------------|
| **Exit code** | `==` exacto | — |
| **stdout** | `==` exacto (bytes → str UTF-8) | `rstrip()` final opcional para `\n` trailing |
| **stderr** | `==` exacto | Igual que stdout; tracebacks se comparan por tipo+mensaje, no por rutas absolutas |
| **Excepción no capturada** | Tipo + mensaje + cadena causal (`__cause__`, `__context__`) | Rutas de archivo normalizadas a nombre base |
| **Filesystem** | Diff de directorio sandbox antes/después | Timestamps ignorados; solo contenido y existencia |
| **Imports cargados** | Set de módulos en `sys.modules` al final | Solo nombre, no ruta ni `loader` |
| **Orden observable** | Cuando el lenguaje lo garantiza (dict insertion order 3.7+, set iteration no garantizado) | Según spec Python |

---

## 3. Formato de resultado observable (para corpus diferencial)

```json
{
  "exit_code": 0,
  "stdout": "Hola, mundo\n",
  "stderr": "",
  "exception": null,
  "exception_chain": [],
  "fs_changes": {
    "created": ["output.txt"],
    "modified": [],
    "deleted": []
  },
  "imports_loaded": ["sys", "os", "pathlib"],
  "duration_ms": 12
}
```

**Campo `exception` (si hay):**
```json
{
  "type": "ValueError",
  "message": "invalid literal for int()",
  "cause": null,
  "context": null,
  "traceback_frames": [
    {"file": "hola.piton", "line": 5, "func": "main", "code": "entero('x')"}
  ]
}
```

---

## 4. Reglas de comparación diferencial

### 4.1 Ejecución de par
Para cada fixture `X.piton` con equivalente `X.py`:

```python
piton_result = run_piton("X.piton", args, stdin, env)
python_result = run_python("equivalentes/X.py", args, stdin, env)

assert piton_result == python_result  # según formato §3
```

### 4.2 Normalización de stderr (tracebacks)

- Rutas absolutas → nombre de archivo base (`C:\path\hola.piton` → `hola.piton`)
- Números de línea → ya remapeados por source map (deben coincidir)
- Columnas → pueden diferir (traducción cambia longitud de tokens)
- Mensajes de `SyntaxError` → pueden diferir en redacción; se compara **tipo + ubicación + texto de la línea**

### 4.3 Excepciones encadenadas

Se compara recursivamente `__cause__` y `__context__` hasta `None`.

### 4.4 Filesystem sandbox

Cada test diferencial corre en `tempfile.TemporaryDirectory()`. Se captura estado antes/después.

---

## 5. Queda FUERA del contrato (NOT_DEMONSTRATED)

| Aspecto | Por qué |
|---------|---------|
| Bytecode (`dis`, `code.co_code`) | Detalle de implementación CPython |
| `id(obj)` / `sys.getrefcount()` | Identidad interna, no observable semántica |
| Layout de `PyObject` / `PyDictObject` | ABI interna |
| `inspect.getsource()` / `getsourcelines()` | Puede fallar en código generado |
| Timing / performance | No es corrección |
| Orden de iteración de `set` / `dict` (pre-3.7) | No garantizado por Python |
| Mensajes exactos de `SyntaxError`/`TypeError` | Pueden variar entre versiones CPython |
| `__annotations__` evaluation (PEP 563/649) | Comportamiento versión-dependiente |
| `sys.settrace`, `sys.setprofile` | Hooks de depuración, no semántica de programa |

---

## 5. Casos边界 (edge cases documentados)

| Caso | Comportamiento Pitón | Notas |
|------|---------------------|-------|
| `sys.argv[0]` | Ruta del archivo `.piton` | No ruta del Python generado |
| `__file__` en módulo importado | Ruta `.piton` | Import hook lo establece |
| `__name__ == "__main__"` | Funciona igual | En `ejecutar_archivo` y import hook |
| Encoding de fuente | `tokenize.open()` respeta coding cookie | UTF-8 por defecto |
| `KeyboardInterrupt` / `SystemExit` | Propagan igual | No interceptados |
| Recursion limit | Heredado de CPython | No modificado |
| GC / finalizers (`__del__`) | Heredado de CPython | No modificado |
| `warnings` | Heredado de CPython | No modificado |

---

## 6. Gates de evidencia (de `tests/evidence.py`)

| Gate | Qué prueba | Estado v1.0 |
|------|------------|-------------|
| `PITON_SOURCE_TRANSLATION` | Traducción básica keywords + builtins | PASS |
| `STRINGS_UNTOUCHED` | Strings no mutados | PASS |
| `COMMENTS_UNTOUCHED` | Comentarios no mutados | PASS |
| `IDENTIFIER_SUBSTRINGS_UNTOUCHED` | Substrings en identificadores intactos | PASS |
| `AST_VALIDATION` | `ast.parse()` pasa | PASS |
| `CPYTHON_EXECUTION` | Ejecución end-to-end | PASS |
| `CORPUS_FRONTEND_EQUIVALENCE` | Programa completo vs Python | PASS |
| `PYTHON_312_STATEMENTS` | Statements modernos (async, match, type alias) | PASS |
| `ASYNC_AWAIT` | `asincrono`/`esperar` | PASS |
| `PATTERN_MATCHING` | `segun`/`caso` | PASS |
| `ASYNC_FOR_WITH` | `asincrono para/con` | PASS |
| `TYPE_ALIASES_AND_PARAMETERS` | `tipo`, genéricos `[T]` | PASS |
| `EXCEPTION_GROUPS` | `excepto*` | PASS |
| `DIFFERENTIAL_EXECUTION` | Equivalencia stdout/stderr/status | PASS |
| `CONTEXTUAL_KEYWORDS` | Soft keywords en contexto | PASS |
| `SOFT_KEYWORDS_AS_IDENTIFIERS` | Soft keywords fuera de contexto | PASS |
| `STRICT_KEYWORDS_OK` | Modo estricto permite keywords en contexto | PASS |
| `STRICT_IDENTIFIER_REJECTED` | Modo estricto rechaza identificadores | PASS |
| `SOFT_KEYWORD_CATEGORY` | Categoría `soft-keyword` en tokens | PASS |
| `SOFT_KEYWORD_DIFFERENTIAL` | Ejecución diferencial soft keywords | PASS |
| `BUILTIN_LOADS` | Builtins como loads (no solo calls) | PASS |
| `BUILTIN_SHADOWING` | Shadowing local detectado | PASS |
| `FSTRING_EXPRESSIONS_TRANSLATED` | Expresiones en f-strings traducidas | PASS |
| `SYNTAX_ERROR_REMAP` | Errores remapeados a .piton | PASS |
| `PITON_MODULE_IMPORT` | Import hook funcional | PASS |
| `MULTILINE_REPL` | REPL multi-linea | PASS |
| `EXPLICIT_PITON_COMPILE_API` | `piton.compilar()` | PASS |
| `PYTHON_GRAMMAR_COVERAGE` | Cobertura gramática declarada | PASS |
| `KEYWORD_TRANSLATION` | Todas las keywords traducen | PASS |
| `SOFT_KEYWORDS` | Soft keywords funcionan | PASS |
| `ATTRIBUTE_NAMES_UNTOUCHED` | Atributos intactos | PASS |
| `STRINGS_UNTOUCHED` | Strings intactos | PASS |
| `COMMENTS_UNTOUCHED` | Comentarios intactos | PASS |
| `IDENTIFIER_INTEGRITY` | Identificadores no mutilados | PASS |
| `IMPORT_SYSTEM` | Import .piton funciona | PASS |
| `ARGV_STDIN_STDOUT_STDERR` | I/O preservado | PASS |
| `WINDOWS_LINUX_MACOS` | Probado en Windows (Linux/macOS pendiente) | PASS* |

\* `WINDOWS_LINUX_MACOS` = PASS en Windows; Linux/macOS = NOT_DEMONSTRATED (sin CI configurado).

---

## 7. Criterio de aceptación para releases futuros

Para cualquier versión `1.x` o `2.x`:

1. **Todos los gates existentes deben seguir en PASS** (no regresiones).
2. **Nuevas features → nuevos gates** en `evidence.py`.
3. **Corpus diferencial versionado** por feature (carpeta `tests/corpus/` en Fase 2+).
4. **Rebaseline del oracle** = actualizar `ORACLE.md` + re-ejecutar todo el corpus.
5. **No gate pasa "por inspección visual"** — solo evidencia automatizada.

---

## 8. Evolución del contrato

| Versión | Cambios al contrato |
|---------|---------------------|
| 1.0 | Baseline: CPython 3.12.4, efectos observables §2 |
| 1.5 | Agregar: HIR/MIR determinismo, MIR trace stability |
| 2.0 | Agregar: ABI x86-64, value model, calling convention |
| 2.5 | Agregar: `PYTHON_RUNTIME_DEPENDENCY = 0`, `PE_IMPORTS_PYTHON = 0` |
| 3.0 | Agregar: runtime objects (str, list, dict, int, exceptions) |
| 4.0 | Agregar: clases nativas, MRO, descriptors, imports nativos |
| 5.0 | Agregar: stdlib nativa declarada, FFI C, `NO_CPYTHON_RUNTIME_LINK = 0` |
| 6.0+ | Cierre progresivo de huecos `PARTIAL` → `PASS` |

---

## 9. Cómo agregar un test al corpus diferencial

1. Crear `examples/nuevo_feature.piton`
2. Crear `examples/equivalentes/nuevo_feature.py` (Python 3.12 válido)
3. Agregar entrada a `CliAndCorpusTests.SALIDAS` en `test_cli_and_corpus.py`
4. Agregar gate específico en `tests/evidence.py` si es feature nueva
5. Correr `py -m unittest discover -s tests -v` y `py tests/evidence.py`
6. Ambos deben pasar sin cambios a código existente.

---

## 10. Referencias

- [PEP 3101] Advanced String Formatting (f-strings)
- [PEP 634] Structural Pattern Matching
- [PEP 695] Type Parameter Syntax (genéricos `[T]`)
- [PEP 654] Exception Groups (`except*`)
- [Python 3.12 Language Reference] Oracle oficial