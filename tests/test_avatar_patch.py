from __future__ import annotations

import importlib
import inspect
import unittest
from dataclasses import dataclass
from functools import wraps
from types import SimpleNamespace
from unittest.mock import Mock

from core.avatar_patch import SenderAvatarPatch

AVATAR_URL = "https://example.invalid/qq/avatar.png"
_MISSING = object()


@dataclass
class FakeMessageMember:
    # Match AstrBot's current two-field, non-slotted dataclass.
    user_id: str
    nickname: str | None = None


def fake_avatar_event(avatar=AVATAR_URL, *, raw_message=_MISSING, sender=_MISSING):
    if raw_message is _MISSING:
        raw_message = SimpleNamespace(author=SimpleNamespace(avatar=avatar))
    if sender is _MISSING:
        sender = FakeMessageMember("member-1", "nickname")
    return SimpleNamespace(
        message_obj=SimpleNamespace(raw_message=raw_message, sender=sender)
    )


class FakePlatform:
    def commit_event(self, event, *args, **kwargs):
        self.events.append(event)
        self.avatars_at_commit.append(getattr(event.message_obj.sender, "avatar", None))
        self.commit_args.append((args, kwargs))
        return "queued"


class FakeAvatarAdapter(FakePlatform):
    def __init__(self, name="qq_official", platform_id="qq-1"):
        self._name = name
        self._platform_id = platform_id
        self.events = []
        self.avatars_at_commit = []
        self.commit_args = []

    def meta(self):
        return SimpleNamespace(name=self._name, id=self._platform_id)


class FakeQQParser:
    @staticmethod
    async def _parse_from_qqofficial(raw_message):
        return fake_avatar_event(raw_message=raw_message).message_obj


class FakeWSClient:
    def __init__(self, platform):
        self.platform = platform

    async def on_message_create(self, raw_message):
        message = await FakeQQParser._parse_from_qqofficial(raw_message)
        return self.platform.commit_event(SimpleNamespace(message_obj=message))


class FakeWebhookAdapter(FakeAvatarAdapter):
    async def convert_message(self, raw_message):
        message = await FakeQQParser._parse_from_qqofficial(raw_message)
        return self.commit_event(SimpleNamespace(message_obj=message))


