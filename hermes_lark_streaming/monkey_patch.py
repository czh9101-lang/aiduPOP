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
                # 此时应该显示"已停止"而非"已完成"。
                is_interrupted = result.get("interrupted", False) or result.get("partial", False)

                card_sent = on_message_completed(
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

    try:
        from gateway.cron.scheduler import Scheduler  # noqa: F401
        layout["has_cron_scheduler"] = True
    except (ImportError, AttributeError):
        try:
            from cron.scheduler import Scheduler  # noqa: F401
            layout["has_cron_scheduler"] = True
        except (ImportError, AttributeError):
            pass

    _logger.info(
        "hermes-lark-streaming: Hermes layout probe → %s",
        layout,
    )
    return layout


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

    # ── Probe Hermes layout ──
    layout = _detect_hermes_layout()

    # ── Patch GatewayRunner ──
    # This is the core patch — without it, streaming cards cannot work.
    gw_patched = False
    if layout["has_gateway_run"]:
        try:
            from gateway.run import GatewayRunner

            GatewayRunner._handle_message = _wrap_handle_message(GatewayRunner._handle_message)
            GatewayRunner._handle_message_with_agent = _wrap_handle_message_with_agent(
                GatewayRunner._handle_message_with_agent
            )
            GatewayRunner._run_agent = _wrap_run_agent(GatewayRunner._run_agent)
            gw_patched = True
            _logger.info("hermes-lark-streaming: GatewayRunner patched ✓")
        except (ImportError, AttributeError) as e:
            _logger.error(
                "hermes-lark-streaming: GatewayRunner patch FAILED — "
                "gateway.run found but incompatible. "
                "Streaming cards will NOT work. Error: %s", e,
            )
    else:
        _logger.error(
            "hermes-lark-streaming: gateway.run NOT FOUND — "
            "this Hermes version may be too old or installed incorrectly. "
            "Streaming cards will NOT work. "
            "Please check: 1) Hermes is running via gateway mode, "
            "2) Hermes version >= v0.5.0, "
            "3) Re-run: hermes setup && hermes gateway start",
        )

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
    cron_patched = False
    if layout["has_cron_scheduler"]:
        try:
            from gateway.cron.scheduler import Scheduler

            Scheduler._deliver_result = _wrap_cron_deliver(
                Scheduler._deliver_result
            )
            cron_patched = True
            _logger.info("hermes-lark-streaming: cron scheduler patched ✓")
        except (ImportError, AttributeError):
            pass
        if not cron_patched:
            try:
                from cron.scheduler import Scheduler  # alternative import path

                Scheduler._deliver_result = _wrap_cron_deliver(
                    Scheduler._deliver_result
                )
                cron_patched = True
                _logger.info("hermes-lark-streaming: cron scheduler patched (alt path) ✓")
            except (ImportError, AttributeError):
                _logger.info("hermes-lark-streaming: cron scheduler not found, cron cards disabled")

    # ── Summary ──
    _logger.info(
        "hermes-lark-streaming: patch summary — "
        "GatewayRunner=%s, conversation_loop=%s, AIAgent=applied, cron=%s",
        "✓" if gw_patched else "✗",
        "✓" if _module_patch_applied else "n/a (direct AIAgent used)",
        "✓" if cron_patched else "n/a",
    )

    # Deferred direct patch: retry AIAgent.run_conversation after Hermes
    # finishes loading all modules (belt-and-suspenders for lazy imports)
    _schedule_direct_patch()


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
