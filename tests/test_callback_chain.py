from __future__ import annotations

import unittest
from types import SimpleNamespace

from core import bridge as bridge_module
from core.bridge import InteractionBridge
from core.registry import InteractionRegistry
from main import is_interaction_bridge_active
from test_bridge import FakeAdapter, FakeClient, QuietLogger, fake_interaction


def marked_callback(platform_id="qq-1"):
    async def callback(_interaction):
        pass

    callback.__qqbotfix_bridge__ = True
    callback.__qqbotfix_platform_id__ = platform_id
    return callback


def wrap_with_defaults(callback):
    async def wrapper(interaction, original=callback):
        await original(interaction)

    return wrapper


def wrap_with_kwdefaults(callback):
    async def wrapper(interaction, *, original=callback):
        await original(interaction)

    return wrapper


def wrap_with_closure(callback):
    async def wrapper(interaction):
        await callback(interaction)

    return wrapper


def wrap_mixed(callback):
    return wrap_with_closure(wrap_with_kwdefaults(wrap_with_defaults(callback)))


WRAPPERS = (wrap_with_defaults, wrap_with_kwdefaults, wrap_with_closure)


class CallbackChainTests(unittest.TestCase):
    def assert_bridge(self, callback, platform_id, expected):
        self.assertEqual(
            bridge_module.callback_chain_has_bridge(callback, platform_id), expected
        )

    def test_requires_callable_with_both_matching_markers(self):
        callback = marked_callback()
        self.assert_bridge(callback, "qq-1", True)
        self.assert_bridge(callback, "qq-2", False)
        self.assert_bridge(None, "qq-1", False)
        self.assert_bridge(lambda: None, "qq-1", False)
        self.assert_bridge(
            SimpleNamespace(__qqbotfix_bridge__=True, __qqbotfix_platform_id__="qq-1"),
            "qq-1",
            False,
        )
        callback.__qqbotfix_bridge__ = False
        self.assert_bridge(callback, "qq-1", False)
        callback.__qqbotfix_bridge__ = 1
        self.assert_bridge(callback, "qq-1", False)
        callback.__qqbotfix_bridge__ = True
        del callback.__qqbotfix_platform_id__
        self.assert_bridge(callback, "qq-1", False)

    def test_follows_each_supported_capture(self):
        for wrap in WRAPPERS:
            with self.subTest(capture=wrap.__name__):
                callback = wrap(marked_callback())
                self.assert_bridge(callback, "qq-1", True)
                self.assert_bridge(callback, "qq-2", False)

    def test_follows_mixed_nested_wrappers(self):
        callback = marked_callback()
        for wrap in WRAPPERS:
            callback = wrap(callback)
        self.assert_bridge(callback, "qq-1", True)
        self.assert_bridge(callback, "qq-2", False)

    def test_keeps_searching_after_foreign_bridge_and_non_callable_captures(self):
        foreign = marked_callback("qq-2")
        original = marked_callback("qq-1")
        config = {"enabled": True}

        async def wrapper(_interaction, default=foreign, *, keyword=original):
            return config, default, keyword

        self.assert_bridge(wrapper, "qq-1", True)
        self.assert_bridge(wrapper, "qq-2", True)
        self.assert_bridge(wrapper, "qq-3", False)

    def test_handles_cycles_in_defaults(self):
        async def first(_interaction):
            pass

        async def second(_interaction):
            pass

        first.__defaults__ = (second,)
        second.__defaults__ = (first, marked_callback())
        self.assert_bridge(first, "qq-1", True)
        self.assert_bridge(first, "qq-2", False)

    def test_handles_self_referencing_closure(self):
        async def wrapper(_interaction):
            return wrapper

        self.assert_bridge(wrapper, "qq-1", False)

    def test_skips_empty_closure_cells(self):
        original = marked_callback()
        unavailable = None

        async def wrapper(_interaction):
            return original, unavailable

        del unavailable
        self.assert_bridge(wrapper, "qq-1", True)
        self.assert_bridge(wrapper, "qq-2", False)

    def test_handles_deep_wrapper_chains_without_recursion(self):
        callback = marked_callback()
        for _ in range(1500):
            callback = wrap_with_defaults(callback)
        self.assert_bridge(callback, "qq-1", True)
        self.assert_bridge(callback, "qq-2", False)


class WrappedBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_active_checks_accept_wrapped_bridge_and_dispatch(self):
        for wrap in (*WRAPPERS, wrap_mixed):
            with self.subTest(capture=wrap.__name__):
                registry = InteractionRegistry()
                client = FakeClient()
                adapter = FakeAdapter("qq_official_webhook", client)
                context = SimpleNamespace(
                    platform_manager=SimpleNamespace(platform_insts=[adapter])
                )
                handled = []
                registry.register(
                    platform_ids={"qq-1"},
                    button_data="route:test",
                    handler=lambda ctx: handled.append(ctx.platform_id),
                )
                bridge = InteractionBridge(registry, logger=QuietLogger())
                self.assertTrue(bridge.patch_adapter(adapter, {"qq-1"}))
                original = client.on_interaction_create
                client.on_interaction_create = wrap(original)

                self.assertFalse(
                    getattr(client.on_interaction_create, "__qqbotfix_bridge__", False)
                )
                self.assertTrue(bridge.is_active("qq-1"))
                self.assertTrue(is_interaction_bridge_active(context, "qq-1"))
                self.assertFalse(bridge.is_active("qq-2"))
                self.assertFalse(is_interaction_bridge_active(context, "qq-2"))
                await client.ws_dispatch("interaction_create", fake_interaction())
                self.assertEqual(handled, ["qq-1"])
                self.assertEqual(client.api.acks, [("iid-1", 0)])

    async def test_both_active_checks_reject_replaced_or_foreign_bridge(self):
        client = FakeClient()
        adapter = FakeAdapter("qq_official_webhook", client)
        context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[adapter])
        )
        bridge = InteractionBridge(InteractionRegistry(), logger=QuietLogger())
        self.assertTrue(bridge.patch_adapter(adapter, {"qq-1"}))

        for callback in (
            None,
            lambda _interaction: None,
            wrap_with_defaults(marked_callback("qq-2")),
        ):
            with self.subTest(callback=callback):
                client.on_interaction_create = callback
                self.assertFalse(bridge.is_active("qq-1"))
                self.assertFalse(is_interaction_bridge_active(context, "qq-1"))


if __name__ == "__main__":
    unittest.main()
