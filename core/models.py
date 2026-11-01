from __future__ import annotations

import asyncio
import inspect
import math
from dataclasses import dataclass, field
from typing import Any


def _get(value: Any, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


class InteractionAcknowledger:
    """Single-attempt ACK controller owned by the bridge for one interaction."""

    def __init__(
        self,
        client: Any,
        interaction_id: str,
        timeout: float,
    ) -> None:
        self._client = client
        self._interaction_id = interaction_id
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("ACK timeout must be a positive finite number")
        self._timeout = max(0.1, timeout)
        self._lock = asyncio.Lock()
        self._attempted = False
        self._acknowledged = False
        self._attempt_event = asyncio.Event()
        self._error: Exception | None = None

    @property
    def attempted(self) -> bool:
        return self._attempted

    @property
    def acknowledged(self) -> bool:
        return self._acknowledged

    @property
    def error(self) -> Exception | None:
        return self._error

    async def wait_attempted(self) -> None:
        await self._attempt_event.wait()

    async def ack_once(self, code: int = 0) -> bool:
        if isinstance(code, bool) or not isinstance(code, int) or code not in range(6):
            raise ValueError("ACK code must be an integer from 0 to 5")
        async with self._lock:
            if self._attempted:
                return False
            # Reserve the one allowed attempt before awaiting network I/O. Even
            # a timed-out request may have reached QQ, so never retry it here.
            self._attempted = True
            self._attempt_event.set()
            api = getattr(self._client, "api", None)
            callback = next(
                (
                    candidate
                    for name in (
                        "on_interaction_result",
                        "on_interactionresult",
                        "oninteractionresult",
                    )
                    if callable(candidate := getattr(api, name, None))
                ),
                None,
            )
            if callback is None:
                error = RuntimeError("client API has no interaction-result method")
                self._error = error
                raise error
            try:
                result = callback(self._interaction_id, code)
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, timeout=self._timeout)
                self._acknowledged = True
                return True
            except Exception as exc:
                self._error = exc
                raise


@dataclass(frozen=True, slots=True)
class InteractionContext:
    """Normalized interaction envelope exposed to registered consumers."""

    adapter: Any
    client: Any
    interaction: Any
    platform_id: str
    interaction_id: str | None
    event_id: str | None
    scene: Any
    button_id: str | None
    button_data: str | None
    message_id: str | None
    guild_id: str | None
    channel_id: str | None
    user_openid: str | None
    group_openid: str | None
    group_member_openid: str | None
    _acknowledger: InteractionAcknowledger | None = field(
        default=None, repr=False, compare=False
    )

    @classmethod
    def from_interaction(
        cls,
        adapter: Any,
        client: Any,
        interaction: Any,
        *,
        platform_id: str,
        acknowledger: InteractionAcknowledger | None = None,
    ) -> "InteractionContext":
        data = _get(interaction, "data")
        resolved = _get(data, "resolved")
        return cls(
            adapter=adapter,
            client=client,
            interaction=interaction,
            platform_id=platform_id,
            interaction_id=_optional_str(_get(interaction, "id")),
            event_id=_optional_str(_get(interaction, "event_id")),
            scene=_get(interaction, "scene"),
            button_id=_optional_str(_get(resolved, "button_id")),
            button_data=_optional_str(_get(resolved, "button_data")),
            message_id=_optional_str(_get(resolved, "message_id")),
            guild_id=_optional_str(_get(interaction, "guild_id")),
            channel_id=_optional_str(_get(interaction, "channel_id")),
            user_openid=_optional_str(_get(interaction, "user_openid")),
            group_openid=_optional_str(_get(interaction, "group_openid")),
            group_member_openid=_optional_str(
                _get(interaction, "group_member_openid")
            ),
            _acknowledger=acknowledger,
        )

    @property
    def user_id(self) -> str | None:
        """Best available actor identifier across C2C and group interactions."""
        return self.user_openid or self.group_member_openid

    @property
    def ack_attempted(self) -> bool:
        return bool(self._acknowledger and self._acknowledger.attempted)

    @property
    def acknowledged(self) -> bool:
        return bool(self._acknowledger and self._acknowledger.acknowledged)

    async def ack_once(self, code: int = 0) -> bool:
        """Attempt one ACK. Later calls are no-ops and return ``False``."""
        if self._acknowledger is None:
            raise RuntimeError("this InteractionContext has no ACK controller")
        return await self._acknowledger.ack_once(code)
