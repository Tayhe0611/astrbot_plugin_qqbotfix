from __future__ import annotations

import logging
import math
from typing import Any, Iterable

try:
    from astrbot.api import logger as astrbot_logger
    from astrbot.api.event import filter
    from astrbot.api.star import Context, Star, register
except ImportError:  # Allows unit tests to run without an AstrBot installation.
    astrbot_logger = logging.getLogger("astrbot_plugin_qqbotfix")

    class Context:  # type: ignore[no-redef]
        pass

    class Star:  # type: ignore[no-redef]
        def __init__(self, context: Any) -> None:
            self.context = context

    class _Filter:
        @staticmethod
        def on_platform_loaded():
            return lambda method: method

    filter = _Filter()  # type: ignore[assignment]

    def register(*_args: Any, **_kwargs: Any):  # type: ignore[no-redef]
        return lambda cls: cls

try:
    from .core.avatar_patch import SenderAvatarPatch
    from .core.bridge import InteractionBridge, callback_chain_has_bridge
    from .core.channel_dm_push import ChannelDMPushBridge
    from .core.group_reply import GroupKeyboardSendError, send_group_keyboard
    from .core.models import InteractionContext
    from .core.registry import (
        InteractionRegistration,
        InteractionRegistry,
        get_shared_registry,
    )
except ImportError:  # Tests may load main.py as a top-level module.
    from core.avatar_patch import SenderAvatarPatch
    from core.bridge import InteractionBridge, callback_chain_has_bridge
    from core.channel_dm_push import ChannelDMPushBridge
    from core.group_reply import GroupKeyboardSendError, send_group_keyboard
    from core.models import InteractionContext
    from core.registry import (
        InteractionRegistration,
        InteractionRegistry,
        get_shared_registry,
    )

PLUGIN_NAME = "astrbot_plugin_qqbotfix"
PLUGIN_VERSION = "0.4.1-beta.5"


def register_interaction_handler(
    context: Context,
    handler: Any,
    *,
    platform_ids: Iterable[str] | str,
    button_data: str | None = None,
    button_data_prefix: str | None = None,
    ack_mode: str = "auto",
    ack_deadline: float = 3.0,
    handler_timeout: float = 60.0,
    fallback_ack_code: int = 1,
) -> InteractionRegistration:
    """Subscribe a consumer handler for explicit QQ platform instance IDs.

    Supply exactly one matcher: an exact ``button_data`` or a
    ``button_data_prefix``. ``ack_mode='auto'`` ACKs code 0 before invoking the
    handler. ``ack_mode='manual'`` gives the handler ``ctx.ack_once(code)``;
    the bridge sends ``fallback_ack_code`` if the handler returns, errors, or
    misses ``ack_deadline`` without attempting an ACK.
    """
    registry = _registry_for(context)
    return registry.register(
        platform_ids=platform_ids,
        handler=handler,
        button_data=button_data,
        button_data_prefix=button_data_prefix,
        ack_mode=ack_mode,  # type: ignore[arg-type]
        ack_deadline=ack_deadline,
        handler_timeout=handler_timeout,
        fallback_ack_code=fallback_ack_code,
    )


def register_button_handler(
    context: Context,
    button_data: str,
    handler: Any,
    *,
    platform_ids: Iterable[str] | str,
    ack_mode: str = "auto",
    ack_deadline: float = 3.0,
    handler_timeout: float = 60.0,
    fallback_ack_code: int = 1,
) -> InteractionRegistration:
    """Convenience API for an exact ``button_data`` match."""
    return register_interaction_handler(
        context,
        handler,
        platform_ids=platform_ids,
        button_data=button_data,
        ack_mode=ack_mode,
        ack_deadline=ack_deadline,
        handler_timeout=handler_timeout,
        fallback_ack_code=fallback_ack_code,
    )


