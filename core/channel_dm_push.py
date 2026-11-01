from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger("astrbot_plugin_qqbotfix")
SUPPORTED_ADAPTERS = {"qq_official"}
DIRECT_CALLBACK_NAME = "on_direct_message_create"
SEND_METHOD_NAME = "send_by_session"
_MISSING = object()


class ChannelDMPushError(RuntimeError):
    """A channel-DM send was intentionally stopped rather than misrouted to C2C."""


@dataclass(frozen=True, slots=True)
class SessionResolution:
    status: str
    session_id: str | None = None


class DirectMessageSessionCache:
    """Process-local mapping from QQ member ID to observed DirectMessage session IDs."""

    def __init__(self) -> None:
        # A member may have more than one QQ channel-DM session. Preserve that
        # fact and fail closed instead of silently choosing the most recent one.
        self._sessions: dict[str, set[str] | None] = {}

    def observe(self, member_id: Any, dm_session_id: Any) -> None:
        member = _optional_id(member_id)
        if member is None:
            return
        dm_session = _optional_id(dm_session_id)
        current = self._sessions.get(member, set())
        if current is None:
            return
        if dm_session is None:
            # Record that the member is known to use channel DMs, but don't
            # invent or infer the missing conversation ID.
            self._sessions.setdefault(member, set())
            return
        current.add(dm_session)
        if len(current) > 1:
            self._sessions[member] = None
        else:
            self._sessions[member] = current

    def resolve(self, member_id: Any) -> SessionResolution:
        member = _optional_id(member_id)
        if member is None or member not in self._sessions:
            return SessionResolution("unknown")
        sessions = self._sessions[member]
        if sessions is None or len(sessions) > 1:
            return SessionResolution("ambiguous")
        if not sessions:
            return SessionResolution("missing")
        return SessionResolution("unique", next(iter(sessions)))


@dataclass(slots=True)
class _PatchRecord:
    adapter: Any
    client: Any
    platform_id: str
    original_send: Any
    send_wrapper: Any
    had_instance_callback: bool
    original_callback: Any
    callback_wrapper: Any