class SenderAvatarPatchTests(unittest.TestCase):
    def setUp(self):
        self.logger = Mock()
        self.patch = SenderAvatarPatch(logger=self.logger)
        self.addCleanup(self.patch.unpatch_all)

    def test_adds_dynamic_avatar_before_original_commit_and_preserves_identity(self):
        adapter = FakeAvatarAdapter()
        original_method = adapter.commit_event.__func__
        event = fake_avatar_event()
        message = event.message_obj
        sender = message.sender
        self.assertFalse(hasattr(sender, "avatar"))
        self.assertTrue(self.patch.patch_adapter(adapter, {"qq-1"}))
        self.assertEqual(adapter.commit_event(event, "extra", source="qq"), "queued")
        self.assertEqual(sender.avatar, AVATAR_URL)
        self.assertEqual(adapter.avatars_at_commit, [AVATAR_URL])
        self.assertIs(adapter.events[0], event)
        self.assertIs(event.message_obj, message)
        self.assertIs(event.message_obj.sender, sender)
        self.assertEqual(adapter.commit_args, [(("extra",), {"source": "qq"})])
        self.patch.unpatch_all()
        self.assertNotIn("commit_event", vars(adapter))
        self.assertIs(adapter.commit_event.__func__, original_method)

    def test_only_supported_configured_instances_are_patched(self):
        for name, platform_id, expected in (
            ("qq_official", "qq-1", True),
            ("qq_official_webhook", "qq-1", True),
            ("qq_official", "qq-other", False),
            ("aiocqhttp", "qq-1", False),
        ):
            with self.subTest(name=name, platform_id=platform_id):
                adapter = FakeAvatarAdapter(name, platform_id)
                self.assertEqual(self.patch.patch_adapter(adapter, {"qq-1"}), expected)
                event = fake_avatar_event()
                adapter.commit_event(event)
                self.assertEqual(hasattr(event.message_obj.sender, "avatar"), expected)

    def test_empty_managed_ids_do_not_patch(self):
        adapter = FakeAvatarAdapter()
        self.assertFalse(self.patch.patch_adapter(adapter, set()))
        self.assertNotIn("commit_event", vars(adapter))

    def test_mapping_metadata_and_raw_author_are_supported(self):
        adapter = FakeAvatarAdapter()
        adapter.meta = lambda: {"name": "qq_official_webhook", "id": "qq-1"}
        self.assertTrue(self.patch.patch_adapter(adapter, {"qq-1"}))
        event = fake_avatar_event(raw_message={"author": {"avatar": AVATAR_URL}})
        adapter.commit_event(event)
        self.assertEqual(event.message_obj.sender.avatar, AVATAR_URL)

    def test_mapping_sender_is_supported(self):
        adapter = FakeAvatarAdapter()
        self.patch.patch_adapter(adapter, {"qq-1"})
        event = fake_avatar_event(sender={"user_id": "member-1"})
        adapter.commit_event(event)
        self.assertEqual(event.message_obj.sender["avatar"], AVATAR_URL)

    def test_absent_or_invalid_avatar_keeps_delivery_unchanged(self):
        adapter = FakeAvatarAdapter()
        self.patch.patch_adapter(adapter, {"qq-1"})
        raw_messages = [
            None, {}, SimpleNamespace(), {"author": None}, {"author": {}},
            *({"author": {"avatar": value}} for value in (None, "", "  ", 1, [], {})),
        ]
        for raw_message in raw_messages:
            with self.subTest(raw_message=raw_message):
                event = fake_avatar_event(raw_message=raw_message)
                self.assertEqual(adapter.commit_event(event), "queued")
                self.assertFalse(hasattr(event.message_obj.sender, "avatar"))
        self.assertEqual(len(adapter.events), len(raw_messages))
        self.logger.warning.assert_not_called()

    def test_missing_message_or_sender_is_ignored(self):
        adapter = FakeAvatarAdapter()
        received = []
        adapter.commit_event = received.append
        self.patch.patch_adapter(adapter, {"qq-1"})
        events = [None, SimpleNamespace(), fake_avatar_event(sender=None)]
        for event in events:
            adapter.commit_event(event)
        self.assertEqual(received, events)
        self.logger.warning.assert_not_called()

    def test_existing_avatar_is_preserved_and_empty_native_field_is_filled(self):
        adapter = FakeAvatarAdapter()
        self.patch.patch_adapter(adapter, {"qq-1"})
        for value, expected in (
            ("https://example.invalid/native.png", "https://example.invalid/native.png"),
            (None, AVATAR_URL), ("", AVATAR_URL), ("  ", AVATAR_URL),
        ):
            with self.subTest(value=value):
                event = fake_avatar_event()
                event.message_obj.sender.avatar = value
                adapter.commit_event(event)
                self.assertEqual(event.message_obj.sender.avatar, expected)

    def test_avatar_url_is_trimmed_without_synthesis(self):
        adapter = FakeAvatarAdapter()
        self.patch.patch_adapter(adapter, {"qq-1"})
        event = fake_avatar_event("  " + AVATAR_URL + "  ")
        adapter.commit_event(event)
        self.assertEqual(event.message_obj.sender.avatar, AVATAR_URL)

    def test_slots_or_frozen_sender_does_not_break_delivery_and_warns_once(self):
        @dataclass(frozen=True, slots=True)
        class ReadOnlySender:
            user_id: str

        adapter = FakeAvatarAdapter()
        self.patch.patch_adapter(adapter, {"qq-1"})
        for _ in range(2):
            event = fake_avatar_event(sender=ReadOnlySender("member-1"))
            self.assertEqual(adapter.commit_event(event), "queued")
        self.assertEqual(len(adapter.events), 2)
        self.logger.warning.assert_called_once()

    def test_original_commit_error_is_not_swallowed_or_retried(self):
        adapter = FakeAvatarAdapter()
        original_commit = Mock(side_effect=RuntimeError("queue error"))
        adapter.commit_event = original_commit
        self.patch.patch_adapter(adapter, {"qq-1"})
        event = fake_avatar_event()
        with self.assertRaisesRegex(RuntimeError, "queue error"):
            adapter.commit_event(event)
        original_commit.assert_called_once_with(event)
        self.assertEqual(event.message_obj.sender.avatar, AVATAR_URL)
        self.patch.unpatch_all()
        self.assertIs(adapter.commit_event, original_commit)

    def test_repeated_installation_does_not_stack_wrappers(self):
        adapter = FakeAvatarAdapter()
        self.assertTrue(self.patch.patch_adapter(adapter, {"qq-1"}))
        wrapper = adapter.commit_event
        self.assertTrue(self.patch.patch_adapter(adapter, {"qq-1"}))
        self.assertEqual(self.patch.patch_platforms([adapter], {"qq-1"}), 1)
        self.assertIs(adapter.commit_event, wrapper)
        other_patch = SenderAvatarPatch(logger=self.logger)
        self.assertFalse(other_patch.patch_adapter(adapter, {"qq-1"}))
        other_patch.unpatch_all()
        self.assertIs(adapter.commit_event, wrapper)
        adapter.commit_event(fake_avatar_event())
        self.assertEqual(len(adapter.events), 1)

    def test_teardown_restores_preexisting_instance_method(self):
        adapter = FakeAvatarAdapter()
        received = []
        original = received.append
        adapter.commit_event = original
        self.patch.patch_adapter(adapter, {"qq-1"})
        self.patch.unpatch_all()
        self.patch.unpatch_all()
        self.assertIs(adapter.commit_event, original)
        event = fake_avatar_event()
        adapter.commit_event(event)
        self.assertFalse(hasattr(event.message_obj.sender, "avatar"))
        self.assertEqual(received, [event])

    def test_teardown_retains_later_owner_and_disables_retained_wrapper(self):
        adapter = FakeAvatarAdapter()
        self.patch.patch_adapter(adapter, {"qq-1"})
        original = adapter.commit_event

        @wraps(original)
        def later_wrapper(event):
            return original(event)

        adapter.commit_event = later_wrapper
        self.assertFalse(self.patch.patch_adapter(adapter, {"qq-1"}))
        self.patch.unpatch_all()
        self.assertIs(adapter.commit_event, later_wrapper)
        event = fake_avatar_event()
        adapter.commit_event(event)
        self.assertFalse(hasattr(event.message_obj.sender, "avatar"))
        # Reloading over an inactive retained wrapper is safe.
        self.assertTrue(self.patch.patch_adapter(adapter, {"qq-1"}))
        event2 = fake_avatar_event()
        adapter.commit_event(event2)
        self.assertEqual(event2.message_obj.sender.avatar, AVATAR_URL)
        self.assertEqual(len(adapter.events), 2)
        self.patch.unpatch_all()
        self.assertIs(adapter.commit_event, later_wrapper)

    def test_module_reload_keeps_existing_owner_detection_and_restoration_safe(self):
        import core.avatar_patch as module

        adapter = FakeAvatarAdapter()
        original_method = adapter.commit_event.__func__
        self.patch.patch_adapter(adapter, {"qq-1"})
        module = importlib.reload(module)
        other_patch = module.SenderAvatarPatch(logger=self.logger)
        self.assertFalse(other_patch.patch_adapter(adapter, {"qq-1"}))
        self.patch.unpatch_all()
        self.assertNotIn("commit_event", vars(adapter))
        self.assertIs(adapter.commit_event.__func__, original_method)
        try:
            self.assertTrue(other_patch.patch_adapter(adapter, {"qq-1"}))
            event = fake_avatar_event()
            adapter.commit_event(event)
            self.assertEqual(event.message_obj.sender.avatar, AVATAR_URL)
        finally:
            other_patch.unpatch_all()
        self.assertNotIn("commit_event", vars(adapter))

    def test_configuration_and_platform_removal_revoke_patches(self):
        first = FakeAvatarAdapter(platform_id="qq-1")
        second = FakeAvatarAdapter(platform_id="qq-2")
        adapters = {"qq-1": first, "qq-2": second}
        self.assertEqual(self.patch.patch_platforms(adapters, {"qq-1", "qq-2"}), 2)
        self.assertEqual(self.patch.patch_platforms(adapters, {"qq-2"}), 1)
        self.assertNotIn("commit_event", vars(first))
        self.assertEqual(self.patch.patch_platforms([], {"qq-2"}), 0)
        self.assertNotIn("commit_event", vars(second))
        self.patch.patch_adapter(first, {"qq-1"})
        self.patch.patch_platforms([first], set())
        self.assertNotIn("commit_event", vars(first))

    def test_unpatchable_adapters_are_skipped_without_crashing(self):
        class SlottedAdapter:
            __slots__ = ()

            def meta(self):
                return {"name": "qq_official", "id": "qq-1"}

            def commit_event(self, event):
                pass

        class ReadOnlyAdapter(FakeAvatarAdapter):
            def __setattr__(self, name, value):
                if name == "commit_event":
                    raise AttributeError("read-only")
                super().__setattr__(name, value)

        class BrokenMetadata(FakeAvatarAdapter):
            def meta(self):
                raise RuntimeError("unavailable")

        for adapter in (
            SlottedAdapter(), ReadOnlyAdapter(), BrokenMetadata(),
            SimpleNamespace(meta=lambda: {"name": "qq_official", "id": "qq-1"}),
        ):
            with self.subTest(adapter=type(adapter).__name__):
                self.assertFalse(self.patch.patch_adapter(adapter, {"qq-1"}))


