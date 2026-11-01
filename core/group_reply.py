from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any


class GroupKeyboardSendError(ValueError):
    """Raised when a group passive-reply request is ambiguous or unsupported."""


_PARAMETER_ALIASES: dict[str, tuple[str, ...]] = {
    "group_openid": ("group_openid", "groupopenid"),
    "msg_type": ("msg_type", "msgtype"),
    "msg_id": ("msg_id", "msgid"),
    "msg_seq": ("msg_seq", "msgseq"),
    "event_id": ("event_id", "eventid"),
    "content": ("content",),
    "markdown": ("markdown",),
    "keyboard": ("keyboard",),
    "media": ("media",),
}


async def send_group_keyboard(
    adapter: Any,
    *,
    platform_id: str,
    group_event: Any,
    content: str,
    msg_type: int,
    keyboard: Any | None,
    msg_id: str | None = None,
    event_id: str | None = None,
    msg_seq: int | None = None,
    markdown: Any | None = None,
    media: Any | None = None,
) -> Any:
    """Make exactly one BotPy group-message API call, never retrying it.

    Only the WS ``qq_official`` adapter is supported. The group ID and reply ID
    are derived/verified against a raw BotPy group event, rather than accepted as
    arbitrary strings. ``keyboard=None`` is allowed only for the image-only leg
    of an explicit two-message fallback.
    """
    group_openid = _validate_target(
        adapter,
        platform_id,
        group_event,
        msg_id=msg_id,
        event_id=event_id,
    )
    _validate_payload(
        content=content,
        msg_type=msg_type,
        keyboard=keyboard,
        msg_id=msg_id,
        event_id=event_id,
        msg_seq=msg_seq,
        markdown=markdown,
        media=media,
    )

    client = getattr(adapter, "client", None)
    api = getattr(client, "api", None) if client is not None else None
    if api is None:
        raise GroupKeyboardSendError(
            "the selected qq_official adapter has no BotPy client.api"
        )

    method = getattr(api, "postgroupmessage", None)
    if not callable(method):
        method = getattr(api, "post_group_message", None)
    if not callable(method):
        raise GroupKeyboardSendError(
            "BotPy API exposes neither postgroupmessage nor post_group_message"
        )

    payload: dict[str, Any] = {
        "group_openid": group_openid,
        "content": "" if msg_type == 2 else content,
        "msg_type": msg_type,
    }
    if msg_id is not None:
        payload["msg_id"] = msg_id
        payload["msg_seq"] = msg_seq
    if event_id is not None:
        payload["event_id"] = event_id
    if markdown is not None:
        payload["markdown"] = markdown
    if keyboard is not None:
        payload["keyboard"] = keyboard
    if media is not None:
        payload["media"] = media

    kwargs = _adapt_api_parameters(method, payload)
    # Intentionally one API attempt. In particular, do not retry after a timeout:
    # QQ may have accepted the message even if the response was lost.
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _validate_target(
    adapter: Any,
    platform_id: str,
    group_event: Any,
    *,
    msg_id: str | None,
    event_id: str | None,
) -> str:
    if not isinstance(platform_id, str) or not platform_id.strip():
        raise GroupKeyboardSendError("platform_id must be a non-empty exact instance ID")

    # Derive the target from a raw BotPy group event, rather than accepting an
    # arbitrary string that could be a C2C openid or a UMO/session identifier.
    group_openid = _field(group_event, "group_openid")
    if not isinstance(group_openid, str) or not group_openid.strip():
        raise GroupKeyboardSendError(
            "group_event must be a raw group event with group_openid; UMO/session IDs are not accepted"
        )
    if group_openid != group_openid.strip():
        raise GroupKeyboardSendError("group_openid must not contain surrounding whitespace")
    if _optional_id(_field(group_event, "user_openid")) is not None:
        raise GroupKeyboardSendError("C2C events are not accepted by the group reply helper")

    source_msg_id = _event_message_id(group_event)
    source_event_id = _optional_id(_field(group_event, "event_id"))
    if msg_id is not None and source_msg_id != msg_id:
        raise GroupKeyboardSendError("msg_id does not match the supplied group event")
    if event_id is not None and source_event_id != event_id:
        raise GroupKeyboardSendError("event_id does not match the supplied group event")
    if msg_id is not None and source_msg_id is None:
        raise GroupKeyboardSendError("group_event does not expose the requested msg_id")
    if event_id is not None and source_event_id is None:
        raise GroupKeyboardSendError("group_event does not expose the requested event_id")

    meta_method = getattr(adapter, "meta", None)
    if not callable(meta_method):
        raise GroupKeyboardSendError("selected platform adapter has no meta() method")
    try:
        meta = meta_method()
    except Exception as exc:
        raise GroupKeyboardSendError("could not read the selected adapter metadata") from exc

    adapter_id = _field(meta, "id")
    adapter_name = _field(meta, "name")
    if str(adapter_id or "").strip() != platform_id:
        raise GroupKeyboardSendError(
            "platform_id does not exactly match the selected adapter instance"
        )
    if str(adapter_name or "").strip() != "qq_official":
        raise GroupKeyboardSendError(
            "group passive keyboard replies require the qq_official WS adapter"
        )
    return group_openid


