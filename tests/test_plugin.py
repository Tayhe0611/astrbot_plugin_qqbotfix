from __future__ import annotations

import unittest
from types import SimpleNamespace

from main import QQBotFix, is_channel_dm_push_active, is_interaction_bridge_active, register_button_handler
from test_avatar_patch import AVATAR_URL, FakeAvatarAdapter, fake_avatar_event
from test_bridge import FakeAdapter, FakeClient, fake_interaction
from test_channel_dm_push import FakeAdapter as PushAdapter, FakeClient as PushClient, FakeMessageChain, Plain, MessageType


class PluginLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_patches_only_configured_instance_and_routes_consumer(self) -> None:
        client1 = FakeClient()
        client2 = FakeClient()
        adapter1 = FakeAdapter("qq_official_webhook", client1, "qq-1")
        adapter2 = FakeAdapter("qq_official_webhook", client2, "qq-2")
        context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[adapter1, adapter2])
        )
        plugin = QQBotFix(context, {"managed_platform_ids": ["qq-1"]})
        handled = []

        async def handler(ctx):
            handled.append(ctx.platform_id)
            await ctx.ack_once(0)

        registration = register_button_handler(
            context,
            "consumer:test",
            handler,
            platform_ids={"qq-1"},
            ack_mode="manual",
        )
        await plugin.initialize()
        self.assertTrue(is_interaction_bridge_active(context, "qq-1"))
        self.assertFalse(is_interaction_bridge_active(context, "qq-2"))
        await client1.ws_dispatch(
            "interaction_create", fake_interaction(button_data="consumer:test")
        )
        self.assertEqual(client1.api.acks, [("iid-1", 0)])
        self.assertEqual(handled, ["qq-1"])

        await plugin.terminate()
        registration.unregister()
        self.assertFalse(is_interaction_bridge_active(context, "qq-1"))

    async def test_supports_legacy_platform_manager_attribute_and_mapping(self) -> None:
        client = FakeClient()
        adapter = FakeAdapter("qq_official_webhook", client, "qq-map")
        context = SimpleNamespace(
            platformmanager=SimpleNamespace(platforminsts={"qq-map": adapter})
        )
        plugin = QQBotFix(context, {"managed_platform_ids": ["qq-map"]})
        await plugin.initialize()
        self.assertTrue(is_interaction_bridge_active(context, "qq-map"))
        await plugin.terminate()

    async def test_channel_dm_push_is_opt_in_and_restored_on_terminate(self) -> None:
        client = PushClient()
        adapter = PushAdapter(client, "qq-push")
        context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[adapter])
        )

        default_plugin = QQBotFix(
            context, {"managed_platform_ids": ["qq-push"]}
        )
        await default_plugin.initialize()
        self.assertFalse(is_channel_dm_push_active(context, "qq-push"))
        await default_plugin.terminate()

        plugin = QQBotFix(
            context,
            {
                "managed_platform_ids": ["qq-push"],
                "channel_dm_push_enabled": True,
            },
        )
        await plugin.initialize()
        self.assertTrue(is_channel_dm_push_active(context, "qq-push"))

        await client.on_direct_message_create(
            SimpleNamespace(author=SimpleNamespace(id="member-1"), guild_id="dm-1")
        )
        session = SimpleNamespace(
            message_type=MessageType.FRIEND_MESSAGE, session_id="member-1"
        )
        await adapter.send_by_session(session, FakeMessageChain([Plain("probe")]))
        self.assertEqual(client.api.dms[0][0], "dm-1")

        await plugin.terminate()
        self.assertFalse(is_channel_dm_push_active(context, "qq-push"))
        self.assertFalse(
            getattr(adapter.send_by_session, "__qqbotfix_channel_dm_bridge__", False)
        )
    async def test_avatar_patch_lifecycle_covers_both_transports(self) -> None:
        for name in ("qq_official", "qq_official_webhook"):
            with self.subTest(name=name):
                adapter = FakeAvatarAdapter(name)
                adapter.client = FakeClient()
                unmanaged = FakeAvatarAdapter(name, "qq-other")
                unmanaged.client = FakeClient()
                context = SimpleNamespace(
                    platform_manager=SimpleNamespace(platform_insts=[adapter, unmanaged])
                )
                plugin = QQBotFix(context, {"managed_platform_ids": ["qq-1"]})
                await plugin.initialize()
                self.assertTrue(is_interaction_bridge_active(context, "qq-1"))
                wrapper = adapter.commit_event
                await plugin.on_platform_loaded()
                self.assertIs(adapter.commit_event, wrapper)
                event = fake_avatar_event()
                adapter.commit_event(event)
                self.assertEqual(event.message_obj.sender.avatar, AVATAR_URL)
                self.assertNotIn("commit_event", vars(unmanaged))
                await plugin.terminate()
                self.assertNotIn("commit_event", vars(adapter))
                event = fake_avatar_event()
                adapter.commit_event(event)
                self.assertFalse(hasattr(event.message_obj.sender, "avatar"))
                await plugin.initialize()
                event = fake_avatar_event()
                adapter.commit_event(event)
                self.assertEqual(event.message_obj.sender.avatar, AVATAR_URL)
                await plugin.terminate()

    async def test_avatar_patch_handles_late_platform_load(self) -> None:
        context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[])
        )
        plugin = QQBotFix(context, {"managed_platform_ids": ["qq-1"]})
        await plugin.initialize()
        adapter = FakeAvatarAdapter()
        adapter.client = FakeClient()
        context.platform_manager.platform_insts.append(adapter)
        await plugin.on_platform_loaded()
        event = fake_avatar_event()
        adapter.commit_event(event)
        self.assertEqual(event.message_obj.sender.avatar, AVATAR_URL)
        await plugin.terminate()
        self.assertNotIn("commit_event", vars(adapter))

    async def test_avatar_patch_disabled_and_managed_id_changes_restore_methods(self) -> None:
        adapter = FakeAvatarAdapter()
        adapter.client = FakeClient()
        context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[adapter])
        )
        config = {"managed_platform_ids": ["qq-1"], "avatar_patch_enabled": False}
        plugin = QQBotFix(context, config)
        await plugin.initialize()
        self.assertNotIn("commit_event", vars(adapter))
        self.assertTrue(is_interaction_bridge_active(context, "qq-1"))
        config["avatar_patch_enabled"] = True
        await plugin.on_platform_loaded()
        self.assertIn("commit_event", vars(adapter))
        config["managed_platform_ids"] = []
        await plugin.on_platform_loaded()
        self.assertNotIn("commit_event", vars(adapter))
        config["managed_platform_ids"] = ["qq-1"]
        await plugin.on_platform_loaded()
        self.assertIn("commit_event", vars(adapter))
        config["avatar_patch_enabled"] = False
        await plugin.on_platform_loaded()
        self.assertNotIn("commit_event", vars(adapter))
        config["avatar_patch_enabled"] = True
        await plugin.on_platform_loaded()
        self.assertIn("commit_event", vars(adapter))
        config["enabled"] = False
        await plugin.on_platform_loaded()
        self.assertNotIn("commit_event", vars(adapter))
        self.assertFalse(is_interaction_bridge_active(context, "qq-1"))
        await plugin.terminate()

    async def test_empty_managed_ids_fail_closed(self) -> None:
        client = FakeClient()
        adapter = FakeAdapter("qq_official", client, "qq-1")
        context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[adapter])
        )
        plugin = QQBotFix(context, {"managed_platform_ids": []})
        await plugin.initialize()
        self.assertFalse(is_interaction_bridge_active(context, "qq-1"))
        self.assertEqual(client.intents, 0)
        await plugin.terminate()


if __name__ == "__main__":
    unittest.main()
