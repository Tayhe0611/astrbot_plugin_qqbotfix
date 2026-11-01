from __future__ import annotations

import unittest
from types import SimpleNamespace

from main import GroupKeyboardSendError, sendgroupkeyboard


def make_group_event(
    *,
    group_openid: str = "group-openid-1",
    msg_id: str | None = None,
    event_id: str | None = None,
    user_openid: str | None = None,
):
    fields = {"group_openid": group_openid}
    if msg_id is not None:
        fields["id"] = msg_id
    if event_id is not None:
        fields["event_id"] = event_id
    if user_openid is not None:
        fields["user_openid"] = user_openid
    return SimpleNamespace(**fields)


class FakeGroupAPI:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.error = error

    async def postgroupmessage(
        self,
        groupopenid: str,
        content: str,
        msgtype: int,
        *,
        msgid: str | None = None,
        msgseq: int | None = None,
        eventid: str | None = None,
        markdown: object | None = None,
        keyboard: object | None = None,
        media: object | None = None,
    ) -> dict[str, str]:
        self.calls.append(
            {
                "groupopenid": groupopenid,
                "content": content,
                "msgtype": msgtype,
                "msgid": msgid,
                "msgseq": msgseq,
                "eventid": eventid,
                "markdown": markdown,
                "keyboard": keyboard,
                "media": media,
            }
        )
        if self.error is not None:
            raise self.error
        return {"id": "sent-1"}


class FakeAdapter:
    def __init__(self, api: FakeGroupAPI, name: str = "qq_official", pid: str = "qq-1"):
        self.client = SimpleNamespace(api=api)
        self._meta = SimpleNamespace(name=name, id=pid)

    def meta(self):
        return self._meta


