# AGENTS 2.0 — PITON

**Lee este documento UNA vez, entero, antes de tocar nada. Después no lo releas:
usa el árbol semántico de §7, lo único que necesitas en contexto.**

No describe el código. Describe **cómo se mide, qué se puede afirmar y qué está
roto hoy**. Nació de una sesión en la que ocho agentes escribieron código
correcto encima de un backend que devolvía punteros, y el corpus decía verde.

---

## 1. Qué es paridad

Para cada ejemplo, PITON y CPython 3.12:

- **(a)** lanzan excepción ambos, o
- **(b)** imprimen **byte a byte** lo mismo y el **exit code** coincide.

No hay (c). No hay tolerancia numérica, ni normalización de espacios, ni
"equivalente salvo el orden", ni "lo importante es que no crashee".

### Lo que NO es paridad (falso verde)

| lo que se hace | por qué es mentira |
|---|---|
| `return None` / `0` / `""` / `[]` para no lanzar | el ejemplo pasa; la función no existe |
| escribir la salida esperada a mano | el oráculo deja de ser oráculo |
| `.strip()` / reordenar / colapsar espacios | oculta la divergencia real |
| ignorar el exit code | una excepción silenciosa cuenta como éxito |
| descartar ejemplos que no compilan | el denominador se encoge solo |
| aceptar el output de PITON como esperado | circular, no mide nada |

Si no se puede igualar de verdad: **divergente con el diff exacto**, o
**`NOT_DEMONSTRATED`**. Un rojo honesto cuesta una tarde; un verde falso cuesta
la credibilidad del proyecto.

---

## 2. El contrato de evidencia

Toda afirmación de éxito necesita:

1. el comando exacto ejecutado,
2. la salida cruda (no resumida a mano),
3. el exit code,
4. sha256 del artefacto si es un fichero,
5. el control que descarta la explicación alternativa.

Sin (1)-(3) no es un hecho: es una impresión. Sin (5) no sabes si el bug es
tuyo, del frente de al lado, o preexistente.

**Regla del worktree.** El árbol es compartido y varios agentes tienen WIP sin
commitear. Un `pytest` ahí **no es comparable con nada**:

```bash
git worktree add /tmp/piton-head <sha-limpio>
```

Así se distinguió el bug real del codegen (`5494486`) de lo que parecía WIP.
Sin worktree habríamos culpado al agente equivocado. Medido: árbol sucio
324 passed / 1134 failed; worktree limpio 45 failed / 1376 passed. El número
del árbol sucio mide el caos, no PITON.

---

## 3. Trampas del runtime — evidencia, no folklore

Verificadas contra **HEAD limpio**. No son "cosas que pasan a veces": son el
comportamiento actual del backend Linux.

### T1 — `str()`, `''.join()` y el slice calculado devuelven un PUNTERO al volver de una función

**ARREGLADO en `d667712` (parche U2, sha256 `1c0b9538…`):** la inferencia de
retornos conserva `str()`, `str.join()` y slices. `capwords('hola mundo')`
→ `Hola Mundo`. Quedan los casos como tests de no-regresión (ver
`tests/test_adv_objetos.py`).

El más grave del repo. Dentro de una función:

```python
def f(v):  return str(v)              # -> 8423456  (entero-puntero, no "5")
def g(t):  return ''.join(list(t))    # -> 8423664
def h(t):  return t[1:3]              # -> 8423744
```

Pero **sí** funcionan:

```python
def i(t): return t[1]                 # -> 'b'
def j(t): return t[0] + t[1]          # -> 'ab'
def k(t):                             # -> 'abc'  (concat en bucle)
    r = ''
    i = 0
    while i < len(t):
        r = r + t[i]
        i = i + 1
    return r
```

Consecuencia directa: `string.capwords` (`return ' '.join(partes)`) **está roto
hoy**, y cualquier `textwrap.fill`, `pprint` o `reprlib.repr` que retorne
`join`/`str`/slice fallará aunque su lógica sea perfecta.

**Norma:** no se implementa librería que retorne cadenas calculadas hasta
arreglar esto. Y ningún arreglo se valida sólo con el corpus primario (T7).

