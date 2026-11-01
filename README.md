# astrbot_plugin_qqbotfix

Runtime compatibility patches for QQ official button interactions, sender avatars,
passive group keyboard replies, and opt-in channel-DM text sends on AstrBot's
`qq_official` (Gateway/WS) adapter. Interaction callbacks and sender-avatar mapping
also support `qq_official_webhook`. The plugin does not edit AstrBot core files
or fabricate an `AstrMessageEvent` itself.

## Ownership and configuration

The bridge is **fail-closed**: `_conf_schema.json` defaults
`managed_platform_ids` to an empty list, so no QQ client is patched until the
operator explicitly assigns platform instance IDs to this bridge. Use the ID
shown for the instance in AstrBot's platform configuration. Only those IDs may
have their callback attached and, for WS, interaction intent enabled.

Consumers must also scope each route to the exact platform IDs it handles. An
interaction is dispatched only if both the bridge's managed-ID list and a
consumer route include the adapter's `meta().id`. This lets one QQ instance stay
owned by an unmigrated plugin while another instance is migrated.

Do not enable an ID in `managed_platform_ids` while a legacy consumer still
patches that same client's callback or intents. The bridge rejects a callback
that another plugin installed into the client's instance dictionary, but a
legacy plugin loaded later can still replace the callback. Migrate that
consumer first, then assign the ID.

## Consumer API

Register during consumer `initialize()` and retain the token for `terminate()`:

```python
from data.plugins.astrbot_plugin_qqbotfix.main import (
    register_interaction_handler,
)

async def refresh_handler(ctx):
    # Normalized fields include:
    # ctx.platform_id, ctx.scene, ctx.button_id, ctx.button_data,
    # ctx.message_id, ctx.channel_id, ctx.guild_id, ctx.user_id,
    # ctx.interaction (raw BotPy object), ctx.client, and ctx.adapter.
    await ctx.ack_once(0)  # only when using ack_mode="manual"
    ...

registration = register_interaction_handler(
    self.context,
    refresh_handler,
    platform_ids={"your-platform-instance-id"},
    button_data_prefix="example:",
    ack_mode="manual",
    fallback_ack_code=1,
)
```

The supported matchers are one exact `button_data` or one
`button_data_prefix`. Exact matches take precedence; among prefixes, the longest
prefix wins. Overlapping duplicate registrations for the same matcher and
platform ID are rejected. One interaction is delivered to one route only.

The returned token's `unregister()` removes only that consumer's route. The
route table is stored on AstrBot's shared `Context`, so subscriptions survive a
reload of this bridge module; consumers should still unregister and recreate
their own tokens as part of their normal lifecycle.

### ACK modes

- `ack_mode="auto"` (default): the bridge attempts code `0` before invoking the
  handler. The handler must not expect to choose another code.
- `ack_mode="manual"`: the handler calls `await ctx.ack_once(code)` at its
  chosen point. Codes accepted by the bridge are `0` through `5`. If the handler
  throws, returns without an ACK, or misses `ack_deadline`, the bridge attempts
  `fallback_ack_code` (default `1`).
- Each dispatched context permits at most one API attempt, even if the HTTP
  request fails or times out. A repeated `ack_once()` returns `False`; it will
  not issue a second ACK for that context. Consumers own business-level
  duplicate detection and may ACK a repeated delivery with their chosen code.
- `handler_timeout` bounds consumer work. If a manual handler exceeds the ACK
  deadline before attempting an ACK, the bridge sends the fallback and cancels
  it. If it has already ACKed, timeout only cancels/logs the handler; there is
  no second ACK.

`is_interaction_bridge_active(context, platform_id)` reports whether that exact
instance currently has the bridge callback. A consumer can use it to decide
whether to send type-1 buttons or fall back to type-2 buttons.

## Lifecycle constraints

- WS intent is ORed before the platform-loaded hook returns, and the bridge
  warns if it detects that the BotPy connection has already started. If the
  intent was added too late, restart that platform instance and verify a real
  button click; changing the in-memory integer alone does not update an
  established Gateway subscription.
- The bridge chains to a baseline callback instead of treating every callable as
  a foreign owner. A callback that resolves through the client's class — a bound
  method such as the one AstrBot's own BotPy client subclass supplies — is
  infrastructure: the bridge wraps it and forwards every interaction no
  registered route claims, so button callbacks owned by the core keep working
  alongside QQ menu plugins. Claiming and forwarding are mutually exclusive; a
  routed interaction is never also handed to the baseline, so one click is
  acknowledged once and handled once.
- Only a callback that another plugin installed *into the instance dictionary*
  still triggers the ownership conflict. Plugins install plain functions or
  closures, so a non-method function found there is preserved untouched and the
  client is skipped. A stale qqbotfix bridge found there (a hot reload that
  skipped `terminate()`) is replaced rather than chained to, and is not restored
  on teardown.
