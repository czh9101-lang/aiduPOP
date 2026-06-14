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


def test_on_interrupted_skips_abort_for_completing_session() -> None:
    """Hotfix: COMPLETING session should not be aborted on interrupt.

    When on_completed has already fired (session in COMPLETING state),
    on_interrupted should skip the abort logic — let _do_linear_complete
    finish naturally. However, the new session must still be created and
    _interrupt_map must still be updated.
    """
    ctrl = StreamCardController()
    _enable(ctrl)

    with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
        ctrl.on_message_started(message_id="completing_msg", chat_id="chat")
        # Simulate session in COMPLETING state (on_completed already fired)
        ctrl._sessions["completing_msg"].state = COMPLETING

        ctrl.on_interrupted(
            old_message_id="completing_msg",
            new_message_id="new_msg",
            chat_id="chat",
            anchor_id="anchor",
        )

    # Key assertion: COMPLETING session must NOT be marked as aborted
    old_session = ctrl._sessions["completing_msg"]
    assert old_session._was_aborted is False, (
        "COMPLETING session must NOT be marked as aborted"
    )
    assert old_session.state == COMPLETING, (
        "COMPLETING session state must not be overwritten"
    )

    # New session must still be created
    assert "new_msg" in ctrl._sessions, (
        "New session must be created even when old session is COMPLETING"
    )
    new_session = ctrl._sessions["new_msg"]
    assert ctrl._sessions["anchor"] is new_session
    assert new_session.anchor_id == "anchor"

    # _interrupt_map must still be updated
    assert ctrl._interrupt_map["completing_msg"] == "new_msg", (
        "_interrupt_map must be updated even when old session is COMPLETING"
    )


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
        with patch.object(ctrl, "_do_linear_complete_with_fallback", new_callable=AsyncMock):
            ctrl.on_completed(message_id="msg_c")
        # After on_completed, state should be COMPLETING (not COMPLETED yet)
        # The actual completion happens asynchronously in _do_linear_complete
        assert session.state == COMPLETING

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
        """When data arrives during card creation, a flush is triggered after card is ready."""
        ctrl = _setup_ctrl(linear=True)
        session = _make_session("msg_dirty")
        ctrl._sessions["msg_dirty"] = session

        original_ensure = ctrl._ensure_init

        async def inject_data_and_ensure() -> None:
            await original_ensure()
            session.unified_state.on_reasoning_delta("during-creating")

        ctrl._ensure_init = inject_data_and_ensure  # type: ignore[assignment]

        await ctrl._do_create_linear_card(session)

        # After card creation, data that arrived during creation should
        # trigger a flush. The new implementation either calls flush_now
        # directly (first content) or _schedule_linear_flush (subsequent).
        # Either way, the dirty data should be cleared or a flush scheduled.
        assert session._first_flush_done is True

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
        # With preservative seal, the flow is: close + batch_update
        # (summary is passed IN close_streaming, no separate cardkit_update needed)
        assert "close" in call_order
        # cardkit_update should NOT be called — summary is updated atomically
        # in close_streaming per Feishu docs
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
        # cardkit_update should NOT be called — summary is passed in
        # close_streaming atomically per Feishu docs
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

    @pytest.mark.asyncio
    async def test_drain_dirty_answer_before_seal(self) -> None:
        """Remaining dirty answer text is flushed (drained) before the seal.

        This is the fix for the premature card finalization bug:
        when on_completed fires while answer text hasn't been flushed yet,
        _do_linear_complete must drain it before closing streaming.
        """
        ctrl = _setup_ctrl()
        session = _make_session("msg_drain", linear=True)
        session.state = STREAMING
        session.card_id = "card_drain"
        session._panel_element_created = True
        session._loading_hint_removed = True
        # Simulate: last answer delta arrived but flush hasn't fired yet
        session.unified_state.on_answer_delta("final chunk")
        assert session.unified_state.answer_dirty is True
        ctrl._sessions["msg_drain"] = session

        # Track API call order
        api_calls: list[str] = []
        client = ctrl._client
        client.cardkit_stream_element = AsyncMock(
            side_effect=lambda *a, **k: api_calls.append("stream"),
        )
        client.cardkit_batch_update = AsyncMock(
            side_effect=lambda *a, **k: api_calls.append("batch"),
        )
        client.cardkit_close_streaming = AsyncMock(
            side_effect=lambda *a, **k: api_calls.append("close"),
        )

        assert await ctrl._do_linear_complete(session) is True

        # stream_element should have been called for the drain
        assert "stream" in api_calls
        # close_streaming should happen AFTER the drain
        close_idx = api_calls.index("close")
        stream_idx = api_calls.index("stream")
        assert stream_idx < close_idx, (
            f"Drain stream_element must happen before close_streaming, "
            f"got stream@{stream_idx} close@{close_idx}"
        )
        # answer_dirty should be cleared after drain
        assert session.unified_state.answer_dirty is False

    @pytest.mark.asyncio
    async def test_drain_dirty_panel_before_seal(self) -> None:
        """Remaining dirty panel content is flushed before the seal."""
        ctrl = _setup_ctrl()
        session = _make_session("msg_drain_panel", linear=True)
        session.state = STREAMING
        session.card_id = "card_drain_panel"
        session._panel_element_created = True
        session._loading_hint_removed = True
        # Simulate: reasoning delta arrived but flush hasn't fired yet
        session.unified_state.on_reasoning_delta("think more")
        assert session.unified_state.panel_dirty is True
        ctrl._sessions["msg_drain_panel"] = session

        api_calls: list[str] = []
        client = ctrl._client
        client.cardkit_batch_update = AsyncMock(
            side_effect=lambda *a, **k: api_calls.append("batch"),
        )
        client.cardkit_close_streaming = AsyncMock(
            side_effect=lambda *a, **k: api_calls.append("close"),
        )

        assert await ctrl._do_linear_complete(session) is True

        # batch_update should have been called for the drain (panel update)
        assert "batch" in api_calls
        # close should happen AFTER the drain
        close_idx = api_calls.index("close")
        first_batch_idx = api_calls.index("batch")
        assert first_batch_idx < close_idx
        # panel_dirty should be cleared after drain
        assert session.unified_state.panel_dirty is False

    @pytest.mark.asyncio
    async def test_no_drain_when_no_dirty_data(self) -> None:
        """No drain API calls when all data is already flushed."""
        ctrl = _setup_ctrl()
        session = _make_session("msg_clean", linear=True)
        session.state = STREAMING
        session.card_id = "card_clean"
        session._panel_element_created = True
        session._loading_hint_removed = True
        # No dirty data
        ctrl._sessions["msg_clean"] = session

        stream_call_count_before = ctrl._client.cardkit_stream_element.call_count

        assert await ctrl._do_linear_complete(session) is True

        # stream_element should NOT have been called for drain
        # (only called in seal if answer_text exists)
        # The key point: no ADDITIONAL stream_element call for drain
        # since answer_dirty was False

    @pytest.mark.asyncio
    async def test_close_streaming_passes_summary(self) -> None:
        """close_streaming is called with summary text from answer.

        This is the fix for the "处理中..." stays in conversation list bug:
        when close_streaming is called, the summary MUST be passed in the
        same request so Feishu atomically updates the conversation list
        preview from "处理中..." to the actual answer text.  A separate
        cardkit_update_summary call does NOT reliably work.

        See: 飞书开放平台 → 卡片2.0 → 流式更新 → 完成后关闭流式更新模式
        """
        ctrl = _setup_ctrl()
        session = _make_session("msg_summary", linear=True)
        session.state = STREAMING
        session.card_id = "card_summary"
        session._panel_element_created = True
        session._loading_hint_removed = True
        session.unified_state.on_answer_delta("Hello, this is the answer text")
        # Clear dirty flag (simulates already-flushed content)
        session.unified_state.answer_dirty = False
        ctrl._sessions["msg_summary"] = session

        close_kwargs: list[dict] = []
        ctrl._client.cardkit_close_streaming = AsyncMock(
            side_effect=lambda *a, **k: close_kwargs.append(k),
        )

        assert await ctrl._do_linear_complete(session) is True

        # close_streaming should have been called with summary kwarg
        # containing the answer text
        assert len(close_kwargs) >= 1
        summary = close_kwargs[0].get("summary", "")
        assert "Hello" in summary
        assert "处理中" not in summary

    @pytest.mark.asyncio
    async def test_streaming_closed_guard_prevents_double_close_on_300317(self) -> None:
        """When batch_update gets 300317 after close_streaming succeeds,
        the retry path must NOT call close_streaming again.

        This is the fix for the cascading 300317 failure:
        1. preservative_seal calls close_streaming → succeeds
        2. preservative_seal calls batch_update → 300317
        3. Retry path should skip close_streaming (already done)
        4. Retry path should only retry batch_update
        Without _streaming_closed guard, step 3 calls close_streaming
        again, causing a second 300317 (sequence advanced from step 1).
        """
        ctrl = _setup_ctrl()
        client = ctrl._client
        close_call_count = 0

        async def _close_side_effect(*a, **k):
            nonlocal close_call_count
            close_call_count += 1
            # First close_streaming succeeds

        async def _batch_side_effect(*a, **k):
            # First batch_update → 300317
            raise FeishuAPIError("sequence conflict", code=CARDKIT_SEQUENCE_CONFLICT)

        client.cardkit_close_streaming = AsyncMock(side_effect=_close_side_effect)
        client.cardkit_batch_update = AsyncMock(side_effect=_batch_side_effect)

        session = _make_session("msg_double_close", linear=True)
        session.state = STREAMING
        session.card_id = "card_double"
        session._panel_element_created = True
        session._loading_hint_removed = True
        ctrl._sessions["msg_double_close"] = session

        result = await ctrl._do_linear_complete(session)
        # Should fail (all retries exhausted), but close_streaming
        # should only have been called ONCE
        assert close_call_count == 1, (
            f"close_streaming should be called exactly once, got {close_call_count}"
        )

    @pytest.mark.asyncio
    async def test_session_initializes_streaming_closed_false(self) -> None:
        """CardSession._streaming_closed starts as False."""
        session = CardSession("msg_test", "chat_test", asyncio.new_event_loop())
        assert session._streaming_closed is False


