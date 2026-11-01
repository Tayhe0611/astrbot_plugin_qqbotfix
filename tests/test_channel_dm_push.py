from __future__ import annotations

import unittest
from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace
from unittest.mock import patch

from core.channel_dm_push import (
    ChannelDMPushBridge,
    ChannelDMPushError,
    DirectMessageSessionCache,
)


class QuietLogger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class MessageType(Enum):
    FRIEND_MESSAGE = "FriendMessage"
    GROUP_MESSAGE = "GroupMessage"


@dataclass
class Plain:
    text: str


@dataclass
class FakeMessageChain:
    chain: list[object]
    use_markdown_: bool = False


class FakeAPI:
    def __init__(self) -> None:
        self.dms: list[tuple[str, dict[str, object]]] = []

    async def post_dms(
        self,
        guild_id: str,
        *,
        content: str | None = None,
        markdown: object | None = None,
        msg_seq: int | None = None,
    ) -> dict[str, str]:
        self.dms.append(
            (
                guild_id,
                {"content": content, "markdown": markdown, "msg_seq": msg_seq},
            )
        )
        return {"id": "sent-message"}


class FakeClient:
    def __init__(self) -> None:
        self.api = FakeAPI()
        self.direct_messages: list[object] = []
        self.intents = 0
        self._connection = None

    async def on_direct_message_create(self, message: object) -> None:
        self.direct_messages.append(message)


class FakeAdapter:
    def __init__(
        self,
        client: FakeClient,
        platform_id: str = "qq-ws-1",
        adapter_name: str = "qq_official",
    ) -> None:
        self.client = client
        self.platform_id = platform_id
        self.adapter_name = adapter_name
        self.sent_by_original: list[tuple[object, object]] = []
        self.use_markdown_default = False

    def meta(self):
        return SimpleNamespace(name=self.adapter_name, id=self.platform_id)

    async def send_by_session(self, session: object, chain: object) -> str:
        self.sent_by_original.append((session, chain))
        return "original-c2c"


