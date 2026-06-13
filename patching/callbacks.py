"""Callback wrapping for AIAgent streaming callbacks.

Split from monkey_patch.py — contains:
  - _maybe_wrap_callbacks() and all inner wrapper functions
    (_answer_wrapper, _thinking_wrapper, _tool_wrapper,
     _reasoning_wrapper, _background_review_wrapper)

Dedup architecture (v1.0.3):
  Hermes delivers the same text through TWO callbacks:
    1. stream_delta_callback  — incremental deltas during streaming
    2. interim_assistant_callback — full accumulated text after the
       model response completes, with already_streamed=True/False

  The dedup mechanism uses:
    _stream_consumed_len[eid] — total length of text consumed by
      stream_delta_callback for this eid.  When interim_assistant_callback
      arrives, if its text length <= _stream_consumed_len, the text was
      fully streamed and we skip it (pass through to original callback
      for segment break / state management).

  When already_streamed=True (Hermes tells us the text was already
  delivered via stream_delta_callback), we skip on_thinking_delta
  entirely and just call the original callback (so Hermes's internal
  _stream_consumer.on_segment_break() fires correctly).
"""

from __future__ import annotations

from typing import Any

from . import (
    _msg_ctx,
    _thread_local_ctx,
    _logger,
    _get_event_message_id,
)


