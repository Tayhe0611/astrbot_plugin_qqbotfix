from __future__ import annotations

import asyncio
import inspect
import logging
import math
from dataclasses import dataclass
from typing import Any

from .models import InteractionAcknowledger, InteractionContext, _get
from .registry import InteractionRegistry, InteractionRoute

LOGGER = logging.getLogger("astrbot_plugin_qqbotfix")
SUPPORTED_ADAPTERS = {"qq_official", "qq_official_webhook"}
CALLBACK_NAME = "on_interaction_create"
INTERACTION_INTENT_FALLBACK = 1 << 26
_MISSING = object()


def callback_chain_has_bridge(callback: Any, platform_id: str) -> bool:
    """Find this platform's marked bridge in a callback's captured callables.

    Wrappers may retain the original callback in positional/keyword defaults
    or closure cells without copying its markers. Inspect those references
    without invoking callbacks; a captured bridge is not proof of forwarding.
    """
    pending = [callback]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if not callable(current) or id(current) in visited:
            continue
        visited.add(id(current))

        if (
            getattr(current, "__qqbotfix_bridge__", False) is True
            and getattr(current, "__qqbotfix_platform_id__", None) == platform_id
        ):
            return True

        pending.extend(getattr(current, "__defaults__", None) or ())
        pending.extend((getattr(current, "__kwdefaults__", None) or {}).values())
        for cell in getattr(current, "__closure__", None) or ():
            try:
                pending.append(cell.cell_contents)
            except ValueError:
                # A deleted nonlocal variable leaves an empty closure cell.
                continue
    return False


@dataclass(slots=True)
class PatchRecord:
    client: Any
    adapter: Any
    platform_id: str
    had_instance_callback: bool
    original_instance_callback: Any
    wrapper: Any


