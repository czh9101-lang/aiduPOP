"""v2.7 Clarify lifecycle contracts at Hermes's real adapter seams.

Hermes 0.21.3 fans a clarify batch out before it calls the adapter: this
plugin receives one ``send_clarify`` call and one ``clarify_id`` per question.
The tests below preserve that host shape and use a small fake gateway entry so
we exercise state, callback, resolve, and retire behavior together.
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
    def __init__(self, success: bool = False, message_id: str | None = None):
        self.success = success
        self.message_id = message_id


@contextmanager
def _host_modules(*, entries: dict[str, object], resolve_gateway_clarify: MagicMock):
    """Provide the actual lazy-import seams used by the plugin wrappers."""
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
    clarify_gateway = types.ModuleType("tools.clarify_gateway")
    clarify_gateway._entries = entries
    clarify_gateway.resolve_gateway_clarify = resolve_gateway_clarify
    clarify_gateway.mark_awaiting_text = MagicMock()
    tools.clarify_gateway = clarify_gateway  # type: ignore[attr-defined]

    # v2.6 called this helper from a fire-and-forget path.  Keeping the seam
    # available makes the test work against both that legacy shape and the
    # v2.7 direct-async path, without making a real Hermes install necessary.
    agent = types.ModuleType("agent")
    agent.__path__ = []  # type: ignore[attr-defined]
    async_utils = types.ModuleType("agent.async_utils")

    def safe_schedule_threadsafe(coro, loop, **_kwargs):
        return asyncio.create_task(coro)

    async_utils.safe_schedule_threadsafe = safe_schedule_threadsafe
    agent.async_utils = async_utils  # type: ignore[attr-defined]

    with patch.dict(
        sys.modules,
        {
            "gateway": gateway,
            "gateway.platforms": platforms,
            "gateway.platforms.base": base,
            "tools": tools,
            "tools.clarify_gateway": clarify_gateway,
            "agent": agent,
            "agent.async_utils": async_utils,
        },
    ):
        yield


class _Adapter:
    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.send = AsyncMock(return_value=_SendResult(success=True, message_id="plain-followup"))
        self._is_interactive_operator_authorized = MagicMock(return_value=True)


def _controller(client: object) -> MagicMock:
    ctrl = MagicMock()
    ctrl.enabled = True
    ctrl._client_ok.return_value = True
    ctrl._client = client
    ctrl._sess_items_snapshot.return_value = []
    ctrl._get_loop.return_value = asyncio.get_running_loop()
    return ctrl


def _card_action_data(
    *,
    clarify_id: str,
    option: object,
    card_message_id: str,
    clarify_action: str = "select",
    form_value: dict[str, object] | None = None,
    input_value: str = "",
    chat_id: str = "clarify-chat-v27",
    thread_id: str | None = None,
    action_value: dict[str, object] | None = None,
    action_tag: str = "select_static",
) -> SimpleNamespace:
    action = SimpleNamespace(
        value=(
            {"hermes_clarify_action": clarify_action, "clarify_id": clarify_id}
            if action_value is None
            else action_value
        ),
        option=option,
        options=option if isinstance(option, (list, tuple)) else None,
        input_value=input_value,
        form_value=form_value or {},
        tag=action_tag,
    )
    # Current CardKit callbacks reliably identify the acted-on card with
    # ``context.open_message_id``.  They do not provide thread/root fields,
    # so normal-path tests deliberately omit those invented values.
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
        operator=SimpleNamespace(open_id="operator-ok"),
    )
    return SimpleNamespace(event=event)


def _interactive_tags(value: object) -> list[str]:
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


def _elements_with_tag(value: object, tag: str) -> list[dict]:
    """Find CardKit elements recursively without depending on their layout."""
    found: list[dict] = []
    if isinstance(value, dict):
        if value.get("tag") == tag:
            found.append(value)
        for child in value.values():
            found.extend(_elements_with_tag(child, tag))
    elif isinstance(value, list):
        for child in value:
            found.extend(_elements_with_tag(child, tag))
    return found


@pytest.fixture(autouse=True)
def _clear_v270_clarify_state():
    """Keep host-id state isolated even when a test deliberately retires it."""
    try:
        from hermes_lark_streaming import patching
    except ImportError:
        yield
        return

    registry_names = (
        "_clarify_records",
        "_clarify_choices",
        "_clarify_questions",
        "_clarify_card_msg_ids",
        "_clarify_selections",
        "_clarify_timestamps",
    )
    for name in registry_names:
        registry = getattr(patching, name, None)
        if isinstance(registry, dict):
            registry.clear()
    yield
    for name in registry_names:
        registry = getattr(patching, name, None)
        if isinstance(registry, dict):
            registry.clear()


def _mark_adapter_patched(adapter: _Adapter) -> None:
    from hermes_lark_streaming.patching import _patched_feishu_classes

    _patched_feishu_classes.add(id(type(adapter)))


def _client(*, card_message_id: str = "clarify-card-v27") -> MagicMock:
    client = MagicMock()
    client.send_card_to_chat = AsyncMock(return_value=card_message_id)
    client.reply_card = AsyncMock(return_value=card_message_id)
    client.update_card = AsyncMock(return_value=None)
    return client


async def _send_one_clarify(
    adapter: _Adapter,
    *,
    ctrl: MagicMock,
    clarify_id: str,
    choices: list[str],
    session_key: str = "clarify-session-v27",
    metadata: dict[str, str] | None = None,
) -> str:
    """Register state through the real adapter send wrapper."""
    from hermes_lark_streaming.patching import _wrap_feishu_adapter_send_clarify

    original = AsyncMock(return_value="legacy clarify fallback")
    wrapped = _wrap_feishu_adapter_send_clarify(original)
    result = await wrapped(
        adapter,
        "clarify-chat-v27",
        f"Question for {clarify_id}?",
        choices,
        clarify_id,
        session_key,
        metadata=(
            metadata if metadata is not None else {}
        ),
    )
    assert getattr(result, "success", False) is True
    original.assert_not_awaited()
    card_message_id = getattr(result, "message_id", None)
    assert isinstance(card_message_id, str) and card_message_id
    return card_message_id


@pytest.mark.asyncio
async def test_clarify_prefers_the_real_reply_to_message_anchor() -> None:
    """A stale compatibility alias must not move a Clarify card to another reply."""
    cid = "clarify-anchor-priority-v27"
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    client = _client(card_message_id="clarify-anchor-card-v27")
    ctrl = _controller(client)
    entries = {cid: SimpleNamespace(multi_select=False)}

    with _host_modules(entries=entries, resolve_gateway_clarify=MagicMock()), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        await _send_one_clarify(
            adapter,
            ctrl=ctrl,
            clarify_id=cid,
            choices=["A", "B"],
            metadata={
                "thread_id": "clarify-thread-v27",
                "reply_to": "stale-compat-anchor-v27",
                "reply_to_message_id": "real-host-anchor-v27",
            },
        )

    client.reply_card.assert_awaited_once()
    assert client.reply_card.await_args.args[0] == "real-host-anchor-v27"
    assert client.reply_card.await_args.kwargs["reply_in_thread"] is True
    client.send_card_to_chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_thread_only_clarify_metadata_preserves_host_thread_text_fallback() -> None:
    """Do not turn a topic-only Clarify into a top-level native card.

    This composes the real generic ``send`` wrapper with the original
    ``send_clarify`` fallback.  A mock of only ``send_clarify`` would miss the
    second wrapper, which is exactly where thread metadata used to be lost.
    """
    from hermes_lark_streaming.patching import (
        _wrap_feishu_adapter_send,
        _wrap_feishu_adapter_send_clarify,
    )

    class ComposedAdapter:
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
            return _SendResult(success=True, message_id="host-thread-text-v27")

        async def send_clarify(
            self, chat_id, question, choices, clarify_id, session_key, metadata=None, **kwargs
        ):
            return await self.send(
                chat_id,
                f"host clarify: {question}",
                metadata=metadata,
                **kwargs,
            )

    ComposedAdapter.send = _wrap_feishu_adapter_send(ComposedAdapter.send)
    ComposedAdapter.send_clarify = _wrap_feishu_adapter_send_clarify(
        ComposedAdapter.send_clarify
    )
    adapter = ComposedAdapter()
    _mark_adapter_patched(adapter)
    cid = "clarify-thread-only-v27"
    client = _client()
    ctrl = _controller(client)
    ctrl._do_gateway_deliver = AsyncMock(return_value=("wrong-top-level-v27", None))
    metadata = {"thread_id": "thread-without-anchor-v27"}

    with _host_modules(
        entries={cid: SimpleNamespace(multi_select=False)},
        resolve_gateway_clarify=MagicMock(),
    ), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch(
        "hermes_lark_streaming.patching.adapter._get_config",
        return_value=SimpleNamespace(gateway_cards=True),
    ):
        result = await adapter.send_clarify(
            "clarify-chat-v27",
            "What belongs in this thread?",
            ["A", "B"],
            cid,
            "clarify-session-v27",
            metadata=metadata,
        )

    assert result.success is True
    client.reply_card.assert_not_awaited()
    client.send_card_to_chat.assert_not_awaited()
    ctrl._do_gateway_deliver.assert_not_awaited()
    assert adapter.raw_sends == [
        {
            "chat_id": "clarify-chat-v27",
            "content": "host clarify: What belongs in this thread?",
            "reply_to": None,
            "metadata": metadata,
            "kwargs": {},
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("question_count", [1, 2, 5])
async def test_hermes_sequential_question_calls_remain_independent_per_clarify_id(
    question_count: int,
) -> None:
    """The plugin must not invent a batch card/API for a shared session key."""
    from hermes_lark_streaming.patching import _clarify_records

    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    client = _client()
    ctrl = _controller(client)
    ids = [f"clarify-{index}" for index in range(question_count)]
    entries = {cid: SimpleNamespace(multi_select=False) for cid in ids}

    with _host_modules(entries=entries, resolve_gateway_clarify=MagicMock()), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        for cid in ids:
            await _send_one_clarify(adapter, ctrl=ctrl, clarify_id=cid, choices=["A", "B"])

    # One host callback means one Feishu card and one isolated record, even
    # when Hermes uses the same session key for 1/2/5 questions.
    assert client.send_card_to_chat.await_count == question_count
    assert set(ids).issubset(_clarify_records)

    card_ids: set[str] = set()
    for call in client.send_card_to_chat.await_args_list:
        card = call.args[1]
        selects = [e for e in card["body"]["elements"] if e.get("tag") == "select_static"]
        assert len(selects) == 1
        card_ids.add(selects[0]["behaviors"][0]["value"]["clarify_id"])
    assert card_ids == set(ids)


@pytest.mark.asyncio
async def test_multi_select_uses_gateway_entry_flag_and_resolves_as_json_string() -> None:
    """Never guess multi-select from choices; use entry.multi_select and JSON."""
    from hermes_lark_streaming.patching import _wrap_handle_card_action_event

    cid = "clarify-multi-v27"
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    client = _client()
    ctrl = _controller(client)
    resolver = MagicMock()
    entries = {cid: SimpleNamespace(multi_select=True)}
    native_router = AsyncMock(return_value="NATIVE /card MUST NOT RUN")
    router = _wrap_handle_card_action_event(native_router)

    with _host_modules(entries=entries, resolve_gateway_clarify=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _send_one_clarify(
            adapter,
            ctrl=ctrl,
            clarify_id=cid,
            choices=["staging", "prod", "dry-run"],
        )
        # The verified entry flag must change the actual pending card into a
        # native multi-select form, rather than merely changing answer parsing.
        pending = client.send_card_to_chat.await_args.args[1]
        forms = _elements_with_tag(pending, "form")
        assert len(forms) == 1
        multi_selects = _elements_with_tag(pending, "multi_select_static")
        assert len(multi_selects) == 1
        assert "behaviors" not in multi_selects[0]
        submits = _elements_with_tag(pending, "button")
        assert len(submits) == 1
        assert submits[0]["form_action_type"] == "submit"
        assert "behaviors" not in submits[0]

        await router(
            adapter,
            _card_action_data(
                clarify_id=cid,
                option=None,
                card_message_id=card_message_id,
                form_value={"clarify_multi_select": ["0", "1"]},
                action_value={},
                action_tag="button",
            ),
        )

    resolver.assert_called_once()
    assert resolver.call_args.args[:2] == (
        cid,
        json.dumps(["staging", "prod"], ensure_ascii=False),
    )
    assert isinstance(resolver.call_args.args[1], str)
    native_router.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("multi_select", "use_actual_card", "action_tag", "form_value"),
    [
        pytest.param(True, False, "button", {"clarify_multi_select": ["0"]}, id="wrong-card"),
        pytest.param(True, True, "select_static", {"clarify_multi_select": ["0"]}, id="not-button"),
        pytest.param(True, True, "button", {}, id="missing-form-field"),
        pytest.param(False, True, "button", {"clarify_multi_select": ["0"]}, id="single-select-record"),
    ],
)
async def test_native_multi_form_submit_requires_an_exact_live_card_mapping(
    multi_select: bool,
    use_actual_card: bool,
    action_tag: str,
    form_value: dict[str, object],
) -> None:
    """A markerless form submit never guesses its clarify id from user input."""
    from hermes_lark_streaming.patching import (
        _clarify_records,
        _wrap_handle_card_action_event,
    )

    cid = f"clarify-native-form-{action_tag}-{multi_select}-{use_actual_card}"
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    client = _client(card_message_id="clarify-native-form-card-v27")
    ctrl = _controller(client)
    resolver = MagicMock()
    native_router = AsyncMock(return_value="NATIVE /card MUST NOT RUN")
    router = _wrap_handle_card_action_event(native_router)

    with _host_modules(
        entries={cid: SimpleNamespace(multi_select=multi_select)},
        resolve_gateway_clarify=resolver,
    ), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _send_one_clarify(
            adapter, ctrl=ctrl, clarify_id=cid, choices=["A", "B"]
        )
        await router(
            adapter,
            _card_action_data(
                clarify_id="untrusted-action-value",
                option=None,
                card_message_id=(
                    card_message_id if use_actual_card else f"{card_message_id}-forged"
                ),
                form_value=form_value,
                action_value={},
                action_tag=action_tag,
            ),
        )

    resolver.assert_not_called()
    native_router.assert_not_awaited()
    assert _clarify_records[cid]["state"] == "PENDING"


@pytest.mark.asyncio
async def test_native_multi_form_submit_rejects_an_ambiguous_local_card_mapping() -> None:
    """Even an actual message id cannot resolve two corrupted local records."""
    from hermes_lark_streaming.patching import (
        _clarify_records,
        _wrap_handle_card_action_event,
    )

    cid = "clarify-native-form-ambiguous-v27"
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    client = _client(card_message_id="clarify-native-form-ambiguous-card-v27")
    ctrl = _controller(client)
    resolver = MagicMock()
    native_router = AsyncMock(return_value="NATIVE /card MUST NOT RUN")
    router = _wrap_handle_card_action_event(native_router)

    with _host_modules(
        entries={cid: SimpleNamespace(multi_select=True)},
        resolve_gateway_clarify=resolver,
    ), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _send_one_clarify(
            adapter, ctrl=ctrl, clarify_id=cid, choices=["A", "B"]
        )
        _clarify_records["clarify-native-form-duplicate-v27"] = dict(_clarify_records[cid])
        await router(
            adapter,
            _card_action_data(
                clarify_id="untrusted-action-value",
                option=None,
                card_message_id=card_message_id,
                form_value={"clarify_multi_select": ["0"]},
                action_value={},
                action_tag="button",
            ),
        )

    resolver.assert_not_called()
    native_router.assert_not_awaited()
    assert _clarify_records[cid]["state"] == "PENDING"


@pytest.mark.asyncio
async def test_submitted_callback_uses_builder_signature_without_legacy_choices_kwarg() -> None:
    """Regression: the v2.6 CallBackCard path passed unsupported ``choices``."""
    from hermes_lark_streaming.patching import _wrap_handle_card_action_event

    cid = "clarify-submitted-v27"
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    client = _client()
    ctrl = _controller(client)
    resolver = MagicMock(side_effect=RuntimeError("host resolver unavailable"))
    entries = {cid: SimpleNamespace(multi_select=False)}
    builder_calls: list[tuple[str, str, str]] = []

    def strict_submitted_builder(*, question: str, selected: str, clarify_id: str = "") -> dict:
        builder_calls.append((question, selected, clarify_id))
        return {"schema": "2.0", "config": {}, "body": {"elements": []}}

    router = _wrap_handle_card_action_event(AsyncMock())
    with _host_modules(entries=entries, resolve_gateway_clarify=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"), patch(
        "hermes_lark_streaming.cardkit.build_clarify_submitted_card",
        side_effect=strict_submitted_builder,
    ):
        card_message_id = await _send_one_clarify(
            adapter, ctrl=ctrl, clarify_id=cid, choices=["A", "B"]
        )
        await router(
            adapter,
            _card_action_data(clarify_id=cid, option="0", card_message_id=card_message_id),
        )

    assert builder_calls == [(f"Question for {cid}?", "A", cid)]
    assert resolver.call_args.args[:2] == (cid, "A")


@pytest.mark.asyncio
async def test_duplicate_clarify_clicks_claim_once_before_any_resolve_schedule() -> None:
    """Two callbacks for one id must yield one host resolve, not two schedules."""
    from hermes_lark_streaming.patching import _wrap_handle_card_action_event

    cid = "clarify-double-click-v27"
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    client = _client()
    ctrl = _controller(client)
    resolver = MagicMock()
    entries = {cid: SimpleNamespace(multi_select=False)}
    native_router = AsyncMock(return_value="NATIVE /card MUST NOT RUN")
    router = _wrap_handle_card_action_event(native_router)
    with _host_modules(entries=entries, resolve_gateway_clarify=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _send_one_clarify(
            adapter, ctrl=ctrl, clarify_id=cid, choices=["A", "B"]
        )
        data = _card_action_data(clarify_id=cid, option="0", card_message_id=card_message_id)
        await asyncio.gather(router(adapter, data), router(adapter, data))

    assert resolver.call_count == 1
    assert resolver.call_args.args[:2] == (cid, "A")
    native_router.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_clarify_server_updates_to_inert_card_when_callback_return_is_discarded() -> None:
    """Feishu must see a server-side terminal card even if Hermes drops return."""
    from hermes_lark_streaming.patching import (
        _clarify_records,
        _wrap_handle_card_action_event,
    )

    cid = "clarify-server-confirmed-v27"
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    client = _client(card_message_id="clarify-server-card-v27")
    ctrl = _controller(client)
    resolver = MagicMock()
    entries = {cid: SimpleNamespace(multi_select=False)}
    router = _wrap_handle_card_action_event(AsyncMock())

    with _host_modules(entries=entries, resolve_gateway_clarify=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"), patch(
        # Hermes's callback dispatcher may discard the plugin callback result.
        # The authoritative visible lock must therefore not depend on it.
        "hermes_lark_streaming.patching.adapter._callback_card_response",
        return_value=None,
    ):
        card_message_id = await _send_one_clarify(
            adapter, ctrl=ctrl, clarify_id=cid, choices=["A", "B"]
        )
        response = await router(
            adapter,
            _card_action_data(clarify_id=cid, option="0", card_message_id=card_message_id),
        )

    assert response is None
    assert resolver.call_args.args[:2] == (cid, "A")
    assert cid not in _clarify_records
    client.update_card.assert_awaited_once()
    assert client.update_card.await_args.args[0] == "clarify-server-card-v27"
    assert _interactive_tags(client.update_card.await_args.args[1]) == []


@pytest.mark.asyncio
async def test_unauthorized_clarify_callback_keeps_the_live_card_unresolved() -> None:
    """Authorization failure is fail-closed and must not retire the user's card."""
    from hermes_lark_streaming.patching import (
        _clarify_records,
        _wrap_handle_card_action_event,
    )

    cid = "clarify-unauthorized-v27"
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    adapter._is_interactive_operator_authorized.return_value = False
    client = _client()
    ctrl = _controller(client)
    resolver = MagicMock()
    entries = {cid: SimpleNamespace(multi_select=False)}
    native_router = AsyncMock(return_value="NATIVE /card MUST NOT RUN")
    router = _wrap_handle_card_action_event(native_router)

    with _host_modules(entries=entries, resolve_gateway_clarify=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _send_one_clarify(
            adapter, ctrl=ctrl, clarify_id=cid, choices=["A", "B"]
        )
        await router(
            adapter,
            _card_action_data(clarify_id=cid, option="0", card_message_id=card_message_id),
        )

    resolver.assert_not_called()
    native_router.assert_not_awaited()
    assert _clarify_records[cid]["state"] == "PENDING"


@pytest.mark.asyncio
async def test_same_chat_different_clarify_card_id_is_fail_closed() -> None:
    """A clarify id cannot be claimed from another card in the same chat."""
    from hermes_lark_streaming.patching import (
        _clarify_records,
        _wrap_handle_card_action_event,
    )

    cid = "clarify-card-scope-v27"
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    client = _client(card_message_id="clarify-actual-card-v27")
    ctrl = _controller(client)
    resolver = MagicMock()
    entries = {cid: SimpleNamespace(multi_select=False)}
    native_router = AsyncMock(return_value="NATIVE /card MUST NOT RUN")
    router = _wrap_handle_card_action_event(native_router)

    with _host_modules(entries=entries, resolve_gateway_clarify=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _send_one_clarify(
            adapter, ctrl=ctrl, clarify_id=cid, choices=["A", "B"]
        )
        await router(
            adapter,
            _card_action_data(
                clarify_id=cid,
                option="0",
                card_message_id=f"{card_message_id}-forged",
            ),
        )

    resolver.assert_not_called()
    native_router.assert_not_awaited()
    client.update_card.assert_not_awaited()
    assert _clarify_records[cid]["state"] == "PENDING"


@pytest.mark.asyncio
async def test_expired_clarify_callback_never_resolves_and_server_retires_inert_card() -> None:
    """A missed host timeout cannot leave an old Clarify card actionable."""
    from hermes_lark_streaming.patching import (
        _clarify_records,
        _wrap_handle_card_action_event,
    )
    from hermes_lark_streaming.patching.adapter import _CLARIFY_TTL_SEC

    cid = "clarify-expired-v27"
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    client = _client(card_message_id="clarify-expired-card-v27")
    ctrl = _controller(client)
    resolver = MagicMock()
    entries = {cid: SimpleNamespace(multi_select=False)}
    native_router = AsyncMock(return_value="NATIVE /card MUST NOT RUN")
    router = _wrap_handle_card_action_event(native_router)

    with _host_modules(entries=entries, resolve_gateway_clarify=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _send_one_clarify(
            adapter, ctrl=ctrl, clarify_id=cid, choices=["A", "B"]
        )
        _clarify_records[cid]["created_at"] = time.monotonic() - _CLARIFY_TTL_SEC - 1
        await router(
            adapter,
            _card_action_data(clarify_id=cid, option="0", card_message_id=card_message_id),
        )

    resolver.assert_not_called()
    native_router.assert_not_awaited()
    assert cid not in _clarify_records
    client.update_card.assert_awaited_once()
    assert client.update_card.await_args.args[0] == "clarify-expired-card-v27"
    terminal_card = client.update_card.await_args.args[1]
    assert "expired" in json.dumps(terminal_card, ensure_ascii=False).casefold()
    assert _interactive_tags(terminal_card) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("clarify_action", "input_value", "form_value"),
    [
        pytest.param("input_submit", "typed answer", None, id="enter-submission"),
        pytest.param("button_submit", "", {"clarify_input": "typed answer"}, id="button-submission"),
    ],
)
async def test_failed_text_submission_keeps_card_retryable_without_wrong_retirement(
    clarify_action: str,
    input_value: str,
    form_value: dict[str, object] | None,
) -> None:
    """Text input failure restores PENDING; retry can resolve the same id once."""
    from hermes_lark_streaming.patching import (
        _clarify_records,
        _wrap_handle_card_action_event,
    )

    cid = f"clarify-text-retry-{clarify_action}-v27"
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    client = _client()
    ctrl = _controller(client)
    resolver = MagicMock(side_effect=[RuntimeError("temporary host failure"), None])
    entries = {cid: SimpleNamespace(multi_select=False)}
    native_router = AsyncMock(return_value="NATIVE /card MUST NOT RUN")
    router = _wrap_handle_card_action_event(native_router)

    with _host_modules(entries=entries, resolve_gateway_clarify=resolver), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        card_message_id = await _send_one_clarify(
            adapter, ctrl=ctrl, clarify_id=cid, choices=["A", "B"]
        )
        await router(
            adapter,
            _card_action_data(
                clarify_id=cid,
                clarify_action=clarify_action,
                option=None,
                card_message_id=card_message_id,
                input_value=input_value,
                form_value=form_value,
            ),
        )

        # Resolver failure must not erase local state or turn the old card
        # terminal; the submitted retry callback can safely reuse selection.
        assert _clarify_records[cid]["state"] == "PENDING"
        assert resolver.call_args.args[:2] == (cid, "typed answer")

        await router(
            adapter,
            _card_action_data(
                clarify_id=cid,
                clarify_action="retry_submit",
                option=None,
                card_message_id=card_message_id,
            ),
        )

    assert resolver.call_count == 2
    assert resolver.call_args.args[:2] == (cid, "typed answer")
    assert cid not in _clarify_records
    native_router.assert_not_awaited()


class _BlockingClient:
    """Makes the first retirement await observable without a real Feishu API."""

    def __init__(self) -> None:
        self.update_started = asyncio.Event()
        self.release_update = asyncio.Event()
        self.updated: list[tuple[str, dict]] = []

    async def send_card_to_chat(self, _chat_id: str, _card: dict) -> str:
        return "clarify-retire-card-v27"

    async def reply_card(
        self, _reply_to: str, _card: dict, *, reply_in_thread: bool = False
    ) -> str:
        return "clarify-retire-card-v27"

    async def update_card(self, message_id: str, card: dict) -> None:
        self.updated.append((message_id, card))
        self.update_started.set()
        await self.release_update.wait()


def _install_retire_method(adapter: _Adapter) -> None:
    """Model the direct class method Hermes capability-detects at runtime."""
    from hermes_lark_streaming.patching import retire_clarify_card

    type(adapter).retire_clarify_card = retire_clarify_card


@pytest.mark.asyncio
async def test_retire_snapshots_and_pops_before_first_await_then_updates_an_inert_card() -> None:
    """Retirement prevents a late click from resolving while Feishu update waits."""
    from hermes_lark_streaming.patching import _clarify_records

    cid = "clarify-retire-v27"
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    _install_retire_method(adapter)
    client = _BlockingClient()
    ctrl = _controller(client)
    entries = {cid: SimpleNamespace(multi_select=False)}

    with _host_modules(entries=entries, resolve_gateway_clarify=MagicMock()), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        await _send_one_clarify(adapter, ctrl=ctrl, clarify_id=cid, choices=["A", "B"])
        task = asyncio.create_task(adapter.retire_clarify_card(cid, "TEXT_RESOLVED"))
        try:
            # If the method awaited before its locked snapshot+pop, this first
            # scheduler turn would still leave the record live.
            await asyncio.sleep(0)
            assert cid not in _clarify_records
            await asyncio.wait_for(client.update_started.wait(), timeout=1)
            assert cid not in _clarify_records
        finally:
            client.release_update.set()
            await task

    assert client.updated[0][0] == "clarify-retire-card-v27"
    assert _interactive_tags(client.updated[0][1]) == []


@pytest.mark.asyncio
async def test_unknown_retire_is_idempotent_noop_and_rejected_text_does_not_kill_live_card() -> None:
    """Only a real host close event retires; unknown/rejected events stay safe."""
    from hermes_lark_streaming.patching import _clarify_records

    cid = "clarify-rejected-v27"
    adapter = _Adapter()
    _mark_adapter_patched(adapter)
    _install_retire_method(adapter)
    client = _client()
    ctrl = _controller(client)
    entries = {cid: SimpleNamespace(multi_select=False)}

    with _host_modules(entries=entries, resolve_gateway_clarify=MagicMock()), patch(
        "hermes_lark_streaming.controller.get_controller", return_value=ctrl
    ), patch("hermes_lark_streaming.patching.adapter._register_gateway_card"):
        await adapter.retire_clarify_card("never-registered", "TEXT_RESOLVED")
        client.update_card.assert_not_awaited()
        adapter.send.assert_not_awaited()

        await _send_one_clarify(adapter, ctrl=ctrl, clarify_id=cid, choices=["A", "B"])
        await adapter.retire_clarify_card(cid, "TEXT_REJECTED_PROSE")

    assert cid in _clarify_records
    client.update_card.assert_not_awaited()
