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
    CREATING,
    FAILED,
    IDLE,
    STREAMING,
)

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
# throttle interval (50ms) so Feishu's typewriter renders characters
# smoothly one-by-one instead of in bursts.  When panel content is
# also dirty, the normal flush interval is used since panel updates
# are inherently batch operations.
_ANSWER_FAST_STREAM_MS = 0.050


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
        session.state = CREATING
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
            if session.state == CREATING:
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
            session.state = FAILED
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
        if session.state == IDLE or session.state in _TERMINAL or session.state == COMPLETING:
            return
        if session.guard.should_skip("_schedule_linear_flush"):
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
        if session.state in _TERMINAL or session.state == COMPLETING or not session.card_id:
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

        # ── Phase 2: First content — add panel + answer element, delete loading hint ──
        if not session._panel_element_created and (state.panel_visible or state.answer_dirty or state.answer_text):
            all_tool_steps = session.tool_use.build_display_steps()
            panel = build_unified_panel(
                reasoning_rounds=state.reasoning_rounds,
                current_reasoning_text=state.current_reasoning_text,
                tool_steps=all_tool_steps,
                tool_elapsed_ms=session.tool_use.elapsed_ms,
                show_reasoning=self._cfg.show_reasoning,
                expanded=self._cfg.streaming_panel_expanded,
                panel_events=state.panel_events,
            )
            # Add panel + answer element before loading hint
            actions.append({
                "action": "add_elements",
                "params": {
                    "type": "insert_before",
                    "target_element_id": _LOADING_HINT_ELEMENT_ID,
                    "elements": [panel, _streaming_element(element_id=ANSWER_ELEMENT_ID)],
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
                session.sequence += 1
                _logger.info(
                    "unified flush (phase 2 — add panel): msg=%s seq=%d actions=%d",
                    (session.message_id or "?")[:12],
                    session.sequence,
                    len(actions),
                )
                try:
                    await self._client.cardkit_batch_update(
                        session.card_id, actions, sequence=session.sequence,
                    )
                    # Update tracking after success
                    session._panel_element_created = True
                    session._loading_hint_removed = True
                    session.existing_elements.add(UNIFIED_PANEL_ELEMENT_ID)
                    session.existing_elements.add(ANSWER_ELEMENT_ID)
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
                        return
                    if is_schema_error(e):
                        # ── Schema error (300315): permanent, don't retry ──
                        # This typically means an invalid property on a CardKit
                        # element.  Log with full error so the developer can
                        # identify the offending property, then mark panel as
                        # created to prevent infinite retry loops.
                        _logger.error(
                            "unified flush phase 2 SCHEMA ERROR (permanent): %s — "
                            "marking panel as created to prevent retry loop, card=%s",
                            e, session.card_id[:12],
                        )
                        session._panel_element_created = True  # Prevent retry loop
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
            all_tool_steps = session.tool_use.build_display_steps()
            panel = build_unified_panel(
                reasoning_rounds=state.reasoning_rounds,
                current_reasoning_text=state.current_reasoning_text,
                tool_steps=all_tool_steps,
                tool_elapsed_ms=session.tool_use.elapsed_ms,
                show_reasoning=self._cfg.show_reasoning,
                expanded=self._cfg.streaming_panel_expanded,
                panel_events=state.panel_events,
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
            except FeishuAPIError as e:
                if e.code == CARDKIT_STREAMING_CLOSED:
                    _logger.info(
                        "unified flush: streaming closed, will be handled by TTL or seal: card=%s",
                        session.card_id[:12],
                    )
                    return
                if is_schema_error(e):
                    _logger.error(
                        "unified flush phase 3 SCHEMA ERROR (permanent): %s — "
                        "clearing dirty flags to stop retry, card=%s",
                        e, session.card_id[:12],
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
        """
        state = session.unified_state
        if state is None:
            return
        split = split_reasoning_text(text)
        reasoning = split.get("reasoning_text")
        answer = split.get("answer_text")

        if reasoning and self._cfg.show_reasoning:
            state.on_reasoning_delta(reasoning)
        if answer:
            # ── Dedup: skip answer text already delivered via stream_delta_callback ──
            # When streaming is active, answer text arrives incrementally via
            # stream_delta_callback → on_answer → state.on_answer_delta.
            # The interim_assistant_callback also delivers the same text in
            # accumulated form.  Appending it here would cause duplication.
            _has_streamed_answer = bool(state.answer_text)
            if not _has_streamed_answer:
                state.on_answer_delta(answer)
        if (reasoning and self._cfg.show_reasoning) or answer:
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
        """Preservative seal — close streaming + update panel + add footer.

        This is the primary seal mechanism for the unified panel
        architecture.  It:

        1. Closes the streaming session (``cardkit_close_streaming``).
        2. Updates the unified panel to its final non-streaming state
           via ``partial_update_element`` (finalized reasoning, no
           in-progress text).
        3. Adds footer / error panel / deletes loading elements via
           ``build_preservative_seal_actions``.

        Because the card never has more than 4 elements, this almost
        never fails due to element limits.  The only expected failure
        mode is a ``CARDKIT_SEQUENCE_CONFLICT``, which is handled by
        retry.

        Returns ``True`` on success, ``False`` on failure (caller
        should fall back to full card rebuild).
        """
        assert self._client is not None
        card_id = session.card_id
        assert card_id is not None

        try:
            # ── Step 1: Close streaming mode ──
            session.sequence += 1
            _logger.info(
                "preservative seal: closing streaming card=%s seq=%d",
                card_id[:12], session.sequence,
            )
            await self._client.cardkit_close_streaming(card_id, sequence=session.sequence)

            # ── Step 2: Update unified panel to final state (non-streaming) ──
            state = session.unified_state
            seal_actions: list[dict[str, Any]] = []

            if state is not None:
                state.finalize()
                all_tool_steps = session.tool_use.build_display_steps()
                panel = build_unified_panel(
                    reasoning_rounds=state.reasoning_rounds,
                    current_reasoning_text="",
                    tool_steps=all_tool_steps,
                    tool_elapsed_ms=session.tool_use.elapsed_ms,
                    show_reasoning=self._cfg.show_reasoning,
                    expanded=self._cfg.panel_expanded,
                    panel_events=state.panel_events,
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

            # ── Step 2b: Update answer element with optimized markdown ──
            # During streaming, answer text was sent raw (no markdown optimization)
            # for performance. Now that streaming is closed, update the answer
            # element with the fully optimized markdown content.
            if state is not None and state.answer_text:
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

            if seal_actions:
                session.sequence += 1
                _logger.debug(
                    "preservative seal: batch_update card=%s seq=%d actions=%d",
                    card_id[:12], session.sequence, len(seal_actions),
                )
                await self._client.cardkit_batch_update(
                    card_id, seal_actions, sequence=session.sequence,
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
                _logger.warning(
                    "preservative seal: sequence conflict, retrying... card=%s seq=%d",
                    card_id[:12], session.sequence,
                )
                for retry in range(2):
                    try:
                        session.sequence += 1
                        await self._client.cardkit_close_streaming(
                            card_id, sequence=session.sequence,
                        )

                        # Rebuild seal actions
                        retry_actions: list[dict[str, Any]] = []
                        if state is not None:
                            retry_actions.append({
                                "action": "partial_update_element",
                                "params": {
                                    "element_id": UNIFIED_PANEL_ELEMENT_ID,
                                    "partial_element": {
                                        "header": panel["header"],  # type: ignore[possibly-undefined]
                                        "elements": panel["elements"],  # type: ignore[possibly-undefined]
                                    },
                                },
                            })
                            # Update answer element with optimized markdown
                            if state.answer_text:
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
                        if retry_actions:
                            session.sequence += 1
                            await self._client.cardkit_batch_update(
                                card_id, retry_actions, sequence=session.sequence,
                            )
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
                        raise
                # All retries exhausted
                _logger.warning(
                    "preservative seal: retry exhausted after sequence conflicts card=%s",
                    card_id[:12],
                )
                return False
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
        2. Finalize the unified state (close any in-progress reasoning).
        3. Try preservative seal (close streaming + update panel + footer).
        4. If preservative seal fails, fall back to full card rebuild
           (``build_unified_complete_card`` + ``cardkit_update``).

        Returns ``True`` on success, ``False`` on failure.
        """
        if session.guard.should_skip("_do_linear_complete"):
            return False

        await session.flush.wait_for_flush()
        session.flush.mark_completed()

        # ── Wait for card creation to finish ──
        # When on_completed fires before _do_create_linear_card finishes,
        # card_id/card_msg_id are still None.  Wait for the signal.
        try:
            await asyncio.wait_for(session._card_ready.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            _logger.warning("complete: card creation timed out: msg=%s", (session.message_id or "?")[:12])

        if not session.card_id:
            session.state = FAILED
            return False

        # ── Finalize state ──
        state = session.unified_state
        if state:
            state.finalize()

        # ── Build footer data ──
        footer_data = session.footer
        is_error = session.state == FAILED
        is_aborted = getattr(session, "_was_aborted", False) or session.state == ABORTED
        error_message = getattr(session, "error_message", "")

        # ── Try preservative seal ──
        seal_ok = await self._preservative_seal(
            session,
            footer_data=footer_data,
            is_error=is_error,
            is_aborted=is_aborted,
            error_message=error_message,
            footer_fields=self._cfg.footer_fields,
            footer_show_label=self._cfg.footer_show_label,
        )

        if not seal_ok:
            # ── Fallback: full card rebuild ──
            _logger.info(
                "preservative seal failed, falling back to full rebuild: card=%s",
                (session.card_id or "")[:12],
            )
            try:
                # Close streaming first (may already be closed by the failed seal attempt)
                session.sequence += 1
                try:
                    await self._client.cardkit_close_streaming(session.card_id, sequence=session.sequence)  # type: ignore[union-attr]
                except FeishuAPIError as e:
                    if e.code != CARDKIT_STREAMING_CLOSED:
                        raise
                    # Streaming already closed — that's fine

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
            session.state = FAILED

        return seal_ok


# ---------------------------------------------------------------------------
# Backward-compatible alias
# ---------------------------------------------------------------------------

LinearControllerMixin = UnifiedControllerMixin

__all__ = [
    "UnifiedControllerMixin",
    "LinearControllerMixin",
]