async def sendgroupkeyboard(
    context: Context,
    *,
    platform_id: str,
    group_event: Any,
    content: str,
    msg_type: int,
    keyboard: Any | None = None,
    msg_id: str | None = None,
    event_id: str | None = None,
    msg_seq: int | None = None,
    markdown: Any | None = None,
    media: Any | None = None,
) -> Any:
    """Send one QQ group passive reply with an optional keyboard/media payload.

    Exactly one of ``msg_id`` or ``event_id`` is required and must match the
    supplied raw BotPy group event. ``group_openid`` is derived from that event,
    so a C2C openid or generic UMO/session string cannot be substituted. Call
    only while handling a live event; QQ remains authoritative on freshness.
    """
    if not isinstance(platform_id, str) or not platform_id.strip():
        raise GroupKeyboardSendError(
            "platform_id must be the exact non-empty AstrBot platform instance ID"
        )
    adapter = _get_platform(context, platform_id)
    if adapter is None:
        raise GroupKeyboardSendError(
            f"no platform adapter found for exact platform_id {platform_id!r}"
        )
    return await send_group_keyboard(
        adapter,
        platform_id=platform_id,
        group_event=group_event,
        content=content,
        msg_type=msg_type,
        keyboard=keyboard,
        msg_id=msg_id,
        event_id=event_id,
        msg_seq=msg_seq,
        markdown=markdown,
        media=media,
    )

def is_interaction_bridge_active(context: Context, platform_id: str) -> bool:
    """Return whether this exact instance retains the bridge in its callback chain."""
    adapter = _get_platform(context, str(platform_id))
    client = getattr(adapter, "client", None) if adapter is not None else None
    callback = getattr(client, "on_interaction_create", None) if client else None
    return callback_chain_has_bridge(callback, str(platform_id))


def is_channel_dm_push_active(context: Context, platform_id: str) -> bool:
    """Return whether the channel-DM proactive sender is patched on this instance."""
    adapter = _get_platform(context, str(platform_id))
    client = getattr(adapter, "client", None) if adapter is not None else None
    send_method = getattr(adapter, "send_by_session", None) if adapter is not None else None
    dm_callback = getattr(client, "on_direct_message_create", None) if client else None
    return bool(
        getattr(send_method, "__qqbotfix_channel_dm_bridge__", False)
        and getattr(send_method, "__qqbotfix_platform_id__", None) == str(platform_id)
        and getattr(dm_callback, "__qqbotfix_channel_dm_bridge__", False)
        and getattr(dm_callback, "__qqbotfix_platform_id__", None) == str(platform_id)
    )


def _registry_for(context: Any) -> InteractionRegistry:
    return get_shared_registry(context)


def _get_platform(context: Any, platform_id: str) -> Any:
    getter = getattr(context, "get_platform_inst", None)
    if callable(getter):
        try:
            found = getter(platform_id)
            if found is not None:
                return found
        except Exception:
            pass
    manager = getattr(context, "platform_manager", None)
    if manager is None:
        manager = getattr(context, "platformmanager", None)
    instances = getattr(manager, "platform_insts", None) if manager else None
    if instances is None and manager is not None:
        instances = getattr(manager, "platforminsts", None)
    if instances is None and manager is not None:
        instances = getattr(manager, "get_insts", None)
    if callable(instances):
        try:
            instances = instances()
        except Exception:
            instances = ()
    if isinstance(instances, dict):
        instances = instances.values()
    for adapter in instances or ():
        try:
            meta = adapter.meta()
            candidate = getattr(meta, "id", None)
            if candidate is None and isinstance(meta, dict):
                candidate = meta.get("id")
            if str(candidate or "") == platform_id:
                return adapter
        except Exception:
            continue
    return None


def _read_platform_ids(value: Any) -> set[str]:
    if isinstance(value, str):
        values = value.replace("，", ",").split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = value
    else:
        values = ()
    return {str(item).strip() for item in values if str(item).strip()}


