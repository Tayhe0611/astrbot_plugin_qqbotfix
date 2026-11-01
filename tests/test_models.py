from __future__ import annotations

import unittest
from types import SimpleNamespace

from core.models import InteractionContext


class InteractionContextTests(unittest.TestCase):
    def test_reads_resolved_fields_from_mapping_payload(self) -> None:
        interaction = {
            "id": 123,
            "event_id": "event",
            "scene": 2,
            "channel_id": "channel",
            "user_openid": "user",
            "data": {
                "resolved": {
                    "button_id": "button",
                    "button_data": "payload",
                    "message_id": "message",
                }
            },
        }
        adapter = SimpleNamespace()
        client = SimpleNamespace()
        context = InteractionContext.from_interaction(
            adapter, client, interaction, platform_id="qq-1"
        )
        self.assertEqual(context.platform_id, "qq-1")
        self.assertEqual(context.interaction_id, "123")
        self.assertEqual(context.button_id, "button")
        self.assertEqual(context.button_data, "payload")
        self.assertEqual(context.channel_id, "channel")
        self.assertEqual(context.user_id, "user")
        with self.assertRaises(RuntimeError):
            import asyncio
            asyncio.run(context.ack_once())


if __name__ == "__main__":
    unittest.main()
