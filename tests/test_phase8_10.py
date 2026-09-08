from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from piton.async_runtime import CancellationToken, PitonCancellation, async_collect, async_with
from piton.module_runtime import PitonModuleCache
from piton.object_protocol import Descriptor, PitonClass, PitonInstance, PitonSuper, Property


class Phase8To10Bootstrap(unittest.TestCase):
    def test_classes_binding_mro_super_and_descriptor(self):
        descriptor = Descriptor("value")

        def base_message(self):
            return "base"

        def child_message(self):
            return PitonSuper(child, self).message() + " child"

        base = PitonClass("Base", namespace={"message": base_message, "value": descriptor})
        child = PitonClass("Child", (base,), {"message": child_message})
        instance = child()
        instance.value = 7
        self.assertEqual(instance.message(), "base child")
        self.assertEqual(instance.value, 7)
        self.assertEqual(child.__mro__, (child, base))

    def test_property_and_method_descriptors(self):
        def get_name(self):
            return self._name

        def set_name(self, value):
            self._name = value

        cls = PitonClass("User", namespace={"name": Property(get_name, set_name)})
        user = cls()
        user.name = "Danny"
        self.assertEqual(user.name, "Danny")

    def test_module_cache_and_invalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "modulo.piton"
            path.write_text("valor = 3\n", encoding="utf-8")
            cache = PitonModuleCache()
            first = cache.load("modulo", path)
            second = cache.load("modulo", path)
            self.assertIs(first, second)
            self.assertEqual(first.namespace["valor"], 3)
            cache.invalidate("modulo")
            self.assertNotIn("modulo", cache.modules)

    def test_async_iteration_context_and_cancellation(self):
        async def values():
            for value in (1, 2, 3):
                yield value

        class Manager:
            async def __aenter__(self):
                return "resource"

            async def __aexit__(self, exc_type, exc, tb):
                return False

        async def scenario():
            collected = await async_collect(values())
            used = await async_with(Manager(), lambda resource: asyncio.sleep(0, result=resource))
            return collected, used

        self.assertEqual(asyncio.run(scenario()), ([1, 2, 3], "resource"))
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(PitonCancellation):
            token.check()


if __name__ == "__main__":
    unittest.main()
