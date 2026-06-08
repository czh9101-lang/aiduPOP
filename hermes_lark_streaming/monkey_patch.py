"""Runtime monkey patching — replaces AST source injection at import time.

Strategy
────────
Instead of modifying ``gateway/run.py`` on disk (AST patching), we apply
runtime patches by wrapping methods on ``GatewayRunner`` and ``AIAgent``
when the plugin loads.

    GatewayRunner._handle_message           → NORMALIZE (before original)
    GatewayRunner._handle_message_with_agent → START (before) + ABORT/INTERRUPT (after)
    GatewayRunner._run_agent                 → event_message_id injection + COMPLETE (after)
    AIAgent.run_conversation                 → wraps all 6 callbacks (ANSWER, THINKING,
                                                TOOL, REASONING, BACKGROUND_REVIEW)
    cron.scheduler._deliver_result           → redirect cron Feishu deliveries to CardKit
    FeishuAdapter.send                       → intercept ALL text → convert to cards
    FeishuAdapter.edit_message               → update gateway card content (Phase 2)
    FeishuAdapter.add_reaction               → card status indicator (Phase 3)
    FeishuAdapter.delete_reaction            → card status clear (Phase 3)
    FeishuAdapter.send_clarify               → interactive clarify card (dropdown + input)
    FeishuAdapter._on_card_action_trigger    → clarify card callback handler

Message context (``message_id``, ``event_message_id``, ``chat_id``, …) is
propagated through a ``contextvars.ContextVar`` — safe within a single async
task execution context.
"""

from __future__ import annotations

import contextvars
import functools
import importlib
import importlib.util
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

from . import __version__


# Thread-local storage for context propagation into worker threads
_thread_local_ctx = threading.local()
_thread_local_ctx.data = None

_logger = logging.getLogger("hermes_lark_streaming")

# ── Module-level Config singleton for inject_time ──────────────────
# Reused across calls so we don't create a new Config() per message.
# inject_time uses _reload() (disk re-read) anyway, so a singleton gives
# the same freshness guarantee without redundant object creation.
_config = None


def _get_config():
    global _config
    if _config is None:
        from .config import Config
        _config = Config()
    return _config


# ── Context propagation ────────────────────────────────────────────
# Set in _wrap_run_agent (from event_message_id param), read by callback
# wrappers in _maybe_wrap_callbacks.

_msg_ctx: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "hermes_lark_streaming_msg_ctx", default=None
)

# Track message starts for interrupt detection.
# When _handle_message_with_agent is called for a new message while
# an old call is still in-flight, the old call's None return indicates
# the old session was interrupted (not just aborted).
_started_msg_ids: set[str] = set()
_started_msg_ids_lock = threading.Lock()

# ── Gateway card registry (Phase 2: edit_message support) ────────────
# Maps card_msg_id → {"chat_id": str, "card_id": str|None, "category": str}
# Used by _wrap_feishu_adapter_edit to update cards created by
# _wrap_feishu_adapter_send instead of trying to edit plain text.
_gateway_cards: dict[str, dict[str, Any]] = {}
_gateway_cards_lock = threading.Lock()

# ── GatewayRunner delayed-patch guard ────────────────────────────────
# Set to True once _apply_gateway_runner_patches() succeeds (either
# immediately or from the delayed-poll thread).  Prevents double-patching.
_gw_runner_patched: bool = False


def _get_event_message_id() -> str | None:
    ctx = _msg_ctx.get()
    if ctx is None:
        ctx = _get_thread_local_ctx()
    if ctx is None:
        return None
    return ctx.get("event_message_id")


def _get_thread_local_ctx() -> dict | None:
    return getattr(_thread_local_ctx, "data", None)


# ── GatewayRunner method wrappers ──────────────────────────────────


def _wrap_handle_message(orig: Callable) -> Callable:
    """Inject NORMALIZE hook at the top of GatewayRunner._handle_message."""

    @functools.wraps(orig)
    async def wrapper(self, event, *args, **kwargs):
        # NORMALIZE hook — fires before any message processing
        try:
            from .patch import on_feishu_normalize

            on_feishu_normalize(
                message_id=event.message_id,
                source=event.source,
                event=event,
                reply_anchor_id=self._reply_anchor_for_event(event),
            )
        except Exception:
            pass
        return await orig(self, event, *args, **kwargs)

    return wrapper


def _wrap_handle_message_with_agent(orig: Callable) -> Callable:
    """Inject START hook at entry and ABORT/INTERRUPT detection on return."""

    @functools.wraps(orig)
    async def wrapper(self, event, source, *args, **kwargs):
        mid = event.message_id
        anchor_id = self._reply_anchor_for_event(event)
        chat_id = source.chat_id if hasattr(source, "chat_id") else ""

        # Track this message as started (for interrupt detection)
        with _started_msg_ids_lock:
            _started_msg_ids.add(mid)

        # ── START hook ──
        try:
            from .patch import on_message_started

            on_message_started(
                message_id=mid,
                chat_id=chat_id,
                anchor_id=anchor_id,
            )
        except Exception:
            pass

        # Seed message context for downstream hooks
        # Use a dedicated dict per message to prevent context leakage
        # between concurrent/overlapping messages.
        msg_context = {
            "message_id": mid,
            "chat_id": chat_id,
            "anchor_id": anchor_id,
            "event_message_id": "",  # filled by _wrap_run_agent
            "card_sent": False,
            "_msg_start_time": time.monotonic(),  # 自计时：替代无法获取的 _response_time 局部变量
        }
        _msg_ctx.set(msg_context)

        result = await orig(self, event, source, *args, **kwargs)

        # ── Use the per-message context dict instead of _msg_ctx ──
        # When a new message interrupts the old one, _msg_ctx may already
        # point to the new message's context. We must use the original
        # per-message dict captured at entry to correctly detect
        # card_sent and interrupt states.
        ctx = msg_context

        # ── CARD ALREADY SENT → suppress Hermes reply ──
        # Runtime wrapping cannot modify gateway internals like the old
        # AST injection, so we return None to simulate "stale agent result",
        # causing Hermes to skip the text reply.
        if result is not None:
            if ctx and ctx.get("card_sent"):
                _logger.info(
                    "card already sent for msg=%s, suppressing gateway reply",
                    mid[:12],
                )
                with _started_msg_ids_lock:
                    _started_msg_ids.discard(mid)
                return None
            # Also check if a card session exists (even in terminal state).
            # This catches cases where card_sent wasn't propagated correctly
            # (e.g., interrupt scenarios with complex context chains).
            try:
                from .controller import get_controller
                _ctrl = get_controller()
                if _ctrl and _ctrl.enabled:
                    _eid = ctx.get("event_message_id", "") if ctx else ""
                    if _eid:
                        _sess = _ctrl._sessions.get(_eid)
                        if _sess and _sess.card_msg_id:
                            _logger.info(
                                "card session exists for msg=%s (state=%s), suppressing gateway reply",
                                mid[:12], _sess.state,
                            )
                            ctx["card_sent"] = True
                            with _started_msg_ids_lock:
                                _started_msg_ids.discard(mid)
                            return None
            except Exception:
                pass

        # ── ABORT / INTERRUPT detection ──
        # When card was already sent, _handle_message_with_agent returns
        # None (the "Discarding stale agent result" path).
        # Use the per-message context (not _msg_ctx) because the global
        # context may have been overwritten by a newer message.
        if result is None:
            if ctx and ctx.get("card_sent"):
                # Card was sent successfully via on_message_completed.
                # Only fire interrupt if a newer message started after this one.
                with _started_msg_ids_lock:
                    others = _started_msg_ids - {mid}
                if others:
                    try:
                        from .patch import on_message_interrupted

                        new_mid = next(iter(others))
                        on_message_interrupted(
                            message_id=mid,
                            new_message_id=new_mid,
                            chat_id=chat_id,
                            anchor_id=anchor_id,
                        )
                    except Exception:
                        pass
                # else: card completed normally, Hermes returned None
                #       to suppress text reply — NOT an abort.
            else:
                # Card was never sent — real abort (error, reset, etc.)
                try:
                    from .patch import on_message_aborted

                    on_message_aborted(message_id=mid)
                except Exception:
                    pass

        # Cleanup tracking
        with _started_msg_ids_lock:
            _started_msg_ids.discard(mid)

        # ── Clear message context to prevent stale leakage ──
        # After this message is fully processed, the context must be
        # cleared so that subsequent non-agent messages (gateway-internal
        # messages like /status, /help, errors, etc.) sent through
        # FeishuAdapter.send() are NOT incorrectly routed to the "Agent
        # path" where they would be silently suppressed.
        #
        # Bug: Without this cleanup, _msg_ctx retains the old event_message_id
        # and card_sent=True, causing the next FeishuAdapter.send() call
        # to enter the agent suppression path and drop the message.
        _msg_ctx.set(None)
        _thread_local_ctx.data = None

        return result

    return wrapper


