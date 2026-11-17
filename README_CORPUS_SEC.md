# Corpus Secundario de Expresiones y Tipos (PITONF-C1A55E77)

**Corpus SECUNDARIO**: 25 ejemplos independientes para verificar la paridad PITON ↔ CPython 3.12.3.

## Propósito

Este corpus es independiente del `corpus_v4` (tier B). Mientras que `corpus_v4` mide un subconjunto de Python del tutorial oficial de CPython con adaptación mecánica, este corpus secundario está diseñado para:

1. **Elegir casos que un implementador no seleccionaría a propósito** - ejemplos que exponen límites y edge cases del runtime nativo.
2. **Cobertura específica de áreas críticas** sin dependencia externa a stdlib real.
3. **Verificación independiente** - resultados que no pueden ser "aprobados" accidentalmente.

## Estructura

```
corpus_sec/
├── expr/
│   ├── 01_int_division_negatives.py      # división entera y módulo con negativos
│   ├── 02_pow_mod.py                     # potencias y módulo
│   ├── 03_round_trunc.py                 # round() y trunc()
│   ├── 04_chained_comparisons.py         # comparaciones encadenadas
│   ├── 05_string_slicing_step.py         # slicing con paso y negativos
│   ├── 06_str_methods_split_join.py      # str.split(), str.join()
│   ├── 07_str_strip_find_replace.py      # str.strip(), str.find(), str.replace()
│   ├── 08_str_zfill_center_ljust.py      # str.zfill(), str.center(), str.ljust()
│   ├── 09_fstring_format.py              # f-strings con width, alineación, precisión
│   ├── 10_percent_format.py              # formateo con operador %
│   ├── 11_list_comprehensions.py         # comprensiones con filtro
│   ├── 12_nested_dicts.py                # diccionarios anidados
│   ├── 13_dict_comprehensions.py         # comprensiones de diccionarios con filtro
│   ├── 14_list_sort_key.py               # ordenación con key, estabilidad
│   ├── 15_tuple_unpacking.py             # unpacking, enumerate, zip
│   ├── 16_range_negative_step.py         # range con paso negativo
│   ├── 17_zip_enumerate.py               # enumerate() y zip() con iterables
│   ├── 18_isinstance_conversion.py       # isinstance(), conversiones numéricas, bool() de vacíos
│   ├── 19_equality_identity.py           # igualdad vs identidad con ints
│   ├── 20_string_fstring_conversion.py # f-strings con conversiones !s, !r
│   ├── 21_dict_get_setdefault_pop.py     # dict.get(), setdefault(), pop()
│   ├── 22_list_comprehension_nested.py # comprensiones anidadas con filtros
│   ├── 23_dict_order_stability.py        # estabilidad de orden en dict
│   ├── 24_float_comparison.py          # comparaciones de floats
│   └── 25_string_escape.py               # strings con escape y caracteres especiales
└── README_CORPUS_SEC.md
```

## Áreas cubiertas

### Números
- Int/float
- División entera y módulo con negativos (`7 // -3`, `7 % -3`)
- Potencias (`2 ** 10`, `(-2) ** 10`)
- Comparaciones encadenadas

### Strings
- Slicing con paso y negativos (`s[::-1]`, `s[7:1:-1]`)
- Métodos: `split()`, `join()`, `strip()`, `find()`, `replace()`, `zfill()`, `center()`, `ljust()`, `rjust()`

### Listas/Dicts
- Comprensiones con filtro
- Anidamiento
- `.get()`, `.setdefault()`, `.pop()`
- Ordenación con `key`

### Tuplas/Rangos
- Unpacking
- `enumerate()`, `zip()`
- `range()` con paso negativo

### Tipos
- `isinstance()`
- Conversiones
- `bool()` de vacíos
- Igualdad vs identidad

## Runner

```bash
python3 tools/parity_corpus_sec.py --json /tmp/sec_expr.json
```

### Criterio de equivalencia (HONESTO)

Un script es **EQUIV** si:

1. Ambos lanzan la misma excepción (mismo nombre de clase), O
2. El stdout coincide byte a byte y el código de salida coincide.

**No normaliza espacios ni ignora stderr.** Cualquier diferencia es DIVERGENT.

## Restricciones conocidas de PITON

- No admite comentarios `#`
- Keywords en español: `si`, `sino`, `mientras`, `para`, `funcion`, `clase`, etc.
- No soporta operador `is` a nivel nativo
- No soporta `round()` con precisión negativa fuera de ciertos rangos
- `float //`, `%` y `**` no son nativos para floats
- Generadores (`()`) no se imprimen como CPython espera
- Algunas combinaciones de `key=` con `reverse=` en `sorted()` pueden fallar

## Token de sesión

Incluye en cada wake: `PITONF-C1A55E77`