@register(
    PLUGIN_NAME,
    "local",
    "为 QQ 官方适配器提供 interaction 桥接、头像兼容、群被动键盘回复及可选频道私聊文本发送",
    PLUGIN_VERSION,
    "",
)
class QQBotFix(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.config = config
        self._bridge: InteractionBridge | None = None
        self._channel_dm_bridge: ChannelDMPushBridge | None = None
        self._avatar_patch: SenderAvatarPatch | None = None
        self._warned_empty_ids = False

    def _conf(self, key: str, default: Any) -> Any:
        config = self.config
        if config is None:
            return default
        try:
            getter = getattr(config, "get", None)
            if callable(getter):
                value = getter(key, default)
            elif isinstance(config, dict):
                value = config.get(key, default)
            else:
                return default
            return default if value is None else value
        except Exception:
            return default

    def _platforms(self) -> Any:
        manager = getattr(self.context, "platform_manager", None)
        if manager is None:
            manager = getattr(self.context, "platformmanager", None)
        if manager is None:
            return ()
        instances = getattr(manager, "platform_insts", None)
        if instances is None:
            instances = getattr(manager, "platforminsts", None)
        if instances is None:
            instances = getattr(manager, "get_insts", None)
        if callable(instances):
            try:
                instances = instances()
            except Exception:
                return ()
        if isinstance(instances, dict):
            instances = instances.values()
        return instances or ()

    def _managed_platform_ids(self) -> set[str]:
        return _read_platform_ids(self._conf("managed_platform_ids", []))

    def _ensure_bridge(self) -> InteractionBridge | None:
        if not self._conf("enabled", True):
            if self._bridge is not None:
                self._bridge.unpatch_all()
                self._bridge = None
            return None
        if self._bridge is None:
            try:
                timeout = float(self._conf("ack_timeout", 3.0))
                if not math.isfinite(timeout) or timeout <= 0:
                    timeout = 3.0
            except (TypeError, ValueError):
                timeout = 3.0
            self._bridge = InteractionBridge(
                _registry_for(self.context),
                ack_timeout=timeout,
                debug_log_payload=bool(self._conf("debug_log_payload", False)),
                logger=astrbot_logger,
            )
        return self._bridge

    def _ensure_channel_dm_bridge(self) -> ChannelDMPushBridge | None:
        enabled = bool(self._conf("enabled", True)) and bool(
            self._conf("channel_dm_push_enabled", False)
        )
        if not enabled:
            if self._channel_dm_bridge is not None:
                self._channel_dm_bridge.unpatch_all()
                self._channel_dm_bridge = None
            return None
        if self._channel_dm_bridge is None:
            self._channel_dm_bridge = ChannelDMPushBridge(logger=astrbot_logger)
        return self._channel_dm_bridge

    def _ensure_avatar_patch(self) -> SenderAvatarPatch | None:
        enabled = bool(self._conf("enabled", True)) and bool(
            self._conf("avatar_patch_enabled", True)
        )
        if not enabled:
            if self._avatar_patch is not None:
                self._avatar_patch.unpatch_all()
                self._avatar_patch = None
            return None
        if self._avatar_patch is None:
            self._avatar_patch = SenderAvatarPatch(logger=astrbot_logger)
        return self._avatar_patch

    def _patch_loaded_platforms(self) -> None:
        # The channel-DM bridge claims its callback exclusively. Temporarily
        # unwrap our avatar layer before installing/removing that inner bridge.
        if self._avatar_patch is not None:
            self._avatar_patch.suspend_direct_callbacks()
        bridge = self._ensure_bridge()
        channel_dm_bridge = self._ensure_channel_dm_bridge()
        avatar_patch = self._ensure_avatar_patch()
        managed_ids = self._managed_platform_ids()
        adapters = tuple(self._platforms() or ())
        # Keep this synchronous so the interaction bit is set before the
        # platform-loaded hook yields to the scheduled Gateway startup task.
        if bridge is not None:
            bridge.patch_platforms(adapters, managed_ids)
        if channel_dm_bridge is not None:
            channel_dm_bridge.patch_platforms(adapters, managed_ids)
        if avatar_patch is not None:
            # Install outside the DM session observer so lookup finishes before
            # the original callback parses the message. Also reconcile empty IDs.
            avatar_patch.patch_platforms(adapters, managed_ids)
        if not managed_ids:
            if not self._warned_empty_ids:
                astrbot_logger.warning(
                    "[qqbotfix] no managed_platform_ids configured; no QQ client will be patched"
                )
                self._warned_empty_ids = True
            return

    async def initialize(self) -> None:
        # Supports a plugin reload after platform instances already exist.
        self._patch_loaded_platforms()

    @filter.on_platform_loaded()
    async def on_platform_loaded(self) -> None:
        self._patch_loaded_platforms()

    async def terminate(self) -> None:
        if self._avatar_patch is not None:
            self._avatar_patch.unpatch_all()
            self._avatar_patch = None
        if self._bridge is not None:
            self._bridge.unpatch_all()
            self._bridge = None
        if self._channel_dm_bridge is not None:
            self._channel_dm_bridge.unpatch_all()
            self._channel_dm_bridge = None


__all__ = [
    "GroupKeyboardSendError",
    "InteractionContext",
    "InteractionRegistration",
    "QQBotFix",
    "is_channel_dm_push_active",
    "is_interaction_bridge_active",
    "register_button_handler",
    "register_interaction_handler",
    "sendgroupkeyboard",
]