def _wrap_run_agent(orig: Callable) -> Callable:
    """Inject COMPLETE hook after agent runs; propagate event_message_id."""

    @functools.wraps(orig)
    async def wrapper(
        self,
        message,
        context_prompt,
        history,
        source,
        session_id,
        session_key=None,
        run_generation=None,
        _interrupt_depth=0,
        event_message_id=None,
        channel_prompt=None,
        **kwargs,
    ):
        # Store event_message_id so callback wrappers can consume it
        # When Hermes recursively calls _run_agent for an interrupt follow-up
        # (_interrupt_depth > 0), the event_message_id changes to the new
        # message's ID. We must create a fresh context for the recursive call
        # instead of mutating the parent message's context dict, because:
        # 1. The parent's COMPLETE hook (after orig() returns) still needs
        #    the original message_id and card_sent state.
        # 2. The recursive call's COMPLETE hook needs the new message_id.
        _saved_parent_ctx = None  # Will hold parent context for restoration
        _original_msg_context_ref = None  # Reference to the original msg_context dict
        ctx = _msg_ctx.get()
        if ctx is not None and event_message_id:
            if _interrupt_depth > 0 and ctx.get("event_message_id") != event_message_id:
                # Recursive interrupt follow-up: save parent context, create new context
                #
                # BUG FIX (v0.15.4): We must keep a reference to the original
                # msg_context dict (from _wrap_handle_message_with_agent) so
                # that when we set card_sent=True on the parent context, the
                # _wrap_handle_message_with_agent wrapper can also see it.
                # Without this, _saved_parent_ctx is a *copy* of the original
                # dict, and the original msg_context.card_sent stays False,
                # causing Hermes to send a duplicate plain text reply.
                _original_msg_context_ref = ctx.get("_original_msg_context_ref") or ctx
                _saved_parent_ctx = dict(ctx)  # Save a copy for restoration after orig()
                _logger.debug(
                    "run_agent: recursive interrupt follow-up, creating new context "
                    "for msg=%s (parent msg=%s, depth=%d)",
                    event_message_id[:12] if event_message_id else "?",
                    (ctx.get("message_id") or "?")[:12],
                    _interrupt_depth,
                )
                ctx = {
                    "message_id": event_message_id,
                    "chat_id": ctx.get("chat_id", ""),
                    "anchor_id": ctx.get("anchor_id"),
                    "event_message_id": event_message_id,
                    "card_sent": False,
                    "_msg_start_time": time.monotonic(),
                    "_agent_ref": None,
                    "_interrupt_depth": _interrupt_depth,
                    "_parent_message_id": ctx.get("message_id"),  # Track parent for cleanup
                    "_force_rewrap": True,  # Signal _maybe_wrap_callbacks to re-wrap
                    "_original_msg_context_ref": _original_msg_context_ref,  # Propagate ref to original
                }
                _msg_ctx.set(ctx)
                _thread_local_ctx.data = dict(ctx)

                # ── Fire INTERRUPT hook for the parent message immediately ──
                # This ensures the old card is marked as ABORTED before the
                # child starts processing, so the old card shows "Interrupted"
                # state instead of staying in streaming/marquee animation.
                try:
                    from .patch import on_message_interrupted
                    on_message_interrupted(
                        message_id=_saved_parent_ctx.get("message_id", ""),
                        new_message_id=event_message_id,
                        chat_id=ctx["chat_id"],
                        anchor_id=ctx.get("anchor_id"),
                    )
                except Exception:
                    _logger.debug("run_agent: interrupt hook failed", exc_info=True)

                # Fire START hook for the new (interrupted-into) message
                try:
                    from .patch import on_message_started
                    on_message_started(
                        message_id=event_message_id,
                        chat_id=ctx["chat_id"],
                        anchor_id=ctx.get("anchor_id"),
                    )
                except Exception:
                    pass
            else:
                ctx["event_message_id"] = event_message_id
            # Copy to thread-local for thread-pool workers
            _thread_local_ctx.data = dict(ctx)

        result = await orig(
            self,
            message,
            context_prompt,
            history,
            source,
            session_id,
            session_key=session_key,
            run_generation=run_generation,
            _interrupt_depth=_interrupt_depth,
            event_message_id=event_message_id,
            channel_prompt=channel_prompt,
            **kwargs,
        )

        # ── COMPLETE hook ──
        # After orig() returns, we need to fire the COMPLETE hooks for
        # the appropriate message(s).
        #
        # When _saved_parent_ctx is not None, we're in a recursive
        # interrupt follow-up: the inner _run_agent(B) has just returned.
        # We must fire B's COMPLETE hook first (with B's result), then
        # fire A's ABORTED COMPLETE (parent was interrupted).
        #
        # Previous bug: only A's ABORTED COMPLETE was fired, leaving
        # B's card stuck in STREAMING state forever, causing:
        # - B's card shows "已停止" (no completion update)
        # - Duplicate gateway card when Hermes sends B's result via
        #   adapter.send() (not intercepted because context was cleared)
        # - B's card quotes A's text (stale session content)
        ctx = _msg_ctx.get()
        if _saved_parent_ctx is not None:
            # ── Step 1: Fire B's (child) COMPLETE hook normally ──
            # B's context is still in _msg_ctx at this point.
            # We use B's result (the inner _run_agent's return value)
            # to complete B's card properly.
            if ctx is not None:
                try:
                    from .patch import on_message_completed

                    _elapsed_child = time.monotonic() - ctx.get("_msg_start_time", time.monotonic())
                    is_interrupted_child = result.get("interrupted", False) or result.get("partial", False)

                    _finish_reason_child = result.get("finish_reason", "")
                    _error_msg_child = result.get("error") or result.get("interrupt_message", "")
                    if _finish_reason_child and _finish_reason_child != "stop":
                        _logger.warning(
                            "hermes-lark-streaming v%s: child non-stop finish_reason=%s model=%s msg=%s",
                            __version__,
                            _finish_reason_child,
                            result.get("model", "?"),
                            (ctx["message_id"] or "?")[:12],
                        )
                    if _error_msg_child:
                        _logger.warning(
                            "hermes-lark-streaming v%s: child agent error: %s model=%s msg=%s",
                            __version__,
                            _error_msg_child[:200],
                            result.get("model", "?"),
                            (ctx["message_id"] or "?")[:12],
                        )

                    _agent_ref_child = ctx.get("_agent_ref")
                    cache_read_child = getattr(_agent_ref_child, "session_cache_read_tokens", 0) if _agent_ref_child else 0
                    cache_write_child = getattr(_agent_ref_child, "session_cache_write_tokens", 0) if _agent_ref_child else 0

                    card_sent_child = on_message_completed(
                        message_id=ctx["message_id"],
                        answer=result.get("final_response", ""),
                        duration=_elapsed_child,
                        model=result.get("model", ""),
                        tokens={
                            "input_tokens": result.get("input_tokens", 0),
                            "output_tokens": result.get("output_tokens", 0),
                            "cache_read_tokens": cache_read_child,
                            "cache_write_tokens": cache_write_child,
                        },
                        context={
                            "used_tokens": result.get("last_prompt_tokens", 0),
                            "max_tokens": result.get("context_length", 0),
                        },
                        api_calls=result.get("api_calls", 0),
                        history_offset=result.get("history_offset", 0),
                        compression_exhausted=result.get("compression_exhausted", False),
                        aborted=is_interrupted_child,
                        error_message=_error_msg_child,
                    )
                    if card_sent_child:
                        result["already_sent"] = True
                        ctx["card_sent"] = True
                        _logger.info(
                            "run_agent: child COMPLETE hook fired for msg=%s card_sent=True",
                            (ctx["message_id"] or "?")[:12],
                        )
                except Exception:
                    _logger.debug("run_agent: child COMPLETE hook failed", exc_info=True)

            # ── Step 2: Fire A's (parent) ABORTED COMPLETE ──
            # The parent message was interrupted by the child (B).
            # Fire its COMPLETE as ABORTED so A's card shows "已停止".
            try:
                from .patch import on_message_completed
                _logger.debug(
                    "run_agent: parent COMPLETE hook firing as interrupted "
                    "for msg=%s (child msg=%s completed normally)",
                    (_saved_parent_ctx.get("message_id") or "?")[:12],
                    (ctx.get("message_id") or "?")[:12] if ctx else "?",
                )
                on_message_completed(
                    message_id=_saved_parent_ctx["message_id"],
                    answer="",
                    duration=time.monotonic() - _saved_parent_ctx.get("_msg_start_time", time.monotonic()),
                    aborted=True,
                    error_message="Interrupted by new message",
                )
                _saved_parent_ctx["card_sent"] = True
                # BUG FIX (v0.15.4): Also set card_sent on the original
                # msg_context dict so that _wrap_handle_message_with_agent
                # can suppress the duplicate plain text reply.
                if _original_msg_context_ref is not None:
                    _original_msg_context_ref["card_sent"] = True
                    _logger.debug(
                        "run_agent: propagated card_sent=True to original "
                        "msg_context for msg=%s",
                        (_saved_parent_ctx.get("message_id") or "?")[:12],
                    )
                # Also mark already_sent so Hermes's gateway doesn't send text reply
                if isinstance(result, dict):
                    result["already_sent"] = True
            except Exception:
                _logger.debug("run_agent: parent ABORTED completion failed", exc_info=True)
        elif ctx is not None:
            try:
                from .patch import on_message_completed

                # 自计时：计算从消息开始到 agent 运行完成的耗时
                # 原因：_response_time 是 _handle_message_with_agent 的局部变量，
                # 不在 _run_agent 的返回值 agent_result 中，
                # 所以 result.get("_response_time", 0) 永远返回 0。
                _elapsed = time.monotonic() - ctx.get("_msg_start_time", time.monotonic())

                # ── 检查是否被中断（/stop 或新消息打断） ──
                # Hermes 的 /stop 不会让 _run_agent 返回 None，而是返回
                # interrupted=True / partial=True 的 result。
                # 此时应该显示"已停止"而非"已完成"。
                is_interrupted = result.get("interrupted", False) or result.get("partial", False)

                # ── 诊断日志：记录 finish_reason / error 等关键信息 ──
                # content_filter 等异常 finish_reason 会导致 AI 返回空回复，
                # 记录这些信息便于排查模型 API 侧的内容安全过滤问题。
                _finish_reason = result.get("finish_reason", "")
                _error_msg = result.get("error") or result.get("interrupt_message", "")
                if _finish_reason and _finish_reason != "stop":
                    _logger.warning(
                        "hermes-lark-streaming v%s: non-stop finish_reason=%s model=%s msg=%s",
                        __version__,
                        _finish_reason,
                        result.get("model", "?"),
                        (ctx["message_id"] or "?")[:12],
                    )
                if _error_msg:
                    _logger.warning(
                        "hermes-lark-streaming v%s: agent error: %s model=%s msg=%s",
                        __version__,
                        _error_msg[:200],
                        result.get("model", "?"),
                        (ctx["message_id"] or "?")[:12],
                    )

                # ── Extract cache tokens from agent reference ──
                # _maybe_wrap_callbacks stores _agent_ref in ctx when wrapping
                # callbacks.  We read cache_read_tokens / cache_write_tokens
                # from the agent object for the footer's cache hit rate display.
                _agent_ref = ctx.get("_agent_ref")
                cache_read = getattr(_agent_ref, "session_cache_read_tokens", 0) if _agent_ref else 0
                cache_write = getattr(_agent_ref, "session_cache_write_tokens", 0) if _agent_ref else 0

                card_sent = on_message_completed(
                    message_id=ctx["message_id"],
                    answer=result.get("final_response", ""),
                    duration=_elapsed,
                    model=result.get("model", ""),
                    tokens={
                        "input_tokens": result.get("input_tokens", 0),
                        "output_tokens": result.get("output_tokens", 0),
                        "cache_read_tokens": cache_read,
                        "cache_write_tokens": cache_write,
                    },
                    context={
                        "used_tokens": result.get("last_prompt_tokens", 0),
                        "max_tokens": result.get("context_length", 0),
                    },
                    api_calls=result.get("api_calls", 0),
                    history_offset=result.get("history_offset", 0),
                    compression_exhausted=result.get("compression_exhausted", False),
                    aborted=is_interrupted,
                    error_message=_error_msg,
                )
                if card_sent:
                    result["already_sent"] = True
                    ctx["card_sent"] = True
            except Exception:
                pass

        # ── Restore parent context after recursive interrupt follow-up ──
        # When we created a new context for the recursive call (above),
        # _msg_ctx now points to the child message's context. We must
        # restore the parent's context so that the parent _wrap_run_agent's
        # COMPLETE hook (which fires after this wrapper returns) can
        # correctly read the parent message's context.
        if _saved_parent_ctx is not None:
            _msg_ctx.set(_saved_parent_ctx)
            _thread_local_ctx.data = dict(_saved_parent_ctx)

        return result

    return wrapper


# ── AIAgent.run_conversation wrapper (callback interception) ───────


# Thread-local re-entrancy guard for _inject_time_prefix.
# When both the module-level patch and the direct AIAgent patch are active,
# AIAgent.run_conversation → (direct patch) _inject_time_prefix → orig →
# agent.conversation_loop.run_conversation → (module patch) _inject_time_prefix.
# The guard prevents the second call from injecting the prefix again.
_inject_time_guard = threading.local()


