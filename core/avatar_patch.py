from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import OrderedDict
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from functools import wraps
from typing import Any

LOGGER = logging.getLogger("astrbot_plugin_qqbotfix")
SUPPORTED_ADAPTERS = {"qq_official", "qq_official_webhook"}
COMMIT_METHOD_NAME = "commit_event"
DIRECT_CALLBACK_NAME = "on_direct_message_create"
LOOKUP_TIMEOUT = 3.0
CACHE_TTL = 3600.0
NEGATIVE_CACHE_TTL = 60.0
CACHE_MAX_ENTRIES = 1024
MAX_INFLIGHT_LOOKUPS = 32


@dataclass(slots=True)
class _DirectMessagePatch:
    client: Any
    platform_id: str
    had_instance_attr: bool = False
    original_attr: Any = None
    original_callback: Any = None
    wrapper: Any = None
    active: bool = True
    installed: bool = False
    warned_lookup_failure: bool = False
    cache: OrderedDict[tuple[str, str], tuple[float, str | None]] = field(
        default_factory=OrderedDict
    )
    inflight: dict[tuple[str, str], asyncio.Task[str | None]] = field(default_factory=dict)


@dataclass(slots=True)
class _PatchRecord:
    adapter: Any
    platform_id: str
    had_instance_attr: bool
    original_attr: Any
    wrapper: Any = None
    active: bool = True
    warned_copy_failure: bool = False
    direct: _DirectMessagePatch | None = None


