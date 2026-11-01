from __future__ import annotations

import asyncio
import logging
import unittest
from types import SimpleNamespace

from core.bridge import InteractionBridge, INTERACTION_INTENT_FALLBACK
from core.registry import InteractionRegistry


class QuietLogger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class FakeAPI:
    def __init__(self) -> None:
        self.acks: list[tuple[str, int]] = []
        self.attempts = 0
        self.delay = 0.0
        self.error: Exception | None = None

    async def on_interaction_result(self, interaction_id: str, code: int) -> None:
        self.attempts += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        self.acks.append((interaction_id, code))


class FakeClient:
    def __init__(self, intents: int = 0) -> None:
        self.intents = intents
        self.api = FakeAPI()
        self._connection = None

    def ws_dispatch(self, event: str, *args, **kwargs):
        callback = getattr(self, "on_" + event, None)
        if callback is None:
            return None
        return asyncio.create_task(callback(*args, **kwargs))


class FakeAdapter:
    def __init__(self, name: str, client: FakeClient, platform_id: str = "qq-1") -> None:
        self._name = name
        self._platform_id = platform_id
        self.client = client

    def meta(self):
        return SimpleNamespace(name=self._name, id=self._platform_id)


def fake_interaction(
    *, interaction_id: str = "iid-1", button_data: str | None = "route:test"
):
    return SimpleNamespace(
        id=interaction_id,
        event_id="event-1",
        scene="group",
        guild_id=None,
        channel_id=None,
        user_openid=None,
        group_openid="group-openid",
        group_member_openid="member-openid",
        data=SimpleNamespace(
            resolved=SimpleNamespace(
                button_id="button-1",
                button_data=button_data,
                message_id="message-1",
            )
        ),
    )