def _inject_time_prefix(user_message: str | None, persist_user_message: str | None) -> tuple[str | None, str | None]:
    """Prepend current time to user_message when inject_time is enabled.

    Returns (modified_user_message, modified_persist_user_message).
    Both are prefixed with ``<time>HH:MM:SS</time>`` so the DB-stored
    content matches what the API received — preserving prefix cache
    consistency.

    Uses XML-style tags instead of ``[HH:MM:SS CST]`` because:
    - LLMs universally understand XML tags as structured metadata, not
      conversational style — they won't mimic the format in responses.
    - Bracket-prefixed time (``[14:30:05 CST]``) can be ignored as noise
      by some models, or worse, mimicked in their output.
    - The date is omitted because Hermes's system prompt already contains
      the current date, so only the time portion is needed.
    - The timezone suffix (CST) is omitted for brevity; the system prompt
      establishes the timezone context.

    Re-entrancy safe: if called again from a nested patch layer (e.g.
    AIAgent.run_conversation → module-level run_conversation), the second
    call is a no-op — the prefix was already added by the outer layer.
    """
    # Re-entrancy guard: skip if an outer call already injected time
    if getattr(_inject_time_guard, 'active', False):
        return user_message, persist_user_message

    try:
        cfg = _get_config()
        if not cfg.inject_time:
            return user_message, persist_user_message
    except Exception:
        _logger.debug("inject_time: config read failed, skipping", exc_info=True)
        return user_message, persist_user_message

    _cst = timezone(timedelta(hours=8))
    now = datetime.now(_cst)
    time_prefix = f"<time>{now.strftime('%H:%M:%S')}</time> "

    if isinstance(user_message, str):
        user_message = time_prefix + user_message
        _logger.info("inject_time: prefixed user_message with %s", time_prefix.strip())

    # Also prefix persist_user_message so DB matches API →
    # prefix cache consistency is preserved.
    # This handles the edge case where gateway sets persist_user_message
    # for group chat observed_group_context.
    if isinstance(persist_user_message, str):
        persist_user_message = time_prefix + persist_user_message

    # Mark as injected so nested patch layers skip
    _inject_time_guard.active = True

    return user_message, persist_user_message


def _wrap_run_conversation(orig: Callable) -> Callable:
    """Wrap all 6 streaming callbacks right before run_conversation executes.

    When ``streaming.inject_time`` is enabled, prepends the current time
    (``<time>HH:MM:SS</time>``) to ``user_message`` so the model can
    perceive the current time without calling the ``date`` tool.

    The time prefix is also added to ``persist_user_message`` when set, so
    the DB-stored content matches what the API received — preserving
    prefix cache consistency across conversation turns.
    """

    @functools.wraps(orig)
    def wrapper(
        self,
        user_message,
        system_message=None,
        conversation_history=None,
        task_id=None,
        stream_callback=None,
        persist_user_message=None,
        **kwargs,
    ):
        # ── inject_time: prepend current time to user_message ──
        user_message, persist_user_message = _inject_time_prefix(
            user_message, persist_user_message
        )

        _maybe_wrap_callbacks(self)
        try:
            return orig(
                self,
                user_message,
                system_message,
                conversation_history,
                task_id,
                stream_callback,
                persist_user_message,
                **kwargs,
            )
        finally:
            # Always reset the re-entrancy guard so the next message
            # in the same thread can be injected again.
            _inject_time_guard.active = False

    return wrapper


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
                from .patch import on_answer_delta

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
                    from .patch import on_thinking_delta
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
                from .patch import on_tool_updated

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
            from .patch import on_reasoning_delta

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
                from .patch import on_background_review_message

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


# ── Background task wrapper ───────────────────────────────────────


def _wrap_run_background_task(orig: Callable) -> Callable:
    """Inject START/COMPLETE hooks for ``/background`` tasks so they get streaming cards.

    Background tasks run in a fire-and-forget asyncio task.  There is no
    Feishu ``message_id`` (the user's ``/background`` command message_id is
    already used by the main session).  We use the ``task_id``
    (e.g. ``bg_HHMMSS_xxxxxx``) as the message_id for the card session.

    The card is created as a **new message** (not a reply), since there is no
    original message to reply to in the background context.

    To prevent the original ``_run_background_task`` from also sending a plain
    text "✅ Background task complete" message (which would duplicate our card),
    we temporarily replace the Feishu adapter's ``send`` method with our own
    that suppresses the delivery when our card was already sent.
    """

    @functools.wraps(orig)
    async def wrapper(self, prompt, source, task_id, **kwargs):
        # Only intercept Feishu platform
        platform_name = getattr(getattr(source, "platform", None), "value", "").lower()
        if platform_name not in ("feishu", "lark"):
            return await orig(self, prompt, source, task_id, **kwargs)

        chat_id = getattr(source, "chat_id", "")

        # Set up message context so _maybe_wrap_callbacks works
        _msg_ctx.set({
            "message_id": task_id,
            "chat_id": chat_id,
            "anchor_id": None,  # No reply anchor for background tasks
            "event_message_id": task_id,  # Use task_id so callbacks find a valid eid
            "card_sent": False,
            "_msg_start_time": time.monotonic(),
            "_agent_ref": None,  # Will be filled by _maybe_wrap_callbacks
        })
        _thread_local_ctx.data = dict(_msg_ctx.get())

        # ── Fire START hook ──
        try:
            from .patch import on_message_started
            on_message_started(message_id=task_id, chat_id=chat_id, anchor_id=None)
        except Exception:
            _logger.debug("background task START hook failed", exc_info=True)

        # ── Wrap adapter.send to suppress duplicate text delivery ──
        adapter = None
        original_send = None

        try:
            if hasattr(self, "adapters") and source.platform:
                adapter = self.adapters.get(source.platform)
        except Exception:
            pass

        if adapter:
            original_send = adapter.send

            async def _intercepting_send(chat_id_send, content, **send_kwargs):
                """Suppress plain text delivery when our card was sent."""
                ctx = _msg_ctx.get()
                if ctx and ctx.get("card_sent"):
                    _logger.debug(
                        "background task: suppressing adapter.send (card already sent), chat=%s",
                        chat_id_send[:12] if chat_id_send else "?",
                    )
                    try:
                        from gateway.platforms.base import SendResult
                        return SendResult(success=True)
                    except (ImportError, AttributeError):
                        return None
                return await original_send(chat_id_send, content, **send_kwargs)

            adapter.send = _intercepting_send
            adapter._hls_bg_sending = True

        try:
            result = await orig(self, prompt, source, task_id, **kwargs)
        finally:
            if original_send and adapter:
                adapter.send = original_send
                adapter._hls_bg_sending = False

        # ── Fire COMPLETE hook ──
        ctx = _msg_ctx.get()
        if ctx is not None:
            try:
                from .patch import on_message_completed

                _elapsed = time.monotonic() - ctx.get("_msg_start_time", time.monotonic())

                # Extract cache tokens from agent reference (set by _maybe_wrap_callbacks)
                _agent_ref = ctx.get("_agent_ref")
                cache_read = getattr(_agent_ref, "session_cache_read_tokens", 0) if _agent_ref else 0
                cache_write = getattr(_agent_ref, "session_cache_write_tokens", 0) if _agent_ref else 0

                card_sent = on_message_completed(
                    message_id=task_id,
                    answer=(result or {}).get("final_response", ""),
                    duration=_elapsed,
                    model=(result or {}).get("model", ""),
                    tokens={
                        "input_tokens": (result or {}).get("input_tokens", 0),
                        "output_tokens": (result or {}).get("output_tokens", 0),
                        "cache_read_tokens": cache_read,
                        "cache_write_tokens": cache_write,
                    },
                    context={
                        "used_tokens": (result or {}).get("last_prompt_tokens", 0),
                        "max_tokens": (result or {}).get("context_length", 0),
                    },
                    api_calls=(result or {}).get("api_calls", 0),
                    history_offset=(result or {}).get("history_offset", 0),
                    compression_exhausted=(result or {}).get("compression_exhausted", False),
                    aborted=False,
                    error_message=(result or {}).get("error") or "",
                )

                if card_sent:
                    ctx["card_sent"] = True
                    # Mark result so upstream knows card was sent
                    if result is not None and isinstance(result, dict):
                        result["_hls_card_sent"] = True
            except Exception:
                _logger.debug("background task COMPLETE hook failed", exc_info=True)

        # Clear context
        _msg_ctx.set(None)
        _thread_local_ctx.data = None

        return result

    return wrapper


# ── Cron delivery wrapper ──────────────────────────────────────────


def _wrap_cron_deliver(orig: Callable) -> Callable:
    """Intercept cron ``_deliver_result`` and redirect Feishu deliveries to CardKit cards.

    The original ``cron.scheduler._deliver_result`` is a **module-level function**
    (not a class method) with signature::

        def _deliver_result(job: dict, content: str, adapters=None, loop=None)

    It iterates over delivery targets from ``_resolve_delivery_targets(job)``,
    and for each Feishu target calls ``runtime_adapter.send(chat_id, text, …)``.

    Our wrapper temporarily replaces the Feishu adapter's ``send`` method with a
    card-sending version.  This way:
    - All the original's logic (thread_id, metadata, error handling) still works.
    - Feishu text messages are replaced with CardKit cards.
    - If card delivery fails, it falls back to the original plain-text send.
    - No duplicate messages (card replaces text, not supplements it).
    - Thread-safe: the original ``send`` is restored in a ``finally`` block.
    """

    @functools.wraps(orig)
    def wrapper(job, content, adapters=None, loop=None, **kwargs):
        # Only intercept when there are adapters with a Feishu/Lark platform
        if not adapters:
            return orig(job, content, adapters=adapters, loop=loop, **kwargs)

        feishu_adapter = None
        feishu_platform_key = None

        try:
            from gateway.config import Platform

            for p in list(adapters.keys()):
                pn = p.value.lower() if hasattr(p, "value") else str(p).lower()
                if pn in ("feishu", "lark"):
                    feishu_adapter = adapters[p]
                    feishu_platform_key = p
                    break
        except (ImportError, AttributeError):
            pass

        if feishu_adapter is None:
            return orig(job, content, adapters=adapters, loop=loop, **kwargs)

        _logger.info(
            "hermes-lark-streaming v%s: cron delivery intercepted, redirecting to card (job=%s)",
            __version__,
            job.get("id", "?")[:12],
        )

        # ── Temporarily replace Feishu adapter.send with card-sending version ──
        original_send = feishu_adapter.send

        async def _card_sending_send(chat_id_send, content_text, **send_kwargs):
            """Redirect Feishu adapter.send to CardKit card delivery.

            This async function replaces the Feishu adapter's ``send`` method.
            Hermes calls ``safe_schedule_threadsafe(adapter.send(...), loop)``
            from ``_deliver_result``, which schedules this coroutine on the
            gateway's event loop.  Since we are *already* running on the event
            loop, we can simply ``await`` the card delivery — no
            ``run_coroutine_threadsafe`` / ``asyncio.run`` needed.

            Previous versions used ``run_coroutine_threadsafe`` +
            ``future.result(timeout=30)`` when the loop was running, which
            caused a **deadlock**: the loop was blocked waiting for a coroutine
            it could never schedule because it was blocked.  The 30-second
            timeout expired and the delivery fell back to plain text.
            """
            try:
                from .controller import get_controller
                ctrl = get_controller()
                _logger.info(
                    "cron _card_sending_send: ctrl.enabled=%s chat=%s content_len=%d",
                    ctrl.enabled,
                    chat_id_send[:12] if chat_id_send else "?",
                    len(content_text) if content_text else 0,
                )
                if ctrl.enabled and content_text:
                    # Try to strip MEDIA tags for cleaner card content
                    cleaned = content_text
                    try:
                        from gateway.platforms.base import BasePlatformAdapter
                        _, cleaned = BasePlatformAdapter.extract_media(content_text)
                    except (ImportError, AttributeError):
                        pass
                    if not cleaned.strip():
                        cleaned = content_text

                    # We are running on the event loop (scheduled via
                    # safe_schedule_threadsafe by _deliver_result), so we
                    # can await the card delivery directly.
                    await ctrl._do_cron_deliver(chat_id_send, cleaned.strip())

                    _logger.info(
                        "hermes-lark-streaming v%s: cron card delivered: chat=%s",
                        __version__,
                        chat_id_send[:12],
                    )
                    # Return a success result so the original _deliver_result
                    # thinks the send succeeded
                    try:
                        from gateway.platforms.base import SendResult
                        return SendResult(success=True)
                    except (ImportError, AttributeError):
                        return None
            except Exception:
                _logger.debug(
                    "hermes-lark-streaming v%s: cron card delivery failed, falling back to plain text",
                    __version__,
                    exc_info=True,
                )

            # Fallback: send plain text via the original adapter
            return await original_send(chat_id_send, content_text, **send_kwargs)

        feishu_adapter.send = _card_sending_send
        # Set flag so the class-level send wrapper knows not to
        # re-intercept cron's fallback plain-text sends.
        feishu_adapter._hls_cron_sending = True
        try:
            return orig(job, content, adapters=adapters, loop=loop, **kwargs)
        finally:
            feishu_adapter.send = original_send
            feishu_adapter._hls_cron_sending = False

    return wrapper