class SenderAvatarPatch:
    """Enrich channel DMs before parsing, then fill sender.avatar before delivery.

    QQ's WS and Webhook paths call a shared static parser by class name, so an
    instance-level parser replacement would be bypassed. Wrapping commit_event
    instead keeps the patch restricted to managed adapter instances without
    replacing the global parser, MessageMember, or interaction callbacks.
    """

    def __init__(self, *, logger: logging.Logger | Any = LOGGER) -> None:
        self.logger = logger
        self._patches: dict[int, _PatchRecord] = {}
        self._clock = time.monotonic

    def patch_adapter(self, adapter: Any, managed_platform_ids: set[str]) -> bool:
        try:
            meta = adapter.meta()
        except Exception:
            return False
        name = _read(meta, "name")
        platform_id = str(_read(meta, "id") or "").strip()
        if name not in SUPPORTED_ADAPTERS or platform_id not in managed_platform_ids:
            return False

        existing = self._patches.get(id(adapter))
        if existing is not None:
            if not existing.active:
                return False
            self._install_direct_callback(existing)
            return getattr(adapter, COMMIT_METHOD_NAME, None) is existing.wrapper

        original_commit = getattr(adapter, COMMIT_METHOD_NAME, None)
        if not callable(original_commit):
            self.logger.warning(
                f"[{platform_id}] sender-avatar patch skipped: commit_event unavailable"
            )
            return False
        owner = getattr(original_commit, "__qqbotfix_avatar_patch__", None)
        if owner is not None and getattr(owner, "active", True):
            self.logger.warning(
                f"[{platform_id}] sender-avatar patch already owned; duplicate patch refused"
            )
            return False
        try:
            instance_attrs = vars(adapter)
        except TypeError:
            self.logger.warning(
                f"[{platform_id}] sender-avatar patch skipped: adapter is not patchable"
            )
            return False
        record = _PatchRecord(
            adapter=adapter,
            platform_id=platform_id,
            had_instance_attr=COMMIT_METHOD_NAME in instance_attrs,
            original_attr=instance_attrs.get(COMMIT_METHOD_NAME),
        )

        if inspect.iscoroutinefunction(original_commit):
            @wraps(original_commit)
            async def commit_wrapper(event: Any, *args: Any, **kwargs: Any) -> Any:
                if record.active:
                    self._fill_sender_avatar(event, record)
                return await original_commit(event, *args, **kwargs)
        else:
            @wraps(original_commit)
            def commit_wrapper(event: Any, *args: Any, **kwargs: Any) -> Any:
                if record.active:
                    self._fill_sender_avatar(event, record)
                return original_commit(event, *args, **kwargs)

        commit_wrapper.__qqbotfix_avatar_patch__ = record
        record.wrapper = commit_wrapper
        try:
            setattr(adapter, COMMIT_METHOD_NAME, commit_wrapper)
        except Exception:
            record.active = False
            self.logger.exception(f"[{platform_id}] unable to install sender-avatar patch")
            return False
        self._patches[id(adapter)] = record
        self._install_direct_callback(record)
        self.logger.info(f"[{platform_id}] sender-avatar compatibility patch installed")
        return True

    def patch_platforms(self, adapters: Any, managed_platform_ids: set[str]) -> int:
        if isinstance(adapters, Mapping):
            adapters = adapters.values()
        adapters = tuple(adapters or ())
        live_ids = {id(adapter) for adapter in adapters}
        for key, record in list(self._patches.items()):
            if key not in live_ids or record.platform_id not in managed_platform_ids:
                self._unpatch(record)
                del self._patches[key]
        patched = 0
        for adapter in adapters:
            try:
                if self.patch_adapter(adapter, managed_platform_ids):
                    patched += 1
            except Exception:
                self.logger.exception("unexpected error while patching sender avatars")
        return patched

    def _install_direct_callback(self, record: _PatchRecord) -> None:
        client = getattr(record.adapter, "client", None)
        callback = getattr(client, DIRECT_CALLBACK_NAME, None)
        if not callable(callback):
            return
        direct = record.direct
        if direct is not None and direct.client is not client:
            self._disable_direct_callback(direct)
            record.direct = direct = None
        if direct is not None and callback is direct.wrapper:
            direct.installed = True
            return
        owner = getattr(callback, "__qqbotfix_avatar_dm_patch__", None)
        if owner is not None and getattr(owner, "active", True):
            # This may be our callback retained by a later third-party wrapper.
            # Never wrap it again or overwrite an independent active owner.
            return
        try:
            attrs = vars(client)
        except TypeError:
            return
        if direct is None:
            direct = _DirectMessagePatch(client=client, platform_id=record.platform_id)
            record.direct = direct
        had_instance_attr = DIRECT_CALLBACK_NAME in attrs
        original_attr = attrs.get(DIRECT_CALLBACK_NAME)
        if not (
            direct.wrapper is not None
            and direct.original_callback == callback
            and direct.had_instance_attr == had_instance_attr
            and direct.original_attr is original_attr
        ):
            direct.had_instance_attr = had_instance_attr
            direct.original_attr = original_attr
            direct.original_callback = callback

            @wraps(callback)
            async def direct_wrapper(message: Any, *args: Any, **kwargs: Any) -> Any:
                if direct.active:
                    try:
                        await self._enrich_direct_message(message, direct)
                    except Exception as exc:
                        self._warn_lookup_failure(direct, exc)
                result = callback(message, *args, **kwargs)
                return await result if inspect.isawaitable(result) else result

            direct_wrapper.__qqbotfix_avatar_dm_patch__ = direct
            direct.wrapper = direct_wrapper
        try:
            setattr(client, DIRECT_CALLBACK_NAME, direct.wrapper)
            direct.installed = True
        except Exception as exc:
            self._warn_lookup_failure(direct, exc)

    def suspend_direct_callbacks(self) -> None:
        """Temporarily unwrap DMs so the channel-DM bridge can reconcile ownership.

        Retain the lookup cache and pending requests. patch_platforms() reattaches
        outside the new underlying callback without stacking wrappers.
        """
        for record in self._patches.values():
            direct = record.direct
            if direct is not None and getattr(
                direct.client, DIRECT_CALLBACK_NAME, None
            ) is direct.wrapper:
                self._restore_direct_callback(direct)

    async def _enrich_direct_message(
        self, message: Any, direct: _DirectMessagePatch
    ) -> None:
        author = _read(message, "author")
        if author is None or _avatar_url(_read(author, "avatar")) is not None:
            return
        raw_data = _read(message, "raw_data")
        raw_author = _read(raw_data, "author")
        avatar = _avatar_url(_read(raw_author, "avatar"))
        if avatar is None:
            # guild_id on a DirectMessage is the DM session, NOT the source guild.
            guild_id = _identifier(_read(message, "src_guild_id")) or _identifier(
                _read(raw_data, "src_guild_id")
            )
            user_id = _identifier(_read(author, "id")) or _identifier(
                _read(raw_author, "id")
            )
            if guild_id is None or user_id is None:
                return
            # Resolve at dispatch time: BotPy may attach its API after plugin init.
            api = getattr(direct.client, "api", None) or _read(message, "_api")
            endpoint = getattr(api, "get_guild_member", None)
            if not callable(endpoint):
                return
            avatar = await self._cached_member_avatar(
                direct, endpoint, (guild_id, user_id)
            )
        if not direct.active or avatar is None or _avatar_url(_read(author, "avatar")):
            return
        _write(author, "avatar", avatar)
        if isinstance(raw_data, MutableMapping):
            if not isinstance(raw_author, MutableMapping):
                raw_author = dict(raw_author) if isinstance(raw_author, Mapping) else {}
                raw_data["author"] = raw_author
            raw_author["avatar"] = avatar

    async def _cached_member_avatar(
        self, direct: _DirectMessagePatch, endpoint: Any, key: tuple[str, str]
    ) -> str | None:
        cached = direct.cache.get(key)
        if cached is not None:
            expires, avatar = cached
            if expires > self._clock():
                direct.cache.move_to_end(key)
                return avatar
            del direct.cache[key]
        task = direct.inflight.get(key)
        if task is None:
            if len(direct.inflight) >= MAX_INFLIGHT_LOOKUPS:
                return None
            task = asyncio.create_task(self._fetch_member_avatar(direct, endpoint, key))
            direct.inflight[key] = task

            def remove_pending(done: asyncio.Task) -> None:
                if direct.inflight.get(key) is done:
                    del direct.inflight[key]

            task.add_done_callback(remove_pending)
        try:
            # Cancellation of one message must not cancel a shared lookup.
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            cancelling = getattr(current, "cancelling", lambda: 0)()
            if not direct.active and not cancelling:
                # Plugin unload cancelled only our lookup, not this message.
                return None
            raise

    async def _fetch_member_avatar(
        self, direct: _DirectMessagePatch, endpoint: Any, key: tuple[str, str]
    ) -> str | None:
        avatar = None
        try:
            async def fetch() -> Any:
                result = endpoint(guild_id=key[0], user_id=key[1])
                return await result if inspect.isawaitable(result) else result

            member = await asyncio.wait_for(fetch(), timeout=LOOKUP_TIMEOUT)
            avatar = _avatar_url(_read(_read(member, "user"), "avatar"))
        except Exception as exc:
            self._warn_lookup_failure(direct, exc)
        if direct.active:
            ttl = CACHE_TTL if avatar is not None else NEGATIVE_CACHE_TTL
            direct.cache[key] = (self._clock() + ttl, avatar)
            direct.cache.move_to_end(key)
            while len(direct.cache) > CACHE_MAX_ENTRIES:
                direct.cache.popitem(last=False)
        return avatar

    def _warn_lookup_failure(self, direct: _DirectMessagePatch, exc: Exception) -> None:
        if not direct.warned_lookup_failure:
            direct.warned_lookup_failure = True
            self.logger.warning(
                f"[{direct.platform_id}] channel-DM avatar enrichment skipped "
                f"({type(exc).__name__}); original message handling unchanged"
            )

    def _restore_direct_callback(self, direct: _DirectMessagePatch) -> None:
        if not direct.installed:
            return
        try:
            if getattr(direct.client, DIRECT_CALLBACK_NAME, None) is direct.wrapper:
                if direct.had_instance_attr:
                    setattr(direct.client, DIRECT_CALLBACK_NAME, direct.original_attr)
                else:
                    delattr(direct.client, DIRECT_CALLBACK_NAME)
                direct.installed = False
            else:
                self.logger.warning(
                    f"[{direct.platform_id}] DirectMessage callback changed after avatar patch; "
                    "new owner retained"
                )
        except Exception:
            self.logger.exception(
                f"[{direct.platform_id}] unable to restore DirectMessage avatar callback"
            )

    def _disable_direct_callback(self, direct: _DirectMessagePatch) -> None:
        direct.active = False
        self._restore_direct_callback(direct)
        for task in direct.inflight.values():
            task.cancel()
        direct.inflight.clear()
        direct.cache.clear()

    def _fill_sender_avatar(self, event: Any, record: _PatchRecord) -> None:
        try:
            message = _read(event, "message_obj")
            sender = _read(message, "sender")
            if sender is None:
                return
            existing_avatar = _read(sender, "avatar")
            if existing_avatar and (
                not isinstance(existing_avatar, str) or existing_avatar.strip()
            ):
                # Preserve a native/future core field or another plugin's value.
                return
            raw_message = _read(message, "raw_message")
            avatar = _read(_read(raw_message, "author"), "avatar")
            if not isinstance(avatar, str) or not avatar.strip():
                return
            if isinstance(sender, MutableMapping):
                sender["avatar"] = avatar.strip()
            else:
                setattr(sender, "avatar", avatar.strip())
        except Exception as exc:
            # Missing fields, slots, frozen models, or read-only properties must
            # never prevent the original event from reaching the event queue.
            if not record.warned_copy_failure:
                record.warned_copy_failure = True
                self.logger.warning(
                    f"[{record.platform_id}] sender-avatar copy skipped "
                    f"({type(exc).__name__}); original event delivery unchanged"
                )

    def _unpatch(self, record: _PatchRecord) -> None:
        # A later plugin may retain this wrapper in its own closure. Deactivate
        # it even when we cannot safely remove it without overwriting that plugin.
        record.active = False
        if record.direct is not None:
            self._disable_direct_callback(record.direct)
        try:
            if getattr(record.adapter, COMMIT_METHOD_NAME, None) is record.wrapper:
                if not record.had_instance_attr:
                    delattr(record.adapter, COMMIT_METHOD_NAME)
                else:
                    setattr(record.adapter, COMMIT_METHOD_NAME, record.original_attr)
            else:
                self.logger.warning(
                    f"[{record.platform_id}] commit_event changed after avatar patch; "
                    "new owner retained and old avatar wrapper disabled"
                )
        except Exception:
            self.logger.exception(
                f"[{record.platform_id}] unable to restore commit_event; avatar wrapper disabled"
            )

    def unpatch_all(self) -> None:
        for record in list(self._patches.values()):
            self._unpatch(record)
        self._patches.clear()


def _read(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _write(obj: Any, key: str, value: Any) -> None:
    if isinstance(obj, MutableMapping):
        obj[key] = value
    else:
        setattr(obj, key, value)


def _avatar_url(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _identifier(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value).strip() or None
