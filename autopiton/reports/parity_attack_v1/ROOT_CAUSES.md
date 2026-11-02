# ROOT_CAUSES — Diagnóstico de Causa Raíz (Fases A y B)

## 1. Causas Raíz de los Cinco Contraejemplos Históricos (Fase A)

### RC-A1: Desempaquetado de diccionarios `{**a, **b}`
- **Ubicación:** `piton/mir.py` (`_lower_expr`, `HIRKind.DICT`) y `piton/linux_x86.py` (`op == "collection_extend"`, `piton_dict_extend_any`).
- **Mecanismo del defecto:** El parser (`piton/parser.py`) y el lowering CST→HIR (`piton/lower.py`) ya representaban entradas `**expr` en literales de diccionario mediante claves `None` en `HIRKind.DICT`. Sin embargo, `piton/mir.py` rechazaba explícitamente cualquier `HIRKind.DICT` con `any(k is None for k in node.keys)` lanzando `MIRLoweringError("dict literal merge ({**a, **b}) is not supported in the native subset")`.
- **Corrección:**
  1. En `piton/mir.py`, cuando un literal `HIRKind.DICT` contiene entradas `None` (`**d`), se emite `build_collection("dict", ())` seguido en orden estricto de izquierda a derecha por `collection_extend(dst, val, handler)` para cada `**d` y `subscript_store(dst, k, v, handler)` para cada par `k: v`.
  2. En `piton/linux_x86.py`, se añadió `piton_dict_extend_any(PitonDict* dst, long src_bits)` que valida en tiempo de ejecución que el operando sea un `PitonDict` (`PK_DICT`), lanzando `TypeError: '<tipo>' object is not a mapping` si no lo es, y copia/sobrescribe las claves en orden preservando la semántica *last-wins* de CPython.

### RC-A2: `zip()` con 0, 1, 2, 3 y N iterables
- **Ubicación:** `piton/mir.py` (`_lower_builtin_call`) y `piton/linux_x86.py` (`PitonZipIterator`, `piton_zip_new_multi`, `piton_zip_next`, `builtin == "zip"`).
- **Mecanismo del defecto:** `piton/mir.py` exigía `len(node.args) == 2` para `zip()`, y el runtime C (`PitonZipIterator`) sólo tenía dos campos fijos (`left`, `right`) y construía tuplas de longitud fija 2.
- **Corrección:**
  1. Se eliminó la restricción `len(node.args) != 2` en `piton/mir.py`, pasando la tupla de todos los iterables a `iter_init("zip", tuple(args))` y marcando el resultado como secuencia de tuplas heterogéneas/homogéneas.
  2. En `piton/linux_x86.py`, se generalizó `PitonZipIterator` con `arity` y `PitonSeq** seqs` vía `piton_zip_new_multi(long arity, PitonSeq** seqs)` y `piton_zip_next(long raw)`:
     - `arity == 0` agota inmediatamente con `StopIteration` (`list(zip()) == []`).
     - `arity >= 1` detiene la iteración en cuanto el iterable más corto se agota y empaqueta una `PK_TUPLE` de longitud `arity`.
     - Materializa listas, tuplas, `range`, `str`, `set`, `dict`, generadores e iteradores respetando el orden de evaluación de izquierda a derecha.

### RC-A3: `super().f(*args)` y empaquetado variádico / aridad en `method_call`
- **Ubicación:** `piton/linux_x86.py` (`op == "method_call"`, `_pack_call_varargs`) y `piton/mir.py` (`MIRFunction.default_params`).
- **Mecanismo del defecto:**
  1. Mientras que las llamadas a funciones libres (`_emit_user_function_call`) empaquetaban los argumentos posicionales sobrantes en una tupla `PK_TUPLE` cuando el destino declaraba `*args`, la emisión de `op == "method_call"` (tanto en llamadas directas a método como en resoluciones `super().f(...)`) pasaba todos los argumentos planos a la función C `A__f(self, args)`, provocando un error de compilación en GCC (`too many arguments to function 'A__f'`).
  2. Además, `builder.function.defaults` almacena `[None, ...]` con la misma longitud que `params`, por lo que `len(function.defaults)` confundía parámetros requeridos (cuyo slot en `defaults` es `None`) con parámetros con valor por defecto.
