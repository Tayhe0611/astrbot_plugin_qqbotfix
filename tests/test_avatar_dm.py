from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass
from functools import wraps
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.avatar_patch import SenderAvatarPatch
from main import QQBotFix, is_channel_dm_push_active
from test_avatar_patch import AVATAR_URL, FakePlatform, fake_avatar_event
from test_channel_dm_push import (
    FakeAPI as PushAPI, FakeAdapter as PushAdapter, FakeClient as PushClient,
    FakeMessageChain, MessageType, Plain,
)


def field(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def direct_message(*, guild_id="source-guild", user_id="member-1", avatar=None):
    author = SimpleNamespace(id=user_id, avatar=avatar)
    return SimpleNamespace(
        src_guild_id=guild_id,
        guild_id="dm-session-1",
        author=author,
        raw_data={"src_guild_id": guild_id, "author": {"id": user_id, "avatar": avatar}},
    )


class MemberAPI(PushAPI):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.avatar = AVATAR_URL
        self.error = None
        self.release = None
        self.started = asyncio.Event()
        self.object_response = False

    async def get_guild_member(self, guild_id, user_id):
        self.calls.append((guild_id, user_id))
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        user = {"id": user_id, "avatar": self.avatar}
        return SimpleNamespace(user=SimpleNamespace(**user)) if self.object_response else {"user": user}


class ParsingClient(PushClient):
    def __init__(self):
        super().__init__()
        self.api = MemberAPI()
        self.avatars_at_parse = []
        self.platform = None

    async def on_direct_message_create(self, message, *args, **kwargs):
        self.avatars_at_parse.append(field(field(message, "author"), "avatar"))
        self.direct_messages.append(message)
        event = fake_avatar_event(raw_message=message)
        return self.platform.commit_event(event, *args, **kwargs)


class AvatarPushAdapter(PushAdapter):
    commit_event = FakePlatform.commit_event

    def __init__(self, name="qq_official", platform_id="qq-dm"):
        client = ParsingClient()
        super().__init__(client, platform_id, name)
        client.platform = self
        self.events = []
        self.avatars_at_commit = []
        self.commit_args = []


class DirectMessageAvatarTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.logger = Mock()
        self.patch = SenderAvatarPatch(logger=self.logger)
        self.addCleanup(self.patch.unpatch_all)
        self.now = 1000.0
        self.patch._clock = lambda: self.now
        self.adapter = AvatarPushAdapter()
        self.client = self.adapter.client
        self.api = self.client.api
        self.assertTrue(self.patch.patch_adapter(self.adapter, {"qq-dm"}))

    async def test_lookup_finishes_before_parsing_and_updates_raw_and_sender(self):
        message = direct_message()
        message.raw_data["other"] = "preserved"
        self.assertEqual(await self.client.on_direct_message_create(message), "queued")
        self.assertEqual(self.api.calls, [("source-guild", "member-1")])
        self.assertEqual(message.author.avatar, AVATAR_URL)
        self.assertEqual(message.raw_data["author"]["avatar"], AVATAR_URL)
        self.assertEqual(message.raw_data["author"]["id"], "member-1")
        self.assertEqual(message.raw_data["other"], "preserved")
        self.assertEqual(self.client.avatars_at_parse, [AVATAR_URL])
        self.assertEqual(self.adapter.avatars_at_commit, [AVATAR_URL])
        self.assertIs(self.adapter.events[0].message_obj.raw_message, message)

    async def test_existing_avatar_skips_member_query(self):
        message = direct_message(avatar="https://example.invalid/existing.png")
        await self.client.on_direct_message_create(message)
        self.assertEqual(self.api.calls, [])
        self.assertEqual(message.author.avatar, "https://example.invalid/existing.png")

    async def test_raw_avatar_can_be_used_without_network(self):
        message = direct_message()
        message.raw_data["author"]["avatar"] = AVATAR_URL
        await self.client.on_direct_message_create(message)
        self.assertEqual(self.api.calls, [])
        self.assertEqual(message.author.avatar, AVATAR_URL)

    async def test_success_cache_is_guild_user_and_instance_scoped(self):
        for _ in range(2):
            await self.client.on_direct_message_create(direct_message())
        await self.client.on_direct_message_create(direct_message(guild_id="another-guild"))
        await self.client.on_direct_message_create(direct_message(user_id="another-member"))
        other = AvatarPushAdapter(platform_id="qq-other")
        self.patch.patch_adapter(other, {"qq-other"})
        await other.client.on_direct_message_create(direct_message())
        self.assertEqual(len(self.api.calls), 3)
        self.assertEqual(len(other.client.api.calls), 1)
        self.assertEqual(self.adapter.avatars_at_commit, [AVATAR_URL] * 4)

    async def test_success_cache_expires(self):
        await self.client.on_direct_message_create(direct_message())
        self.now += 3601
        self.api.avatar = "https://example.invalid/new.png"
        message = direct_message()
        await self.client.on_direct_message_create(message)
        self.assertEqual(len(self.api.calls), 2)
        self.assertEqual(message.author.avatar, self.api.avatar)

    async def test_cache_has_an_lru_capacity_limit(self):
        with patch("core.avatar_patch.CACHE_MAX_ENTRIES", 2):
            for user in ("a", "b", "a", "c", "b"):
                await self.client.on_direct_message_create(direct_message(user_id=user))
            self.assertEqual([user for _, user in self.api.calls], ["a", "b", "c", "b"])
            direct = self.patch._patches[id(self.adapter)].direct
            self.assertEqual(len(direct.cache), 2)

    async def test_failures_are_negative_cached_and_original_delivery_continues(self):
        self.api.error = PermissionError("not allowed")
        for _ in range(2):
            await self.client.on_direct_message_create(direct_message())
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.client.avatars_at_parse, [None, None])
        self.assertEqual(len(self.adapter.events), 2)
        self.logger.warning.assert_called_once()
        self.now += 61
        self.api.error = None
        message = direct_message()
        await self.client.on_direct_message_create(message)
        self.assertEqual(len(self.api.calls), 2)
        self.assertEqual(message.author.avatar, AVATAR_URL)

    async def test_empty_member_avatar_is_negative_cached(self):
        self.api.avatar = None
        for _ in range(2):
            await self.client.on_direct_message_create(direct_message())
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.adapter.avatars_at_commit, [None, None])

    async def test_member_query_timeout_does_not_drop_message(self):
        self.api.release = asyncio.Event()
        with patch("core.avatar_patch.LOOKUP_TIMEOUT", 0.01):
            await self.client.on_direct_message_create(direct_message())
        self.assertEqual(self.client.avatars_at_parse, [None])
        self.assertEqual(len(self.adapter.events), 1)
        self.logger.warning.assert_called_once()

    async def test_concurrent_messages_share_one_request(self):
        self.api.release = asyncio.Event()
        messages = [direct_message() for _ in range(5)]
        tasks = [asyncio.create_task(self.client.on_direct_message_create(msg)) for msg in messages]
        await asyncio.wait_for(self.api.started.wait(), 1)
        self.api.release.set()
        await asyncio.gather(*tasks)
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.client.avatars_at_parse, [AVATAR_URL] * 5)

    async def test_cancelling_one_waiter_does_not_cancel_shared_request(self):
        self.api.release = asyncio.Event()
        first = asyncio.create_task(self.client.on_direct_message_create(direct_message()))
        second = asyncio.create_task(self.client.on_direct_message_create(direct_message()))
        await asyncio.wait_for(self.api.started.wait(), 1)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.api.release.set()
        await second
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.client.avatars_at_parse, [AVATAR_URL])

    async def test_inflight_limit_skips_extra_lookups_without_blocking_delivery(self):
        self.api.release = asyncio.Event()
        with patch("core.avatar_patch.MAX_INFLIGHT_LOOKUPS", 1):
            first = asyncio.create_task(self.client.on_direct_message_create(direct_message()))
            await asyncio.wait_for(self.api.started.wait(), 1)
            await self.client.on_direct_message_create(direct_message(user_id="other"))
            self.assertEqual(self.client.avatars_at_parse, [None])
            self.api.release.set()
            await first
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.client.avatars_at_parse, [None, AVATAR_URL])

    async def test_unload_cancels_lookup_but_keeps_the_waiting_message(self):
        self.api.release = asyncio.Event()
        task = asyncio.create_task(self.client.on_direct_message_create(direct_message()))
        await asyncio.wait_for(self.api.started.wait(), 1)
        direct = self.patch._patches[id(self.adapter)].direct
        self.patch.unpatch_all()
        self.assertEqual(await asyncio.wait_for(task, 1), "queued")
        self.assertEqual(self.client.avatars_at_parse, [None])
        self.assertNotIn("on_direct_message_create", vars(self.client))
        self.assertNotIn("commit_event", vars(self.adapter))
        self.assertEqual(len(direct.cache), 0)
        self.assertEqual(len(direct.inflight), 0)

    async def test_missing_source_or_user_never_uses_dm_session_as_guild(self):
        for guild_id, user_id in ((None, "member-1"), ("", "member-1"), ("source", None), ("source", False)):
            await self.client.on_direct_message_create(direct_message(guild_id=guild_id, user_id=user_id))
        self.assertEqual(self.api.calls, [])
        self.assertEqual(len(self.adapter.events), 4)

    async def test_raw_data_source_and_user_id_fallback(self):
        message = direct_message()
        del message.src_guild_id
        del message.author.id
        await self.client.on_direct_message_create(message)
        self.assertEqual(self.api.calls, [("source-guild", "member-1")])

    async def test_missing_api_or_method_is_a_noop_and_late_api_is_supported(self):
        self.client.api = None
        await self.client.on_direct_message_create(direct_message())
        self.client.api = SimpleNamespace()
        await self.client.on_direct_message_create(direct_message())
        self.client.api = self.api
        await self.client.on_direct_message_create(direct_message())
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.client.avatars_at_parse, [None, None, AVATAR_URL])

    async def test_message_api_fallback_and_object_member_response(self):
        self.client.api = None
        self.api.object_response = True
        message = direct_message()
        message._api = self.api
        await self.client.on_direct_message_create(message)
        self.assertEqual(message.author.avatar, AVATAR_URL)

    async def test_no_raw_data_is_required(self):
        message = direct_message()
        del message.raw_data
        await self.client.on_direct_message_create(message)
        self.assertEqual(message.author.avatar, AVATAR_URL)
        self.assertEqual(self.adapter.avatars_at_commit, [AVATAR_URL])

    async def test_mapping_message_and_author_are_supported(self):
        message = {"src_guild_id": "guild", "author": {"id": "member"}, "raw_data": {}}
        await self.client.on_direct_message_create(message)
        self.assertEqual(self.api.calls, [("guild", "member")])
        self.assertEqual(message["author"]["avatar"], AVATAR_URL)
        self.assertEqual(message["raw_data"]["author"]["avatar"], AVATAR_URL)

    async def test_readonly_author_does_not_break_original_callback(self):
        @dataclass(frozen=True)
        class Author:
            id: str = "member-1"
            avatar: str | None = None

        message = direct_message()
        message.author = Author()
        await self.client.on_direct_message_create(message)
        self.assertEqual(self.client.avatars_at_parse, [None])
        self.assertEqual(len(self.adapter.events), 1)
        self.logger.warning.assert_called_once()

    async def test_original_callback_return_arguments_and_errors_are_preserved(self):
        self.patch.unpatch_all()
        calls = []

        def original(message, *args, **kwargs):
            calls.append((message, args, kwargs))
            return "result"

        self.client.on_direct_message_create = original
        self.patch.patch_adapter(self.adapter, {"qq-dm"})
        message = direct_message()
        result = await self.client.on_direct_message_create(message, "extra", source="qq")
        self.assertEqual(result, "result")
        self.assertEqual(calls, [(message, ("extra",), {"source": "qq"})])
        self.patch.unpatch_all()
        self.assertIs(self.client.on_direct_message_create, original)

        async def broken(message):
            raise RuntimeError("parse failed")

        self.client.on_direct_message_create = broken
        self.patch.patch_adapter(self.adapter, {"qq-dm"})
        with self.assertRaisesRegex(RuntimeError, "parse failed"):
            await self.client.on_direct_message_create(direct_message())

    async def test_suspend_reinstall_keeps_cache_and_does_not_stack_wrappers(self):
        callback = self.client.on_direct_message_create
        await callback(direct_message())
        self.patch.suspend_direct_callbacks()
        self.assertNotIn("on_direct_message_create", vars(self.client))
        self.patch.patch_platforms([self.adapter], {"qq-dm"})
        self.assertIs(self.client.on_direct_message_create, callback)
        self.patch.patch_platforms([self.adapter], {"qq-dm"})
        self.assertIs(self.client.on_direct_message_create, callback)
        await callback(direct_message())
        self.assertEqual(len(self.api.calls), 1)

    async def test_unload_retains_later_wrapper_and_disables_own_lookup(self):
        original = self.client.on_direct_message_create

        @wraps(original)
        async def later(message):
            return await original(message)

        self.client.on_direct_message_create = later
        self.patch.unpatch_all()
        self.assertIs(self.client.on_direct_message_create, later)
        await later(direct_message())
        self.assertEqual(self.api.calls, [])
        self.assertEqual(len(self.adapter.events), 1)

    async def test_api_can_be_initialized_after_the_callback_is_wrapped(self):
        self.patch.unpatch_all()
        self.client.api = None
        self.patch.patch_adapter(self.adapter, {"qq-dm"})
        self.client.api = self.api
        await self.client.on_direct_message_create(direct_message())
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.adapter.avatars_at_commit, [AVATAR_URL])

    async def test_late_native_avatar_is_not_overwritten_by_lookup_result(self):
        self.api.release = asyncio.Event()
        message = direct_message()
        task = asyncio.create_task(self.client.on_direct_message_create(message))
        await asyncio.wait_for(self.api.started.wait(), 1)
        native = "https://example.invalid/native.png"
        message.author.avatar = native
        self.api.release.set()
        await task
        self.assertEqual(message.author.avatar, native)
        self.assertEqual(self.adapter.avatars_at_commit, [native])

    async def test_client_replacement_restores_old_callback_and_uses_new_cache(self):
        old_client = self.client
        await old_client.on_direct_message_create(direct_message())
        new_client = ParsingClient()
        new_client.platform = self.adapter
        self.adapter.client = new_client
        self.patch.patch_adapter(self.adapter, {"qq-dm"})
        self.assertNotIn("on_direct_message_create", vars(old_client))
        await new_client.on_direct_message_create(direct_message())
        self.assertEqual(len(old_client.api.calls), 1)
        self.assertEqual(len(new_client.api.calls), 1)

    async def test_reload_over_an_inactive_retained_callback_does_not_double_query(self):
        original = self.client.on_direct_message_create

        @wraps(original)
        async def later(message):
            return await original(message)

        self.client.on_direct_message_create = later
        self.patch.unpatch_all()
        self.patch.patch_adapter(self.adapter, {"qq-dm"})
        await self.client.on_direct_message_create(direct_message())
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.adapter.avatars_at_commit, [AVATAR_URL])
        self.patch.unpatch_all()
        self.assertIs(self.client.on_direct_message_create, later)

    async def test_commit_mapping_alone_never_queries_a_member(self):
        # Group/C2C receive callbacks are not wrapped by this patch.
        self.adapter.commit_event(fake_avatar_event(avatar=None))
        self.assertEqual(self.api.calls, [])
        self.assertEqual(self.adapter.avatars_at_commit, [None])

    async def test_webhook_and_unmanaged_instance_scopes(self):
        webhook = AvatarPushAdapter("qq_official_webhook", "qq-wh")
        unmanaged = AvatarPushAdapter(platform_id="qq-unmanaged")
        self.patch.patch_platforms([self.adapter, webhook, unmanaged], {"qq-dm", "qq-wh"})
        await webhook.client.on_direct_message_create(direct_message())
        await unmanaged.client.on_direct_message_create(direct_message())
        self.assertEqual(len(webhook.client.api.calls), 1)
        self.assertEqual(unmanaged.client.api.calls, [])
        self.assertNotIn("on_direct_message_create", vars(unmanaged.client))
        self.patch.patch_platforms([self.adapter, webhook], set())
        self.assertNotIn("on_direct_message_create", vars(webhook.client))
        self.assertNotIn("on_direct_message_create", vars(self.client))


class AvatarAndPushLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = AvatarPushAdapter()
        self.client = self.adapter.client
        self.context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[self.adapter])
        )
        self.config = {"managed_platform_ids": ["qq-dm"], "channel_dm_push_enabled": True}
        self.plugin = QQBotFix(self.context, self.config)
        self.addAsyncCleanup(self.plugin.terminate)

    async def assert_push_routes_to_dm_session(self):
        session = SimpleNamespace(message_type=MessageType.FRIEND_MESSAGE, session_id="member-1")
        await self.adapter.send_by_session(session, FakeMessageChain([Plain("probe")]))
        self.assertEqual(self.client.api.dms[-1][0], "dm-session-1")
        self.assertEqual(self.adapter.sent_by_original, [])

    async def test_both_features_install_repeat_and_unload_in_correct_order(self):
        await self.plugin.initialize()
        callback = self.client.on_direct_message_create
        self.assertTrue(is_channel_dm_push_active(self.context, "qq-dm"))
        await callback(direct_message())
        self.assertEqual(self.adapter.avatars_at_commit, [AVATAR_URL])
        await self.assert_push_routes_to_dm_session()
        await self.plugin.on_platform_loaded()
        self.assertIs(self.client.on_direct_message_create, callback)
        self.assertTrue(is_channel_dm_push_active(self.context, "qq-dm"))
        await callback(direct_message())
        self.assertEqual(len(self.client.api.calls), 1)
        await self.plugin.terminate()
        self.assertNotIn("on_direct_message_create", vars(self.client))
        self.assertNotIn("commit_event", vars(self.adapter))
        self.assertNotIn("send_by_session", vars(self.adapter))
        self.assertFalse(is_channel_dm_push_active(self.context, "qq-dm"))
        await self.plugin.initialize()
        await self.client.on_direct_message_create(direct_message())
        self.assertEqual(len(self.client.api.calls), 2)
        self.assertTrue(is_channel_dm_push_active(self.context, "qq-dm"))

    async def test_toggle_push_after_avatar_patch_is_already_installed(self):
        self.config["channel_dm_push_enabled"] = False
        await self.plugin.initialize()
        self.assertFalse(is_channel_dm_push_active(self.context, "qq-dm"))
        for enabled in (True, False, True):
            self.config["channel_dm_push_enabled"] = enabled
            await self.plugin.on_platform_loaded()
            self.assertEqual(is_channel_dm_push_active(self.context, "qq-dm"), enabled)
            await self.client.on_direct_message_create(direct_message())
            self.assertEqual(self.adapter.avatars_at_commit[-1], AVATAR_URL)
            if enabled:
                await self.assert_push_routes_to_dm_session()
        self.assertEqual(len(self.client.api.calls), 1)

    async def test_toggle_avatar_leaves_push_active(self):
        await self.plugin.initialize()
        for enabled in (False, True, False, True):
            self.config["avatar_patch_enabled"] = enabled
            await self.plugin.on_platform_loaded()
            self.assertTrue(is_channel_dm_push_active(self.context, "qq-dm"))
            await self.client.on_direct_message_create(direct_message())
            self.assertEqual(self.client.avatars_at_parse[-1], AVATAR_URL if enabled else None)
            await self.assert_push_routes_to_dm_session()

    async def test_failed_member_query_still_observes_dm_session_for_push(self):
        self.client.api.error = PermissionError("denied")
        await self.plugin.initialize()
        await self.client.on_direct_message_create(direct_message())
        self.assertEqual(self.client.avatars_at_parse, [None])
        await self.assert_push_routes_to_dm_session()


if __name__ == "__main__":
    unittest.main()