- Intent bits are retained for the lifetime of the BotPy client. A consumer
  unregistering must not remove an intent another route still depends on.
- BotPy's callback and AstrBot adapter client remain internal integration
  points; test against the deployed AstrBot/BotPy versions.

## QQ group passive keyboard replies

`sendgroupkeyboard()` is a low-level, one-attempt helper for replying to a live
QQ group event. It requires the exact AstrBot `platform_id`, the raw BotPy
`group_event`, a string `content`, and exactly one `msg_id` or `event_id`
that matches that event. The helper derives `group_openid` from the group event
and rejects C2C events, mismatched platform IDs, and generic UMO/session IDs.
`msg_seq` is required with `msg_id`; start at `1` and increment it for any additional
reply to that source message.

```python
from data.plugins.astrbot_plugin_qqbotfix.main import sendgroupkeyboard

# These values must come from the current, live QQ group event and the existing
# BotPy upload flow. Build keyboard_payload using QQ's current keyboard schema.
await sendgroupkeyboard(
    self.context,
    platform_id=qq_platform_instance_id,
    group_event=raw_group_event,
    msg_id=raw_group_event.id,
    msg_seq=1,
    content="",  # The actual card text belongs in markdown_payload["content"].
    msg_type=2,
    markdown=markdown_payload,
    keyboard=keyboard_payload,
)
```

For Markdown (`msg_type=2`), `content` may be empty. Existing callers may still
provide non-empty text, but the helper always sends an empty outer `content` to
BotPy. The actual card text stays in the supplied `markdown` payload, which is
passed through unchanged, including `force_verify_image_resource`. Media
messages (`msg_type=7`) still require non-empty `content` and retain it unchanged.

After upgrading, restart AstrBot or reload `qqbotfix` first and then reload its
consumer plugins so their imported helpers use the new version.

The helper passes the supplied keyboard payload through unchanged; it does not
invent button action values or register callback handlers. In QQ's button schema,
a URL-jump button uses `action.type=0` with the target URL in `action.data`;
`action.type=1` is a callback and is not needed here. The
first live check should be a Markdown message plus that URL keyboard (`msg_type=2`)
without media. Then, in a separate test event, try the uploaded `media` together
with the same keyboard (`msg_type=7`):

```python
await sendgroupkeyboard(
    self.context,
    platform_id=qq_platform_instance_id,
    group_event=raw_group_event,
    msg_id=raw_group_event.id,
    msg_seq=1,
    content=" ",  # QQ's media message still requires the content field.
    msg_type=7,
    media=uploaded_media,
    keyboard=keyboard_payload,
)
```

A `media` value must be the uploaded BotPy media/file-info object, not a local
path or public image URL. The SDK exposing both `media` and `keyboard` parameters
does not guarantee QQ accepts them together; only a real bot/group test can
confirm that. If QQ explicitly rejects the combined payload, make two passive
replies to the same message: media-only with `msg_seq=1`, followed by Markdown
and keyboard with `msg_seq=2`. `keyboard=None` is supported for that media-only
fallback leg. Do not automatically fall back or retry after a timeout: the
original request may already have been accepted, and retrying can duplicate a
reply.

QQ's current send documentation states that active group push has not been
provided since 2025-04-21. Use this helper only while handling a replyable
group event, not for a detached or late background task: passive replies must
arrive within the documented five-minute window and are limited per source
message. QQ enforces freshness and reply limits server-side; this helper
requires a reply identifier but cannot prove that it is fresh. The URL
keyboard/custom-button feature may also require the corresponding QQ bot
permission/template setup.

## QQ sender-avatar compatibility patch

`avatar_patch_enabled` defaults to `true`. The patch only applies when `enabled`
is true and the exact QQ instance ID is present in `managed_platform_ids`.
Both `qq_official` (WS) and `qq_official_webhook` are supported. An empty ID list
still fails closed; no adapter is patched.

```json
{
  "enabled": true,
  "managed_platform_ids": ["your-qq-platform-instance-id"],
  "avatar_patch_enabled": true
}
```

The independent `core/avatar_patch.py` module handles two stages:

1. **Channel DM enrichment, added in `0.4.1-beta.4`:** wrap the BotPy client's
   `on_direct_message_create()` callback before AstrBot parses the message. If
   `message.author.avatar` is missing/empty, first reuse an avatar already present
   in `raw_data.author.avatar`. Otherwise, query
   `get_guild_member(guild_id=message.src_guild_id, user_id=message.author.id)`
   and read the returned member's `user.avatar`. Write a successful result into
   `message.author.avatar` and, when available, `raw_data.author.avatar`, then
   invoke the original callback. The source guild and user ID can also be read
   from `raw_data` if BotPy omitted the corresponding object fields.
