from __future__ import annotations

from collections.abc import Callable, Iterable
import math
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from .models import InteractionContext

AckMode = Literal["auto", "manual"]
ButtonHandler = Callable[[InteractionContext], Any]


@dataclass(frozen=True, slots=True)
class ButtonMatcher:
    kind: Literal["exact", "prefix"]
    value: str

    def matches(self, button_data: str | None) -> bool:
        if button_data is None:
            return False
        if self.kind == "exact":
            return button_data == self.value
        return button_data.startswith(self.value)


@dataclass(slots=True)
class InteractionRoute:
    registration_id: str
    platform_ids: frozenset[str]
    matcher: ButtonMatcher
    handler: ButtonHandler
    ack_mode: AckMode
    ack_deadline: float
    handler_timeout: float
    fallback_ack_code: int


@dataclass(slots=True)
class InteractionRegistration:
    """A consumer-owned subscription. Call ``unregister`` on plugin terminate."""

    registry: "InteractionRegistry"
    registration_id: str
    active: bool = True

    @property
    def platform_ids(self) -> frozenset[str]:
        route = self.registry.get(self.registration_id)
        return route.platform_ids if route else frozenset()

    def unregister(self) -> None:
        if not self.active:
            return
        self.registry.unregister(self.registration_id)
        self.active = False


class InteractionRegistry:
    """Per-Context interaction routes, scoped to explicit platform instance IDs."""

    def __init__(self) -> None:
        self._routes: dict[str, InteractionRoute] = {}

    def register(
        self,
        *,
        platform_ids: Iterable[str] | str,
        handler: ButtonHandler,
        button_data: str | None = None,
        button_data_prefix: str | None = None,
        ack_mode: AckMode = "auto",
        ack_deadline: float = 3.0,
        handler_timeout: float = 60.0,
        fallback_ack_code: int = 1,
    ) -> InteractionRegistration:
        ids = _normalize_platform_ids(platform_ids)
        if not ids:
            raise ValueError("platform_ids must contain at least one platform instance ID")
        if not callable(handler):
            raise TypeError("handler must be callable")
        if (button_data is None) == (button_data_prefix is None):
            raise ValueError("provide exactly one of button_data or button_data_prefix")
        matcher_value = button_data if button_data is not None else button_data_prefix
        if not isinstance(matcher_value, str) or not matcher_value:
            raise ValueError("button matcher must be a non-empty string")
        if ack_mode not in {"auto", "manual"}:
            raise ValueError("ack_mode must be 'auto' or 'manual'")
        if (
            isinstance(fallback_ack_code, bool)
            or not isinstance(fallback_ack_code, int)
            or fallback_ack_code not in range(6)
        ):
            raise ValueError("fallback_ack_code must be an integer from 0 to 5")
        ack_deadline = _positive_seconds(ack_deadline, "ack_deadline")
        handler_timeout = _positive_seconds(handler_timeout, "handler_timeout")
        matcher: ButtonMatcher = ButtonMatcher(
            kind="exact" if button_data is not None else "prefix",
            value=matcher_value,
        )

        # Reject ambiguous duplicate subscriptions for any shared platform.
        for route in self._routes.values():
            overlaps = bool(ids & route.platform_ids)
            if overlaps and route.matcher == matcher:
                raise ValueError(
                    f"route {matcher.kind}={matcher.value!r} is already registered "
                    "for one or more of these platform IDs"
                )

        registration_id = uuid4().hex
        self._routes[registration_id] = InteractionRoute(
            registration_id=registration_id,
            platform_ids=ids,
            matcher=matcher,
            handler=handler,
            ack_mode=ack_mode,
            ack_deadline=ack_deadline,
            handler_timeout=handler_timeout,
            fallback_ack_code=int(fallback_ack_code),
        )
        return InteractionRegistration(self, registration_id)

    def resolve(self, platform_id: str, button_data: str | None) -> InteractionRoute | None:
        matches = [
            route
            for route in self._routes.values()
            if platform_id in route.platform_ids and route.matcher.matches(button_data)
        ]
        if not matches:
            return None
        # Exact routes beat prefixes; among prefixes, the most specific wins.
        matches.sort(
            key=lambda route: (
                route.matcher.kind == "exact",
                len(route.matcher.value),
            ),
            reverse=True,
        )
        return matches[0]

    def get(self, registration_id: str) -> InteractionRoute | None:
        return self._routes.get(registration_id)

    def unregister(self, registration_id: str) -> None:
        self._routes.pop(registration_id, None)

    def platform_ids(self) -> frozenset[str]:
        return frozenset(
            platform_id
            for route in self._routes.values()
            for platform_id in route.platform_ids
        )

    def __len__(self) -> int:
        return len(self._routes)


def _normalize_platform_ids(platform_ids: Iterable[str] | str) -> frozenset[str]:
    if isinstance(platform_ids, str):
        values = platform_ids.split(",")
    else:
        values = platform_ids
    return frozenset(
        value.strip()
        for value in values
        if isinstance(value, str) and value.strip()
    )


def _positive_seconds(value: float, name: str) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return seconds


_SHARED_REGISTRY_ATTR = "_astrbot_plugin_qqbotfix_interaction_registry_v2"


def get_shared_registry(owner: Any) -> InteractionRegistry:
    """Return the registry stored on AstrBot Context, surviving plugin reloads."""
    try:
        existing = getattr(owner, _SHARED_REGISTRY_ATTR, None)
    except Exception:
        existing = None
    if callable(getattr(existing, "register", None)) and callable(
        getattr(existing, "resolve", None)
    ) and callable(getattr(existing, "platform_ids", None)):
        return existing

    registry = InteractionRegistry()
    try:
        setattr(owner, _SHARED_REGISTRY_ATTR, registry)
    except Exception as exc:
        raise RuntimeError(
            "AstrBot Context does not allow storing the QQ interaction registry"
        ) from exc
    return registry