### T1-bis — El mecanismo real: DOS ABI incompatibles

Verificado leyendo el runtime, no especulando.

**Runtime embebido en `piton/linux_x86.py` (`_RICH_FREESTANDING_C`): SIN etiquetas.**
- entero = `long` crudo; string = `char*` crudo; `None` = `&piton_none_singleton`;
- colecciones = puntero a `long*` con `ptr[1]` = `PK_LIST/PK_DICT/...` y `ptr[2]` = longitud;
- `piton_len_of_value(bits)` y `piton_maybe_ptr(bits)` trabajan así.

**`piton/native_runtime.c` (añadido por F1): ETIQUETADO.**
- `pv_encode(tag, payload)`, `TAG_SHIFT 61`, `PitonHeader{sub_tag, refcount}`, `SUB_TAG_STR=5`;
- helpers: `piton_payload`, `piton_is_str`, `piton_object_class_name`.

**Consecuencia 1 — no se mezclan.** Un bloque escrito contra el ABI etiquetado
pegado en el runtime embebido no enlaza: pide `piton_is_str`, `piton_payload`,
`piton_object_class_name`, que allí no existen. Le pasó a F4 con `_TIME_LOCALE_C`
(+260 líneas) y el build murió con "referencia a X sin definir".

**Consecuencia 2 — el bug de los retornos no es "devolver un puntero".** Ya
existe una recuperación: `piton_print_int_safe` (`PRINT_INT_V1`) detecta
`piton_maybe_ptr(bits)` y, si es un string imprimible, lo imprime como string.
Eso es lo que hace que a nivel de módulo `str(5)` se vea bien. El fallo aparece
cuando el valor cruza un **hueco de inferencia de tipos**: el retorno se tipa
`int`, y el consumidor de ese retorno no aplica la recuperación. Arreglar esto
es arreglar la propagación de tipos/consumo, no "boxear el return".

**Regla:** antes de escribir C en `linux_x86.py`, mira cómo lo hace una función
que ya funciona en ESE runtime (`piton_builtin_ord` castea a `char*` sin tag;
`piton_len_of_value` usa `ptr[1]`). Copia esa convención, no la de
`native_runtime.c`.

### T2 — `*args` vacío llega como `(None, None)`

```python
def f(*a):
    return len(a)
f()          # -> 2, no 0
```

Rompe `Template.substitute()`, `len(*args)` y toda API que distinga "sin
argumentos" de "argumentos None". Se arregla en el codegen de llamadas.

### T3 — `''.join(lista)` con un int dentro

Falla con `TypeError: can only concatenate str (not "int") to str`, no con el
mensaje de CPython (`sequence item 0: expected str instance, int found`). Si
comparas sólo "lanzó", pasa; si comparas el mensaje, no.

### T4 — `isinstance()` dentro de una función no compila

Emite `PITON_TAG_INT` indefinido. No hay despacho de tipo en runtime: `tipo()`
es estático, inferido por el analizador. Cualquier algoritmo que distinga `str`
de `list` recursivamente (pprint) es inimplementable hasta arreglar esto.

### T5 — El runtime Linux tiene DOS copias y sólo una se compila

`piton/native_runtime.c` define símbolos que el runtime **embebido como cadena C
en `piton/linux_x86.py`** no tiene. El linker falla con "referencia a X sin
definir" aunque `grep X` encuentre la definición en el repo.

Casos confirmados:

| símbolo ausente | síntoma | quien lo dispara |
|---|---|---|
| `piton_is_str` | `time.strftime` no enlaza | F4 / `time.piton` |
| `deque__rotate` | `collections.deque.rotate` no enlaza | C2 corpus secundario |

**Norma:** cuando un símbolo "existe" y el linker dice que no, busca en qué
copia del runtime se compila de verdad. `native_runtime.c` **no** es la fuente
del build Linux: la fuente es la cadena de `linux_x86.py`.

### T6 — `import time` no prueba nada

`import time` compila y corre (rc 0). El fallo aparece al **llamar** a
`strftime`. Un ejemplo que sólo importa mide el import, no la función. Todo
ejemplo debe **imprimir el valor de retorno**.

