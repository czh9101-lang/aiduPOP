"""controller.py 测试 — 会话生命周期边界条件 + 线性模式 dispatch 与集成测试."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_lark_streaming.controller import CardSession, StreamCardController
from hermes_lark_streaming.controller_linear_mixin import _estimate_segment_elements
from hermes_lark_streaming.controller_mixin import (
    ABORTED,
    COMPLETED,
    FAILED,
    STREAMING,
)
from hermes_lark_streaming.feishu import (
    CARDKIT_CONTENT_FAILED,
    CARDKIT_ELEMENT_LIMIT,
    CARDKIT_RATE_LIMITED,
    CARDKIT_STREAMING_CLOSED,
    FeishuAPIError,
    FeishuClient,
)
from hermes_lark_streaming.linear import LinearState, Segment


def _enable(ctrl: StreamCardController, *, linear: bool = False) -> None:
    ctrl._cfg._raw = {
        "streaming": {"enabled": True, "linear": linear},
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
        image_resolver=None,
    )
    valid_stale_session = SimpleNamespace(
        created_at=time.time() - ctrl._session_ttl - 1,
        flush=_DummyFlush(),
        image_resolver=None,
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
        session.linear_state = LinearState()
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

    @pytest.mark.parametrize("event,kwargs,seg_type", [
        ("on_reasoning", {"text": "r"}, "reasoning"),
        ("on_answer", {"text": "a"}, "answer"),
    ])
    def test_linear_dispatch_creates_segment(self, event: str, kwargs: dict, seg_type: str) -> None:
        ctrl = _setup_ctrl()
        ctrl._cfg._reload_cached = lambda: {"display": {"platforms": {"feishu": {"show_reasoning": True}}}}  # type: ignore[assignment]
        session = _make_session("msg_d", linear=True)
        ctrl._sessions["msg_d"] = session
        getattr(ctrl, event)(message_id="msg_d", **kwargs)
        assert session.linear_state.segments[0].type == seg_type

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
        assert session.linear_state.segments[0].type == "tool"

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
        assert session.linear_state is None
        assert session.text.display_text == "answer text"

    def test_guard_skips_terminal(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_term", linear=True)
        session.state = COMPLETED
        ctrl._sessions["msg_term"] = session
        ctrl.on_answer(message_id="msg_term", text="late text")
        assert len(session.linear_state.segments) == 0

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
        assert session.linear_state is not None
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
        assert session.linear_state is None
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
    async def test_linear_state_set_before_await(self) -> None:
        """CREATING 期间的事件进入线性路径 — linear_state 在 try 之前设置."""
        ctrl = _setup_ctrl(linear=True)
        session = _make_session("msg_early")
        ctrl._sessions["msg_early"] = session

        original_ensure = ctrl._ensure_init

        async def check_state_then_ensure() -> None:
            assert session.linear is True
            assert session.linear_state is not None
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
            session.linear_state.on_reasoning_delta("during-creating")

        ctrl._ensure_init = inject_data_and_ensure  # type: ignore[assignment]

        with patch.object(ctrl, "_schedule_linear_flush") as m:
            await ctrl._do_create_linear_card(session)
            m.assert_called()


# ── _do_linear_flush 集成测试 ──


class TestDoLinearFlush:
    @pytest.mark.asyncio
    async def test_three_step_pipeline(self) -> None:
        """step1 创建元素 → step2 刷文本 → step3 创建 tool 面板."""
        ctrl = _setup_ctrl()
        session = _make_session("msg_flush", linear=True)
        session.state = STREAMING
        session.card_id = "card_flush"
        session.linear_state.on_reasoning_delta("think")
        session.linear_state.on_answer_delta("hello world")
        session.tool_use.record_start("read", "f")
        session.linear_state.on_tool_event(1)
        ctrl._sessions["msg_flush"] = session

        await ctrl._do_linear_flush(session)

        # step1: elements created
        assert session.linear_state.segments[0].created is True
        assert session.linear_state.segments[1].created is True
        # step2: dirty cleared for reasoning + answer (pre-fill optimization)
        assert session.linear_state.segments[0].dirty is False
        assert session.linear_state.segments[1].dirty is False
        # step2: stream_element NOT called because text was pre-filled in batch_update (v0.10.1 optimization)
        ctrl._client.cardkit_stream_element.assert_not_called()
        # step3: tool created
        tool_seg = session.linear_state.segments[2]
        assert tool_seg.created is True

    @pytest.mark.asyncio
    async def test_no_split_keeps_original_single_card_flow(self) -> None:
        """低于阈值时仍是原来的单卡 flush：只 batch/stream 当前 card，不触发拆卡 API."""
        ctrl = _setup_ctrl()
        session = _make_session("msg_no_split", linear=True)
        session.state = STREAMING
        session.card_id = "card_no_split"
        session.element_count = 1
        session.linear_state.on_reasoning_delta("think")
        session.linear_state.on_answer_delta("hello")
        ctrl._sessions["msg_no_split"] = session

        await ctrl._do_linear_flush(session)

        assert session.split_index == 0
        assert session.card_id == "card_no_split"
        assert [s.created for s in session.linear_state.segments] == [True, True]
        assert [s.dirty for s in session.linear_state.segments] == [False, False]
        ctrl._client.cardkit_create.assert_not_called()
        ctrl._client.reply_card_by_id.assert_not_called()
        ctrl._client.cardkit_close_streaming.assert_not_called()
        ctrl._client.cardkit_update.assert_not_called()
        ctrl._client.cardkit_batch_update.assert_called_once()
        # v0.10.1 optimization: text is pre-filled in batch_update, so stream_element is not called
        assert ctrl._client.cardkit_stream_element.call_count == 0

    @pytest.mark.asyncio
    async def test_split_flushes_pending_actions_then_moves_to_next_card(self) -> None:
        """超阈值时先把 pending segment 写入旧卡，再封旧卡并把后续 segment 写入新卡."""
        ctrl = _setup_ctrl()
        calls = _capture_split_calls(ctrl)

        session = _make_session("msg_split", linear=True)
        session.state = STREAMING
        session.card_id = "card_old"
        session.card_msg_id = "msg_old"
        session.element_count = 174
        session.linear_state.on_reasoning_delta("old")
        session.linear_state.segments[0].created = True
        session.linear_state.segments[0].dirty = False
        session.linear_state.on_answer_delta("pending answer")
        session.tool_use.record_start("read", "file")
        session.linear_state.on_tool_event(1)
        ctrl._sessions["msg_split"] = session

        await ctrl._do_linear_flush(session)

        assert calls == [
            ("batch", "card_old"),
            ("create", ""),
            ("reply", ""),
            ("close", "card_old"),
            ("seal", "card_old"),
            ("batch", "card_next"),
        ]
        assert session.card_id == "card_next"
        assert session.card_msg_id == "msg_next"
        assert session.split_index == 2
        assert session.split_disabled is False
        assert session.element_count > 1
        assert [s.created for s in session.linear_state.segments] == [True, True, True]

    @pytest.mark.asyncio
    async def test_tool_growth_rolls_over_at_step_boundary(self) -> None:
        """同一个 tool segment 增长超阈值时，在 step 边界拆到新卡继续更新."""
        ctrl = _setup_ctrl()
        calls = _capture_split_calls(
            ctrl,
            cards=["card_tool_next"],
            messages=["msg_tool_next"],
        )

        session = _make_session("msg_tool_roll", linear=True)
        session.state = STREAMING
        session.card_id = "card_tool_old"
        session.card_msg_id = "msg_tool_old"
        session.tool_use.record_start("read", "file0")
        session.linear_state.on_tool_event(1)
        tool_seg = session.linear_state.segments[0]
        tool_seg.created = True
        tool_seg.element_estimate = _estimate_segment_elements(tool_seg, session.tool_use.build_display_steps())
        session.element_count = 174

        for idx in range(1, 4):
            session.tool_use.record_start("read", f"file{idx}")
        session.linear_state.on_tool_event(len(session.tool_use.build_display_steps()))
        ctrl._sessions["msg_tool_roll"] = session

        await ctrl._do_linear_flush(session)

        assert calls == [
            ("batch", "card_tool_old"),
            ("create", ""),
            ("reply", ""),
            ("close", "card_tool_old"),
            ("seal", "card_tool_old"),
            ("batch", "card_tool_next"),
        ]
        assert session.card_id == "card_tool_next"
        assert session.split_index == 1
        assert len(session.linear_state.segments) == 2
        assert session.linear_state.segments[0].tool_end_offset == 1
        assert session.linear_state.segments[1].tool_offset == 1
        assert session.linear_state.segments[1].created is True

    @pytest.mark.asyncio
    async def test_oversized_new_tool_segment_splits_across_multiple_cards(self) -> None:
        """单次 flush 内 tool steps 很多时，未创建的 tool segment 也会连续分片拆卡."""
        ctrl = _setup_ctrl()
        calls = _capture_split_calls(
            ctrl,
            cards=["card_tool_page_2", "card_tool_page_3"],
            messages=["msg_tool_page_2", "msg_tool_page_3"],
        )

        session = _make_session("msg_tool_many", linear=True)
        session.state = STREAMING
        session.card_id = "card_tool_page_1"
        session.card_msg_id = "msg_tool_page_1"
        session.element_count = 1
        session.tool_use.record_start("check")
        session.linear_state.on_tool_event(1)
        for _ in range(127):
            session.tool_use.record_start("check")
        session.linear_state.on_tool_event(len(session.tool_use.build_display_steps()))
        ctrl._sessions["msg_tool_many"] = session

        await ctrl._do_linear_flush(session)

        assert calls == [
            ("batch", "card_tool_page_1"),
            ("create", ""),
            ("reply", ""),
            ("close", "card_tool_page_1"),
            ("seal", "card_tool_page_1"),
            ("batch", "card_tool_page_2"),
            ("create", ""),
            ("reply", ""),
            ("close", "card_tool_page_2"),
            ("seal", "card_tool_page_2"),
            ("batch", "card_tool_page_3"),
        ]
        assert session.card_id == "card_tool_page_3"
        assert session.card_msg_id == "msg_tool_page_3"
        assert session.split_index == 2
        assert len(session.linear_state.segments) == 3
        assert [s.tool_offset for s in session.linear_state.segments] == [0, 58, 116]
        assert [s.tool_end_offset for s in session.linear_state.segments] == [58, 116, 0]
        assert all(s.created for s in session.linear_state.segments)
        assert session.linear_state.segments[-1].element_estimate + session.element_count <= 180

    @pytest.mark.asyncio
    async def test_tool_rollover_create_failure_falls_back_on_current_card(self) -> None:
        """tool rollover 新卡创建失败后，在当前卡保留 step 分界并禁用后续拆卡重试."""
        ctrl = _setup_ctrl()
        batch_card_ids = _capture_split_calls(ctrl, create_error=RuntimeError("create failed"))
        client = ctrl._client

        session = _make_session("msg_tool_roll_fallback", linear=True)
        session.state = STREAMING
        session.card_id = "card_tool_current"
        session.card_msg_id = "msg_tool_current"
        session.tool_use.record_start("read", "file0")
        session.linear_state.on_tool_event(1)
        tool_seg = session.linear_state.segments[0]
        tool_seg.created = True
        tool_seg.element_estimate = _estimate_segment_elements(tool_seg, session.tool_use.build_display_steps())
        session.element_count = 174

        for idx in range(1, 4):
            session.tool_use.record_start("read", f"file{idx}")
        session.linear_state.on_tool_event(len(session.tool_use.build_display_steps()))
        ctrl._sessions["msg_tool_roll_fallback"] = session

        await ctrl._do_linear_flush(session)

        assert session.card_id == "card_tool_current"
        assert session.split_index == 0
        assert session.split_disabled is True
        assert len(session.linear_state.segments) == 2
        assert session.linear_state.segments[0].tool_end_offset == 1
        assert session.linear_state.segments[1].tool_offset == 1
        assert session.linear_state.segments[1].created is True
        assert batch_card_ids == [("batch", "card_tool_current"), ("batch", "card_tool_current")]
        client.cardkit_close_streaming.assert_not_called()
        client.cardkit_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_split_create_failure_falls_back_to_current_card(self) -> None:
        """新卡创建失败是有意降级：不推进 split_index，继续把后续内容写回当前卡."""
        ctrl = _setup_ctrl()
        batch_card_ids = _capture_split_calls(ctrl, create_error=RuntimeError("create failed"))
        client = ctrl._client

        session = _make_session("msg_split_fallback", linear=True)
        session.state = STREAMING
        session.card_id = "card_current"
        session.card_msg_id = "msg_current"
        session.element_count = 174
        session.linear_state.on_reasoning_delta("old")
        session.linear_state.segments[0].created = True
        session.linear_state.segments[0].dirty = False
        session.linear_state.on_answer_delta("pending answer")
        session.tool_use.record_start("read", "file")
        session.linear_state.on_tool_event(1)
        ctrl._sessions["msg_split_fallback"] = session

        await ctrl._do_linear_flush(session)

        assert session.card_id == "card_current"
        assert session.card_msg_id == "msg_current"
        assert session.split_index == 0
        assert session.split_disabled is True
        assert session.element_count > 174
        assert batch_card_ids == [("batch", "card_current"), ("batch", "card_current")]
        assert session.linear_state.segments[2].created is True
        client.cardkit_close_streaming.assert_not_called()
        client.cardkit_update.assert_not_called()

        client.cardkit_create.reset_mock()
        session.linear_state.on_answer_delta(" after fallback")

        await ctrl._do_linear_flush(session)

        client.cardkit_create.assert_not_called()
        assert batch_card_ids == [
            ("batch", "card_current"),
            ("batch", "card_current"),
            ("batch", "card_current"),
        ]
        assert session.linear_state.segments[-1].created is True

    @pytest.mark.asyncio
    async def test_reasoning_finalized_snapshot(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_snap", linear=True)
        session.state = STREAMING
        session.card_id = "card_snap"
        session.linear_state.on_reasoning_delta("think")
        session.linear_state.on_answer_delta("reply")
        session.linear_state.segments[0].elapsed_ms = 1500.0
        session.linear_state.segments[0].reasoning_finalized = False
        ctrl._sessions["msg_snap"] = session

        await ctrl._do_linear_flush(session)

        assert session.linear_state.segments[0].reasoning_finalized is True

    @pytest.mark.asyncio
    async def test_reasoning_title_update_with_elapsed(self) -> None:
        ctrl = _setup_ctrl()
        batch_calls: list[list[dict]] = []

        async def capture_batch(card_id: str, actions: list[dict], **kw: object) -> None:
            batch_calls.append(actions)

        ctrl._client.cardkit_batch_update = capture_batch

        session = _make_session("msg_title", linear=True)
        session.state = STREAMING
        session.card_id = "card_title"
        session.linear_state.on_reasoning_delta("think")
        session.linear_state.on_answer_delta("reply")
        session.linear_state.segments[0].elapsed_ms = 2500.0
        session.linear_state.segments[0].created = True
        session.linear_state.segments[0].reasoning_finalized = False
        ctrl._sessions["msg_title"] = session

        await ctrl._do_linear_flush(session)

        partials = [a for a in batch_calls[0] if a["action"] == "partial_update_element"]
        assert len(partials) == 1
        assert "2.5s" in partials[0]["params"]["partial_element"]["header"]["title"]["content"]

    @pytest.mark.asyncio
    async def test_tool_dirty_snapshot(self) -> None:
        """await 期间 tool_end_offset 变化 → dirty 保持."""
        ctrl = _setup_ctrl()
        original_batch = ctrl._client.cardkit_batch_update
        tool_seg_ref: Segment | None = None
        batch_counter = 0

        async def batch_with_race(card_id: str, actions: list[dict], **kw: object) -> None:
            nonlocal batch_counter
            await original_batch(card_id, actions, **kw)
            batch_counter += 1
            if batch_counter == 1 and tool_seg_ref is not None and tool_seg_ref.tool_end_offset == 0:
                tool_seg_ref.tool_end_offset = 5

        ctrl._client.cardkit_batch_update = batch_with_race

        session = _make_session("msg_tool_snap", linear=True)
        session.state = STREAMING
        session.card_id = "card_snap"
        session.linear_state.on_answer_delta("text")
        session.tool_use.record_start("read", "f")
        session.linear_state.on_tool_event(1)
        tool_seg_ref = session.linear_state.segments[1]
        ctrl._sessions["msg_tool_snap"] = session

        await ctrl._do_linear_flush(session)

        assert tool_seg_ref.tool_end_offset == 5
        assert tool_seg_ref.dirty is True

    @pytest.mark.asyncio
    async def test_step2_exception_does_not_block_step3(self) -> None:
        ctrl = _setup_ctrl()
        ctrl._client.cardkit_stream_element = AsyncMock(side_effect=RuntimeError("stream fail"))
        session = _make_session("msg_exc", linear=True)
        session.state = STREAMING
        session.card_id = "card_exc"
        session.linear_state.on_answer_delta("text")
        session.tool_use.record_start("read", "f")
        session.linear_state.on_tool_event(1)
        ctrl._sessions["msg_exc"] = session

        await ctrl._do_linear_flush(session)

        assert ctrl._client.cardkit_batch_update.call_count >= 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [230020, 300309])
    async def test_api_errors_swallowed(self, code: int) -> None:
        """rate limited / streaming closed 不抛异常."""
        ctrl = _setup_ctrl()
        ctrl._client.cardkit_batch_update = AsyncMock(side_effect=FeishuAPIError("e", code=code))
        session = _make_session("msg_err", linear=True)
        session.state = STREAMING
        session.card_id = "card_e"
        session.linear_state.on_reasoning_delta("think")
        ctrl._sessions["msg_err"] = session

        await ctrl._do_linear_flush(session)

    @pytest.mark.asyncio
    async def test_skip_conditions(self) -> None:
        """终态 / 无 card_id / 无 dirty 全部跳过 API 调用."""
        ctrl = _setup_ctrl()

        # 终态
        s1 = _make_session("m1", linear=True)
        s1.state = COMPLETED
        ctrl._sessions["m1"] = s1
        await ctrl._do_linear_flush(s1)

        # 无 card_id
        s2 = _make_session("m2", linear=True)
        s2.state = STREAMING
        s2.card_id = None
        ctrl._sessions["m2"] = s2
        await ctrl._do_linear_flush(s2)

        # 无 dirty
        s3 = _make_session("m3", linear=True)
        s3.state = STREAMING
        s3.card_id = "c"
        s3.linear_state.on_reasoning_delta("t")
        s3.linear_state.segments[0].created = True
        s3.linear_state.segments[0].dirty = False
        ctrl._sessions["m3"] = s3
        await ctrl._do_linear_flush(s3)

        ctrl._client.cardkit_batch_update.assert_not_called()
        ctrl._client.cardkit_stream_element.assert_not_called()


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
        assert call_order == ["close", "update"]

    @pytest.mark.asyncio
    async def test_streaming_closed_flag_prevents_double_close(self) -> None:
        ctrl = _setup_ctrl()
        client = ctrl._client
        client.cardkit_close_streaming = AsyncMock()
        call_count = 0
        original_update = client.cardkit_update

        async def flaky_update(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FeishuAPIError("conflict", code=300317)
            return await original_update(*args, **kwargs)

        client.cardkit_update = flaky_update

        session = _make_session("msg_retry", linear=True)
        session.state = STREAMING
        session.card_id = "card_retry"
        session.card_msg_id = "msg_retry_reply"
        ctrl._sessions["msg_retry"] = session

        assert await ctrl._do_linear_complete(session) is True
        assert client.cardkit_close_streaming.call_count == 1
        assert call_count == 2

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
        session.linear_state.on_reasoning_delta("think")
        time.sleep(0.001)
        ctrl._sessions["msg_fc"] = session

        await ctrl._do_linear_complete(session)

        assert session.linear_state.segments[0].elapsed_ms > 0
        assert "msg_fc" not in ctrl._sessions

    @pytest.mark.asyncio
    async def test_no_card_id_skips_close(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_nocard", linear=True)
        session.state = STREAMING
        session.card_id = None
        ctrl._sessions["msg_nocard"] = session

        assert await ctrl._do_linear_complete(session) is True
        assert session.state == COMPLETED
        ctrl._client.cardkit_close_streaming.assert_not_called()

    @pytest.mark.asyncio
    async def test_image_resolve_per_segment(self) -> None:
        """单个 segment resolve 失败不影响后续."""
        from unittest.mock import MagicMock

        ctrl = _setup_ctrl()
        session = _make_session("msg_img", linear=True)
        session.state = STREAMING
        session.card_id = "card_img"
        session.linear_state.on_answer_delta("![a](http://x.com/img.png)")
        session.linear_state.on_reasoning_delta("mid")
        session.linear_state.on_answer_delta("![b](http://y.com/img2.png)")

        resolver = MagicMock()
        resolver.resolve_await = AsyncMock(side_effect=[RuntimeError("timeout"), "ok"])
        session.image_resolver = resolver
        ctrl._sessions["msg_img"] = session

        await ctrl._do_linear_complete(session)

        assert resolver.resolve_await.call_count == 2


# ── _linear_on_thinking 集成测试 ──


class TestLinearOnThinking:
    def test_splits_and_dispatches(self) -> None:
        ctrl = _setup_ctrl()
        ctrl._cfg._reload_cached = lambda: {"display": {"platforms": {"feishu": {"show_reasoning": True}}}}  # type: ignore[assignment]
        session = _make_session("msg_think", linear=True)
        ctrl._sessions["msg_think"] = session

        with patch.object(ctrl, "_schedule_linear_flush"):
            ctrl._linear_on_thinking(session, "<thinking>reasoning here</thinking>\nanswer text")

        types = [s.type for s in session.linear_state.segments]
        assert types == ["reasoning", "answer"]

    def test_empty_text_no_flush(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_think2", linear=True)
        ctrl._sessions["msg_think2"] = session

        with patch.object(ctrl, "_schedule_linear_flush") as m:
            ctrl._linear_on_thinking(session, "")
            m.assert_not_called()

    def test_linear_state_none_skips(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_think3", linear=True)
        session.linear_state = None
        ctrl._sessions["msg_think3"] = session

        ctrl._linear_on_thinking(session, "some text")

    def test_show_reasoning_false_skips_reasoning(self) -> None:
        ctrl = _setup_ctrl()
        ctrl._cfg._reload_cached = lambda: {"display": {"platforms": {"feishu": {"show_reasoning": False}}}}  # type: ignore[assignment]
        session = _make_session("msg_noreas", linear=True)
        ctrl._sessions["msg_noreas"] = session

        with patch.object(ctrl, "_schedule_linear_flush"):
            ctrl._linear_on_thinking(session, "<thinking>secret thoughts</thinking>\nreal answer")

        assert all(s.type == "answer" for s in session.linear_state.segments)

    def test_reasoning_only_with_show_reasoning(self) -> None:
        ctrl = _setup_ctrl()
        ctrl._cfg._reload_cached = lambda: {"display": {"platforms": {"feishu": {"show_reasoning": True}}}}  # type: ignore[assignment]
        session = _make_session("msg_ronly", linear=True)
        ctrl._sessions["msg_ronly"] = session

        with patch.object(ctrl, "_schedule_linear_flush"):
            ctrl._linear_on_thinking(session, "Reasoning:\njust thinking")

        assert len(session.linear_state.segments) == 1
        assert session.linear_state.segments[0].type == "reasoning"


class TestCronDeliver:
    def test_returns_false_when_disabled(self) -> None:
        ctrl = StreamCardController()
        ctrl._cfg = MagicMock()
        ctrl._cfg.enabled = False
        assert ctrl.on_cron_deliver(chat_id="c1", content="text", loop=MagicMock()) is False

    def test_returns_false_on_empty_content(self) -> None:
        ctrl = StreamCardController()
        ctrl._cfg = MagicMock()
        ctrl._cfg.enabled = True
        assert ctrl.on_cron_deliver(chat_id="c1", content="", loop=MagicMock()) is False

    def test_sends_card_on_success(self) -> None:
        import threading

        ctrl = StreamCardController()
        ctrl._cfg = MagicMock()
        ctrl._cfg.enabled = True

        mock_client = AsyncMock()
        mock_client.send_card_to_chat.return_value = "msg_123"
        ctrl._client = mock_client
        ctrl._initialized = True

        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        try:
            result = ctrl.on_cron_deliver(chat_id="c1", content="hello", loop=loop)
            assert result is True
            mock_client.send_card_to_chat.assert_called_once()
            args = mock_client.send_card_to_chat.call_args[0]
            assert args[0] == "c1"
            card = args[1]
            assert card["schema"] == "2.0"
            assert "hello" in card["body"]["elements"][0]["content"]
        finally:
            loop.call_soon_threadsafe(loop.stop)

    def test_returns_false_on_send_failure(self) -> None:
        import threading

        ctrl = StreamCardController()
        ctrl._cfg = MagicMock()
        ctrl._cfg.enabled = True

        mock_client = AsyncMock()
        mock_client.send_card_to_chat.side_effect = RuntimeError("API error")
        ctrl._client = mock_client
        ctrl._initialized = True

        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        try:
            result = ctrl.on_cron_deliver(chat_id="c1", content="hello", loop=loop)
            assert result is False
        finally:
            loop.call_soon_threadsafe(loop.stop)


class TestOnCompleted:
    """on_completed 新增参数测试: compression_exhausted, aborted, error_message."""

    def test_aborted_sets_session_state(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_abort")
        session.state = STREAMING
        session.card_id = "card_abort"
        ctrl._sessions["msg_abort"] = session

        with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
            ctrl.on_completed(message_id="msg_abort", aborted=True)

        assert session.state == ABORTED

    def test_error_message_saved_on_session(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_err")
        session.state = STREAMING
        session.card_id = "card_err"
        ctrl._sessions["msg_err"] = session

        with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
            ctrl.on_completed(message_id="msg_err", error_message="API timeout")

        assert session.error_message == "API timeout"

    def test_compression_exhausted_in_footer(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_ctx")
        session.state = STREAMING
        session.card_id = "card_ctx"
        ctrl._sessions["msg_ctx"] = session

        with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
            ctrl.on_completed(
                message_id="msg_ctx",
                compression_exhausted=True,
            )

        assert session.footer.get("compression_exhausted") is True

    def test_card_session_has_error_message_attribute(self) -> None:
        session = _make_session("msg_attr")
        assert hasattr(session, "error_message")
        assert session.error_message == ""

    def test_aborted_and_error_message_together(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_both")
        session.state = STREAMING
        session.card_id = "card_both"
        ctrl._sessions["msg_both"] = session

        with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
            ctrl.on_completed(
                message_id="msg_both",
                aborted=True,
                error_message="User stopped",
            )

        assert session.state == ABORTED
        assert session.error_message == "User stopped"


# ── on_cron_deliver_async 测试 ──


class TestCronDeliverAsync:
    """on_cron_deliver_async 异步版 cron 推送测试."""

    @pytest.mark.asyncio
    async def test_returns_false_when_disabled(self) -> None:
        ctrl = StreamCardController()
        ctrl._cfg = MagicMock()
        ctrl._cfg.enabled = False
        loop = asyncio.new_event_loop()
        try:
            result = await ctrl.on_cron_deliver_async(
                chat_id="c1", content="text", loop=loop,
            )
            assert result is False
        finally:
            loop.close()

    @pytest.mark.asyncio
    async def test_returns_false_on_empty_content(self) -> None:
        ctrl = StreamCardController()
        ctrl._cfg = MagicMock()
        ctrl._cfg.enabled = True
        loop = asyncio.new_event_loop()
        try:
            result = await ctrl.on_cron_deliver_async(
                chat_id="c1", content="", loop=loop,
            )
            assert result is False
        finally:
            loop.close()

    @pytest.mark.asyncio
    async def test_returns_false_on_empty_chat_id(self) -> None:
        ctrl = StreamCardController()
        ctrl._cfg = MagicMock()
        ctrl._cfg.enabled = True
        loop = asyncio.new_event_loop()
        try:
            result = await ctrl.on_cron_deliver_async(
                chat_id="", content="hello", loop=loop,
            )
            assert result is False
        finally:
            loop.close()

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self) -> None:
        ctrl = _setup_ctrl()
        loop = asyncio.new_event_loop()
        try:
            with patch.object(ctrl, "_do_cron_deliver", new_callable=AsyncMock):
                result = await ctrl.on_cron_deliver_async(
                    chat_id="c1", content="hello", loop=loop,
                )
            assert result is True
        finally:
            loop.close()

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self) -> None:
        ctrl = _setup_ctrl()
        loop = asyncio.new_event_loop()
        try:
            with patch.object(
                ctrl, "_do_cron_deliver", new_callable=AsyncMock, side_effect=RuntimeError("API error"),
            ):
                result = await ctrl.on_cron_deliver_async(
                    chat_id="c1", content="hello", loop=loop,
                )
            assert result is False
        finally:
            loop.close()

    @pytest.mark.asyncio
    async def test_awaits_do_cron_deliver(self) -> None:
        """验证 _do_cron_deliver 被 await 而非 run_coroutine_threadsafe."""
        ctrl = _setup_ctrl()
        loop = asyncio.new_event_loop()
        try:
            mock_deliver = AsyncMock()
            with patch.object(ctrl, "_do_cron_deliver", mock_deliver):
                await ctrl.on_cron_deliver_async(
                    chat_id="c1", content="hello", loop=loop,
                )
            mock_deliver.assert_awaited_once_with("c1", "hello")
        finally:
            loop.close()


# ── on_aborted 测试 ──


class TestOnAborted:
    """on_aborted 中断处理测试."""

    def test_sets_aborted_state(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_aborted")
        session.state = STREAMING
        ctrl._sessions["msg_aborted"] = session

        with patch.object(ctrl, "_fire_and_forget", side_effect=lambda coro, loop: coro.close()):
            ctrl.on_aborted(message_id="msg_aborted")

        assert session.state == ABORTED

    def test_calls_complete_session(self) -> None:
        ctrl = _setup_ctrl()
        session = _make_session("msg_complete")
        session.state = STREAMING
        ctrl._sessions["msg_complete"] = session

        with patch.object(ctrl, "_complete_session") as mock_complete:
            ctrl.on_aborted(message_id="msg_complete")
            mock_complete.assert_called_once_with(session)

    def test_skips_when_disabled(self) -> None:
        ctrl = StreamCardController()
        ctrl._cfg = MagicMock()
        ctrl._cfg.enabled = False

        with patch.object(ctrl, "_complete_session") as mock_complete:
            ctrl.on_aborted(message_id="msg_skip")
            mock_complete.assert_not_called()

    def test_skips_when_no_session(self) -> None:
        ctrl = _setup_ctrl()

        with patch.object(ctrl, "_complete_session") as mock_complete:
            ctrl.on_aborted(message_id="nonexistent")
            mock_complete.assert_not_called()


# ── _handle_linear_flush_error_async 测试 ──


def _make_element_limit_error() -> FeishuAPIError:
    """Construct a FeishuAPIError with CARDKIT_CONTENT_FAILED code and CARDKIT_ELEMENT_LIMIT sub_code."""
    return FeishuAPIError(
        f"Failed to create card content, ext=ErrCode: {CARDKIT_ELEMENT_LIMIT}; detail",
        code=CARDKIT_CONTENT_FAILED,
    )


class TestHandleLinearFlushError:
    """_handle_linear_flush_error_async: CARDKIT_ELEMENT_LIMIT 触发拆卡."""

    @pytest.mark.asyncio
    async def test_element_limit_sets_flag_and_triggers_split(self) -> None:
        """CARDKIT_ELEMENT_LIMIT 错误设置 element_limit_hit=True 并尝试拆卡.

        测试 _handle_linear_flush_error_async 直接调用：
        当有未创建的 segment 时，设置标记后尝试拆卡，拆卡成功返回 True,
        并且 element_limit_hit 被重置为 False.
        """
        ctrl = _setup_ctrl()
        calls = _capture_split_calls(ctrl)

        session = _make_session("msg_elimit", linear=True)
        session.state = STREAMING
        session.card_id = "card_old"
        session.card_msg_id = "msg_old"
        session.element_count = 174
        session.linear_state.on_reasoning_delta("think")
        session.linear_state.segments[0].created = True
        session.linear_state.segments[0].dirty = False
        session.linear_state.on_answer_delta("new answer")
        ctrl._sessions["msg_elimit"] = session

        e = _make_element_limit_error()

        # Call the error handler directly with empty actions (no pending batch)
        result = await ctrl._handle_linear_flush_error_async(
            e, session,
            session.linear_state.segments,
            [], set(), {}, [],
        )

        # Split was attempted; since actions is empty, _do_linear_split skips
        # the batch_update and proceeds directly to create the new card
        assert result is True
        # After successful split, element_limit_hit is reset
        assert session.element_limit_hit is False
        assert session.card_id == "card_next"

    @pytest.mark.asyncio
    async def test_element_limit_no_splittable_content_returns_false(self) -> None:
        """split_index 已到末尾时，CARDKIT_ELEMENT_LIMIT 返回 False（无法拆分）."""
        ctrl = _setup_ctrl()

        session = _make_session("msg_nosplit", linear=True)
        session.state = STREAMING
        session.card_id = "card_nosplit"
        session.element_count = 195
        session.linear_state.on_answer_delta("answer")
        session.linear_state.segments[0].created = True
        session.linear_state.segments[0].dirty = False
        # split_index past all segments → no splittable content
        session.split_index = 1
        ctrl._sessions["msg_nosplit"] = session

        e = _make_element_limit_error()

        result = await ctrl._handle_linear_flush_error_async(
            e, session,
            session.linear_state.segments,
            [], set(), {}, [],
        )

        assert result is False
        assert session.element_limit_hit is True

    @pytest.mark.asyncio
    async def test_element_limit_split_create_failure_degrades_gracefully(self) -> None:
        """CARDKIT_ELEMENT_LIMIT 触发拆卡但新卡创建失败时，_do_linear_split 返回 True（降级继续）.

        _do_linear_split 在新卡创建失败时设置 split_disabled=True 并返回 True
        （有意降级为继续写当前卡）。element_limit_hit 保持 True（未被重置）。
        """
        ctrl = _setup_ctrl()
        # Make cardkit_create fail so split degrades
        _capture_split_calls(ctrl, create_error=RuntimeError("create failed"))

        session = _make_session("msg_splitfail", linear=True)
        session.state = STREAMING
        session.card_id = "card_current"
        session.card_msg_id = "msg_current"
        session.element_count = 174
        session.linear_state.on_reasoning_delta("think")
        session.linear_state.segments[0].created = True
        session.linear_state.segments[0].dirty = False
        session.linear_state.on_answer_delta("new answer")
        ctrl._sessions["msg_splitfail"] = session

        e = _make_element_limit_error()

        result = await ctrl._handle_linear_flush_error_async(
            e, session,
            session.linear_state.segments,
            [], set(), {}, [],
        )

        # _do_linear_split returns True (graceful degradation)
        assert result is True
        # element_limit_hit remains True (not reset because new card was not created)
        assert session.element_limit_hit is True
        # split_disabled is set to prevent retry
        assert session.split_disabled is True

    @pytest.mark.asyncio
    async def test_rate_limited_returns_false(self) -> None:
        """CARDKIT_RATE_LIMITED 错误返回 False，不设置 element_limit_hit."""
        ctrl = _setup_ctrl()

        session = _make_session("msg_ratelimit", linear=True)
        session.state = STREAMING
        session.card_id = "card_rate"
        ctrl._sessions["msg_ratelimit"] = session

        e = FeishuAPIError("rate limited", code=CARDKIT_RATE_LIMITED)

        result = await ctrl._handle_linear_flush_error_async(
            e, session,
            session.linear_state.segments,
            [], set(), {}, [],
        )

        assert result is False
        assert session.element_limit_hit is False

    @pytest.mark.asyncio
    async def test_streaming_closed_returns_false(self) -> None:
        """CARDKIT_STREAMING_CLOSED 错误返回 False，不设置 element_limit_hit."""
        ctrl = _setup_ctrl()

        session = _make_session("msg_closed", linear=True)
        session.state = STREAMING
        session.card_id = "card_closed"
        ctrl._sessions["msg_closed"] = session

        e = FeishuAPIError("streaming closed", code=CARDKIT_STREAMING_CLOSED)

        result = await ctrl._handle_linear_flush_error_async(
            e, session,
            session.linear_state.segments,
            [], set(), {}, [],
        )

        assert result is False
        assert session.element_limit_hit is False

    @pytest.mark.asyncio
    async def test_other_content_failed_without_element_limit_subcode(self) -> None:
        """CARDKIT_CONTENT_FAILED 但 sub_code 不是 CARDKIT_ELEMENT_LIMIT 返回 False."""
        ctrl = _setup_ctrl()

        session = _make_session("msg_other", linear=True)
        session.state = STREAMING
        session.card_id = "card_other"
        ctrl._sessions["msg_other"] = session

        # CARDKIT_CONTENT_FAILED with a different sub code
        e = FeishuAPIError(
            "Failed to create card content, ext=ErrCode: 99999; detail",
            code=CARDKIT_CONTENT_FAILED,
        )

        result = await ctrl._handle_linear_flush_error_async(
            e, session,
            session.linear_state.segments,
            [], set(), {}, [],
        )

        assert result is False
        assert session.element_limit_hit is False


# ── element_limit_hit 标记测试 ──


class TestElementLimitHit:
    """element_limit_hit 标记：当设置时 _do_linear_flush 跳过未创建的 segment."""

    @pytest.mark.asyncio
    async def test_element_limit_hit_skips_uncreated_segments(self) -> None:
        """element_limit_hit=True 时，_do_linear_flush 跳过 not seg.created 的 segment."""
        ctrl = _setup_ctrl()

        session = _make_session("msg_skip", linear=True)
        session.state = STREAMING
        session.card_id = "card_skip"
        session.element_limit_hit = True
        # First segment: created, dirty (should be flushed)
        session.linear_state.on_reasoning_delta("think")
        session.linear_state.segments[0].created = True
        session.linear_state.segments[0].dirty = True
        # Second segment: not created (should be skipped because element_limit_hit)
        session.linear_state.on_answer_delta("answer")
        assert session.linear_state.segments[1].created is False
        ctrl._sessions["msg_skip"] = session

        await ctrl._do_linear_flush(session)

        # The answer segment should NOT have been created (skipped due to element_limit_hit)
        assert session.linear_state.segments[1].created is False
        # The reasoning segment should have been flushed
        assert session.linear_state.segments[0].dirty is False

    @pytest.mark.asyncio
    async def test_element_limit_hit_does_not_skip_created_dirty_segments(self) -> None:
        """element_limit_hit=True 时，已创建的 dirty segment 仍会被正常更新（via stream_element）."""
        ctrl = _setup_ctrl()

        session = _make_session("msg_dirty_flush", linear=True)
        session.state = STREAMING
        session.card_id = "card_dirty"
        session.element_limit_hit = True
        session.linear_state.on_answer_delta("text")
        session.linear_state.segments[0].created = True
        session.linear_state.segments[0].dirty = True
        ctrl._sessions["msg_dirty_flush"] = session

        await ctrl._do_linear_flush(session)

        # Created dirty segment should be flushed via stream_element (step 2)
        assert session.linear_state.segments[0].dirty is False
        ctrl._client.cardkit_stream_element.assert_called()

    @pytest.mark.asyncio
    async def test_element_limit_hit_reset_after_successful_split(self) -> None:
        """成功拆卡后 element_limit_hit 被重置为 False.

        Use a tool segment rollover to trigger the split while element_limit_hit
        is True — the split resets the flag.
        """
        ctrl = _setup_ctrl()
        calls = _capture_split_calls(
            ctrl,
            cards=["card_tool_next"],
            messages=["msg_tool_next"],
        )

        session = _make_session("msg_reset", linear=True)
        session.state = STREAMING
        session.card_id = "card_old"
        session.card_msg_id = "msg_old"
        session.element_limit_hit = True  # Pre-set
        session.tool_use.record_start("read", "file0")
        session.linear_state.on_tool_event(1)
        tool_seg = session.linear_state.segments[0]
        tool_seg.created = True
        tool_seg.element_estimate = _estimate_segment_elements(tool_seg, session.tool_use.build_display_steps())
        session.element_count = 174

        for idx in range(1, 4):
            session.tool_use.record_start("read", f"file{idx}")
        session.linear_state.on_tool_event(len(session.tool_use.build_display_steps()))
        ctrl._sessions["msg_reset"] = session

        await ctrl._do_linear_flush(session)

        # Split happened → element_limit_hit should be reset
        assert session.element_limit_hit is False
        assert session.card_id == "card_tool_next"

    @pytest.mark.asyncio
    async def test_split_disabled_with_element_limit_is_deadlock_safe(self) -> None:
        """split_disabled=True + element_limit_hit=True 时不会死锁：
        flush 只更新已创建的 dirty segment，跳过未创建的 segment."""
        ctrl = _setup_ctrl()

        session = _make_session("msg_deadlock", linear=True)
        session.state = STREAMING
        session.card_id = "card_deadlock"
        session.split_disabled = True
        session.element_limit_hit = True
        # Created answer segment with dirty text
        session.linear_state.on_answer_delta("existing text")
        session.linear_state.segments[0].created = True
        session.linear_state.segments[0].dirty = True
        # Uncreated reasoning segment (would normally trigger split, but split is disabled)
        session.linear_state.on_reasoning_delta("more text")
        assert session.linear_state.segments[1].created is False
        ctrl._sessions["msg_deadlock"] = session

        await ctrl._do_linear_flush(session)

        # Should NOT have tried to split (split_disabled)
        ctrl._client.cardkit_create.assert_not_called()
        ctrl._client.cardkit_close_streaming.assert_not_called()
        # Should have flushed the created dirty segment via stream_element
        assert session.linear_state.segments[0].dirty is False
        # Should have skipped the uncreated segment
        assert session.linear_state.segments[1].created is False
        # element_limit_hit should remain True (no split to reset it)
        assert session.element_limit_hit is True

    @pytest.mark.asyncio
    async def test_element_limit_hit_with_all_segments_created(self) -> None:
        """element_limit_hit=True 且所有 segment 都已创建时，flush 只更新 dirty segment (via stream_element)."""
        ctrl = _setup_ctrl()

        session = _make_session("msg_all_created", linear=True)
        session.state = STREAMING
        session.card_id = "card_all"
        session.element_limit_hit = True
        session.linear_state.on_reasoning_delta("think")
        session.linear_state.on_answer_delta("answer")
        # Mark all as created but dirty
        session.linear_state.segments[0].created = True
        session.linear_state.segments[0].dirty = True
        session.linear_state.segments[1].created = True
        session.linear_state.segments[1].dirty = True
        ctrl._sessions["msg_all_created"] = session

        await ctrl._do_linear_flush(session)

        # All dirty segments flushed via stream_element (step 2)
        assert session.linear_state.segments[0].dirty is False
        assert session.linear_state.segments[1].dirty is False
        ctrl._client.cardkit_stream_element.assert_called()
