from __future__ import annotations

import importlib
import unittest
from types import SimpleNamespace

import core.registry as registry_module


class SharedRegistryTests(unittest.TestCase):
    def test_shared_registry_survives_module_reload(self) -> None:
        context = SimpleNamespace()
        registry_before = registry_module.get_shared_registry(context)
        handler = lambda _ctx: None
        registration = registry_before.register(
            platform_ids={"qq-1"}, button_data="survive:reload", handler=handler
        )
        reloaded = importlib.reload(registry_module)
        registry_after = reloaded.get_shared_registry(context)
        self.assertIs(registry_after, registry_before)
        self.assertIs(registry_after.resolve("qq-1", "survive:reload").handler, handler)
        registration.unregister()
        self.assertIsNone(registry_after.resolve("qq-1", "survive:reload"))


if __name__ == "__main__":
    unittest.main()
