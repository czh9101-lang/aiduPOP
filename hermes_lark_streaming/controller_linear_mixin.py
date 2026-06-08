"""线性单卡模式的异步 API 编排 — 创建、刷新、拆卡、完成."""

from __future__ import annotations

import asyncio
import logging
import time as _time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import re

from .cardkit import (
    _LOADING_ELEMENT_ID,
    _CONTEXT_LOADING_ELEMENT_ID,
    _build_background_review_panel,
    _build_reasoning_panel,
    _build_tool_panel,
    _context_loading_element,
    _format_elapsed,
    _streaming_element,
    build_im_fallback_card,
    build_linear_compact_seal_card,
    build_linear_complete_card,
    build_preservative_seal_actions,
    build_streaming_card_v2,
)

# 匹配 markdown 图片语法: ![alt](img_xxx) — 与 cardkit._IMG_MD_PATTERN 对齐
_IMG_MD_PATTERN = re.compile(r"!\[([^\]]*)\]\((img_[^)\s]+)\)")
from .cardkit_i18n import _T, _i18n
from .cardkit_md import (
    _downgrade_tables,
    _split_long_text,
    optimize_markdown_style,
)
from .controller_mixin import (
    _TERMINAL,
    ABORTED,
    COMPLETED,
    COMPLETING,
    CREATING,
    FAILED,
    IDLE,
    STREAMING,
)
from .feishu import (
    CARDKIT_CONTENT_FAILED,
    CARDKIT_ELEMENT_LIMIT,
    CARDKIT_RATE_LIMITED,
    CARDKIT_SEQUENCE_CONFLICT,
    CARDKIT_STREAMING_CLOSED,
    FeishuAPIError,
)
from .flush import CARDKIT_MS, PATCH_MS
from .image import ImageResolver
from .linear import LinearState, Segment
from .text import split_reasoning_text

if TYPE_CHECKING:
    from .config import Config
    from .controller import CardSession
    from .feishu import FeishuClient

_logger = logging.getLogger("hermes_lark_streaming")

_ELEMENT_THRESHOLD = 150  # 拆卡阈值（飞书硬上限 200 总元素含嵌套，预留 50 给 footer + 图片 + 波动）
_FOOTER_RESERVE = 2  # footer 元素预留（hr + markdown）


def _count_images_in_text(text: str) -> int:
    """统计 markdown 文本中 img_ 前缀的图片数量（与 cardkit._extract_images_from_markdown 对齐）."""
    return len(_IMG_MD_PATTERN.findall(text))


def _estimate_segment_elements(seg: Segment, all_steps: list[dict[str, Any]]) -> int:
    """估算单个 segment 封卡时实际占用的卡片元素数.

    流式阶段 answer 虽只占 1 个 streaming markdown element，
    但封卡时会被 `_split_long_text` 拆成 N 个 markdown 元素。
    估算必须对齐封卡实际元素数，否则拆卡判断失效——
    流式阶段判断"不超限"，封卡时实际超限。
    """
    if seg.type == "reasoning":
        return 4  # collapsible_panel + plain_text + standard_icon + markdown
    elif seg.type == "answer":
        if seg.text:
            content = _downgrade_tables(optimize_markdown_style(seg.text))
            # 图片提取后变成独立 img 元素，需计入
            img_count = _count_images_in_text(content)
            return max(len(_split_long_text(content)), 1) + img_count
        return 1
    elif seg.type == "tool":
        return _estimate_tool_elements(
            seg.tool_offset,
            _tool_segment_end(seg, all_steps),
            all_steps,
        )
    return 0


def _tool_segment_end(seg: Segment, all_steps: list[dict[str, Any]]) -> int:
    return seg.tool_end_offset if seg.tool_end_offset else len(all_steps)


def _estimate_tool_elements(start: int, end: int, all_steps: list[dict[str, Any]]) -> int:
    """估算 tool panel 在 [start, end) step 区间内的元素数."""
    steps = all_steps[start:end]
    count = 3  # panel/header 基础元素
    for step in steps:
        count += 3  # title: div + standard_icon + lark_md
        if step.get("detail"):
            count += 2  # detail: div + plain_text
        if step.get("result_block") or step.get("error_block"):
            count += 2  # output: div + lark_md
    return count