- **Corrección:**
  1. Se añadió `MIRFunction.default_params` en `piton/mir.py` con los nombres exactos de parámetros que poseen default.
  2. Se extrajo `_pack_call_varargs` en `piton/linux_x86.py` y se integró junto con validación de aridad posicional (`TypeError: ... missing N required positional arguments` / `takes ... positional arguments`) en `op == "method_call"` tanto para llamadas vía `super()` como directas.

### RC-A4 y RC-A5: Validación de constructores implícitos y explícitos (`class A: pass; A(0)`)
- **Ubicación:** `piton/mir.py` (`_lower_class_construct`).
- **Mecanismo del defecto:** El commit previo `00ef19c` (`mir: relax class without __init__ to ignore args/kwargs`) había reemplazado el rechazo de argumentos en clases sin `__init__` por un `pass` silencioso que ignoraba los argumentos y devolvía la instancia con `exit_code=0`.
- **Corrección:** En `_lower_class_construct` (`piton/mir.py`), cuando una clase (y su MRO) no define `__init__` ni hereda de un tipo builtin/excepción, si recibe argumentos posicionales o keyword, primero evalúa los argumentos de izquierda a derecha (preservando efectos secundarios observables) y emite `raise_typed("TypeError", f"{cls_name}() takes no arguments", handler)` en tiempo de ejecución (capturable por `try/except TypeError`).

---

## 2. Causas Raíz Descubiertas y Corregidas en el Ciclo de Ataque Diferencial (Fase B)

### RC-B1: Retorno de closures llamadas vía `frame_call` (`case-a049289f937a7127`, `case-cf28812ae3e06f1e` en `seed=23`)
- **Mecanismo del defecto:** `_infer_return_types` sólo inspeccionaba `op == "call"` e ignoraba `op == "frame_call"`, y el emisor de `op == "frame_call"` fijaba `types[result] = "int"`. Cuando una función externa retornaba `f2()` llamada por `frame_call` con resultado `bool` o `float`, se imprimía `0`/`1` o los bits IEEE 754 como entero.
- **Corrección:** Se incluyó `frame_call` en `_infer_return_types` y se propagó `self.function_return_types.get(_fname, "int")` en la emisión de `frame_call`.

### RC-B2: Inferencia de retorno sobre `@property` / `get_attr` y `cell_load` (`case-13562e1b7bfa4bc0`, `case-fa841914aca19bd2` en `seed=99`)
- **Mecanismo del defecto:** `_infer_return_types` no propagaba tipos a través de `op == "get_attr"` (lectura de `self._v` en un `@property` donde `__init__` asignó un `float` por defecto `7.5 * 3.5`) ni a través de `op == "cell_load"` (lectura de celdas `bool` capturadas por una closure).
- **Corrección:** Se añadieron ramas para `get_attr` (consultando `self.class_properties` y `self._instance_attr_types` en el MRO) y `cell_load` (consultando `self._cell_types`) dentro de `_infer_return_types`.

### RC-B3: Polimorfismo en funciones identidad (`typing.cast`) y tuplas centinela `('__name__', ...)` en defaults
- **Mecanismo del defecto:**
  1. Sembrar `_call_param_types` globalmente sobre funciones identidad (`def cast(typ, val): return val`) hacía que llamadas sucesivas `cast(int, 7)` y `cast(str, 'hola')` tiparan el retorno de ambas como `str`, causando `SIGSEGV` al imprimir `7` como `char*`.
  2. Las tuplas centinela internas `('__name__', 'a')` y `('__attr__', 'mod', 'attr')` en `function.defaults` eran tratadas por `isinstance(_pd_value, tuple)` como defaults literales de tipo `tuple`.
- **Corrección:** Se propagó el tipo del argumento por sitio de llamada para funciones en `self._identity_params` y se excluyeron las tuplas centinela `('__name__', ...)` / `('__attr__', ...)` del seed de defaults tipo `tuple`.
