from piton.native_differential import compare_native_to_cpython
import unittest


class TestPatternMatchingParity(unittest.TestCase):
    def test_match_literals_and_wildcard(self):
        source = """
funcion describe(val):
    segun val:
        caso 1:
            imprimir("one")
        caso 2:
            imprimir("two")
        caso _:
            imprimir("other")

describe(1)
describe(2)
describe(99)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_match_capture_variable(self):
        source = """
funcion process_val(x):
    segun x:
        caso 0:
            imprimir("zero")
        caso n:
            imprimir("captured", n * 2)

process_val(0)
process_val(21)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_match_guard_with_capture(self):
        source = """
funcion categorize(n):
    segun n:
        caso x si x < 0:
            imprimir("negative", x)
        caso x si x == 0:
            imprimir("zero")
        caso x si x > 10:
            imprimir("large", x)
        caso x:
            imprimir("small positive", x)

categorize(-5)
categorize(0)
categorize(7)
categorize(42)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_match_or_patterns(self):
        source = """
funcion is_weekend_code(code):
    segun code:
        caso 6 | 7:
            imprimir("weekend")
        caso 1 | 2 | 3 | 4 | 5:
            imprimir("weekday")
        caso _:
            imprimir("invalid")

is_weekend_code(1)
is_weekend_code(6)
is_weekend_code(7)
is_weekend_code(9)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_match_sequence_patterns(self):
        source = """
funcion handle_command(cmd):
    segun cmd:
        caso ["quit"]:
            imprimir("quitting")
        caso ["load", filename]:
            imprimir("loading", filename)
        caso ["move", x, y]:
            imprimir("moving to", x, y)
        caso _:
            imprimir("unknown")

handle_command(["quit"])
handle_command(["load", "data.txt"])
handle_command(["move", 10, 20])
handle_command(["move", 10])
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_match_nested_sequences(self):
        source = """
funcion unpack_matrix(m):
    segun m:
        caso [[a, b], [c, d]]:
            imprimir("2x2 matrix det:", a * d - b * c)
        caso _:
            imprimir("not 2x2")

unpack_matrix([[1, 2], [3, 4]])
unpack_matrix([[5, 0], [0, 5]])
unpack_matrix([1, 2, 3])
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_match_dict_patterns(self):
        source = """
funcion handle_event(ev):
    segun ev:
        caso {"type": "login", "user": u}:
            imprimir("login user", u)
        caso {"type": "click", "x": cx, "y": cy}:
            imprimir("click at", cx, cy)
        caso _:
            imprimir("unknown event")

handle_event({"type": "login", "user": "alice"})
handle_event({"type": "click", "x": 100, "y": 200})
handle_event({"type": "hover"})
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_match_type_patterns(self):
        source = """
funcion check_type(v):
    segun v:
        caso int():
            imprimir("is int", v)
        caso str():
            imprimir("is str", v)
        caso list():
            imprimir("is list")
        caso _:
            imprimir("other type")

check_type(42)
check_type("hello")
check_type([1, 2])
check_type(3.14)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_match_class_patterns(self):
        source = """
clase Point:
    funcion __init__(self, x, y):
        self.x = x
        self.y = y

funcion route_point(p):
    segun p:
        caso Point(x=0, y=0):
            imprimir("origin")
        caso Point(x=0, y=target_y):
            imprimir("y-axis at", target_y)
        caso Point(x=target_x, y=0):
            imprimir("x-axis at", target_x)
        caso Point(x=px, y=py):
            imprimir("point at", px, py)

route_point(Point(0, 0))
route_point(Point(0, 15))
route_point(Point(7, 0))
route_point(Point(3, 4))
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)


if __name__ == "__main__":
    unittest.main()