class ChannelDMPushBridge:
    """Route observed channel-DM UMO sends to BotPy's post_dms API.

    This intentionally patches only the public adapter send method and the
    BotPy DirectMessage callback. It does not touch interaction callbacks or
    AstrBot core files. Text/Markdown sends use exactly one HTTP API attempt;
    rich media is rejected rather than going through the C2C upload path.
    """

    def __init__(self, *, logger: logging.Logger | Any = LOGGER) -> None:
        self.logger = logger
        self._patches: dict[int, _PatchRecord] = {}
        self._sessions: dict[int, DirectMessageSessionCache] = {}

    def patch_adapter(self, adapter: Any, managed_platform_ids: set[str]) -> bool:
        name = _adapter_name(adapter)
        if name not in SUPPORTED_ADAPTERS:
            return False
        platform_id = _platform_id(adapter)
        if not platform_id or platform_id not in managed_platform_ids:
            return False

        existing = self._patches.get(id(adapter))
        if existing is not None and existing.adapter is adapter:
            return self.is_active(platform_id)

        client = getattr(adapter, "client", None)
        if client is None or getattr(client, "api", None) is None:
            self.logger.warning(
                f"[{platform_id}] channel-DM push bridge skipped: BotPy client/API unavailable"
            )
            return False
        api, guild_keyword = _post_dms_endpoint(client.api)
        if api is None or guild_keyword is None:
            self.logger.warning(
                f"[{platform_id}] channel-DM push bridge skipped: compatible post_dms API not found"
            )
            return False

        original_send = getattr(adapter, SEND_METHOD_NAME, None)
        if not callable(original_send):
            self.logger.warning(
                f"[{platform_id}] channel-DM push bridge skipped: send_by_session unavailable"
            )
            return False
        adapter_dict = _instance_dict(adapter)
        if adapter_dict is None:
            self.logger.warning(
                f"[{platform_id}] channel-DM push bridge skipped: adapter is not patchable"
            )
            return False
        existing_send = adapter_dict.get(SEND_METHOD_NAME, _MISSING)
        if existing_send is not _MISSING:
            self.logger.error(
                f"[{platform_id}] send_by_session already has an instance-level owner; "
                "channel-DM send patch refused"
            )
            return False

        callback = getattr(client, DIRECT_CALLBACK_NAME, None)
        if not callable(callback):
            self.logger.warning(
                f"[{platform_id}] channel-DM push bridge skipped: DirectMessage callback unavailable"
            )
            return False
        client_dict = _instance_dict(client)
        if client_dict is None:
            self.logger.warning(
                f"[{platform_id}] channel-DM push bridge skipped: BotPy client is not patchable"
            )
            return False
        existing_callback = client_dict.get(DIRECT_CALLBACK_NAME, _MISSING)
        if existing_callback is not _MISSING:
            self.logger.error(
                f"[{platform_id}] {DIRECT_CALLBACK_NAME} already has an instance-level owner; "
                "channel-DM session observation refused"
            )
            return False

        sessions = DirectMessageSessionCache()
        had_instance_callback = existing_callback is not _MISSING
        original_callback = existing_callback if had_instance_callback else _MISSING

        async def callback_wrapper(message: Any, *args: Any, **kwargs: Any) -> Any:
            author = _field(message, "author")
            sessions.observe(_field(author, "id"), _field(message, "guild_id"))
            result = callback(message, *args, **kwargs)
            return await result if inspect.isawaitable(result) else result

        async def send_wrapper(session: Any, message_chain: Any, *args: Any, **kwargs: Any) -> Any:
            if not _is_friend_message(_field(session, "message_type")):
                return await _await_result(original_send(session, message_chain, *args, **kwargs))

            member_id = _field(session, "session_id")
            resolution = sessions.resolve(member_id)
            if resolution.status == "unknown":
                # Ordinary C2C sessions have never appeared as DirectMessage
                # events, so keep their original route untouched.
                return await _await_result(original_send(session, message_chain, *args, **kwargs))
            if resolution.status != "unique" or resolution.session_id is None:
                self.logger.error(
                    f"[{platform_id}] channel-DM send blocked: observed session mapping is "
                    f"{resolution.status}; refusing C2C fallback"
                )
                raise ChannelDMPushError(
                    f"QQ channel-DM target is {resolution.status}; send was not attempted"
                )

            payload = _text_payload(adapter, message_chain)
            # Deliberately one request: retries / Markdown fallback can consume
            # the small daily active-message allowance more than once.
            result = api(**{guild_keyword: resolution.session_id}, **payload)
            return await _await_result(result)

        setattr(callback_wrapper, "__qqbotfix_channel_dm_bridge__", True)
        setattr(callback_wrapper, "__qqbotfix_platform_id__", platform_id)
        setattr(send_wrapper, "__qqbotfix_channel_dm_bridge__", True)
        setattr(send_wrapper, "__qqbotfix_platform_id__", platform_id)

        try:
            setattr(client, DIRECT_CALLBACK_NAME, callback_wrapper)
            setattr(adapter, SEND_METHOD_NAME, send_wrapper)
        except Exception as exc:
            # Restore only our own callback wrapper if the second patch failed.
            if getattr(client, DIRECT_CALLBACK_NAME, None) is callback_wrapper:
                _restore_instance_attr(
                    client,
                    DIRECT_CALLBACK_NAME,
                    had_instance_callback,
                    original_callback,
                )
            self.logger.warning(
                f"[{platform_id}] unable to install channel-DM send patch: {exc}"
            )
            return False

        self._patches[id(adapter)] = _PatchRecord(
            adapter=adapter,
            client=client,
            platform_id=platform_id,
            original_send=existing_send if existing_send is not _MISSING else _MISSING,
            send_wrapper=send_wrapper,
            had_instance_callback=had_instance_callback,
            original_callback=original_callback,
            callback_wrapper=callback_wrapper,
        )
        self._sessions[id(adapter)] = sessions
        self.logger.info(
            f"[{platform_id}] channel-DM proactive text/Markdown send patch installed"
        )
        return True

    def patch_platforms(self, adapters: Any, managed_platform_ids: set[str]) -> int:
        if isinstance(adapters, dict):
            adapters = adapters.values()
        patched = 0
        for adapter in tuple(adapters or ()):
            try:
                if self.patch_adapter(adapter, managed_platform_ids):
                    patched += 1
            except Exception:
                self.logger.exception("unexpected error while patching channel-DM sender")
        return patched

    def is_active(self, platform_id: str) -> bool:
        return any(
            record.platform_id == str(platform_id)
            and getattr(record.adapter, SEND_METHOD_NAME, None) is record.send_wrapper
            and getattr(record.client, DIRECT_CALLBACK_NAME, None) is record.callback_wrapper
            for record in self._patches.values()
        )

    def unpatch_all(self) -> None:
        for record in list(self._patches.values()):
            try:
                if getattr(record.adapter, SEND_METHOD_NAME, None) is record.send_wrapper:
                    if record.original_send is _MISSING:
                        try:
                            delattr(record.adapter, SEND_METHOD_NAME)
                        except AttributeError:
                            pass
                    else:
                        setattr(record.adapter, SEND_METHOD_NAME, record.original_send)
                else:
                    self.logger.warning(
                        f"[{record.platform_id}] send_by_session ownership changed; "
                        "leaving the current method untouched"
                    )
            except Exception:
                self.logger.exception(
                    f"[{record.platform_id}] failed to restore send_by_session"
                )

            try:
                if getattr(record.client, DIRECT_CALLBACK_NAME, None) is record.callback_wrapper:
                    _restore_instance_attr(
                        record.client,
                        DIRECT_CALLBACK_NAME,
                        record.had_instance_callback,
                        record.original_callback,
                    )
                else:
                    self.logger.warning(
                        f"[{record.platform_id}] DirectMessage callback ownership changed; "
                        "leaving the current callback untouched"
                    )
            except Exception:
                self.logger.exception(
                    f"[{record.platform_id}] failed to restore DirectMessage callback"
                )

        self._patches.clear()
        self._sessions.clear()