### T7 — El corpus primario tiene un punto ciego: mide sentencias de módulo

`corpus_v4` ejecuta código a nivel de módulo, donde `str`, `join` y los slices
funcionan (T1 sólo rompe **dentro** de funciones). Por eso un backend con el
codegen roto puede "aprobar" el corpus primario.

**Norma:** ningún arreglo de librería se valida sólo con el corpus primario.
Hace falta un ejemplo que llame a la función y compare su retorno.

### T8 — Los sets no son comparables byte a byte

CPython sin `PYTHONHASHSEED` fijo baraja `print(set)`. Tres corridas, tres
órdenes. No es bug de PITON: es limitación del arnés. Se documenta, no se toca
el código para "arreglarlo".

### T14 — Tracebacks con path absoluto no son comparables byte a byte

CPython imprime `File "/home/.../45_...py", line 3, in <module>` con la ruta
absoluta del script. PITON no conoce esa ruta en runtime, así que los casos de
excepción no capturada que CPython reporta con traceback (objetos 45 y 47)
no pueden igualarse byte a byte: son **limitación del arnés**, no bug de
PITON. Se documentan, no se tocan.

### T9 — Ficheros de otros frentes rompen la medición global

`time.piton`, `datetime.piton`, `locale.piton` y `linux_x86.py` tienen WIP de
varios agentes a la vez. Un BUILD_FAIL de todo `stdlib2` puede venir de un
símbolo que otro frente está moviendo, no de tu módulo. Worktree (T2 de §2).

### T10 — Trampas del dialecto (parseo y sintaxis), todas con sonda

| trampa | consecuencia | alternativa que SÍ funciona |
|---|---|---|
| `tipo` es **soft keyword** (`segun`, `caso`, `tipo`) | `tipo = x` no parsea | otro nombre (`etiqueta`) |
| `sino si` no existe | `Se esperaba COLON` | `sino_si` (guion bajo) |
| `or`/`and` inglés como operador binario | `binary operator not supported: or` | `o` / `y`, o cadena de `si` |
| `in` sobre valor dinámico (slot) | `binary 'in' … no soportado` | `intentar/excepto KeyError` (dicts); comparaciones (chars) |
| slice con cotas variables | `slice of a dynamically typed str is not supported` | construir con `+` en bucle |
| aritmética en condición/subíndice sin paréntesis | `Se esperaba COLON` | variable precalculada (`sig = i + 1`) |
| comentarios `#` dentro de ciertos bloques | `Expresión inesperada: Token(COMMENT…)` | evitar comentarios dentro de clases |

### T11 — Trampas del runtime al escribir stdlib (módulo bundled)

Verificadas con sondas el 2026-11-13 (rama `rescate/wip-2026-11-13`):

- `self.attr[i]` devuelve un **int**. El subíndice de string solo funciona
  cuando el string llega como **parámetro de función**
  (`def f(t): return t[i]` sí; `t = self.s; t[i]` no; `t = str(self.s); t[i]` no).
- `*args`/`**kwargs` en **métodos de módulos bundled** no reciben los
  argumentos: `len` fijo 2, valores como punteros. En código de usuario sí
  funcionan. Consecuencia: `Template.substitute` no puede recibir ni el
  mapping ni los kwargs desde fuera del módulo.
- Las referencias a funciones **entre módulos** llegan como puntero
  (`from string import _f` → `_f` es un int).
- Los métodos que **retornan listas** se tipan mal (`object is not
  subscriptable`); solo `str`/`join`/slice están cubiertos por el parche U2.
- La ruta `substitute({'a':1})` con mapping positional hace **segfault**
  (rc=-11).
- **Dict derivado de atributo**: `d = self._d; for k in d` → KeyError vacío;
  `for k in self._d` → MemoryError; `d[k]` (subíndice) sí funciona. Bloquea
  `Counter`/`defaultdict` como clases (stubs honestos en collections.piton,
  commit `eb76097`). La misma lógica funciona en código de usuario.

Por eso `string.Template` sigue en stub honesto con el motivo escrito en el
fichero: bloqueado por el codegen, no por la librería.