# ── FeishuAdapter interception layer (Phase 1: gateway message cards) ─


def _try_add_image_to_session(message_id: str, content: Any) -> bool:
    """Try to add an image from FeishuAdapter.send() to the card session.

    When Hermes sends an image via FeishuAdapter.send() with non-string
    content (e.g. a dict with image_key or a file:// URL string wrapped
    in a non-str type), we attempt to add it to the active card session
    so it appears inside the card instead of as a standalone message.

    Returns True if the image was added to the session, False otherwise.
    """
    try:
        from .controller import get_controller
        ctrl = get_controller()
        if not ctrl.enabled:
            return False

        session = ctrl._get_active_session(message_id)
        if session is None:
            return False

        # Extract image_key from Hermes's content format
        img_key = None
        if isinstance(content, dict):
            # Hermes may send a dict with image_key
            img_key = content.get("image_key") or content.get("img_key")
        elif isinstance(content, str):
            # Sometimes Hermes sends a file:// URL or MEDIA tag
            if content.startswith("file://") or "image" in content.lower():
                # We can't directly use file:// URLs in cards,
                # but the ImageResolver handles markdown image syntax
                return False

        if not img_key:
            return False

        # Add the image key to the session's image_resolver cache
        # so it gets included in the next card update
        if session.image_resolver:
            # Create a fake URL→img_key mapping so resolve_images
            # will replace markdown image refs with the img_key
            _fake_url = f"hermes_image://{img_key}"
            session.image_resolver._cache[_fake_url] = img_key
            _logger.info(
                "image added to session: msg=%s img_key=%s",
                message_id[:12], img_key[:12] if img_key else "?",
            )
            # Schedule a card update to include the image
            ctrl._schedule_card_update(session)
            return True

        return False
    except Exception:
        _logger.debug("_try_add_image_to_session failed", exc_info=True)
        return False


def _classify_gateway_message(content: str) -> str:
    """Classify a gateway-internal message by its content for card category.

    Returns one of: "error", "auth", "session", "slash", "system"
    """
    if not isinstance(content, str):
        return "system"
    # Auth / pairing messages
    if any(kw in content for kw in ("pairing code", "pairing requests", "配对码", "I don't recognize you")):
        return "auth"
    # Error messages
    if any(kw in content for kw in ("❌", "⚠️", "error", "failed", "Error", "Failed")):
        return "error"
    # Session lifecycle messages
    if any(kw in content for kw in ("Session", "session", "🔄", "♻", "compress", "compres")):
        return "session"
    # Slash command replies (common prefixes)
    if any(kw in content for kw in ("/help", "/status", "/model", "/usage", "/whoami", "/reset", "/new", "/stop", "/resume", "/undo", "/compress", "/goal", "/agents", "/background", "/queue", "/steer", "/yolo", "/footer")):
        return "slash"
    return "system"


def _wrap_feishu_adapter_send(orig_send: Callable) -> Callable:
    """Intercept ``FeishuAdapter.send()`` — convert text to gateway cards.

    This wrapper intercepts ALL text messages sent through the Feishu
    adapter's ``send()`` method and converts them to CardKit cards when:

    1. The message is NOT from the AI agent pipeline (which is already
       handled by callback interception + consumed mechanism).
    2. The controller is enabled and the FeishuClient is initialized.

    When the card delivery fails, it falls back to the original plain
    text ``send()``.

    **Agent path detection**: When a message is being handled by the
    AI agent pipeline, ``_msg_ctx`` has ``card_sent=True`` after the
    streaming card is delivered.  In that case, the gateway's own text
    reply should be suppressed (returned as success with no message
    sent).  When ``card_sent=False`` and there IS an event_message_id,
    the agent is still running — we also skip to avoid interfering
    with the streaming card.

    **Gateway-internal path**: When there is NO ``event_message_id``
    in the context (or the context is None), the message originates
    from the gateway itself (slash commands, auth, errors, etc.)
    and should be converted to a card.
    """
    async def _intercepted_send(self_feishu, chat_id, content, reply_to=None, metadata=None, **kwargs):
        # ── Agent path: handle image sends during agent pipeline ──
        # When Hermes sends an image (non-string content like a dict with
        # image_key) during the agent pipeline, we let it through as a
        # standalone image message. Previously (v0.15.3) we tried to suppress
        # standalone images and inject them into the card, but this caused
        # images to disappear entirely (see issue-v0.15.3-image-card-wrapping).
        # Images from the AI's markdown response are already handled by the
        # card streaming pipeline (ImageResolver). Standalone MEDIA sends
        # (send_message tool with <MEDIA>) should go through as independent
        # messages — they are NOT part of the streaming card content.
        if not isinstance(content, str):
            return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)

        # ── Guard: skip empty content ──
        if not content.strip():
            return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)

        # ── Phase 4: Media message card wrapping ──
        # When content contains MEDIA tags (Hermes wraps images/files in
        # <MEDIA>...</MEDIA> tags), extract the media and text parts, then
        # build a card with both the media and the text content.
        _media_parts: list[dict] | None = None
        _text_content = content
        try:
            from gateway.platforms.base import BasePlatformAdapter
            _media_parts, _text_content = BasePlatformAdapter.extract_media(content)
        except (ImportError, AttributeError):
            pass

        # ── Guard: check if this is a cron/background fallback send ──
        # When cron's _card_sending_send or background task's
        # _intercepting_send falls back to original_send, it calls
        # the (now-wrapped) send method. In that case we should
        # NOT try to make another card — just send plain text.
        # Detection: cron/bg sets a flag on the adapter instance.
        if getattr(self_feishu, "_hls_cron_sending", False) or getattr(self_feishu, "_hls_bg_sending", False):
            return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)

        # ── Agent path: suppress duplicate text reply ──
        ctx = _msg_ctx.get(None)
        if ctx is not None:
            eid = ctx.get("event_message_id", "")
            if eid:
                # We're inside an agent message pipeline.
                # If card was already sent, suppress the gateway's text reply.
                if ctx.get("card_sent"):
                    _logger.debug(
                        "feishu_adapter_send: suppressing gateway text reply "
                        "(card already sent), chat=%s content_len=%d",
                        chat_id[:12] if chat_id else "?",
                        len(content),
                    )
                    try:
                        from gateway.platforms.base import SendResult
                        return SendResult(success=True)
                    except (ImportError, AttributeError):
                        return None
                else:
                    # card_sent is False, but check if a card session exists
                    # (even in terminal state like ABORTED). If a card was
                    # created and is visible, suppress the plain text to
                    # avoid duplicates.
                    try:
                        from .controller import get_controller
                        _ctrl = get_controller()
                        if _ctrl and _ctrl.enabled:
                            _sess = _ctrl._sessions.get(eid)
                            if _sess and _sess.card_msg_id:
                                _logger.info(
                                    "feishu_adapter_send: suppressing text reply "
                                    "(card exists for msg=%s, state=%s, card_sent=%s)",
                                    eid[:12], _sess.state, ctx.get("card_sent"),
                                )
                                ctx["card_sent"] = True
                                try:
                                    from gateway.platforms.base import SendResult
                                    return SendResult(success=True)
                                except (ImportError, AttributeError):
                                    return None
                    except Exception:
                        pass
                    # Agent still running, card not yet sent — don't interfere
                    return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)

        # ── Gateway-internal path: convert to card ──
        _logger.info(
            "gateway_send: entering gateway-internal path, chat=%s content_len=%d has_media=%s",
            chat_id[:12] if chat_id else "?",
            len(content),
            bool(_media_parts),
        )
        try:
            from .controller import get_controller
            ctrl = get_controller()
            if ctrl and ctrl.enabled:
                # Check if gateway_cards feature is enabled
                cfg = _get_config()
                if not cfg.gateway_cards:
                    _logger.info("gateway_send: gateway_cards disabled, falling back to plain text")
                    return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)

                # Phase 4: Media-aware card building
                has_media = bool(_media_parts)
                cleaned = _text_content
                if not cleaned.strip() and not has_media:
                    cleaned = content
                if not cleaned.strip() and not has_media:
                    return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)

                category = _classify_gateway_message(cleaned or content)
                card_msg_id, card_id = await ctrl._do_gateway_deliver(
                    chat_id, cleaned.strip() if cleaned.strip() else content,
                    category=category,
                    media_parts=_media_parts if has_media else None,
                )
                if card_msg_id:
                    # Register the card so edit_message can update it later
                    _register_gateway_card(
                        card_msg_id,
                        chat_id=chat_id,
                        card_id=card_id,
                        category=category,
                    )
                    _logger.info(
                        "hermes-lark-streaming v%s: gateway message card sent: "
                        "chat=%s category=%s content_len=%d card_id=%s",
                        __version__,
                        chat_id[:12] if chat_id else "?",
                        category,
                        len(content),
                        (card_id or "?")[:12],
                    )
                    try:
                        from gateway.platforms.base import SendResult
                        return SendResult(success=True, message_id=card_msg_id)
                    except (ImportError, AttributeError):
                        return None
            else:
                _logger.info(
                    "gateway_send: controller not enabled (ctrl=%s), falling back to plain text",
                    bool(ctrl),
                )
        except Exception:
            _logger.info(
                "hermes-lark-streaming v%s: gateway card delivery failed, "
                "falling back to plain text",
                __version__,
                exc_info=True,
            )

        # ── Fallback: original plain text send ──
        _logger.info(
            "gateway_send: plain text fallback, chat=%s content_len=%d",
            chat_id[:12] if chat_id else "?",
            len(content),
        )
        return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)

    return _intercepted_send


