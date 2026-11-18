"""Paridad de modulos de la stdlib implementados de forma nativa (heapq, gc, ...).

Cada caso lleva su version PITON y la version Python equivalente; la salida nativa se compara
byte a byte con la de CPython ejecutando la version Python (oraculo real, sin valores fijados).
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from piton.linux_x86 import compile_native_linux


def _native(source: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory(prefix="piton-stdlib-") as directory:
        exe = Path(directory) / "prog"
        compile_native_linux(source, exe)
        return subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)


def _oracle(py_source: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-c", py_source], capture_output=True, text=True, timeout=30)


class StdlibParityCase(unittest.TestCase):
    def assert_parity(self, piton_src: str, py_src: str):
        native, oracle = _native(piton_src), _oracle(py_src)
        self.assertEqual(oracle.returncode, 0, oracle.stderr)
        self.assertEqual(native.returncode, 0, native.stderr)
        self.assertEqual(native.stdout, oracle.stdout)


class HeapqNativeV1(StdlibParityCase):
    """HEAPQ_NATIVE_V1: mismo algoritmo que CPython (el orden del monticulo coincide)."""

    def test_tutorial_example(self):
        self.assert_parity(
            "desde heapq importar heapify, heappop, heappush\n"
            "data = [1 ,3 ,5 ,7 ,9 ,2 ,4 ,6 ,8 ,0 ]\nheapify (data )\nimprimir (data )\n"
            "heappush (data ,-5 )\nimprimir ([heappop (data )para i en rango (3 )])\nimprimir (data )\n",
            "from heapq import heapify, heappop, heappush\n"
            "data=[1,3,5,7,9,2,4,6,8,0]\nheapify(data)\nprint(data)\n"
            "heappush(data,-5)\nprint([heappop(data) for i in range(3)])\nprint(data)\n",
        )

    def test_push_pop_pushpop_replace_and_types(self):
        self.assert_parity(
            "importar heapq\nh = []\npara x en [5 ,3 ,8 ,1 ,9 ,2 ]:\n    heapq .heappush (h ,x )\n"
            "imprimir (h )\nimprimir (heapq .heappop (h ))\nimprimir (heapq .heappushpop (h ,0 ))\n"
            "imprimir (heapq .heapreplace (h ,7 ))\nimprimir (h )\n"
            "s = [\"pera\" ,\"manzana\" ,\"kiwi\" ]\nheapq .heapify (s )\nimprimir (s )\nimprimir (heapq .heappop (s ))\n"
            "v = [3.5 ,1 ,2.25 ]\nheapq .heapify (v )\nimprimir (v )\n",
            "import heapq\nh=[]\nfor x in [5,3,8,1,9,2]: heapq.heappush(h,x)\n"
            "print(h);print(heapq.heappop(h));print(heapq.heappushpop(h,0));print(heapq.heapreplace(h,7));print(h)\n"
            "s=['pera','manzana','kiwi'];heapq.heapify(s);print(s);print(heapq.heappop(s))\n"
            "v=[3.5,1,2.25];heapq.heapify(v);print(v)\n",
        )

    def test_errors(self):
        self.assert_parity(
            "importar heapq\nintentar:\n    heapq .heappop ([])\nexcepto IndexError:\n    imprimir (\"IE\")\n"
            "intentar:\n    heapq .heappush ([1 ],\"a\")\nexcepto TypeError:\n    imprimir (\"TE\")\n",
            "import heapq\ntry: heapq.heappop([])\nexcept IndexError: print('IE')\n"
            "try: heapq.heappush([1],'a')\nexcept TypeError: print('TE')\n",
        )


class GcNativeV1(StdlibParityCase):
    def test_enable_disable_collect(self):
        self.assert_parity(
            "importar gc\nimprimir (gc .isenabled ())\ngc .disable ()\nimprimir (gc .isenabled ())\n"
            "gc .enable ()\nimprimir (gc .isenabled ())\nimprimir (gc .collect ())\nimprimir (gc .get_threshold ())\n"
            "desde gc importar collect\nimprimir (collect ())\n",
            "import gc\nprint(gc.isenabled());gc.disable();print(gc.isenabled());gc.enable();print(gc.isenabled())\n"
            "print(gc.collect());print(gc.get_threshold())\nfrom gc import collect;print(collect())\n",
        )


class ZlibOsNativeV1(StdlibParityCase):
    """STDLIB_NATIVE_V1: zlib.crc32/adler32 exactos y os.getcwd."""

    def test_crc32_adler32_and_getcwd(self):
        self.assert_parity(
            "importar zlib\nimportar os\ns = b\"witch which has which witches wrist watch\"\n"
            "imprimir (zlib .crc32 (s ))\nimprimir (zlib .adler32 (s ))\nimprimir (zlib .crc32 (b\"\" ))\n"
            "imprimir (zlib .crc32 (b\"hello\" ,12345 ))\nimprimir (zlib .adler32 (b\"hello\" ,77 ))\n"
            "imprimir (os .getcwd ())\ndesde zlib importar crc32\nimprimir (crc32 (b\"abc\" ))\n"
            "intentar:\n    zlib .crc32 (\"texto\")\nexcepto TypeError:\n    imprimir (\"TE\")\n",
            "import zlib,os\ns=b'witch which has which witches wrist watch'\n"
            "print(zlib.crc32(s));print(zlib.adler32(s));print(zlib.crc32(b''));print(zlib.crc32(b'hello',12345))\n"
            "print(zlib.adler32(b'hello',77));print(os.getcwd())\nfrom zlib import crc32;print(crc32(b'abc'))\n"
            "try: zlib.crc32('texto')\nexcept TypeError: print('TE')\n",
        )

    def test_compress_decompress_round_trip(self):
        native = _native(
            "importar zlib\ns = b\"x\"\nt = zlib .compress (s )\n"
            "imprimir (zlib .decompress (t ) == s )\nimprimir (len (t ))\n"
        )
        self.assertEqual((native.returncode, native.stdout), (0, "True\n12\n"), native.stderr)


class StatisticsNativeV1(StdlibParityCase):
    """STATISTICS_NATIVE_V1: aritmetica exacta; mismo tipo (int/float) y mismos bits que CPython."""

    def test_tutorial_and_types(self):
        data = "[2.75 ,1.75 ,1.25 ,0.25 ,0.5 ,1.25 ,3.5 ]"
        pydata = "[2.75,1.75,1.25,0.25,0.5,1.25,3.5]"
        self.assert_parity(
            "importar statistics\nd = " + data + "\n"
            "imprimir (statistics .mean (d ))\nimprimir (statistics .median (d ))\nimprimir (statistics .variance (d ))\n"
            "imprimir (statistics .mean ([20 ,30 ,70 ]))\nimprimir (statistics .mean ([1 ,2 ]))\n"
            "imprimir (statistics .median ([1 ,3 ]))\nimprimir (statistics .median ([1 ,2 ,3 ]))\n"
            "imprimir (statistics .variance ([1 ,3 ]))\nimprimir (statistics .variance ([1 ,2 ,3 ,4 ,5 ]))\n"
            "imprimir (statistics .pvariance ([1 ,3 ]))\nimprimir (statistics .stdev ([1 ,2 ,3 ,4 ,5 ]))\n"
            "imprimir (statistics .pstdev ([1 ,2 ,3 ,4 ]))\nimprimir (statistics .median_low ([1 ,2 ,3 ,4 ]))\n"
            "imprimir (statistics .median_high ([1 ,2 ,3 ,4 ]))\nimprimir (statistics .mean ([Verdadero ,2 ]))\n",
            "import statistics as s\nd=" + pydata + "\n"
            "print(s.mean(d));print(s.median(d));print(s.variance(d));print(s.mean([20,30,70]));print(s.mean([1,2]))\n"
            "print(s.median([1,3]));print(s.median([1,2,3]));print(s.variance([1,3]));print(s.variance([1,2,3,4,5]))\n"
            "print(s.pvariance([1,3]));print(s.stdev([1,2,3,4,5]));print(s.pstdev([1,2,3,4]))\n"
            "print(s.median_low([1,2,3,4]));print(s.median_high([1,2,3,4]));print(s.mean([True,2]))\n",
        )

    def test_errors_are_valueerror(self):
        self.assert_parity(
            "importar statistics\n"
            "intentar:\n    statistics .mean ([])\nexcepto ValueError:\n    imprimir (\"VE1\")\n"
            "intentar:\n    statistics .variance ([1 ])\nexcepto ValueError:\n    imprimir (\"VE2\")\n"
            "intentar:\n    statistics .mean (5 )\nexcepto TypeError:\n    imprimir (\"TE1\")\n"
            "intentar:\n    statistics .mean ([\"a\"])\nexcepto TypeError:\n    imprimir (\"TE2\")\n",
            "import statistics as s\n"
            "try: s.mean([])\nexcept ValueError: print('VE1')\n"
            "try: s.variance([1])\nexcept ValueError: print('VE2')\n"
            "try: s.mean(5)\nexcept TypeError: print('TE1')\n"
            "try: s.mean(['a'])\nexcept TypeError: print('TE2')\n",
        )


class BisectHtmlKeywordNativeV1(StdlibParityCase):
    """BISECT/HTML/KEYWORD: implementaciones reales (no stubs): mismo resultado que CPython."""

    def test_bisect_and_insort(self):
        self.assert_parity(
            "importar bisect\na = [1 ,3 ,5 ,7 ]\nimprimir (bisect .bisect_left (a ,5 ))\n"
            "imprimir (bisect .bisect (a ,4 ))\nimprimir (bisect .bisect_right (a ,5 ))\n"
            "imprimir (bisect .bisect_left (a ,0 ))\nimprimir (bisect .bisect_left (a ,9 ))\n"
            "imprimir (bisect .bisect_left (a ,5 ,2 ,4 ))\nb = [1 ,3 ,5 ]\nbisect .insort (b ,4 )\n"
            "imprimir (b )\nbisect .insort_left (b ,3 )\nimprimir (b )\n",
            "import bisect\na=[1,3,5,7]\nprint(bisect.bisect_left(a,5));print(bisect.bisect(a,4))\n"
            "print(bisect.bisect_right(a,5));print(bisect.bisect_left(a,0));print(bisect.bisect_left(a,9))\n"
            "print(bisect.bisect_left(a,5,2,4))\nb=[1,3,5];bisect.insort(b,4);print(b)\n"
            "bisect.insort_left(b,3);print(b)\n",
        )

    def test_html_escape_and_keyword(self):
        self.assert_parity(
            "importar html\nimportar keyword\n"
            "imprimir (html .escape ('<a href=\"x\">Tom & \\'Jerry\\'</a>'))\n"
            "imprimir (html .escape ('<a href=\"x\">' ,quote =Falso ))\n"
            "imprimir (keyword .iskeyword ('if'),keyword .iskeyword ('match'),keyword .iskeyword ('foo'))\n"
            "imprimir (keyword .issoftkeyword ('match'),keyword .issoftkeyword ('if'))\n",
            "import html,keyword\n"
            "print(html.escape('<a href=\"x\">Tom & \\'Jerry\\'</a>'))\n"
            "print(html.escape('<a href=\"x\">',quote=False))\n"
            "print(keyword.iskeyword('if'),keyword.iskeyword('match'),keyword.iskeyword('foo'))\n"
            "print(keyword.issoftkeyword('match'),keyword.issoftkeyword('if'))\n",
        )


class SilentStubsFailClosedV1(StdlibParityCase):
    """Los stubs que devolvian un valor fijo en silencio (b\"\", 0, [], Falso, '') ahora fallan cerrado."""

    CALLS = [
        "pickle .dumps (1 )",
        # timeit.timeit fue implementado en 26898e8 (feat(stdlib): run literal timeit sources natively)
        "filecmp .cmp ('a' ,'b' )",
        "unicodedata .name ('a' )",
        "unicodedata .digit ('1' )",
        "select .select ([] ,[] ,[] )",
        "typing .get_origin (int )",
        "email .message_from_string ('')",
        "_struct .calcsize ('i' )",
        "tty .tcgetattr (0 )",
        "html .unescape ('&amp;' )",
    ]

    def test_each_call_raises_notimplemented_not_a_value(self):
        # control: una excepcion real de Python (ZeroDivisionError) sigue distinguiendose del stub
        blocks = "".join(
            f"intentar:\n    imprimir ({call } )\nexcepto NotImplementedError:\n    imprimir (\"NI\")\n"
            for call in self.CALLS
        ) + "intentar:\n    imprimir (1 /0 )\nexcepto ZeroDivisionError:\n    imprimir (\"ZD\")\n"
        modulos = ["pickle", "timeit", "filecmp", "unicodedata", "select", "typing",
                   "email", "_struct", "tty", "html"]
        native = _native("".join(f"importar {m }\n" for m in modulos) + blocks)
        self.assertEqual(native.returncode, 0, native.stderr)
        self.assertEqual(native.stdout, "NI\n" * len(self.CALLS) + "ZD\n", native.stdout)


class OrderingTypeErrorRoutedV1(StdlibParityCase):
    """EXC_ORDER_ROUTE_V1: un TypeError de comparacion de orden (tuple < int) es una excepcion
    capturable como en CPython: lo atrapa el intentar del mismo nivel o el de quien llama."""

    def test_caught_in_same_scope_and_across_call(self):
        self.assert_parity(
            "t = (1 ,'a')\nintentar:\n    imprimir (t < 2 )\nexcepto TypeError:\n    imprimir ('caught-a')\n"
            "imprimir ('after-a')\n"
            "funcion f (x ):\n    devolver x < 2\n"
            "intentar:\n    y = f ((1 ,'a'))\n    imprimir ('y', y)\nexcepto TypeError:\n    imprimir ('caught-c')\n"
            "imprimir ('after-c')\n",
            "t=(1,'a')\ntry:\n    print(t < 2)\nexcept TypeError:\n    print('caught-a')\n"
            "print('after-a')\n"
            "def f(x):\n    return x < 2\n"
            "try:\n    y = f((1,'a'))\n    print('y', y)\nexcept TypeError:\n    print('caught-c')\n"
            "print('after-c')\n",
        )


class ParamListIntAccumV1(StdlibParityCase):
    """Regresion: acumuladores enteros sobre parametros lista (s = 0; s = s + v). Un intento
    de etiquetar elementos desconocidos rompio esto (s mezclaba int y slot): sale basura."""

    def test_int_sum_by_iteration_and_index(self):
        self.assert_parity(
            "funcion total (a ):\n    s = 0\n    para v en a :\n        s = s + v\n    devolver s\n"
            "imprimir (total ([1 ,2 ,3 ]))\n"
            "funcion total2 (a ):\n    s = 0\n    para i en rango (len (a )):\n        s = s + a [i ]\n    devolver s\n"
            "imprimir (total2 ([1 ,2 ,3 ]))\n",
            "def total(a):\n    s=0\n    for v in a: s=s+v\n    return s\nprint(total([1,2,3]))\n"
            "def total2(a):\n    s=0\n    for i in range(len(a)): s=s+a[i]\n    return s\nprint(total2([1,2,3]))\n",
        )

    def test_float_and_bool_accumulator(self):
        # El acumulador arranca en float/bool y recibe elementos etiquetados:
        # el kind se decide en runtime (CPython: 0.0 + 1.5 == 1.5).
        self.assert_parity(
            "funcion total (a ):\n    s = 0.0\n    para v en a :\n        s = s + v\n    devolver s\n"
            "imprimir (total ([1.5 ,2.5 ]))\n"
            "funcion conteo (a ):\n    s = 0\n    para v en a :\n        s = s + 1\n    devolver s\n"
            "imprimir (conteo ([Verdadero ,Falso ]))\n",
            "def total(a):\n    s=0.0\n    for v in a: s=s+v\n    return s\nprint(total([1.5,2.5]))\n"
            "def count(a):\n    s=0\n    for v in a: s=s+1\n    return s\nprint(count([True,False]))\n",
        )


class ListParamElemKindV1(StdlibParityCase):
    """LIST_PARAM_UNKNOWN_ELEM_V1: el ELEMENTO de un parametro lista sin
    evidencia de tipo se lee ETIQUETADO (kind real float/bool/int/str). La
    coleccion sigue siendo el PitonSeq* crudo (envolverla era doble boxing
    -> SIGSEGV)."""

    def test_element_kinds_are_preserved(self):
        self.assert_parity(
            "funcion g (a ):\n    devolver a [0 ]\n"
            "imprimir (g ([1.5 ]))\n"
            "imprimir (g ([Verdadero ]))\n"
            "imprimir (g ([42 ]))\n"
            "imprimir (g ([\"hola\" ]))\n",
            "def g(a):\n    return a[0]\n"
            "print(g([1.5]))\n"
            "print(g([True]))\n"
            "print(g([42]))\n"
            "print(g(['hola']))\n",
        )

    def test_element_kind_follows_the_value_not_the_callee(self):
        # La MISMA funcion llamada con listas de tipos distintos: el kind del
        # elemento lo decide el VALOR (como en CPython).
        self.assert_parity(
            "funcion g (a ):\n    devolver a [0 ]\n"
            "imprimir (g ([1.5 ]))\n"
            "imprimir (g ([7 ]))\n"
            "imprimir (g ([Verdadero ]))\n",
            "def g(a):\n    return a[0]\n"
            "print(g([1.5]))\n"
            "print(g([7]))\n"
            "print(g([True]))\n",
        )


class SharedSpecializationV1(StdlibParityCase):
    """SHARED_SPECIALIZATION_V1: un param que recibe argumentos de tipos
    DISTINTOS en distintos call-sites se pasa como slot etiquetado (con el kind
    real de ESE argumento) -> la misma funcion compilada sirve para todos."""

    def test_param_with_two_types_across_call_sites(self):
        self.assert_parity(
            "funcion g (a ,x ):\n    devolver a [0 ]< x\n"
            "imprimir (g ([1 ,5 ],2 ))\n"
            "imprimir (g (['b'],'c' ))\n",
            "def g(a,x):\n    return a[0] < x\nprint(g([1,5],2))\nprint(g(['b'],'c'))\n",
        )

    def test_param_with_three_types_across_call_sites(self):
        self.assert_parity(
            "funcion g (a ,x ):\n    devolver a [0 ]< x\n"
            "imprimir (g ([1 ,5 ],2 ))\n"
            "imprimir (g (['b'],'c' ))\n"
            "imprimir (g ([1.5 ],2.5 ))\n",
            "def g(a,x):\n    return a[0] < x\nprint(g([1,5],2))\nprint(g(['b'],'c'))\nprint(g([1.5],2.5))\n",
        )

    def test_file_handle_param_is_not_wrapped_in_a_slot(self):
        # Un param que en un call-site recibe un handle de archivo y en otro un
        # int NO es un valor dinamico: envolverlo rompe la llamada
        # (json.load(fp) -> fp.read()).
        with tempfile.TemporaryDirectory(prefix="piton-fh-") as directory:
            path = str(Path(directory) / "x.json").replace("\\", "/")
            Path(path).write_text('{"z": [1, 2]}', encoding="utf-8")
            native = _native(
                "importar json\n"
                "funcion lee (fn ,arg ):\n    devolver fn (arg )\n"
                "l = json .load\n"
                "r = lee (l ,abrir (\"" + path + "\"))\n"
                "imprimir (r )\n"
                "imprimir (lee (json .loads ,\"[7]\"))\n"
            )
            oracle = _oracle(
                "import json\n"
                "def lee(fn,arg):\n    return fn(arg)\n"
                "l = json.load\n"
                "r = lee(l,open(\"" + path + "\"))\n"
                "print(r)\n"
                "print(lee(json.loads,\"[7]\"))\n"
            )
            self.assertEqual(native.returncode, 0, native.stderr)
            self.assertEqual(oracle.returncode, 0, oracle.stderr)
            self.assertEqual(native.stdout, oracle.stdout)


class TaggedStdlibConsumersV1(StdlibParityCase):
    """Los consumidores de la stdlib que reciben una lista de elementos
    desconocidos (itertools.filterfalse, functools.reduce, min/max con clave)
    deben comparar/consumir el ELEMENTO con su kind real, no el puntero."""

    def test_filterfalse_over_a_list_param(self):
        self.assert_parity(
            "importar itertools\n"
            "funcion es_par (n ):\n    devolver n % 2 == 0\n"
            "imprimir (list (itertools .filterfalse (es_par ,[1 ,2 ,3 ,4 ])))\n",
            "import itertools\n"
            "def es_par(n):\n    return n % 2 == 0\n"
            "print(list(itertools.filterfalse(es_par,[1,2,3,4])))\n",
        )

    def test_reduce_over_a_list_param(self):
        self.assert_parity(
            "importar functools\n"
            "funcion suma (a ,b ):\n    devolver a +b\n"
            "imprimir (functools .reduce (suma ,[1 ,2 ,3] ))\n",
            "import functools\n"
            "def suma(a,b):\n    return a+b\n"
            "print(functools.reduce(suma,[1,2,3]))\n",
        )

    def test_min_with_key_over_an_iterator_param(self):
        self.assert_parity(
            "funcion clave (v ):\n"
            "    devolver -v \n"
            "funcion ultimo (xs ):\n"
            "    it = iter (xs )\n"
            "    devolver min (it ,default =0 ,key =clave )\n"
            "imprimir (ultimo ([3 ,1 ,2 ]))\n",
            "def clave(v):\n    return -v\n"
            "def ultimo(xs):\n    it = iter(xs)\n    return min(it,default=0,key=clave)\n"
            "print(ultimo([3,1,2]))\n",
        )


class KnownGapsV1(StdlibParityCase):
    """Brechas conocidas (documentadas, no arregladas). Un expectedFailure que pasa a ser
    exito avisa de que la brecha se cerro y hay que quitar el marcador."""

    def test_list_param_float_element_is_tagged(self):
        # GAP_LIST_PARAM_ELEM_V1 (CERRADO 2026-11-12): un parametro lista sin
        # evidencia de tipo de elemento lee el elemento como int crudo: a[0] de
        # [1.5] salia como sus bits IEEE-754 (silencio).
        # LIST_PARAM_UNKNOWN_ELEM_V1: el ELEMENTO se lee como slot ETIQUETADO
        # (el kind real float/bool/int/str se decide en runtime, como CPython).
        # La COLECCION sigue siendo el PitonSeq* crudo (envolverla era doble
        # boxing -> SIGSEGV).
        self.assert_parity(
            "funcion g (a ):\n    devolver a [0 ]\nimprimir (g ([1.5 ]))\n",
            "def g(a):\n    return a[0]\nprint(g([1.5]))\n",
        )

    def test_one_function_called_with_two_element_kinds(self):
        # GAP_SHARED_SPECIALIZATION_V1 (CERRADO 2026-11-12): el backend compila
        # una sola version de la funcion; con llamadas int y str en el mismo
        # programa, la llamada int fallaba con un TypeError falso.
        # SHARED_SPECIALIZATION_V1: un param que recibe tipos DISTINTOS en
        # distintos call-sites se pasa como slot etiquetado (con el kind real de
        # ESE argumento) -> la misma funcion sirve para ambos casos.
        self.assert_parity(
            "funcion g (a ,x ):\n    devolver a [0 ]< x\nimprimir (g ([1 ,5 ],2 ))\nimprimir (g (['b'],'c' ))\n",
            "def g(a,x):\n    return a[0] < x\nprint(g([1,5],2))\nprint(g(['b'],'c'))\n",
        )

    def test_bisect_on_tuple_list(self):
        # GAP_BISECT_TUPLES_V1 (CERRADO 2026-11-12): el mismo fallo: el
        # elemento de `a` se especializaba como int. Con el elemento
        # etiquetado, la comparacion de tuplas la decide el runtime.
        self.assert_parity(
            "importar bisect\nprint (bisect .bisect_left ([(1 ,'a' )],(2 ,'b' )))\n",
            "import bisect\nprint(bisect.bisect_left([(1,'a')],(2,'b')))\n",
        )


if __name__ == "__main__":
    unittest.main()
