from __future__ import annotations

import unittest

from piton.object_runtime import (
    RuntimeDict,
    RuntimeList,
    RuntimeSet,
    RuntimeTuple,
    RuntimeValue,
    runtime_equal,
    runtime_hash,
    runtime_iter,
    runtime_slice,
    runtime_truthy,
    rt_bool,
    rt_int,
    rt_str,
)


class Phase6RuntimeModel(unittest.TestCase):
    def test_unicode_and_arbitrary_precision_int(self):
        text = rt_str("qué onda 🌵")
        number = rt_int(2**200)
        self.assertEqual(text.value, "qué onda 🌵")
        self.assertEqual(number.value, 2**200)
        self.assertTrue(runtime_truthy(text))

    def test_containers_and_iteration(self):
        values = RuntimeList([rt_int(1), rt_int(2), rt_int(3)])
        self.assertEqual([item.value for item in runtime_iter(values)], [1, 2, 3])
        self.assertFalse(runtime_truthy(RuntimeList()))
        self.assertEqual([item.value for item in runtime_slice(values, 1, None).items], [2, 3])

    def test_dict_set_and_hashing(self):
        mapping = RuntimeDict({"x": rt_int(1)})
        values = RuntimeSet({"x", "y"})
        self.assertEqual([item.value for item in runtime_iter(mapping)], ["x"])
        self.assertEqual(set(item.value for item in runtime_iter(values)), {"x", "y"})
        self.assertEqual(runtime_hash(rt_int(4)), hash(4))
        with self.assertRaises(TypeError):
            runtime_hash(RuntimeList())

    def test_equality_and_lifetime(self):
        left = RuntimeTuple((rt_bool(True), rt_str("x")))
        right = RuntimeTuple((rt_bool(True), rt_str("x")))
        self.assertTrue(runtime_equal(left, right))
        value = RuntimeValue("object", 1)
        value.incref()
        value.decref()
        value.decref()
        self.assertFalse(value.alive)


if __name__ == "__main__":
    unittest.main()