def _register_gateway_card(card_msg_id: str, *, chat_id: str, card_id: str | None, category: str) -> None:
    """Register a gateway card so edit_message can update it later."""
    if not card_msg_id:
        return
    with _gateway_cards_lock:
        _gateway_cards[card_msg_id] = {
            "chat_id": chat_id,
            "card_id": card_id,
            "category": category,
        }
    _logger.debug(
        "registered gateway card: msg_id=%s card_id=%s category=%s",
        card_msg_id[:12], (card_id or "?")[:12], category,
    )


def _unregister_gateway_card(card_msg_id: str) -> None:
    """Remove a gateway card from the registry."""
    with _gateway_cards_lock:
        _gateway_cards.pop(card_msg_id, None)


def _wrap_feishu_adapter_edit(orig_edit: Callable) -> Callable:
    """Intercept ``FeishuAdapter.edit_message()`` — update gateway card content.

    When Hermes calls ``edit_message()`` on a message_id that was created
    by our gateway card system (Phase 1), we update the card content instead
    of trying to edit the plain text message (which no longer exists).

    This handles long-running notifications where Hermes initially sends
    a status message and later updates it (e.g. "Thinking..." → "Processing...").

    When the message_id is NOT a gateway card (i.e. it's an original Feishu
    message that was never converted to a card), we pass through to the
    original ``edit_message()``.
    """
    async def _intercepted_edit(self_feishu, chat_id, message_id, content, metadata=None, **kwargs):
        # ── Check if this message_id is a gateway card ──
        with _gateway_cards_lock:
            card_info = _gateway_cards.get(message_id)

        if card_info is not None and isinstance(content, str) and content.strip():
            _logger.info(
                "feishu_adapter_edit: updating gateway card msg_id=%s content_len=%d",
                message_id[:12] if message_id else "?",
                len(content),
            )
            try:
                from .controller import get_controller
                ctrl = get_controller()
                if ctrl and ctrl.enabled:
                    # Check if gateway_cards feature is enabled
                    cfg = _get_config()
                    if cfg.gateway_cards:
                        # Strip MEDIA tags for cleaner card content
                        cleaned = content
                        try:
                            from gateway.platforms.base import BasePlatformAdapter
                            _, cleaned = BasePlatformAdapter.extract_media(content)
                        except (ImportError, AttributeError):
                            pass
                        if not cleaned.strip():
                            cleaned = content

                        category = _classify_gateway_message(cleaned)
                        updated = await ctrl._do_gateway_card_update(
                            chat_id=card_info.get("chat_id", chat_id),
                            card_msg_id=message_id,
                            card_id=card_info.get("card_id"),
                            content=cleaned.strip(),
                            category=category,
                        )
                        if updated:
                            # Update category in registry
                            with _gateway_cards_lock:
                                if message_id in _gateway_cards:
                                    _gateway_cards[message_id]["category"] = category
                            try:
                                from gateway.platforms.base import SendResult
                                return SendResult(success=True)
                            except (ImportError, AttributeError):
                                return None
            except Exception:
                _logger.debug(
                    "feishu_adapter_edit: card update failed, falling back to original",
                    exc_info=True,
                )

        # ── Fallback: original edit_message ──
        _logger.debug(
            "feishu_adapter_edit: pass-through msg_id=%s content_len=%d",
            message_id[:12] if message_id else "?",
            len(content) if isinstance(content, str) else 0,
        )
        # Strip 'metadata' kwarg — Hermes's StreamConsumer passes it but
        # the original FeishuAdapter.edit_message() may not accept it.
        # This prevents: "edit_message() got an unexpected keyword argument 'metadata'"
        _fallback_kwargs = {k: v for k, v in kwargs.items() if k != "metadata"}
        try:
            return await orig_edit(self_feishu, chat_id, message_id, content, **_fallback_kwargs)
        except TypeError:
            # If the original still rejects kwargs, try with no extra kwargs
            return await orig_edit(self_feishu, chat_id, message_id, content)

    return _intercepted_edit


# ── Reaction → card status indicator (Phase 3) ─────────────────────


# Map Feishu reaction emojis to human-readable status labels
_REACTION_STATUS_MAP: dict[str, str] = {
    "👀": "Reading",
    "👍": "Done",
    "🤔": "Thinking",
    "⏳": "Processing",
    "✅": "Completed",
    "🔄": "Refreshing",
    "📝": "Composing",
}


def _wrap_feishu_adapter_add_reaction(orig_add_reaction: Callable) -> Callable:
    """Intercept ``FeishuAdapter.add_reaction()`` — card status indicator.

    When Hermes adds a reaction to a message that has a gateway card,
    we suppress the reaction emoji and instead show the status as a
    text indicator in the card's header/footer.

    This replaces the "emoji reaction on the user's message" pattern
    (which is invisible in card-only mode) with an in-card status
    indicator.
    """
    async def _intercepted_add_reaction(self_feishu, message_id, emoji, **kwargs):
        # ── Check if this message_id is a gateway card ──
        with _gateway_cards_lock:
            card_info = _gateway_cards.get(message_id)

        if card_info is not None:
            status_label = _REACTION_STATUS_MAP.get(emoji)
            if status_label:
                _logger.info(
                    "feishu_adapter_add_reaction: gateway card status msg_id=%s emoji=%s → %s",
                    message_id[:12] if message_id else "?",
                    emoji,
                    status_label,
                )
                try:
                    from .controller import get_controller
                    ctrl = get_controller()
                    if ctrl and ctrl.enabled:
                        cfg = _get_config()
                        if cfg.gateway_cards:
                            # Update the card with a status indicator
                            updated = await ctrl._do_gateway_card_status(
                                card_msg_id=message_id,
                                card_id=card_info.get("card_id"),
                                status_label=status_label,
                                emoji=emoji,
                                category=card_info.get("category", "system"),
                            )
                            if updated:
                                # Suppress the actual reaction — card shows status instead
                                try:
                                    from gateway.platforms.base import SendResult
                                    return SendResult(success=True)
                                except (ImportError, AttributeError):
                                    return None
                except Exception:
                    _logger.debug(
                        "feishu_adapter_add_reaction: card status update failed",
                        exc_info=True,
                    )

        # ── Fallback: original add_reaction ──
        return await orig_add_reaction(self_feishu, message_id, emoji, **kwargs)

    return _intercepted_add_reaction


def _wrap_feishu_adapter_delete_reaction(orig_delete_reaction: Callable) -> Callable:
    """Intercept ``FeishuAdapter.delete_reaction()`` — clear card status.

    When Hermes removes a reaction from a gateway card message,
    we clear the status indicator from the card.
    """
    async def _intercepted_delete_reaction(self_feishu, message_id, emoji, **kwargs):
        # ── Check if this message_id is a gateway card ──
        with _gateway_cards_lock:
            card_info = _gateway_cards.get(message_id)

        if card_info is not None:
            status_label = _REACTION_STATUS_MAP.get(emoji)
            if status_label:
                _logger.info(
                    "feishu_adapter_delete_reaction: gateway card clear status msg_id=%s emoji=%s",
                    message_id[:12] if message_id else "?",
                    emoji,
                )
                try:
                    from .controller import get_controller
                    ctrl = get_controller()
                    if ctrl and ctrl.enabled:
                        cfg = _get_config()
                        if cfg.gateway_cards:
                            # Clear the status indicator from the card
                            updated = await ctrl._do_gateway_card_status(
                                card_msg_id=message_id,
                                card_id=card_info.get("card_id"),
                                status_label="",
                                emoji="",
                                category=card_info.get("category", "system"),
                            )
                            if updated:
                                try:
                                    from gateway.platforms.base import SendResult
                                    return SendResult(success=True)
                                except (ImportError, AttributeError):
                                    return None
                except Exception:
                    _logger.debug(
                        "feishu_adapter_delete_reaction: card status clear failed",
                        exc_info=True,
                    )

        # ── Fallback: original delete_reaction ──
        return await orig_delete_reaction(self_feishu, message_id, emoji, **kwargs)

    return _intercepted_delete_reaction


# ── Clarify interactive card registry ──────────────────────────────────
# Stores the choices list for each clarify_id so the card action callback
# handler can look up the choice text from the option index.
_clarify_choices: dict[str, list[str]] = {}  # clarify_id → choices list
_clarify_questions: dict[str, str] = {}  # clarify_id → question text


def _wrap_feishu_adapter_send_clarify(orig_send_clarify: Callable) -> Callable:
    """Intercept ``FeishuAdapter.send_clarify()`` — render interactive card.

    Instead of the default text-based numbered list, we build a CardKit 2.0
    interactive card with:
      - A ``select_static`` dropdown for choices + "✏️ 自定义输入" option
      - An ``input`` field for open-ended questions (no choices)
      - Callback behaviors that route to our card action handler

    When the card can't be sent (controller disabled, API error), falls
    back to the original text-based send_clarify.
    """

    async def _intercepted_send_clarify(
        self_feishu, chat_id, question, choices, clarify_id, session_key, metadata=None, **kwargs
    ):
        _logger.info(
            "clarify card: send_clarify intercepted chat=%s question=%r choices=%s clarify_id=%s",
            (chat_id or "?")[:12],
            question[:50] if question else "",
            choices,
            (clarify_id or "?")[:12],
        )

        try:
            from .controller import get_controller
            ctrl = get_controller()
            if not ctrl or not ctrl.enabled or not ctrl._client_ok():
                _logger.debug("clarify card: controller not available, falling back to text")
                return await orig_send_clarify(
                    self_feishu, chat_id, question, choices, clarify_id, session_key,
                    metadata=metadata, **kwargs
                )

            from .cardkit import build_clarify_card

            card = build_clarify_card(
                question=question,
                choices=choices if choices else None,
                clarify_id=clarify_id,
            )

            # Store choices and question for callback lookup
            if choices:
                _clarify_choices[clarify_id] = list(choices)
            _clarify_questions[clarify_id] = question

            # Send the card via FeishuClient
            reply_to = None
            if metadata and isinstance(metadata, dict):
                reply_to = metadata.get("reply_to") or metadata.get("message_id")

            if reply_to:
                card_msg_id = await ctrl._client.reply_card(reply_to, card)
            else:
                card_msg_id = await ctrl._client.send_card_to_chat(chat_id, card)

            _logger.info(
                "clarify card: card sent successfully, clarify_id=%s card_msg_id=%s",
                (clarify_id or "?")[:12],
                (card_msg_id or "?")[:12],
            )

            # Register the card in gateway card registry (for edit tracking)
            _register_gateway_card(card_msg_id, chat_id=chat_id, card_id=None, category="clarify")

            # For choices mode: we handle resolution in the card action callback.
            # For open-ended mode (no choices): the input field triggers input_submit.
            # In both cases, we call mark_awaiting_text as a fallback so the
            # gateway text-intercept can also resolve if the user types instead.
            try:
                from tools.clarify_gateway import mark_awaiting_text
                mark_awaiting_text(clarify_id)
                _logger.debug("clarify card: mark_awaiting_text called for clarify_id=%s", (clarify_id or "?")[:12])
            except (ImportError, Exception) as e:
                _logger.debug("clarify card: mark_awaiting_text failed (%s), card callback will handle resolution", e)

            # Return success to suppress the original text-based send_clarify
            try:
                from gateway.platforms.base import SendResult
                return SendResult(success=True, message_id=card_msg_id)
            except (ImportError, AttributeError):
                return None

        except Exception as e:
            _logger.warning(
                "clarify card: failed to send card, falling back to text: %s",
                e,
                exc_info=True,
            )
            return await orig_send_clarify(
                self_feishu, chat_id, question, choices, clarify_id, session_key,
                metadata=metadata, **kwargs
            )

    return _intercepted_send_clarify