2. **Sender mapping, present since `0.4.1-beta.3`:** wrap each managed adapter's
   `commit_event()` method. After parsing and before queueing, copy a non-empty
   `message.raw_message.author.avatar` into `message.sender.avatar`. This does
   not globally replace the shared QQ static parser or edit AstrBot core,
   `MessageMember`, `bridge.py`, or interaction callbacks.

Lookup/cache behavior:

- Only channel-DM callbacks query members; group/C2C events and existing avatars
  do not trigger this lookup. No avatar URL is invented. The DM's `guild_id` is
  a conversation/session ID, **not** the source guild, and is never substituted
  for `src_guild_id` in member queries.
- The cache is per adapter instance and keyed by `(source guild ID, user ID)`.
  Successful lookups are cached for one hour; failures/empty results for one
  minute. Each instance retains at most 1,024 LRU entries. It is process-local
  and cleared when the avatar patch is disabled or unloaded.
- Concurrent messages for the same key share one lookup. A lookup has a
  three-second timeout; at most 32 distinct lookups may be pending per instance.
  Additional lookups are skipped rather than holding up message delivery.
- Missing source/user IDs, unavailable APIs/permissions, failed requests, empty
  results, and unwritable fields degrade to original message processing. The
  member API may not return an avatar; a successful patch installation does not
  guarantee that every channel DM can acquire one.

Lifecycle and consumer behavior:

- A non-empty existing `sender.avatar` is preserved, including a native field
  supplied by a future core version or another plugin. Missing senders and
  unwritable sender objects do not block event delivery.
- The optional channel-DM session/push bridge installs first; the avatar callback
  wraps outside it, so enrichment finishes before the original message parser.
  Reload hooks temporarily unwrap and reattach only the avatar callback,
  retaining its cache while allowing the push bridge to reconcile ownership.
  Repeated initialization does not stack wrappers.
- Disabling/unloading restores original methods and cancels pending lookups
  without dropping messages waiting on those lookups. If a later plugin has
  wrapped/replaced a method, its method is retained and this patch's retained
  wrapper is deactivated instead of clobbering the new owner. Managed-ID and
  platform removals are reconciled on the next load hook.
- `sender.avatar` remains a runtime compatibility attribute, not a formal
  dataclass field. Consumers can use
  `getattr(event.message_obj.sender, "avatar", None)`, but dataclass serialization
  and type declarations are not extended. Already queued messages are not
  retroactively changed. A long-term upstream fix should add an optional
  `avatar` field to `MessageMember` and populate it in the QQ adapter parser.

After upgrading, reload `qqbotfix` or restart AstrBot. Check for
`sender-avatar compatibility patch installed` for each configured instance,
then test a real channel DM. If the original event lacks an avatar but has
`src_guild_id` and `author.id`, verify that a permitted member query fills both
the raw author and `sender.avatar`. Setting `avatar_patch_enabled` to `false`
and reloading disables both avatar stages without disabling the interaction
bridge or separately enabled channel-DM push feature.

## Optional channel-DM proactive text sends

This feature is separate from the interaction callback bridge and is **off by
default**. To enable it, set `channel_dm_push_enabled: true` and include the
specific `qq_official` WS instance ID in `managed_platform_ids`. It does not
support `qq_official_webhook` in this version.

The shim observes BotPy `DirectMessage` events and uses the message's member ID
and DM session ID for subsequent `send_by_session` calls. The cache is
process-local: after a restart, a member's DM session must be observed again
before this path is available. For `/skdtestpush`, run the command from the
channel DM after enabling the shim so that the current DirectMessage event has
populated the cache. If the same member is observed with multiple DM session
IDs, the target is considered ambiguous and the send fails closed; it will not
fall back to C2C. Ordinary C2C sessions that have not been observed as channel
DMs continue through AstrBot's original send method. A saved channel-DM UMO that
has not been observed since restart is indistinguishable from an ordinary friend
UMO and will retain the original C2C behavior until a DirectMessage is observed.

Only plain text and Markdown chains are supported. Rich-media chains are rejected
rather than sent through C2C-specific upload logic. The shim makes exactly one
`post_dms` API attempt (no retries or Markdown fallback) to avoid unexpectedly
consuming the channel-DM active-send quota. A successful patch check is not a
guarantee that QQ will accept a send: the gateway must be online and QQ's active
message limits still apply.

Consumers can use `is_channel_dm_push_active(context, platform_id)` as a
capability gate. It reports that the runtime shim is installed; it does not
change consumer policy. For example, a reminder plugin that deliberately filters
channel DMs must separately opt in before it will send reminders there.
## CS Match migration

See [`CS_MIGRATION.md`](CS_MIGRATION.md) for the concrete changes required in
`astrbot_plugin_cs_match`: register a prefix route, use `ctx.ack_once()`, and
remove its direct callback/intent patching.

## Local tests

```powershell
python -m unittest discover -s tests -v
```
