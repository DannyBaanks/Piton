from __future__ import annotations

import unittest
from piton.native_differential import compare_native_to_cpython


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    diff = compare_native_to_cpython(source)
    testcase.assertEqual(
        diff.native.returncode,
        diff.oracle.returncode,
        f"returncode mismatch\nnative stderr: {diff.native.stderr!r}\noracle stderr: {diff.oracle.stderr!r}",
    )
    testcase.assertEqual(
        diff.native.stdout,
        diff.oracle.stdout,
        f"stdout mismatch\nnative stderr: {diff.native.stderr!r}\noracle stderr: {diff.oracle.stderr!r}",
    )


class TestBytesBytearray(unittest.TestCase):

    def test_literals_print_and_len(self):
        _assert_matches(self, """
b = b'abc'
ba = bytearray(b'xyz')
imprimir(b)
imprimir(ba)
imprimir(len(b))
imprimir(len(ba))
imprimir(b'')
imprimir(b'a\\x00\\xff\\n')
""")

    def test_truthiness_and_equality(self):
        _assert_matches(self, """
imprimir(bool(b''))
imprimir(bool(b'a'))
imprimir(bool(bytearray(b'')))
imprimir(b'ab' == b'ab')
imprimir(b'ab' == b'ac')
imprimir(b'ab' == bytearray(b'ab'))
""")

    def test_index_slice_iterate(self):
        _assert_matches(self, """
b = b'abcdef'
imprimir(b[1])
imprimir(b[-1])
imprimir(b[1:4])
imprimir(b[::2])
imprimir(b[::-1])
total = 0
para x en b:
    total = total + x
imprimir(total)
""")

    def test_contains(self):
        _assert_matches(self, """
imprimir(b'bc' en b'abcd')
imprimir(b'zz' en b'abcd')
imprimir(98 en b'abcd')
imprimir(120 en b'abcd')
""")

    def test_concat_repeat(self):
        _assert_matches(self, """
imprimir(b'ab' + b'cd')
imprimir(b'ab' * 3)
imprimir(bytearray(b'ab') + bytearray(b'cd'))
""")

    def test_search_methods(self):
        _assert_matches(self, """
b = b'hello world hello'
imprimir(b.count(b'hello'))
imprimir(b.find(b'world'))
imprimir(b.find(b'zzz'))
imprimir(b.rfind(b'hello'))
imprimir(b.index(b'o'))
imprimir(b.rindex(b'o'))
imprimir(b.startswith(b'hello'))
imprimir(b.endswith(b'hello'))
imprimir(b.endswith(b'x'))
""")

    def test_decode_hex(self):
        _assert_matches(self, """
imprimir(b'abc'.hex())
imprimir(b'abc'.decode())
imprimir(b'abc'.decode('utf-8'))
imprimir(bytes.fromhex('616263'))
""")

    def test_bytearray_mutation(self):
        _assert_matches(self, """
ba = bytearray(b'abc')
ba.append(100)
imprimir(ba)
ba.extend(b'ef')
imprimir(ba)
ba[0] = 65
imprimir(ba)
imprimir(len(ba))
""")

    def test_constructors(self):
        _assert_matches(self, """
imprimir(bytes(3))
imprimir(bytearray(2))
imprimir(bytes([65, 66, 67]))
imprimir(bytearray([1, 2, 3]))
imprimir(bytes(range(4)))
""")
    def test_repr_quote_selection_and_escapes(self):
        _assert_matches(self, """
imprimir(b"it's")
imprimir(b'say "hi"')
imprimir(b'both \\' and "')
imprimir(b'tab\\there')
imprimir(b'back\\\\slash')
imprimir(bytearray(b"it's"))
imprimir(b'\\x01\\x7f\\x80\\xfe')
""")

    def test_ordering_and_inequality(self):
        _assert_matches(self, """
imprimir(b'aa' < b'bb')
imprimir(b'ab' < b'a')
imprimir(b'a' < b'ab')
imprimir(b'b' >= b'b')
imprimir(b'a' != b'b')
imprimir(b'a' != bytearray(b'a'))
imprimir(b'' < b'a')
imprimir(bytearray(b'z') > b'a')
""")

    def test_not_in_and_empty(self):
        _assert_matches(self, """
imprimir(b'zz' no en b'abcd')
imprimir(b'' en b'abcd')
imprimir(97 no en b'abcd')
imprimir(b'abc' en bytearray(b'xabcx'))
""")

    def test_slice_edges_and_bytearray_slice(self):
        _assert_matches(self, """
b = b'abcdef'
imprimir(b[-3:])
imprimir(b[:-3])
imprimir(b[10:])
imprimir(b[4:2])
imprimir(b[-100:100])
imprimir(b[::-2])
ba = bytearray(b'abcdef')
imprimir(ba[1:3])
imprimir(ba[::2])
imprimir(len(ba[2:]))
""")

    def test_bytearray_empty_ops_and_bool(self):
        _assert_matches(self, """
ba = bytearray()
imprimir(len(ba))
imprimir(bool(ba))
ba.append(0)
imprimir(bool(ba))
imprimir(ba)
imprimir(ba == bytearray(b'\\x00'))
b = bytes()
imprimir(b)
imprimir(b + b'x')
imprimir(0 * b'xy')
imprimir(2 * b'xy')
""")

    def test_bytearray_extend_variants_and_negative_index(self):
        _assert_matches(self, """
ba = bytearray(b'ab')
ba.extend(bytearray(b'cd'))
ba.extend([101, 102])
imprimir(ba)
ba[-1] = 90
imprimir(ba)
imprimir(ba[-1])
imprimir(ba[0])
""")

    def test_utf8_and_decode_roundtrip(self):
        _assert_matches(self, """
s = bytes('hola', 'utf-8')
imprimir(s)
imprimir(s.decode('utf-8'))
imprimir(bytes('abc', 'ascii').hex())
imprimir(bytes.fromhex('de ad BE ef'))
imprimir(bytearray.fromhex('00ff'))
imprimir(bytes.fromhex(''))
""")

    def test_errors_match_cpython_exit_status(self):
        for body in (
            "b = b'abc'\nimprimir(b[5])\n",
            "ba = bytearray(b'abc')\nba.append(300)\n",
            "ba = bytearray(b'abc')\nba[0] = 256\n",
            "b = b'abc'\nimprimir(b.index(b'z'))\n",
        ):
            diff = compare_native_to_cpython(body)
            self.assertNotEqual(diff.oracle.returncode, 0, body)
            self.assertNotEqual(diff.native.returncode, 0, body)
            self.assertEqual(diff.native.stdout, diff.oracle.stdout, body)


if __name__ == "__main__":
    unittest.main()