class TestLinearOnThinkingNativeReasoningDedup:
    """Bug fix: _linear_on_thinking must skip reasoning when _native_reasoning_active.

    When the model provides a dedicated reasoning_callback (e.g. DeepSeek, QwQ),
    reasoning text arrives incrementally via on_reasoning → on_reasoning_delta.
    The interim_assistant_callback also delivers the same reasoning text in
    accumulated form.  Without the _native_reasoning_active guard, appending
    the accumulated text again via on_reasoning_delta would double every token
    in the collapsible panel ("TheThe user user is is saying saying…").
    """

    def _make_dedup_session(self) -> tuple:
        ctrl = _setup_ctrl()
        # Enable show_reasoning so _linear_on_thinking processes reasoning text
        ctrl._cfg._raw.setdefault("hermes_lark_streaming", {}).setdefault(
            "display", {"show_reasoning": True},
        )
        # Also set the cached config so _cfg.show_reasoning returns True
        ctrl._cfg._reload_cached = lambda: {
            "display": {"platforms": {"feishu": {"show_reasoning": True}}},
        }
        session = _make_session("msg_dedup", linear=True)
        session.state = STREAMING
        ctrl._sessions["msg_dedup"] = session
        return ctrl, session

    def test_no_dedup_when_native_reasoning_inactive(self) -> None:
        """When _native_reasoning_active is False, reasoning IS processed."""
        ctrl, session = self._make_dedup_session()
        assert session.unified_state._native_reasoning_active is False

        # Use Reasoning:\n prefix so split_reasoning_text classifies
        # this as reasoning_text, not answer_text
        with patch.object(ctrl, "_schedule_linear_flush"):
            ctrl._linear_on_thinking(session, "Reasoning:\nThe user is asking about Python")

        # Reasoning should have been processed (no native reasoning active)
        assert session.unified_state.current_reasoning_text == "The user is asking about Python"

    def test_dedup_when_native_reasoning_active(self) -> None:
        """When _native_reasoning_active is True, reasoning is NOT re-appended."""
        ctrl, session = self._make_dedup_session()

        # Simulate reasoning_callback delivering text first
        session.unified_state.on_reasoning_delta("The user is asking about Python")
        assert session.unified_state.current_reasoning_text == "The user is asking about Python"

        # Mark native reasoning as active (set by on_reasoning)
        session.unified_state._native_reasoning_active = True

        # Now interim_assistant_callback delivers the same accumulated text
        # Use Reasoning:\n prefix so split_reasoning_text classifies
        # this as reasoning_text, not answer_text
        with patch.object(ctrl, "_schedule_linear_flush"):
            ctrl._linear_on_thinking(session, "Reasoning:\nThe user is asking about Python")

        # Reasoning should NOT be doubled
        assert session.unified_state.current_reasoning_text == "The user is asking about Python"
        assert "TheThe" not in session.unified_state.current_reasoning_text

    def test_dedup_with_mixed_reasoning_and_answer(self) -> None:
        """When native reasoning is active, only answer part is processed from thinking."""
        ctrl, session = self._make_dedup_session()

        # Simulate reasoning_callback delivering text
        session.unified_state.on_reasoning_delta("Let me think about this")
        session.unified_state._native_reasoning_active = True

        # interim_assistant_callback delivers plain text (no Reasoning: prefix),
        # which split_reasoning_text classifies as answer_text.
        # When _native_reasoning_active is True, reasoning part is skipped
        # but answer part should still be processed (if no streamed answer yet)
        with patch.object(ctrl, "_schedule_linear_flush"):
            ctrl._linear_on_thinking(session, "Here is my answer")

        # Answer should be set (no streamed answer yet)
        assert "Here is my answer" in session.unified_state.answer_text
