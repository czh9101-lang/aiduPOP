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
    Scheduler._deliver_result                → redirect cron Feishu deliveries to CardKit

Message context (``message_id``, ``event_message_id``, ``chat_id``, …) is
propagated through a ``contextvars.ContextVar`` — safe within a single async
task execution context.
"""

from __future__ import annotations

import contextvars
import functools
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable


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
        _msg_ctx.set(
            {
                "message_id": mid,
                "chat_id": chat_id,
                "anchor_id": anchor_id,
                "event_message_id": "",  # filled by _wrap_run_agent
                "card_sent": False,
                "_msg_start_time": time.monotonic(),  # 自计时：替代无法获取的 _response_time 局部变量
            }
        )

        result = await orig(self, event, source, *args, **kwargs)

        # ── CARD ALREADY SENT → suppress Hermes reply ──
        # Runtime wrapping cannot modify gateway internals like the old
        # AST injection, so we return None to simulate "stale agent result",
        # causing Hermes to skip the text reply.
        if result is not None:
            ctx = _msg_ctx.get()
            if ctx and ctx.get("card_sent"):
                _logger.info(
                    "card already sent for msg=%s, suppressing gateway reply",
                    mid[:12],
                )
                _started_msg_ids.discard(mid)
                return None

        # ── ABORT / INTERRUPT detection ──
        # When card was already sent, _handle_message_with_agent returns
        # None (the "Discarding stale agent result" path).
        if result is None:
            ctx = _msg_ctx.get()
            if ctx and ctx.get("card_sent"):
                # Card was sent successfully via on_message_completed.
                # Only fire interrupt if a newer message started after this one.
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
        _started_msg_ids.discard(mid)

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
        ctx = _msg_ctx.get()
        if ctx is not None and event_message_id:
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
        ctx = _msg_ctx.get()
        if ctx is not None:
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
                # 此时应该显示“已停止”而非“已完成”。
                is_interrupted = result.get("interrupted", False) or result.get("partial", False)

                if is_interrupted:
                    message_id=ctx["message_id"],
                    answer=result.get("final_response", ""),
                    duration=_elapsed,
                    model=result.get("model", ""),
                    tokens={
                        "input_tokens": result.get("input_tokens", 0),
                        "output_tokens": result.get("output_tokens", 0),
                    },
                    context={
                        "used_tokens": result.get("last_prompt_tokens", 0),
                        "max_tokens": result.get("context_length", 0),
                    },
                    api_calls=result.get("api_calls", 0),
                    history_offset=result.get("history_offset", 0),
                    compression_exhausted=result.get("compression_exhausted", False),
                    aborted=is_interrupted,
                    error_message=result.get("error") or result.get("interrupt_message", ""),
                )
                if card_sent:
                    result["already_sent"] = True
                    ctx["card_sent"] = True
            except Exception:
                pass

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
    Both are prefixed with ``[HH:MM:SS CST] `` so the DB-stored content
    matches what the API received — preserving prefix cache consistency.

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
    time_prefix = f"[{now.strftime('%H:%M:%S')} CST] "

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
    (``[HH:MM:SS CST] ``) to ``user_message`` so the model can perceive
    the current time without calling the ``date`` tool.

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
    _logger.info("HLS_CALLED: _maybe_wrap_callbacks invoked, has_stream=%s, eid_lookup=%s", bool(getattr(agent, "stream_delta_callback", None)), bool(_get_event_message_id()))

    eid = _get_event_message_id()
    if not eid:
        _logger.info("HLS_CALLED: skip — no event_message_id in ctx")
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
    _current_stream = getattr(agent, "stream_delta_callback", None)
    _current_interim = getattr(agent, "interim_assistant_callback", None)
    _current_tool = getattr(agent, "tool_progress_callback", None)
    _current_reasoning = getattr(agent, "reasoning_callback", None)
    _current_bg = getattr(agent, "background_review_callback", None)
    _logger.info(
        "HLS_WRAP: guard check stream=%s(hls=%s) interim=%s tool=%s reasoning=%s bg=%s eid=%s",
        bool(_current_stream),
        getattr(_current_stream, "_hls_wrapper", False) if _current_stream else "N/A",
        bool(_current_interim),
        bool(_current_tool),
        bool(_current_reasoning),
        bool(_current_bg),
        eid[:12] if eid else "?",
    )
    if _current_stream and getattr(_current_stream, "_hls_wrapper", False):
        _logger.info("HLS_WRAP: guard SKIP — stream_delta already wrapped")
        return

    # ── ANSWER: wrap stream_delta_callback ──
    if getattr(agent, "stream_delta_callback", None):
        _orig = agent.stream_delta_callback

        def _answer_wrapper(text, *args, **kwargs):
            try:
                from .patch import on_answer_delta

                if text and on_answer_delta(message_id=eid, text=text):
                    _logger.debug(
                        "answer_wrapper: consumed text len=%d eid=%s",
                        len(text), eid[:12],
                    )
                    return
                else:
                    _logger.debug(
                        "answer_wrapper: passed through (text=%r) eid=%s",
                        bool(text), eid[:12],
                    )
            except Exception:
                _logger.debug("answer_wrapper: exception", exc_info=True)
            return _orig(text, *args, **kwargs)

        agent.stream_delta_callback = _answer_wrapper
        _logger.debug("_maybe_wrap_callbacks: stream_delta_callback wrapped")
    else:
        _logger.debug("_maybe_wrap_callbacks: NO stream_delta_callback on agent")

    # ── THINKING: 不包裹 interim_assistant_callback ──
    # 原因：Hermes 内部 on_thinking 和 on_answer 可能处理同一段文本，
    # 原版 AST 注入有 already_streamed 守卫防重，但 monkey patch 无法访问该参数。
    # 如果两层都包裹，会导致内容重复显示（thinking 一次 + answer 一次）。
    # 思考内容仍由 reasoning_callback（原生模型推理）处理。
    # 详见：Bug fix 1 — 重复内容问题

    # ── TOOL: wrap tool_progress_callback ──
    if getattr(agent, "tool_progress_callback", None):
        _orig = agent.tool_progress_callback

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
            return _orig(event_type, tool_name, preview, *args, **kwargs)

        agent.tool_progress_callback = _tool_wrapper

    # Mark wrapper functions so guard can detect them next time
    if getattr(agent, "stream_delta_callback", None):
        setattr(agent.stream_delta_callback, "_hls_wrapper", True)
    # 不再标记 interim_assistant_callback（未包裹）
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
        _orig = agent.background_review_callback

        def _bg_wrapper(message, *args, **kwargs):
            try:
                from .patch import on_background_review_message

                deferred = on_background_review_message(
                    message_id=eid,
                    text=message,
                    sender=_orig,
                )
                if deferred:
                    return
            except Exception:
                pass
            return _orig(message, *args, **kwargs)

        agent.background_review_callback = _bg_wrapper


# ── Cron delivery wrapper ──────────────────────────────────────────


def _wrap_cron_deliver(orig: Callable) -> Callable:
    """Intercept cron delivery and redirect to Feishu CardKit cards."""

    @functools.wraps(orig)
    async def wrapper(
        self,
        platform_name,
        chat_id,
        cleaned_delivery_content,
        loop=None,
        **_kwargs,
    ):
        if platform_name.lower() in ("feishu", "lark"):
            try:
                from .patch import on_cron_deliver

                if on_cron_deliver(
                    chat_id=chat_id,
                    content=cleaned_delivery_content.strip(),
                    loop=loop,
                ):
                    return True
            except Exception:
                _logger.debug("cron deliver hook failed", exc_info=True)
        return await orig(
            self,
            platform_name,
            chat_id,
            cleaned_delivery_content,
            loop=loop,
            **_kwargs,
        )

    return wrapper


# ── Public entry point ─────────────────────────────────────────────


def apply_patches() -> None:
    """Apply all runtime monkey patches to ``GatewayRunner`` and ``AIAgent``.

    Call exactly once during plugin loading (from ``plugin.register()``).
    Idempotent — protected by a module-level flag.
    """
    if getattr(apply_patches, "_applied", False):
        return
    apply_patches._applied = True  # type: ignore[attr-defined]

    from gateway.run import GatewayRunner

    GatewayRunner._handle_message = _wrap_handle_message(GatewayRunner._handle_message)
    GatewayRunner._handle_message_with_agent = _wrap_handle_message_with_agent(
        GatewayRunner._handle_message_with_agent
    )
    GatewayRunner._run_agent = _wrap_run_agent(GatewayRunner._run_agent)

    from agent.conversation_loop import run_conversation as _cl_run_conversation

    import agent.conversation_loop as _cl_mod
    _cl_mod.run_conversation = _wrap_run_conversation(_cl_run_conversation)

    # Also try to patch AIAgent.run_conversation directly (belt-and-suspenders)
    # This ensures _maybe_wrap_callbacks is called even if the module-level
    # patch doesn't take effect in the running process.
    _apply_direct_agent_patch()

    # ── Cron scheduler (graceful if not found) ──
    try:
        from gateway.cron.scheduler import Scheduler

        Scheduler._deliver_result = _wrap_cron_deliver(
            Scheduler._deliver_result
        )
        _logger.info("hermes-lark-streaming: cron scheduler patched")
    except (ImportError, AttributeError):
        try:
            from cron.scheduler import Scheduler  # alternative import path

            Scheduler._deliver_result = _wrap_cron_deliver(
                Scheduler._deliver_result
            )
            _logger.info("hermes-lark-streaming: cron scheduler patched (alt path)")
        except (ImportError, AttributeError):
            _logger.info("hermes-lark-streaming: cron scheduler not found, cron cards disabled")

    _logger.info("hermes-lark-streaming: all runtime patches applied")
    _logger.info("hermes-lark-streaming: about to call _schedule_direct_patch")

    # Deferred direct patch: retry AIAgent.run_conversation after Hermes
    # finishes loading all modules
    _schedule_direct_patch()
    _logger.info("hermes-lark-streaming: _schedule_direct_patch returned")


def _schedule_direct_patch() -> None:
    """Schedule _apply_direct_agent_patch to run after Hermes finishes loading."""
    import threading

    def _delayed_patch():
        import time
        time.sleep(5)  # Wait for Hermes to finish loading
        _apply_direct_agent_patch()

    t = threading.Thread(target=_delayed_patch, daemon=True)
    t.start()
    _logger.info("hermes-lark-streaming: scheduled direct agent patch (5s delay)")


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
