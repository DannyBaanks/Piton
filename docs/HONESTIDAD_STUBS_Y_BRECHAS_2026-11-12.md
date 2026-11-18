# Stubs silenciosos, medicion honesta y brechas abiertas (2026-11-12)

- rama: `feat/honesty-stubs-2026-11-12` (desde `e126e01`, sin push)
- oracle: CPython 3.12.3, backend medido: linux_x86

## 1. Hallazgo: stubs que mienten

`piton/stdlib_piton/*.piton` tenia funciones cuyo cuerpo era `pasar` o un valor
fijo SIN lanzar error: `bisect.bisect_left` devolvia siempre `0`, `pickle.dumps`
devolvia `b""`, `filecmp.cmp` devolvia `0` (= "no iguales" siempre),
`html.escape` devolvia la entrada intacta, `keyword.iskeyword` devolvia
`Falso`, `timeit.timeit` devolvia `0.0`, `unicodedata.name` devolvia `''`.

Heuristica (script de escaneo, no exhaustivo): 143 funciones candidatas. Revisadas
a mano: 83 reescritas a fallo cerrado (`NotImplementedError`, como ya hacia
`zlib.compress`), 3 implementadas de verdad (`bisect`, `html.escape`,
`keyword.iskeyword/issoftkeyword`), el resto son correctas o estan en la lista
de brechas (abajo).

Los que son correctos y se dejan: `operator.*` (salvo `delitem`), `gettext.gettext`
(identidad sin catalogo), `typing.cast`, `os.path.normcase/splitdrive` (POSIX),
`gc.collect` (documentado: devuelve 0 porque no hay ciclos), `json.loads/load`.

## 2. Efecto en la medicion tier B

Antes de este cambio, varios ejemplos del tutorial "pasaban" por accidente: una
expresion sin `print` produce salida vacia en CPython, y el stub tambien. Al
quitar el stub, esos ejemplos dejan de ser iguales. Es el efecto correcto.

| script | antes | despues | motivo |
|---|---|---|---|
| inputoutput | 3/68 divergentes | 3/68 | sin cambio (39, 41, 61: ya documentados) |
| stdlib | 22/52 | 22/52 | sin cambio |
| stdlib2 | 15/60 | 16/60 | +1: ej. 45 `bisect.insort` con tuplas: antes "igual" por silencio, ahora TypeError honesto |

Metrica del corpus (`parity_corpus_v4.py run --tier b`, por script): 63,6 % EQUIV
antes y despues (7 de 11 scripts). El mapa por ejemplo es el instrumento fino.

## 3. Instrumento corregido: programa que aborta

`tools/tier_b_divergence_map.py` comparaba solo los ejemplos que el oracle imprimia.
Si el programa nativo abortaba a mitad, los ejemplos posteriores no imprimian nada
en ningun lado y salian "iguales". Con esa guarda, `stdlib2` pasaba de 15/60 a
"25/60 divergentes" por el aborto (ver `NO_EJECUTADO`). Ahora un ejemplo que el
lado nativo nunca alcanza cuenta como divergente.

## 4. Bug de backend corregido: TypeError de orden no capturable

Una comparacion de orden con tipos mezclados fijos en compilacion (`(1,'a') < 2`,
`range < int`, `complex < int`) emitia un TypeError terminal (`piton_raise_unhandled`):
`try/except TypeError` NO lo capturaba y el proceso moria con rc=1. CPython lo
captura. Ahora (`_runtime_type_error` con contexto de funcion) se enruta como cualquier
otra excepcion: al `intentar` activo, o se propaga al llamador. Cubre `pa`, `pb`,
`pc` del caso de prueba (tests: `OrderingTypeErrorRoutedV1`).

## 5. Brechas abiertas (con prueba)

Todas estan en `tests/test_stdlib_native_parity.py::KnownGapsV1` como `expectedFailure`.

