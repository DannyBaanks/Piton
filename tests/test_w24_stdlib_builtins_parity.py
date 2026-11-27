from piton.native_differential import compare_native_to_cpython


def test_complex_string():
    # complex('3+4j') -> (3+4j) en CPython 3.12; ambas ramas sin salida
    result = compare_native_to_cpython("complex('3+4j')")
    assert result.equivalent, (result.native, result.oracle)


def test_datetime_fromtimestamp_matches_cpython(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    result = compare_native_to_cpython(
        "desde datetime importar datetime\n"
        "imprimir(datetime.fromtimestamp(0))\n"
    )
    assert result.equivalent, (result.native, result.oracle)


def test_zip_itertools_chain():
    result = compare_native_to_cpython(
        "desde itertools importar chain\n"
        "imprimir(list(zip(chain([1, 2], [3, 4]), [5, 6])))\n"
    )
    assert result.equivalent, (result.native, result.oracle)


def test_os_path_ismount():
    # os.path.ismount('/') -> True (raiz es punto de montaje)
    result = compare_native_to_cpython(
        "importar os\n"
        "imprimir(os.path.ismount('/'))\n"
    )
    assert result.equivalent, (result.native, result.oracle)


def test_bytearray_extend():
    # bytearray.extend(bytes) muta igual que CPython
    result = compare_native_to_cpython(
        "ba = bytearray(b'abc')\n"
        "ba.extend(b'123')\n"
        "imprimir(ba)\n"
    )
    assert result.equivalent, (result.native, result.oracle)


def test_bytes_extend_error():
    # bytes no tiene metodo extend: tanto nativo como oracle fallan (rc != 0)
    result = compare_native_to_cpython(
        "b = bytes(b'abc')\n"
        "b.extend(b'123')\n"
    )
    assert result.native.returncode != 0, result.native
    assert result.oracle.returncode != 0, result.oracle