def _wrap_feishu_card_action_trigger(original_method: Callable) -> Callable:
    """Wrap ``FeishuAdapter._on_card_action_trigger`` to handle clarify card callbacks.

    When a user interacts with a clarify card (selects a dropdown option
    or submits text input), this wrapper intercepts the callback and:
      - For predefined choices: calls ``resolve_gateway_clarify(clarify_id, choice_text)``
      - For "Other/custom input": calls ``mark_awaiting_text(clarify_id)``
      - For input_submit: calls ``resolve_gateway_clarify(clarify_id, input_text)``

    Returns an updated card inline (via P2CardActionTriggerResponse) to
    show the resolved/awaiting state.
    """

    def _wrapped(self, data):
        # ── Check if this is a clarify card action ──
        event = getattr(data, "event", None)
        action = getattr(event, "action", None)
        action_value = getattr(action, "value", {}) or {}

        clarify_action = action_value.get("hermes_clarify_action") if isinstance(action_value, dict) else None

        if clarify_action:
            return _handle_clarify_card_action(self, data, clarify_action, action_value)

        # Not a clarify action — pass through to original
        return original_method(self, data)

    return _wrapped


def _handle_clarify_card_action(
    adapter_instance,
    data: Any,
    clarify_action: str,
    action_value: dict,
) -> Any:
    """Handle a clarify card action callback.

    This function is called synchronously from the card action trigger.
    It resolves the clarify and returns an inline card update.
    """
    # Import P2CardActionTriggerResponse and CallBackCard (may be None if SDK version doesn't support)
    try:
        from lark_oapi.api.cardkit.v1 import P2CardActionTriggerResponse, CallBackCard
    except ImportError:
        P2CardActionTriggerResponse = None
        CallBackCard = None

    def _empty_response():
        if P2CardActionTriggerResponse is None:
            return None
        return P2CardActionTriggerResponse()

    clarify_id = action_value.get("clarify_id", "")
    if not clarify_id:
        _logger.debug("clarify card: callback missing clarify_id, ignoring")
        return _empty_response()

    _logger.info(
        "clarify card: callback received action=%s clarify_id=%s",
        clarify_action,
        (clarify_id or "?")[:12],
    )

    # ── Authorization check ──
    event = getattr(data, "event", None)
    operator = getattr(event, "operator", None)
    open_id = str(getattr(operator, "open_id", "") or "")
    if hasattr(adapter_instance, "_is_interactive_operator_authorized"):
        if not adapter_instance._is_interactive_operator_authorized(open_id):
            _logger.warning(
                "clarify card: unauthorized click by %s for clarify_id=%s",
                open_id or "<unknown>",
                (clarify_id or "?")[:12],
            )
            return _empty_response()

    question = _clarify_questions.get(clarify_id, "")

    # ── Handle select action (dropdown choice) ──
    if clarify_action == "select":
        selected_option = str(getattr(getattr(event, "action", None), "option", "") or "")

        if selected_option == "other":
            # User selected "Custom input" → switch to text-capture mode
            _logger.info(
                "clarify card: user selected 'Other' for clarify_id=%s",
                (clarify_id or "?")[:12],
            )
            try:
                from tools.clarify_gateway import mark_awaiting_text
                mark_awaiting_text(clarify_id)
            except (ImportError, Exception) as e:
                _logger.warning("clarify card: mark_awaiting_text failed: %s", e)

            # Return an updated card showing "awaiting input" state
            if P2CardActionTriggerResponse is not None and CallBackCard is not None:
                from .cardkit import build_clarify_awaiting_input_card
                card_data = build_clarify_awaiting_input_card(question=question)
                response = P2CardActionTriggerResponse()
                card = CallBackCard()
                card.type = "raw"
                card.data = card_data
                response.card = card
                return response
            return _empty_response()

        # Predefined choice selected → resolve immediately
        choices = _clarify_choices.get(clarify_id, [])
        try:
            idx = int(selected_option)
            choice_text = choices[idx]
        except (ValueError, IndexError):
            _logger.warning(
                "clarify card: invalid option index '%s' for clarify_id=%s (choices=%s)",
                selected_option,
                (clarify_id or "?")[:12],
                choices,
            )
            return _empty_response()

        _logger.info(
            "clarify card: resolving with choice '%s' for clarify_id=%s",
            choice_text,
            (clarify_id or "?")[:12],
        )

        # Resolve the clarify (schedule on event loop since we're in a sync callback)
        loop = getattr(adapter_instance, "_loop", None)
        if loop is not None:
            try:
                from tools.clarify_gateway import resolve_gateway_clarify
                from agent.async_utils import safe_schedule_threadsafe

                async def _do_resolve():
                    resolve_gateway_clarify(clarify_id, choice_text)

                safe_schedule_threadsafe(
                    _do_resolve(), loop,
                    logger=_logger,
                    log_message="clarify card: failed to schedule resolve_gateway_clarify",
                    log_level=logging.WARNING,
                )
            except (ImportError, Exception) as e:
                _logger.warning("clarify card: resolve_gateway_clarify scheduling failed: %s", e)
                # Try synchronous fallback
                try:
                    from tools.clarify_gateway import resolve_gateway_clarify
                    resolve_gateway_clarify(clarify_id, choice_text)
                except (ImportError, Exception) as e2:
                    _logger.warning("clarify card: synchronous resolve also failed: %s", e2)

        # Cleanup stored data
        _clarify_choices.pop(clarify_id, None)
        _clarify_questions.pop(clarify_id, None)

        # Return updated card showing resolved state
        if P2CardActionTriggerResponse is not None and CallBackCard is not None:
            from .cardkit import build_clarify_resolved_card
            card_data = build_clarify_resolved_card(question=question, selected=choice_text)
            response = P2CardActionTriggerResponse()
            card = CallBackCard()
            card.type = "raw"
            card.data = card_data
            response.card = card
            return response
        return _empty_response()

    # ── Handle input_submit action (text input) ──
    if clarify_action == "input_submit":
        action_obj = getattr(event, "action", None)
        input_text = str(getattr(action_obj, "input_value", "") or "").strip()

        if not input_text:
            _logger.debug("clarify card: empty input submitted for clarify_id=%s", (clarify_id or "?")[:12])
            return _empty_response()

        _logger.info(
            "clarify card: resolving with input '%s' for clarify_id=%s",
            input_text[:50],
            (clarify_id or "?")[:12],
        )

        # Resolve the clarify
        loop = getattr(adapter_instance, "_loop", None)
        if loop is not None:
            try:
                from tools.clarify_gateway import resolve_gateway_clarify
                from agent.async_utils import safe_schedule_threadsafe

                async def _do_resolve():
                    resolve_gateway_clarify(clarify_id, input_text)

                safe_schedule_threadsafe(
                    _do_resolve(), loop,
                    logger=_logger,
                    log_message="clarify card: failed to schedule resolve_gateway_clarify",
                    log_level=logging.WARNING,
                )
            except (ImportError, Exception) as e:
                _logger.warning("clarify card: resolve_gateway_clarify scheduling failed: %s", e)
                try:
                    from tools.clarify_gateway import resolve_gateway_clarify
                    resolve_gateway_clarify(clarify_id, input_text)
                except (ImportError, Exception) as e2:
                    _logger.warning("clarify card: synchronous resolve also failed: %s", e2)

        # Cleanup stored data
        _clarify_choices.pop(clarify_id, None)
        _clarify_questions.pop(clarify_id, None)

        # Return updated card showing resolved state
        if P2CardActionTriggerResponse is not None and CallBackCard is not None:
            from .cardkit import build_clarify_resolved_card
            card_data = build_clarify_resolved_card(question=question, selected=input_text)
            response = P2CardActionTriggerResponse()
            card = CallBackCard()
            card.type = "raw"
            card.data = card_data
            response.card = card
            return response
        return _empty_response()

    _logger.debug("clarify card: unknown action '%s', ignoring", clarify_action)
    return _empty_response()


def _wrap_feishu_adapter_send_image_file(orig_send_image_file: Callable) -> Callable:
    """Intercept ``FeishuAdapter.send_image_file()`` — add image to card session.

    When Hermes sends a local image via ``send_image_file()`` during the
    agent pipeline, we upload it to Feishu first (to get an img_key), then
    add the img_key to the card session's image resolver and inject a
    markdown image reference into the session text. This renders the image
    inside the card instead of as a standalone image message.

    When not in an agent pipeline, or if the upload/interception fails,
    the original method is called as fallback.
    """

    async def _intercepted_send_image_file(
        self_feishu, chat_id, image_path, caption=None, reply_to=None, metadata=None, **kwargs
    ):
        ctx = _msg_ctx.get(None)
        if ctx is not None:
            eid = ctx.get("event_message_id", "")
            if eid:
                # Inside agent pipeline — try to add image to card session
                try:
                    from .controller import get_controller
                    ctrl = get_controller()
                    if ctrl.enabled:
                        session = ctrl._get_active_session(eid)
                        if session is not None and ctrl._client is not None:
                            _logger.info(
                                "feishu_adapter_send_image_file: intercepting image for "
                                "card session, eid=%s path=%s",
                                eid[:12],
                                image_path[:40] if image_path else "?",
                            )
                            # Upload the image file to Feishu to get img_key
                            import os as _os
                            img_key = None
                            if _os.path.exists(image_path):
                                try:
                                    img_key = await ctrl._client.upload_local_image(image_path)
                                except Exception:
                                    _logger.debug(
                                        "feishu_adapter_send_image_file: upload failed",
                                        exc_info=True,
                                    )

                            if img_key:
                                # Register the img_key in the image resolver cache
                                _fake_url = f"file://{image_path}"
                                if session.image_resolver:
                                    session.image_resolver._cache[_fake_url] = img_key
                                # Inject a markdown image reference into the session text
                                _img_md = f"![{caption or 'image'}]({_fake_url})"
                                session.text.on_partial(_img_md)
                                ctrl._schedule_card_update(session)
                                _logger.info(
                                    "feishu_adapter_send_image_file: image added to card, "
                                    "eid=%s img_key=%s",
                                    eid[:12], img_key[:12],
                                )
                                try:
                                    from gateway.platforms.base import SendResult
                                    return SendResult(success=True)
                                except (ImportError, AttributeError):
                                    return None
                            else:
                                _logger.debug(
                                    "feishu_adapter_send_image_file: upload failed, "
                                    "falling back to standalone send, eid=%s",
                                    eid[:12],
                                )
                except Exception:
                    _logger.debug(
                        "feishu_adapter_send_image_file: interception failed, "
                        "falling back to standalone send",
                        exc_info=True,
                    )

        # Fallback: original send_image_file
        return await orig_send_image_file(
            self_feishu, chat_id, image_path,
            caption=caption, reply_to=reply_to, metadata=metadata, **kwargs
        )

    return _intercepted_send_image_file