class InteractionBridgeTests(unittest.IsolatedAsyncioTestCase):
    def make_bridge(self, registry, **kwargs):
        return InteractionBridge(registry, logger=QuietLogger(), **kwargs)

    async def test_auto_route_is_instance_scoped_and_acked_before_handler(self) -> None:
        registry = InteractionRegistry()
        client = FakeClient()
        adapter = FakeAdapter("qq_official", client)
        seen = []

        async def handler(ctx):
            seen.append((ctx.platform_id, ctx.button_data, ctx.acknowledged))

        registry.register(
            platform_ids={"qq-1"}, button_data="route:test", handler=handler
        )
        bridge = self.make_bridge(registry)
        self.assertTrue(bridge.patch_adapter(adapter, {"qq-1"}))
        self.assertTrue(client.intents & INTERACTION_INTENT_FALLBACK)

        task = client.ws_dispatch("interaction_create", fake_interaction())
        await task

        self.assertEqual(client.api.acks, [("iid-1", 0)])
        self.assertEqual(seen, [("qq-1", "route:test", True)])
        self.assertTrue(bridge.patch_adapter(adapter, {"qq-1"}))

    async def test_webhook_never_mutates_gateway_intents(self) -> None:
        registry = InteractionRegistry()
        client = FakeClient(intents=123)
        registry.register(
            platform_ids={"qq-1"}, button_data="route:test", handler=lambda _ctx: None
        )
        bridge = self.make_bridge(registry)
        self.assertTrue(
            bridge.patch_adapter(FakeAdapter("qq_official_webhook", client), {"qq-1"})
        )
        await client.ws_dispatch("interaction_create", fake_interaction())
        self.assertEqual(client.intents, 123)
        self.assertEqual(client.api.acks, [("iid-1", 0)])

    async def test_managed_instance_id_is_required(self) -> None:
        client = FakeClient()
        bridge = self.make_bridge(InteractionRegistry())
        self.assertFalse(bridge.patch_adapter(FakeAdapter("qq_official", client), set()))
        self.assertEqual(client.intents, 0)
        self.assertNotIn("on_interaction_create", vars(client))

    async def test_intent_is_not_rolled_back_if_callback_install_fails(self) -> None:
        class FailingClient(FakeClient):
            def __setattr__(self, name, value):
                if name == "on_interaction_create":
                    raise RuntimeError("read-only callback")
                super().__setattr__(name, value)

        client = FailingClient()
        bridge = self.make_bridge(InteractionRegistry())
        self.assertFalse(
            bridge.patch_adapter(FakeAdapter("qq_official", client), {"qq-1"})
        )
        self.assertTrue(client.intents & INTERACTION_INTENT_FALLBACK)

    async def test_same_client_cannot_be_claimed_by_two_platform_ids(self) -> None:
        client = FakeClient()
        bridge = self.make_bridge(InteractionRegistry())
        adapter1 = FakeAdapter("qq_official_webhook", client, "qq-1")
        adapter2 = FakeAdapter("qq_official_webhook", client, "qq-2")
        self.assertTrue(bridge.patch_adapter(adapter1, {"qq-1", "qq-2"}))
        self.assertFalse(bridge.patch_adapter(adapter2, {"qq-1", "qq-2"}))
        self.assertTrue(bridge.is_active("qq-1"))
        self.assertFalse(bridge.is_active("qq-2"))

    async def test_foreign_instance_callback_is_not_wrapped(self) -> None:
        client = FakeClient()

        async def foreign(_interaction):
            pass

        client.on_interaction_create = foreign
        bridge = self.make_bridge(InteractionRegistry())
        self.assertFalse(
            bridge.patch_adapter(FakeAdapter("qq_official", client), {"qq-1"})
        )
        self.assertIs(client.on_interaction_create, foreign)
        self.assertEqual(client.intents, 0)

    async def test_class_level_callback_is_wrapped_as_baseline(self) -> None:
        delivered = []

        class CoreClient(FakeClient):
            async def on_interaction_create(self, interaction):
                delivered.append(interaction.id)

        client = CoreClient()
        self.assertNotIn("on_interaction_create", vars(client))
        bridge = self.make_bridge(InteractionRegistry())
        self.assertTrue(
            bridge.patch_adapter(FakeAdapter("qq_official", client), {"qq-1"})
        )
        self.assertIn("on_interaction_create", vars(client))
        self.assertTrue(bridge.is_active("qq-1"))

    async def test_unmatched_interaction_chains_to_baseline_callback(self) -> None:
        delivered = []

        class CoreClient(FakeClient):
            async def on_interaction_create(self, interaction):
                delivered.append(interaction.id)

        client = CoreClient()
        bridge = self.make_bridge(InteractionRegistry())
        self.assertTrue(
            bridge.patch_adapter(FakeAdapter("qq_official_webhook", client), {"qq-1"})
        )
        task = client.ws_dispatch(
            "interaction_create", fake_interaction(button_data="other")
        )
        await task
        self.assertEqual(delivered, ["iid-1"])
        self.assertEqual(client.api.acks, [])

    async def test_claimed_interaction_is_not_chained_to_baseline(self) -> None:
        delivered = []
        handled = []

        class CoreClient(FakeClient):
            async def on_interaction_create(self, interaction):
                delivered.append(interaction.id)

        registry = InteractionRegistry()
        registry.register(
            platform_ids={"qq-1"},
            button_data="route:test",
            handler=lambda ctx: handled.append(ctx.platform_id),
        )
        client = CoreClient()
        bridge = self.make_bridge(registry)
        self.assertTrue(
            bridge.patch_adapter(FakeAdapter("qq_official_webhook", client), {"qq-1"})
        )
        await client.ws_dispatch("interaction_create", fake_interaction())
        self.assertEqual(handled, ["qq-1"])
        self.assertEqual(client.api.acks, [("iid-1", 0)])
        self.assertEqual(delivered, [])

    async def test_stale_bridge_in_instance_dict_is_replaced_not_chained(self) -> None:
        calls = []

        async def stale(interaction):
            calls.append(interaction)

        stale.__qqbotfix_bridge__ = True
        stale.__qqbotfix_platform_id__ = "qq-1"

        client = FakeClient()
        client.on_interaction_create = stale
        bridge = self.make_bridge(InteractionRegistry())
        self.assertTrue(
            bridge.patch_adapter(FakeAdapter("qq_official_webhook", client), {"qq-1"})
        )
        self.assertIsNot(client.on_interaction_create, stale)
        await client.ws_dispatch("interaction_create", fake_interaction(button_data="other"))
        self.assertEqual(calls, [])

        bridge.unpatch_all()
        self.assertNotIn("on_interaction_create", vars(client))

    async def test_unmatched_interaction_is_not_acked_or_delegated(self) -> None:
        client = FakeClient()
        bridge = self.make_bridge(InteractionRegistry())
        self.assertTrue(
            bridge.patch_adapter(FakeAdapter("qq_official_webhook", client), {"qq-1"})
        )
        await client.ws_dispatch("interaction_create", fake_interaction(button_data="other"))
        self.assertEqual(client.api.acks, [])

    async def test_manual_handler_controls_single_ack_code(self) -> None:
        registry = InteractionRegistry()
        client = FakeClient()

        async def handler(ctx):
            self.assertTrue(await ctx.ack_once(4))
            self.assertFalse(await ctx.ack_once(0))

        registry.register(
            platform_ids={"qq-1"},
            button_data_prefix="route:",
            handler=handler,
            ack_mode="manual",
        )
        bridge = self.make_bridge(registry)
        bridge.patch_adapter(FakeAdapter("qq_official_webhook", client), {"qq-1"})
        await client.ws_dispatch("interaction_create", fake_interaction())
        self.assertEqual(client.api.acks, [("iid-1", 4)])

    async def test_manual_handler_without_ack_gets_fallback(self) -> None:
        registry = InteractionRegistry()
        client = FakeClient()
        registry.register(
            platform_ids={"qq-1"},
            button_data="route:test",
            handler=lambda _ctx: None,
            ack_mode="manual",
            fallback_ack_code=1,
        )
        bridge = self.make_bridge(registry)
        bridge.patch_adapter(FakeAdapter("qq_official_webhook", client), {"qq-1"})
        await client.ws_dispatch("interaction_create", fake_interaction())
        self.assertEqual(client.api.acks, [("iid-1", 1)])

    async def test_manual_ack_deadline_falls_back_and_cancels_handler(self) -> None:
        registry = InteractionRegistry()
        client = FakeClient()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def hanging_handler(_ctx):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        registry.register(
            platform_ids={"qq-1"},
            button_data="route:test",
            handler=hanging_handler,
            ack_mode="manual",
            ack_deadline=0.03,
            handler_timeout=1.0,
            fallback_ack_code=1,
        )
        bridge = self.make_bridge(registry)
        bridge.patch_adapter(FakeAdapter("qq_official_webhook", client), {"qq-1"})
        await client.ws_dispatch("interaction_create", fake_interaction())
        self.assertTrue(started.is_set())
        self.assertTrue(cancelled.is_set())
        self.assertEqual(client.api.acks, [("iid-1", 1)])

    async def test_handler_exception_before_manual_ack_gets_fallback(self) -> None:
        registry = InteractionRegistry()
        client = FakeClient()

        async def fail(_ctx):
            raise RuntimeError("before ACK")

        registry.register(
            platform_ids={"qq-1"},
            button_data="route:test",
            handler=fail,
            ack_mode="manual",
        )
        bridge = self.make_bridge(registry)
        bridge.patch_adapter(FakeAdapter("qq_official_webhook", client), {"qq-1"})
        await client.ws_dispatch("interaction_create", fake_interaction())
        self.assertEqual(client.api.acks, [("iid-1", 1)])

    async def test_handler_exception_after_manual_ack_does_not_ack_twice(self) -> None:
        registry = InteractionRegistry()
        client = FakeClient()

        async def fail_after_ack(ctx):
            await ctx.ack_once(0)
            raise RuntimeError("after ACK")

        registry.register(
            platform_ids={"qq-1"},
            button_data="route:test",
            handler=fail_after_ack,
            ack_mode="manual",
        )
        bridge = self.make_bridge(registry)
        bridge.patch_adapter(FakeAdapter("qq_official_webhook", client), {"qq-1"})
        await client.ws_dispatch("interaction_create", fake_interaction())
        self.assertEqual(client.api.acks, [("iid-1", 0)])

    async def test_failed_ack_is_not_retried(self) -> None:
        registry = InteractionRegistry()
        client = FakeClient()
        client.api.error = RuntimeError("network failure")

        async def handler(ctx):
            with self.assertRaises(RuntimeError):
                await ctx.ack_once(0)

        registry.register(
            platform_ids={"qq-1"},
            button_data="route:test",
            handler=handler,
            ack_mode="manual",
        )
        bridge = self.make_bridge(registry)
        bridge.patch_adapter(FakeAdapter("qq_official_webhook", client), {"qq-1"})
        await client.ws_dispatch("interaction_create", fake_interaction())
        self.assertEqual(client.api.attempts, 1)

    async def test_consumer_owns_duplicate_detection_and_ack_codes(self) -> None:
        registry = InteractionRegistry()
        client = FakeClient()
        seen_ids = set()
        handled = []

        async def consumer_dedupe(ctx):
            if ctx.interaction_id in seen_ids:
                await ctx.ack_once(3)
                return
            seen_ids.add(ctx.interaction_id)
            handled.append(ctx.interaction_id)
            await ctx.ack_once(0)

        registry.register(
            platform_ids={"qq-1"},
            button_data="route:test",
            handler=consumer_dedupe,
            ack_mode="manual",
        )
        bridge = self.make_bridge(registry)
        bridge.patch_adapter(FakeAdapter("qq_official_webhook", client), {"qq-1"})
        await client.ws_dispatch("interaction_create", fake_interaction())
        await client.ws_dispatch("interaction_create", fake_interaction())
        self.assertEqual(handled, ["iid-1"])
        self.assertEqual(client.api.acks, [("iid-1", 0), ("iid-1", 3)])

    async def test_unpatch_restores_callback_but_keeps_intent_bit(self) -> None:
        registry = InteractionRegistry()
        client = FakeClient()
        registry.register(
            platform_ids={"qq-1"}, button_data="route:test", handler=lambda _ctx: None
        )
        bridge = self.make_bridge(registry)
        bridge.patch_adapter(FakeAdapter("qq_official", client), {"qq-1"})
        bridge.unpatch_all()
        self.assertNotIn("on_interaction_create", vars(client))
        self.assertTrue(client.intents & INTERACTION_INTENT_FALLBACK)