def _maybe_wrap_callbacks(agent) -> None:
    """Replace streaming callbacks on *agent* with wrappers that also fire
    Feishu CardKit updates.  Skips silently when outside a Feishu message
    context (i.e. no event_message_id in context)."""
    _logger.debug("HLS_CALLED: _maybe_wrap_callbacks invoked, has_stream=%s, eid_lookup=%s", bool(getattr(agent, "stream_delta_callback", None)), bool(_get_event_message_id()))

    eid = _get_event_message_id()
    if not eid:
        _logger.debug("HLS_CALLED: skip — no event_message_id in ctx")
        return  # Not in a hermes-lark-streaming context — skip

    _logger.debug(
        "_maybe_wrap_callbacks: eid=%s has_stream_delta=%s has_interim=%s has_tool=%s has_reasoning=%s has_bg=%s",
        eid[:12] if eid else "?",
        bool(getattr(agent, "stream_delta_callback", None)),
        bool(getattr(agent, "interim_assistant_callback", None)),
        bool(getattr(agent, "tool_progress_callback", None)),
        bool(getattr(agent, "reasoning_callback", None)),
        bool(getattr(agent, "background_review_callback", None)),
    )

    # ── Guard: skip if ANY callback is already wrapped ──
    # Bug fix (v1.0.3): The old guard only checked stream_delta_callback.
    # When stream_delta_callback is None (e.g. DeepSeek models that use
    # interim_assistant_callback but not stream_delta_callback), the guard
    # never triggered, causing interim_assistant_callback to be wrapped
    # TWICE — each invocation processed the same text twice, producing
    # doubled content in the collapsible panel ("TheThe user user is is
    # saying saying...").
    #
    # Now we check BOTH stream_delta_callback AND interim_assistant_callback
    # for the _hls_wrapper mark. If either is already wrapped (and we are
    # not forcing a re-wrap for interrupt follow-ups), we skip.
    _current_stream = getattr(agent, "stream_delta_callback", None)
    _current_interim = getattr(agent, "interim_assistant_callback", None)
    _current_tool = getattr(agent, "tool_progress_callback", None)
    _current_reasoning = getattr(agent, "reasoning_callback", None)
    _current_bg = getattr(agent, "background_review_callback", None)
    _force_rewrap = bool(ctx and ctx.get("_force_rewrap")) if (ctx := _msg_ctx.get()) else False
    _logger.warning(
        "HLS_DIAG: _maybe_wrap_callbacks GUARD CHECK eid=%s "
        "stream=%s(hls=%s) interim=%s(hls=%s) tool=%s "
        "reasoning=%s(hls=%s type=%s repr=%s) bg=%s force_rewrap=%s",
        eid[:12] if eid else "?",
        bool(_current_stream),
        getattr(_current_stream, "_hls_wrapper", False) if _current_stream else "N/A",
        bool(_current_interim),
        getattr(_current_interim, "_hls_wrapper", False) if _current_interim else "N/A",
        bool(_current_tool),
        bool(_current_reasoning),
        getattr(_current_reasoning, "_hls_wrapper", False) if _current_reasoning else "N/A",
        type(_current_reasoning).__name__ if _current_reasoning else "None",
        repr(_current_reasoning)[:80] if _current_reasoning else "None",
        bool(_current_bg),
        _force_rewrap,
    )
    _any_wrapped = (
        (_current_stream and getattr(_current_stream, "_hls_wrapper", False))
        or (_current_interim and getattr(_current_interim, "_hls_wrapper", False))
    )
    if _any_wrapped and not _force_rewrap:
        _logger.debug("HLS_WRAP: guard SKIP — callbacks already wrapped (stream=%s interim=%s) eid=%s",
            bool(_current_stream and getattr(_current_stream, "_hls_wrapper", False)),
            bool(_current_interim and getattr(_current_interim, "_hls_wrapper", False)),
            eid[:12] if eid else "?",
        )
        # ── Late-arriving reasoning_callback fix ──
        # When _maybe_wrap_callbacks is called a second time (Hermes
        # sometimes sets reasoning_callback AFTER the first call), the
        # guard SKIP prevents ALL wrapping — including reasoning_callback.
        # Without wrapping, on_reasoning is never called, so
        # _native_reasoning_active stays False and the dedup guard in
        # _linear_on_thinking doesn't trigger, causing reasoning text
        # duplication in the collapsible panel.
        #
        # Fix: Even when the guard SKIP fires, check if reasoning_callback
        # exists and is NOT yet wrapped. If so, wrap it separately.
        _current_reasoning_now = getattr(agent, "reasoning_callback", None)
        if _current_reasoning_now and not getattr(_current_reasoning_now, "_hls_wrapper", False):
            _orig_reasoning_late = _current_reasoning_now

            _logger.warning(
                "HLS_DIAG: late_reasoning_wrapper SETUP eid=%s "
                "_orig_reasoning_late=%s late_type=%s late_has_hls=%s late_repr=%s",
                eid[:12] if eid else "?",
                bool(_orig_reasoning_late),
                type(_orig_reasoning_late).__name__ if _orig_reasoning_late else "None",
                getattr(_orig_reasoning_late, "_hls_wrapper", False) if _orig_reasoning_late else "N/A",
                repr(_orig_reasoning_late)[:120] if _orig_reasoning_late else "None",
            )

            def _late_reasoning_wrapper(text, *args, **kwargs):
                _logger.warning(
                    "HLS_DIAG: late_reasoning_wrapper CALLED eid=%s text=%r "
                    "_orig_late=%s late_type=%s late_has_hls=%s",
                    eid[:12] if eid else "?",
                    text[:50] if text else "",
                    bool(_orig_reasoning_late),
                    type(_orig_reasoning_late).__name__ if _orig_reasoning_late else "None",
                    getattr(_orig_reasoning_late, "_hls_wrapper", False) if _orig_reasoning_late else "N/A",
                )
                try:
                    from .hooks import on_reasoning_delta
                    if text:
                        on_reasoning_delta(message_id=eid, text=text)
                except Exception:
                    pass
                # FIX: If _orig_reasoning_late is already an HLS wrapper, skip it
                _orig_late_is_hls = getattr(_orig_reasoning_late, "_hls_wrapper", False)
                if _orig_late_is_hls:
                    _logger.debug(
                        "HLS_FIX: late_reasoning_wrapper skips _orig_late (already HLS-wrapped) eid=%s",
                        eid[:12] if eid else "?",
                    )
                else:
                    return _orig_reasoning_late(text, *args, **kwargs)

            agent.reasoning_callback = _late_reasoning_wrapper
            setattr(agent.reasoning_callback, "_hls_wrapper", True)
            _logger.info(
                "HLS_WRAP: late-wrapped reasoning_callback eid=%s",
                eid[:12] if eid else "?",
            )
        return

    # ── ANSWER: wrap stream_delta_callback ──
    # Track total consumed text LENGTH for dedup with interim_assistant_callback.
    # We use length instead of exact text match because:
    #   - stream_delta_callback delivers incremental deltas: "The ", "user ", ...
    #   - interim_assistant_callback delivers accumulated text: "The user keeps asking"
    #   - Exact match on last chunk fails; length-based check is robust.
    _stream_consumed_len: dict[str, int] = {}

    if getattr(agent, "stream_delta_callback", None):
        _orig_stream = agent.stream_delta_callback

        def _answer_wrapper(text, *args, **kwargs):
            try:
                from .hooks import on_answer_delta

                if text and on_answer_delta(message_id=eid, text=text):
                    _logger.debug(
                        "answer_wrapper: consumed text len=%d eid=%s",
                        len(text), eid[:12],
                    )
                    # Record total consumed length for dedup with interim_assistant_callback
                    _stream_consumed_len[eid] = _stream_consumed_len.get(eid, 0) + len(text)
                    return
                else:
                    _logger.debug(
                        "answer_wrapper: passed through (text=%r) eid=%s",
                        bool(text), eid[:12],
                    )
            except Exception:
                _logger.debug("answer_wrapper: exception", exc_info=True)
            return _orig_stream(text, *args, **kwargs)

        agent.stream_delta_callback = _answer_wrapper
        _logger.debug("_maybe_wrap_callbacks: stream_delta_callback wrapped")
    else:
        _logger.debug("_maybe_wrap_callbacks: NO stream_delta_callback on agent")

    # ── THINKING: wrap interim_assistant_callback ──
    # Routes interim content (status messages, thinking text) to the card.
    # When the card consumes the text, skip the original callback to prevent
    # duplicate messages (card + plain text) on Feishu.
    #
    # Dedup strategy:
    #   1. If already_streamed=True (Hermes tells us the text was already
    #      delivered via stream_delta_callback), skip on_thinking_delta
    #      entirely and pass through to _orig_interim for segment break.
    #   2. If the text length <= total consumed by stream_delta_callback,
    #      the text was fully streamed — skip on_thinking_delta.
    #   3. Otherwise, process through on_thinking_delta for the card.
    if getattr(agent, "interim_assistant_callback", None):
        _orig_interim = agent.interim_assistant_callback

        def _thinking_wrapper(text, *args, **kwargs):
            try:
                # ── Check already_streamed kwarg from Hermes ──
                already_streamed = kwargs.get("already_streamed", False)
                if already_streamed:
                    _logger.warning(
                        "HLS_DIAG: thinking_wrapper SKIP(already_streamed) eid=%s len=%d",
                        eid[:12], len(text) if text else 0,
                    )
                    return _orig_interim(text, *args, **kwargs)

                # ── Length-based dedup ──
                consumed_len = _stream_consumed_len.get(eid, 0)
                if text and consumed_len > 0 and len(text) <= consumed_len:
                    _logger.warning(
                        "HLS_DIAG: thinking_wrapper SKIP(length_dedup) eid=%s "
                        "consumed=%d interim=%d",
                        eid[:12], consumed_len, len(text),
                    )
                    return _orig_interim(text, *args, **kwargs)

                if text:
                    from .hooks import on_thinking_delta
                    _logger.warning(
                        "HLS_DIAG: thinking_wrapper CALLING on_thinking_delta eid=%s "
                        "len=%d text_head=%r consumed_len=%d",
                        eid[:12], len(text), text[:60], consumed_len,
                    )
                    consumed = on_thinking_delta(message_id=eid, text=text)
                    if consumed:
                        _logger.debug(
                            "thinking_wrapper: consumed text len=%d eid=%s",
                            len(text), eid[:12],
                        )
                        return
            except Exception:
                _logger.debug("thinking_wrapper: exception", exc_info=True)
            return _orig_interim(text, *args, **kwargs)

        agent.interim_assistant_callback = _thinking_wrapper
        setattr(agent.interim_assistant_callback, "_hls_wrapper", True)
        _logger.debug("_maybe_wrap_callbacks: interim_assistant_callback wrapped")
    else:
        _logger.debug("_maybe_wrap_callbacks: NO interim_assistant_callback on agent")

    # ── TOOL: wrap tool_progress_callback ──
    if getattr(agent, "tool_progress_callback", None):
        _orig_tool = agent.tool_progress_callback

        def _tool_wrapper(event_type, tool_name=None, preview=None, *args, **kwargs):
            try:
                from .hooks import on_tool_updated

                if event_type in ("tool.started", "tool.completed"):
                    if on_tool_updated(
                        message_id=eid,
                        tool_name=tool_name or "",
                        status="started" if event_type == "tool.started" else "completed",
                        detail=preview or "",
                    ):
                        return
            except Exception:
                pass
            return _orig_tool(event_type, tool_name, preview, *args, **kwargs)

        agent.tool_progress_callback = _tool_wrapper

    # Mark wrapper functions so guard can detect them next time
    if getattr(agent, "stream_delta_callback", None):
        setattr(agent.stream_delta_callback, "_hls_wrapper", True)
    # interim_assistant_callback is already marked above (in its wrapper block)
    if getattr(agent, "tool_progress_callback", None):
        setattr(agent.tool_progress_callback, "_hls_wrapper", True)
    if getattr(agent, "reasoning_callback", None):
        setattr(agent.reasoning_callback, "_hls_wrapper", True)
    if getattr(agent, "background_review_callback", None):
        setattr(agent.background_review_callback, "_hls_wrapper", True)

    # ── REASONING: set reasoning_callback ──
    _orig_reasoning = getattr(agent, "reasoning_callback", None)
    _logger.warning(
        "HLS_DIAG: reasoning_wrapper SETUP eid=%s _orig_reasoning=%s "
        "orig_type=%s orig_has_hls=%s orig_repr=%s",
        eid[:12] if eid else "?",
        bool(_orig_reasoning),
        type(_orig_reasoning).__name__ if _orig_reasoning else "None",
        getattr(_orig_reasoning, "_hls_wrapper", False) if _orig_reasoning else "N/A",
        repr(_orig_reasoning)[:120] if _orig_reasoning else "None",
    )

    def _reasoning_wrapper(text, *args, **kwargs):
        _logger.warning(
            "HLS_DIAG: reasoning_wrapper CALLED eid=%s text=%r "
            "_orig_reasoning=%s orig_type=%s orig_has_hls=%s",
            eid[:12] if eid else "?",
            text[:50] if text else "",
            bool(_orig_reasoning),
            type(_orig_reasoning).__name__ if _orig_reasoning else "None",
            getattr(_orig_reasoning, "_hls_wrapper", False) if _orig_reasoning else "N/A",
        )
        try:
            from .hooks import on_reasoning_delta

            if text:
                on_reasoning_delta(message_id=eid, text=text)
        except Exception:
            pass
        if _orig_reasoning:
            # FIX: If _orig_reasoning is already an HLS wrapper (agent reuse scenario),
            # skip calling it — it would call on_reasoning_delta again with the OLD
            # message_id, causing duplicate reasoning text in the collapsible panel.
            _orig_is_hls = getattr(_orig_reasoning, "_hls_wrapper", False)
            if _orig_is_hls:
                _logger.debug(
                    "HLS_FIX: _reasoning_wrapper skips _orig_reasoning (already HLS-wrapped) eid=%s",
                    eid[:12] if eid else "?",
                )
            else:
                return _orig_reasoning(text, *args, **kwargs)

    agent.reasoning_callback = _reasoning_wrapper
    # BUG FIX: Mark _reasoning_wrapper with _hls_wrapper AFTER setting it.
    # The old code marked reasoning_callback at lines 285-286 BEFORE
    # _reasoning_wrapper was created (lines 290-326), so the wrapper
    # was never marked. When _maybe_wrap_callbacks is called a second
    # time (from the module-level conversation_loop patch), the
    # late-arriving fix sees that reasoning_callback lacks _hls_wrapper
    # and wraps it AGAIN with _late_reasoning_wrapper. This creates a
    # chain: _late_reasoning_wrapper → _reasoning_wrapper, where both
    # call on_reasoning_delta(), causing every token to appear twice
    # in the collapsible panel ("TheThe user user wants wants...").
    setattr(agent.reasoning_callback, "_hls_wrapper", True)

    # ── BACKGROUND_REVIEW: wrap background_review_callback ──
    if getattr(agent, "background_review_callback", None):
        _orig_bg = agent.background_review_callback

        def _bg_wrapper(message, *args, **kwargs):
            try:
                from .hooks import on_background_review_message

                deferred = on_background_review_message(
                    message_id=eid,
                    text=message,
                    sender=_orig_bg,
                )
                if deferred:
                    return
            except Exception:
                pass
            return _orig_bg(message, *args, **kwargs)

        agent.background_review_callback = _bg_wrapper

    # ── Store agent reference for cache token extraction (Feature 2-c) ──
    ctx = _msg_ctx.get()
    if ctx is not None:
        ctx["_agent_ref"] = agent
        # Clear _force_rewrap flag after callbacks have been re-wrapped
        ctx.pop("_force_rewrap", None)
        _thread_local_ctx.data = dict(ctx)
