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

from .. import __version__


__all__ = [
    # Shared state
    '_thread_local_ctx',
    '_logger',
    '_config',
    '_msg_ctx',
    '_started_msg_ids',
    '_started_msg_ids_lock',
    '_gateway_cards',
    '_gateway_cards_lock',
    '_gw_runner_patched',
    '_inject_time_guard',
    # Functions
    '_get_config',
    '_get_event_message_id',
    '_get_thread_local_ctx',
    '_resolve_hermes_agent_module',
    '_detect_hermes_layout',
    '_apply_gateway_runner_patches',
    'apply_patches',
    '_schedule_direct_patch',
    '_apply_direct_agent_patch',
    # From gateway
    '_wrap_handle_message',
    '_wrap_handle_message_with_agent',
    '_wrap_run_agent',
    '_wrap_run_background_task',
    '_wrap_cron_deliver',
    '_inject_time_prefix',
    '_wrap_run_conversation',
    # From callbacks
    '_maybe_wrap_callbacks',
    # From adapter
    '_classify_gateway_message',
    '_wrap_feishu_adapter_send',
    '_register_gateway_card',
    '_unregister_gateway_card',
    '_wrap_feishu_adapter_edit',
    '_wrap_feishu_adapter_add_reaction',
    '_wrap_feishu_adapter_delete_reaction',
    '_wrap_feishu_adapter_send_clarify',
    '_wrap_feishu_card_action_trigger',
    '_handle_clarify_card_action',
    '_REACTION_STATUS_MAP',
    '_clarify_choices',
    '_clarify_questions',
    '_clarify_card_msg_ids',
    '_clarify_selections',
    '_clarify_answers',
    '_clarify_card_info',
]


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
        from ..config import Config
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

# ── Thread-local re-entrancy guard for _inject_time_prefix ───────────
# When both the module-level patch and the direct AIAgent patch are active,
# AIAgent.run_conversation → (direct patch) _inject_time_prefix → orig →
# agent.conversation_loop.run_conversation → (module patch) _inject_time_prefix.
# The guard prevents the second call from injecting the prefix again.
_inject_time_guard = threading.local()


def _get_event_message_id() -> str | None:
    ctx = _msg_ctx.get()
    if ctx is None:
        ctx = _get_thread_local_ctx()
    if ctx is None:
        return None
    return ctx.get("event_message_id")


def _get_thread_local_ctx() -> dict | None:
    return getattr(_thread_local_ctx, "data", None)


# ── Import wrapper functions from sub-modules ──────────────────────
# These imports must come AFTER shared state is defined to avoid circular
# import issues (sub-modules import shared state from this module).
# The sub-modules are:
#   monkey_patch_gateway   — GatewayRunner wrappers, inject_time, cron
#   monkey_patch_callbacks — _maybe_wrap_callbacks and inner wrappers
#   monkey_patch_adapter   — FeishuAdapter wrappers, clarify cards

from .gateway import (  # noqa: E402
    _wrap_handle_message,
    _wrap_handle_message_with_agent,
    _wrap_run_agent,
    _wrap_run_background_task,
    _wrap_cron_deliver,
    _inject_time_prefix,
    _wrap_run_conversation,
)
from .callbacks import (  # noqa: E402
    _maybe_wrap_callbacks,
)
from .adapter import (  # noqa: E402
    _classify_gateway_message,
    _wrap_feishu_adapter_send,
    _register_gateway_card,
    _unregister_gateway_card,
    _wrap_feishu_adapter_edit,
    _wrap_feishu_adapter_add_reaction,
    _wrap_feishu_adapter_delete_reaction,
    _wrap_feishu_adapter_send_clarify,
    _wrap_feishu_card_action_trigger,
    _handle_clarify_card_action,
    _REACTION_STATUS_MAP,
    _clarify_choices,
    _clarify_questions,
    _clarify_card_msg_ids,
    _clarify_selections,
    _clarify_answers,
    _clarify_card_info,
)


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

    # ── TEMPORARY: Bridge media_delivery_allow_dirs → HERMES_MEDIA_ALLOW_DIRS ──
    # Hermes's BasePlatformAdapter reads HERMES_MEDIA_ALLOW_DIRS from os.environ
    # to validate file paths in <MEDIA> tags, but the gateway startup does NOT
    # bridge the config.yaml `media_delivery_allow_dirs` setting to this env var.
    # This means MEDIA file uploads fail after a gateway restart even when the
    # user has configured the directories correctly.
    #
    # This bridge reads the config and sets the env var if it's not already set.
    # TODO: Remove this block once Hermes fixes this upstream — the gateway
    # should bridge its own config to the env var on startup.
    try:
        _existing_media_dirs = os.environ.get("HERMES_MEDIA_ALLOW_DIRS")
        if not _existing_media_dirs:
            from ..config import Config as _BridgeConfig
            _bridge_cfg = _BridgeConfig()
            _bridge_raw = _bridge_cfg._load()
            _media_dirs = _bridge_raw.get("media_delivery_allow_dirs")
            if _media_dirs:
                if isinstance(_media_dirs, list):
                    _media_dirs_str = ":".join(str(d) for d in _media_dirs)
                else:
                    _media_dirs_str = str(_media_dirs)
                os.environ["HERMES_MEDIA_ALLOW_DIRS"] = _media_dirs_str
                _logger.info(
                    "hermes-lark-streaming: bridged media_delivery_allow_dirs → "
                    "HERMES_MEDIA_ALLOW_DIRS=%s (temporary fix, remove when Hermes fixes upstream)",
                    _media_dirs_str,
                )
    except Exception:
        _logger.debug("media_delivery_allow_dirs bridge failed", exc_info=True)

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
        # NOTE(v0.15.4): send_image_file / send_image interceptors DELETED (2026-06-09).
        # The v0.15.3 interception was fundamentally broken — it injected file:// URLs
        # into session.text.on_partial() which were then stripped by
        # _strip_invalid_image_keys(), and suppressed the original standalone
        # send, causing images to disappear entirely.
        # Images are now sent as standalone messages (pre-v0.15.3 behavior).
        # The three zombie functions (_try_add_image_to_session,
        # _wrap_feishu_adapter_send_image_file, _wrap_feishu_adapter_send_image)
        # have been fully removed from monkey_patch.py.

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
