"""Callback wrapping for AIAgent streaming callbacks.

Split from monkey_patch.py — contains:
  - _maybe_wrap_callbacks() and all inner wrapper functions
    (_answer_wrapper, _thinking_wrapper, _tool_wrapper,
     _reasoning_wrapper, _background_review_wrapper)
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

    # ── Guard: skip if stream_delta_callback is already wrapped ──
    # Hermes resets stream_delta_callback per message in _run_agent, so we
    # check the function itself for our wrapper mark rather than a global
    # agent flag. This ensures new messages get freshly wrapped callbacks
    # while preventing double-wrapping within a single run_conversation.
    #
    # EXCEPTION: When a recursive interrupt follow-up occurs, the
    # event_message_id changes but the agent object is reused. We must
    # re-wrap the callbacks with the new eid so that streaming text goes
    # to the new message's card, not the old one.
    _current_stream = getattr(agent, "stream_delta_callback", None)
    _current_interim = getattr(agent, "interim_assistant_callback", None)
    _current_tool = getattr(agent, "tool_progress_callback", None)
    _current_reasoning = getattr(agent, "reasoning_callback", None)
    _current_bg = getattr(agent, "background_review_callback", None)
    _force_rewrap = bool(ctx and ctx.get("_force_rewrap")) if (ctx := _msg_ctx.get()) else False
    _logger.debug(
        "HLS_WRAP: guard check stream=%s(hls=%s) interim=%s tool=%s reasoning=%s bg=%s eid=%s force_rewrap=%s",
        bool(_current_stream),
        getattr(_current_stream, "_hls_wrapper", False) if _current_stream else "N/A",
        bool(_current_interim),
        bool(_current_tool),
        bool(_current_reasoning),
        bool(_current_bg),
        eid[:12] if eid else "?",
        _force_rewrap,
    )
    if _current_stream and getattr(_current_stream, "_hls_wrapper", False) and not _force_rewrap:
        _logger.debug("HLS_WRAP: guard SKIP — stream_delta already wrapped")
        return

    # ── ANSWER: wrap stream_delta_callback ──
    # Track the last consumed text hash for dedup with interim_assistant_callback.
    _stream_consumed_texts: dict[str, str] = {}

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
                    # Record consumed text for dedup with interim_assistant_callback
                    _stream_consumed_texts[eid] = text
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
    # Dedup: skip if the text was already consumed by stream_delta_callback,
    # which happens when Hermes processes the same text through both callbacks.
    if getattr(agent, "interim_assistant_callback", None):
        _orig_interim = agent.interim_assistant_callback

        def _thinking_wrapper(text, *args, **kwargs):
            try:
                # Dedup: skip if stream_delta_callback already consumed this text
                last_consumed = _stream_consumed_texts.get(eid, "")
                if text and text != last_consumed:
                    from .hooks import on_thinking_delta
                    consumed = on_thinking_delta(message_id=eid, text=text)
                    if consumed:
                        # Card consumed the text — skip original callback to
                        # avoid duplicate plain-text delivery via _stream_consumer.
                        # Safe because: when the card is active, _stream_consumer's
                        # _accumulated is empty (answer_wrapper already intercepted
                        # the stream deltas), so on_segment_break() would be a no-op.
                        _logger.debug(
                            "thinking_wrapper: consumed text len=%d eid=%s",
                            len(text), eid[:12],
                        )
                        return
                elif text and text == last_consumed:
                    # Text already consumed by stream_delta_callback (card shown),
                    # skip original callback to avoid duplicate plain-text send.
                    _logger.debug(
                        "thinking_wrapper: dedup skip (stream already consumed) eid=%s",
                        eid[:12],
                    )
                    return
            except Exception:
                _logger.debug("thinking_wrapper: exception", exc_info=True)
            # Card didn't consume (disabled / not Feishu / error) — call original
            # so Hermes internal state (e.g. on_segment_break for already_streamed)
            # stays consistent and plain-text fallback works.
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

    def _reasoning_wrapper(text, *args, **kwargs):
        try:
            from .hooks import on_reasoning_delta

            if text:
                on_reasoning_delta(message_id=eid, text=text)
        except Exception:
            pass
        if _orig_reasoning:
            return _orig_reasoning(text, *args, **kwargs)

    agent.reasoning_callback = _reasoning_wrapper

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