def _wrap_feishu_adapter_send_image(orig_send_image: Callable) -> Callable:
    """Intercept ``FeishuAdapter.send_image()`` — add image to card session.

    When Hermes sends a remote image via ``send_image()`` during the agent
    pipeline, we add it to the active card session's image resolver instead
    of sending it as a standalone image message.

    When not in an agent pipeline, the original method is called unchanged.
    """

    async def _intercepted_send_image(
        self_feishu, chat_id, image_url, caption=None, reply_to=None, metadata=None, **kwargs
    ):
        ctx = _msg_ctx.get(None)
        if ctx is not None:
            eid = ctx.get("event_message_id", "")
            if eid:
                # Inside agent pipeline — try to add image to card session
                try:
                    from .controller import get_controller
                    ctrl = get_controller()
                    if ctrl.enabled:
                        session = ctrl._get_active_session(eid)
                        if session is not None:
                            _logger.info(
                                "feishu_adapter_send_image: intercepting remote image for "
                                "card session, eid=%s url=%s",
                                eid[:12],
                                image_url[:40] if image_url else "?",
                            )
                            # If image_resolver exists, it will handle the URL
                            # via resolve_images on next card update
                            # Inject a markdown image reference into the session text
                            _img_md = f"![{caption or 'image'}]({image_url})"
                            session.text.on_partial(_img_md)
                            ctrl._schedule_card_update(session)
                            _logger.info(
                                "feishu_adapter_send_image: image added to card session, "
                                "eid=%s url=%s",
                                eid[:12],
                                image_url[:40] if image_url else "?",
                            )
                            try:
                                from gateway.platforms.base import SendResult
                                return SendResult(success=True)
                            except (ImportError, AttributeError):
                                return None
                except Exception:
                    _logger.debug(
                        "feishu_adapter_send_image: interception failed, "
                        "falling back to standalone send",
                        exc_info=True,
                    )

        # Fallback: original send_image
        return await orig_send_image(
            self_feishu, chat_id, image_url,
            caption=caption, reply_to=reply_to, metadata=metadata, **kwargs
        )

    return _intercepted_send_image


# ── Namespace-collision-safe module resolver ────────────────────────


def _resolve_hermes_agent_module() -> tuple[Any, Any] | None:
    """Resolve Hermes's ``agent.conversation_loop`` module reliably.

    This function works around a **namespace collision** bug on Apple
    Silicon Macs where a PyPI package named ``agent`` shadows Hermes's
    own ``agent`` package.  The symptom is::

        ModuleNotFoundError: No module named 'agent.conversation_loop'

    (Python finds *an* ``agent`` package, just not Hermes's one.)

    Resolution strategy (in order of priority):

    1. **sys.modules cache** — if Hermes already imported
      ``agent.conversation_loop``, it's sitting in ``sys.modules``.
      Reading it from there bypasses the import machinery entirely and
      is immune to any path / namespace issues.
    2. **Anchor-based discovery** — use a known Hermes module
      (``gateway.run`` or ``run_agent``) as a filesystem anchor to
      locate the ``agent/`` directory, then load it directly with
      ``importlib``.
    3. **Standard import** — ``from agent.conversation_loop import …``
      as a last resort (works when there's no collision).

    Returns ``(conversation_loop_module, run_conversation_func)`` or
    ``None`` if the module cannot be found.
    """
    # ── Strategy 1: sys.modules ──
    # Hermes MUST have imported agent.conversation_loop before loading
    # plugins (it's used by run_agent.py which gateway.run imports).
    # If it's here, just use it — no path issues possible.
    cl_mod = sys.modules.get("agent.conversation_loop")
    if cl_mod is not None:
        func = getattr(cl_mod, "run_conversation", None)
        if func is not None:
            _logger.info(
                "hermes-lark-streaming: agent.conversation_loop resolved "
                "via sys.modules (path=%s)",
                getattr(cl_mod, "__file__", "?"),
            )
            return cl_mod, func
        else:
            _logger.warning(
                "hermes-lark-streaming: agent.conversation_loop found in "
                "sys.modules but has no 'run_conversation' attribute"
            )

    # ── Strategy 2: Anchor-based discovery ──
    # Use known Hermes modules to find the repo root, then load
    # agent/conversation_loop.py directly by file path.
    for anchor_name in ("gateway.run", "run_agent"):
        anchor = sys.modules.get(anchor_name)
        if anchor is None:
            try:
                anchor = importlib.import_module(anchor_name)
            except ImportError:
                continue

        anchor_file = getattr(anchor, "__file__", None)
        if not anchor_file:
            continue

        # gateway/run.py → repo root;  run_agent.py → repo root
        repo_root = Path(anchor_file).resolve().parent
        if anchor_name == "gateway.run":
            repo_root = repo_root.parent

        cl_file = repo_root / "agent" / "conversation_loop.py"
        if not cl_file.is_file():
            _logger.debug(
                "hermes-lark-streaming: anchor %s → %s, but %s not found",
                anchor_name, repo_root, cl_file,
            )
            continue

        _logger.info(
            "hermes-lark-streaming: found conversation_loop.py via anchor "
            "%s → %s", anchor_name, cl_file,
        )

        # Load the module directly by file path, bypassing the
        # ``agent`` namespace entirely.
        spec = importlib.util.spec_from_file_location(
            "agent.conversation_loop",  # canonical name
            str(cl_file),
        )
        if spec is None or spec.loader is None:
            continue

        try:
            mod = importlib.util.module_from_spec(spec)
            # Register in sys.modules so subsequent imports find it
            sys.modules["agent.conversation_loop"] = mod
            # Also ensure the parent 'agent' package can find it
            agent_pkg = sys.modules.get("agent")
            if agent_pkg is not None:
                if not hasattr(agent_pkg, "conversation_loop"):
                    agent_pkg.conversation_loop = mod  # type: ignore[attr-defined]
            spec.loader.exec_module(mod)
            func = getattr(mod, "run_conversation", None)
            if func is not None:
                _logger.info(
                    "hermes-lark-streaming: agent.conversation_loop loaded "
                    "via anchor-based discovery ✓",
                )
                return mod, func
        except Exception as e:
            _logger.warning(
                "hermes-lark-streaming: anchor-based load of "
                "agent.conversation_loop failed: %s", e,
                exc_info=True,
            )

    # ── Strategy 3: Standard import ──
    try:
        from agent.conversation_loop import run_conversation as _func
        import agent.conversation_loop as _mod
        _logger.info(
            "hermes-lark-streaming: agent.conversation_loop resolved "
            "via standard import",
        )
        return _mod, _func
    except (ImportError, AttributeError) as e:
        _logger.warning(
            "hermes-lark-streaming: agent.conversation_loop standard "
            "import failed: %s. This is likely caused by a namespace "
            "collision (another Python package named 'agent' shadowing "
            "Hermes's 'agent'). Try: pip uninstall agent", e,
        )

    return None


# ── Public entry point ─────────────────────────────────────────────


def _detect_hermes_layout() -> dict[str, bool]:
    """Probe which Hermes internal modules are available.

    Hermes has undergone several internal restructurings:

    - **Pre-v0.10**: ``run_conversation`` was a ~4000-line method inside
      ``AIAgent`` (``run_agent.py``).  No ``agent/conversation_loop.py``
      existed.
    - **v0.10+**: The body was extracted into ``agent/conversation_loop.py``
      and ``AIAgent.run_conversation`` became a thin forwarder that does
      ``from agent.conversation_loop import run_conversation``.

    Both layouts are fully supported — the probe just tells us which
    patch strategy to prefer.
    """
    layout = {
        "has_conversation_loop": False,
        "has_gateway_run": False,
        "has_cron_scheduler": False,
    }

    # Use _resolve_hermes_agent_module() instead of bare import —
    # this handles the Apple Silicon namespace collision bug.
    resolved = _resolve_hermes_agent_module()
    if resolved is not None:
        layout["has_conversation_loop"] = True

    try:
        from gateway.run import GatewayRunner  # noqa: F401
        layout["has_gateway_run"] = True
    except (ImportError, AttributeError):
        pass

    # Cron scheduler: probe for the module-level _deliver_result function.
    # In Hermes, _deliver_result is a module-level function in cron.scheduler,
    # NOT a class method on Scheduler.  We check for the module directly.
    try:
        import cron.scheduler as _cron_probe  # noqa: F401
        if hasattr(_cron_probe, "_deliver_result"):
            layout["has_cron_scheduler"] = True
    except ImportError:
        try:
            import gateway.cron.scheduler as _cron_probe  # noqa: F401
            if hasattr(_cron_probe, "_deliver_result"):
                layout["has_cron_scheduler"] = True
        except (ImportError, AttributeError):
            pass

    _logger.info(
        "hermes-lark-streaming: Hermes layout probe → %s",
        layout,
    )
    return layout


def _apply_gateway_runner_patches() -> bool:
    """Apply the three critical GatewayRunner method patches.

    Patches:
      - ``_handle_message``           → NORMALIZE hook
      - ``_handle_message_with_agent`` → START + ABORT/INTERRUPT hooks
      - ``_run_agent``                → event_message_id injection + COMPLETE hook
      - ``_run_background_task``       → START/COMPLETE for background tasks (optional)

    Returns ``True`` if the patches were applied successfully,
    ``False`` if gateway.run could not be imported or was incompatible.

    Thread-safe: guarded by ``_gw_runner_patched`` flag so the delayed
    thread won't double-patch if the immediate path already succeeded.
    """
    global _gw_runner_patched

    if _gw_runner_patched:
        return True  # Already patched (e.g. immediate path succeeded)

    try:
        from gateway.run import GatewayRunner
    except (ImportError, AttributeError):
        return False  # Not available yet

    try:
        GatewayRunner._handle_message = _wrap_handle_message(GatewayRunner._handle_message)
        GatewayRunner._handle_message_with_agent = _wrap_handle_message_with_agent(
            GatewayRunner._handle_message_with_agent
        )
        GatewayRunner._run_agent = _wrap_run_agent(GatewayRunner._run_agent)

        # ── Background task patch ──
        # Wraps _run_background_task to inject START/COMPLETE hooks
        # so /background tasks also get streaming cards.
        try:
            GatewayRunner._run_background_task = _wrap_run_background_task(
                GatewayRunner._run_background_task
            )
            _logger.info("hermes-lark-streaming: GatewayRunner._run_background_task patched ✓")
        except AttributeError:
            _logger.debug("hermes-lark-streaming: _run_background_task not found, background cards disabled")

        _gw_runner_patched = True
        return True
    except (ImportError, AttributeError) as e:
        _logger.error(
            "hermes-lark-streaming: GatewayRunner patch FAILED — "
            "gateway.run found but incompatible. "
            "Streaming cards will NOT work. Error: %s", e,
        )
        return False