def _event_message_id(group_event: Any) -> str | None:
    data = _field(group_event, "data")
    resolved = _field(data, "resolved") if data is not None else None
    resolved_id = _optional_id(_field(resolved, "message_id"))
    if resolved_id is not None:
        return resolved_id
    for name in ("msg_id", "message_id", "id"):
        value = _optional_id(_field(group_event, name))
        if value is not None:
            return value
    return None


def _validate_payload(
    *,
    content: str,
    msg_type: int,
    keyboard: Any | None,
    msg_id: str | None,
    event_id: str | None,
    msg_seq: int | None,
    markdown: Any | None,
    media: Any | None,
) -> None:
    if not isinstance(content, str):
        raise GroupKeyboardSendError("content must be a string")

    # Only Markdown accepts empty content; preserve media-message validation.
    if msg_type == 7 and not content:
        raise GroupKeyboardSendError(
            "content must be a non-empty string for media messages"
        )
    if isinstance(msg_type, bool) or msg_type not in (2, 7):
        raise GroupKeyboardSendError("msg_type must be 2 (Markdown) or 7 (media)")

    normalized_msg_id = _optional_id(msg_id)
    normalized_event_id = _optional_id(event_id)
    if (normalized_msg_id is None) == (normalized_event_id is None):
        raise GroupKeyboardSendError("supply exactly one of msg_id or event_id")
    if normalized_msg_id != msg_id or normalized_event_id != event_id:
        raise GroupKeyboardSendError("msg_id and event_id must be non-empty strings")

    if msg_id is not None:
        if msg_seq is None:
            raise GroupKeyboardSendError("msg_seq is required when replying with msg_id")
        if isinstance(msg_seq, bool) or not isinstance(msg_seq, int) or msg_seq < 1:
            raise GroupKeyboardSendError("msg_seq must be a positive integer")
    elif msg_seq is not None:
        raise GroupKeyboardSendError("msg_seq is only valid when replying with msg_id")

    if keyboard is not None and _is_empty_container(keyboard):
        raise GroupKeyboardSendError("keyboard must not be empty")

    if msg_type == 2:
        if markdown is None:
            raise GroupKeyboardSendError("msg_type=2 requires an explicit markdown payload")
        if media is not None:
            raise GroupKeyboardSendError("media is only accepted with msg_type=7")
        if keyboard is None:
            raise GroupKeyboardSendError("msg_type=2 requires a keyboard")
    else:
        if media is None or _is_empty_container(media):
            raise GroupKeyboardSendError(
                "msg_type=7 requires uploaded media (for example, file_info)"
            )
        if markdown is not None:
            raise GroupKeyboardSendError("markdown is only accepted with msg_type=2")


def _adapt_api_parameters(method: Any, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        # The documented BotPy convention uses snake_case. If a wrapper does not
        # expose a signature, make one call with that convention and propagate
        # any failure rather than probing by retrying.
        return payload

    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_kwargs:
        return payload

    kwargs: dict[str, Any] = {}
    for canonical, value in payload.items():
        target_name = next(
            (name for name in _PARAMETER_ALIASES[canonical] if name in parameters),
            None,
        )
        if target_name is None:
            raise GroupKeyboardSendError(
                f"BotPy group-message method does not accept required field {canonical!r}"
            )
        if parameters[target_name].kind is inspect.Parameter.POSITIONAL_ONLY:
            raise GroupKeyboardSendError(
                f"BotPy parameter {target_name!r} is positional-only; refusing an unsafe call"
            )
        kwargs[target_name] = value
    return kwargs


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _optional_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _is_empty_container(value: Any) -> bool:
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return not value
    return False