### T13 — Reglas probadas para escribir stdlib en módulos bundled (2026-11-13)

Todas verificadas con sondas (textwrap/reprlib fueron los casos reales):

- **No pasar strings entre funciones del módulo**: llegan como `PitonSlot`,
  no como `char*` → `len()` falla o segfault. Solución: todo inline en una
  función, o los strings viajan como **parámetro de la función que los usa**.
- **`str + loop_var` falla** (`can only concatenate str (not "int")`): el loop
  var es un slot. `len(loop_var)` SÍ funciona. Evitar listas de strings como
  entrada de bucles; usar un string local con separador y recorrer por índice.
- **Subíndice de lista** (`lineas[i]`) devuelve un objeto no subscriptable:
  usar `sep.join(lista)` para unir.
- **Tipo de retorno de funciones bundled**: hace falta seed en
  `function_return_types` (`math_*`, `textwrap__wrap: "list"`, …); sin seed el
  caller tipa el resultado como `int` y `print` saca el puntero.
- **Contador de temps `%mbox`** era `% 4` → redeclaración C con 5+ usos en una
  función; ahora es monótono (commit `347841f`).
- **Aritmética dentro de condiciones** (`mientras j + ancho < k`) rompe:
  precalcular en variables.

### T12 — Las excepciones no son objetos (y muchos errores no se capturan)

Verificado con sondas y con el C emitido (2026-11-13):
- `except X as exc:` SÍ entra al handler, pero `catch_bind` (busca
  `op == "catch_bind"` en `piton/linux_x86.py`) emite
  `exc = piton_exc_value ? ... : piton_exc_message` → `exc` queda ligado al
  **mensaje (str)**, no a un objeto. Falla `type(exc).__name__` y `exc.args`.
  Helper propuesto: `piton_exc_current()` que construya un `PitonObject` con
  `class_name=piton_exc_type` y attr `args` (tupla). OJO: cambiar el tipo
  estático del resultado rompió el dispatch en pruebas; el fix correcto es
  emitir el chequeo de excepción que falta (ver abajo), no tocar el tipo.
- **Falta el chequeo `if(piton_exc_flag) goto handler` tras llamadas a método
  usadas como enunciado** (p. ej. `values.pop();`): en el C emitido hay
  `pop(...); goto next;` sin chequeo, así que ni siquiera un `raise_set`
  llega al handler. 126 sitios usan además `piton_write+piton_exit(1)`
  (incapturables de plano). Son la causa de las divergencias 23/30/42/44/48
  del corpus `objetos`.

---

## 4. Cómo se trabaja

### 4.1 Antes de tocar nada

1. ¿Qué fichero es mío? Si el dueño es otro frente, no lo toco: lo reporto.
2. ¿El bug es preexistente o WIP? Worktree limpio.
3. ¿Qué ejemplo concreto estoy cerrando? Sin ejemplo, no existe.

### 4.2 El ciclo

```
medir (oráculo CPython) -> aislar (sonda mínima) -> arreglar
  -> probar el ejemplo Y los vecinos -> commit path-scoped -> RESULT
```

**Sonda mínima**: 5-15 líneas que reproducen el fallo sin la librería de por
medio. Si no puedes escribirla, no has entendido el bug.

### 4.3 Commits

- Un agente, un conjunto disjunto de ficheros.
- Siempre `git commit -m "..." -- <rutas>`. **Nunca** `git commit` a secas:
  varios agentes comparten el índice.
- Sin commit no hay release. Si no cambiaste nada, el RESULT dice
  "no files changed".
- No hay remoto. Nunca `git push`.

### 4.4 Lo que nunca se hace

- Tocar `piton/linux_x86.py` o `piton/native_runtime.c` sin ser su dueño.
- Rodear una trampa del runtime (§3). Se arregla el runtime o se documenta.
- Declarar `PASS` sin ejecutar el ejemplo contra el oráculo.
- Reescribir la evidencia cruda para que cuadre.

---

## 5. El pipeline por niveles

El orden es un **gate**, no una sugerencia.