class ChannelDMPushBridgeTests(unittest.IsolatedAsyncioTestCase):
    def make_bridge(self, adapter: FakeAdapter) -> ChannelDMPushBridge:
        bridge = ChannelDMPushBridge(logger=QuietLogger())
        self.assertTrue(bridge.patch_adapter(adapter, {adapter.platform_id}))
        return bridge

    @staticmethod
    def direct_message(member_id: str, dm_session_id: str | None):
        return SimpleNamespace(
            author=SimpleNamespace(id=member_id), guild_id=dm_session_id
        )

    @staticmethod
    def friend_session(member_id: str):
        return SimpleNamespace(
            message_type=MessageType.FRIEND_MESSAGE, session_id=member_id
        )

    async def test_observed_channel_dm_routes_once_to_post_dms(self) -> None:
        client = FakeClient()
        adapter = FakeAdapter(client)
        bridge = self.make_bridge(adapter)
        event = self.direct_message("member-1", "dm-session-1")

        await client.on_direct_message_create(event)
        result = await adapter.send_by_session(
            self.friend_session("member-1"), FakeMessageChain([Plain("hello")], use_markdown_=None)
        )

        self.assertEqual(result, {"id": "sent-message"})
        self.assertEqual(len(client.api.dms), 1)
        self.assertEqual(client.api.dms[0][0], "dm-session-1")
        self.assertEqual(client.api.dms[0][1]["content"], "hello")
        self.assertIsNone(client.api.dms[0][1]["msg_seq"])
        self.assertEqual(adapter.sent_by_original, [])
        self.assertEqual(client.direct_messages, [event])
        bridge.unpatch_all()

    async def test_unknown_friend_session_stays_on_original_c2c_route(self) -> None:
        client = FakeClient()
        adapter = FakeAdapter(client)
        bridge = self.make_bridge(adapter)

        result = await adapter.send_by_session(
            self.friend_session("c2c-openid"), FakeMessageChain([Plain("hello")], use_markdown_=None)
        )

        self.assertEqual(result, "original-c2c")
        self.assertEqual(len(adapter.sent_by_original), 1)
        self.assertEqual(client.api.dms, [])
        bridge.unpatch_all()

    async def test_ambiguous_channel_sessions_fail_closed_without_c2c_fallback(self) -> None:
        client = FakeClient()
        adapter = FakeAdapter(client)
        bridge = self.make_bridge(adapter)
        await client.on_direct_message_create(self.direct_message("member-1", "dm-a"))
        await client.on_direct_message_create(self.direct_message("member-1", "dm-b"))

        with self.assertRaisesRegex(ChannelDMPushError, "ambiguous"):
            await adapter.send_by_session(
                self.friend_session("member-1"), FakeMessageChain([Plain("hello")], use_markdown_=None)
            )

        self.assertEqual(client.api.dms, [])
        self.assertEqual(adapter.sent_by_original, [])
        bridge.unpatch_all()

    async def test_known_member_without_session_id_fails_closed(self) -> None:
        client = FakeClient()
        adapter = FakeAdapter(client)
        bridge = self.make_bridge(adapter)
        await client.on_direct_message_create(self.direct_message("member-1", None))

        with self.assertRaisesRegex(ChannelDMPushError, "missing"):
            await adapter.send_by_session(
                self.friend_session("member-1"), FakeMessageChain([Plain("hello")], use_markdown_=None)
            )

        self.assertEqual(client.api.dms, [])
        self.assertEqual(adapter.sent_by_original, [])
        bridge.unpatch_all()

    async def test_non_friend_message_keeps_original_route(self) -> None:
        client = FakeClient()
        adapter = FakeAdapter(client)
        bridge = self.make_bridge(adapter)
        group_session = SimpleNamespace(
            message_type=MessageType.GROUP_MESSAGE, session_id="group-1"
        )
        chain = FakeMessageChain([Plain("hello")], use_markdown_=None)

        result = await adapter.send_by_session(group_session, chain)

        self.assertEqual(result, "original-c2c")
        self.assertEqual(adapter.sent_by_original, [(group_session, chain)])
        self.assertEqual(client.api.dms, [])
        bridge.unpatch_all()

    async def test_unsupported_components_fail_without_c2c_fallback(self) -> None:
        client = FakeClient()
        adapter = FakeAdapter(client)
        bridge = self.make_bridge(adapter)
        await client.on_direct_message_create(self.direct_message("member-1", "dm-a"))

        with self.assertRaisesRegex(ChannelDMPushError, "unsupported component"):
            await adapter.send_by_session(
                self.friend_session("member-1"),
                FakeMessageChain([Plain("hello"), SimpleNamespace(url="image")]),
            )

        self.assertEqual(client.api.dms, [])
        self.assertEqual(adapter.sent_by_original, [])
        bridge.unpatch_all()

    async def test_markdown_uses_one_dms_attempt_without_msg_seq(self) -> None:
        class MarkdownPayload:
            def __init__(self, content: str) -> None:
                self.content = content

        client = FakeClient()
        adapter = FakeAdapter(client)
        adapter.use_markdown_default = True
        bridge = self.make_bridge(adapter)
        await client.on_direct_message_create(self.direct_message("member-1", "dm-a"))

        with patch("core.channel_dm_push._markdown_payload_type", return_value=MarkdownPayload):
            await adapter.send_by_session(
                self.friend_session("member-1"), FakeMessageChain([Plain("hello")], use_markdown_=None)
            )

        self.assertEqual(len(client.api.dms), 1)
        self.assertEqual(client.api.dms[0][1]["markdown"].content, "hello")
        self.assertIsNone(client.api.dms[0][1]["content"])
        self.assertEqual(client.api.dms[0][1]["msg_seq"], None)
        bridge.unpatch_all()

    async def test_webhook_and_unmanaged_instances_are_not_patched(self) -> None:
        client = FakeClient()
        bridge = ChannelDMPushBridge(logger=QuietLogger())
        webhook = FakeAdapter(client, adapter_name="qq_official_webhook")
        unmanaged = FakeAdapter(client, platform_id="qq-other")

        self.assertFalse(bridge.patch_adapter(webhook, {webhook.platform_id}))
        self.assertFalse(bridge.patch_adapter(unmanaged, {"qq-ws-1"}))
        self.assertFalse(
            getattr(webhook.send_by_session, "__qqbotfix_channel_dm_bridge__", False)
        )
        bridge.unpatch_all()

    async def test_existing_direct_callback_owner_is_not_wrapped(self) -> None:
        client = FakeClient()
        async def another_owner(_message):
            return None
        client.on_direct_message_create = another_owner
        adapter = FakeAdapter(client)
        bridge = ChannelDMPushBridge(logger=QuietLogger())

        self.assertFalse(bridge.patch_adapter(adapter, {adapter.platform_id}))
        self.assertIs(client.on_direct_message_create, another_owner)
        self.assertFalse(
            getattr(adapter.send_by_session, "__qqbotfix_channel_dm_bridge__", False)
        )
    async def test_teardown_restores_adapter_and_callback_methods(self) -> None:
        client = FakeClient()
        adapter = FakeAdapter(client)
        original_send = adapter.send_by_session
        bridge = self.make_bridge(adapter)
        self.assertTrue(getattr(adapter.send_by_session, "__qqbotfix_channel_dm_bridge__"))
        self.assertTrue(
            getattr(client.on_direct_message_create, "__qqbotfix_channel_dm_bridge__")
        )

        bridge.unpatch_all()

        self.assertFalse(
            getattr(adapter.send_by_session, "__qqbotfix_channel_dm_bridge__", False)
        )
        self.assertFalse(
            getattr(client.on_direct_message_create, "__qqbotfix_channel_dm_bridge__", False)
        )
        self.assertEqual(
            await adapter.send_by_session(self.friend_session("member"), "plain"),
            "original-c2c",
        )
        self.assertEqual(await original_send(self.friend_session("member"), "plain"), "original-c2c")


class DirectMessageSessionCacheTests(unittest.TestCase):
    def test_unique_missing_and_ambiguous_states(self) -> None:
        cache = DirectMessageSessionCache()
        self.assertEqual(cache.resolve("member").status, "unknown")
        cache.observe("member", None)
        self.assertEqual(cache.resolve("member").status, "missing")
        cache.observe("member", "dm-a")
        self.assertEqual(cache.resolve("member").session_id, "dm-a")
        cache.observe("member", "dm-b")
        self.assertEqual(cache.resolve("member").status, "ambiguous")


if __name__ == "__main__":
    unittest.main()
