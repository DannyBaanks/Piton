from __future__ import annotations

import unittest

from piton.call_runtime import (
    CallBindingError,
    Cell,
    Frame,
    PitonFunction,
    PitonArgVector,
    PitonKwVector,
    PitonGenerator,
    Signature,
    bind_call_vectors,
    with_runtime,
)


class Phase7Bootstrap(unittest.TestCase):
    def test_call_binding_defaults_varargs_kwargs(self):
        signature = Signature(
            positional=("a", "b"),
            defaults={"b": 2},
            keyword_only=("scale",),
            keyword_defaults={"scale": 1},
            vararg="rest",
            kwarg="options",
        )
        function = PitonFunction("f", signature, lambda frame: frame.locals)
        result = function(1, 3, 4, scale=2, mode="fast")
        self.assertEqual(result, {"a": 1, "b": 3, "rest": (4,), "scale": 2, "options": {"mode": "fast"}})
        with self.assertRaises(CallBindingError):
            function()

    def test_generic_call_vectors_preserve_binding(self):
        signature = Signature(
            positional=("base", "extra"),
            defaults={"extra": 2},
            keyword_only=("scale",),
            keyword_defaults={"scale": 1},
            vararg="rest",
            kwarg="options",
        )
        values = bind_call_vectors(
            "f",
            signature,
            PitonArgVector((3, 4, 5)),
            PitonKwVector((("scale", 10), ("mode", "fast"))),
        )
        self.assertEqual(
            values,
            {"base": 3, "extra": 4, "rest": (5,), "scale": 10, "options": {"mode": "fast"}},
        )

    def test_generic_call_vectors_reject_duplicate_and_unexpected(self):
        signature = Signature(positional=("base",))
        with self.assertRaisesRegex(CallBindingError, "multiple values"):
            bind_call_vectors("f", signature, PitonArgVector((1,)), PitonKwVector((("base", 2),)))
        with self.assertRaisesRegex(CallBindingError, "unexpected keyword"):
            bind_call_vectors("f", signature, PitonArgVector((1,)), PitonKwVector((("other", 2),)))

    def test_frames_cells_and_recursion_shape(self):
        frame = Frame("outer", closure={"count": Cell(1)})
        frame.set("count", 2)
        self.assertEqual(frame.get("count"), 2)

        def factorial(current: int) -> int:
            return 1 if current <= 1 else current * factorial(current - 1)

        self.assertEqual(factorial(6), 720)

    def test_context_manager_and_generator(self):
        events = []

        class Manager:
            def __enter__(self):
                events.append("enter")
                return "resource"

            def __exit__(self, exc_type, exc, tb):
                events.append("exit")
                return False

        self.assertEqual(with_runtime(Manager(), lambda value: value + "!") , "resource!")
        self.assertEqual(events, ["enter", "exit"])
        self.assertEqual(list(PitonGenerator.from_iterable([1, 2, 3])), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
