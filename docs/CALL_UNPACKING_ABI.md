# PITON — Diseño de ABI para Call-Site Unpacking

Estado: diseño aprobado para `CALL_UNPACKING_V2`; binder bootstrap implementado,
pero el ABI nativo dinámico no es todavía un gate PASS.

## 1. Problema

Las llamadas nativas actuales reciben argumentos físicos directamente en
registros (`rcx`, `rdx`, `r8`, `r9` en Windows; ABI equivalente en Linux) y
rechazan más de cuatro argumentos. Por eso `f(*xs)` y `f(**mapping)` solo pueden
expandirse de forma segura cuando el contenido se conoce durante lowering.

`CALL_UNPACKING_LITERAL_V1` ya cubre literales `list`/`tuple`/`dict`. Este diseño
cubre el siguiente paso sin cambiar la firma de todas las funciones existentes
de una vez.

## 2. Gates

### `CALL_UNPACKING_DYNAMIC4_V1`

- callee conocido estáticamente;
- expansión dinámica de list/tuple hasta cuatro argumentos físicos;
- expansión dinámica de dict contra una firma conocida de hasta cuatro
  parámetros;
- orden de evaluación y duplicados CPython;
- llamadas directas, no closures indirectas;
- exceder cuatro argumentos produce error estructurado, no corrupción.

### `CALL_FRAME_ABI_V1`

- vector de argumentos arbitrario;
- binding por firma en runtime;
- `*args` y `**kwargs` construidos por runtime;
- funciones normales y closures mediante el mismo protocolo;
- límite solo de memoria/recursos documentado, no de cuatro words.

## 3. Representación MIR

No se cambia `call` existente. Se añade una operación separada:

```text
call_unpack(callee, positional_parts, keyword_parts, signature_id)
```

`positional_parts` conserva el orden de evaluación:

```text
("value", v)
("star", sequence)
```

`keyword_parts` conserva el orden de evaluación:

```text
("keyword", name, v)
("kwstar", mapping)
```

El lowering literal puede seguir normalizando a `call`; únicamente la forma
dinámica emite `call_unpack`. No se debe inferir `**mapping` a partir de un
keyword con `arg=None` fuera de esta operación.

## 4. ABI común

```c
typedef struct {
    int64_t *values;
    uint64_t count;
} PitonArgVector;

typedef struct {
    const char **names;
    int64_t *values;
    uint64_t count;
} PitonKwVector;

typedef int64_t (*PitonFrameFn)(const PitonArgVector *, const PitonKwVector *);
```

El runtime debe exponer una entrada equivalente a:

```c
int64_t piton_call_frame(PitonFrameFn callee,
                         const PitonArgVector *pos,
                         const PitonKwVector *kw,
                         const PitonSignature *signature);
```

La firma contiene nombres de parámetros, defaults, flags positional-only,
keyword-only, varargs y kwargs. El binding ocurre una sola vez en runtime y
produce los argumentos de la función, no en cada backend.

## 5. Compatibilidad con funciones actuales

Durante la migración se mantienen dos entradas:

1. `call`: ABI directo actual, rápido y sin cambios.
2. `call_unpack`: ABI de frame, inicialmente con dispatcher `switch(count)`
   para funciones existentes de hasta cuatro parámetros.

Cada `MIRFunction` recibe un `signature_id`. El backend emite la tabla de
firmas una sola vez por módulo. Las funciones levantadas de closures migran a
la misma entrada cuando `CALL_FRAME_ABI_V1` esté disponible.

## 6. Semántica obligatoria

El binder debe reproducir, con tests explícitos:

- evaluación izquierda a derecha de valores y expansiones;
- una sola evaluación de cada expresión;
- `TypeError` si `*` no es iterable;
- `TypeError` si `**` no es mapping;
- claves de `**` que sean strings;
- argumento posicional duplicado por keyword;
- keyword duplicado entre mappings;
- keyword inesperado;
- parámetro requerido ausente;
- demasiados argumentos;
- `*args` y `**kwargs` preservando orden observable.

## 7. Seguridad del cambio

- Ningún backend debe leer posiciones fuera de `count`.
- El dispatcher debe validar la firma antes de llamar al function pointer.
- El vector vive hasta que termina la llamada; no se guardan punteros a
  temporales después del retorno.
- Las colecciones que alimentan una expansión no se liberan antes del binding.
- Un error de tipo o de aridad termina con el código de error del contrato,
  nunca con una llamada parcial.
- El corpus debe incluir ASAN/UBSAN donde estén disponibles y un control de
  heap-live-count.

## 8. Orden de implementación

1. Extraer `PitonSignature` desde metadata MIR existente.
2. Añadir iteración de secuencias y mappings en ambos runtimes.
3. Añadir binder común y tests unitarios sin backend.
4. Añadir `call_unpack` MIR solo para dynamic4.
5. Emitir dynamic4 en Windows y Linux.
6. Añadir corpus diferencial y negativos.
7. Migrar closures a `PitonFrameFn`.
8. Quitar el límite de cuatro con `CALL_FRAME_ABI_V1`.
9. Repetir corpus completo y cerrar `CALL_UNPACKING_V2`.

## 9. Criterio de cierre

`CALL_UNPACKING_V2 = PASS` solo cuando el mismo corpus pasa en Windows/Linux,
incluye llamadas normales, variádicas y closures, cubre errores y no hay límite
artificial de cuatro argumentos. Hasta entonces el estado correcto es
`PARTIAL`: `CALL_UNPACKING_LITERAL_V1 = PASS`, dynamic4 o frame ABI según el
subgate alcanzado.

## 10. Progreso actual

- `CALL_UNPACKING_LITERAL_V1`: PASS, Win/Linux.
- `bind_call_vectors`: implementado en `piton/call_runtime.py` y probado con
  defaults, varargs, kwargs, duplicados y keywords inesperados.
- `CALL_UNPACKING_DYNAMIC4_V1`: pendiente; aún no conectado a MIR ni a los
  backends nativos.

## 11. Progreso de M3

- `CLOSURE_FRAME_ABI_INITIAL_V1`: implementado en Windows/Linux.
- Las funciones levantadas de closures usan firma `long* frame`.
- `PitonClosure` almacena captures dinámicos en heap/arena, sin `cells[4]`.
- `piton_frame_call` cubre llamadas inmediatas a funciones levantadas.
- `piton_closure_call_frame` cubre closures escapadas y conserva fallback para
  funciones directas del ABI antiguo.
- Verificación actual: fase Windows `181 passed`, fase Linux `117 passed`;
  incluye una closure con 5 captures y 5 argumentos.
- Pendiente: closures variádicas (`*args`/`**kwargs`), lifetime/GC de frames y
  retirar los helpers legacy cuando no quede ningún consumidor.