class LinearControllerMixin:
    """线性模式专用方法 — 由 StreamCardController 继承."""

    _client: FeishuClient | None
    _cfg: Config
    _ensure_init: Callable[..., Coroutine[Any, Any, None]]
    _schedule_card_update: Callable[[CardSession], None]
    _cleanup: Callable[[str], None]
    _flush_deferred_background_reviews: Callable[[CardSession], None]
    _do_complete_inner: Callable[..., Coroutine[Any, Any, bool]]

    def _schedule_linear_flush(self, session: CardSession) -> None:
        if session.state == IDLE or session.state in _TERMINAL or session.state == COMPLETING:
            return
        if session.guard.should_skip("_schedule_linear_flush"):
            return
        # ── First-Token Immediate Flush (首字即显) ──
        # When this is the first content for the session (no elements created yet
        # and there are dirty segments), skip the throttle interval and flush
        # immediately. This reduces first-visible-text latency by 0~500ms.
        if (
            not session._first_flush_done
            and session.element_count <= 1
            and session.linear_state is not None
            and session.linear_state.has_dirty
        ):
            session._first_flush_done = True
            import asyncio
            asyncio.create_task(
                session.flush.flush_now(lambda: self._do_linear_flush(session))
            )
            return
        session.flush.schedule_update(lambda: self._do_linear_flush(session))

    def _linear_on_thinking(self, session: CardSession, text: str) -> None:
        linear_state = session.linear_state
        if linear_state is None:
            return
        split = split_reasoning_text(text)
        reasoning = split.get("reasoning_text")
        answer = split.get("answer_text")

        if reasoning and self._cfg.show_reasoning:
            linear_state.on_reasoning_delta(reasoning)
        if answer:
            # ── Dedup: skip answer text already delivered via stream_delta_callback ──
            # When streaming is active, answer text arrives incrementally via
            # stream_delta_callback → on_answer_delta → linear_state.on_answer_delta.
            # The interim_assistant_callback also delivers the same text in
            # accumulated form.  Appending it here would cause duplication because
            # linear_state.on_answer_delta APPENDS to the existing segment.
            # Only push answer text when no answer segment has text yet
            # (non-streaming fallback where stream_delta_callback is absent).
            _has_streamed_answer = any(
                seg.type == "answer" and seg.text for seg in linear_state.segments
            )
            if not _has_streamed_answer:
                linear_state.on_answer_delta(answer)
        if not (reasoning and self._cfg.show_reasoning) and not answer:
            return
        self._schedule_linear_flush(session)

    async def _do_create_linear_card(self, session: CardSession) -> None:
        """线性模式：创建只有 loading 的占位卡片."""
        if session.state != IDLE:
            return
        session.state = CREATING
        session.linear = True
        session.linear_state = LinearState()

        t0 = _time.monotonic()
        try:
            await self._ensure_init()
            assert self._client is not None

            try:
                reply_to_message_id = session.anchor_id or session.message_id
                card = build_streaming_card_v2(
                    show_tool_use=False,
                    show_reasoning=False,
                    show_streaming_element=False,
                    streaming_panel_expanded=self._cfg.streaming_panel_expanded,
                    print_strategy=self._cfg.print_strategy,
                )
                card_id = await self._client.cardkit_create(card)
                card_msg_id = await self._client.reply_card_by_id(
                    reply_to_message_id,
                    card_id,
                )
                session.card_id = card_id
                session.card_msg_id = card_msg_id
                session.use_cardkit = True
                session.element_count = 1  # loading element
                session.flush.set_throttle(self._cfg.flush_interval_sec)

                # ── Insert context loading hint (immediate user feedback) ──
                try:
                    session.sequence += 1
                    await self._client.cardkit_batch_update(
                        session.card_id,
                        [{
                            "action": "add_elements",
                            "params": {
                                "type": "insert_before",
                                "target_element_id": _LOADING_ELEMENT_ID,
                                "elements": [_context_loading_element()],
                            },
                        }],
                        sequence=session.sequence,
                    )
                    session.element_count = 2  # loading + context hint
                except Exception:
                    _logger.debug("context loading hint insert failed, continuing", exc_info=True)
            except FeishuAPIError:
                _logger.info("linear CardKit create failed, falling back to non-linear")
                card = build_im_fallback_card()
                card_msg_id = await self._client.reply_card(
                    reply_to_message_id,
                    card,
                )
                session.card_msg_id = card_msg_id
                session.use_cardkit = False
                session.linear = False
                session.linear_state = None
                session.flush.set_throttle(PATCH_MS)

            if session.image_resolver is None and self._client:
                session.image_resolver = ImageResolver(
                    client=self._client,
                    on_image_resolved=(
                        lambda: self._schedule_linear_flush(session)
                        if session.linear
                        else self._schedule_card_update(session)
                    ),
                )

            session.flush.set_card_message_ready(True)
            if session.state == CREATING:
                session.state = STREAMING
            if session.linear and session.linear_state and session.linear_state.has_dirty:
                self._schedule_linear_flush(session)
            # ── Signal card readiness ──
            # Must be set AFTER card_id/card_msg_id are assigned and
            # session state is transitioned out of CREATING.
            # _do_linear_complete_inner awaits this event to ensure
            # the card exists before attempting close_streaming + update.
            session._card_ready.set()
            _logger.info(
                "linear card created: msg=%s linear=%s card_id=%s",
                (session.message_id or "?")[:12],
                session.linear,
                (session.card_id or "")[:12],
            )
        except Exception:
            _logger.exception("_do_create_linear_card failed")
            session.state = FAILED
            # Signal readiness even on failure so awaiters don't deadlock
            session._card_ready.set()
            _logger.debug("perf: card_create msg=%s elapsed=%.0fms", (session.message_id or "?")[:12], (_time.monotonic()-t0)*1000)

    async def _do_linear_flush(self, session: CardSession) -> None:
        """线性模式幂等 flush：按 segment 顺序处理结构性变更，超阈值时拆卡."""
        if session.state in _TERMINAL or session.state == COMPLETING or not session.card_id:
            return
        linear_state = session.linear_state
        if linear_state is None:
            return

        t0 = _time.monotonic()
        assert self._client is not None
        segments = linear_state.segments
        all_steps = session.tool_use.build_display_steps()

        # ── 步骤 0: 重新估算已创建 answer segment 的元素数 ──
        # answer 在流式阶段只有一个 streaming element，但封卡时会被 _split_long_text
        # 拆成 N 个 markdown 元素。文本增长后旧估算可能偏低，需要动态更新 element_count
        # 以确保拆卡判断基于封卡时的实际元素数。
        # 如果增长后超限，先做 answer 内部拆分再拆卡。
        for i, seg in enumerate(segments[session.split_index:]):
            real_i = i + session.split_index
            if seg.created and seg.type == "answer" and seg.dirty:
                new_est = _estimate_segment_elements(seg, all_steps)
                if new_est != seg.element_estimate:
                    delta = new_est - seg.element_estimate
                    session.element_count += delta
                    seg.element_estimate = new_est
                    _logger.debug(
                        "answer estimate updated: msg=%s el=%s old=%d new=%d",
                        (session.message_id or "?")[:12], seg.el_id,
                        new_est - delta, new_est,
                    )
                # 增长后超限 → answer 内部拆分 + 拆卡
                if (
                    session.element_count + _FOOTER_RESERVE > _ELEMENT_THRESHOLD
                    and not session.split_disabled
                ):
                    split_offset = self._find_answer_split_offset(
                        session.element_count - seg.element_estimate, seg,
                    )
                    if split_offset is not None:
                        linear_state.split_answer_segment(real_i, split_offset)
                        seg.element_estimate = _estimate_segment_elements(seg, all_steps)
                        # 新 segment 的估算
                        new_seg = segments[real_i + 1]
                        new_seg_est = _estimate_segment_elements(new_seg, all_steps)
                        new_seg.element_estimate = new_seg_est
                        # 拆卡：封当前卡到 real_i+1，新卡从 real_i+1 开始
                        split_ok = await self._do_linear_split(
                            session, real_i + 1, [], set(), {}, [],
                        )
                        if not split_ok:
                            return
                        # 拆卡后重新获取 segments 和 all_steps（状态已变化）
                        segments = linear_state.segments
                        all_steps = session.tool_use.build_display_steps()
                        break

        # ── 步骤 1: batch_update — 按 segment 顺序处理结构性变更 ──
        actions: list[dict[str, Any]] = []
        new_el_ids: set[str] = set()
        new_el_estimates: dict[str, int] = {}
        updated_tool_segs: list[Segment] = []
        new_el_total = 0

        for i, seg in enumerate(segments):
            if i < session.split_index:
                continue

            if not seg.created:
                # 超限后不再新增元素，只刷已有段的脏文本，等完成阶段整体重建
                if session.element_limit_hit:
                    continue
                estimated = _estimate_segment_elements(seg, all_steps)
                # ── Tool 内部拆分：按 step 边界拆 ──
                if (
                    seg.type == "tool"
                    and session.element_count + new_el_total + estimated + _FOOTER_RESERVE > _ELEMENT_THRESHOLD
                    and not session.split_disabled
                ):
                    split_offset = self._find_tool_split_offset(
                        session.element_count + new_el_total,
                        seg,
                        all_steps,
                    )
                    if split_offset is not None:
                        linear_state.split_tool_segment(i, split_offset)
                        estimated = _estimate_segment_elements(seg, all_steps)
                # ── Answer 内部拆分：按文本块边界拆 ──
                if (
                    seg.type == "answer"
                    and session.element_count + new_el_total + estimated + _FOOTER_RESERVE > _ELEMENT_THRESHOLD
                    and not session.split_disabled
                ):
                    split_offset = self._find_answer_split_offset(
                        session.element_count + new_el_total, seg,
                    )
                    if split_offset is not None:
                        linear_state.split_answer_segment(i, split_offset)
                        estimated = _estimate_segment_elements(seg, all_steps)
                # ── 超阈值 → 拆卡 ──
                if (
                    session.element_count + new_el_total + estimated + _FOOTER_RESERVE > _ELEMENT_THRESHOLD
                    and session.element_count + new_el_total > 1
                    and not session.split_disabled
                ):
                    split_ok = await self._do_linear_split(
                        session, i, actions, new_el_ids, new_el_estimates, updated_tool_segs,
                    )
                    if not split_ok:
                        return
                    actions = []
                    new_el_ids = set()
                    new_el_estimates = {}
                    updated_tool_segs = []
                    new_el_total = 0

                if seg.type == "reasoning":
                    # 预填充推理文本：与 answer 优化同理
                    _reasoning_content = optimize_markdown_style(seg.text) or " " if seg.text else " "
                    el = _build_reasoning_panel(
                        _reasoning_content,
                        seg.elapsed_ms,
                        expanded=self._cfg.streaming_panel_expanded,
                        element_id=seg.el_id,
                        text_element_id=seg.text_el_id,
                    )
                    if seg.text:
                        seg.dirty = False  # 文本已在 batch_update 中发送
                elif seg.type == "answer":
                    # 预填充文本：避免 batch_update 后再调一次 stream_element，
                    # 减少首次文字出现的 API 调用次数（省 ~100-200ms）
                    _ans_content = seg.text or ""
                    if session.image_resolver:
                        _ans_content = session.image_resolver.resolve_images(_ans_content)
                    _ans_content = _downgrade_tables(optimize_markdown_style(_ans_content)) or " "
                    el = _streaming_element(content=_ans_content, element_id=seg.el_id)
                    if seg.text:
                        seg.dirty = False  # 文本已在 batch_update 中发送
                elif seg.type == "tool":
                    start = seg.tool_offset
                    end = seg.tool_end_offset if seg.tool_end_offset else len(all_steps)
                    el = _build_tool_panel(all_steps[start:end], expanded=self._cfg.streaming_panel_expanded, element_id=seg.el_id)
                    updated_tool_segs.append(seg)
                new_el_ids.add(seg.el_id)
                new_el_estimates[seg.el_id] = estimated
                new_el_total += estimated
                actions.append({
                    "action": "add_elements",
                    "params": {
                        "type": "insert_before",
                        "target_element_id": _LOADING_ELEMENT_ID,
                        "elements": [el],
                    },
                })
                if (
                    seg.type == "tool"
                    and i + 1 < len(segments)
                    and segments[i + 1].type == "tool"
                    and segments[i + 1].tool_offset == seg.tool_end_offset
                    and not session.split_disabled
                ) or (
                    seg.type == "answer"
                    and i + 1 < len(segments)
                    and segments[i + 1].type == "answer"
                    and not session.split_disabled
                ):
                    split_ok = await self._do_linear_split(
                        session, i + 1, actions, new_el_ids, new_el_estimates, updated_tool_segs,
                    )
                    if not split_ok:
                        return
                    actions = []
                    new_el_ids = set()
                    new_el_estimates = {}
                    updated_tool_segs = []
                    new_el_total = 0
            elif seg.type == "reasoning" and seg.elapsed_ms > 0 and not seg.reasoning_finalized:
                _logger.info(
                    "linear reasoning finalize: msg=%s el=%s elapsed=%.0fms seq=%d",
                    (session.message_id or "?")[:12],
                    seg.el_id,
                    seg.elapsed_ms,
                    session.sequence + 1,
                )
                d = _format_elapsed(seg.elapsed_ms)
                en_label = _T["thought_for"][0].format(d)
                zh_label = _T["thought_for"][1].format(d)
                actions.append({
                    "action": "partial_update_element",
                    "params": {
                        "element_id": seg.el_id,
                        "partial_element": {
                            "header": {
                                "title": {
                                    "tag": "plain_text",
                                    "content": f"💭 {en_label}",
                                    "i18n_content": _i18n(f"💭 {en_label}", f"💭 {zh_label}"),
                                    "text_color": "grey",
                                    "text_size": "notation",
                                },
                            },
                        },
                    },
                })
            elif seg.type == "tool" and seg.dirty:
                if seg.tool_end_offset > 0:
                    start, end = seg.tool_offset, seg.tool_end_offset
                else:
                    start, end = seg.tool_offset, len(all_steps)
                rollover = await self._maybe_rollover_tool_segment(
                    session=session,
                    linear_state=linear_state,
                    index=i,
                    seg=seg,
                    all_steps=all_steps,
                    actions=actions,
                    new_el_ids=new_el_ids,
                    new_el_estimates=new_el_estimates,
                    updated_tool_segs=updated_tool_segs,
                )
                if rollover == "failed":
                    return
                if rollover == "split":
                    actions = []
                    new_el_ids = set()
                    new_el_estimates = {}
                    updated_tool_segs = []
                    new_el_total = 0
                    continue
                estimate = _estimate_tool_elements(start, end, all_steps)
                panel = _build_tool_panel(all_steps[start:end], expanded=self._cfg.streaming_panel_expanded)
                actions.append({
                    "action": "partial_update_element",
                    "params": {
                        "element_id": seg.el_id,
                        "partial_element": {
                            "elements": panel["elements"],
                            "header": panel["header"],
                        },
                    },
                })
                updated_tool_segs.append(seg)
                new_el_estimates[seg.el_id] = estimate

        # ── Background review panel ──
        if linear_state.bg_review_messages and not linear_state.bg_review_panel_added:
            panel = _build_background_review_panel(
                linear_state.bg_review_messages,
                expanded=self._cfg.streaming_panel_expanded,
                element_id=linear_state.bg_review_panel_id,
            )
            actions.append({
                "action": "add_elements",
                "params": {
                    "type": "insert_before",
                    "target_element_id": _LOADING_ELEMENT_ID,
                    "elements": [panel],
                },
            })
            new_el_ids.add(linear_state.bg_review_panel_id)
            new_el_estimates[linear_state.bg_review_panel_id] = 4  # panel + header + icon + markdown
            linear_state.bg_review_panel_added = True
        elif linear_state.bg_review_panel_added and linear_state.bg_review_messages:
            # Update existing panel
            panel = _build_background_review_panel(
                linear_state.bg_review_messages,
                expanded=self._cfg.streaming_panel_expanded,
            )
            actions.append({
                "action": "partial_update_element",
                "params": {
                    "element_id": linear_state.bg_review_panel_id,
                    "partial_element": {
                        "elements": panel["elements"],
                    },
                },
            })

        if actions and not await self._do_linear_batch_update(
            session, segments, actions, new_el_ids, new_el_estimates, updated_tool_segs,
        ):
            return

        # ── 步骤 2: stream_element 刷脏文本 ──
        for seg in segments[session.split_index:]:
            if not seg.created or not seg.dirty:
                continue
            try:
                if seg.type == "reasoning":
                    content = optimize_markdown_style(seg.text) or " "
                    session.sequence += 1
                    _logger.info(
                        "linear stream: msg=%s seq=%d type=reasoning len=%d",
                        (session.message_id or "?")[:12],
                        session.sequence,
                        len(content),
                    )
                    t_se = _time.monotonic()
                    await self._client.cardkit_stream_element(
                        session.card_id,
                        seg.text_el_id,
                        content,
                        sequence=session.sequence,
                    )
                    _logger.debug("perf: stream_element msg=%s type=%s elapsed=%.0fms", (session.message_id or "?")[:12], seg.type, (_time.monotonic()-t_se)*1000)
                    seg.dirty = False
                elif seg.type == "answer":
                    content = seg.text
                    if session.image_resolver:
                        content = session.image_resolver.resolve_images(content)
                    content = _downgrade_tables(optimize_markdown_style(content)) or " "
                    session.sequence += 1
                    _logger.info(
                        "linear stream: msg=%s seq=%d type=answer len=%d",
                        (session.message_id or "?")[:12],
                        session.sequence,
                        len(content),
                    )
                    t_se = _time.monotonic()
                    await self._client.cardkit_stream_element(
                        session.card_id,
                        seg.el_id,
                        content,
                        sequence=session.sequence,
                    )
                    _logger.debug("perf: stream_element msg=%s type=%s elapsed=%.0fms", (session.message_id or "?")[:12], seg.type, (_time.monotonic()-t_se)*1000)
                    seg.dirty = False
            except Exception as e:
                _logger.debug("linear stream failed: %s el=%s", e, seg.el_id, exc_info=True)

        _logger.debug("perf: linear_flush msg=%s elapsed=%.0fms actions=%d", (session.message_id or "?")[:12], (_time.monotonic()-t0)*1000, len(actions))

    async def _do_linear_batch_update(
        self,
        session: CardSession,
        segments: list[Segment],
        actions: list[dict[str, Any]],
        new_el_ids: set[str],
        new_el_estimates: dict[str, int],
        updated_tool_segs: list[Segment],
    ) -> bool:
        """执行 batch_update 并处理快照/标记。返回 False 表示失败."""
        assert self._client is not None
        assert session.card_id is not None
        session.sequence += 1
        _logger.info(
            "linear flush: msg=%s seq=%d actions=%d",
            (session.message_id or "?")[:12],
            session.sequence,
            len(actions),
        )
        pre_flush_reasoning_elapsed = {
            seg.el_id: seg.elapsed_ms for seg in segments if seg.type == "reasoning"
        }
        pre_flush_tool_offsets = {
            seg.el_id: seg.tool_end_offset for seg in updated_tool_segs
        }
        try:
            await self._client.cardkit_batch_update(
                session.card_id,
                actions,
                sequence=session.sequence,
            )
            for seg in segments:
                if seg.el_id in new_el_ids:
                    seg.created = True
                    estimate = new_el_estimates.get(seg.el_id, 0)
                    seg.element_estimate = estimate
                    session.element_count += estimate
            for seg in segments:
                if seg.type == "reasoning" and pre_flush_reasoning_elapsed.get(seg.el_id, 0) > 0:
                    seg.reasoning_finalized = True
            # 注：旧版本在 new_el_ids 非空时会强制将所有已创建的
            # reasoning/answer segment 设 dirty=True（冗余重刷保险）。
            # v0.10.2 起，预填充优化已在 add_elements 时发送文本内容，
            # 且后续 delta 到来时 on_answer_delta/on_reasoning_delta 会
            # 自然标记 dirty，因此不再强制重刷——减少冗余 stream_element 调用。
            for seg in updated_tool_segs:
                offset_ok = pre_flush_tool_offsets.get(seg.el_id, -1) == seg.tool_end_offset
                if seg.el_id in new_el_estimates:
                    estimate = new_el_estimates[seg.el_id]
                    session.element_count += estimate - seg.element_estimate
                    seg.element_estimate = estimate
                if seg.created and offset_ok and seg.tool_end_offset > 0:
                    seg.dirty = False
        except FeishuAPIError as e:
            _logger.warning("linear batch update failed: %s", e, exc_info=True)
            handled = await self._handle_linear_flush_error_async(
                e, session, segments, actions, new_el_ids, new_el_estimates, updated_tool_segs,
            )
            if handled:
                return True  # 错误已处理（如拆卡），flush 可继续
            return False
        return True

    async def _preservative_seal(
        self,
        session: CardSession,
        *,
        partial: bool = False,
        footer_data: dict | None = None,
        is_error: bool = False,
        is_aborted: bool = False,
        error_message: str = "",
        footer_fields: list[list[str]] | None = None,
        footer_show_label: bool = False,
    ) -> bool:
        """保留式封卡：关闭流式模式 + 增量更新，不重建整卡.

        优势：流式阶段的 streaming element 在封卡后仍为 1 个元素（不触发 _split_long_text），
        避免 1→N+2M 的元素爆炸，根本性解决封卡超限问题。

        返回 True 表示成功，False 表示失败（需降级为全量重建）。
        失败时 session.sequence 可能已递增，调用方需自行处理降级路径。
        """
        assert self._client is not None
        card_id = session.card_id
        assert card_id is not None

        try:
            # Step 1: Close streaming mode
            session.sequence += 1
            _logger.info(
                "preservative seal: closing streaming card=%s seq=%d",
                card_id[:12], session.sequence,
            )
            await self._client.cardkit_close_streaming(card_id, sequence=session.sequence)

            # Step 2: Batch update — delete loading, add partial/footer
            actions = build_preservative_seal_actions(
                partial=partial,
                footer_data=footer_data,
                is_error=is_error,
                is_aborted=is_aborted,
                error_message=error_message,
                footer_fields=footer_fields,
                footer_show_label=footer_show_label,
            )
            if actions:
                session.sequence += 1
                _logger.info(
                    "preservative seal: batch_update card=%s seq=%d actions=%d",
                    card_id[:12], session.sequence, len(actions),
                )
                await self._client.cardkit_batch_update(
                    card_id,
                    actions,
                    sequence=session.sequence,
                )

            _logger.info(
                "preservative seal: success card=%s partial=%s",
                card_id[:12], partial,
            )
            return True
        except FeishuAPIError as e:
            if e.code == CARDKIT_SEQUENCE_CONFLICT:
                _logger.info(
                    "preservative seal: 300317 sequence conflict → idempotent success, card=%s",
                    card_id[:12],
                )
                return True
            _logger.debug(
                "preservative seal failed: card=%s, falling back to full rebuild",
                card_id[:12], exc_info=True,
            )
            return False
        except Exception:
            _logger.debug(
                "preservative seal failed: card=%s, falling back to full rebuild",
                (card_id or "")[:12], exc_info=True,
            )
            return False

    def _find_tool_split_offset(
        self,
        base_count: int,
        seg: Segment,
        all_steps: list[dict[str, Any]],
    ) -> int | None:
        """寻找 tool step 拆分点，让当前卡保留尽可能多的 steps."""
        start = seg.tool_offset
        end = _tool_segment_end(seg, all_steps)
        if end - start <= 1:
            return None
        for split_offset in range(end - 1, start, -1):
            estimate = _estimate_tool_elements(start, split_offset, all_steps)
            if base_count + estimate + _FOOTER_RESERVE <= _ELEMENT_THRESHOLD:
                return split_offset
        return None

    def _find_answer_split_offset(
        self,
        base_count: int,
        seg: Segment,
    ) -> int | None:
        """寻找 answer 文本拆分点，让当前卡保留尽可能多的文本块.

        按 `_split_long_text` 的实际分块边界拆分：
        1. 将 answer 文本按 2400 字符分块
        2. 从后往前找，找到当前卡能容纳的最大块数
        3. 反推字符偏移量作为拆分点
        """
        if not seg.text:
            return None
        content = _downgrade_tables(optimize_markdown_style(seg.text))
        chunks = _split_long_text(content)
        if len(chunks) <= 1:
            return None
        # 从后往前找：保留尽可能多的 chunks 在当前卡
        for keep in range(len(chunks), 0, -1):
            if base_count + keep + _FOOTER_RESERVE <= _ELEMENT_THRESHOLD:
                # 反推字符偏移：前 keep 个 chunk 的总长度
                char_offset = sum(len(c) for c in chunks[:keep])
                return char_offset
        return None

    async def _maybe_rollover_tool_segment(
        self,
        *,
        session: CardSession,
        linear_state: LinearState,
        index: int,
        seg: Segment,
        all_steps: list[dict[str, Any]],
        actions: list[dict[str, Any]],
        new_el_ids: set[str],
        new_el_estimates: dict[str, int],
        updated_tool_segs: list[Segment],
    ) -> str | None:
        """按 tool step 边界拆分过大的 dirty tool segment."""
        start = seg.tool_offset
        end = _tool_segment_end(seg, all_steps)
        estimate = _estimate_tool_elements(start, end, all_steps)
        delta = estimate - seg.element_estimate
        if (
            delta <= 0
            or session.element_count + delta + _FOOTER_RESERVE <= _ELEMENT_THRESHOLD
            or session.split_disabled
        ):
            return None

        split_offset = self._find_tool_split_offset(
            session.element_count - seg.element_estimate,
            seg,
            all_steps,
        )
        if split_offset is None:
            return None

        old_estimate = _estimate_tool_elements(seg.tool_offset, split_offset, all_steps)
        panel = _build_tool_panel(all_steps[seg.tool_offset:split_offset], expanded=self._cfg.streaming_panel_expanded)
        actions.append({
            "action": "partial_update_element",
            "params": {
                "element_id": seg.el_id,
                "partial_element": {
                    "elements": panel["elements"],
                    "header": panel["header"],
                },
            },
        })
        updated_tool_segs.append(seg)
        new_el_estimates[seg.el_id] = old_estimate
        linear_state.split_tool_segment(index, split_offset)
        split_ok = await self._do_linear_split(
            session, index + 1, actions, new_el_ids, new_el_estimates, updated_tool_segs,
        )
        if not split_ok:
            return "failed"
        return "split"

    async def _do_linear_split(
        self,
        session: CardSession,
        split_idx: int,
        actions: list[dict[str, Any]],
        new_el_ids: set[str],
        new_el_estimates: dict[str, int],
        updated_tool_segs: list[Segment],
    ) -> bool:
        """拆卡：先 flush pending actions，封旧卡，创建新卡。返回 False 表示失败需中断 flush.

        操作顺序：flush → seal old card → create new card
        封旧卡在创建新卡之前，避免并发 stream_element 导致 sequence 冲突使封卡静默失败。
        """
        assert self._client is not None
        old_card_id = session.card_id
        assert old_card_id is not None
        linear_state = session.linear_state
        assert linear_state is not None
        segments = linear_state.segments
        all_steps = session.tool_use.build_display_steps()

        # Step 1: Flush pending actions
        if actions and not await self._do_linear_batch_update(
            session, segments, actions, new_el_ids, new_el_estimates, updated_tool_segs,
        ):
            return False

        # Step 2: Seal old card FIRST (before creating new card, to avoid sequence conflicts)
        seal_segments = [s for s in segments[:split_idx] if s.created]
        if session.image_resolver:
            for seg in seal_segments:
                if seg.type == "answer" and seg.text:
                    try:
                        seg.text = await session.image_resolver.resolve_await(seg.text)
                    except Exception:
                        _logger.debug("linear seal image resolve failed: el=%s", seg.el_id, exc_info=True)

        seal_card = build_linear_complete_card(
            segments=seal_segments,
            all_tool_steps=all_steps,
            footer_fields=[],
            footer_show_label=False,
            panel_expanded=self._cfg.panel_expanded,
            partial=True,
            bg_review_messages=linear_state.bg_review_messages if linear_state else None,
        )

        try:
            seal_ok = await self._preservative_seal(session, partial=True)
            if not seal_ok:
                session.sequence += 1
                try:
                    await self._client.cardkit_close_streaming(old_card_id, sequence=session.sequence)
                except FeishuAPIError as close_err:
                    if close_err.code != CARDKIT_STREAMING_CLOSED:
                        raise
                session.sequence += 1
                await self._client.cardkit_update(old_card_id, seal_card, sequence=session.sequence)
        except FeishuAPIError as e:
            if e.code == CARDKIT_CONTENT_FAILED and e.extract_sub_code() == CARDKIT_ELEMENT_LIMIT:
                _logger.warning(
                    "linear seal element limit for old card %s, attempting compact seal",
                    old_card_id[:12],
                )
                try:
                    compact_seal = build_linear_compact_seal_card(
                        segments=seal_segments,
                        all_tool_steps=all_steps,
                        panel_expanded=self._cfg.panel_expanded,
                        partial=True,
                    )
                    session.sequence += 1
                    await self._client.cardkit_update(old_card_id, compact_seal, sequence=session.sequence)
                except Exception:
                    _logger.debug(
                        "compact seal also failed for old card %s, trying minimal seal",
                        old_card_id[:12], exc_info=True,
                    )
                    try:
                        minimal_seal = build_linear_complete_card(
                            segments=[s for s in seal_segments if s.type == "answer"][:1],
                            all_tool_steps=all_steps,
                            footer_fields=[],
                            footer_show_label=False,
                            panel_expanded=False,
                            partial=True,
                            bg_review_messages=linear_state.bg_review_messages if linear_state else None,
                        )
                        session.sequence += 1
                        await self._client.cardkit_update(old_card_id, minimal_seal, sequence=session.sequence)
                    except Exception:
                        _logger.debug(
                            "minimal seal also failed for old card %s",
                            old_card_id[:12], exc_info=True,
                        )
            else:
                _logger.warning(
                    "linear seal failed for old card %s, continuing",
                    old_card_id[:12],
                    exc_info=True,
                )
        except Exception:
            _logger.warning(
                "linear seal failed for old card %s, continuing",
                old_card_id[:12],
                exc_info=True,
            )

        # Step 3: Create new card (AFTER sealing old card)
        try:
            card = build_streaming_card_v2(
                show_tool_use=False,
                show_reasoning=False,
                show_streaming_element=False,
                streaming_panel_expanded=self._cfg.streaming_panel_expanded,
                print_strategy=self._cfg.print_strategy,
            )
            new_card_id = await self._client.cardkit_create(card)
            new_msg_id = await self._client.reply_card_by_id(
                session.anchor_id or session.message_id, new_card_id,
            )
        except Exception:
            _logger.warning(
                "linear split fallback: create next card failed, continue on current card",
                exc_info=True,
            )
            session.split_disabled = True
            return True

        # Insert context loading hint on new card
        try:
            await self._client.cardkit_batch_update(
                new_card_id,
                [{
                    "action": "add_elements",
                    "params": {
                        "type": "insert_before",
                        "target_element_id": _LOADING_ELEMENT_ID,
                        "elements": [_context_loading_element()],
                    },
                }],
                sequence=1,
            )
        except Exception:
            _logger.debug("context loading hint insert on new card failed", exc_info=True)

        # Update session for new card
        session.card_id = new_card_id
        session.card_msg_id = new_msg_id
        session.element_count = 2  # loading + context hint
        session.sequence = 1
        session.split_disabled = False
        session.element_limit_hit = False
        session.split_index = split_idx
        _logger.info(
            "linear split: msg=%s sealed=%d new_card=%s",
            (session.message_id or "?")[:12],
            len(seal_segments),
            new_card_id[:12],
        )
        return True

    async def _handle_linear_flush_error_async(
        self,
        e: FeishuAPIError,
        session: CardSession,
        segments: list[Segment],
        actions: list[dict[str, Any]],
        new_el_ids: set[str],
        new_el_estimates: dict[str, int],
        updated_tool_segs: list[Segment],
    ) -> bool:
        """处理 batch_update 错误。返回 True 表示错误已处理，flush 可继续。"""
        if e.code == CARDKIT_RATE_LIMITED:
            return False
        if e.code == CARDKIT_STREAMING_CLOSED:
            return False
        if e.code == CARDKIT_CONTENT_FAILED:
            sub_code = e.extract_sub_code()
            if sub_code == CARDKIT_ELEMENT_LIMIT:
                _logger.warning(
                    "linear card element limit exceeded: msg=%s element_count=%d split_disabled=%s",
                    (session.message_id or "?")[:12],
                    session.element_count,
                    session.split_disabled,
                )
                session.element_limit_hit = True
                # ── 超限触发拆卡 ──
                # 找到第一个未创建 segment 的索引作为拆分点
                split_idx = session.split_index
                for i, seg in enumerate(segments):
                    if i < session.split_index:
                        continue
                    if not seg.created:
                        split_idx = i
                        break
                # 如果所有段都已创建，拆分点在最后一个段之后
                if split_idx == session.split_index:
                    # 没有未创建段 → 已有段更新导致超限
                    # 找最后一个已创建段作为拆分边界
                    for i in range(len(segments) - 1, session.split_index - 1, -1):
                        if segments[i].created:
                            split_idx = i + 1
                            break

                if split_idx <= session.split_index:
                    # 没有可拆分的内容 → 标记后等待完成阶段处理
                    _logger.warning(
                        "linear element limit: no splittable content, deferring to complete: msg=%s",
                        (session.message_id or "?")[:12],
                    )
                    return False

                split_ok = await self._do_linear_split(
                    session, split_idx, actions, new_el_ids, new_el_estimates, updated_tool_segs,
                )
                if split_ok:
                    _logger.info(
                        "linear element limit triggered split: msg=%s split_at=%d",
                        (session.message_id or "?")[:12],
                        split_idx,
                    )
                    return True
                # 拆卡失败 → 禁用拆卡，标记超限，等完成阶段重建
                _logger.warning(
                    "linear element limit split failed, deferring to complete: msg=%s",
                    (session.message_id or "?")[:12],
                )
                return False
        return False

    async def _do_linear_complete(self, session: CardSession) -> bool:
        """线性模式完成：close streaming + 全量重建卡片（保持 segments 顺序）."""
        try:
            return await self._do_linear_complete_inner(session)
        finally:
            self._flush_deferred_background_reviews(session)
            self._release_session_data(session)
            self._cleanup(session.message_id)

    async def _do_linear_complete_inner(self, session: CardSession) -> bool:
        t0 = _time.monotonic()
        if session.guard.should_skip("_do_linear_complete"):
            return False

        await session.flush.wait_for_flush()
        session.flush.mark_completed()

        # ── Wait for card creation to finish ──
        # When on_completed fires before _do_create_linear_card finishes
        # (e.g. agent fails fast with HTTP 401), card_id is still None.
        # Without this await, the card would stay in streaming mode forever
        # because we skip close_streaming when card_id is None.
        try:
            await asyncio.wait_for(session._card_ready.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            _logger.warning(
                "linear complete: card creation timed out, msg=%s",
                (session.message_id or "?")[:12],
            )

        # If card creation failed, we cannot render a completion card.
        # Return False so card_sent=False → gateway sends its own text reply.
        if not session.card_id and not session.card_msg_id:
            _logger.info(
                "linear complete: no card to complete, msg=%s state=%s",
                (session.message_id or "?")[:12],
                session.state,
            )
            session.state = FAILED
            return False

        linear_state = session.linear_state
        is_error = session.state == FAILED
        # COMPLETING 状态下需通过 _was_aborted 获取中断标记
        is_aborted = getattr(session, "_was_aborted", False) or session.state == ABORTED
        error_message = getattr(session, "error_message", "")
        all_tool_steps = session.tool_use.build_display_steps()

        if linear_state is not None:
            linear_state.finalize_segments(len(all_tool_steps))

        active_segments = (
            linear_state.segments[session.split_index:] if linear_state is not None else []
        )

        if session.image_resolver:
            for seg in active_segments:
                if seg.type == "answer" and seg.text:
                    try:
                        seg.text = await session.image_resolver.resolve_await(seg.text)
                    except Exception:
                        _logger.debug("linear image resolve failed: el=%s", seg.el_id, exc_info=True)

        card = build_linear_complete_card(
            segments=active_segments,
            all_tool_steps=all_tool_steps,
            footer_data=session.footer,
            is_error=is_error,
            is_aborted=is_aborted,
            error_message=error_message,
            footer_fields=self._cfg.footer_fields,
            footer_show_label=self._cfg.footer_show_label,
            panel_expanded=self._cfg.panel_expanded,
            bg_review_messages=linear_state.bg_review_messages if linear_state else None,
        )

        # ── Try preservative seal first (no element explosion) ──
        # 保留式封卡：关闭流式 + 增量更新，不触发 _split_long_text，
        # 流式阶段的 streaming element 仍为 1 个元素。
        if session.card_id:
            seal_ok = await self._preservative_seal(
                session,
                partial=False,
                footer_data=session.footer,
                is_error=is_error,
                is_aborted=is_aborted,
                error_message=error_message,
                footer_fields=self._cfg.footer_fields,
                footer_show_label=self._cfg.footer_show_label,
            )
            if seal_ok:
                session.state = COMPLETED
                _logger.debug("perf: linear_complete msg=%s elapsed=%.0fms", (session.message_id or "?")[:12], (_time.monotonic()-t0)*1000)
                return True
            # Preservative seal failed — fall back to full rebuild below

        # ── Full rebuild path (preservative seal failed or not applicable) ──
        streaming_closed = False
        simplify_level = 0  # 0=full, 1=compact, 2=minimal
        for attempt in range(3):
            try:
                assert self._client is not None
                if session.card_id:
                    if not streaming_closed:
                        session.sequence += 1
                        try:
                            await self._client.cardkit_close_streaming(
                                session.card_id,
                                sequence=session.sequence,
                            )
                        except FeishuAPIError as close_err:
                            if close_err.code != CARDKIT_STREAMING_CLOSED:
                                raise
                            # Streaming already closed by preservative seal attempt
                        streaming_closed = True
                    session.sequence += 1
                    await self._client.cardkit_update(
                        session.card_id,
                        card,
                        sequence=session.sequence,
                    )
                session.state = COMPLETED
                _logger.debug("perf: linear_complete msg=%s elapsed=%.0fms", (session.message_id or "?")[:12], (_time.monotonic()-t0)*1000)
                return True
            except FeishuAPIError as e:
                # 300317 sequence 冲突 → 幂等成功
                # hermes 可能双调 on_completed（finally + pop_post_delivery_callback），
                # 竞态窗口内两次调用触发 300317，表示另一条路径已完成操作。
                if e.code == CARDKIT_SEQUENCE_CONFLICT:
                    _logger.info(
                        "linear complete: 300317 sequence conflict → idempotent success, "
                        "card_id=%s seq=%d",
                        session.card_id,
                        session.sequence,
                    )
                    session.state = COMPLETED
                    _logger.debug("perf: linear_complete msg=%s elapsed=%.0fms", (session.message_id or "?")[:12], (_time.monotonic()-t0)*1000)
                    return True

                # ── 300305 元素超限 → 渐进降级重试 ──
                # 重试时提交相同 payload 无意义（仍会超限），
                # 需要简化卡片内容后再提交。
                # Level 1 (compact): 保留所有面板但截断内容
                # Level 2 (minimal): 移除 reasoning，保留 tool+answer
                if (
                    e.code == CARDKIT_CONTENT_FAILED
                    and e.extract_sub_code() == CARDKIT_ELEMENT_LIMIT
                    and simplify_level < 2
                ):
                    simplify_level += 1
                    _logger.warning(
                        "linear complete: element limit (300305), rebuilding card (level %d): "
                        "card_id=%s msg=%s",
                        simplify_level,
                        session.card_id,
                        (session.message_id or "?")[:12],
                    )
                    simplified_active = self._simplify_segments_for_complete(
                        active_segments, all_tool_steps, level=simplify_level,
                    )
                    card = build_linear_complete_card(
                        segments=simplified_active,
                        all_tool_steps=all_tool_steps,
                        footer_data=session.footer,
                        is_error=is_error,
                        is_aborted=is_aborted,
                        error_message=error_message,
                        footer_fields=self._cfg.footer_fields,
                        footer_show_label=self._cfg.footer_show_label,
                        panel_expanded=self._cfg.panel_expanded,
                        bg_review_messages=linear_state.bg_review_messages if linear_state else None,
                    )
                    continue  # 立即用简化卡片重试，不等待

                _logger.warning(
                    "linear complete attempt %d failed: code=%s msg=%s card_id=%s seq=%d",
                    attempt,
                    e.code,
                    e,
                    session.card_id,
                    session.sequence,
                    exc_info=True,
                )
                if session.guard.terminate("_do_linear_complete", e):
                    return False
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                continue
            except Exception as e:
                _logger.warning(
                    "linear complete attempt %d failed: %s: %s card_id=%s seq=%d",
                    attempt,
                    type(e).__name__,
                    e,
                    session.card_id,
                    session.sequence,
                    exc_info=True,
                )
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                continue

        _logger.error(
            "linear complete failed after 3 attempts: card_id=%s seq=%d",
            session.card_id,
            session.sequence,
        )
        session.state = FAILED
        _logger.debug("perf: linear_complete msg=%s elapsed=%.0fms", (session.message_id or "?")[:12], (_time.monotonic()-t0)*1000)
        return False

    def _simplify_segments_for_complete(
        self,
        segments: list[Segment],
        all_tool_steps: list[dict[str, Any]],
        level: int = 1,
    ) -> list[Segment]:
        """为简化卡片构建精简 segment 列表.

        当封卡因元素超限 (300305) 失败时，构建一个精简版的 segment 列表。

        Level 1 (compact): 保留所有面板类型，截断内容以减少元素
          - reasoning 文本截断至 2000 字符
          - answer 文本截断至 4000 字符
          - tool 保留但精简步骤详情（移除 detail 和 result_block）
        Level 2 (minimal): 移除 reasoning，保留 tool+answer
          - 截断 answer 文本至 4000 字符
          - 保留 tool segment 但精简步骤详情
        """
        simplified = []
        for seg in segments:
            if seg.type == "reasoning":
                if level >= 2:
                    # Level 2+: drop reasoning entirely
                    continue
                # Level 1: truncate reasoning text
                if seg.text and len(seg.text) > 2000:
                    new_seg = Segment(seg.type, seg.el_id)
                    new_seg.text = seg.text[:2000] + "\n\n... (truncated)"
                    new_seg.text_el_id = seg.text_el_id
                    new_seg.created = seg.created
                    new_seg.element_estimate = 4
                    simplified.append(new_seg)
                else:
                    simplified.append(seg)
            elif seg.type == "answer":
                # 截断过长文本
                if len(seg.text) > 4000:
                    new_seg = Segment(seg.type, seg.el_id)
                    new_seg.text = seg.text[:4000] + "\n\n... (truncated)"
                    new_seg.text_el_id = seg.text_el_id
                    new_seg.created = seg.created
                    new_seg.element_estimate = 1  # 保守估算
                    simplified.append(new_seg)
                else:
                    simplified.append(seg)
            elif seg.type == "tool":
                # 保留 tool segment 但精简步骤详情
                simplified.append(seg)
            else:
                simplified.append(seg)
        if not simplified:
            # 如果所有 segment 都被过滤，至少保留 answer 的文本
            for seg in segments:
                if seg.type == "answer" and seg.text:
                    simple_seg = Segment("answer", seg.el_id)
                    simple_seg.text = seg.text[:4000]
                    simple_seg.text_el_id = seg.text_el_id
                    simple_seg.created = seg.created
                    simple_seg.element_estimate = 1
                    simplified.append(simple_seg)
                    break
        return simplified