class InteractionBridge:
    """The BotPy callback/intent owner for explicitly managed instances.

    Registered routes take precedence over any baseline callback supplied by
    AstrBot or BotPy; interactions no route claims are chained back to it.
    """

    def __init__(
        self,
        registry: InteractionRegistry,
        *,
        ack_timeout: float = 3.0,
        debug_log_payload: bool = False,
        logger: logging.Logger | Any = LOGGER,
    ) -> None:
        self.registry = registry
        timeout = float(ack_timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("ack_timeout must be a positive finite number")
        self.ack_timeout = max(0.1, timeout)
        self.debug_log_payload = bool(debug_log_payload)
        self.logger = logger
        self._patches: dict[int, PatchRecord] = {}

    def patch_adapter(self, adapter: Any, managed_platform_ids: set[str]) -> bool:
        name = self._adapter_name(adapter)
        if name not in SUPPORTED_ADAPTERS:
            return False
        platform_id = self._platform_id(adapter)
        if not platform_id or platform_id not in managed_platform_ids:
            return False

        client = getattr(adapter, "client", None)
        if client is None:
            self.logger.warning(f"[{platform_id}] QQ adapter has no client; bridge skipped")
            return False

        existing = self._patches.get(id(client))
        if existing is not None and existing.client is client:
            if existing.platform_id != platform_id or existing.adapter is not adapter:
                self.logger.error(
                    f"[{platform_id}] this BotPy client is already owned by "
                    f"platform instance {existing.platform_id}; refusing cross-instance reuse"
                )
                return False
            return getattr(client, CALLBACK_NAME, None) is existing.wrapper

        try:
            instance_dict = vars(client)
        except TypeError:
            self.logger.warning(f"[{platform_id}] BotPy client has no instance dictionary")
            return False

        # Fail closed only when a *third-party* callback owns the instance slot.
        # A callback that resolves through the client's class (or any bound
        # method) is infrastructure supplied by AstrBot or BotPy, not a sibling
        # plugin: those are treated as a baseline this bridge may wrap and
        # chain to. Plugins can only install plain functions or closures, so a
        # non-method function in the instance dictionary is the only shape that
        # has to be preserved untouched.
        instance_callback = instance_dict.get(CALLBACK_NAME, _MISSING)
        stale_bridge = instance_callback is not _MISSING and bool(
            getattr(instance_callback, "__qqbotfix_bridge__", False)
        )
        if instance_callback is _MISSING or inspect.ismethod(instance_callback):
            chain_target = getattr(client, CALLBACK_NAME, None)
        elif stale_bridge:
            # A previous mount survived a hot reload without unpatching. Its
            # closure pins a registry entry that may already be gone, so it is
            # replaced rather than chained to.
            self.logger.warning(
                f"[{platform_id}] replacing a stale qqbotfix interaction bridge "
                "left in the instance dictionary"
            )
            chain_target = None
        else:
            self.logger.error(
                f"[{platform_id}] on_interaction_create already has an owner "
                f"(another plugin); not stacking another callback patch"
            )
            return False

        if name == "qq_official" and not self._enable_ws_intent(client, platform_id):
            return False

        # A replaced stale bridge must not be restored on teardown: dropping the
        # instance attribute lets the client class resolve its own callback.
        had_instance_callback = (
            instance_callback is not _MISSING and not stale_bridge
        )
        original_instance_callback = (
            instance_callback if had_instance_callback else _MISSING
        )

        async def wrapper(interaction: Any) -> None:
            try:
                claimed = await self._dispatch(
                    adapter, client, platform_id, interaction
                )
            except Exception:
                # A malformed payload must not unwind into botpy's dispatcher.
                self.logger.exception(
                    f"[{platform_id}] unexpected failure in QQ interaction bridge"
                )
                return
            if claimed or not callable(chain_target):
                return
            # Unclaimed interactions belong to the baseline owner. Claiming and
            # forwarding are mutually exclusive: a button this bridge routed is
            # never also handed to the chained callback, so a single click can
            # be neither acknowledged nor handled twice.
            try:
                result = chain_target(interaction)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                self.logger.exception(
                    f"[{platform_id}] chained interaction callback failed"
                )

        setattr(wrapper, "__qqbotfix_bridge__", True)
        setattr(wrapper, "__qqbotfix_platform_id__", platform_id)
        try:
            setattr(client, CALLBACK_NAME, wrapper)
        except Exception as exc:
            self.logger.warning(
                f"[{platform_id}] unable to install interaction callback: {exc}"
            )
            # Keep the intent bit once enabled. It is monotonic for this client
            # lifetime and may already be relied on by another migrated consumer.
            return False

        self._patches[id(client)] = PatchRecord(
            client=client,
            adapter=adapter,
            platform_id=platform_id,
            had_instance_callback=had_instance_callback,
            original_instance_callback=original_instance_callback,
            wrapper=wrapper,
        )
        if callable(chain_target):
            fallback = (
                f"unmatched interactions chain to "
                f"{getattr(chain_target, '__qualname__', type(chain_target).__name__)}"
            )
        else:
            fallback = "no baseline callback to chain to"
        self.logger.info(
            f"[{platform_id}] QQ interaction bridge installed for {name}; {fallback}"
        )
        return True

    def patch_platforms(self, adapters: Any, managed_platform_ids: set[str]) -> int:
        patched = 0
        if isinstance(adapters, dict):
            adapters = adapters.values()
        for adapter in tuple(adapters or ()):
            try:
                if self.patch_adapter(adapter, managed_platform_ids):
                    patched += 1
            except Exception:
                self.logger.exception("unexpected error while patching QQ adapter")
        return patched

    def is_active(self, platform_id: str) -> bool:
        return any(
            record.platform_id == platform_id
            and callback_chain_has_bridge(
                getattr(record.client, CALLBACK_NAME, None), platform_id
            )
            for record in self._patches.values()
        )

    def unpatch_all(self) -> None:
        for record in list(self._patches.values()):
            client = record.client
            try:
                current = getattr(client, CALLBACK_NAME, None)
                if current is not record.wrapper:
                    self.logger.warning(
                        f"[{record.platform_id}] callback ownership changed; "
                        "leaving the current callback untouched"
                    )
                    continue
                if record.had_instance_callback:
                    setattr(client, CALLBACK_NAME, record.original_instance_callback)
                else:
                    try:
                        delattr(client, CALLBACK_NAME)
                    except AttributeError:
                        pass
            except Exception:
                self.logger.exception(
                    f"[{record.platform_id}] failed to restore interaction callback"
                )
        self._patches.clear()
        # Intent bits are monotonic for this client lifetime. A consumer/plugin
        # teardown must not remove a bit another owner still relies on.

    async def _dispatch(
        self,
        adapter: Any,
        client: Any,
        platform_id: str,
        interaction: Any,
    ) -> bool:
        """Route one interaction and report whether this bridge claimed it.

        ``True`` means the interaction belongs to a registered route, including
        the case where it cannot be acknowledged; the caller must then leave it
        alone. ``False`` means no route matched and the baseline callback may
        still handle it.
        """
        data = _get(interaction, "data")
        resolved = _get(data, "resolved")
        button_data = _optional_string(_get(resolved, "button_data"))
        route = self.registry.resolve(platform_id, button_data)
        interaction_id = _optional_string(_get(interaction, "id"))
        self.logger.info(
            f"received QQ interaction: platform_id={platform_id} "
            f"button_id={_optional_string(_get(resolved, 'button_id'))} "
            f"claimed={route is not None}"
        )
        if self.debug_log_payload:
            self.logger.debug(f"QQ interaction payload: {interaction!r}")
        if route is None:
            return False
        if interaction_id is None:
            self.logger.warning(
                f"[{platform_id}] matched interaction has no ID; cannot ACK or dispatch"
            )
            return True

        acknowledger = InteractionAcknowledger(
            client,
            interaction_id,
            timeout=self.ack_timeout,
        )
        context = InteractionContext.from_interaction(
            adapter,
            client,
            interaction,
            platform_id=platform_id,
            acknowledger=acknowledger,
        )
        await self._run_route(route, context)
        return True

    async def _run_route(
        self, route: InteractionRoute, context: InteractionContext
    ) -> None:
        async def invoke() -> None:
            result = route.handler(context)
            if inspect.isawaitable(result):
                await result

        if route.ack_mode == "auto":
            try:
                await context.ack_once(0)
            except Exception:
                self.logger.exception(
                    f"[{context.platform_id}] automatic interaction ACK failed"
                )
                return
            try:
                await asyncio.wait_for(invoke(), timeout=route.handler_timeout)
            except asyncio.TimeoutError:
                self.logger.error(
                    f"[{context.platform_id}] interaction handler timed out after ACK"
                )
            except Exception:
                self.logger.exception(
                    f"[{context.platform_id}] interaction handler failed after ACK"
                )
            return

        await self._run_manual_ack_route(route, context, invoke)

    async def _run_manual_ack_route(
        self,
        route: InteractionRoute,
        context: InteractionContext,
        invoke: Any,
    ) -> None:
        task = asyncio.create_task(invoke())
        ack_event_task = asyncio.create_task(context._acknowledger.wait_attempted())
        started = asyncio.get_running_loop().time()
        first_timeout = min(route.ack_deadline, route.handler_timeout)
        try:
            done, _ = await asyncio.wait(
                {task, ack_event_task},
                timeout=first_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if context.ack_attempted:
                remaining = route.handler_timeout - (
                    asyncio.get_running_loop().time() - started
                )
                if remaining > 0 and not task.done():
                    try:
                        await asyncio.wait_for(task, timeout=remaining)
                    except asyncio.TimeoutError:
                        self.logger.error(
                            f"[{context.platform_id}] manual-ACK handler timed out after ACK"
                        )
                    except Exception:
                        self.logger.exception(
                            f"[{context.platform_id}] manual-ACK handler failed"
                        )
                elif task.done():
                    self._log_task_exception(task, context.platform_id)
                else:
                    self.logger.error(
                        f"[{context.platform_id}] manual-ACK handler exceeded its timeout"
                    )
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                return

            if task in done:
                try:
                    task.result()
                except Exception:
                    self.logger.exception(
                        f"[{context.platform_id}] manual-ACK handler failed before ACK"
                    )
                await self._fallback_ack(route, context)
                return

            # The consumer neither ACKed nor returned before its ACK deadline.
            self.logger.error(
                f"[{context.platform_id}] handler missed manual ACK deadline "
                f"({route.ack_deadline:.2f}s); sending fallback ACK and cancelling it"
            )
            await self._fallback_ack(route, context)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            if not ack_event_task.done():
                ack_event_task.cancel()
            await asyncio.gather(ack_event_task, return_exceptions=True)
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _fallback_ack(
        self, route: InteractionRoute, context: InteractionContext
    ) -> None:
        if context.ack_attempted:
            return
        try:
            await context.ack_once(route.fallback_ack_code)
        except Exception:
            self.logger.exception(
                f"[{context.platform_id}] fallback interaction ACK failed"
            )

    def _log_task_exception(self, task: asyncio.Task[Any], platform_id: str) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            self.logger.exception(
                f"[{platform_id}] manual-ACK handler failed"
            )

    def _enable_ws_intent(self, client: Any, platform_id: str) -> bool:
        current = getattr(client, "intents", None)
        if isinstance(current, bool) or not isinstance(current, int):
            self.logger.warning(
                f"[{platform_id}] client.intents is not an int; intent unchanged"
            )
            return False
        try:
            bit = self._interaction_intent_bit()
            updated = current | bit
            client.intents = updated
            if updated != current and getattr(client, "_connection", None) is not None:
                self.logger.warning(
                    f"[{platform_id}] intent changed after Gateway startup; "
                    "restart this adapter once to negotiate it"
                )
            return True
        except Exception:
            self.logger.exception(f"[{platform_id}] failed to enable interaction intent")
            return False

    @staticmethod
    def _interaction_intent_bit() -> int:
        try:
            import botpy  # type: ignore[import-not-found]

            flags = botpy.Intents.none()
            flags.interaction = True
            value = flags.value
            if isinstance(value, int) and value:
                return value
        except Exception:
            pass
        return INTERACTION_INTENT_FALLBACK

    @staticmethod
    def _adapter_name(adapter: Any) -> str | None:
        try:
            meta = adapter.meta()
            return _get(meta, "name")
        except Exception:
            return None

    @staticmethod
    def _platform_id(adapter: Any) -> str | None:
        try:
            meta = adapter.meta()
            value = _get(meta, "id")
            return str(value).strip() if value not in (None, "") else None
        except Exception:
            return None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