class RegistryTests(unittest.TestCase):
    def test_explicit_ids_required_and_normalized(self) -> None:
        registry = InteractionRegistry()
        with self.assertRaises(ValueError):
            registry.register(platform_ids=[], button_data="a", handler=lambda _ctx: None)
        token = registry.register(
            platform_ids=" qq-1, qq-2 ",
            button_data_prefix="cs:",
            handler=lambda _ctx: None,
            ack_mode="manual",
        )
        self.assertEqual(token.platform_ids, frozenset({"qq-1", "qq-2"}))

    def test_exact_match_precedes_longest_prefix_and_scopes_instances(self) -> None:
        registry = InteractionRegistry()
        broad = lambda _ctx: "broad"
        narrow = lambda _ctx: "narrow"
        exact = lambda _ctx: "exact"
        registry.register(
            platform_ids={"qq-1"}, button_data_prefix="cs:", handler=broad
        )
        registry.register(
            platform_ids={"qq-1"}, button_data_prefix="cs:menu:", handler=narrow
        )
        registry.register(
            platform_ids={"qq-1"}, button_data="cs:menu:main", handler=exact
        )
        self.assertIs(registry.resolve("qq-1", "cs:menu:main").handler, exact)
        self.assertIs(registry.resolve("qq-1", "cs:menu:other").handler, narrow)
        self.assertIsNone(registry.resolve("qq-2", "cs:menu:main"))

    def test_invalid_timeouts_and_ack_codes_are_rejected(self) -> None:
        registry = InteractionRegistry()
        with self.assertRaises(ValueError):
            registry.register(
                platform_ids={"qq-1"},
                button_data="bad-timeout",
                handler=lambda _ctx: None,
                ack_deadline=float("nan"),
            )
        with self.assertRaises(ValueError):
            registry.register(
                platform_ids={"qq-1"},
                button_data="bad-code",
                handler=lambda _ctx: None,
                fallback_ack_code=True,
            )

    def test_duplicate_overlapping_subscription_rejected(self) -> None:
        registry = InteractionRegistry()
        registry.register(
            platform_ids={"qq-1"}, button_data="same", handler=lambda _ctx: None
        )
        with self.assertRaises(ValueError):
            registry.register(
                platform_ids={"qq-1", "qq-2"},
                button_data="same",
                handler=lambda _ctx: None,
            )


if __name__ == "__main__":
    unittest.main()