class SenderAvatarReceivePathTests(unittest.IsolatedAsyncioTestCase):
    async def test_ws_and_webhook_static_parser_paths_are_covered_per_instance(self):
        patch = SenderAvatarPatch(logger=Mock())
        self.addCleanup(patch.unpatch_all)
        ws = FakeAvatarAdapter()
        webhook = FakeWebhookAdapter("qq_official_webhook", "qq-2")
        unmanaged = FakeAvatarAdapter(platform_id="qq-other")
        parser = FakeQQParser._parse_from_qqofficial
        patch.patch_platforms([ws, webhook, unmanaged], {"qq-1", "qq-2"})
        raw_message = SimpleNamespace(author=SimpleNamespace(avatar=AVATAR_URL))
        await FakeWSClient(ws).on_message_create(raw_message)
        await webhook.convert_message(raw_message)
        await FakeWSClient(unmanaged).on_message_create(raw_message)
        self.assertEqual(ws.avatars_at_commit, [AVATAR_URL])
        self.assertEqual(webhook.avatars_at_commit, [AVATAR_URL])
        self.assertEqual(unmanaged.avatars_at_commit, [None])
        self.assertIs(FakeQQParser._parse_from_qqofficial, parser)

    async def test_async_commit_method_keeps_async_shape_and_return_value(self):
        class AsyncAdapter(FakeAvatarAdapter):
            async def commit_event(self, event, *args, **kwargs):
                return super().commit_event(event, *args, **kwargs)

        patch = SenderAvatarPatch(logger=Mock())
        self.addCleanup(patch.unpatch_all)
        adapter = AsyncAdapter()
        patch.patch_adapter(adapter, {"qq-1"})
        self.assertTrue(inspect.iscoroutinefunction(adapter.commit_event))
        event = fake_avatar_event()
        self.assertEqual(await adapter.commit_event(event), "queued")
        self.assertEqual(adapter.avatars_at_commit, [AVATAR_URL])
        patch.unpatch_all()
        self.assertNotIn("commit_event", vars(adapter))


if __name__ == "__main__":
    unittest.main()
