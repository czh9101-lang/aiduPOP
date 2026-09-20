"""v2.7 Slash-confirm contracts at the real FeishuAdapter seams.

The host calls ``send_slash_confirm`` and decides whether to emit its legacy
``/approve`` / ``/cancel`` text fallback from the returned ``SendResult``.
These tests deliberately exercise the wrapper and its callback router with
small host fakes instead of testing only a CardKit payload.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import types
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _SendResult:
    """Small stand-in for Hermes's gateway.platforms.base.SendResult."""

    def __init__(self, success: bool = False, message_id: str | None = None):
        self.success = success
        self.message_id = message_id


@contextmanager
def _host_modules(*, slash_resolve: AsyncMock):
    """Install only the host imports touched by the v2.7 wrapper.

    Hermes is intentionally not a test dependency for unit tests in this
    repository.  Supplying the same import seams used in production keeps the
    test about the plugin's contract, rather than about an installed Hermes.
    """
    gateway = types.ModuleType("gateway")
    gateway.__path__ = []  # type: ignore[attr-defined]
    platforms = types.ModuleType("gateway.platforms")
    platforms.__path__ = []  # type: ignore[attr-defined]
    base = types.ModuleType("gateway.platforms.base")
    base.SendResult = _SendResult
    gateway.platforms = platforms  # type: ignore[attr-defined]
    platforms.base = base  # type: ignore[attr-defined]

    tools = types.ModuleType("tools")
    tools.__path__ = []  # type: ignore[attr-defined]
    slash_confirm = types.ModuleType("tools.slash_confirm")
    slash_confirm.resolve = slash_resolve
    tools.slash_confirm = slash_confirm  # type: ignore[attr-defined]

    with patch.dict(
        sys.modules,
        {
            "gateway": gateway,
            "gateway.platforms": platforms,
            "gateway.platforms.base": base,
            "tools": tools,
            "tools.slash_confirm": slash_confirm,
        },
    ):
        yield


class _Adapter:
    """Enough of FeishuAdapter for the interactive callback path."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.send = AsyncMock(return_value=_SendResult(success=True, message_id="followup-msg"))
        self._is_interactive_operator_authorized = MagicMock(return_value=True)
        # The production wrapper preflights the dynamically patched callback
        # seam before it may suppress Hermes's text fallback.
        self._handle_card_action_event = AsyncMock()


def _controller(*, reply_result: object = "confirm-card") -> MagicMock:
    client = MagicMock()
    client.reply_card = AsyncMock(return_value=reply_result)
    client.send_card_to_chat = AsyncMock(return_value=reply_result)
    client.update_card = AsyncMock(return_value=None)

    ctrl = MagicMock()
    ctrl.enabled = True
    ctrl._client_ok.return_value = True
    ctrl._client = client
    return ctrl


def _action_data(
    *,
    session_key: str,
    confirm_id: str,
    option: str,
    card_message_id: str,
    chat_id: str = "chat-v27",
    thread_id: str | None = None,
    open_id: str = "operator-ok",
) -> SimpleNamespace:
    action = SimpleNamespace(
        value={
            "hermes_slash_confirm_action": "select",
            "session_key": session_key,
            "confirm_id": confirm_id,
        },
        option=option,
        tag="select_static",
    )
    # CardKit callbacks reliably expose the acted-on card through
    # ``context.open_message_id``.  They do not currently supply a thread/root
    # id, so test the real callback shape instead of inventing one here.
    context_fields = {
        "open_chat_id": chat_id,
        "chat_id": chat_id,
        "open_message_id": card_message_id,
    }
    if thread_id:
        context_fields["thread_id"] = thread_id
    context = SimpleNamespace(
        **context_fields,
    )
    event = SimpleNamespace(
        action=action,
        context=context,
        operator=SimpleNamespace(open_id=open_id),
    )
    return SimpleNamespace(event=event)


def _interactive_tags(value: object) -> list[str]:
    """Return interactive CardKit tags anywhere in a card payload."""
    found: list[str] = []
    if isinstance(value, dict):
        tag = value.get("tag")
        if tag in {"select_static", "input", "action", "button"}:
            found.append(tag)
        for child in value.values():
            found.extend(_interactive_tags(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_interactive_tags(child))
    return found


@pytest.fixture(autouse=True)
def _clear_v270_slash_registry():
    """Do not let a prior callback's terminal state leak into another test."""
    try:
        from hermes_lark_streaming.patching import _slash_confirms
    except ImportError:
        # Before the v2.7 implementation lands this deliberately makes the
        # first behavior test red, rather than hiding a missing feature.
        yield
        return

    _slash_confirms.clear()
    yield
    _slash_confirms.clear()