1. **Elemento float/bool de un parametro lista se lee como int crudo (silencio). — CERRADO.**
   `def g(a): return a[0]` con `g([1.5])` imprimia `4609434218613702656` (bits IEEE-754);
   `[True]` imprimia `1`. Ya pasaba en `e126e01`.
   Intento de cierre REVERTIDO (commits `e4dcbb9` + revert `9961ec9`): etiquetar los
   elementos de un parametro lista sin evidencia de tipo (indexado y `para v en a`) hace
   que TODO entero sobre parametro lista se vuelva slot. Un acumulador `s = 0; s = s + v`
   mezcla int y slot en la misma variable y da basura (`total([1,2,3])` daba `8423648`;
   en `e126e01` daba 6). La suite no lo vio porque no habia prueba de sumas enteras sobre
   parametros; ahora la hay (`ParamListIntAccumV1`).
   **Cierre (2026-11-12, LIST_PARAM_UNKNOWN_ELEM_V1)**: el fallo del intento revertido
   era la MANERA de etiquetar, no la idea. En vez de envolver la COLECCION en un slot
   (doble boxing: el caller lo envolvia y el callee lo envolvia otra vez -> SIGSEGV),
   se etiqueta el **ELEMENTO**: la lista guarda cada item como `PitonSlot{bits,kind}`,
   asi que `piton_seq_get_tagged` devuelve el item con su kind REAL (float/bool/int/str)
   y el consumidor lo trata como slot. La coleccion sigue siendo el `PitonSeq*` crudo.
   El acumulador mixto se resuelve con `DYN_SLOT_VAR_RET_V1`: una variable que en
   algun store recibe un slot se resuelve a "slot" en la inferencia de retorno, y el
   emisor la empaqueta desde el principio (`DYN_SLOT_VAR_SEED_V1`).
2. **Una sola version compilada por funcion (GAP_SHARED_SPECIALIZATION_V1). — CERRADO.**
   Con `g([1,5],2)` y `g(['b'],'c')` en el mismo programa, la llamada int lanzaba
   `TypeError: '<' not supported between 'int' and 'str'` (programa legal en CPython).
   En `bisect`, la misma situacion daba `bisect_left` = `0` en silencio.
   **Cierre (2026-11-12, SHARED_SPECIALIZATION_V1)**: un param que recibe argumentos de
   tipos DISTINTOS en distintos call-sites se marca como "multi-tipo". El caller lo
   envuelve en un slot con el kind real de ESE argumento, y el callee lo trata como slot
   -> la misma funcion sirve para ambos casos (CPython decide el tipo por el VALOR, no
   por el parametro). Excepcion: si el param recibio alguna vez un argumento que no es
   un valor simple (un handle de archivo, un builtin), NO se envuelve: `json.load(fp)`
   necesita el handle crudo.
   Apoyo: `SLOT_PARAM_V1` (un param que recibe un slot se trata como slot),
   `FUNC_AS_VALUE_V1` (una funcion cargada como valor es una closure con origen),
   `CLOSURE_PARAM_ORIGIN_V1` (el param que recibe una closure la vuelve a exponer),
   `SLOT_RETURN_V1` (el resultado de una closure que devuelve un slot ya es un slot:
   re-empaquetarlo guardaba el PUNTERO como si fuera el valor).
3. **bisect sobre listas de tuplas** (GAP_BISECT_TUPLES_V1): mismo origen que (2); con
   un solo tipo de llamada funciona. **CERRADO** por (1): el elemento de `a` llega
   etiquetado y la comparacion de tuplas la decide el runtime.
4. **Variable que mezcla str e int/otro tipo entre asignaciones (silencio).** `cur = ''`
   y luego `cur = w` (con `w` de un parametro) en una rama: el tipo de la variable es
   el de la ultima asignacion emitida; el resultado es basura. Mismo origen que (1).
   Parcialmente mitigado por `DYN_SLOT_VAR_RET_V1` (una variable que alguna vez recibe
   un slot se empaqueta y se resuelve a "slot"), pero la mezcla str/int sin slot sigue
   sin resolverse: es el siguiente frente.
5. **`import dataclasses` falla al cargar**: `MISSING = object()` no es soportado.
   Ya fallaba en la linea base (error ruidoso, no silencio).

## 6. Verificacion

- `tests/test_stdlib_native_parity.py` + `tests/test_json_del_parity.py`: 63 passed,
  3 xfailed (brechas 1-3).
- Suite completa (`python3 -m pytest tests -q`): linea base `e126e01` (worktree limpio):
  43 failed, 1361 passed, 27 skipped. Working tree: 43 failed, 1365 passed, 27 skipped,
  3 xfailed. Conjuntos de fallos por nombre IDENTICOS (0 fallos nuevos). Los 43 fallos
  ya existian en `e126e01` (no son de esta rama).