def _text_payload(adapter: Any, message_chain: Any) -> dict[str, Any]:
    text = _extract_plain_text(message_chain)
    if not text:
        raise ChannelDMPushError("empty text message; channel-DM send was not attempted")

    use_markdown = getattr(message_chain, "use_markdown_", None)
    if use_markdown is None:
        use_markdown = getattr(adapter, "use_markdown_default", False)
    if not use_markdown:
        return {"content": text}

    markdown_type = _markdown_payload_type()
    if markdown_type is None:
        raise ChannelDMPushError(
            "BotPy MarkdownPayload type unavailable; channel-DM send was not attempted"
        )
    return {"markdown": markdown_type(content=text)}


def _extract_plain_text(message_chain: Any) -> str:
    if isinstance(message_chain, str):
        return message_chain

    components = getattr(message_chain, "chain", None)
    if components is None:
        # AstrBot may pass an already-rendered string-like value.
        if type(message_chain).__name__ in {"Plain", "PlainComponent"}:
            value = _field(message_chain, "text")
            return str(value or "")
        raise ChannelDMPushError(
            "only plain text/Markdown is supported for channel-DM proactive sends"
        )

    pieces: list[str] = []
    for component in components:
        name = type(component).__name__
        if name in {"Plain", "PlainComponent"}:
            value = _field(component, "text")
            if value is not None:
                pieces.append(str(value))
        elif isinstance(component, str):
            pieces.append(component)
        else:
            raise ChannelDMPushError(
                "channel-DM proactive send supports only plain text/Markdown; "
                f"unsupported component: {name}"
            )
    return "".join(pieces)


def _markdown_payload_type() -> Any | None:
    try:
        from botpy.types.message import MarkdownPayload  # type: ignore[import-not-found]

        return MarkdownPayload
    except (ImportError, AttributeError):
        try:
            from botpy.types import MarkdownPayload  # type: ignore[import-not-found]

            return MarkdownPayload
        except (ImportError, AttributeError):
            return None


def _post_dms_endpoint(api: Any) -> tuple[Any | None, str | None]:
    method = getattr(api, "post_dms", None)
    if not callable(method):
        method = getattr(api, "postdms", None)
    if not callable(method):
        return None, None
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return None, None

    accepts_kwargs = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )
    if not accepts_kwargs and not {"content", "markdown"}.issubset(parameters):
        return None, None
    for keyword in ("guild_id", "guildid"):
        if keyword in parameters:
            return method, keyword
    if accepts_kwargs:
        # Prefer the snake_case API used by AstrBot 4.28.x when wrappers expose
        # only **kwargs; old BotPy aliases can still declare guildid explicitly.
        return method, "guild_id"
    return None, None


def _is_friend_message(value: Any) -> bool:
    name = getattr(value, "name", None)
    if name is not None and str(name).upper() == "FRIEND_MESSAGE":
        return True
    text = str(value).upper()
    return text in {"FRIEND_MESSAGE", "MESSAGETYPE.FRIEND_MESSAGE"} or text.endswith(
        ".FRIEND_MESSAGE"
    )


def _adapter_name(adapter: Any) -> str | None:
    try:
        meta = adapter.meta()
        return _optional_id(_field(meta, "name"))
    except Exception:
        return None


def _platform_id(adapter: Any) -> str | None:
    try:
        return _optional_id(_field(adapter.meta(), "id"))
    except Exception:
        return None


def _instance_dict(instance: Any) -> dict[str, Any] | None:
    try:
        return vars(instance)
    except TypeError:
        return None


def _restore_instance_attr(
    instance: Any, name: str, had_instance_value: bool, original_value: Any
) -> None:
    if had_instance_value:
        setattr(instance, name, original_value)
    else:
        try:
            delattr(instance, name)
        except AttributeError:
            pass


async def _await_result(result: Any) -> Any:
    return await result if inspect.isawaitable(result) else result


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _optional_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