def _mark_adapter_patched(adapter: _Adapter) -> None:
    """Avoid testing the unrelated deferred-class-identity re-patch path."""
    from hermes_lark_streaming.patching import _patched_feishu_classes

    _patched_feishu_classes.add(id(type(adapter)))


def test_pending_and_terminal_slash_cards_use_the_safe_native_controls() -> None:
    """Initial cards expose only once/cancel; terminal cards are inert.

    Feishu IM rejects schema-2.0 ``action/button`` as a first interactive
    message (230099).  A select callback is the production-safe surface.
    """
    from hermes_lark_streaming.cardkit import (
        build_slash_confirm_card,
        build_slash_confirm_resolved_card,
    )

    pending = build_slash_confirm_card(
        title="Switch model?",
        message="This costs more. Text fallback: /approve /cancel",
        session_key="session-v27",
        confirm_id="confirm-v27",
    )
    selects = [
        element
        for element in pending["body"]["elements"]
        if element.get("tag") == "select_static"
    ]
    assert len(selects) == 1
    assert [option["value"] for option in selects[0]["options"]] == ["once", "cancel"]
    callback = selects[0]["behaviors"][0]["value"]
    assert callback["hermes_slash_confirm_action"] == "select"
    assert callback["session_key"] == "session-v27"
    assert callback["confirm_id"] == "confirm-v27"
    assert "action" not in _interactive_tags(pending)
    assert "button" not in _interactive_tags(pending)

    terminal = build_slash_confirm_resolved_card(
        title="Switch model?",
        message="This costs more.",
        choice="once",
    )
    assert _interactive_tags(terminal) == []


@pytest.mark.asyncio
async def test_successful_card_delivery_alone_suppresses_host_text_fallback() -> None:
    """The wrapper may return success only after a real card message exists."""
    from hermes_lark_streaming.patching import _wrap_feishu_adapter_send_slash_confirm

    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller(reply_result="confirm-card-v27")
    original = AsyncMock(return_value="legacy-text-result")
    wrapped = _wrap_feishu_adapter_send_slash_confirm(original)
    metadata = {"reply_to": "anchor-v27", "thread_id": "thread-v27"}

    with _host_modules(slash_resolve=AsyncMock(return_value="")), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        result = await wrapped(
            adapter,
            "chat-v27",
            "Switch model?",
            "This costs more.",
            "session-v27",
            "confirm-v27",
            metadata=metadata,
        )

    assert result.success is True
    assert result.message_id == "confirm-card-v27"
    original.assert_not_awaited()
    ctrl._client.reply_card.assert_awaited_once()
    assert ctrl._client.reply_card.await_args.kwargs["reply_in_thread"] is True


@pytest.mark.asyncio
async def test_first_slash_initializes_the_card_client_before_falling_back() -> None:
    """A skipped registration pre-warm must not force the first Slash to text."""
    from hermes_lark_streaming.patching import _wrap_feishu_adapter_send_slash_confirm

    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller(reply_result="confirm-card-after-init")
    ctrl._client_ok.side_effect = [False, True]
    ctrl._ensure_init = AsyncMock(return_value=None)
    original = AsyncMock(return_value="legacy-text-result")
    wrapped = _wrap_feishu_adapter_send_slash_confirm(original)

    with _host_modules(slash_resolve=AsyncMock(return_value="")), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        result = await wrapped(
            adapter,
            "chat-v27",
            "Switch model?",
            "This costs more.",
            "session-v27",
            "confirm-v27",
        )

    assert result.success is True
    assert result.message_id == "confirm-card-after-init"
    ctrl._ensure_init.assert_awaited_once()
    original.assert_not_awaited()