def apply_patches() -> None:
    """Apply all runtime monkey patches to ``GatewayRunner`` and ``AIAgent``.

    Call exactly once during plugin loading (from ``plugin.register()``).
    Idempotent — protected by a module-level flag.

    **Architecture-adaptive patching**: Hermes has been restructured
    multiple times internally.  This function probes which modules are
    available and applies the optimal patch strategy for that layout,
    rather than assuming a specific internal structure.

    Two equivalent patch paths for ``run_conversation``:

    1. **Module-level** (``agent.conversation_loop.run_conversation``) —
       patches the "water main" so ALL callers are intercepted.  Only
       available on Hermes v0.10+.
    2. **Direct AIAgent** (``AIAgent.run_conversation``) — patches the
       "faucet".  Works on ALL Hermes versions and is functionally
       equivalent to the module-level patch.

    Both paths call ``_maybe_wrap_callbacks(self)`` and handle
    ``inject_time``.  The re-entrancy guard in ``_inject_time_prefix``
    ensures no double-injection when both are active.
    """
    if getattr(apply_patches, "_applied", False):
        return
    apply_patches._applied = True  # type: ignore[attr-defined]

    _logger.info("hermes-lark-streaming v%s: apply_patches() starting", __version__)

    # ── Probe Hermes layout ──
    layout = _detect_hermes_layout()

    # ── Patch GatewayRunner ──
    # This is the core patch — without it, streaming cards cannot work.
    gw_patched = False
    gw_delayed = False
    if layout["has_gateway_run"]:
        # gateway.run already loaded — patch immediately
        if _apply_gateway_runner_patches():
            gw_patched = True
            _logger.info("hermes-lark-streaming: GatewayRunner patched ✓")
    else:
        # gateway.run not yet loaded — start delayed-patch poll thread
        _logger.info(
            "hermes-lark-streaming: gateway.run not loaded yet — "
            "starting delayed patch poll (2s interval, 60s timeout)",
        )
        gw_delayed = True

        def _delayed_gw_patch():
            """Poll for gateway.run and apply GatewayRunner patches once available."""
            deadline = time.monotonic() + 60.0  # 60-second timeout
            while time.monotonic() < deadline:
                time.sleep(2.0)  # Poll every 2 seconds
                if _apply_gateway_runner_patches():
                    _logger.info(
                        "hermes-lark-streaming: GatewayRunner patched (delayed) ✓"
                    )
                    return
                _logger.debug(
                    "hermes-lark-streaming: delayed patch — gateway.run still not available, "
                    "retrying (%.0fs remaining)",
                    deadline - time.monotonic(),
                )
            # Timeout — gateway.run never became available
            _logger.error(
                "hermes-lark-streaming: gateway.run NOT FOUND after 60s — "
                "this Hermes version may be too old or installed incorrectly. "
                "Streaming cards will NOT work. "
                "Please check: 1) Hermes is running via gateway mode, "
                "2) Hermes version >= v0.5.0, "
                "3) Re-run: hermes setup && hermes gateway start",
            )

        _delayed_thread = threading.Thread(target=_delayed_gw_patch, daemon=True)
        _delayed_thread.start()

    # ── Patch run_conversation (strategy depends on Hermes layout) ──
    # Both strategies are functionally equivalent — they both call
    # _maybe_wrap_callbacks(self) and handle inject_time.
    # The module-level patch is preferred only because it intercepts
    # ALL callers, not just AIAgent.

    _module_patch_applied = False
    if layout["has_conversation_loop"]:
        # Hermes v0.10+: patch the module-level function (preferred)
        # Use _resolve_hermes_agent_module() to get the module safely,
        # bypassing any namespace collision.
        resolved = _resolve_hermes_agent_module()
        if resolved is not None:
            _cl_mod, _cl_run_conversation = resolved
            try:
                _cl_mod.run_conversation = _wrap_run_conversation(_cl_run_conversation)
                _module_patch_applied = True
                _logger.info("hermes-lark-streaming: agent.conversation_loop module patched ✓")
            except (AttributeError, TypeError) as e:
                _logger.warning(
                    "hermes-lark-streaming: agent.conversation_loop found but "
                    "patch failed (%s). Falling back to direct AIAgent patch.", e,
                )

    if not _module_patch_applied:
        # Hermes <v0.10 OR module patch failed: use direct AIAgent patch
        _logger.info(
            "hermes-lark-streaming: using direct AIAgent patch "
            "(Hermes %s conversation_loop module)",
            "has no" if not layout["has_conversation_loop"] else "has incompatible",
        )

    # Always apply the direct AIAgent patch as well — it serves as:
    # 1. The PRIMARY patch when conversation_loop doesn't exist (older Hermes)
    # 2. A belt-and-suspenders backup when conversation_loop IS patched
    # The re-entrancy guard in _inject_time_prefix prevents double-injection.
    _apply_direct_agent_patch()

    # ── Cron scheduler ──
    # Patch the module-level _deliver_result function instead of the
    # Scheduler class method.  In Hermes, _deliver_result is a standalone
    # function in cron.scheduler, not Scheduler._deliver_result.
    cron_patched = False
    if layout["has_cron_scheduler"]:
        try:
            import cron.scheduler as _cron_mod
            _cron_mod._deliver_result = _wrap_cron_deliver(_cron_mod._deliver_result)
            cron_patched = True
            _logger.info("hermes-lark-streaming: cron scheduler patched ✓")
        except (ImportError, AttributeError) as e:
            _logger.debug("hermes-lark-streaming: cron.scheduler patch failed (%s)", e)
        if not cron_patched:
            try:
                import gateway.cron.scheduler as _cron_mod
                _cron_mod._deliver_result = _wrap_cron_deliver(_cron_mod._deliver_result)
                cron_patched = True
                _logger.info("hermes-lark-streaming: cron scheduler patched (gateway path) ✓")
            except (ImportError, AttributeError) as e:
                _logger.info("hermes-lark-streaming: cron scheduler not found (%s), cron cards disabled", e)

    # ── FeishuAdapter interception (Phase 1: gateway message cards) ──
    # Patch FeishuAdapter.send() and edit_message() to intercept ALL
    # text messages and convert non-agent messages to CardKit cards.
    # This covers: slash commands, auth messages, errors, notifications,
    # session lifecycle, busy-ack, gateway lifecycle, etc.
    feishu_patched = False
    try:
        from gateway.platforms.feishu import FeishuAdapter

        FeishuAdapter.send = _wrap_feishu_adapter_send(FeishuAdapter.send)
        try:
            FeishuAdapter.edit_message = _wrap_feishu_adapter_edit(FeishuAdapter.edit_message)
        except AttributeError:
            _logger.debug("hermes-lark-streaming: FeishuAdapter.edit_message not found, edit interception skipped")
        # Phase 3: Reaction → card status indicator
        try:
            FeishuAdapter.add_reaction = _wrap_feishu_adapter_add_reaction(FeishuAdapter.add_reaction)
        except AttributeError:
            _logger.debug("hermes-lark-streaming: FeishuAdapter.add_reaction not found, reaction interception skipped")
        try:
            FeishuAdapter.delete_reaction = _wrap_feishu_adapter_delete_reaction(FeishuAdapter.delete_reaction)
        except AttributeError:
            _logger.debug("hermes-lark-streaming: FeishuAdapter.delete_reaction not found, reaction interception skipped")
        # NOTE(v0.15.4): send_image_file / send_image interception REMOVED.
        # The interception was fundamentally broken — it injected file:// URLs
        # into session.text.on_partial() which were then stripped by
        # _strip_invalid_image_keys(), and suppressed the original standalone
        # send, causing images to disappear entirely.
        # Images are now sent as standalone messages (pre-v0.15.3 behavior).
        # See: issue-v0.15.3-image-card-wrapping

        # ── Clarify interactive card patches ──
        # Patch send_clarify to render interactive CardKit cards instead of
        # text-based numbered lists.  Patch _on_card_action_trigger to handle
        # clarify card callbacks (dropdown select, text input).
        clarify_patched = False
        try:
            FeishuAdapter.send_clarify = _wrap_feishu_adapter_send_clarify(FeishuAdapter.send_clarify)
            clarify_patched = True
            _logger.info("hermes-lark-streaming: FeishuAdapter.send_clarify patched ✓ (clarify interactive card)")
        except AttributeError:
            _logger.debug("hermes-lark-streaming: FeishuAdapter.send_clarify not found, clarify card skipped")
        try:
            FeishuAdapter._on_card_action_trigger = _wrap_feishu_card_action_trigger(FeishuAdapter._on_card_action_trigger)
            _logger.info("hermes-lark-streaming: FeishuAdapter._on_card_action_trigger patched ✓ (clarify card callback)")
        except AttributeError:
            _logger.debug("hermes-lark-streaming: FeishuAdapter._on_card_action_trigger not found, clarify callback skipped")

        feishu_patched = True
        _logger.info("hermes-lark-streaming: FeishuAdapter.send/edit/reaction/image/clarify patched ✓ (gateway message cards enabled)")
    except (ImportError, AttributeError) as e:
        _logger.info("hermes-lark-streaming: FeishuAdapter patch skipped (%s)", e)

    # ── Summary ──
    _logger.info(
        "hermes-lark-streaming v%s: patch summary — "
        "GatewayRunner=%s, conversation_loop=%s, AIAgent=applied, cron=%s, "
        "background=%s, FeishuAdapter=%s",
        __version__,
        "✓" if gw_patched else ("pending (delayed poll)" if gw_delayed else "✗"),
        "✓" if _module_patch_applied else "n/a (direct AIAgent used)",
        "✓" if cron_patched else "n/a",
        "✓" if gw_patched else ("pending" if gw_delayed else "n/a"),  # background task patch is part of GatewayRunner
        "✓" if feishu_patched else "✗",
    )

    # Deferred direct patch: retry AIAgent.run_conversation after Hermes
    # finishes loading all modules (belt-and-suspenders for lazy imports)
    _schedule_direct_patch()


def _schedule_direct_patch() -> None:
    """Schedule _apply_direct_agent_patch to run after Hermes finishes loading."""
    import threading

    def _delayed_patch():
        import time
        time.sleep(2)  # Wait for Hermes to finish loading
        _apply_direct_agent_patch()

    t = threading.Thread(target=_delayed_patch, daemon=True)
    t.start()
    _logger.info("hermes-lark-streaming: scheduled direct agent patch (2s delay)")


def _apply_direct_agent_patch() -> None:
    """Directly patch AIAgent.run_conversation as belt-and-suspenders.

    The module-level agent.conversation_loop.run_conversation patch should
    suffice, but in some Hermes runtimes the module attribute replacement
    doesn't propagate to the AIAgent method's lazy import.  This function
    patches the instance method directly.
    """
    try:
        from run_agent import AIAgent

        _orig_method = AIAgent.run_conversation

        # Guard: skip if already patched
        if getattr(_orig_method, "_hls_direct_patched", False):
            _logger.info("hermes-lark-streaming: AIAgent.run_conversation already directly patched, skip")
            return

        def _patched_run_conversation(
            self,
            user_message,
            system_message=None,
            conversation_history=None,
            task_id=None,
            stream_callback=None,
            persist_user_message=None,
            **kwargs,
        ):
            # ── inject_time: prepend current time to user_message ──
            user_message, persist_user_message = _inject_time_prefix(
                user_message, persist_user_message
            )

            _maybe_wrap_callbacks(self)
            try:
                return _orig_method(
                    self,
                    user_message,
                    system_message,
                    conversation_history,
                    task_id,
                    stream_callback,
                    persist_user_message,
                    **kwargs,
                )
            finally:
                # Always reset the re-entrancy guard so the next message
                # in the same thread can be injected again.
                _inject_time_guard.active = False

        _patched_run_conversation._hls_direct_patched = True
        AIAgent.run_conversation = _patched_run_conversation
        _logger.info("hermes-lark-streaming: AIAgent.run_conversation patched directly")
    except ImportError:
        _logger.info("hermes-lark-streaming: AIAgent.run_conversation direct patch deferred (run_agent not yet loaded)")
