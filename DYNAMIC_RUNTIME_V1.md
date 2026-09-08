# Dynamic Runtime V1

Estado: `PASS` cuando el corpus nativo de `tests/test_phase5.py` pasa completo
y el recibo `piton-native-subset-evidence-v1` sigue válido.

Este milestone introduce `PitonValue`, el tagged union de 64 bits que
representa todos los valores en el runtime nativo. Sustituye el modelo
anterior de enteros crudos sin tipo por un sistema con refcount, heap
gestionado y tipos internos.

## Comando canónico

```powershell
py -m piton compilar examples\01_hola.piton --backend=x86 --output build\dynamic-v1\hola.exe --evidencia build\dynamic-v1\receipt.json
```

## Gates automatizados

| Gate | Criterio |
|---|---|
| `PITON_VALUE_WIRED` | `PitonValue` tagged union se usa en todas las operaciones de colección/objeto |
| `HEAP_STRING_OWNERSHIP` | `piton_str_new` + deep-free en todas las rutas (sin leak) |
| `HETEROGENEOUS_COLLECTIONS` | Listas con tipos mixtos pasan differential contra CPython |
| `NESTED_COLLECTIONS` | Listas anidadas pasan differential |
| `DYNAMIC_OBJECT_ATTRS` | Objetos con valores dinámicos (PitonValue) funcionan |
| `REFCOUNTING_CLEAN` | Live counts retornan 0 al exit |
| `RECURSIVE_PRINT` | Estructuras anidadas se imprimen correctamente |
| `ORACLE_PINNED` | CPython 3.12.4 |

## Diseño del wire format

```
Tag 0: NONE        → payload = 0
Tag 1: BOOL        → payload = 0 or 1
Tag 2: INT         → payload = signed 61-bit complement-2
Tag 3: FLOAT_HANDLE → payload = pointer to heap double
Tag 4: OBJECT_HANDLE → payload = pointer to heap object
```

Sub-tags dentro de OBJECT_HANDLE:
```
5: STR     → heap string (refcounted)
6: LIST    → heap list (refcounted, PitonValue items)
7: TUPLE   → heap tuple (refcounted, PitonValue items)
8: DICT    → heap dict (refcounted, PitonValue key/value)
9: SET     → heap set (refcounted, PitonValue items)
10: BIGINT → heap bigint (refcounted)
```

## Alcance demostrado

- Enteros, booleanos, None, floats: inmediatos en el wire format
- Strings: literales en .rdata, heap strings para concat
- Listas, tuplas: elementos PitonValue,soportan enteros y punteros
- Diccionarios: key/value PitonValue, duplicados se actualizan
- Conjuntos: valores PitonValue, deduplicación automática
- Objetos: campos PitonValue con refcount
- BigInt: wire format OBJECT_HANDLE con sub-tag 10
- Impresión recursiva: anidación ilimitada
- Refcounting: decref + free en cleanup al exit

## Límites explícitos

- `WINDOWS_CLEAN_MACHINE_EXECUTION` permanece `NOT_DEMONSTRATED`
- Strings en collections requieren que el emitter boxee el literal
  (soporte completo de strings heterogéneos queda para V2)
- Colecciones anidadas requieren que el emitter distinga punteros
  de enteros (soporte completo queda para V2)
- Async, generators, imports: sin cambios en este milestone
