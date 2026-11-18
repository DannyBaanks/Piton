"""Gates de paridad: json.dumps nativo, borrar nombres no ligados, desborde de int().

Los valores esperados se verificaron contra CPython 3.12 (salida byte a byte).
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

from native_linux import compile_native_linux


def _run(source: str):
    with tempfile.TemporaryDirectory(prefix="piton-jdel-") as directory:
        exe = Path(directory) / "prog"
        compile_native_linux(source, exe)
        return subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)


class JsonDumpsNativeV1(unittest.TestCase):
    """JSON_DUMPS_NATIVE_V1: json.dumps baja a un emisor C sobre valores
    etiquetados; la salida es la de CPython 3.12 para datos ASCII."""

    def _dumps_line(self, call: str) -> str:
        r = _run(f"importar json\nimprimir (json .dumps ({call}))\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_mixed_list(self):
        self.assertEqual(self._dumps_line('[1 ,"simple" ,"list" ]'), '[1, "simple", "list"]\n')

    def test_nested_sort_keys(self):
        self.assertEqual(
            self._dumps_line('{"b": 1 ,"a": [1 ,{"x": "y" }]}, sort_keys =Verdadero'),
            '{"a": [1, {"x": "y"}], "b": 1}\n',
        )

    def test_indent(self):
        self.assertEqual(
            self._dumps_line('{"b": [1 ,2 ],"a": {"z": Falso ,"c": Nada }}, indent =2'),
            '{\n  "b": [\n    1,\n    2\n  ],\n  "a": {\n    "z": false,\n    "c": null\n  }\n}\n',
        )

    def test_empty_containers(self):
        self.assertEqual(self._dumps_line("[]"), "[]\n")
        self.assertEqual(self._dumps_line("{}"), "{}\n")

    def test_escapes_and_control_chars(self):
        self.assertEqual(self._dumps_line('"a\\"b\\\\c"'), '"a\\"b\\\\c"\n')
        self.assertEqual(self._dumps_line('"a" + chr (1 ) + chr (127 ) + "z"'), '"a\\u0001\\u007fz"\n')

    def test_unicode_ensure_ascii_and_raw(self):
        # pares sustitutos para U+1F600; ensure_ascii=Falso emite UTF-8 tal cual
        self.assertEqual(self._dumps_line("'é€😀'"), '"\\u00e9\\u20ac\\ud83d\\ude00"\n')
        self.assertEqual(self._dumps_line("'é€😀', ensure_ascii =Falso"), '"é€😀"\n')

    def test_floats_special_values(self):
        self.assertEqual(
            self._dumps_line('[1.0 ,0.1 ,1e16 ,-0.0 ,1e-7 ,3.14159 ]'),
            "[1.0, 0.1, 1e+16, -0.0, 1e-07, 3.14159]\n",
        )
        self.assertEqual(
            self._dumps_line('[float ("nan" ),float ("inf" ),-float ("inf" )]'),
            "[NaN, Infinity, -Infinity]\n",
        )

    def test_bigint_and_int_bounds(self):
        self.assertEqual(self._dumps_line("12345678901234567890123"), "12345678901234567890123\n")
        self.assertEqual(self._dumps_line("[-5 ,0 ,9223372036854775807 ]"), "[-5, 0, 9223372036854775807]\n")

    def test_non_str_keys(self):
        self.assertEqual(
            self._dumps_line('{1 : "a" ,2.5 : "b" ,"x" : "c" ,Nada : "d" }'),
            '{"1": "a", "2.5": "b", "x": "c", "null": "d"}\n',
        )

    def test_tuple_as_array(self):
        self.assertEqual(self._dumps_line("(1 ,2 )"), "[1, 2]\n")

    def test_non_serializable_raises_typeerror(self):
        r = _run('importar json\nimprimir (json .dumps ([1 ,{1 ,2 }]))\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn("TypeError: Object of type set is not JSON serializable", r.stderr)

    def test_bad_key_raises_typeerror(self):
        r = _run('importar json\nimprimir (json .dumps ({(1 ,2 ):"a" }))\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn("TypeError: keys must be str, int, float, bool or None, not tuple", r.stderr)

class JsonLoadsNativeV1(unittest.TestCase):
    """JSON_LOADS_NATIVE_V1: json.loads/load/dump en C; mismos valores y mensajes
    de error que CPython (el tipo de excepcion es ValueError, no JSONDecodeError)."""

    def _roundtrip(self, text: str) -> str:
        lit = '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
        r = _run(f"importar json\nimprimir (json .dumps (json .loads ({lit })))\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_values_roundtrip(self):
        self.assertEqual(self._roundtrip('{"b": 1, "a": [true, false, null]}'),
                         '{"b": 1, "a": [true, false, null]}\n')
        self.assertEqual(self._roundtrip('"\\u00e9\\ud83d\\ude00"'), '"\\u00e9\\ud83d\\ude00"\n')
        self.assertEqual(self._roundtrip("  [ ]  "), "[]\n")
        self.assertEqual(self._roundtrip("[NaN, Infinity, -Infinity]"), "[NaN, Infinity, -Infinity]\n")

    def test_floats_exact_range(self):
        self.assertEqual(self._roundtrip("[1e5, 1.5E-3, -12.25, 0.1, 2e+3]"),
                         "[100000.0, 0.0015, -12.25, 0.1, 2000.0]\n")

    def test_float_outside_exact_range_is_loud(self):
        r = _run('importar json\nimprimir (json .loads ("1.23456789012345678"))\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn("fuera del rango exacto", r.stderr)

    def test_error_messages_match_cpython(self):
        r = _run('importar json\nintentar:\n    json .loads ("[1,]")\nexcepto ValueError:\n    imprimir ("VE")\n')
        self.assertEqual((r.returncode, r.stdout), (0, "VE\n"))
        r = _run('importar json\njson .loads ("[1 2]")\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn("ValueError: Expecting ',' delimiter: line 1 column 4 (char 3)", r.stderr)

    def test_dump_and_load_file(self):
        import tempfile as _tf
        with _tf.TemporaryDirectory(prefix="piton-jdl-") as d:
            path = str(Path(d) / "x.json").replace("\\", "/")
            r = _run(
                f'importar json\nf = abrir ("{path}", "w")\njson .dump ([1 ,"a" ,Verdadero ], f )\nf .close ()\n'
                f'g = abrir ("{path}")\nimprimir (json .dumps (json .load (g )))\n'
            )
            self.assertEqual((r.returncode, r.stdout), (0, '[1, "a", true]\n'))


class JsonFromImportV1(unittest.TestCase):
    """JSON_FROM_IMPORT_V1: `desde json importar X` baja al mismo lowering nativo."""

    def test_from_import_all_four(self):
        import tempfile as _tf
        with _tf.TemporaryDirectory(prefix="piton-jfi-") as d:
            path = str(Path(d) / "x.json").replace("\\", "/")
            r = _run(
                "desde json importar loads, dumps, dump, load\n"
                'd = loads ("[1, \\"a\\", true]")\n'
                "imprimir (dumps (d , sort_keys =Verdadero ))\n"
                f'f = abrir ("{path}", "w")\ndump ({{"k": [1, 2.5]}}, f )\nf .close ()\n'
                f'imprimir (dumps (load (abrir ("{path}")) ))\n'
            )
            self.assertEqual((r.returncode, r.stdout), (0, '[1, "a", true]\n{"k": [1, 2.5]}\n'))

    def test_from_import_alias(self):
        r = _run('desde json importar loads como ld\nimprimir (ld ("{\\"a\\": 1}") )\n')
        self.assertEqual((r.returncode, r.stdout), (0, "{'a': 1}\n"))


class ParamTypeConflictV1(unittest.TestCase):
    """PARAM_TYPE_CONFLICT_V1: un parametro usado con isinstance/tipo que recibe
    tipos distintos en distintas llamadas falla en COMPILACION (antes daba un
    resultado silenciosamente falso)."""

    def _build_error(self, source: str):
        from piton.x86 import NativeBuildError
        with tempfile.TemporaryDirectory(prefix="piton-ptc-") as directory:
            with self.assertRaises(NativeBuildError) as ctx:
                compile_native_linux(source, Path(directory) / "p")
        return str(ctx.exception)

    def test_isinstance_mixed_param_fails_closed(self):
        msg = self._build_error(
            "funcion f (obj ):\n    si es_instancia (obj ,int):\n        devolver 1\n    devolver 0\n"
            "imprimir (f (3 ))\nimprimir (f (\"a\" ))\n"
        )
        self.assertIn("recibe tipos distintos", msg)

    def test_type_mixed_param_fails_closed(self):
        msg = self._build_error(
            "funcion f (x ):\n    si tipo (x )== int:\n        devolver 1\n    devolver 0\n"
            "imprimir (f (3 ))\nimprimir (f (\"a\" ))\n"
        )
        self.assertIn("recibe tipos distintos", msg)

    def test_single_type_param_still_compiles(self):
        r = _run("funcion f (obj ):\n    si es_instancia (obj ,int):\n        devolver 1\n    devolver 0\nimprimir (f (3 ))\nimprimir (f (4 ))\n")
        self.assertEqual((r.returncode, r.stdout), (0, "1\n1\n"))


class JsonValueAliasV1(unittest.TestCase):
    """JSON_VALUE_ALIAS_V1: `f = json.X` con el nombre ligado una sola vez se baja
    como llamadas directas. Pasar el alias como valor (`h = f`) falla en compilacion."""

    def test_alias_call_loads_and_dumps(self):
        r = _run('importar json\nf = json .loads\nimprimir (f ("[1, 2]" ))\ng = json .dumps\nimprimir (g ([1 ,"a" ]))\n')
        self.assertEqual((r.returncode, r.stdout), (0, '[1, 2]\n[1, "a"]\n'))

    def test_from_import_alias_call(self):
        r = _run('desde json importar loads como ld\nf = ld\nimprimir (f ("{\\"a\\": 1}") )\n')
        self.assertEqual((r.returncode, r.stdout), (0, "{'a': 1}\n"))

    def test_alias_used_as_value_is_compile_error(self):
        from piton.x86 import NativeBuildError
        with tempfile.TemporaryDirectory(prefix="piton-jv-") as directory:
            with self.assertRaises(NativeBuildError):
                compile_native_linux("importar json\nf = json .dumps\nh = f\n", Path(directory) / "p")

    def test_unused_alias_compiles(self):
        r = _run("importar json\nh = json .dumps\nimprimir (1 )\n")
        self.assertEqual((r.returncode, r.stdout), (0, "1\n"))


class JsonAliasOrderV1(unittest.TestCase):
    """JSON_VALUE_ALIAS_V2: el alias se liga al ejecutarse la asignacion; antes, NameError
    (como CPython), tambien dentro de bucles y funciones."""

    def test_call_before_assignment_nameerror(self):
        r = _run('importar json\nimprimir (f ("[1]" ))\nf = json .loads\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("NameError: name f is not defined", r.stderr)

    def test_loop_first_iteration_nameerror(self):
        r = _run('importar json\ni = 0\nmientras i < 2:\n    intentar:\n        imprimir (f ("[1]" ))\n'
                 '    excepto NameError:\n        imprimir ("NE")\n    f = json .loads\n    i = i + 1\n')
        self.assertEqual((r.returncode, r.stdout), (0, "NE\n[1]\n"))

    def test_function_sees_alias_defined_later(self):
        r = _run('importar json\nfuncion g ():\n    devolver f ("[2]" )\nf = json .loads\nimprimir (g ())\n')
        self.assertEqual((r.returncode, r.stdout), (0, "[2]\n"))


class DynSlotOpsV1(unittest.TestCase):
    """DYN_SLOT_*: un valor de json.loads se opera por su etiqueta real (indexar, iterar, len,
    verdad, en, aritmetica, comparacion, es_instancia). Salidas verificadas contra CPython 3.12."""

    def test_index_len_iterate(self):
        r = _run('importar json\nd = json .loads ("{\\"a\\": [1, 2], \\"b\\": \\"x\\"}")\nimprimir (d ["a"])\nimprimir (longitud (d ))\nimprimir (d ["b"])\nl = json .loads ("[10, 20]")\nimprimir (l [1 ])\npara x en l:\n    imprimir (x )\n')
        self.assertEqual((r.returncode, r.stdout), (0, '[1, 2]\n2\nx\n20\n10\n20\n'))

    def test_truth_membership_arith_isinstance(self):
        r = _run('importar json\nd = json .loads ("{\\"a\\": [1, 2, {\\"z\\": \\"q\\"}], \\"n\\": 5, \\"s\\": \\"hi\\", \\"e\\": [], \\"t\\": true, \\"f\\": 2.5, \\"nul\\": null}")\nimprimir (d ["a"][2 ]["z"])\nsi d ["a"]:\n    imprimir ("a no vacio")\nsi d ["e"]:\n    imprimir ("MAL e")\nsino:\n    imprimir ("e vacio")\nsi "n" en d:\n    imprimir ("n en d")\nsi "zz" en d:\n    imprimir ("MAL zz")\nimprimir (d ["n"]== 5 )\nimprimir (d ["n"]+ 1 )\nimprimir (d ["s"]+ "!" )\nimprimir (es_instancia (d ["n"],int ))\nimprimir (es_instancia (d ["s"],str ))\nimprimir (es_instancia (d ["a"],list ))\nimprimir (es_instancia (d ["nul"],int ))\nx = d ["a"][0 ]\nimprimir (x )\nimprimir (d ["f"]* 2 )\nimprimir (d ["t"])\nimprimir (longitud (d ["a"]))\npara k en d:\n    imprimir (k )\n')
        self.assertEqual((r.returncode, r.stdout), (0, 'q\na no vacio\ne vacio\nn en d\nTrue\n6\nhi!\nTrue\nTrue\nTrue\nFalse\n1\n5.0\nTrue\n3\na\nn\ns\ne\nt\nf\nnul\n'))

    def test_arith_compare_errors(self):
        r = _run('importar json\nd = json .loads ("{\\"a\\": [10, 20, 30], \\"n\\": 7, \\"s\\": \\"abc\\", \\"f\\": 1.5, \\"neg\\": -4}")\nimprimir (d ["n"]- 2 )\nimprimir (d ["n"]* d ["n"])\nimprimir (d ["n"]// 2 )\nimprimir (d ["n"]% 4 )\nimprimir (d ["neg"]// 3 )\nimprimir (d ["neg"]% 3 )\nimprimir (d ["n"]/ 2 )\nimprimir (d ["n"]** 2 )\nimprimir (d ["f"]+ d ["n"])\nimprimir (d ["s"]* 2 )\nimprimir (d ["a"]+ [1 ])\nimprimir (d ["n"]> 3 )\nimprimir (d ["n"]<= 7 )\nimprimir (d ["s"]< "abd")\nimprimir (d ["s"]== "abc")\nimprimir (d ["a"]== [10 ,20 ,30 ])\nimprimir (d ["f"]> 1 )\ntotal = 0\npara v en d ["a"]:\n    total = total + v\nimprimir (total )\npara ch en d ["s"]:\n    imprimir (ch )\nintentar:\n    imprimir (d ["zz"])\nexcepto KeyError:\n    imprimir ("KeyError")\nintentar:\n    imprimir (d ["a"][9 ])\nexcepto IndexError:\n    imprimir ("IndexError")\nintentar:\n    imprimir (d ["n"]/ 0 )\nexcepto ZeroDivisionError:\n    imprimir ("ZDE")\nintentar:\n    imprimir (d ["s"]+ 1 )\nexcepto TypeError:\n    imprimir ("TE")\nintentar:\n    imprimir (d ["s"]< 1 )\nexcepto TypeError:\n    imprimir ("TE2")\n')
        self.assertEqual((r.returncode, r.stdout), (0, '5\n49\n3\n3\n-2\n2\n3.5\n49\n8.5\nabcabc\n[10, 20, 30, 1]\nTrue\nTrue\nTrue\nTrue\nTrue\nTrue\n60\na\nb\nc\nKeyError\nIndexError\nZDE\nTE\nTE2\n'))

    def test_loop_accumulation(self):
        r = _run('importar json\nd = json .loads ("[10, 20, 30]")\ntotal = 0\npara v en d:\n    total = total + v\nimprimir (total )\n')
        self.assertEqual((r.returncode, r.stdout), (0, '60\n'))

    def test_function_returning_loads(self):
        r = _run('importar json\nfuncion g (s ):\n    devolver json .loads (s )\nd = g ("[1, \\"a\\"]" )\nimprimir (d )\nimprimir (json .dumps (d ))\n')
        self.assertEqual((r.returncode, r.stdout), (0, '[1, \'a\']\n[1, "a"]\n'))


class JsonFirstClassV1(unittest.TestCase):
    """JSON_FIRST_CLASS_V1: json.loads / json.load como valores de primera clase."""

    def test_loads_passed_as_argument(self):
        r = _run('importar json\nfuncion aplica (fn ,x ):\n    devolver fn (x )\nh = json .loads\nimprimir (aplica (h ,"[3]" ))\n')
        self.assertEqual((r.returncode, r.stdout), (0, '[3]\n'))

    def test_function_value_results_are_dynamic(self):
        r = _run('importar json\nfuncion aplica (fn ,x ):\n    devolver fn (x )\nfuncion usa (fn ):\n    r = fn ("{\\"k\\": [1, 2, 3], \\"n\\": 4}")\n    devolver r ["k"][1 ]+ r ["n"]\nh = json .loads\nimprimir (aplica (h ,"[3]" ))\nimprimir (aplica (h ,"{\\"a\\": true}" ))\nimprimir (usa (h ))\nv = aplica (h ,"[1, 2]" )\nimprimir (longitud (v ))\nimprimir (json .dumps (v ))\n')
        self.assertEqual((r.returncode, r.stdout), (0, "[3]\n{'a': True}\n6\n2\n[1, 2]\n"))

    def test_load_first_class_and_type_error(self):
        import tempfile as _tf
        with _tf.TemporaryDirectory(prefix="piton-fcl-") as d:
            path = str(Path(d) / "x.json").replace("\\", "/")
            Path(path).write_text('{"z": [1, 2], "w": "q"}', encoding="utf-8")
            r = _run(
                "importar json\nfuncion lee (fn ,arg ):\n    devolver fn (arg )\n"
                "l = json .load\nr = lee (l ,abrir (\"" + path + "\"))\nimprimir (r )\nimprimir (r [\"z\"][1 ])\n"
                "imprimir (lee (json .loads ,\"[7]\"))\n"
                "intentar:\n    lee (json .loads ,5 )\nexcepto TypeError:\n    imprimir (\"TE\")\n"
            )
            self.assertEqual((r.returncode, r.stdout), (0, "{'z': [1, 2], 'w': 'q'}\n2\n[7]\nTE\n"))

    def test_dumps_value_in_builtin_is_compile_error(self):
        from piton.x86 import NativeBuildError
        with tempfile.TemporaryDirectory(prefix="piton-jv-") as directory:
            with self.assertRaises(NativeBuildError):
                compile_native_linux("importar json\nr = lista (map (json .dumps ,[[1 ]]))\nimprimir (r )\n", Path(directory) / "p")

class JsonSpecializeV1(unittest.TestCase):
    """JSON_SPECIALIZE_V1: json.dumps / json.dump pasados como argumento a una funcion de usuario
    generan una copia por punto de llamada donde `fn(x)` es la llamada directa (tipos estaticos)."""

    def test_dumps_as_callback_matches_cpython(self):
        r = _run('importar json\nfuncion aplica (fn ,x ):\n    devolver fn (x )\nfuncion doble (fn ,a ,b ):\n    devolver fn (a )+ " | " + fn (b )\nfuncion vuelca (fn ,x ,k ):\n    devolver fn (x ,sort_keys =k )\nh = json .dumps\nimprimir (aplica (json .dumps ,[1 ,"a" ,2.5 ,Verdadero ,Nada ]))\nimprimir (aplica (h ,{"b": 1 ,"a": [1.5 ,Falso ]}))\nimprimir (doble (json .dumps ,3.5 ,Verdadero ))\nimprimir (doble (h ,"x" ,[1 ]))\nimprimir (aplica (json .dumps ,Nada ))\nimprimir (aplica (json .dumps ,1.5 ))\n')
        self.assertEqual((r.returncode, r.stdout), (0, '[1, "a", 2.5, true, null]\n{"b": 1, "a": [1.5, false]}\n3.5 | true\n"x" | [1]\nnull\n1.5\n'))

    def test_recursion_and_dump_to_file(self):
        import tempfile as _tf
        with _tf.TemporaryDirectory(prefix="piton-jsp-") as d:
            path = str(Path(d) / "x.json").replace("\\", "/")
            r = _run(
                "importar json\nfuncion cuenta (fn ,x ,n ):\n    si n == 0:\n        devolver fn (x )\n"
                "    devolver cuenta (fn ,x ,n - 1 )\n"
                "funcion guarda (fn ,obj ,ruta ):\n    f = abrir (ruta ,\"w\")\n    fn (obj ,f )\n    f .close ()\n"
                "imprimir (cuenta (json .dumps ,[1 ,2.5 ],3 ))\n"
                "guarda (json .dump ,{\"k\": [1 ,Verdadero ]},\"" + path + "\")\n"
                "imprimir (json .load (abrir (\"" + path + "\")))\n"
            )
            self.assertEqual((r.returncode, r.stdout), (0, "[1, 2.5]\n{'k': [1, True]}\n"))

    def test_unsupported_shapes_fail_closed(self):
        from piton.x86 import NativeBuildError
        shapes = [
            "funcion dev (fn ):\n    devolver fn\nf = dev (json .dumps )\n",
            "funcion alm (fn ):\n    l = [fn ]\n    devolver l\nalm (json .dumps )\n",
            "xs = [[1 ],[2 ]]\nr = lista (map (json .dumps ,xs ))\nimprimir (r )\n",
        ]
        for body in shapes:
            with tempfile.TemporaryDirectory(prefix="piton-jsp-") as directory:
                with self.assertRaises(NativeBuildError):
                    compile_native_linux("importar json\n" + body, Path(directory) / "p")


class DelUnboundNameV1(unittest.TestCase):
    """DEL_NAME_UNBOUND_V1: borrar un nombre no ligado levanta NameError
    capturable, igual que CPython."""

    def test_never_bound_is_catchable(self):
        r = _run('intentar:\n    borrar a\nexcepto NameError:\n    imprimir ("EXC")\nimprimir ("fin")\n')
        self.assertEqual((r.returncode, r.stdout), (0, "EXC\nfin\n"))

    def test_bound_then_deleted_then_deleted_again(self):
        r = _run('x = 1\nborrar x\nintentar:\n    borrar x\nexcepto NameError:\n    imprimir ("EXC2")\n')
        self.assertEqual((r.returncode, r.stdout), (0, "EXC2\n"))

    def test_linear_double_delete_uncaught_exits_1(self):
        r = _run("x = 1\nborrar x\nborrar x\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("NameError: name x is not defined", r.stderr)

    def test_conditional_delete_in_branches_is_not_an_error(self):
        r = _run('c = 1\nx = 1\nsi c == 1:\n    borrar x\nsino:\n    borrar x\nimprimir ("ok")\n')
        self.assertEqual((r.returncode, r.stdout), (0, "ok\n"))

    def test_failed_store_then_delete_is_nameerror(self):
        # `a = A(10)` con A indefinido falla antes del store: `borrar a` debe fallar
        r = _run(
            'intentar:\n    a = A (10 )\nexcepto BaseException:\n    imprimir ("EXC")\n'
            'intentar:\n    borrar a\nexcepto BaseException:\n    imprimir ("EXC2")\n'
        )
        self.assertEqual((r.returncode, r.stdout), (0, "EXC\nEXC2\n"))


class DelUnboundNameFnV1(unittest.TestCase):
    """DEL_NAME_FN_BOUND_V1: `borrar` de un local dentro de una funcion."""

    def test_function_scope_cases(self):
        r = _run(
            "funcion f ():\n"
            "    intentar:\n        borrar z\n    excepto NameError:\n        imprimir (\"EXC1\")\n"
            "    intentar:\n        a = A (10 )\n    excepto BaseException:\n        imprimir (\"EXC2\")\n"
            "    intentar:\n        borrar a\n    excepto NameError:\n        imprimir (\"EXC3\")\n"
            "    x = 1\n    borrar x\n"
            "    intentar:\n        borrar x\n    excepto NameError:\n        imprimir (\"EXC4\")\n"
            "f ()\n"
        )
        self.assertEqual((r.returncode, r.stdout), (0, "EXC1\nEXC2\nEXC3\nEXC4\n"))

    def test_branch_dependent_local_delete(self):
        r = _run(
            "funcion g (c ):\n    si c == 1:\n        x = 5\n"
            "    intentar:\n        borrar x\n    excepto NameError:\n        imprimir (\"EXC\")\n"
            "    imprimir (\"ok\")\ng (1 )\ng (0 )\n"
        )
        self.assertEqual((r.returncode, r.stdout), (0, "ok\nEXC\nok\n"))


class IntFromStrOverflowV1(unittest.TestCase):
    """INT_FROM_STR_OVERFLOW_V1: int(str) fuera de i64 falla ruidosamente
    (antes devolvia basura en silencio)."""

    def test_overflow_is_loud(self):
        r = _run('imprimir (int ("123456789012345678901234567890" ))\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("-4362896299872285998", r.stdout)
        self.assertIn("OverflowError", r.stderr)

    def test_i64_bounds_still_exact(self):
        r = _run('imprimir (int ("9223372036854775807" ))\nimprimir (int ("-9223372036854775807" ))\n')
        self.assertEqual((r.returncode, r.stdout), (0, "9223372036854775807\n-9223372036854775807\n"))


if __name__ == "__main__":
    unittest.main()