class GroupKeyboardSendTests(unittest.IsolatedAsyncioTestCase):
    def make_context(self, adapter: FakeAdapter) -> SimpleNamespace:
        return SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[adapter])
        )

    async def test_markdown_url_keyboard_sends_once_with_event_id(self) -> None:
        api = FakeGroupAPI()
        context = self.make_context(FakeAdapter(api))
        keyboard = {"content": {"rows": [{"buttons": [{"action": {"type": 0}}]}]}}
        markdown = {
            "content": "PRTS 结果摘要",
            "force_verify_image_resource": True,
        }

        result = await sendgroupkeyboard(
            context,
            platform_id="qq-1",
            group_event=make_group_event(event_id="event-1"),
            event_id="event-1",
            content="PRTS 结果摘要",
            msg_type=2,
            markdown=markdown,
            keyboard=keyboard,
        )

        self.assertEqual(result, {"id": "sent-1"})
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(
            api.calls[0],
            {
                "groupopenid": "group-openid-1",
                "content": "",
                "msgtype": 2,
                "msgid": None,
                "msgseq": None,
                "eventid": "event-1",
                "markdown": markdown,
                "keyboard": keyboard,
                "media": None,
            },
        )

        self.assertIs(api.calls[0]["markdown"], markdown)
        self.assertIs(api.calls[0]["keyboard"], keyboard)

    async def test_markdown_content_is_normalized_for_msg_id_replies(self) -> None:
        for content in ("", "旧版调用者文本", " "):
            for force_verify in (True, False):
                with self.subTest(content=content, force_verify=force_verify):
                    api = FakeGroupAPI()
                    context = self.make_context(FakeAdapter(api))
                    markdown = {
                        "content": "实际的 Markdown 卡片内容",
                        "force_verify_image_resource": force_verify,
                    }
                    keyboard = {"content": {"rows": [{"buttons": []}]}}

                    result = await sendgroupkeyboard(
                        context,
                        platform_id="qq-1",
                        group_event=make_group_event(msg_id="incoming-msg-1"),
                        msg_id="incoming-msg-1",
                        msg_seq=2,
                        content=content,
                        msg_type=2,
                        markdown=markdown,
                        keyboard=keyboard,
                    )

                    self.assertEqual(result, {"id": "sent-1"})
                    self.assertEqual(len(api.calls), 1)
                    self.assertEqual(
                        api.calls[0],
                        {
                            "groupopenid": "group-openid-1",
                            "content": "",
                            "msgtype": 2,
                            "msgid": "incoming-msg-1",
                            "msgseq": 2,
                            "eventid": None,
                            "markdown": markdown,
                            "keyboard": keyboard,
                            "media": None,
                        },
                    )
                    self.assertIs(api.calls[0]["markdown"], markdown)
                    self.assertEqual(
                        markdown,
                        {
                            "content": "实际的 Markdown 卡片内容",
                            "force_verify_image_resource": force_verify,
                        },
                    )
                    self.assertIs(api.calls[0]["keyboard"], keyboard)

    async def test_uploaded_media_and_keyboard_use_msg_id_and_sequence(self) -> None:
        api = FakeGroupAPI()
        context = self.make_context(FakeAdapter(api))
        media = {"file_info": "uploaded-file-info"}
        keyboard = {"content": {"rows": [{"buttons": [{"action": {"type": 0}}]}]}}

        await sendgroupkeyboard(
            context,
            platform_id="qq-1",
            group_event=make_group_event(msg_id="incoming-msg-1"),
            msg_id="incoming-msg-1",
            msg_seq=2,
            content="结果图片",
            msg_type=7,
            media=media,
            keyboard=keyboard,
        )

        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.calls[0]["msgid"], "incoming-msg-1")
        self.assertEqual(api.calls[0]["msgseq"], 2)
        self.assertEqual(api.calls[0]["content"], "结果图片")
        self.assertEqual(api.calls[0]["media"], media)
        self.assertEqual(api.calls[0]["keyboard"], keyboard)

    async def test_media_only_fallback_call_is_supported(self) -> None:
        api = FakeGroupAPI()
        context = self.make_context(FakeAdapter(api))

        await sendgroupkeyboard(
            context,
            platform_id="qq-1",
            group_event=make_group_event(msg_id="incoming-msg-1"),
            msg_id="incoming-msg-1",
            msg_seq=1,
            content=" ",
            msg_type=7,
            media={"file_info": "uploaded-file-info"},
            keyboard=None,
        )

        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.calls[0]["content"], " ")
        self.assertIsNone(api.calls[0]["keyboard"])

    async def test_rejects_non_string_content_before_api(self) -> None:
        api = FakeGroupAPI()
        context = self.make_context(FakeAdapter(api))
        for msg_type in (2, 7):
            for content in (None, 1, True, {}, [], b"text"):
                with self.subTest(msg_type=msg_type, content=content):
                    with self.assertRaisesRegex(
                        GroupKeyboardSendError, "content must be a string"
                    ):
                        await sendgroupkeyboard(
                            context,
                            platform_id="qq-1",
                            group_event=make_group_event(event_id="event-1"),
                            event_id="event-1",
                            content=content,
                            msg_type=msg_type,
                            markdown={"content": "卡片内容"} if msg_type == 2 else None,
                            media={"file_info": "uploaded-file-info"} if msg_type == 7 else None,
                            keyboard={"content": {"rows": [{"buttons": []}]}},
                        )
        self.assertEqual(api.calls, [])

    async def test_rejects_empty_media_content_before_api(self) -> None:
        api = FakeGroupAPI()
        context = self.make_context(FakeAdapter(api))
        with self.assertRaisesRegex(
            GroupKeyboardSendError,
            "content must be a non-empty string for media messages",
        ):
            await sendgroupkeyboard(
                context,
                platform_id="qq-1",
                group_event=make_group_event(msg_id="incoming-msg-1"),
                msg_id="incoming-msg-1",
                msg_seq=1,
                content="",
                msg_type=7,
                media={"file_info": "uploaded-file-info"},
                keyboard=None,
            )
        self.assertEqual(api.calls, [])

    async def test_timeout_is_propagated_without_retry(self) -> None:
        api = FakeGroupAPI(TimeoutError("response timed out"))
        context = self.make_context(FakeAdapter(api))

        with self.assertRaises(TimeoutError):
            await sendgroupkeyboard(
                context,
                platform_id="qq-1",
                group_event=make_group_event(event_id="event-1"),
                event_id="event-1",
                content="查看结果",
                msg_type=2,
                markdown={"content": "查看结果"},
                keyboard={"content": {"rows": [{"buttons": []}]}},
            )
        self.assertEqual(len(api.calls), 1)

    async def test_rejects_wrong_adapter_name_and_mismatched_instance_id(self) -> None:
        for adapter in (
            FakeAdapter(FakeGroupAPI(), name="qq_official_webhook"),
            FakeAdapter(FakeGroupAPI(), pid="another-id"),
        ):
            with self.subTest(adapter=adapter._meta):
                with self.assertRaises(GroupKeyboardSendError):
                    await sendgroupkeyboard(
                        self.make_context(adapter),
                        platform_id="qq-1",
                        group_event=make_group_event(msg_id="incoming-msg-1"),
                        msg_id="incoming-msg-1",
                        content="查看结果",
                        msg_type=2,
                        markdown={"content": "查看结果"},
                        keyboard={"content": {"rows": [{"buttons": []}]}},
                    )

    async def test_rejects_umo_c2c_and_mismatched_event_identifiers(self) -> None:
        api = FakeGroupAPI()
        context = self.make_context(FakeAdapter(api))
        invalid_events = (
            SimpleNamespace(session_id="qq:group:looks-like-a-umo"),
            make_group_event(user_openid="c2c-openid"),
            make_group_event(msg_id="other-message"),
            make_group_event(event_id="other-event"),
        )
        for event in invalid_events:
            with self.subTest(event=event):
                with self.assertRaises(GroupKeyboardSendError):
                    await sendgroupkeyboard(
                        context,
                        platform_id="qq-1",
                        group_event=event,
                        msg_id="incoming-msg-1",
                        content="查看结果",
                        msg_type=2,
                        markdown={"content": "查看结果"},
                        keyboard={"content": {"rows": [{"buttons": []}]}},
                    )
        self.assertEqual(api.calls, [])

    async def test_rejects_ambiguous_or_incomplete_reply_payload_before_api(self) -> None:
        api = FakeGroupAPI()
        context = self.make_context(FakeAdapter(api))
        base = {
            "context": context,
            "platform_id": "qq-1",
            "group_event": make_group_event(msg_id="m1", event_id="e1"),
            "content": "查看结果",
            "msg_type": 2,
            "markdown": {"content": "查看结果"},
            "keyboard": {"content": {"rows": [{"buttons": []}]}},
        }
        cases = (
            {},
            {"msg_id": "m1", "event_id": "e1"},
            {"msg_id": "m1"},
            {"msg_id": "m1", "msg_seq": 0},
            {"event_id": "e1", "msg_seq": 2},
            {"msg_id": "m1", "msg_seq": 1, "content": None},
            {"event_id": "e1", "keyboard": {}},
            {"event_id": "e1", "keyboard": None},
            {"event_id": "e1", "markdown": None},
            {"event_id": "e1", "media": {"file_info": "uploaded-file-info"}},
            {"event_id": "e1", "msg_type": True},
            {"event_id": "e1", "msg_type": 1},
            {"msg_id": "m1", "msg_seq": True},
            {"msg_id": "m1", "msg_seq": "1"},
            {"msg_id": "m1", "msg_type": 7},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                arguments = dict(base)
                arguments.update(changes)
                call_context = arguments.pop("context")
                with self.assertRaises(GroupKeyboardSendError):
                    await sendgroupkeyboard(call_context, **arguments)
        self.assertEqual(api.calls, [])

    async def test_supports_snake_case_sdk_method(self) -> None:
        calls = []

        async def post_group_message(
            group_openid: str,
            content: str,
            msg_type: int,
            event_id: str,
            markdown: object,
            keyboard: object,
        ):
            calls.append((group_openid, content, msg_type, event_id, markdown, keyboard))

        adapter = FakeAdapter(FakeGroupAPI())
        adapter.client.api = SimpleNamespace(post_group_message=post_group_message)
        await sendgroupkeyboard(
            self.make_context(adapter),
            platform_id="qq-1",
            group_event=make_group_event(event_id="event-1"),
            event_id="event-1",
            content="查看结果",
            msg_type=2,
            markdown={"content": "查看结果"},
            keyboard={"content": {"rows": [{"buttons": []}]}},
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0:4], ("group-openid-1", "", 2, "event-1"))


    async def test_supports_kwargs_sdk_and_returns_result_unchanged(self) -> None:
        calls = []
        result = {"id": "sent-kwargs", "sdk_metadata": object()}

        async def post_group_message(**kwargs):
            calls.append(kwargs)
            return result

        markdown = {"content": "卡片内容", "force_verify_image_resource": True}
        keyboard = {"content": {"rows": [{"buttons": []}]}}
        adapter = FakeAdapter(FakeGroupAPI())
        adapter.client.api = SimpleNamespace(post_group_message=post_group_message)
        response = await sendgroupkeyboard(
            self.make_context(adapter),
            platform_id="qq-1",
            group_event=make_group_event(event_id="event-1"),
            event_id="event-1",
            content="旧版调用者文本",
            msg_type=2,
            markdown=markdown,
            keyboard=keyboard,
        )

        self.assertIs(response, result)
        self.assertEqual(
            calls,
            [{
                "group_openid": "group-openid-1",
                "content": "",
                "msg_type": 2,
                "event_id": "event-1",
                "markdown": markdown,
                "keyboard": keyboard,
            }],
        )
        self.assertIs(calls[0]["markdown"], markdown)
        self.assertIs(calls[0]["keyboard"], keyboard)


if __name__ == "__main__":
    unittest.main()
