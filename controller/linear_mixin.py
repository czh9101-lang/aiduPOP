"""
Unified panel linear mode — create, flush, and seal a single-card streaming session.

Architecture
-----------
Card lifecycle (v1.0.2+):

Phase 1 — **Placeholder card** (_do_create_linear_card):
    When the user sends a message, create a placeholder card with only
    "正在加载上下文..." + loading icon (2 elements). No panel, no
    answer element — just a clean loading state.

Phase 2 — **First LLM token** (_do_unified_flush):
    When the first reasoning/tool/answer content arrives, delete the
    loading hint and add the unified panel + answer element via
    add_elements in a single batch_update call.

Phase 3 — **Streaming updates** (_do_unified_flush):
    Subsequent content updates the panel via partial_update_element
    and answer text via stream_element. Max 2 API calls per flush.

Phase 4 — **Complete** (_preservative_seal):
    Close streaming mode, update panel to final state, add footer.

Key elements:
- **1 unified panel** (UNIFIED_PANEL_ELEMENT_ID): holds all
  reasoning rounds and tool steps in a single collapsible panel.
- **1 answer streaming element** (ANSWER_ELEMENT_ID): receives
  answer text via cardkit_stream_element.
- **1 loading icon** (_LOADING_ELEMENT_ID): deleted on seal.

Migration
---------
LinearControllerMixin is kept as a backward-compatible alias for
UnifiedControllerMixin. All removed methods (_do_linear_split,
_maybe_rollover_tool_segment, etc.) are no longer present.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from ..cardkit import (
    ANSWER_ELEMENT_ID,
    UNIFIED_PANEL_ELEMENT_ID,
    _LOADING_ELEMENT_ID,
    _LOADING_HINT_ELEMENT_ID,
    _streaming_element,
    build_streaming_card_v2,
    build_im_fallback_card,
    build_unified_panel,
    build_unified_complete_card,
    build_preservative_seal_actions,
    _count_tag_objects,
    _enforce_card_element_limit,
)
from ..cardkit.md import _downgrade_tables, optimize_markdown_style
from ..state.linear import UnifiedLinearState
from ..state.text import split_reasoning_text
from ..feishu import (
    CARDKIT_SCHEMA_ERROR,
    CARDKIT_SEQUENCE_CONFLICT,
    CARDKIT_STREAMING_CLOSED,
    FeishuAPIError,
    is_schema_error,
)
from ..flush import PATCH_MS

from .mixin import (
    _TERMINAL,
    ABORTED,
    COMPLETED,
    COMPLETING,
    CREATION_FAILED,
    CREATING,
    FAILED,
    IDLE,
    STREAMING,
    TERMINATED,
)
from ..state.phase import TerminalReason

if TYPE_CHECKING:
    from ..config import Config
    from ..state.session import CardSession
    from ..feishu import FeishuClient

_logger = logging.getLogger("hermes_lark_streaming")

# ---------------------------------------------------------------------------
# TTL proactive extension
# ---------------------------------------------------------------------------

_TTL_EXTEND_THRESHOLD_SEC = 540.0  # Extend TTL when card has lived > 540s
_TTL_EXTEND_DELTA_SEC = 600        # Extend by 600s

# Fast-stream throttle for answer-only updates.
# When only answer text is dirty (no panel changes), use a shorter
# throttle interval so Feishu's typewriter renders characters
# smoothly one-by-one instead of in bursts.  When panel content is
# also dirty, the normal flush interval is used since panel updates
# are inherently batch operations.
#
# NOTE: This is the *server-side flush interval* (how often we send
# stream_element API calls).  It is NOT the same as Feishu's client-
# side print_frequency_ms (which controls the typewriter render speed
# on the user's device).  The two work together: we flush content to
# Feishu at this interval, and Feishu renders it character-by-character
# at print_frequency_ms pace.  We keep this at 70ms to align with the
# official print_frequency_ms default, avoiding over-buffering.
_ANSWER_FAST_STREAM_MS = 0.070


class UnifiedControllerMixin:
    """Unified panel linear mode — phased card lifecycle.

    This mixin is designed to be inherited by :class:`StreamCardController`
    alongside :class:`ControllerMixin`.  It provides the linear-mode
    creation, flush, and completion logic using the unified panel
    architecture where all reasoning rounds and tool steps live in **one**
    collapsible panel element.

    Card lifecycle:
    Phase 1 — Placeholder card ("正在加载上下文..." only)
    Phase 2 — First token: add panel + answer element, delete loading hint
    Phase 3 — Stream panel + answer updates
    Phase 4 — Complete: close streaming, add footer
    """

    # ── Instance attributes provided by StreamCardController ──
    _client: FeishuClient | None
    _cfg: Config
    _ensure_init: Callable[..., Coroutine[Any, Any, None]]
    _schedule_card_update: Callable[[CardSession], None]
    _cleanup: Callable[[str], None]
    _flush_deferred_background_reviews: Callable[[CardSession], None]
    _do_complete_inner: Callable[..., Coroutine[Any, Any, bool]]

    # ===================================================================
    # Card creation
    # ===================================================================

    async def _do_create_linear_card(self, session: CardSession) -> None:
        """Create the initial placeholder card — loading hint only, no panel.

        Card lifecycle (v1.0.2+):
        Phase 1 — This method: Create placeholder card with only
        "正在加载上下文..." + loading icon (2 elements).
        Phase 2 — First LLM token: Delete loading hint, add unified
        panel + answer element via ``add_elements``.
        Phase 3 — Stream panel content + answer text.
        Phase 4 — Complete: Add footer.
        """
        if session.state != IDLE:
            return
        # Snapshot epoch before async creation
        epoch = session.create_epoch
        session.state = CREATING
        session._create_epoch_snap = epoch
        session.linear = True
        session.unified_state = UnifiedLinearState()

        t0 = _time.monotonic()
        try:
            await self._ensure_init()
            assert self._client is not None

            try:
                reply_to = session.anchor_id or session.message_id
                card = build_streaming_card_v2(
                    include_unified_panel=False,   # Panel added on first token
                    include_answer_element=False,   # Answer element added with panel
                    include_loading_hint=True,      # "正在加载上下文..."
                    streaming_panel_expanded=self._cfg.streaming_panel_expanded,
                    print_strategy=self._cfg.print_strategy,
                    header_enabled=self._cfg.header_enabled,
                )
                card_id = await self._client.cardkit_create(card)
                card_msg_id = await self._client.reply_card_by_id(reply_to, card_id)

                session.card_id = card_id
                session.card_msg_id = card_msg_id
                session.use_cardkit = True
                session.card_created_at = _time.time()
                session.flush.set_throttle(self._cfg.flush_interval_sec)

                # Track existing elements — only 2 are pre-allocated
                session.existing_elements = {
                    _LOADING_HINT_ELEMENT_ID,
                    _LOADING_ELEMENT_ID,
                }
                session._panel_element_created = False  # Panel NOT in initial card

            except FeishuAPIError:
                _logger.info("linear CardKit create failed, falling back to non-linear")
                card = build_im_fallback_card()
                card_msg_id = await self._client.reply_card(reply_to, card)
                session.card_msg_id = card_msg_id
                session.use_cardkit = False
                session.linear = False
                session.unified_state = None
                session.flush.set_throttle(PATCH_MS)

            session.flush.set_card_message_ready(True)

            # ── Stale-create guard ──
            # If the session was terminated/aborted while we were awaiting
            # card creation, the epoch will have changed — skip the
            # CREATING → STREAMING transition.
            if session.state == CREATING and not session.is_stale_create(epoch):
                session.state = STREAMING

            # ── Execute deferred flush immediately after card is ready ──
            # When reasoning/tool deltas arrived while the card was still
            # being created, _schedule_linear_flush marked _pending_flush
            # instead of scheduling (card_message_ready was False).  Now
            # that the card is ready, execute the flush immediately so the
            # user sees content without waiting for the next event.
            if session.linear and session.unified_state and (
                session.unified_state.has_dirty or session._pending_flush
            ):
                session._pending_flush = False
                if not session._first_flush_done:
                    # First content → immediate flush (首字即显)
                    session._first_flush_done = True
                    asyncio.get_event_loop().create_task(
                        session.flush.flush_now(lambda: self._do_unified_flush(session))
                    )
                else:
                    # Subsequent content → throttled flush
                    self._schedule_linear_flush(session)

            # ── Signal card readiness ──
            # Must be set AFTER card_id/card_msg_id are assigned and
            # session state is transitioned out of CREATING.
            session._card_ready.set()
            _logger.info(
                "linear card created: msg=%s linear=%s card_id=%s",
                (session.message_id or "?")[:12],
                session.linear,
                (session.card_id or "")[:12],
            )
        except Exception:
            _logger.exception("_do_create_linear_card failed")
            session.state = CREATION_FAILED
            session.enter_terminal(
                reason=TerminalReason.CREATION_FAILED,
                source="_do_create_linear_card",
            )
            # Signal readiness even on failure so awaiters don't deadlock
            session._card_ready.set()

        _logger.debug(
            "perf: card_create msg=%s elapsed=%.0fms",
            (session.message_id or "?")[:12],
            (_time.monotonic() - t0) * 1000,
        )

    # ===================================================================
    # Flush scheduling
    # ===================================================================

    def _schedule_linear_flush(self, session: CardSession) -> None:
        """Schedule a unified panel flush for the given session.

        First-token immediate flush (首字即显): when this is the first
        content for the session and there are dirty data, skip the
        throttle interval and flush immediately.  This reduces
        first-visible-text latency by 0~500 ms.

        Deferred flush (卡片未就绪): when data arrives before the card
        is created (``card_message_ready=False``), the flush request is
        deferred.  The card creation routine will pick it up once the
        card is ready.
        """
        if not session.should_proceed("_schedule_linear_flush"):
            return
        # COMPLETING is not terminal, but we should not schedule new flushes
        if session.state == IDLE or session.state == COMPLETING:
            return

        state = session.unified_state
        if state is None or not state.has_dirty:
            return

        # ── Card not ready yet — mark deferred instead of dropping ──
        # When reasoning/tool deltas arrive before card creation completes,
        # schedule_update would silently drop them (card_message_ready=False).
        # Mark the session as needing a flush; the card creation routine
        # will execute it once the card is ready.
        if not session.flush._card_message_ready:
            session._pending_flush = True
            return

        # ── First-Token Immediate Flush (首字即显) ──
        if not session._first_flush_done:
            session._first_flush_done = True
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = session._loop
            if loop is not None and not loop.is_closed():
                loop.create_task(
                    session.flush.flush_now(lambda: self._do_unified_flush(session))
                )
            return

        # ── Dynamic throttle for typewriter effect ──
        # When only answer text is dirty (no panel changes), use a shorter
        # throttle interval so Feishu's typewriter renders characters smoothly
        # instead of in bursts.  When panel is also dirty, use the normal
        # flush interval since panel updates are inherently batch operations.
        _answer_only = state.answer_dirty and not state.panel_dirty and not state.tool_steps_dirty
        if _answer_only:
            session.flush.set_throttle(_ANSWER_FAST_STREAM_MS)
        else:
            session.flush.set_throttle(self._cfg.flush_interval_sec)

        session.flush.schedule_update(lambda: self._do_unified_flush(session))

    # ===================================================================
    # Unified flush
    # ===================================================================

    async def _do_unified_flush(self, session: CardSession) -> None:
        """Unified panel flush — max 2 API calls per flush cycle.

        Card lifecycle phases handled here:

        Phase 2 (first LLM token):
            When the first reasoning/tool/answer content arrives and the
            panel hasn't been created yet, this flush:
            1. Builds the unified panel with initial content
            2. Adds panel + answer element via ``add_elements``
            3. Deletes the "正在加载上下文..." loading hint
            All in a single ``batch_update`` call.

        Phase 3 (streaming):
            Subsequent flushes update existing elements:
            1. ``partial_update_element`` for panel content
            2. ``stream_element`` for answer text

        Phase 4 (complete):
            Handled by ``_preservative_seal``.
        """
        if session.is_terminal_phase or session.state == COMPLETING or not session.card_id:
            return
        state = session.unified_state
        if state is None:
            return
        assert self._client is not None

        # ── TTL proactive extension ──
        if session.card_created_at and _time.time() - session.card_created_at > _TTL_EXTEND_THRESHOLD_SEC:
            try:
                session.sequence += 1
                await self._client.cardkit_extend_ttl(
                    session.card_id,
                    ttl_seconds=_TTL_EXTEND_DELTA_SEC,
                    sequence=session.sequence,
                )
                _logger.info(
                    "TTL extended: card=%s seq=%d",
                    session.card_id[:12],
                    session.sequence,
                )
            except Exception:
                _logger.debug("TTL extend failed, ignoring", exc_info=True)

        actions: list[dict[str, Any]] = []

        # ── Phase 2: First content — add answer element (and panel if needed), delete loading hint ──
        #
        # Bug fix (v1.0.5): Split Phase 2 into two sub-paths:
        #   A) If panel_visible (reasoning/tools exist) → add panel + answer element
        #   B) If only answer text (simple conversation) → add answer element only, no panel
        #
        # Previously, the condition was `panel_visible or answer_dirty or answer_text`,
        # which always created the unified panel — even for simple conversations with
        # no tools/reasoning, producing an empty collapsible panel.
        #
        # The answer element must be created in BOTH paths so the answer text can be
        # streamed to the card. Without it, simple conversations would never show text.
        if not session._answer_element_created and (state.panel_visible or state.answer_dirty or state.answer_text):
            new_elements: list[dict[str, Any]] = []

            # ── Path A: Has reasoning or tools → add unified panel ──
            if state.panel_visible:
                all_tool_steps = session.tool_use.build_display_steps()
                panel = build_unified_panel(
                    reasoning_rounds=state.reasoning_rounds,
                    current_reasoning_text=state.current_reasoning_text,
                    tool_steps=all_tool_steps,
                    tool_elapsed_ms=session.tool_use.elapsed_ms,
                    show_reasoning=self._cfg.show_reasoning,
                    expanded=self._cfg.streaming_panel_expanded,
                    panel_events=state.panel_events,
                    max_tool_steps=self._cfg.max_tool_steps,
                    max_reasoning_rounds=self._cfg.max_reasoning_rounds,
                )
                new_elements.append(panel)

            # ── Path A & B: Always add answer streaming element ──
            new_elements.append(_streaming_element(element_id=ANSWER_ELEMENT_ID))

            # Add new elements before loading hint
            actions.append({
                "action": "add_elements",
                "params": {
                    "type": "insert_before",
                    "target_element_id": _LOADING_HINT_ELEMENT_ID,
                    "elements": new_elements,
                },
            })
            # Delete loading hint
            if _LOADING_HINT_ELEMENT_ID in session.existing_elements:
                actions.append({
                    "action": "delete_elements",
                    "params": {"element_ids": [_LOADING_HINT_ELEMENT_ID]},
                })
            # Note: panel_dirty and tool_steps_dirty are cleared AFTER
            # the API call succeeds, not before — if the call fails we
            # want the next flush to retry Phase 2 with fresh content.

            # ── Execute Phase 2 batch_update ──
            if actions:
                _has_panel = state.panel_visible
                session.sequence += 1
                _logger.info(
                    "unified flush (phase 2 — add %s): msg=%s seq=%d actions=%d",
                    "panel+answer" if _has_panel else "answer only",
                    (session.message_id or "?")[:12],
                    session.sequence,
                    len(actions),
                )
                try:
                    await self._client.cardkit_batch_update(
                        session.card_id, actions, sequence=session.sequence,
                    )
                    # Update tracking after success
                    session._answer_element_created = True
                    session._loading_hint_removed = True
                    session.existing_elements.add(ANSWER_ELEMENT_ID)
                    if _has_panel:
                        session._panel_element_created = True
                        session.existing_elements.add(UNIFIED_PANEL_ELEMENT_ID)
                    session.existing_elements.discard(_LOADING_HINT_ELEMENT_ID)
                    # Clear dirty flags only after API success
                    state.panel_dirty = False
                    state.tool_steps_dirty = False
                except FeishuAPIError as e:
                    if e.code == CARDKIT_STREAMING_CLOSED:
                        _logger.info(
                            "unified flush: streaming closed, will be handled by TTL or seal: card=%s",
                            session.card_id[:12],
                        )
                        session._streaming_closed = True
                        return
                    if is_schema_error(e):
                        # ── Schema error (300315): permanent, don't retry ──
                        # This typically means an invalid property on a CardKit
                        # element.  Log with full error so the developer can
                        # identify the offending property, then mark element as
                        # created to prevent infinite retry loops.
                        _logger.error(
                            "unified flush phase 2 SCHEMA ERROR (permanent): %s — "
                            "detail: %s — "
                            "marking elements as created to prevent retry loop, card=%s",
                            e, e.extract_schema_detail(), session.card_id[:12],
                        )
                        session._answer_element_created = True  # Prevent retry loop
                        session._panel_element_created = True
                        session._loading_hint_removed = True
                        # Fall through to Phase 3 (partial_update may still fail
                        # if panel wasn't actually added, but at least we won't
                        # loop infinitely on Phase 2)
                    else:
                        _logger.warning("unified flush phase 2 batch_update failed: %s", e)
                        return

            # ── Stream answer text if also dirty ──
            # Note: skip markdown optimization during streaming for performance;
            # it will be applied on seal via _preservative_seal.
            if state.answer_dirty:
                content = state.answer_text or " "
                session.sequence += 1
                _logger.debug(
                    "unified stream: msg=%s seq=%d type=answer len=%d",
                    (session.message_id or "?")[:12],
                    session.sequence,
                    len(content),
                )
                try:
                    await self._client.cardkit_stream_element(
                        session.card_id, ANSWER_ELEMENT_ID, content, sequence=session.sequence,
                    )
                    state.answer_dirty = False
                except FeishuAPIError as e:
                    if e.code == CARDKIT_STREAMING_CLOSED:
                        session._streaming_closed = True
                        return
                    _logger.debug("unified stream_element failed: %s", e)

            # ── Re-check for new dirty data after Phase 2 ──
            # While Phase 2 was executing (add_elements + stream_element),
            # new reasoning/tool deltas may have arrived and set panel_dirty.
            # Don't return immediately — fall through to Phase 3 so the
            # panel content is updated in the same flush cycle.
            if not state.panel_dirty and not state.tool_steps_dirty and not state.answer_dirty:
                return  # Phase 2 done, nothing more to do

        # ── Phase 3: Update existing panel + stream answer ──
        if state.panel_dirty:
            if session._panel_element_created:
                # Panel exists — update its content
                all_tool_steps = session.tool_use.build_display_steps()
                panel = build_unified_panel(
                    reasoning_rounds=state.reasoning_rounds,
                    current_reasoning_text=state.current_reasoning_text,
                    tool_steps=all_tool_steps,
                    tool_elapsed_ms=session.tool_use.elapsed_ms,
                    show_reasoning=self._cfg.show_reasoning,
                    expanded=self._cfg.streaming_panel_expanded,
                    panel_events=state.panel_events,
                    max_tool_steps=self._cfg.max_tool_steps,
                    max_reasoning_rounds=self._cfg.max_reasoning_rounds,
                )
                actions.append({
                    "action": "partial_update_element",
                    "params": {
                        "element_id": UNIFIED_PANEL_ELEMENT_ID,
                        "partial_element": {
                            "header": panel["header"],
                            "elements": panel["elements"],
                        },
                    },
                })
            elif session._answer_element_created:
                # ── Bug fix (v1.0.5): Late-arriving reasoning/tools ──
                # The answer element was created first (simple conversation path),
                # but now reasoning/tool events have arrived. We need to add the
                # panel element dynamically via add_elements, inserting before
                # the answer element.
                all_tool_steps = session.tool_use.build_display_steps()
                panel = build_unified_panel(
                    reasoning_rounds=state.reasoning_rounds,
                    current_reasoning_text=state.current_reasoning_text,
                    tool_steps=all_tool_steps,
                    tool_elapsed_ms=session.tool_use.elapsed_ms,
                    show_reasoning=self._cfg.show_reasoning,
                    expanded=self._cfg.streaming_panel_expanded,
                    panel_events=state.panel_events,
                    max_tool_steps=self._cfg.max_tool_steps,
                    max_reasoning_rounds=self._cfg.max_reasoning_rounds,
                )
                actions.append({
                    "action": "add_elements",
                    "params": {
                        "type": "insert_before",
                        "target_element_id": ANSWER_ELEMENT_ID,
                        "elements": [panel],
                    },
                })
                # Note: _panel_element_created will be set after API success below
            # Note: panel_dirty and tool_steps_dirty are cleared AFTER
            # the API call succeeds, not before — if the call fails we
            # want the next flush to rebuild the panel content.

        # ── Delete loading hint if still present (safety net) ──
        _hint_delete_in_batch = False
        if not session._loading_hint_removed and _LOADING_HINT_ELEMENT_ID in session.existing_elements:
            actions.append({
                "action": "delete_elements",
                "params": {"element_ids": [_LOADING_HINT_ELEMENT_ID]},
            })
            _hint_delete_in_batch = True

        # ── Execute Phase 3 batch_update ──
        if actions:
            session.sequence += 1
            _logger.info(
                "unified flush: msg=%s seq=%d actions=%d hint_delete=%s",
                (session.message_id or "?")[:12],
                session.sequence,
                len(actions),
                _hint_delete_in_batch,
            )
            try:
                await self._client.cardkit_batch_update(
                    session.card_id, actions, sequence=session.sequence,
                )
                # Clear dirty flags only after API success
                if state.panel_dirty or state.tool_steps_dirty:
                    state.panel_dirty = False
                    state.tool_steps_dirty = False
                if _hint_delete_in_batch:
                    session._loading_hint_removed = True
                    session.existing_elements.discard(_LOADING_HINT_ELEMENT_ID)
                # ── Track late-arriving panel creation ──
                if not session._panel_element_created and state.panel_visible:
                    session._panel_element_created = True
                    session.existing_elements.add(UNIFIED_PANEL_ELEMENT_ID)
            except FeishuAPIError as e:
                if e.code == CARDKIT_STREAMING_CLOSED:
                    _logger.info(
                        "unified flush: streaming closed, will be handled by TTL or seal: card=%s",
                        session.card_id[:12],
                    )
                    session._streaming_closed = True
                    return
                if is_schema_error(e):
                    _logger.error(
                        "unified flush phase 3 SCHEMA ERROR (permanent): %s — "
                        "detail: %s — "
                        "clearing dirty flags to stop retry, card=%s",
                        e, e.extract_schema_detail(), session.card_id[:12],
                    )
                    # Clear dirty to stop retry loop on permanent errors
                    state.panel_dirty = False
                    state.tool_steps_dirty = False
                    return
                _logger.warning("unified flush batch_update failed: %s", e)
                return

        # ── Stream answer text ──
        # Note: skip markdown optimization during streaming for performance;
        # it will be applied on seal via _preservative_seal.
        if state.answer_dirty and session._answer_element_created:
            content = state.answer_text or " "
            session.sequence += 1
            _logger.debug(
                "unified stream: msg=%s seq=%d type=answer len=%d",
                (session.message_id or "?")[:12],
                session.sequence,
                len(content),
            )
            try:
                t_se = _time.monotonic()
                await self._client.cardkit_stream_element(
                    session.card_id, ANSWER_ELEMENT_ID, content, sequence=session.sequence,
                )
                _logger.debug(
                    "perf: stream_element msg=%s type=answer elapsed=%.0fms",
                    (session.message_id or "?")[:12],
                    (_time.monotonic() - t_se) * 1000,
                )
                state.answer_dirty = False
            except FeishuAPIError as e:
                if e.code == CARDKIT_STREAMING_CLOSED:
                    _logger.info(
                        "unified stream: streaming closed, will be handled by TTL or seal: card=%s",
                        session.card_id[:12],
                    )
                    session._streaming_closed = True
                    return
                _logger.debug("unified stream_element failed: %s", e)

        # ── Re-check: schedule next flush if new data arrived during this flush ──
        # While we were awaiting API calls, new reasoning/tool deltas may have
        # set panel_dirty or answer_dirty.  Schedule a follow-up flush so the
        # panel content stays up-to-date in real-time.
        if state.panel_dirty or state.answer_dirty or state.tool_steps_dirty:
            self._schedule_linear_flush(session)

    # ===================================================================
    # Thinking handler
    # ===================================================================

    def _linear_on_thinking(self, session: CardSession, text: str) -> None:
        """Handle a thinking/reasoning delta in linear mode.

        Splits the incoming text into reasoning and answer components,
        updates the unified state, and schedules a flush.

        Native reasoning dedup
        ----------------------
        When the model provides a dedicated ``reasoning_callback`` (e.g.
        DeepSeek, QwQ), reasoning text arrives incrementally via
        :meth:`on_reasoning` → :meth:`on_reasoning_delta`.  The
        ``interim_assistant_callback`` also delivers the same reasoning
        text in accumulated form.  Without the ``_native_reasoning_active``
        guard, ``on_reasoning_delta`` would *append* the accumulated text
        again, causing every token to appear twice in the collapsible
        panel ("TheThe user user is is saying saying…").
        """
        state = session.unified_state
        if state is None:
            return
        split = split_reasoning_text(text)
        reasoning = split.get("reasoning_text")
        answer = split.get("answer_text")

        _logger.debug(
            "HLS_DIAG: _linear_on_thinking msg=%s text_head=%r "
            "reasoning=%s answer=%s _native_reasoning_active=%s "
            "show_reasoning=%s current_reasoning_len=%d",
            (session.message_id or "?")[:12],
            text[:80] if text else "",
            bool(reasoning),
            bool(answer),
            state._native_reasoning_active,
            self._cfg.show_reasoning,
            len(state._current_reasoning),
        )

        # ── Native reasoning dedup ──
        # When the model provides a dedicated reasoning_callback (e.g.
        # DeepSeek, QwQ), reasoning text is already tracked via
        # on_reasoning → on_reasoning_delta.  The interim_assistant_callback
        # delivers the same text in accumulated form — appending it again
        # via on_reasoning_delta would double the content.
        if reasoning and self._cfg.show_reasoning and not state._native_reasoning_active:
            _logger.debug(
                "HLS_DIAG: _linear_on_thinking APPENDS reasoning via on_reasoning_delta "
                "msg=%s reasoning_head=%r",
                (session.message_id or "?")[:12],
                reasoning[:60] if reasoning else "",
            )
            state.on_reasoning_delta(reasoning)
        if answer:
            # ── Answer dedup with incremental append ──
            # interim_assistant_callback delivers ACCUMULATED text (not incremental).
            # When the model generates multiple answer segments (answer -> tool -> answer),
            # each interim call contains the full text so far. We must:
            #   1. If no answer exists yet -> accept the full text
            #   2. If the new text starts with the existing answer -> append only the diff
            #   3. If the new text is different -> accept it (edge case: model rewrite)
            _existing_len = len(state.answer_text)
            if _existing_len == 0:
                # No answer yet - accept the full text
                state.on_answer_delta(answer)
            elif len(answer) > _existing_len and answer[:_existing_len] == state.answer_text:
                # New text extends the existing answer - append only the new portion
                _new_part = answer[_existing_len:]
                if _new_part:
                    _logger.info(
                        "HLS_FIX: _linear_on_thinking appends incremental answer "
                        "existing_len=%d new_total=%d diff=%d msg=%s",
                        _existing_len, len(answer), len(_new_part),
                        (session.message_id or "?")[:12],
                    )
                    state.on_answer_delta(_new_part)
            # else: text is same length or shorter - already captured, skip
        if (reasoning and self._cfg.show_reasoning and not state._native_reasoning_active) or answer:
            self._schedule_linear_flush(session)

    # ===================================================================
    # Preservative seal
    # ===================================================================

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
        """Preservative seal — update panel + add footer + close streaming.

        This is the primary seal mechanism for the unified panel
        architecture.  It:

        1. Updates the unified panel to its final non-streaming state
           via ``partial_update_element`` (finalized reasoning, no
           in-progress text).
        2. Updates the answer element with optimized markdown.
        3. Adds footer / error panel / deletes loading elements via
           ``build_preservative_seal_actions``.
        4. **Card-level element limit safety net**: Counts total tag
           objects across all elements that will exist after seal.
           If over 195 (200 - 5 margin), trims oldest items from
           panel children until under threshold, adding/updating a
           collapse hint.
        5. batch_update (while still in streaming mode).
        6. Closes the streaming session (``cardkit_close_streaming``).

        By performing the batch_update BEFORE close_streaming, we
        ensure that the card's content and footer are visible during
        the streaming→non-streaming transition, avoiding a flash of
        incomplete content.

        Returns ``True`` on success, ``False`` on failure (caller
        should fall back to full card rebuild).
        """
        assert self._client is not None
        card_id = session.card_id
        assert card_id is not None

        try:
            # ── Content completeness guard — FLUSH, don't drop ──
            # Before closing streaming, we MUST flush any remaining dirty
            # data to the card.  The drain loop in _do_linear_complete
            # handles the common case, but in edge cases (e.g. a very
            # late on_answer callback arriving after mark_completed but
            # before seal), dirty flags might still be set.  Unlike the
            # previous implementation which merely logged and cleared the
            # flags (silently dropping content), we now actually flush
            # the remaining content BEFORE close_streaming, because once
            # streaming is closed, stream_element can no longer be called
            # and the content would be permanently lost — causing the
            # "footer appears before content finishes" bug.
            state = session.unified_state
            if state is not None and (state.answer_dirty or state.panel_dirty or state.tool_steps_dirty):
                _logger.warning(
                    "preservative seal: dirty data detected at seal time "
                    "answer_dirty=%s panel_dirty=%s tool_steps_dirty=%s card=%s — "
                    "flushing before close",
                    state.answer_dirty, state.panel_dirty, state.tool_steps_dirty,
                    card_id[:12],
                )
                # ── Flush remaining panel content ──
                if (state.panel_dirty or state.tool_steps_dirty) and session._panel_element_created:
                    all_tool_steps = session.tool_use.build_display_steps()
                    panel = build_unified_panel(
                        reasoning_rounds=state.reasoning_rounds,
                        current_reasoning_text=state.current_reasoning_text,
                        tool_steps=all_tool_steps,
                        tool_elapsed_ms=session.tool_use.elapsed_ms,
                        show_reasoning=self._cfg.show_reasoning,
                        expanded=self._cfg.streaming_panel_expanded,
                        panel_events=state.panel_events,
                        max_tool_steps=self._cfg.max_tool_steps,
                        max_reasoning_rounds=self._cfg.max_reasoning_rounds,
                    )
                    try:
                        session.sequence += 1
                        await self._client.cardkit_batch_update(
                            session.card_id,
                            [{
                                "action": "partial_update_element",
                                "params": {
                                    "element_id": UNIFIED_PANEL_ELEMENT_ID,
                                    "partial_element": {
                                        "header": panel["header"],
                                        "elements": panel["elements"],
                                    },
                                },
                            }],
                            sequence=session.sequence,
                        )
                        state.panel_dirty = False
                        state.tool_steps_dirty = False
                    except FeishuAPIError as e:
                        if e.code == CARDKIT_STREAMING_CLOSED:
                            _logger.info("seal drain: streaming already closed, skipping panel flush")
                            session._streaming_closed = True
                        else:
                            _logger.warning("seal drain panel failed: %s", e)
                        state.panel_dirty = False
                        state.tool_steps_dirty = False

                # ── Flush remaining answer text ──
                if state.answer_dirty and session._answer_element_created and not session._streaming_closed:
                    content = state.answer_text or " "
                    try:
                        session.sequence += 1
                        _logger.info(
                            "seal drain: flushing answer text len=%d card=%s",
                            len(content), card_id[:12],
                        )
                        await self._client.cardkit_stream_element(
                            session.card_id, ANSWER_ELEMENT_ID, content,
                            sequence=session.sequence,
                        )
                        state.answer_dirty = False
                    except FeishuAPIError as e:
                        if e.code == CARDKIT_STREAMING_CLOSED:
                            _logger.info("seal drain: streaming already closed, skipping answer flush")
                            session._streaming_closed = True
                        else:
                            _logger.warning("seal drain answer failed: %s", e)
                        state.answer_dirty = False

            # ── Step 1: Update unified panel to final state (non-streaming) ──
            seal_actions: list[dict[str, Any]] = []
            panel: dict[str, Any] | None = None

            if state is not None:
                state.finalize()

                # ── Bug fix (v1.0.5): Only update panel if it was created ──
                # Simple conversations (no tools/reasoning) don't have a panel element.
                # Attempting to partial_update a non-existent element causes a
                # FeishuAPIError.  Only update the panel when _panel_element_created.
                if session._panel_element_created:
                    all_tool_steps = session.tool_use.build_display_steps()
                    panel = build_unified_panel(
                        reasoning_rounds=state.reasoning_rounds,
                        current_reasoning_text="",
                        tool_steps=all_tool_steps,
                        tool_elapsed_ms=session.tool_use.elapsed_ms,
                        show_reasoning=self._cfg.show_reasoning,
                        expanded=self._cfg.panel_expanded,
                        panel_events=state.panel_events,
                        max_tool_steps=self._cfg.max_tool_steps,
                        max_reasoning_rounds=self._cfg.max_reasoning_rounds,
                    )
                    seal_actions.append({
                        "action": "partial_update_element",
                        "params": {
                            "element_id": UNIFIED_PANEL_ELEMENT_ID,
                            "partial_element": {
                                "header": panel["header"],
                                "elements": panel["elements"],
                            },
                        },
                    })

            # ── Step 2: Update answer element with optimized markdown ──
            # During streaming, answer text was sent raw (no markdown optimization)
            # for performance. Now that streaming is about to be closed, update the answer
            # element with the fully optimized markdown content.
            if state is not None and state.answer_text and session._answer_element_created:
                optimized_content = _downgrade_tables(optimize_markdown_style(state.answer_text)) or " "
                seal_actions.append({
                    "action": "partial_update_element",
                    "params": {
                        "element_id": ANSWER_ELEMENT_ID,
                        "partial_element": {
                            "content": optimized_content,
                        },
                    },
                })

            # ── Step 3: Add footer + delete loading elements ──
            seal_actions.extend(
                build_preservative_seal_actions(
                    partial=partial,
                    footer_data=footer_data,
                    is_error=is_error,
                    is_aborted=is_aborted,
                    error_message=error_message,
                    footer_fields=footer_fields,
                    footer_show_label=footer_show_label,
                    existing_elements=session.existing_elements,
                )
            )

            # ── Card-level element limit safety net ──
            # Before submitting batch_update, count the total tag objects
            # that the card will have after all seal actions are applied.
            # If over 195 (200 - 5 margin), trim oldest items from the
            # panel children and rebuild the panel update action.
            #
            # We simulate the final card elements by collecting all
            # new/updated elements from seal_actions, then counting
            # with _count_tag_objects.
            if panel is not None:
                # Collect all elements that will exist after seal:
                # - Panel (updated) + Answer (updated) + Footer/Error (added)
                # We don't count elements being deleted (loading icon/hint).
                simulated_elements: list[dict] = []
                # Panel
                simulated_elements.append(panel)
                # Answer element (1 markdown with content)
                if state is not None and state.answer_text:
                    simulated_elements.append({"tag": "markdown", "content": state.answer_text})
                else:
                    simulated_elements.append({"tag": "markdown", "content": " "})
                # Elements from add_elements actions (footer, error, partial, bg review)
                for action in seal_actions:
                    if action.get("action") == "add_elements":
                        for elem in action.get("params", {}).get("elements", []):
                            simulated_elements.append(elem)
                # Count total tag objects in simulated card body
                total_count = _count_tag_objects(simulated_elements)
                _FEISHU_ELEMENT_LIMIT = 200
                _ELEMENT_LIMIT_MARGIN = 5
                threshold = _FEISHU_ELEMENT_LIMIT - _ELEMENT_LIMIT_MARGIN
                if total_count > threshold:
                    _logger.warning(
                        "preservative seal: card element count %d exceeds threshold %d, "
                        "trimming panel children card=%s",
                        total_count, threshold, card_id[:12],
                    )
                    # Trim panel children from the front
                    children: list[dict] = panel.get("elements", [])
                    # Check if a collapse hint already exists
                    hint_idx = None
                    for i, child in enumerate(children):
                        if isinstance(child.get("content"), str) and "已折叠" in child["content"]:
                            hint_idx = i
                            break
                    # If no hint exists yet, we'll need to add one (1 element), so account for it
                    if hint_idx is None:
                        total_count += 1
                    trimmed_count = 0
                    while total_count > threshold and len(children) > 1:
                        # Skip the collapse hint (first child if it contains "已折叠")
                        remove_idx = 1 if children[0].get("content", "").endswith("已折叠") else 0
                        removed = children.pop(remove_idx)
                        total_count -= _count_tag_objects([removed])
                        trimmed_count += 1
                    if trimmed_count > 0:
                        # Update or add collapse hint
                        # Re-find hint_idx (may have shifted due to removals)
                        hint_idx = None
                        for i, child in enumerate(children):
                            if isinstance(child.get("content"), str) and "已折叠" in child["content"]:
                                hint_idx = i
                                break
                        if hint_idx is not None:
                            old_hint = children[hint_idx]["content"]
                            children[hint_idx]["content"] = old_hint.rstrip("已折叠") + f"、{trimmed_count} 项已折叠"
                        else:
                            children.insert(0, {
                                "tag": "markdown",
                                "content": f"⚡ 还有 {trimmed_count} 项已折叠",
                                "text_size": "notation",
                            })
                        # Update panel's elements
                        panel["elements"] = children
                        # Rebuild the panel update action in seal_actions
                        for i, action in enumerate(seal_actions):
                            if (action.get("action") == "partial_update_element"
                                    and action.get("params", {}).get("element_id") == UNIFIED_PANEL_ELEMENT_ID):
                                seal_actions[i]["params"]["partial_element"]["elements"] = children
                                break
                    _logger.info(
                        "preservative seal: after trimming, estimated total %d, trimmed %d items card=%s",
                        total_count, trimmed_count, card_id[:12],
                    )

            # ── batch_update (while still in streaming mode) ──
            # Perform the batch_update BEFORE close_streaming so that
            # content and footer are visible during the streaming→
            # non-streaming transition, avoiding a flash of incomplete
            # content.
            if seal_actions:
                session.sequence += 1
                _logger.debug(
                    "preservative seal: batch_update card=%s seq=%d actions=%d",
                    card_id[:12], session.sequence, len(seal_actions),
                )
                await self._client.cardkit_batch_update(
                    card_id, seal_actions, sequence=session.sequence,
                )

            # ── Step 4: Close streaming mode + update summary ──
            # When closing streaming, we MUST also update the card's summary
            # text.  During streaming, the summary shows "处理中..."; after
            # close_streaming, Feishu displays the summary in the conversation
            # list.  Without updating it, the conversation list would forever
            # show "处理中..." even though the card is completed — the exact
            # bug the user reported.
            #
            # CRITICAL: Only call close_streaming ONCE per card lifecycle.
            # If streaming was already closed (e.g. by a TTL timeout or an
            # earlier seal attempt), skip the close_streaming call — calling
            # it again causes 300317 sequence conflict because the card's
            # server-side sequence has already advanced past our local
            # sequence number.  The _streaming_closed flag ensures we
            # never call close_streaming twice.
            #
            # ── Prepare summary text for conversation list preview ──
            # Feishu documentation: When streaming_mode transitions from
            # true to false, the conversation list preview is atomically
            # updated to config.summary.content.  The summary MUST be
            # included in the close_streaming request itself — a separate
            # cardkit_update_summary call after streaming is closed does
            # NOT reliably update the conversation list preview.
            seal_summary = ""
            if state is not None:
                summary_text = state.answer_text
                if not summary_text and state.reasoning_rounds:
                    summary_text = state.reasoning_rounds[-1].text if state.reasoning_rounds else ""
                if summary_text:
                    seal_summary = summary_text[:120].replace("\n", " ").replace("```", "").strip()

            if not session._streaming_closed:
                session.sequence += 1
                _logger.info(
                    "preservative seal: closing streaming card=%s seq=%d summary=%s",
                    card_id[:12], session.sequence,
                    repr(seal_summary[:40]) if seal_summary else "(empty)",
                )
                # ── Bug fix (v1.0.3): Pass summary IN close_streaming ──
                # Feishu atomically updates the conversation list preview
                # when streaming_mode transitions to false.  The summary
                # must be in THIS request — passing summary="" and then
                # calling cardkit_update_summary separately does NOT work
                # reliably.  See: 飞书开放平台 → 卡片2.0 → 流式更新.
                await self._client.cardkit_close_streaming(
                    card_id, sequence=session.sequence, summary=seal_summary,
                )
                session._streaming_closed = True
            else:
                _logger.info(
                    "preservative seal: streaming already closed, skipping close_streaming card=%s",
                    card_id[:12],
                )
                # ── Fallback: update summary when streaming was already closed ──
                # When Feishu auto-closes streaming (TTL timeout) or a
                # previous flush hit CARDKIT_STREAMING_CLOSED, the summary
                # was never updated from "处理中..." to the actual answer.
                # cardkit_update_summary is used as a belt-and-suspenders
                # for this edge case only.
                if seal_summary:
                    try:
                        session.sequence += 1
                        await self._client.cardkit_update_summary(
                            card_id, seal_summary, sequence=session.sequence,
                        )
                        _logger.info(
                            "preservative seal: summary updated (streaming already closed) "
                            "card=%s seq=%d summary=%s",
                            card_id[:12], session.sequence,
                            repr(seal_summary[:40]),
                        )
                    except FeishuAPIError as e:
                        _logger.warning(
                            "preservative seal: summary update failed (already closed) "
                            "card=%s error=%s",
                            card_id[:12], e,
                        )

            _logger.debug(
                "preservative seal: success card=%s partial=%s",
                card_id[:12], partial,
            )
            return True

        except FeishuAPIError as e:
            if e.code == CARDKIT_SEQUENCE_CONFLICT:
                # ── Sequence conflict retry ──
                # The old logic incorrectly treated 300317 as idempotent
                # success, causing close_streaming and batch_update to
                # silently fail (spinning icon, no footer).
                # We now retry with incremented sequence numbers.
                #
                # In the retry, we replay batch_update first, then
                # close_streaming (if not already closed).  The
                # _streaming_closed flag prevents calling close_streaming
                # twice if it succeeded in the try block before the
                # 300317 occurred on a subsequent operation.
                _logger.warning(
                    "preservative seal: sequence conflict, retrying... card=%s seq=%d",
                    card_id[:12], session.sequence,
                )
                for retry in range(2):
                    try:
                        # Rebuild seal actions — always rebuild panel to
                        # avoid UnboundLocalError if the 300317 occurred
                        # before panel was assigned in the try block.
                        retry_actions: list[dict[str, Any]] = []
                        if state is not None:
                            # ── Bug fix (v1.0.5): Only update panel if it was created ──
                            if session._panel_element_created:
                                all_tool_steps = session.tool_use.build_display_steps()
                                retry_panel = build_unified_panel(
                                    reasoning_rounds=state.reasoning_rounds,
                                    current_reasoning_text="",
                                    tool_steps=all_tool_steps,
                                    tool_elapsed_ms=session.tool_use.elapsed_ms,
                                    show_reasoning=self._cfg.show_reasoning,
                                    expanded=self._cfg.panel_expanded,
                                    panel_events=state.panel_events,
                                    max_tool_steps=self._cfg.max_tool_steps,
                                    max_reasoning_rounds=self._cfg.max_reasoning_rounds,
                                )
                                retry_actions.append({
                                    "action": "partial_update_element",
                                    "params": {
                                        "element_id": UNIFIED_PANEL_ELEMENT_ID,
                                        "partial_element": {
                                            "header": retry_panel["header"],
                                            "elements": retry_panel["elements"],
                                        },
                                    },
                                })
                            # Update answer element with optimized markdown
                            if state.answer_text and session._answer_element_created:
                                optimized_content = _downgrade_tables(optimize_markdown_style(state.answer_text)) or " "
                                retry_actions.append({
                                    "action": "partial_update_element",
                                    "params": {
                                        "element_id": ANSWER_ELEMENT_ID,
                                        "partial_element": {
                                            "content": optimized_content,
                                        },
                                    },
                                })
                        retry_actions.extend(
                            build_preservative_seal_actions(
                                partial=partial,
                                footer_data=footer_data,
                                is_error=is_error,
                                is_aborted=is_aborted,
                                error_message=error_message,
                                footer_fields=footer_fields,
                                footer_show_label=footer_show_label,
                                existing_elements=session.existing_elements,
                            )
                        )
                        # batch_update BEFORE close_streaming (same order as try block)
                        if retry_actions:
                            session.sequence += 1
                            await self._client.cardkit_batch_update(
                                card_id, retry_actions, sequence=session.sequence,
                            )

                        # Close streaming AFTER batch_update
                        if not session._streaming_closed:
                            # Recompute seal_summary for retry (state may have changed)
                            retry_summary = ""
                            if state is not None:
                                summary_text = state.answer_text
                                if not summary_text and state.reasoning_rounds:
                                    summary_text = state.reasoning_rounds[-1].text if state.reasoning_rounds else ""
                                if summary_text:
                                    retry_summary = summary_text[:120].replace("\n", " ").replace("```", "").strip()
                            session.sequence += 1
                            await self._client.cardkit_close_streaming(
                                card_id, sequence=session.sequence, summary=retry_summary,
                            )
                            session._streaming_closed = True

                        _logger.info(
                            "preservative seal: retry %d succeeded card=%s",
                            retry + 1, card_id[:12],
                        )
                        return True
                    except FeishuAPIError as retry_e:
                        if retry_e.code == CARDKIT_SEQUENCE_CONFLICT:
                            _logger.debug(
                                "preservative seal: retry %d still conflict card=%s",
                                retry + 1, card_id[:12],
                            )
                            continue
                        if retry_e.code == CARDKIT_STREAMING_CLOSED:
                            session._streaming_closed = True
                        raise
                # All retries exhausted
                _logger.warning(
                    "preservative seal: retry exhausted after sequence conflicts card=%s",
                    card_id[:12],
                )
                return False
            if e.code == CARDKIT_STREAMING_CLOSED:
                session._streaming_closed = True
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

    # ===================================================================
    # Linear complete
    # ===================================================================

    async def _do_linear_complete(self, session: CardSession) -> bool:
        """Complete the card with the unified panel architecture.

        Strategy:
        1. Wait for any pending flush to finish.
        2. **Drain loop**: Flush any remaining dirty data (answer text, panel
           content) that arrived before or during ``on_completed``.  This is
           critical — the state machine transitions to COMPLETING, but
           on_answer/on_thinking callbacks can still update unified_state
           (COMPLETING is NOT a terminal state).  We must drain ALL content
           before closing streaming.  The loop yields between iterations to
           allow late-arriving callbacks to execute.
        3. Mark flush as completed (no more updates accepted).
        4. Finalize the unified state (close any in-progress reasoning).
        5. Try preservative seal (close streaming + update panel + footer).
        6. If preservative seal fails, fall back to full card rebuild
           (``build_unified_complete_card`` + ``cardkit_update``).

        Returns ``True`` on success, ``False`` on failure.
        """
        if session.guard.should_skip("_do_linear_complete"):
            return False

        # ── Step 1: Wait for any in-progress flush to finish ──
        await session.flush.wait_for_flush()

        # ── Step 2: Drain remaining dirty data (loop with yield) ──
        # After on_completed sets state=COMPLETING, on_answer/on_thinking
        # callbacks can STILL update unified_state (COMPLETING is not in
        # _TERMINAL).  However, _schedule_linear_flush refuses to schedule
        # new flushes during COMPLETING, so the dirty data accumulates
        # without being flushed.  We must drain it ALL here, before
        # closing streaming, or the user sees incomplete content.
        #
        # The loop yields between iterations to allow any late-arriving
        # on_answer callbacks from the agent worker thread to execute
        # and update the state before we check again.  We use a small
        # sleep (20ms) instead of sleep(0) because sleep(0) only yields
        # to the event loop but doesn't give worker threads enough time
        # to deliver their last callbacks — this was the root cause of
        # the "footer appears before content finishes" bug.
        # Maximum 8 drain rounds to prevent infinite loops.
        state = session.unified_state
        _MAX_DRAIN_ROUNDS = 8
        _DRAIN_YIELD_SEC = 0.020  # 20ms yield — enough for worker thread callbacks
        for _drain_round in range(_MAX_DRAIN_ROUNDS):
            if not (
                state is not None
                and session.card_id
                and session._answer_element_created
                and (state.answer_dirty or state.panel_dirty or state.tool_steps_dirty)
            ):
                break  # No dirty data — drain complete

            _logger.info(
                "linear complete: drain round %d/%d "
                "answer_dirty=%s panel_dirty=%s tool_steps_dirty=%s msg=%s",
                _drain_round + 1, _MAX_DRAIN_ROUNDS,
                state.answer_dirty, state.panel_dirty, state.tool_steps_dirty,
                (session.message_id or "?")[:12],
            )
            assert self._client is not None

            # ── Drain panel content ──
            if state.panel_dirty and session._panel_element_created:
                all_tool_steps = session.tool_use.build_display_steps()
                panel = build_unified_panel(
                    reasoning_rounds=state.reasoning_rounds,
                    current_reasoning_text=state.current_reasoning_text,
                    tool_steps=all_tool_steps,
                    tool_elapsed_ms=session.tool_use.elapsed_ms,
                    show_reasoning=self._cfg.show_reasoning,
                    expanded=self._cfg.streaming_panel_expanded,
                    panel_events=state.panel_events,
                    max_tool_steps=self._cfg.max_tool_steps,
                    max_reasoning_rounds=self._cfg.max_reasoning_rounds,
                )
                drain_actions: list[dict[str, Any]] = [{
                    "action": "partial_update_element",
                    "params": {
                        "element_id": UNIFIED_PANEL_ELEMENT_ID,
                        "partial_element": {
                            "header": panel["header"],
                            "elements": panel["elements"],
                        },
                    },
                }]
                try:
                    session.sequence += 1
                    await self._client.cardkit_batch_update(
                        session.card_id, drain_actions, sequence=session.sequence,
                    )
                    state.panel_dirty = False
                    state.tool_steps_dirty = False
                except FeishuAPIError as e:
                    if e.code == CARDKIT_STREAMING_CLOSED:
                        _logger.info("drain: streaming already closed, skipping")
                        session._streaming_closed = True
                    elif is_schema_error(e):
                        _logger.error("drain SCHEMA ERROR: %s — detail: %s", e, e.extract_schema_detail())
                        state.panel_dirty = False
                        state.tool_steps_dirty = False
                    else:
                        _logger.warning("drain panel failed: %s", e)

            # ── Drain answer text ──
            if state.answer_dirty and session._answer_element_created:
                content = state.answer_text or " "
                try:
                    session.sequence += 1
                    _logger.info(
                        "linear complete: draining answer text len=%d msg=%s",
                        len(content), (session.message_id or "?")[:12],
                    )
                    await self._client.cardkit_stream_element(
                        session.card_id, ANSWER_ELEMENT_ID, content,
                        sequence=session.sequence,
                    )
                    state.answer_dirty = False
                except FeishuAPIError as e:
                    if e.code == CARDKIT_STREAMING_CLOSED:
                        _logger.info("drain: streaming already closed, skipping")
                        session._streaming_closed = True
                    else:
                        _logger.warning("drain answer failed: %s", e)

            # ── Yield to allow late-arriving callbacks to execute ──
            # on_answer/on_thinking may be called from the agent worker
            # thread and update unified_state between our check and the
            # next iteration.  A small sleep (20ms) gives the event loop
            # time to process call_soon_threadsafe callbacks from worker
            # threads — sleep(0) is insufficient because it only yields
            # to the event loop's task queue without allowing worker
            # threads to deliver their pending updates.
            if _drain_round < _MAX_DRAIN_ROUNDS - 1:
                await asyncio.sleep(_DRAIN_YIELD_SEC)

        # ── Final drain check: log warning if dirty data remains ──
        # If dirty data persists after all drain rounds, the preservative
        # seal's content completeness guard will flush it before close_streaming,
        # so this is not a content loss — just a performance concern.
        if state is not None and (state.answer_dirty or state.panel_dirty or state.tool_steps_dirty):
            _logger.warning(
                "linear complete: dirty data remains after %d drain rounds "
                "answer_dirty=%s panel_dirty=%s tool_steps_dirty=%s msg=%s — "
                "will be flushed by preservative seal before close_streaming",
                _MAX_DRAIN_ROUNDS,
                state.answer_dirty, state.panel_dirty, state.tool_steps_dirty,
                (session.message_id or "?")[:12],
            )

        # ── Step 3: Mark flush as completed — no more updates accepted ──
        session.flush.mark_completed()

        # ── Wait for card creation to finish ──
        # When on_completed fires before _do_create_linear_card finishes,
        # card_id/card_msg_id are still None.  Wait for the signal.
        try:
            await asyncio.wait_for(session._card_ready.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            _logger.warning("complete: card creation timed out: msg=%s", (session.message_id or "?")[:12])

        if not session.card_id:
            session.state = CREATION_FAILED
            session.enter_terminal(
                reason=TerminalReason.CREATION_FAILED,
                source="_do_linear_complete",
            )
            return False

        # ── Step 4: Finalize state ──
        if state:
            state.finalize()

        # ── Build footer data ──
        footer_data = session.footer
        is_error = session.state in (CREATION_FAILED, TERMINATED)
        is_aborted = getattr(session, "_was_aborted", False) or session.state == ABORTED
        error_message = getattr(session, "error_message", "")

        # ── Step 5: Try preservative seal ──
        seal_ok = await self._preservative_seal(
            session,
            footer_data=footer_data,
            is_error=is_error,
            is_aborted=is_aborted,
            error_message=error_message,
            footer_fields=self._cfg.footer_fields,
            footer_show_label=self._cfg.footer_show_label,
        )

        # ── Summary is already updated in close_streaming ──
        # No separate cardkit_update is needed for summary sync.
        # The summary was passed to cardkit_close_streaming which
        # atomically updates the conversation list preview when
        # streaming_mode transitions to false.  See: 飞书开放平台 →
        # 卡片2.0 → 流式更新 → 完成后关闭流式更新模式.

        if not seal_ok:
            # ── Fallback: full card rebuild ──
            _logger.info(
                "preservative seal failed, falling back to full rebuild: card=%s",
                (session.card_id or "")[:12],
            )
            try:
                # Close streaming first (may already be closed by the failed seal attempt)
                # Also update summary for the conversation list.
                # Use _streaming_closed guard to prevent duplicate close_streaming
                # calls which cause 300317 sequence conflicts.
                if not session._streaming_closed:
                    fallback_summary = ""
                    if state is not None:
                        summary_text = state.answer_text
                        if not summary_text and state.reasoning_rounds:
                            summary_text = state.reasoning_rounds[-1].text if state.reasoning_rounds else ""
                        if summary_text:
                            fallback_summary = summary_text[:120].replace("\n", " ").replace("```", "").strip()
                    session.sequence += 1
                    try:
                        # ── Bug fix (v1.0.3): Pass summary IN close_streaming ──
                        # Feishu atomically updates the conversation list preview
                        # when streaming_mode transitions to false.  The summary
                        # must be in THIS request.  See: 飞书开放平台 → 卡片2.0
                        # → 流式更新 → 完成后关闭流式更新模式.
                        await self._client.cardkit_close_streaming(
                            session.card_id, sequence=session.sequence, summary=fallback_summary,  # type: ignore[union-attr]
                        )
                        session._streaming_closed = True
                    except FeishuAPIError as e:
                        if e.code == CARDKIT_STREAMING_CLOSED:
                            # Streaming already closed — that's fine
                            session._streaming_closed = True
                        else:
                            raise
                else:
                    _logger.info(
                        "fallback: streaming already closed, skipping close_streaming card=%s",
                        (session.card_id or "")[:12],
                    )
                    # ── Update summary when streaming was already closed ──
                    # Belt-and-suspenders for the edge case where Feishu
                    # auto-closed streaming (TTL timeout) before we could
                    # pass the summary in close_streaming.
                    fallback_summary = ""
                    if state is not None:
                        summary_text = state.answer_text
                        if not summary_text and state.reasoning_rounds:
                            summary_text = state.reasoning_rounds[-1].text if state.reasoning_rounds else ""
                        if summary_text:
                            fallback_summary = summary_text[:120].replace("\n", " ").replace("```", "").strip()
                    if fallback_summary:
                        try:
                            session.sequence += 1
                            await self._client.cardkit_update_summary(
                                session.card_id, fallback_summary, sequence=session.sequence,  # type: ignore[union-attr]
                            )
                            _logger.info(
                                "fallback: summary updated (already closed) card=%s seq=%d summary=%s",
                                session.card_id[:12], session.sequence,
                                repr(fallback_summary[:40]),
                            )
                        except FeishuAPIError as e:
                            _logger.warning(
                                "fallback: summary update failed (already closed) card=%s error=%s",
                                session.card_id[:12], e,
                            )

                complete_card = build_unified_complete_card(
                    reasoning_rounds=state.reasoning_rounds if state else [],
                    current_reasoning_text="",
                    tool_steps=session.tool_use.build_display_steps(),
                    tool_elapsed_ms=session.tool_use.elapsed_ms,
                    answer_text=state.answer_text if state else "",
                    show_reasoning=self._cfg.show_reasoning,
                    footer_data=footer_data,
                    is_error=is_error,
                    is_aborted=is_aborted,
                    error_message=error_message,
                    footer_fields=self._cfg.footer_fields,
                    footer_show_label=self._cfg.footer_show_label,
                    panel_expanded=self._cfg.panel_expanded,
                    header_enabled=self._cfg.header_enabled,
                    panel_events=state.panel_events if state else None,
                    max_tool_steps=self._cfg.max_tool_steps,
                    max_reasoning_rounds=self._cfg.max_reasoning_rounds,
                )
                session.sequence += 1
                assert self._client is not None
                await self._client.cardkit_update(session.card_id, complete_card, sequence=session.sequence)
                seal_ok = True
                _logger.info(
                    "full rebuild succeeded: card=%s",
                    session.card_id[:12],
                )
            except Exception:
                _logger.warning(
                    "full rebuild also failed: card=%s",
                    (session.card_id or "")[:12],
                    exc_info=True,
                )
                seal_ok = False

        if seal_ok:
            session.state = COMPLETED
        else:
            session.state = CREATION_FAILED
            session.enter_terminal(
                reason=TerminalReason.CREATION_FAILED,
                source="_do_linear_complete_seal_failed",
            )

        return seal_ok


# ---------------------------------------------------------------------------
# Backward-compatible alias
# ---------------------------------------------------------------------------

LinearControllerMixin = UnifiedControllerMixin

__all__ = [
    "UnifiedControllerMixin",
    "LinearControllerMixin",
]
