# Windows native (x86.py) — fallos conocidos P36–P41

Origen: correr `python -m pytest tests/ --ignore=tests/test_windows_validate.py` en CI
Windows (PE). Recorrido 2026-10-03 sobre PR #39. La suite verde es en Linux/x86.

Clasificación por bucket, sin borrar nada: cada grupo existe o NO antes de P36?
El campo **estado** distingue: heredado antes de P36 / aceptado como fail-closed /
feature-gap conocido / **REGRESIÓN candidata** (a investigar).

## Heredado antes de P36 (ya documentado)
- `CollMethodsV1::test_dict_get_missing_key_prints_none` (Linux ya imprime None; Windows sigue fallando)
- `DivergentCluster29V1` (4 tests): subscript/get, keys-sorted, sum sobre bigint, raises tipados
- `SumElementTypeV1` (3): str en sum, listas con elementos str
- `ChrNulV1` (2): chr multibyte, chr(0)
- `WithProtocolNativeV1` (3): propagación de excepción en `con`, supresión, unhandled
- `Phase5Gates::test_x86_bare_reraise_from_catchall_rejected`: access violation 3221225477 (KNOWN: requiere CALL_PROPAGATE/FINALBODY_UNWIND portar a PE)
- `OrderingMixedTypesV1::test_orderable_pairs_still_work`, `NonCallableCallV1` (crash AV), `test_dict_get_missing_key_prints_none`

## Fail-closed / gap declarado
- `DictKeyTypeV1::bool_and_float_keys` — x86 exige key iterable estática
- `ContainsV1::contains_positive_form` — needle int/bool requerido en dict
- `FormatSpecV1::test_float_presentations`, `PercentFormatV1::test_float_spec`, `FStringsV1::test_format_spec` — `%f` / format specs no portados
- `StripV1::test_strip_with_chars` — strip con chars no implementado
- `SumElementTypeV1::test_non_int_elements_fail_closed` — exige rechazo explícito
- `NativeSubsetEvidenceTests::test_cli_writes_passing_native_subset_receipt` — build evidence en PE roto

## REGRESIÓN candidata introducida por P36–P41 (investigar)
- `BuiltinMarkerV1::test_conversion_builtins_as_values` (~13 subtests) y `test_converted_collection_keeps_element_type` (~3): `piton/x86.py` no ramifica antes del chequeo `BUILTIN_MARKER_V1`, así que `lista/tupla/conjunto/diccionario/rango` en posición de valor muere.
- `BoolShortV1::test_dynamic_mixed_types`: `y/o` con operandos mixtos — P41 cubre Linux, falta portarlo al branch Windows.
- `BuiltinMarkerV1::test_range_as_value` (5): solo CRLF EOL (`\r\n` vs `\n`) — probablemente beda de tests y no del emitter.
