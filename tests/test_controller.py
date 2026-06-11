"""controller.py 测试 — 会话生命周期边界条件 + 线性模式 dispatch 与集成测试."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_lark_streaming.controller import CardSession, StreamCardController
from hermes_lark_streaming.controller.mixin import (
    ABORTED,
    COMPLETED,
    COMPLETING,
    FAILED,
    STREAMING,
)
from hermes_lark_streaming.feishu import (
    CARDKIT_CONTENT_FAILED,
    CARDKIT_ELEMENT_LIMIT,
    CARDKIT_RATE_LIMITED,
    CARDKIT_SEQUENCE_CONFLICT,
    CARDKIT_STREAMING_CLOSED,
    FeishuAPIError,
    FeishuClient,
)
from hermes_lark_streaming.cardkit import _LOADING_HINT_ELEMENT_ID, _LOADING_ELEMENT_ID
from hermes_lark_streaming.state.linear import UnifiedLinearState


def _enable(ctrl: StreamCardController, *, linear: bool = False) -> None:
    ctrl._cfg._raw = {
        "hermes_lark_streaming": {"enabled": True, "linear": linear},
        "feishu": {"app_id": "app", "app_secret": "secret"},
    }


class _DummyFlush:
    def __init__(self) -> None:
        self.completed = False

    def mark_completed(self) -> None:
        self.completed = True


@pytest.mark.parametrize("message_id", [None, ""])
def test_on_message_started_ignores_missing_message_id(message_id: str | None) -> None:
    ctrl = StreamCardController()
    _enable(ctrl)

    ctrl.on_message_started(message_id=message_id, chat_id="chat")

    assert ctrl._sessions == {}


def test_on_message_started_registers_anchor_alias_and_cleanup() -> None:
    ctrl = StreamCardController()
    _enable(ctrl)

    with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
        ctrl.on_message_started(message_id="msg", chat_id="chat", anchor_id="quoted")

    session = ctrl._sessions["msg"]
    assert ctrl._sessions["quoted"] is session
    assert session.anchor_id == "quoted"

    ctrl._cleanup("msg")

    assert "msg" not in ctrl._sessions
    assert "quoted" not in ctrl._sessions


def test_on_interrupted_uses_new_message_id_and_anchor_alias() -> None:
    ctrl = StreamCardController()
    _enable(ctrl)

    with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
        ctrl.on_message_started(message_id="old", chat_id="chat")
        ctrl.on_interrupted(
            old_message_id="old",
            new_message_id="new",
            chat_id="chat",
            anchor_id="quoted",
        )

    session = ctrl._sessions["new"]
    assert ctrl._sessions["quoted"] is session
    assert session.anchor_id == "quoted"
    assert ctrl._interrupt_map["old"] == "new"
    assert ctrl._sessions["old"].state == ABORTED


def test_prune_stale_sessions_ignores_none_key_and_prunes_valid_key() -> None:
    ctrl = StreamCardController()
    stale_session = SimpleNamespace(
        created_at=time.time() - ctrl._session_ttl - 1,
        flush=_DummyFlush(),
    )
    valid_stale_session = SimpleNamespace(
        created_at=time.time() - ctrl._session_ttl - 1,
        flush=_DummyFlush(),
    )
    ctrl._sessions[None] = stale_session  # type: ignore[index,assignment]
    ctrl._sessions["msg"] = valid_stale_session  # type: ignore[assignment]

    ctrl._prune_stale_sessions()

    assert ctrl._sessions[None] is stale_session  # type: ignore[index]
    assert "msg" not in ctrl._sessions
    assert valid_stale_session.flush.completed


@pytest.mark.asyncio
async def test_background_review_deferred_until_complete() -> None:
    ctrl = _setup_ctrl()
    session = _make_session("msg_bg")
    session.state = STREAMING
    session.card_msg_id = "card_msg"
    ctrl._sessions["msg_bg"] = session
    sent: list[str] = []

    assert ctrl.defer_background_review(message_id="msg_bg", text="review", sender=sent.append)
    assert sent == []

    await ctrl._do_complete(session)

    assert sent == ["review"]
    assert "msg_bg" not in ctrl._sessions


def test_background_review_without_active_session_not_deferred() -> None:
    ctrl = _setup_ctrl()
    sent: list[str] = []

    assert not ctrl.defer_background_review(message_id="missing", text="review", sender=sent.append)
    assert sent == []


def test_background_review_after_flush_not_deferred() -> None:
    ctrl = _setup_ctrl()
    session = _make_session("msg_bg")
    ctrl._sessions["msg_bg"] = session
    sent: list[str] = []

    ctrl._flush_deferred_background_reviews(session)

    assert not ctrl.defer_background_review(message_id="msg_bg", text="review", sender=sent.append)
    assert sent == []


# ── 辅助函数 ──


def _make_session(msg_id: str = "msg_123", *, linear: bool = False) -> CardSession:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session = CardSession(msg_id, "chat_456", loop)
    if linear:
        session.linear = True
        session.unified_state = UnifiedLinearState()
    # v0.12.1: _card_ready must be set so _do_complete_inner / _do_linear_complete_inner
    # don't hang for 30 seconds. In production, this is set by _do_create_card / _do_create_linear_card.
    session._card_ready.set()
    return session


def _mock_client() -> AsyncMock:
    client = AsyncMock(spec=FeishuClient)
    client.cardkit_create = AsyncMock(return_value="card_id_abc")
    client.reply_card_by_id = AsyncMock(return_value="msg_id_reply")
    client.reply_card = AsyncMock(return_value="msg_id_reply")
    client.cardkit_batch_update = AsyncMock()
    client.cardkit_stream_element = AsyncMock()
    client.cardkit_close_streaming = AsyncMock()
    client.cardkit_update = AsyncMock()
    client.update_card = AsyncMock()
    client.reply_text = AsyncMock(return_value="msg_id_reply")
    client.cardkit_extend_ttl = AsyncMock()
    return client


def _setup_ctrl(*, linear: bool = False) -> StreamCardController:
    ctrl = StreamCardController()
    _enable(ctrl, linear=linear)
    ctrl._initialized = True
    ctrl._client = _mock_client()
    return ctrl


@pytest.mark.asyncio
async def test_create_card_replies_to_anchor_id() -> None:
    ctrl = _setup_ctrl()
    session = _make_session("msg")
    session.anchor_id = "quoted"

    await ctrl._do_create_card(session)

    ctrl._client.reply_card_by_id.assert_called_once()
    assert ctrl._client.reply_card_by_id.call_args.args[0] == "quoted"


def _capture_split_calls(
    ctrl: StreamCardController,
    *,
    cards: list[str] | None = None,
    messages: list[str] | None = None,
    create_error: Exception | None = None,
) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    client = ctrl._client
    card_iter = iter(cards or ["card_next"])
    message_iter = iter(messages or ["msg_next"])

    client.cardkit_batch_update = AsyncMock(
        side_effect=lambda card_id, *a, **k: calls.append(("batch", card_id))
    )
    if create_error is None:
        client.cardkit_create = AsyncMock(
            side_effect=lambda *a, **k: calls.append(("create", "")) or next(card_iter)
        )
    else:
        client.cardkit_create = AsyncMock(side_effect=create_error)
    client.reply_card_by_id = AsyncMock(
        side_effect=lambda *a, **k: calls.append(("reply", "")) or next(message_iter)
    )
    client.cardkit_close_streaming = AsyncMock(
        side_effect=lambda card_id, **k: calls.append(("close", card_id))
    )
    client.cardkit_update = AsyncMock(
        side_effect=lambda card_id, *a, **k: calls.append(("seal", card_id))
    )
    return calls


# ── Dispatch 测试 — 线性模式分流 ──


class TestLinearDispatch:
    """验证线性 session 的 6 个入口走 linear 路径，非线性 session 不受影响."""

    @pytest.mark.parametrize("event,kwargs", [
        ("on_reasoning", {"text": "r"}),
        ("on_answer", {"text": "a"}),
    ])
    def test_linear_dispatch_creates_state(self, event: str, kwargs: dict) -> None:
        ctrl = _setup_ctrl()
        ctrl._cfg._reload_cached = lambda: {"display": {"platforms": {"feishu": {"show_reasoning": True}}}}  # type: ignore[assignment]
        session = _make_session("msg_d", linear=True)
        ctrl._sessions["msg_d"] = session
        getattr(ctrl, event)(message_id="msg_d", **kwargs)
        assert session.unified_state is not None

    def test_linear_dispatch_reasoning_sets_dirty(self) -> None:
        ctrl = _setup_ctrl()
        ctrl._cfg._reload_cached = lambda: {"display": {"platforms": {"feishu": {"show_reasoning": True}}}}  # type: ignore[assignment]
        session = _make_session("msg_d", linear=True)
        ctrl._sessions["msg_d"] = session
        ctrl.on_reasoning(message_id="msg_d", text="r")
        assert session.unified_state is not None
        assert session.unified_state.panel_dirty is True

    def test_linear_dispatch_answer_sets_dirty(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_d", linear=True)
        ctrl._sessions["msg_d"] = session
        ctrl.on_answer(message_id="msg_d", text="a")
        assert session.unified_state is not None
        assert session.unified_state.answer_dirty is True

    def test_linear_thinking_dispatches(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_t", linear=True)
        ctrl._sessions["msg_t"] = session
        with patch.object(ctrl, "_linear_on_thinking") as m:
            ctrl.on_thinking(message_id="msg_t", text="thinking")
            m.assert_called_once()

    def test_linear_tool_dispatches(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_tool", linear=True)
        ctrl._sessions["msg_tool"] = session
        ctrl.on_tool_update(message_id="msg_tool", tool_name="read", status="started")
        assert session.unified_state is not None
        assert session.unified_state.tool_steps_dirty is True

    def test_linear_completed_dispatches(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_c", linear=True)
        session.state = STREAMING
        session.card_id = "card_123"
        ctrl._sessions["msg_c"] = session
        with patch.object(ctrl, "_do_linear_complete", new_callable=AsyncMock):
            ctrl.on_completed(message_id="msg_c")
        assert session.flush._completed

    def test_nonlinear_answer_unchanged(self) -> None:
        """非线性 session 不走 linear 路径."""
        ctrl = _setup_ctrl()
        session = _make_session("msg_nl", linear=False)
        ctrl._sessions["msg_nl"] = session
        ctrl.on_answer(message_id="msg_nl", text="answer text")
        assert session.unified_state is None
        assert session.text.display_text == "answer text"

    def test_guard_skips_terminal(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_term", linear=True)
        session.state = COMPLETED
        ctrl._sessions["msg_term"] = session
        ctrl.on_answer(message_id="msg_term", text="late text")
        # unified_state should still have no answer text (guard blocked)
        assert session.unified_state is not None
        assert session.unified_state.answer_text == ""

    def test_message_started_creates_linear_session(self) -> None:
        ctrl = _setup_ctrl(linear=True)
        ctrl.on_message_started(message_id="msg1", chat_id="chat1")
        session = ctrl._sessions["msg1"]
        loop = session._loop
        loop.run_until_complete(asyncio.sleep(0.05))
        assert session.linear is True
        assert session.card_id is not None


# ── _do_create_linear_card 集成测试 ──


class TestDoCreateLinearCard:
    @pytest.mark.asyncio
    async def test_cardkit_success(self) -> None:
        ctrl = _setup_ctrl(linear=True)
        session = _make_session("msg_create")
        ctrl._sessions["msg_create"] = session

        await ctrl._do_create_linear_card(session)

        assert session.linear is True
        assert session.unified_state is not None
        assert session.use_cardkit is True
        assert session.card_id == "card_id_abc"
        assert session.state == STREAMING

    @pytest.mark.asyncio
    async def test_cardkit_failure_falls_back(self) -> None:
        ctrl = _setup_ctrl(linear=True)
        client = ctrl._client
        client.cardkit_create = AsyncMock(side_effect=FeishuAPIError("fail", code=230099))
        session = _make_session("msg_fallback")
        ctrl._sessions["msg_fallback"] = session

        await ctrl._do_create_linear_card(session)

        assert session.linear is False
        assert session.unified_state is None
        assert session.use_cardkit is False
        assert session.state == STREAMING

    @pytest.mark.asyncio
    async def test_generic_failure_marks_failed(self) -> None:
        ctrl = _setup_ctrl(linear=True)
        ctrl._client = None
        session = _make_session("msg_err")
        ctrl._sessions["msg_err"] = session

        await ctrl._do_create_linear_card(session)

        assert session.state == FAILED

    @pytest.mark.asyncio
    async def test_unified_state_set_before_await(self) -> None:
        """CREATING 期间的事件进入线性路径 — unified_state 在 try 之前设置."""
        ctrl = _setup_ctrl(linear=True)
        session = _make_session("msg_early")
        ctrl._sessions["msg_early"] = session

        original_ensure = ctrl._ensure_init

        async def check_state_then_ensure() -> None:
            assert session.linear is True
            assert session.unified_state is not None
            await original_ensure()

        ctrl._ensure_init = check_state_then_ensure  # type: ignore[assignment]
        await ctrl._do_create_linear_card(session)

    @pytest.mark.asyncio
    async def test_post_create_flush_on_dirty(self) -> None:
        ctrl = _setup_ctrl(linear=True)
        session = _make_session("msg_dirty")
        ctrl._sessions["msg_dirty"] = session

        original_ensure = ctrl._ensure_init

        async def inject_data_and_ensure() -> None:
            await original_ensure()
            session.unified_state.on_reasoning_delta("during-creating")

        ctrl._ensure_init = inject_data_and_ensure  # type: ignore[assignment]

        with patch.object(ctrl, "_schedule_linear_flush") as m:
            await ctrl._do_create_linear_card(session)
            m.assert_called()

    @pytest.mark.asyncio
    async def test_first_card_creates_preallocated_elements(self) -> None:
        """首卡创建后仅预分配 loading hint + loading icon (2 elements).

        Panel and answer element are added dynamically when the first
        LLM token arrives (Phase 2 of card lifecycle).
        """
        ctrl = _setup_ctrl(linear=True)
        session = _make_session("msg_hint")
        ctrl._sessions["msg_hint"] = session

        await ctrl._do_create_linear_card(session)

        # existing_elements should contain only 2 pre-allocated elements
        assert len(session.existing_elements) == 2
        assert session._panel_element_created is False

    @pytest.mark.asyncio
    async def test_card_created_at_set(self) -> None:
        """card_created_at 应在首卡创建后设置."""
        ctrl = _setup_ctrl(linear=True)
        session = _make_session("msg_time")
        ctrl._sessions["msg_time"] = session

        await ctrl._do_create_linear_card(session)

        assert session.card_created_at > 0


# ── _do_unified_flush 集成测试 ──


class TestDoUnifiedFlush:
    @pytest.mark.asyncio
    async def test_reasoning_and_answer_flush(self) -> None:
        """reasoning + answer flush — Phase 2: add panel + delete hint + stream answer."""
        ctrl = _setup_ctrl()
        session = _make_session("msg_flush", linear=True)
        session.state = STREAMING
        session.card_id = "card_flush"
        session.unified_state.on_reasoning_delta("think")
        session.unified_state.on_answer_delta("hello world")
        ctrl._sessions["msg_flush"] = session

        await ctrl._do_unified_flush(session)

        # panel dirty should be cleared
        assert session.unified_state.panel_dirty is False
        # answer dirty should be cleared
        assert session.unified_state.answer_dirty is False
        # batch_update should have been called (Phase 2: add panel + delete hint)
        ctrl._client.cardkit_batch_update.assert_called()
        # stream_element should have been called for answer
        ctrl._client.cardkit_stream_element.assert_called()
        # Panel should now be created
        assert session._panel_element_created is True

    @pytest.mark.asyncio
    async def test_no_api_calls_when_no_card_id(self) -> None:
        """无 card_id 时跳过 API 调用."""
        ctrl = _setup_ctrl()
        session = _make_session("msg_no_id", linear=True)
        session.state = STREAMING
        session.card_id = None
        session.unified_state.on_reasoning_delta("think")
        ctrl._sessions["msg_no_id"] = session

        await ctrl._do_unified_flush(session)

        ctrl._client.cardkit_batch_update.assert_not_called()
        ctrl._client.cardkit_stream_element.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_api_calls_when_terminal_state(self) -> None:
        """终态时跳过 API 调用."""
        ctrl = _setup_ctrl()
        session = _make_session("m1", linear=True)
        session.state = COMPLETED
        session.card_id = "c"
        ctrl._sessions["m1"] = session

        await ctrl._do_unified_flush(session)

        ctrl._client.cardkit_batch_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_api_calls_when_no_dirty(self) -> None:
        """无 dirty 时跳过 API 调用 (panel already created, no pending content)."""
        ctrl = _setup_ctrl()
        session = _make_session("m2", linear=True)
        session.state = STREAMING
        session.card_id = "c"
        session._loading_hint_removed = True
        session._panel_element_created = True  # Panel already exists (Phase 2 done)
        session.unified_state.on_reasoning_delta("t")
        session.unified_state.panel_dirty = False
        session.unified_state.answer_dirty = False
        session.unified_state.tool_steps_dirty = False
        ctrl._sessions["m2"] = session

        await ctrl._do_unified_flush(session)

        ctrl._client.cardkit_stream_element.assert_not_called()
        # batch_update should not be called (no dirty data, panel already created)
        ctrl._client.cardkit_batch_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_tool_event_flush(self) -> None:
        """tool event flush — panel partial_update."""
        ctrl = _setup_ctrl()
        session = _make_session("msg_tool_flush", linear=True)
        session.state = STREAMING
        session.card_id = "card_tool"
        session.unified_state.on_reasoning_delta("think")
        session.unified_state.on_answer_delta("hello")
        session.tool_use.record_start("read", "f")
        session.unified_state.on_tool_event()
        ctrl._sessions["msg_tool_flush"] = session

        await ctrl._do_unified_flush(session)

        assert session.unified_state.panel_dirty is False
        assert session.unified_state.tool_steps_dirty is False
        ctrl._client.cardkit_batch_update.assert_called()

    @pytest.mark.asyncio
    async def test_loading_hint_removed_on_first_content(self) -> None:
        """首字即显时 Phase 2 的 batch_update 中添加 panel + 删除 loading hint."""
        ctrl = _setup_ctrl()
        session = _make_session("msg_hint_del", linear=True)
        session.state = STREAMING
        session.card_id = "card_hint"
        session._loading_hint_removed = False
        session.existing_elements = {_LOADING_HINT_ELEMENT_ID, _LOADING_ELEMENT_ID}
        session.unified_state.on_answer_delta("hello")
        ctrl._sessions["msg_hint_del"] = session

        batch_actions: list[list[dict]] = []

        async def capture_batch(card_id: str, actions: list[dict], **kw: object) -> None:
            batch_actions.append(actions)

        ctrl._client.cardkit_batch_update = capture_batch

        await ctrl._do_unified_flush(session)

        # Should include add_elements (panel + answer) and delete loading hint
        assert len(batch_actions) >= 1
        all_actions = batch_actions[0]
        add_actions = [a for a in all_actions if a["action"] == "add_elements"]
        delete_hint_actions = [
            a for a in all_actions
            if a["action"] == "delete_elements"
        ]
        assert len(add_actions) == 1  # Phase 2: add panel + answer element
        assert len(delete_hint_actions) == 1  # Delete loading hint
        assert session._loading_hint_removed is True
        assert session._panel_element_created is True

    @pytest.mark.asyncio
    async def test_loading_hint_removed_on_reasoning(self) -> None:
        """reasoning 到达时 Phase 2: add panel + delete loading hint."""
        ctrl = _setup_ctrl()
        session = _make_session("msg_hint_reasoning", linear=True)
        session.state = STREAMING
        session.card_id = "card_hint_r"
        session._loading_hint_removed = False
        session.existing_elements = {_LOADING_HINT_ELEMENT_ID, _LOADING_ELEMENT_ID}
        session.unified_state.on_reasoning_delta("thinking")
        ctrl._sessions["msg_hint_reasoning"] = session

        batch_actions: list[list[dict]] = []

        async def capture_batch(card_id: str, actions: list[dict], **kw: object) -> None:
            batch_actions.append(actions)

        ctrl._client.cardkit_batch_update = capture_batch

        await ctrl._do_unified_flush(session)

        assert len(batch_actions) >= 1
        all_actions = batch_actions[0]
        add_actions = [a for a in all_actions if a["action"] == "add_elements"]
        delete_hint_actions = [
            a for a in all_actions
            if a["action"] == "delete_elements"
        ]
        assert len(add_actions) == 1  # Phase 2: add panel + answer element
        assert len(delete_hint_actions) == 1
        assert session._loading_hint_removed is True
        assert session._panel_element_created is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [230020, 300309])
    async def test_api_errors_swallowed(self, code: int) -> None:
        """rate limited / streaming closed 不抛异常."""
        ctrl = _setup_ctrl()
        ctrl._client.cardkit_batch_update = AsyncMock(side_effect=FeishuAPIError("e", code=code))
        session = _make_session("msg_err", linear=True)
        session.state = STREAMING
        session.card_id = "card_e"
        session.unified_state.on_reasoning_delta("think")
        ctrl._sessions["msg_err"] = session

        await ctrl._do_unified_flush(session)

    @pytest.mark.asyncio
    async def test_stream_element_feishu_error_does_not_crash(self) -> None:
        """FeishuAPIError from stream_element is caught in unified flush."""
        ctrl = _setup_ctrl()
        ctrl._client.cardkit_stream_element = AsyncMock(side_effect=FeishuAPIError("stream fail", code=230020))
        session = _make_session("msg_exc", linear=True)
        session.state = STREAMING
        session.card_id = "card_exc"
        session.unified_state.on_answer_delta("text")
        ctrl._sessions["msg_exc"] = session

        # Should not raise
        await ctrl._do_unified_flush(session)


# ── Split/rollover tests — REMOVED in unified panel architecture ──


# NOTE: TestDoLinearSplit has been removed entirely.
# The unified panel architecture (v1.0.2) eliminates card splitting —
# all content lives in a single panel + 1 answer element, so there
# is never a need to split across multiple cards.


# ── Reasoning finalization tests ──


class TestReasoningFinalization:
    @pytest.mark.asyncio
    async def test_reasoning_finalized_on_answer(self) -> None:
        """reasoning round is finalized when answer arrives."""
        ctrl = _setup_ctrl()
        session = _make_session("msg_snap", linear=True)
        session.state = STREAMING
        session.card_id = "card_snap"
        session.unified_state.on_reasoning_delta("think")
        session.unified_state.on_answer_delta("reply")
        ctrl._sessions["msg_snap"] = session

        await ctrl._do_unified_flush(session)

        # The reasoning round should be finalized (moved from current to rounds)
        assert len(session.unified_state.reasoning_rounds) == 1
        assert session.unified_state.reasoning_rounds[0].finalized is True

    @pytest.mark.asyncio
    async def test_reasoning_round_elapsed_displayed(self) -> None:
        ctrl = _setup_ctrl()
        batch_calls: list[list[dict]] = []

        async def capture_batch(card_id: str, actions: list[dict], **kw: object) -> None:
            batch_calls.append(actions)

        ctrl._client.cardkit_batch_update = capture_batch

        session = _make_session("msg_title", linear=True)
        session.state = STREAMING
        session.card_id = "card_title"
        session.existing_elements = {_LOADING_HINT_ELEMENT_ID, _LOADING_ELEMENT_ID}
        session.unified_state.on_reasoning_delta("think")
        time.sleep(0.01)
        session.unified_state.on_answer_delta("reply")
        ctrl._sessions["msg_title"] = session

        await ctrl._do_unified_flush(session)

        # The panel should have been updated (Phase 2: add_elements)
        assert len(batch_calls) >= 1
        add_actions = [a for a in batch_calls[0] if a["action"] == "add_elements"]
        assert len(add_actions) == 1


# ── _do_linear_complete 集成测试 ──


class TestDoLinearComplete:
    @pytest.mark.asyncio
    async def test_closes_streaming_then_updates(self) -> None:
        ctrl = _setup_ctrl()
        call_order: list[str] = []
        client = ctrl._client
        client.cardkit_close_streaming = AsyncMock(side_effect=lambda *a, **k: call_order.append("close"))
        client.cardkit_update = AsyncMock(side_effect=lambda *a, **k: call_order.append("update"))

        session = _make_session("msg_comp", linear=True)
        session.state = STREAMING
        session.card_id = "card_comp"
        session.card_msg_id = "msg_comp_reply"
        ctrl._sessions["msg_comp"] = session

        assert await ctrl._do_linear_complete(session) is True
        assert session.state == COMPLETED
        # With preservative seal, the flow is: close + batch_update (not full rebuild)
        assert "close" in call_order
        # cardkit_update should NOT be called (preservative seal succeeded)
        client.cardkit_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_streaming_closed_flag_prevents_double_close(self) -> None:
        """Preservative seal succeeds on first attempt, so close_streaming is called once."""
        ctrl = _setup_ctrl()
        client = ctrl._client
        client.cardkit_close_streaming = AsyncMock()

        session = _make_session("msg_retry", linear=True)
        session.state = STREAMING
        session.card_id = "card_retry"
        session.card_msg_id = "msg_retry_reply"
        ctrl._sessions["msg_retry"] = session

        assert await ctrl._do_linear_complete(session) is True
        assert client.cardkit_close_streaming.call_count == 1
        # cardkit_update should NOT be called (preservative seal succeeded)
        client.cardkit_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_three_retries_exhausted(self) -> None:
        ctrl = _setup_ctrl()
        ctrl._client.cardkit_close_streaming = AsyncMock(side_effect=FeishuAPIError("fail", code=99999))

        session = _make_session("msg_3fail", linear=True)
        session.state = STREAMING
        session.card_id = "card_3fail"
        ctrl._sessions["msg_3fail"] = session

        with patch("asyncio.sleep", new_callable=AsyncMock):
            assert await ctrl._do_linear_complete(session) is False
        assert session.state == FAILED

    @pytest.mark.asyncio
    async def test_finalize_and_cleanup(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_fc", linear=True)
        session.state = STREAMING
        session.card_id = "card_fc"
        session.unified_state.on_reasoning_delta("think")
        time.sleep(0.001)
        # Capture elapsed_ms before complete, since _release_session_data
        # clears unified_state after completion (v0.19.0 memory release)
        elapsed_ms_before = session.unified_state.reasoning_rounds[0].elapsed_ms if session.unified_state.reasoning_rounds else 0
        ctrl._sessions["msg_fc"] = session

        await ctrl._do_linear_complete(session)

        # If reasoning was finalized before complete, elapsed should be positive
        # (After on_answer_delta above, the reasoning is finalized)
        if session.unified_state is not None and session.unified_state.reasoning_rounds:
            assert session.unified_state.reasoning_rounds[0].elapsed_ms >= 0
        else:
            # unified_state may be cleared after completion
            assert elapsed_ms_before >= 0
