# CS Match migration to the shared QQ interaction bridge

This guide describes the consumer-side change only. Keep the current CS bridge
as the owner until this migration is complete; **do not put a CS-owned platform
ID in `qqbotfix.managed_platform_ids` while the old CS patch is still enabled**.
The CS plugin file is not changed by this package.

## 1. Replace patch bookkeeping with route tokens

In `CSMatch.__init__`, remove `_qq_interaction_patches`. Keep the existing
`_qq_interaction_seen` deduplication map and add:

```python
self._qq_interaction_registrations: dict[str, Any] = {}
```

Remove `_install_qq_interaction_bridges()` and
`_restore_qq_interaction_bridges()` after replacing their call sites. In
`initialize()`, register routes for QQ instances that are already present. In
`_on_qq_platform_loaded()`, register again; the per-instance token map makes
this idempotent and catches adapters created after plugin initialization.

A concrete registration helper can follow this shape:

```python
def _register_qq_interaction_routes(self) -> None:
    try:
        from data.plugins.astrbot_plugin_qqbotfix.main import (
            register_interaction_handler,
        )
    except ImportError as exc:
        logger.warning(f"[cs_match] QQ interaction bridge unavailable: {exc}")
        return

    for adapter in self._qq_platform_instances():
        platform_name = self._qq_adapter_name(adapter)
        if platform_name not in {"qq_official", "qq_official_webhook"}:
            continue
        try:
            platform_id = str(adapter.meta().id or "").strip()
        except Exception:
            platform_id = ""
        if not platform_id or platform_id in self._qq_interaction_registrations:
            continue

        async def handler(ctx, plugin=self):
            await plugin._handle_qq_interaction(ctx)

        try:
            token = register_interaction_handler(
                self.context,
                handler,
                platform_ids={platform_id},
                button_data_prefix=self._QQ_INTERACTION_PREFIX,
                ack_mode="manual",
                ack_deadline=3.0,
                handler_timeout=60.0,
                fallback_ack_code=1,
            )
        except Exception as exc:
            logger.warning(
                f"[cs_match] cannot register QQ interaction route "
                f"for {platform_id}: {exc}"
            )
            continue
        self._qq_interaction_registrations[platform_id] = token
```

Keep the existing `@filter.on_platform_loaded()` hook, but change its body to
call `_register_qq_interaction_routes()` rather than patching clients. Also call
the helper from `initialize()` for plugin reloads after platforms already
exist. Registration is synchronous and does not itself modify BotPy.

In `terminate()`, unregister the consumer's tokens instead of restoring client
callbacks or changing intents:

```python
for token in self._qq_interaction_registrations.values():
    token.unregister()
self._qq_interaction_registrations.clear()
self._qq_interaction_seen.clear()
```

## 2. Make the existing handler consume `InteractionContext`

Change the handler signature from five raw arguments to one context:

```python
async def _handle_qq_interaction(self, ctx) -> None:
    interaction = ctx.interaction
    adapter = ctx.adapter
    client = ctx.client
    platform_id = ctx.platform_id
    platform_name = adapter.meta().name
    button_data = ctx.button_data or ""
    # Keep the existing command allowlist, dedupe, message construction,
    # create_event, and commit_event logic below.
```

Use `ctx.interaction_id` where the old code reread the ID from BotPy. Keep
`_qq_interaction_astrbot_message()` and the existing business validation.

Replace direct calls to `client.api.on_interaction_result(...)` with:

```python
async def _qq_ack_interaction(self, ctx, code: int) -> bool:
    try:
        return await ctx.ack_once(code)
    except Exception as exc:
        logger.warning(f"[cs_match] QQ interaction ACK failed: {exc}")
        return False
```

Then update the existing branches without changing their codes:

- invalid/unknown CS command: `await self._qq_ack_interaction(ctx, 4)`;
- duplicate interaction ID: `await self._qq_ack_interaction(ctx, 3)`;
- successful event construction: `acknowledged = await self._qq_ack_interaction(ctx, 0)` **before** `commit_event(event)`;
- exception before ACK: try `await self._qq_ack_interaction(ctx, 1)`.

Calling `ack_once()` again after an earlier attempt is safe: it returns `False`
and never makes a second BotPy API request. The bridge also supplies code `1`
if the handler exits or raises before attempting its own ACK. If the CS handler
already ACKed, the bridge will not send another code on error or timeout.

## 3. Decide whether to send type-1 or type-2 keyboards by instance

Replace `_qq_interaction_enabled_for_event()`'s local patch-dictionary check
with the bridge status API:

```python
def _qq_interaction_enabled_for_event(self, event: AstrMessageEvent) -> bool:
    try:
        from data.plugins.astrbot_plugin_qqbotfix.main import (
            is_interaction_bridge_active,
        )
        platform_id = str(event.get_platform_id())
        return (
            platform_id in self._qq_interaction_registrations
            and is_interaction_bridge_active(self.context, platform_id)
        )
    except Exception:
        return False
```

Keep the existing type-2 fallback when this returns `False`.

## 4. Assign instance ownership and verify startup timing

After the consumer migration is deployed, add the intended CS platform instance
IDs to `qqbotfix.managed_platform_ids`. CS may register routes for all QQ
instances; the bridge's allowlist decides which instances it owns and patches.
An ID omitted from that list stays under its existing owner and CS should use
the type-2 fallback there.

A baseline callback supplied by AstrBot itself — a bound method resolved through
the client's class — is not an owner in this sense. The bridge wraps it and
chains every unclaimed interaction back to it, so the core keeps handling its
own buttons on the same instances. Only a plain function installed into the
client's instance dictionary by another plugin blocks the patch.

Test with the actual BotPy client, not only a fake object:

1. Confirm the configured ID matches `adapter.meta().id`.
2. Confirm `client.intents` contains interaction **before** the Gateway session
   is created. The bridge warning means a platform restart is required.
3. Click a CS type-1 button; verify exactly one ACK with the expected code and
   one committed AstrBot event.
4. Click a button that belongs to no registered route and verify it reaches the
   core callback instead (the bridge logs the chained callback's name at mount
   time, and "claimed=False" for that interaction).
5. Reload CS only, bridge only, and both in each plugin order. Confirm CS
   unregisters only its route and the bridge callback remains singly installed.
6. Disconnect/reconnect the WS adapter and click again to verify the Gateway
   re-subscribed to the interaction intent.