@pytest.mark.asyncio
async def test_raw_host_prompt_removes_always_approval_prose_but_keeps_once_and_cancel() -> None:
    """The native card must not visually advertise Hermes's /always branch."""
    from hermes_lark_streaming.patching import _wrap_feishu_adapter_send_slash_confirm

    # This is the relevant shape of the raw Hermes host prompt: its text
    # fallback advertises a permanent approval branch before its footer.
    raw_host_prompt = (
        "Run the requested operation?\n"
        "Approve Once: /approve session-v27 confirm-v27\n"
        "Always Approve: /always session-v27 confirm-v27\n"
        "Cancel: /cancel session-v27 confirm-v27\n"
        "Text fallback: /approve /always /cancel"
    )
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller(reply_result="confirm-card-v27")
    original = AsyncMock(return_value="legacy-text-result")
    wrapped = _wrap_feishu_adapter_send_slash_confirm(original)

    with _host_modules(slash_resolve=AsyncMock(return_value="")), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        result = await wrapped(
            adapter,
            "chat-v27",
            "Confirmation required",
            raw_host_prompt,
            "session-v27",
            "confirm-v27",
            metadata={"reply_to_message_id": "host-anchor-v27"},
        )

    assert result.success is True
    delivered_card = ctrl._client.reply_card.await_args.args[1]
    rendered = json.dumps(delivered_card, ensure_ascii=False).casefold()
    assert "always" not in rendered
    assert "/always" not in rendered
    assert "approve once" in rendered
    assert "cancel" in rendered
    assert ctrl._client.reply_card.await_args.kwargs["reply_in_thread"] is False


@pytest.mark.asyncio
async def test_chinese_host_prompt_removes_always_and_text_fallback_prose() -> None:
    """Chinese host prose must not reintroduce a permanent approval path."""
    from hermes_lark_streaming.patching import _wrap_feishu_adapter_send_slash_confirm

    raw_host_prompt = (
        "确认操作\\n"
        "- 批准一次 — 立即执行\\n"
        "- 始终批准 — 永久静默此提示\\n"
        "- 取消 — 保持不变\\n"
        "文本备用：回复 /approve、/always 或 /cancel。"
    )
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller(reply_result="confirm-card-v27")
    wrapped = _wrap_feishu_adapter_send_slash_confirm(AsyncMock(return_value="legacy-text-result"))

    with _host_modules(slash_resolve=AsyncMock(return_value="")), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        result = await wrapped(
            adapter,
            "chat-v27",
            "Confirmation required",
            raw_host_prompt,
            "session-v27",
            "confirm-v27",
        )

    assert result.success is True
    rendered = json.dumps(ctrl._client.send_card_to_chat.await_args.args[1], ensure_ascii=False)
    assert "始终批准" not in rendered
    assert "文本备用" not in rendered
    assert "确认一次" in rendered
    assert "取消" in rendered


@pytest.mark.asyncio
async def test_card_delivery_failure_keeps_the_original_text_fallback_with_original_args() -> None:
    """A failed/no-id delivery must not suppress Hermes's text fallback."""
    from hermes_lark_streaming.patching import _wrap_feishu_adapter_send_slash_confirm

    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller(reply_result=None)
    # Exercise both likely delivery routes; neither produced a message id.
    ctrl._client.reply_card = AsyncMock(return_value=None)
    ctrl._client.send_card_to_chat = AsyncMock(return_value=None)
    original = AsyncMock(return_value="legacy-text-result")
    wrapped = _wrap_feishu_adapter_send_slash_confirm(original)
    metadata = {"reply_to": "anchor-v27", "thread_id": "thread-v27"}

    with _host_modules(slash_resolve=AsyncMock(return_value="")), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        result = await wrapped(
            adapter,
            "chat-v27",
            "Switch model?",
            "This costs more.",
            "session-v27",
            "confirm-v27",
            metadata=metadata,
        )

    assert result == "legacy-text-result"
    original.assert_awaited_once_with(
        adapter,
        "chat-v27",
        "Switch model?",
        "This costs more.",
        "session-v27",
        "confirm-v27",
        metadata=metadata,
    )


async def _register_confirm(
    adapter: _Adapter,
    *,
    ctrl: MagicMock,
    session_key: str = "session-v27",
    confirm_id: str = "confirm-v27",
    metadata: dict[str, str] | None = None,
) -> str:
    """Create live plugin state through the real send wrapper, not a dict poke."""
    from hermes_lark_streaming.patching import _wrap_feishu_adapter_send_slash_confirm

    wrapped = _wrap_feishu_adapter_send_slash_confirm(AsyncMock(return_value="fallback"))
    result = await wrapped(
        adapter,
        "chat-v27",
        "Switch model?",
        "This costs more.",
        session_key,
        confirm_id,
        metadata=(
            metadata
            if metadata is not None
            else {"reply_to": "anchor-v27", "thread_id": "thread-v27"}
        ),
    )
    assert getattr(result, "success", False) is True
    card_message_id = getattr(result, "message_id", None)
    assert isinstance(card_message_id, str) and card_message_id
    return card_message_id