```
T1  ARQUITECTOS DE CORPUS   (lentos, iterativos; 3 agentes)
     corpus + salidas del oráculo + tests, ANTES de que nadie escriba código
T2  ADVERSARIOS ANTIFAKE    (5-6 agentes)
     atacan el corpus de T1: tipos raros, límites, retornos descartados
T3  IMPLEMENTADORES         (rápidos; 10-20, ramificados 3-5 por corpus de T2)
T0  COORDINADOR             integra, mide, audita, decide
```

Un implementador rápido con corpus malo produce veinte parches que pasan el
ejemplo y rompen el resto. Un corpus con esperados verdaderos produce un rojo
honesto que dice exactamente qué falta.

### Reglas de T1
- Los esperados se capturan de CPython **antes** de mirar PITON.
- Cada ejemplo llama al menos una función de librería e imprime su retorno (T7).
- No se toca `piton/`. Si la función no existe, el ejemplo se queda.

### Reglas de T2
- Buscar el falso verde, no el estilo. No se arregla código.
- Un test de T2 que no puede fallar no es un test.

### Reglas de T3
- Un área acotada y el corpus que la prueba. Un fichero por agente.
- Si el corpus no se puede satisfacer, se dice con el diff delante.

---

## 6. Estado honesto del backend (2026-11-13)

| constructo | estado |
|---|---|
| módulo top-level: `str`, `join`, slices, aritmética | funciona |
| funciones: índice literal/variable, `+` de cadenas | funciona |
| funciones: retornar `str()`, `join`, slice calculado | **roto** (T1) |
| `*args` vacío | **roto** (T2) |
| `isinstance()` en función | **no compila** (T4) |
| `time.strftime` | **no enlaza** (T5) |
| `collections.deque.rotate` | **no enlaza** (T5) |
| sets impresos | no comparable (T8) |
| `timeit` | `NOT_DEMONSTRATED` (sin `exec`/`eval`/`perf_counter`) |
| `zlib` | `NOT_DEMONSTRATED` (sin zlib embebido) |

**Estado medido (2026-11-13, rama `rescate/wip-2026-11-13`):**
`corpus_t1/formato` **50/50 EQUIV** (tras `9a1a417`: float con exponente/
underscores, flag `#` y align `=` en format); `corpus_t1/objetos` **46/56**
(10 divergencias: clúster excepciones T12 + `casefold` unicode + `__str__`
con f-string); ADV formato 22/22 verdes; PARITY formato 15/15 con arnés
anti-mentira verificado.

Mientras T1/T4/T5 sigan abiertos, implementar librería que retorne cadenas es
trabajo tirado. Orden correcto: **codegen primero, librería después**.

---

## 7. Árbol semántico — lo único que se relee

```
PARIDAD      = excepción==excepción  |  bytes idénticos + exit code idéntico
FALSO VERDE  = stub, esperado a mano, normalizar, ignorar exit, encoger corpus
EVIDENCIA    = comando + salida cruda + exit + sha256 + control
WORKTREE     = git worktree add /tmp/x <sha>   (separa preexistente de WIP)

RUNTIME ROTO (HEAD limpio 5494486)
  dentro de función:
    str(v) / ''.join(x) / t[a:b]  -> PUNTERO      [T1]
    t[i] / t[0]+t[1] / concat     -> OK           [T1]
    *args vacío                   -> (None,None)  [T2]
    isinstance()                  -> no compila   [T4]
  time.strftime                   -> no enlaza (piton_is_str)  [T5]
  deque.rotate                    -> no enlaza (deque__rotate) [T5]
  import time                     -> OK, no prueba nada        [T6]
  print(set)                      -> orden no determinista     [T8]
  pytest en árbol sucio           -> número inválido           [T9]

CORPUS
  primario  = sentencias de módulo  -> punto ciego de funciones [T7]
  T1        = corpus + esperados del oráculo (gate)
  T2        = adversarial antifake
  T3        = implementación, 1 fichero por agente

TRABAJO
  1 fichero por agente · commit path-scoped · sin commit no hay release
  no rodear una trampa del runtime: arreglarla o documentarla
  si no se demuestra: NOT_DEMONSTRATED
```