@pytest.mark.asyncio
async def test_once_callback_resolves_at_most_once_and_replies_on_original_anchor() -> None:
    """A claimed confirmation is single-use and preserves chat/reply scope."""
    from hermes_lark_streaming.patching import _wrap_handle_card_action_event

    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def resolve(session_key: str, confirm_id: str, choice: str, *, timeout: int) -> str:
        assert (session_key, confirm_id, choice, timeout) == (
            "session-v27",
            "confirm-v27",
            "once",
            300,
        )
        entered.set()
        await release.wait()
        return "model switch completed"

    resolver = AsyncMock(side_effect=resolve)
    original_native_router = AsyncMock(return_value="NATIVE /card MUST NOT RUN")
    action_router = _wrap_handle_card_action_event(original_native_router)
    with _host_modules(slash_resolve=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _register_confirm(adapter, ctrl=ctrl)
        data = _action_data(
            session_key="session-v27",
            confirm_id="confirm-v27",
            option="once",
            card_message_id=card_message_id,
        )
        first_click = asyncio.create_task(action_router(adapter, data))
        await entered.wait()

        # The first callback owns PENDING -> RESOLVING before it awaits the
        # host resolver, so a replay cannot schedule a second resolver.
        await action_router(adapter, data)
        release.set()
        await first_click

    resolver.assert_awaited_once()
    original_native_router.assert_not_awaited()
    adapter.send.assert_awaited_once()
    sent = adapter.send.await_args
    assert sent.args[:2] == ("chat-v27", "model switch completed")
    assert sent.kwargs["reply_to"] == "anchor-v27"
    assert sent.kwargs["metadata"]["thread_id"] == "thread-v27"


@pytest.mark.asyncio
async def test_slash_result_bypasses_gateway_card_wrapper_to_preserve_reply_anchor() -> None:
    """The production send wrapper must not drop a Slash result's reply anchor.

    ``_do_gateway_deliver`` only accepts chat/content/category and always
    posts a top-level card.  Exercise the real wrapper around a small original
    adapter send implementation so a bare ``AsyncMock`` cannot mask that seam.
    """
    from hermes_lark_streaming.patching.adapter import (
        _deliver_slash_result,
        _wrap_feishu_adapter_send,
    )

    class GatewayWrappedAdapter:
        def __init__(self) -> None:
            self.raw_sends: list[dict[str, object]] = []

        async def send(self, chat_id, content, reply_to=None, metadata=None, **kwargs):
            self.raw_sends.append(
                {
                    "chat_id": chat_id,
                    "content": content,
                    "reply_to": reply_to,
                    "metadata": metadata,
                    "kwargs": kwargs,
                }
            )
            return _SendResult(success=True, message_id="native-followup-v27")

    GatewayWrappedAdapter.send = _wrap_feishu_adapter_send(GatewayWrappedAdapter.send)
    adapter = GatewayWrappedAdapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller()
    ctrl._do_gateway_deliver = AsyncMock(return_value=("top-level-card-v27", None))
    metadata = {"thread_id": "thread-v27", "reply_to_message_id": "anchor-v27"}
    record = {
        "chat_id": "chat-v27",
        "reply_to": "anchor-v27",
        "card_msg_id": "confirm-card-v27",
        "metadata": metadata,
    }

    with _host_modules(slash_resolve=AsyncMock(return_value="")), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch(
        "hermes_lark_streaming.patching.adapter._get_config",
        return_value=SimpleNamespace(gateway_cards=True),
    ):
        delivered = await _deliver_slash_result(adapter, record, "operation completed")
        # The task-local routing escape hatch must be reset after the awaited
        # result send: an unrelated gateway notice remains cardified.
        await adapter.send("chat-v27", "ordinary gateway notice")

    assert delivered is True
    ctrl._do_gateway_deliver.assert_awaited_once_with(
        "chat-v27", "ordinary gateway notice", category="system"
    )
    assert adapter.raw_sends == [
        {
            "chat_id": "chat-v27",
            "content": "operation completed",
            "reply_to": "anchor-v27",
            "metadata": metadata,
            "kwargs": {},
        }
    ]


@pytest.mark.asyncio
async def test_slash_result_routing_flag_resets_after_raw_host_send_exception() -> None:
    """A failed result transport cannot leak its raw-send route to later sends."""
    from hermes_lark_streaming.patching.adapter import (
        _deliver_slash_result,
        _wrap_feishu_adapter_send,
    )

    class RaisingGatewayWrappedAdapter:
        def __init__(self) -> None:
            self.raw_contents: list[str] = []

        async def send(self, _chat_id, content, reply_to=None, metadata=None, **_kwargs):
            self.raw_contents.append(content)
            raise RuntimeError("result transport unavailable")

    RaisingGatewayWrappedAdapter.send = _wrap_feishu_adapter_send(
        RaisingGatewayWrappedAdapter.send
    )
    adapter = RaisingGatewayWrappedAdapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller()
    ctrl._do_gateway_deliver = AsyncMock(return_value=("ordinary-card-v27", None))
    record = {
        "chat_id": "chat-v27",
        "reply_to": "anchor-v27",
        "card_msg_id": "confirm-card-v27",
        "metadata": {"thread_id": "thread-v27"},
    }

    with _host_modules(slash_resolve=AsyncMock(return_value="")), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch(
        "hermes_lark_streaming.patching.adapter._get_config",
        return_value=SimpleNamespace(gateway_cards=True),
    ):
        assert await _deliver_slash_result(adapter, record, "result prose") is False
        await adapter.send("chat-v27", "ordinary gateway notice")

    assert adapter.raw_contents == ["result prose"]
    ctrl._do_gateway_deliver.assert_awaited_once_with(
        "chat-v27", "ordinary gateway notice", category="system"
    )


@pytest.mark.asyncio
async def test_unknown_or_invalid_slash_choice_is_fail_closed_without_native_card_route() -> None:
    """Only the registered once/cancel options may reach slash_confirm.resolve."""
    from hermes_lark_streaming.patching import _wrap_handle_card_action_event

    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller()
    resolver = AsyncMock(return_value="should never be returned")
    native_router = AsyncMock(return_value="NATIVE /card MUST NOT RUN")
    action_router = _wrap_handle_card_action_event(native_router)

    with _host_modules(slash_resolve=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _register_confirm(adapter, ctrl=ctrl)
        await action_router(
            adapter,
            _action_data(
                session_key="session-v27",
                confirm_id="unknown-confirm-id",
                option="once",
                card_message_id=card_message_id,
            ),
        )
        await action_router(
            adapter,
            _action_data(
                session_key="session-v27",
                confirm_id="confirm-v27",
                option="always",
                card_message_id=card_message_id,
            ),
        )
        # A genuine option is still invalid when replayed from a different
        # chat scope than the card's saved delivery context.
        await action_router(
            adapter,
            _action_data(
                session_key="session-v27",
                confirm_id="confirm-v27",
                option="once",
                card_message_id=card_message_id,
                chat_id="different-chat-v27",
            ),
        )
        # The card id is also a mandatory boundary: a callback from another
        # card in the same chat cannot claim this confirmation.
        await action_router(
            adapter,
            _action_data(
                session_key="session-v27",
                confirm_id="confirm-v27",
                option="once",
                card_message_id="other-card-in-chat-v27",
            ),
        )

    resolver.assert_not_awaited()
    native_router.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_is_the_only_other_legal_choice() -> None:
    """Cancel reaches the host exactly as cancel, never as a permanent grant."""
    from hermes_lark_streaming.patching import _wrap_handle_card_action_event

    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller()
    resolver = AsyncMock(return_value="")
    action_router = _wrap_handle_card_action_event(AsyncMock())

    with _host_modules(slash_resolve=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _register_confirm(adapter, ctrl=ctrl)
        await action_router(
            adapter,
            _action_data(
                session_key="session-v27",
                confirm_id="confirm-v27",
                option="cancel",
                card_message_id=card_message_id,
            ),
        )

    resolver.assert_awaited_once()
    assert resolver.await_args.args[:3] == ("session-v27", "confirm-v27", "cancel")
    assert resolver.await_args.kwargs.get("timeout", 300) == 300


@pytest.mark.asyncio
async def test_unauthorized_slash_callback_never_reaches_resolver() -> None:
    """The adapter's real Feishu operator authorization is a hard boundary."""
    from hermes_lark_streaming.patching import (
        _slash_confirms,
        _wrap_handle_card_action_event,
    )

    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller()
    resolver = AsyncMock(return_value="should not run")
    native_router = AsyncMock(return_value="NATIVE /card MUST NOT RUN")
    action_router = _wrap_handle_card_action_event(native_router)

    with _host_modules(slash_resolve=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _register_confirm(adapter, ctrl=ctrl)
        adapter._is_interactive_operator_authorized.return_value = False
        await action_router(
            adapter,
            _action_data(
                session_key="session-v27",
                confirm_id="confirm-v27",
                option="once",
                card_message_id=card_message_id,
            ),
        )

    resolver.assert_not_awaited()
    native_router.assert_not_awaited()
    assert _slash_confirms[("session-v27", "confirm-v27")]["state"] == "PENDING"


@pytest.mark.asyncio
async def test_expired_slash_callback_never_resolves_and_retires_to_an_inert_card() -> None:
    """Expiry must fail closed before resolver scheduling, not merely after it."""
    from hermes_lark_streaming.patching import (
        _slash_confirms,
        _wrap_handle_card_action_event,
    )

    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller()
    resolver = AsyncMock(return_value="should not run")
    action_router = _wrap_handle_card_action_event(AsyncMock())

    with _host_modules(slash_resolve=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _register_confirm(adapter, ctrl=ctrl)
        _slash_confirms[("session-v27", "confirm-v27")]["created_at"] = time.monotonic() - 301
        await action_router(
            adapter,
            _action_data(
                session_key="session-v27",
                confirm_id="confirm-v27",
                option="once",
                card_message_id=card_message_id,
            ),
        )

    resolver.assert_not_awaited()
    assert _slash_confirms[("session-v27", "confirm-v27")]["state"] == "EXPIRED"
    ctrl._client.update_card.assert_awaited_once()
    assert _interactive_tags(ctrl._client.update_card.await_args.args[1]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resolver_config",
    [
        pytest.param(RuntimeError("resolver down"), id="exception"),
        pytest.param("", id="empty-once-result"),
    ],
)
async def test_exception_or_empty_once_result_never_claims_success(resolver_config: object) -> None:
    """A missing successful resolver result must lock to failure, never 'once'."""
    from hermes_lark_streaming.patching import (
        _slash_confirms,
        _wrap_handle_card_action_event,
    )

    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller()
    resolver = (
        AsyncMock(side_effect=resolver_config)
        if isinstance(resolver_config, Exception)
        else AsyncMock(return_value=resolver_config)
    )
    action_router = _wrap_handle_card_action_event(AsyncMock())

    with _host_modules(slash_resolve=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _register_confirm(adapter, ctrl=ctrl)
        await action_router(
            adapter,
            _action_data(
                session_key="session-v27",
                confirm_id="confirm-v27",
                option="once",
                card_message_id=card_message_id,
            ),
        )

    resolver.assert_awaited_once()
    assert _slash_confirms[("session-v27", "confirm-v27")]["state"] == "FAILED"
    adapter.send.assert_not_awaited()
    ctrl._client.update_card.assert_awaited_once()
    assert _interactive_tags(ctrl._client.update_card.await_args.args[1]) == []


@pytest.mark.asyncio
async def test_unsuccessful_result_followup_never_claims_success() -> None:
    """A false SendResult is a delivery failure even when it did not raise."""
    from hermes_lark_streaming.patching import (
        _slash_confirms,
        _wrap_handle_card_action_event,
    )

    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    adapter.send = AsyncMock(return_value=_SendResult(success=False))
    ctrl = _controller()
    resolver = AsyncMock(return_value="result prose that must not disappear")
    router = _wrap_handle_card_action_event(AsyncMock())

    with _host_modules(slash_resolve=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _register_confirm(adapter, ctrl=ctrl)
        await router(
            adapter,
            _action_data(
                session_key="session-v27",
                confirm_id="confirm-v27",
                option="once",
                card_message_id=card_message_id,
            ),
        )

    resolver.assert_awaited_once()
    adapter.send.assert_awaited_once()
    assert _slash_confirms[("session-v27", "confirm-v27")]["state"] == "FAILED"
    ctrl._client.update_card.assert_awaited_once()
    terminal = json.dumps(ctrl._client.update_card.await_args.args[1], ensure_ascii=False).casefold()
    assert "confirmed once" not in terminal
    assert _interactive_tags(ctrl._client.update_card.await_args.args[1]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("native_marker", ["hermes_action", "hermes_update_prompt_action"])
async def test_native_hermes_card_actions_still_delegate_to_original_handler(native_marker: str) -> None:
    """The v2.7 router must not steal Hermes's own approval/prompt actions."""
    from hermes_lark_streaming.patching import _wrap_handle_card_action_event

    original = AsyncMock(return_value="native-result")
    adapter = _Adapter()
    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(value={native_marker: "native-value"}, tag="button"),
        ),
    )

    result = await _wrap_handle_card_action_event(original)(adapter, data)

    assert result == "native-result"
    original.assert_awaited_once_with(adapter, data)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "expected_delivery_anchor", "expected_result_anchor"),
    [
        pytest.param(
            {"reply_to_message_id": "host-anchor-v27", "thread_id": "thread-v27"},
            "host-anchor-v27",
            "host-anchor-v27",
            id="real-host-reply-to-message-id",
        ),
        pytest.param(
            {},
            None,
            "confirm-card",
            id="top-level-replies-to-confirm-card",
        ),
    ],
)
async def test_slash_result_uses_real_host_anchor_or_top_level_confirmation_card(
    metadata: dict[str, str],
    expected_delivery_anchor: str | None,
    expected_result_anchor: str,
) -> None:
    """Use native cards only when their reply scope can be preserved."""
    from hermes_lark_streaming.patching import _wrap_handle_card_action_event

    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller(reply_result="confirm-card")
    resolver = AsyncMock(return_value="completed with result prose")
    router = _wrap_handle_card_action_event(AsyncMock())

    with _host_modules(slash_resolve=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _register_confirm(adapter, ctrl=ctrl, metadata=metadata)
        await router(
            adapter,
            _action_data(
                session_key="session-v27",
                confirm_id="confirm-v27",
                option="once",
                card_message_id=card_message_id,
            ),
        )

    if expected_delivery_anchor is None:
        ctrl._client.send_card_to_chat.assert_awaited_once()
        ctrl._client.reply_card.assert_not_awaited()
    else:
        assert ctrl._client.reply_card.await_args.args[0] == expected_delivery_anchor
    assert adapter.send.await_args.kwargs["reply_to"] == expected_result_anchor
    assert adapter.send.await_args.kwargs["metadata"] == metadata


@pytest.mark.asyncio
async def test_thread_only_slash_metadata_preserves_the_host_text_fallback() -> None:
    """Never move a threaded confirmation to top level without its anchor."""
    from hermes_lark_streaming.patching import (
        _slash_confirms,
        _wrap_feishu_adapter_send_slash_confirm,
    )

    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    ctrl = _controller(reply_result="confirm-card")
    original = AsyncMock(return_value="legacy-threaded-text")
    wrapped = _wrap_feishu_adapter_send_slash_confirm(original)
    metadata = {"thread_id": "thread-without-anchor-v27"}

    with _host_modules(slash_resolve=AsyncMock(return_value="")), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        result = await wrapped(
            adapter,
            "chat-v27",
            "Switch model?",
            "This costs more.",
            "session-v27",
            "confirm-v27",
            metadata=metadata,
        )

    assert result == "legacy-threaded-text"
    original.assert_awaited_once_with(
        adapter,
        "chat-v27",
        "Switch model?",
        "This costs more.",
        "session-v27",
        "confirm-v27",
        metadata=metadata,
    )
    ctrl._client.reply_card.assert_not_awaited()
    ctrl._client.send_card_to_chat.assert_not_awaited()
    assert _slash_confirms == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_in_thread", [False, True])
async def test_reply_card_explicitly_serializes_thread_routing(
    reply_in_thread: bool,
) -> None:
    """The plugin client must not rely on Feishu's false-by-default setting."""
    from hermes_lark_streaming.feishu.client import FeishuClient

    class Response:
        data = SimpleNamespace(message_id="reply-card-v27")

        @staticmethod
        def success() -> bool:
            return True

    class MessageAPI:
        def __init__(self) -> None:
            self.request = None

        async def areply(self, request):
            self.request = request
            return Response()

    message_api = MessageAPI()
    client = object.__new__(FeishuClient)
    client._client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(message=message_api)))

    result = await client.reply_card(
        "anchor-v27", {"schema": "2.0"}, reply_in_thread=reply_in_thread
    )

    assert result == "reply-card-v27"
    assert message_api.request.request_body.reply_in_thread is reply_in_thread
