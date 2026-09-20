"""FeishuAdapter interception layer — send, edit, reactions, and clarify cards."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
import time
from contextvars import ContextVar
from typing import Any, Callable

from .. import __version__
from . import (
    _msg_ctx,
    _gateway_cards,
    _gateway_cards_lock,
    _logger,
    _get_config,
    _patched_feishu_classes,
)

# ── FeishuAdapter interception layer (Phase 1: gateway message cards) ─

# A task-local escape hatch for interactive paths whose transport contract is
# stricter than a top-level gateway notice.  It is set and reset around the
# awaited host call so concurrent gateway sends cannot accidentally inherit it.
_preserve_host_send_routing: ContextVar[bool] = ContextVar(
    "hls_preserve_host_send_routing", default=False
)

def _classify_gateway_message(content: str) -> str:
    """Classify a gateway-internal message by its content for card category."""
    if not isinstance(content, str):
        return "system"
    # Auth / pairing messages
    if any(kw in content for kw in ("pairing code", "pairing requests", "配对码", "I don't recognize you")):
        return "auth"
    # v1.8.0 (P3-4): Gateway lifecycle notices — informational, NOT errors.
    # hermes _notify_active_sessions_of_shutdown (gateway/run.py) hardcodes
    # "⚠️ Gateway restarting — …" / "⚠️ Gateway shutting down — …"; the
    # drain path sends "⏳ Gateway restarting — queued for the next turn…"
    # and zh locales use "♻ 正在重启网关…" / "⏳ 正在等待 N 个活跃代理结束
    # 后重启…". The ⚠️ prefix made every one of them land in the "error"
    # bucket below — production 2026-08 audit: 13/13 planned restarts were
    # misclassified as errors in the card registry / logs.
    if any(kw in content for kw in (
        "Gateway restarting", "Gateway shutting down", "Gateway stopping",
        "queued for the next turn", "not accepting another turn",
        "Draining", "正在重启网关", "活跃代理结束后重启",
    )):
        return "lifecycle"
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
    """Intercept ``FeishuAdapter.send()`` — convert text to gateway cards."""
    async def _intercepted_send(self_feishu, chat_id, content, reply_to=None, metadata=None, **kwargs):
        # On-demand repatch: if this adapter instance's class isn't patched yet
        # (deferred loading edge case), patch it now. O(1) set lookup.
        _cls = type(self_feishu)
        if id(_cls) not in _patched_feishu_classes:
            from . import _apply_feishu_adapter_patches
            _apply_feishu_adapter_patches(_cls, is_repatch=True)

        # Interactive completion prose has a stricter routing contract than a
        # gateway notice: it must stay attached to the source message (or to
        # the confirmation card when that is the only available anchor).  The
        # gateway-card path below intentionally has no reply/thread arguments
        # and therefore cannot satisfy that contract.  This task-local flag is
        # set by narrowly-scoped interactive paths and is deliberately
        # invisible to Hermes's original ``send`` signature.
        if _preserve_host_send_routing.get():
            return await orig_send(
                self_feishu,
                chat_id,
                content,
                reply_to=reply_to,
                metadata=metadata,
                **kwargs,
            )

        # ── EphemeralReply passthrough (v1.3.1 fix) ──
        # NOT duplicate agent replies. They must NEVER be suppressed by the
        try:
            from gateway.platforms.base import EphemeralReply
            if isinstance(content, EphemeralReply):
                return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)
        except (ImportError, AttributeError):
            pass  # EphemeralReply not available in this Hermes version

        if not isinstance(content, str):
            return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)

        # ── Guard: skip empty content ──
        if not content.strip():
            return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)

        _text_content = content

        if getattr(self_feishu, "_hls_cron_sending", 0) or getattr(self_feishu, "_hls_bg_sending", 0):
            return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)

        # ── Agent path: suppress duplicate text reply ──
        ctx = _msg_ctx.get(None)
        if ctx is not None:
            eid = ctx.get("event_message_id", "")
            if eid:
                # We're inside an agent message pipeline.
                # If card was already sent, suppress the gateway's text reply.
                if ctx.get("card_sent"):
                    try:
                        from gateway.platforms.base import SendResult
                        return SendResult(success=True)
                    except (ImportError, AttributeError):
                        return None
                else:
                    try:
                        from ..controller import get_controller
                        _ctrl = get_controller()
                        if _ctrl and _ctrl.enabled:
                            _sess = _ctrl._sess_get(eid)
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
                        _logger.debug("HLS: suppressed exception", exc_info=True)
                    # Agent still running, card not yet sent — don't interfere
                    return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)

        # v1.3.2 fix (B3-04): the previous detection used a bare substring
        _stripped = content.strip()
        _is_stop_response = (
            len(_stripped) < 50  # /stop response is always short
            and _stripped.startswith("⚡")
            and any(kw in _stripped for kw in ("已停止", "stopped", "Stopped"))
        )
        if _is_stop_response:
            try:
                from ..controller import get_controller
                _ctrl = get_controller()
                if _ctrl and _ctrl.enabled:
                    # Find an active streaming session in this chat
                    for _sess in _ctrl._sess_values_snapshot():
                        if (
                            _sess.chat_id == chat_id
                            and _sess.state in ("streaming", "creating", "idle")
                            and _sess.card_msg_id
                        ):
                            _logger.info(
                                "gateway_send: /stop response detected, aborting "
                                "streaming card for msg=%s (state=%s)",
                                (_sess.message_id or "?")[:12],
                                _sess.state,
                            )
                            try:
                                from .hooks import on_message_aborted
                                on_message_aborted(message_id=_sess.message_id)
                            except Exception:
                                _logger.debug("HLS: suppressed exception", exc_info=True)
                            # Suppress the "⚡ 已停止" gateway card —
                            # the streaming card will show the stopped state.
                            try:
                                from gateway.platforms.base import SendResult
                                return SendResult(success=True)
                            except (ImportError, AttributeError):
                                return None
            except Exception:
                _logger.debug("HLS: suppressed exception", exc_info=True)
        _logger.info(
            "gateway_send: entering gateway-internal path, chat=%s content_len=%d",
            chat_id[:12] if chat_id else "?",
            len(content),
        )
        try:
            from ..controller import get_controller
            ctrl = get_controller()
            if ctrl and ctrl.enabled:
                # Check if gateway_cards feature is enabled
                cfg = _get_config()
                if not cfg.gateway_cards:
                    _logger.info("gateway_send: gateway_cards disabled, falling back to plain text")
                    return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)

                cleaned = _text_content
                if not cleaned.strip():
                    cleaned = content
                if not cleaned.strip():
                    return await orig_send(self_feishu, chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs)

                category = _classify_gateway_message(cleaned or content)
                card_msg_id, card_id = await ctrl._do_gateway_deliver(
                    chat_id, cleaned.strip() if cleaned.strip() else content,
                    category=category,
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
            "registered_at": time.time(),
        }
        # v1.3.1: prune oldest entries when over capacity
        _GATEWAY_CARDS_MAX = 500
        if len(_gateway_cards) > _GATEWAY_CARDS_MAX:
            # Sort by registered_at, remove oldest 20% to amortize prune cost
            excess = len(_gateway_cards) - _GATEWAY_CARDS_MAX + (_GATEWAY_CARDS_MAX // 5)
            sorted_keys = sorted(_gateway_cards, key=lambda k: _gateway_cards[k].get("registered_at", 0))
            for k in sorted_keys[:excess]:
                _gateway_cards.pop(k, None)
            _logger.debug("HLS: _gateway_cards pruned %d entries (was %d)", excess, len(_gateway_cards) + excess)

def _unregister_gateway_card(card_msg_id: str) -> None:
    """Remove a gateway card from the registry."""
    with _gateway_cards_lock:
        _gateway_cards.pop(card_msg_id, None)

def _wrap_feishu_adapter_edit(orig_edit: Callable) -> Callable:
    """Intercept ``FeishuAdapter.edit_message()`` — update gateway card content."""
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
                from ..controller import get_controller
                ctrl = get_controller()
                if ctrl and ctrl.enabled:
                    # Check if gateway_cards feature is enabled
                    cfg = _get_config()
                    if cfg.gateway_cards:
                        cleaned = content
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
                _logger.debug("HLS: edit interception failed", exc_info=True)

        # ── Fallback: original edit_message ──
        _fallback_kwargs = {k: v for k, v in kwargs.items() if k != "metadata"}
        try:
            return await orig_edit(self_feishu, chat_id, message_id, content, **_fallback_kwargs)
        except TypeError:
            # If the original still rejects kwargs, try with no extra kwargs
            return await orig_edit(self_feishu, chat_id, message_id, content)

    return _intercepted_edit

# ── Reaction → card status indicator (Phase 3) ─────────────────────

# Map Feishu reaction emojis to human-readable status labels.
# v2.2.0 泡波样式（猴哥拍板，决策表③）；数据源 = theme 层。
# 注: reaction 拦截默认关闭（嘟嘟定制），此映射为休眠数据，供开源启用方使用。
def _build_reaction_status_map() -> dict[str, str]:
    from ..cardkit.theme import get_theme  # lazy: patching→cardkit 不做顶层导入
    return dict(get_theme()["reactions"])

_REACTION_STATUS_MAP: dict[str, str] = _build_reaction_status_map()

def _wrap_feishu_adapter_add_reaction(orig_add_reaction: Callable) -> Callable:
    """Intercept ``FeishuAdapter.add_reaction()`` — card status indicator."""
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
                    from ..controller import get_controller
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
                    _logger.debug("HLS: add_reaction interception failed", exc_info=True)

        # ── Fallback: original add_reaction ──
        return await orig_add_reaction(self_feishu, message_id, emoji, **kwargs)

    return _intercepted_add_reaction

def _wrap_feishu_adapter_delete_reaction(orig_delete_reaction: Callable) -> Callable:
    """Intercept ``FeishuAdapter.delete_reaction()`` — clear card status."""
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
                    from ..controller import get_controller
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
                    _logger.debug("HLS: delete_reaction interception failed", exc_info=True)

        # ── Fallback: original delete_reaction ──
        return await orig_delete_reaction(self_feishu, message_id, emoji, **kwargs)

    return _intercepted_delete_reaction

_clarify_lock = threading.Lock()
_clarify_choices: dict[str, list[str]] = {}  # clarify_id → choices list (normalized)
_clarify_questions: dict[str, str] = {}  # clarify_id → question text
_clarify_card_msg_ids: dict[str, str] = {}  # clarify_id → card_msg_id (for server-side confirm update)
_clarify_selections: dict[str, str] = {}  # clarify_id → user's selected/input text (for retry)
_clarify_timestamps: dict[str, float] = {}  # clarify_id → creation time (for TTL cleanup)
_CLARIFY_TTL_SEC = 30 * 60  # 30 分钟后未确认的追问自动清除

# Backward-compatible aliases (old names used in tests)
_clarify_answers = _clarify_selections  # noqa: F841
_clarify_card_info = _clarify_card_msg_ids  # noqa: F841

def _prune_expired_clarify() -> None:
    """清理过期的追问数据（超过 _CLARIFY_TTL_SEC 未确认的条目）."""
    with _clarify_lock:
        if not _clarify_timestamps:
            return
        now = time.time()
        expired = [cid for cid, ts in _clarify_timestamps.items() if now - ts > _CLARIFY_TTL_SEC]
        for cid in expired:
            _clarify_choices.pop(cid, None)
            _clarify_questions.pop(cid, None)
            _clarify_card_msg_ids.pop(cid, None)
            _clarify_selections.pop(cid, None)
            _clarify_timestamps.pop(cid, None)
        if expired:
            _logger.debug("HLS: pruned %d expired clarify entries", len(expired))

def _wrap_feishu_adapter_send_clarify(orig_send_clarify: Callable) -> Callable:
    """Intercept ``FeishuAdapter.send_clarify()`` — render interactive card."""

    async def _intercepted_send_clarify(
        self_feishu, chat_id, question, choices, clarify_id, session_key, metadata=None, **kwargs
    ):
        # 🔥 v1.5.1 fix: on-demand repatch — 如果 _status_adapter 是另一个 class identity
        # (hermes_plugins vs plugins namespace)，补丁没挂上 → 这里当场补挂
        _cls = type(self_feishu)
        if id(_cls) not in _patched_feishu_classes:
            from . import _apply_feishu_adapter_patches
            _apply_feishu_adapter_patches(_cls, is_repatch=True)

        _logger.info(
            "clarify card: send_clarify intercepted chat=%s question=%r choices=%s clarify_id=%s",
            (chat_id or "?")[:12],
            question[:50] if question else "",
            choices,
            (clarify_id or "?")[:12],
        )

        # Prune expired clarify data before creating new entries
        _prune_expired_clarify()

        try:
            from ..controller import get_controller
            ctrl = get_controller()
            if not ctrl or not ctrl.enabled or not ctrl._client_ok():
                _logger.debug("clarify card: controller not available, falling back to text")
                return await orig_send_clarify(
                    self_feishu, chat_id, question, choices, clarify_id, session_key,
                    metadata=metadata, **kwargs
                )

            # v1.3.0 fix: Flush + cancel pending timers BEFORE sending clarify card.
            # Fix: find the active streaming session for this chat_id, cancel its
            try:
                for _mid, _sess in ctrl._sess_items_snapshot():
                    if _sess.chat_id == chat_id and not _sess.is_terminal_phase:
                        if _sess.unified_state and _sess.unified_state.has_dirty:
                            _logger.info(
                                "clarify card: flushing pending answer before clarify "
                                "msg=%s dirty=%s",
                                (_mid or "?")[:12],
                                bool(_sess.unified_state.answer_dirty),
                            )
                            # Force immediate flush and wait for completion.
                            # This cancels the pending timer and writes dirty data now.
                            await _sess.flush.flush_now(
                                lambda s=_sess: ctrl._do_unified_flush(s)
                            )
                        else:
                            # No dirty data — just cancel the pending timer so the
                            # streaming card stops updating while the clarify is shown.
                            _sess.flush._cancel_timer()
                        break
            except Exception:
                _logger.debug("clarify card: pre-flush failed (non-fatal)", exc_info=True)

            from ..cardkit import build_clarify_card, normalize_clarify_choices

            normalized = normalize_clarify_choices(choices) if choices else None

            card = build_clarify_card(
                question=question,
                choices=normalized,
                clarify_id=clarify_id,
            )

            # Store normalized choices and question for callback lookup
            with _clarify_lock:
                if normalized:
                    _clarify_choices[clarify_id] = list(normalized)
                _clarify_questions[clarify_id] = question
                _clarify_timestamps[clarify_id] = time.time()

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

            # Store card_msg_id for server-side confirm update
            with _clarify_lock:
                if card_msg_id:
                    _clarify_card_msg_ids[clarify_id] = card_msg_id

            # Register the card in gateway card registry (for edit tracking)
            _register_gateway_card(card_msg_id, chat_id=chat_id, card_id=None, category="clarify")

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

def _safe_action_value_repr(action_value: Any) -> str:
    """Safely repr an action_value dict for logging (truncated, no secrets)."""
    try:
        import json
        s = json.dumps(action_value, ensure_ascii=False, default=str)
        return s[:200]
    except Exception:
        return repr(action_value)[:200]

def _wrap_handle_card_action_event(original_method: Callable) -> Callable:
    """Wrap ``FeishuAdapter._handle_card_action_event`` — the REAL interception point."""

    async def _wrapped(self, data):
        event = getattr(data, "event", None)
        action = getattr(event, "action", None)
        action_value = getattr(action, "value", {}) or {}

        clarify_action = (
            action_value.get("hermes_clarify_action")
            if isinstance(action_value, dict) else None
        )

        if clarify_action:
            # didn't run (SDK holds stale bound method). Handle clarify resolution
            _cid = action_value.get("clarify_id", "") if isinstance(action_value, dict) else ""
            with _clarify_lock:
                _known_clarify = bool(
                    _cid
                    and (
                        _cid in _clarify_questions
                        or _cid in _clarify_choices
                        or _cid in _clarify_selections
                        or _cid in _clarify_card_msg_ids
                    )
                )
            if not _known_clarify:
                _logger.warning(
                    "HLS: clarify card action %r has no live plugin state; "
                    "falling back to original Feishu card action handler, clarify_id=%s",
                    clarify_action,
                    (_cid or "?")[:12],
                )
                return await original_method(self, data)
            _logger.info(
                "HLS: clarify card action %r reached _handle_card_action_event "
                "(SDK stale bound method path — _on_card_action_trigger wrapper "
                "bypassed), handling clarify resolution here, clarify_id=%s",
                clarify_action,
                (_cid or "?")[:12],
            )
            try:
                return _handle_clarify_card_action(self, data, clarify_action, action_value)
            except Exception:
                _logger.warning(
                    "HLS: clarify card action handling in _handle_card_action_event "
                    "failed; falling back to original handler, clarify_id=%s",
                    (_cid or "?")[:12],
                    exc_info=True,
                )
                return await original_method(self, data)

        # which Gateway rejects ("Unknown command /card"). Note: hermes_action /
        try:
            action_tag = str(getattr(action, "tag", "") or "button")
        except Exception:
            action_tag = "button"
        _logger.warning(
            "HLS: card action %r reached _handle_card_action_event — suppressing "
            "/card synthetic command (gateway would reject it). action_value=%s",
            action_tag,
            _safe_action_value_repr(action_value),
        )
        return  # suppress

    return _wrapped

def _schedule_clarify_split(adapter_instance, data: Any) -> None:
    """v2.6.0: clarify resolve 后调度流式卡切卡（clarify 前后输出分卡呈现）.

    从回调事件取 chat_id，在 event loop 上 fire-and-forget controller 的
    maybe_split_for_clarify。失败仅记 debug 日志（切卡是体验增强，不影响 resolve 主链路）。
    """
    try:
        event = getattr(data, "event", None)
        context = getattr(event, "context", None)
        chat_id = str(getattr(context, "open_chat_id", "") or "")
        if not chat_id:
            return

        from ..controller import get_controller
        ctrl = get_controller()
        if ctrl is None or not getattr(ctrl, "enabled", False):
            return

        loop = getattr(adapter_instance, "_loop", None) or ctrl._get_loop()
        if loop is None:
            return

        from agent.async_utils import safe_schedule_threadsafe

        def _do_split():
            ctrl.maybe_split_for_clarify(chat_id)

        # maybe_split_for_clarify 是同步方法（内部自行调度协程），包成协程跑在 loop 上
        async def _do_split_async():
            _do_split()

        safe_schedule_threadsafe(
            _do_split_async(), loop,
            logger=_logger,
            log_message="clarify split: schedule failed",
        )
        _logger.info("clarify split: scheduled chat=%s", chat_id[:12])
    except Exception:
        _logger.debug("clarify split: scheduling error (non-fatal)", exc_info=True)

async def _schedule_confirm_card(*, cid: str) -> None:
    """Server-side card update: soft-lock → hard-lock (confirmed state)."""
    # v1.3.2 fix (B3-05): removed redundant local `import asyncio` —
    # asyncio is already imported at module level.

    # Small delay to ensure the CallBackCard (submitted state) is processed first
    await asyncio.sleep(1.0)

    with _clarify_lock:
        card_msg_id = _clarify_card_msg_ids.get(cid, "")
        question = _clarify_questions.get(cid, "")
        selected = _clarify_selections.get(cid, "")

    def _cleanup():
        """Pop all stored entries for this clarify_id (idempotent)."""
        with _clarify_lock:
            _clarify_choices.pop(cid, None)
            _clarify_questions.pop(cid, None)
            _clarify_card_msg_ids.pop(cid, None)
            _clarify_selections.pop(cid, None)
            _clarify_timestamps.pop(cid, None)

    if not card_msg_id:
        _logger.warning(
            "clarify card: cannot confirm, no card_msg_id for clarify_id=%s",
            (cid or "?")[:12],
        )
        _cleanup()
        return

    if not selected:
        _logger.warning(
            "clarify card: cannot confirm, no stored selection for clarify_id=%s",
            (cid or "?")[:12],
        )
        _cleanup()
        return

    try:
        from ..cardkit import build_clarify_confirmed_card
        from ..controller import get_controller

        ctrl = get_controller()
        if not ctrl or not ctrl._client_ok():
            _logger.warning(
                "clarify card: cannot confirm, controller not available for clarify_id=%s",
                (cid or "?")[:12],
            )
            return

        card_data = build_clarify_confirmed_card(
            question=question, selected=selected,
        )
        await ctrl._client.update_card(card_msg_id, card_data)

        _logger.info(
            "clarify card: confirmed (hard lock) for clarify_id=%s card_msg_id=%s",
            (cid or "?")[:12],
            (card_msg_id or "?")[:12],
        )
    except Exception:
        _logger.warning(
            "clarify card: server-side confirm update failed for clarify_id=%s",
            (cid or "?")[:12],
            exc_info=True,
        )
    finally:
        # Always cleanup stored data after confirm attempt
        _cleanup()

def _handle_clarify_card_action(
    adapter_instance,
    data: Any,
    clarify_action: str,
    action_value: dict,
) -> Any:
    """Handle a clarify card action callback — three-state flow."""
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

    def _submitted_card_response(selected_text: str, choices_list: list[str] | None, q: str, cid: str):
        """Build a CallBackCard showing the soft-lock submitted state."""
        if P2CardActionTriggerResponse is None or CallBackCard is None:
            return _empty_response()
        from ..cardkit import build_clarify_submitted_card
        card_data = build_clarify_submitted_card(
            question=q, selected=selected_text,
            clarify_id=cid,
        )
        response = P2CardActionTriggerResponse()
        card = CallBackCard()
        card.type = "raw"
        card.data = card_data
        response.card = card
        return response

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

    # v1.3.0: snapshot question + choices atomically (used by all action branches)
    with _clarify_lock:
        question = _clarify_questions.get(clarify_id, "")
        choices = _clarify_choices.get(clarify_id) or None

    # ── Handle retry_submit action (re-send previous selection) ──
    if clarify_action == "retry_submit":
        with _clarify_lock:
            stored_selection = _clarify_selections.get(clarify_id, "")
        if not stored_selection:
            _logger.debug("clarify card: retry but no stored selection for clarify_id=%s", (clarify_id or "?")[:12])
            return _empty_response()

        _logger.info(
            "clarify card: retrying with selection '%s' for clarify_id=%s",
            stored_selection[:50],
            (clarify_id or "?")[:12],
        )

        # Re-resolve the clarify
        loop = getattr(adapter_instance, "_loop", None)
        if loop is not None:
            try:
                from tools.clarify_gateway import resolve_gateway_clarify
                from agent.async_utils import safe_schedule_threadsafe

                async def _do_retry_resolve():
                    resolve_gateway_clarify(clarify_id, stored_selection)
                    # Schedule server-side confirm update after retry
                    await _schedule_confirm_card(cid=clarify_id)

                safe_schedule_threadsafe(
                    _do_retry_resolve(), loop,
                    logger=_logger,
                    log_message="clarify card: failed to schedule retry resolve",
                    log_level=logging.WARNING,
                )
            except (ImportError, Exception) as e:
                _logger.warning("clarify card: retry resolve scheduling failed: %s", e)
                try:
                    from tools.clarify_gateway import resolve_gateway_clarify
                    resolve_gateway_clarify(clarify_id, stored_selection)
                except (ImportError, Exception) as e2:
                    _logger.warning("clarify card: synchronous retry resolve also failed: %s", e2)
        else:
            # No event loop — synchronous fallback
            try:
                from tools.clarify_gateway import resolve_gateway_clarify
                resolve_gateway_clarify(clarify_id, stored_selection)
            except (ImportError, Exception) as e:
                _logger.warning("clarify card: synchronous retry resolve failed: %s", e)

        # Return the same submitted card (soft lock with retry button)
        return _submitted_card_response(stored_selection, choices, question, clarify_id)

    # ── Handle select action (dropdown choice) ──
    if clarify_action == "select":
        selected_option = str(getattr(getattr(event, "action", None), "option", "") or "")

        # Predefined choice selected → resolve
        with _clarify_lock:
            choices_list = list(_clarify_choices.get(clarify_id, []))
        try:
            idx = int(selected_option)
            choice_text = choices_list[idx]
        except (ValueError, IndexError):
            _logger.warning(
                "clarify card: invalid option index '%s' for clarify_id=%s (choices=%s)",
                selected_option,
                (clarify_id or "?")[:12],
                choices_list,
            )
            return _empty_response()

        _logger.info(
            "clarify card: resolving with choice '%s' for clarify_id=%s",
            choice_text,
            (clarify_id or "?")[:12],
        )

        # Store selection for retry
        with _clarify_lock:
            _clarify_selections[clarify_id] = choice_text

        # v2.6.0: clarify 拆卡 — resolve 后把流式卡切到新卡（clarify 前后输出分卡呈现）
        _schedule_clarify_split(adapter_instance, data)

        # Resolve the clarify (schedule on event loop since we're in a sync callback)
        loop = getattr(adapter_instance, "_loop", None)
        if loop is not None:
            try:
                from tools.clarify_gateway import resolve_gateway_clarify
                from agent.async_utils import safe_schedule_threadsafe

                async def _do_resolve():
                    resolve_gateway_clarify(clarify_id, choice_text)
                    # Schedule server-side confirm update after resolve
                    await _schedule_confirm_card(cid=clarify_id)

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
        else:
            # No event loop — synchronous fallback
            try:
                from tools.clarify_gateway import resolve_gateway_clarify
                resolve_gateway_clarify(clarify_id, choice_text)
            except (ImportError, Exception) as e:
                _logger.warning("clarify card: synchronous resolve failed: %s", e)

        # Return submitted card (soft lock with retry button) — don't cleanup yet
        return _submitted_card_response(choice_text, choices_list or None, question, clarify_id)

    # ── Handle input_submit action (text input via Enter key) ──
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

        # Store selection for retry
        with _clarify_lock:
            _clarify_selections[clarify_id] = input_text

        # v2.6.0: clarify 拆卡 — resolve 后把流式卡切到新卡（clarify 前后输出分卡呈现）
        _schedule_clarify_split(adapter_instance, data)

        # Resolve the clarify
        loop = getattr(adapter_instance, "_loop", None)
        if loop is not None:
            try:
                from tools.clarify_gateway import resolve_gateway_clarify
                from agent.async_utils import safe_schedule_threadsafe

                async def _do_resolve_input():
                    resolve_gateway_clarify(clarify_id, input_text)
                    # Schedule server-side confirm update after resolve
                    await _schedule_confirm_card(cid=clarify_id)

                safe_schedule_threadsafe(
                    _do_resolve_input(), loop,
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
        else:
            # No event loop — synchronous fallback
            try:
                from tools.clarify_gateway import resolve_gateway_clarify
                resolve_gateway_clarify(clarify_id, input_text)
            except (ImportError, Exception) as e:
                _logger.warning("clarify card: synchronous resolve failed: %s", e)

        # Return submitted card (soft lock with retry button) — don't cleanup yet
        return _submitted_card_response(input_text, choices, question, clarify_id)

    # ── Handle button_submit action (click submit button) ──
    if clarify_action == "button_submit":
        action_obj = getattr(event, "action", None)
        # Read input from form_value (button callbacks include all form values)
        form_value = getattr(action_obj, "form_value", None) or {}
        input_text = str(form_value.get("clarify_input", "") or "").strip()

        if not input_text:
            _logger.debug("clarify card: empty button submit for clarify_id=%s", (clarify_id or "?")[:12])
            return _empty_response()

        _logger.info(
            "clarify card: resolving with button submit '%s' for clarify_id=%s",
            input_text[:50],
            (clarify_id or "?")[:12],
        )

        # Store selection for retry
        with _clarify_lock:
            _clarify_selections[clarify_id] = input_text

        # v2.6.0: clarify 拆卡 — resolve 后把流式卡切到新卡（clarify 前后输出分卡呈现）
        _schedule_clarify_split(adapter_instance, data)

        # Resolve the clarify
        loop = getattr(adapter_instance, "_loop", None)
        if loop is not None:
            try:
                from tools.clarify_gateway import resolve_gateway_clarify
                from agent.async_utils import safe_schedule_threadsafe

                async def _do_resolve_button():
                    resolve_gateway_clarify(clarify_id, input_text)
                    # Schedule server-side confirm update after resolve
                    await _schedule_confirm_card(cid=clarify_id)

                safe_schedule_threadsafe(
                    _do_resolve_button(), loop,
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
        else:
            # No event loop — synchronous fallback
            try:
                from tools.clarify_gateway import resolve_gateway_clarify
                resolve_gateway_clarify(clarify_id, input_text)
            except (ImportError, Exception) as e:
                _logger.warning("clarify card: synchronous resolve failed: %s", e)

        # Return submitted card (soft lock with retry button) — don't cleanup yet
        return _submitted_card_response(input_text, choices, question, clarify_id)

    _logger.debug("clarify card: unknown action '%s', ignoring", clarify_action)
    return _empty_response()


# ── Slash-confirm and Clarify lifecycle hardening ───────────────────────
#
# The v2.6 implementations above are retained only so an already-imported
# function object from an old gateway process does not disappear during a
# rolling reload.  The names below deliberately replace those implementations
# for every newly patched adapter class.  In particular, this path uses no
# old one-second ``_schedule_confirm_card`` delay: that delay left a window in
# which two callbacks could both schedule a gateway resolve.

_SLASH_CONFIRM_TTL_SEC = 300
_slash_confirm_lock = threading.Lock()
_slash_confirms: dict[tuple[str, str], dict[str, Any]] = {}

# Keep the local registration beyond Hermes' default one-hour Clarify TTL.
# Normal cleanup is driven by ``retire_clarify_card``; this value is only a
# defensive retention bound for a process which never receives that callback.
_CLARIFY_TTL_SEC = 65 * 60
_clarify_records: dict[str, dict[str, Any]] = {}


def _as_text(value: Any) -> str:
    """Return a real non-empty string, never a mock/object repr."""
    return value.strip() if isinstance(value, str) else ""


def _field(obj: Any, *names: str) -> str:
    """Read the first string field from a mapping or lightweight SDK object."""
    for name in names:
        if isinstance(obj, dict):
            value = obj.get(name)
        else:
            value = getattr(obj, name, None)
        text = _as_text(value)
        if text:
            return text
    return ""


def _metadata_field(metadata: Any, *names: str) -> str:
    return _field(metadata, *names) if metadata is not None else ""


def _event_parts(data: Any) -> tuple[Any, Any, Any]:
    event = getattr(data, "event", None)
    return event, getattr(event, "action", None), getattr(event, "context", None)


def _event_chat_id(data: Any) -> str:
    event, _action, context = _event_parts(data)
    return _field(context, "open_chat_id", "chat_id") or _field(event, "open_chat_id", "chat_id")


def _event_thread_id(data: Any) -> str:
    event, _action, context = _event_parts(data)
    # ``open_message_id`` identifies the acted-on card/message, not reliably
    # the source conversation thread.  Only compare genuine thread/root fields
    # recorded from metadata so a non-thread card cannot be mistaken for one.
    return _field(context, "thread_id", "root_id") or _field(event, "thread_id", "root_id")


def _event_card_message_id(data: Any) -> str:
    """Return the acted-on card's message id from the CardKit callback."""
    event, _action, context = _event_parts(data)
    return _field(context, "open_message_id") or _field(event, "open_message_id")


def _event_operator_open_id(data: Any) -> str:
    event, _action, _context = _event_parts(data)
    return _field(getattr(event, "operator", None), "open_id")


async def _await_if_needed(value: Any) -> Any:
    """Await SDK values when needed while keeping simple test doubles usable."""
    if inspect.isawaitable(value):
        return await value
    return value


async def _call_original_card_handler(original_method: Callable, self_feishu: Any, data: Any) -> Any:
    return await _await_if_needed(original_method(self_feishu, data))


def _callback_card_response(card_data: dict[str, Any] | None) -> Any:
    """Build the SDK callback response used to replace an interactive card."""
    if not card_data:
        return None
    try:
        from lark_oapi.api.cardkit.v1 import CallBackCard, P2CardActionTriggerResponse

        response = P2CardActionTriggerResponse()
        card = CallBackCard()
        card.type = "raw"
        card.data = card_data
        response.card = card
        return response
    except Exception:  # noqa: BLE001 - optional CardKit builder boundary
        # A missing optional SDK must not route a callback into Hermes' generic
        # /card handler.  The server-side update remains best effort below.
        return None


def _send_result_type() -> Any | None:
    try:
        from gateway.platforms.base import SendResult
        return SendResult
    except Exception:  # noqa: BLE001 - optional host gateway boundary
        return None


def _success_send_result(message_id: str) -> Any | None:
    result_type = _send_result_type()
    if result_type is None:
        return None
    try:
        return result_type(success=True, message_id=message_id)
    except TypeError:
        # Hermes' current SendResult accepts message_id.  Do not pretend
        # success if a future incompatible class does not.
        return None


def _message_id(value: Any) -> str:
    """Extract a usable Feishu message id without accepting arbitrary reprs."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _field(value, "message_id", "msg_id", "id")
    return _field(value, "message_id", "msg_id", "id")


def _strip_text_fallback(message: Any) -> str:
    """Remove host-only and permanent-approval prose from a native card.

    Hermes 0.21.3's text fallback advertises an ``Always Approve`` branch
    before its ``Text fallback:`` footer.  The native card deliberately
    supports only one-shot confirmation or cancellation, so retaining that
    line would visually reintroduce a permanent authorization path even
    though no corresponding option exists in the card.
    """
    text = message if isinstance(message, str) else str(message or "")
    visible_lines: list[str] = []
    for line in text.splitlines():
        folded = line.casefold()
        # The footer contains text-only commands, including /always.  It and
        # everything after it are intentionally absent from the card surface.
        if "text fallback:" in folded or "文本备用" in line:
            break
        # Match the exact permanent branch emitted by Hermes rather than a
        # broad bare "always", which could occur innocently in command detail.
        if (
            "always approve" in folded
            or "approve always" in folded
            or "始终批准" in line
        ):
            continue
        visible_lines.append(line)
    return "\n".join(visible_lines).rstrip()


def _fallback_slash_confirm_card(
    *, title: str, message: str, session_key: str, confirm_id: str
) -> dict[str, Any]:
    """Schema-2.0 fallback with the only proven first-send interaction.

    Do not introduce ``tag: action`` / ``button`` here.  Feishu IM rejects
    that shape for a first/updated card (230099); ``select_static`` callback is
    the deployed compatibility path.
    """
    return {
        "schema": "2.0",
        "config": {"streaming_mode": False},
        "body": {
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**{title}" + "**"},
                },
                {"tag": "markdown", "content": message},
                {
                    "tag": "select_static",
                    "element_id": "hermes_slash_confirm",
                    "placeholder": {"tag": "plain_text", "content": "请选择"},
                    "options": [
                        {
                            "text": {"tag": "plain_text", "content": "本次确认"},
                            "value": "once",
                        },
                        {
                            "text": {"tag": "plain_text", "content": "取消"},
                            "value": "cancel",
                        },
                    ],
                    "behaviors": [
                        {
                            "type": "callback",
                            "value": {
                                "hermes_slash_confirm_action": "select",
                                "session_key": session_key,
                                "confirm_id": confirm_id,
                            },
                        }
                    ],
                },
            ]
        },
    }


def _build_slash_confirm_card(
    *, title: str, message: str, session_key: str, confirm_id: str
) -> dict[str, Any]:
    clean_message = _strip_text_fallback(message)
    try:
        from ..cardkit import build_slash_confirm_card

        return build_slash_confirm_card(
            title=title,
            message=clean_message,
            session_key=session_key,
            confirm_id=confirm_id,
        )
    except Exception:  # noqa: BLE001 - optional CardKit builder boundary
        _logger.debug("slash confirm: CardKit pending builder unavailable", exc_info=True)
        return _fallback_slash_confirm_card(
            title=title,
            message=clean_message,
            session_key=session_key,
            confirm_id=confirm_id,
        )


def _fallback_slash_resolved_card(*, title: str, message: str, choice: str) -> dict[str, Any]:
    labels = {
        "once": "已确认",
        "cancel": "已取消",
        "expired": "确认已过期",
        "failed": "确认未完成",
    }
    return {
        "schema": "2.0",
        "config": {"streaming_mode": False},
        "body": {
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**{title}" + "**"}},
                *([{"tag": "markdown", "content": message}] if message else []),
                {"tag": "div", "text": {"tag": "plain_text", "content": labels.get(choice, labels["failed"])}},
            ]
        },
    }


def _build_slash_resolved_card(*, title: str, message: str, choice: str) -> dict[str, Any]:
    try:
        from ..cardkit import build_slash_confirm_resolved_card

        return build_slash_confirm_resolved_card(title=title, message=message, choice=choice)
    except Exception:  # noqa: BLE001 - optional CardKit builder boundary
        _logger.debug("slash confirm: CardKit resolved builder unavailable", exc_info=True)
        return _fallback_slash_resolved_card(title=title, message=message, choice=choice)


def _slash_scope_matches(record: dict[str, Any], data: Any) -> bool:
    event_chat = _event_chat_id(data)
    event_thread = _event_thread_id(data)
    event_card = _event_card_message_id(data)
    expected_chat = _as_text(record.get("chat_id"))
    expected_thread = _as_text(record.get("thread_id"))
    expected_card = _as_text(record.get("card_msg_id"))
    # Chat and the acted-on card are mandatory boundaries.  Never infer either
    # from session_key, which is not a trusted user identity.
    if expected_chat and event_chat != expected_chat:
        return False
    # CardKit's current callback context exposes the acted-on card id, but
    # does not expose ``thread_id`` / ``root_id``.  Binding that id is stronger
    # than accepting any card in the chat; absence or mismatch must fail
    # closed.  A future SDK that does expose thread scope receives the extra
    # comparison below without breaking today's legitimate thread callbacks.
    if expected_card and event_card != expected_card:
        return False
    return not (expected_thread and event_thread and event_thread != expected_thread)


def _interactive_operator_authorized(adapter_instance: Any, data: Any) -> bool:
    checker = getattr(adapter_instance, "_is_interactive_operator_authorized", None)
    open_id = _event_operator_open_id(data)
    if not callable(checker) or not open_id:
        return False
    try:
        return bool(checker(open_id))
    except Exception:  # noqa: BLE001 - host authorization hook boundary
        _logger.warning("interactive card: authorization check failed", exc_info=True)
        return False


def _slash_action_choice(data: Any) -> str:
    """Read the choice only from the select option, never callback value."""
    _event, action, _context = _event_parts(data)
    raw_option = getattr(action, "option", None)
    if isinstance(raw_option, dict):
        option = _field(raw_option, "value")
    else:
        option = _as_text(raw_option)
    return option if option in {"once", "cancel"} else ""


async def _update_card_safely(card_msg_id: str, card_data: dict[str, Any]) -> bool:
    if not card_msg_id:
        return False
    try:
        from ..controller import get_controller

        ctrl = get_controller()
        if not ctrl or not ctrl.enabled or not ctrl._client_ok():
            return False
        await ctrl._client.update_card(card_msg_id, card_data)
        return True
    except Exception:  # noqa: BLE001 - controller transport boundary
        _logger.warning("interactive card: server-side card update failed", exc_info=True)
        return False


def _set_slash_state(key: tuple[str, str], record: dict[str, Any], state: str) -> None:
    with _slash_confirm_lock:
        if _slash_confirms.get(key) is record:
            record["state"] = state


async def _deliver_slash_result(adapter_instance: Any, record: dict[str, Any], result_text: str) -> bool:
    if not result_text:
        return True
    try:
        # Hermes 0.21.3's slash-confirm metadata contains thread routing but
        # does not currently carry its inbound reply anchor.  When a future
        # host does provide one we preserve it; otherwise replying to the
        # native confirmation card is the narrowest available anchor and
        # keeps the completion visibly bound to the action that produced it.
        reply_to = record.get("reply_to") or record.get("card_msg_id") or None
        token = _preserve_host_send_routing.set(True)
        try:
            delivered = await adapter_instance.send(
                record["chat_id"],
                result_text,
                reply_to=reply_to,
                metadata=record.get("metadata"),
            )
        finally:
            _preserve_host_send_routing.reset(token)
        # The adapter contract returns SendResult.  A transport can report a
        # failed result without raising, and treating that as delivered would
        # falsely lock an already-resolved high-risk action as successful while
        # silently losing its required result prose.
        if getattr(delivered, "success", None) is not True:
            _logger.warning("slash confirm: result follow-up returned unsuccessful SendResult")
            return False
        return True
    except Exception:  # noqa: BLE001 - host follow-up delivery boundary
        # The action may already have completed; never silently claim success
        # when its user-visible result could not be delivered.
        _logger.warning("slash confirm: result follow-up delivery failed", exc_info=True)
        return False


def _result_text(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        return _field(result, "text", "content", "message")
    return _field(result, "text", "content", "message")


def _wrap_feishu_adapter_send_slash_confirm(orig_send_slash_confirm: Callable) -> Callable:
    """Render the real Hermes Slash-confirm contract as a native Feishu card."""

    async def _intercepted_send_slash_confirm(
        self_feishu,
        chat_id,
        title,
        message,
        session_key,
        confirm_id,
        metadata=None,
        **kwargs,
    ):
        async def _fallback():
            return await orig_send_slash_confirm(
                self_feishu,
                chat_id,
                title,
                message,
                session_key,
                confirm_id,
                metadata=metadata,
                **kwargs,
            )

        chat = _as_text(chat_id)
        session = _as_text(session_key)
        confirm = _as_text(confirm_id)
        # A genuine SendResult and the dynamically-called callback seam are
        # required before we send anything.  Otherwise run_busy must retain
        # Hermes' text /approve-/cancel fallback.
        if (
            not chat
            or not session
            or not confirm
            or _send_result_type() is None
            or not callable(getattr(self_feishu, "_handle_card_action_event", None))
            or not callable(getattr(self_feishu, "_is_interactive_operator_authorized", None))
        ):
            _logger.info(
                "slash confirm: native preflight unavailable; preserving host text fallback "
                "chat=%s session=%s confirm=%s",
                chat[:12] or "?",
                session[:12] or "?",
                confirm[:12] or "?",
            )
            return await _fallback()

        try:
            from ..controller import get_controller

            ctrl = get_controller()
            if not ctrl or not ctrl.enabled:
                _logger.info(
                    "slash confirm: card controller unavailable; preserving host text fallback "
                    "chat=%s confirm=%s",
                    chat[:12],
                    confirm[:12],
                )
                return await _fallback()

            # Registration can happen before the gateway event loop exists, in
            # which case its best-effort pre-warm is intentionally skipped.
            # A first high-risk Slash must not silently lose native confirmation
            # merely because it wins that startup race: initialization is
            # idempotent and is safe to await from this real delivery seam.
            if not ctrl._client_ok():
                await ctrl._ensure_init()
            if not ctrl._client_ok():
                _logger.warning(
                    "slash confirm: card client unavailable after initialization; "
                    "preserving host text fallback chat=%s confirm=%s",
                    chat[:12],
                    confirm[:12],
                )
                return await _fallback()

            card = _build_slash_confirm_card(
                title=_as_text(title) or "确认操作",
                message=_as_text(message),
                session_key=session,
                confirm_id=confirm,
            )
            # ``reply_to_message_id`` is the real Feishu message anchor.  It
            # must be preferred over older rolling-compatibility spellings.
            # A ``thread_id`` alone is not an API-valid reply target: turning
            # it into a card reply would silently move a threaded Slash
            # confirmation to the chat's top level.
            thread_id = _metadata_field(metadata, "thread_id", "root_id")
            reply_to = _metadata_field(
                metadata, "reply_to_message_id", "reply_to", "message_id"
            )
            if thread_id and not reply_to:
                _logger.info(
                    "slash confirm: thread has no message anchor; preserving host text fallback"
                )
                return await _fallback()
            if reply_to:
                delivered = await ctrl._client.reply_card(
                    reply_to, card, reply_in_thread=bool(thread_id)
                )
            else:
                delivered = await ctrl._client.send_card_to_chat(chat, card)
            card_msg_id = _message_id(delivered)
            if not card_msg_id:
                _logger.warning("slash confirm: delivery returned no message id; using text fallback")
                return await _fallback()

            record = {
                "state": "PENDING",
                "chat_id": chat,
                "thread_id": thread_id,
                "reply_to": reply_to,
                "metadata": metadata,
                "session_key": session,
                "confirm_id": confirm,
                "title": _as_text(title) or "确认操作",
                "message": _strip_text_fallback(message),
                "card_msg_id": card_msg_id,
                "created_at": time.monotonic(),
            }
            with _slash_confirm_lock:
                _slash_confirms[(session, confirm)] = record
            _register_gateway_card(card_msg_id, chat_id=chat, card_id=None, category="slash")

            success = _success_send_result(card_msg_id)
            if success is None:
                # This should be impossible after the preflight above, but it
                # is safer to retain the host fallback than to claim success.
                with _slash_confirm_lock:
                    if _slash_confirms.get((session, confirm)) is record:
                        _slash_confirms.pop((session, confirm), None)
                return await _fallback()
            return success
        except Exception:  # noqa: BLE001 - native delivery must retain text fallback
            _logger.warning("slash confirm: native card delivery failed; using text fallback", exc_info=True)
            return await _fallback()

    return _intercepted_send_slash_confirm


async def _handle_slash_confirm_card_action(
    adapter_instance: Any, data: Any, action_value: dict[str, Any]
) -> Any:
    """Claim and resolve a Slash confirmation exactly once on the event loop."""
    if not isinstance(action_value, dict) or action_value.get("hermes_slash_confirm_action") != "select":
        return None
    session = _as_text(action_value.get("session_key"))
    confirm = _as_text(action_value.get("confirm_id"))
    choice = _slash_action_choice(data)
    if not session or not confirm or not choice or not _interactive_operator_authorized(adapter_instance, data):
        return None

    key = (session, confirm)
    expired_record: dict[str, Any] | None = None
    with _slash_confirm_lock:
        record = _slash_confirms.get(key)
        if record is None:
            return None
        if record.get("state") != "PENDING":
            return None
        if not _slash_scope_matches(record, data):
            _logger.warning("slash confirm: rejected callback outside stored chat/thread boundary")
            return None
        if time.monotonic() - float(record.get("created_at", 0.0)) > _SLASH_CONFIRM_TTL_SEC:
            record["state"] = "EXPIRED"
            expired_record = record
        else:
            # The assignment is the atomic PENDING -> RESOLVING claim.  No
            # await or task scheduling occurs before it.
            record["state"] = "RESOLVING"

    if expired_record is not None:
        card = _build_slash_resolved_card(
            title=expired_record["title"], message=expired_record["message"], choice="expired"
        )
        await _update_card_safely(expired_record.get("card_msg_id", ""), card)
        return _callback_card_response(card)

    try:
        from tools.slash_confirm import resolve

        # This is the Hermes 0.21.3 contract.  Do not substitute a legacy
        # GatewayRunner resolver or fire-and-forget it: completion decides the
        # terminal card state.
        resolved = await resolve(session, confirm, choice, timeout=300)
    except Exception:  # noqa: BLE001 - resolver boundary must fail closed
        _logger.warning("slash confirm: resolver failed", exc_info=True)
        _set_slash_state(key, record, "FAILED")
        failed = _build_slash_resolved_card(
            title=record["title"], message=record["message"], choice="failed"
        )
        await _update_card_safely(record.get("card_msg_id", ""), failed)
        return _callback_card_response(failed)

    text = _result_text(resolved)
    # A cancel is a valid terminal outcome even when its handler deliberately
    # has no result prose.  A once confirmation, however, is not displayed as
    # successful if the resolver returned no result at all.
    if choice == "once" and not text:
        _set_slash_state(key, record, "FAILED")
        failed = _build_slash_resolved_card(
            title=record["title"], message=record["message"], choice="failed"
        )
        await _update_card_safely(record.get("card_msg_id", ""), failed)
        return _callback_card_response(failed)

    if text and not await _deliver_slash_result(adapter_instance, record, text):
        _set_slash_state(key, record, "FAILED")
        failed = _build_slash_resolved_card(
            title=record["title"], message=record["message"], choice="failed"
        )
        await _update_card_safely(record.get("card_msg_id", ""), failed)
        return _callback_card_response(failed)

    terminal = "CANCELLED" if choice == "cancel" else "RESOLVED"
    _set_slash_state(key, record, terminal)
    resolved_card = _build_slash_resolved_card(
        title=record["title"], message=record["message"], choice=choice
    )
    await _update_card_safely(record.get("card_msg_id", ""), resolved_card)
    return _callback_card_response(resolved_card)


def _clear_clarify_legacy_locked(clarify_id: str) -> None:
    _clarify_choices.pop(clarify_id, None)
    _clarify_questions.pop(clarify_id, None)
    _clarify_card_msg_ids.pop(clarify_id, None)
    _clarify_selections.pop(clarify_id, None)
    _clarify_timestamps.pop(clarify_id, None)


def _pop_clarify_record_locked(clarify_id: str) -> dict[str, Any] | None:
    record = _clarify_records.pop(clarify_id, None)
    _clear_clarify_legacy_locked(clarify_id)
    return record


def _hydrate_legacy_clarify_locked(clarify_id: str) -> dict[str, Any] | None:
    """Keep legacy test/rolling-reload registry entries harmlessly usable."""
    record = _clarify_records.get(clarify_id)
    if record is not None:
        return record
    if not any(
        clarify_id in registry
        for registry in (_clarify_questions, _clarify_choices, _clarify_card_msg_ids, _clarify_selections)
    ):
        return None
    record = {
        "state": "PENDING",
        "chat_id": "",
        "thread_id": "",
        "reply_to": "",
        "metadata": None,
        "session_key": "",
        "question": _clarify_questions.get(clarify_id, ""),
        "choices": list(_clarify_choices.get(clarify_id, [])),
        "multi_select": False,
        "card_msg_id": _clarify_card_msg_ids.get(clarify_id, ""),
        "selection": _clarify_selections.get(clarify_id, ""),
        "display_selection": _clarify_selections.get(clarify_id, ""),
        "created_at": time.monotonic(),
    }
    _clarify_records[clarify_id] = record
    return record


def _clarify_record_is_expired(record: dict[str, Any], *, now: float | None = None) -> bool:
    """Return whether a record outlived its defensive local retention."""
    created_at = record.get("created_at")
    if not isinstance(created_at, (int, float)):
        # A malformed record is not trustworthy enough to resolve.  Expire it
        # rather than guessing at its lifetime.
        return True
    current = time.monotonic() if now is None else now
    return current - float(created_at) > _CLARIFY_TTL_SEC


def _prune_expired_clarify() -> list[dict[str, Any]]:
    """Atomically remove stale local Clarify records.

    Hermes normally invokes ``retire_clarify_card`` at its one-hour timeout.
    This 65-minute guard is intentionally later and only protects the rare
    missed lifecycle callback.  It uses the same monotonic clock as the
    registration; the legacy timestamp map remains compatible with older
    concurrency tests and rolling-reload residue.
    """
    expired_records: list[dict[str, Any]] = []
    with _clarify_lock:
        now_monotonic = time.monotonic()
        for clarify_id, record in list(_clarify_records.items()):
            if _clarify_record_is_expired(record, now=now_monotonic):
                popped = _pop_clarify_record_locked(clarify_id)
                if popped is not None:
                    expired_records.append(popped)

        # Legacy-only entries do not have a card record to render, but must
        # not retain stale mutable answer state indefinitely.  Old code wrote
        # wall-clock timestamps; new records use monotonic values, so select the
        # matching clock without mixing their epochs.
        now_wall = time.time()
        for clarify_id, timestamp in list(_clarify_timestamps.items()):
            if clarify_id in _clarify_records:
                continue
            if not isinstance(timestamp, (int, float)):
                expired = True
            elif timestamp > 100_000_000:
                expired = now_wall - float(timestamp) > _CLARIFY_TTL_SEC
            else:
                expired = now_monotonic - float(timestamp) > _CLARIFY_TTL_SEC
            if expired:
                _clear_clarify_legacy_locked(clarify_id)

    if expired_records:
        _logger.info("clarify card: locally expired %d unretired records", len(expired_records))
    return expired_records


async def _retire_expired_clarify_records() -> None:
    """Best-effort visual retirement for records cleaned by the local guard."""
    for record in _prune_expired_clarify():
        card_msg_id = _as_text(record.get("card_msg_id"))
        _unregister_gateway_card(card_msg_id)
        if not card_msg_id:
            continue
        card = _build_clarify_retired_card(
            question=_as_text(record.get("question")),
            notice="Clarification expired.",
        )
        await _update_card_safely(card_msg_id, card)


def _read_gateway_multi_select(clarify_id: str) -> bool | None:
    """Read only the verified Hermes entry flag; never infer it from choices."""
    try:
        from tools import clarify_gateway

        entries = getattr(clarify_gateway, "_entries", None)
        if not isinstance(entries, dict):
            return None
        entry = entries.get(clarify_id)
        if entry is None or not hasattr(entry, "multi_select"):
            return None
        value = entry.multi_select
        return value if isinstance(value, bool) else None
    except Exception:  # noqa: BLE001 - private host gateway boundary
        return None


def _build_clarify_pending_card(
    *, question: str, choices: list[str] | None, clarify_id: str, multi_select: bool
) -> dict[str, Any]:
    from ..cardkit import build_clarify_card

    try:
        return build_clarify_card(
            question=question,
            choices=choices,
            clarify_id=clarify_id,
            multi_select=multi_select,
        )
    except TypeError:
        # A builder unable to represent a verified multi-select must not silently
        # turn it into a single-select card.  Caller will retain text fallback.
        if multi_select:
            raise
        return build_clarify_card(question=question, choices=choices, clarify_id=clarify_id)


def _wrap_feishu_adapter_send_clarify(orig_send_clarify: Callable) -> Callable:
    """Render one independent Clarify card for each host-issued clarify_id."""

    async def _intercepted_send_clarify(
        self_feishu, chat_id, question, choices, clarify_id, session_key, metadata=None, **kwargs
    ):
        # ``thread_id`` is a routing destination, not an IM reply-message ID.
        # The plugin client's top-level-card API cannot use it, while Hermes's
        # original adapter send can.  Work out this boundary before every
        # fallback is defined so a controller/card failure cannot accidentally
        # turn a threaded Clarify into a top-level gateway card either.
        thread_id = _metadata_field(metadata, "thread_id", "root_id")
        reply_to = _metadata_field(
            metadata, "reply_to_message_id", "reply_to", "message_id"
        )
        preserve_thread_route = bool(thread_id and not reply_to)

        async def _fallback():
            if not preserve_thread_route:
                return await orig_send_clarify(
                    self_feishu,
                    chat_id,
                    question,
                    choices,
                    clarify_id,
                    session_key,
                    metadata=metadata,
                    **kwargs,
                )
            # BasePlatformAdapter.send_clarify delegates to ``self.send``.
            # Keep the flag across that await so HLS's generic send wrapper
            # forwards the host's thread-aware call instead of cardifying it.
            token = _preserve_host_send_routing.set(True)
            try:
                return await orig_send_clarify(
                    self_feishu,
                    chat_id,
                    question,
                    choices,
                    clarify_id,
                    session_key,
                    metadata=metadata,
                    **kwargs,
                )
            finally:
                _preserve_host_send_routing.reset(token)

        _cls = type(self_feishu)
        if id(_cls) not in _patched_feishu_classes:
            from . import _apply_feishu_adapter_patches

            _apply_feishu_adapter_patches(_cls, is_repatch=True)

        chat = _as_text(chat_id)
        cid = _as_text(clarify_id)
        if not chat or not cid:
            return await _fallback()

        multi_select = _read_gateway_multi_select(cid)
        if multi_select is None:
            # This is intentionally text fallback, not a guessed single-select:
            # a private/moved entry can otherwise corrupt host answer shape.
            _logger.warning("clarify card: cannot read multi_select entry; using text fallback cid=%s", cid[:12])
            return await _fallback()

        if preserve_thread_route:
            _logger.info(
                "clarify card: thread has no message anchor; preserving host text fallback cid=%s",
                cid[:12],
            )
            return await _fallback()

        try:
            from ..cardkit import normalize_clarify_choices
            from ..controller import get_controller

            ctrl = get_controller()
            if not ctrl or not ctrl.enabled or not ctrl._client_ok():
                return await _fallback()

            # Do not let a missed host timeout turn an old interactive card
            # into a live resolver forever.  This is deliberately after the
            # controller check because rendering the terminal card needs it.
            await _retire_expired_clarify_records()

            # Preserve the v2.6 visual split behavior, but it is not part of
            # resolution/claim correctness and failure is non-fatal.
            try:
                for _mid, session in ctrl._sess_items_snapshot():
                    if session.chat_id == chat and not session.is_terminal_phase:
                        if session.unified_state and session.unified_state.has_dirty:
                            await session.flush.flush_now(lambda s=session: ctrl._do_unified_flush(s))
                        else:
                            session.flush._cancel_timer()
                        break
            except Exception:  # noqa: BLE001 - non-fatal visual pre-flush boundary
                _logger.debug("clarify card: pre-flush failed (non-fatal)", exc_info=True)

            normalized = normalize_clarify_choices(choices) if choices else []
            card = _build_clarify_pending_card(
                question=_as_text(question),
                choices=normalized or None,
                clarify_id=cid,
                multi_select=multi_select,
            )
            if reply_to:
                delivered = await ctrl._client.reply_card(
                    reply_to, card, reply_in_thread=bool(thread_id)
                )
            else:
                delivered = await ctrl._client.send_card_to_chat(chat, card)
            card_msg_id = _message_id(delivered)
            if not card_msg_id:
                _logger.warning("clarify card: delivery returned no message id; using text fallback cid=%s", cid[:12])
                return await _fallback()

            record = {
                "state": "PENDING",
                "chat_id": chat,
                "thread_id": thread_id,
                "reply_to": reply_to,
                "metadata": metadata,
                "session_key": _as_text(session_key),
                "question": _as_text(question),
                "choices": list(normalized),
                "multi_select": multi_select,
                "card_msg_id": card_msg_id,
                "selection": "",
                "display_selection": "",
                "created_at": time.monotonic(),
            }
            with _clarify_lock:
                # A host retries the same id only after ending/replacing its old
                # entry.  Retire the local registration atomically before a
                # replacement can receive a click.
                _clear_clarify_legacy_locked(cid)
                _clarify_records[cid] = record
                _clarify_choices[cid] = list(normalized)
                _clarify_questions[cid] = record["question"]
                _clarify_card_msg_ids[cid] = card_msg_id
                _clarify_timestamps[cid] = time.monotonic()
            _register_gateway_card(card_msg_id, chat_id=chat, card_id=None, category="clarify")

            try:
                from tools.clarify_gateway import mark_awaiting_text

                await _await_if_needed(mark_awaiting_text(cid))
            except Exception:  # noqa: BLE001 - optional host lifecycle marker
                _logger.debug("clarify card: mark_awaiting_text unavailable", exc_info=True)

            success = _success_send_result(card_msg_id)
            # Existing Hermes versions also treat a plain None as the adapter's
            # send result in test-only environments.  In production a real
            # SendResult is available; do not fabricate one here.
            return success
        except Exception:  # noqa: BLE001 - native delivery must retain text fallback
            _logger.warning("clarify card: native delivery failed; using text fallback", exc_info=True)
            return await _fallback()

    return _intercepted_send_clarify


def _action_option_values(
    action: Any, action_value: dict[str, Any], *, form_only: bool = False
) -> list[str]:
    """Normalize Feishu single/multi-select callback values to option strings."""
    candidates: list[Any] = []
    if not form_only:
        for name in ("option_array", "options", "option"):
            value = getattr(action, name, None)
            if value is not None:
                candidates.append(value)
        for name in ("selected_options", "option_array", "options", "option"):
            if isinstance(action_value, dict) and name in action_value:
                candidates.append(action_value[name])
    # CardKit multi-select uses a root ``form``.  Selection-change
    # callbacks are intentionally inert; only the form submit supplies this
    # value to the resolver.
    form_value = getattr(action, "form_value", None)
    if isinstance(form_value, dict) and "clarify_multi_select" in form_value:
        candidates.append(form_value["clarify_multi_select"])

    values: list[str] = []

    def _collect(value: Any) -> None:
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.startswith("["):
                try:
                    parsed = json.loads(candidate)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, list):
                    _collect(parsed)
                    return
            if "," in candidate:
                for part in candidate.split(","):
                    _collect(part)
                return
            if candidate:
                values.append(candidate)
            return
        if isinstance(value, dict):
            candidate = _field(value, "value", "option", "key")
            if candidate:
                values.append(candidate)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                _collect(item)

    for candidate in candidates:
        _collect(candidate)
    # Feishu can include the same chosen option in both option_array and
    # option.  Preserve ordering but avoid duplicate answers.
    return list(dict.fromkeys(values))


def _clarify_scope_matches(record: dict[str, Any], data: Any) -> bool:
    expected_chat = _as_text(record.get("chat_id"))
    expected_thread = _as_text(record.get("thread_id"))
    expected_card = _as_text(record.get("card_msg_id"))
    if expected_chat and _event_chat_id(data) != expected_chat:
        return False
    if expected_card and _event_card_message_id(data) != expected_card:
        return False
    event_thread = _event_thread_id(data)
    return not (expected_thread and event_thread and event_thread != expected_thread)


def _native_multi_form_submit_clarify_id(data: Any) -> str:
    """Identify only a real native multi-select form submission.

    Card 2.0 form buttons submit as ``action.tag == 'button'`` with values in
    ``action.form_value``.  They deliberately do not carry a callback
    ``behaviors`` marker.  Resolve the clarify id from the locally registered,
    actual card message id instead of trusting any optional action value.
    """
    _event, action, _context = _event_parts(data)
    if _as_text(getattr(action, "tag", None)) != "button":
        return ""
    form_value = getattr(action, "form_value", None)
    if not isinstance(form_value, dict) or "clarify_multi_select" not in form_value:
        return ""
    card_msg_id = _event_card_message_id(data)
    if not card_msg_id:
        return ""

    with _clarify_lock:
        matches = [
            clarify_id
            for clarify_id, record in _clarify_records.items()
            if isinstance(record, dict)
            and record.get("state") == "PENDING"
            and record.get("multi_select") is True
            and _as_text(record.get("card_msg_id")) == card_msg_id
            and _clarify_scope_matches(record, data)
        ]
    # A duplicated or malformed local registration is unsafe to guess through.
    # The normal handler takes the same lock again to make the later claim
    # atomic with respect to retire/replay.
    return matches[0] if len(matches) == 1 else ""


def _clarify_choice_payload(
    record: dict[str, Any], action: Any, action_value: dict[str, Any], *, form_only: bool = False
) -> tuple[str, str] | None:
    options = _action_option_values(action, action_value, form_only=form_only)
    choices = list(record.get("choices") or [])
    selected: list[str] = []
    for option in options:
        try:
            index = int(option)
            # Python permits negative list indexes, but Feishu's configured
            # option values are non-negative.  Treat a forged ``-1`` exactly
            # like any other invalid callback rather than selecting the last
            # answer accidentally.
            if index < 0:
                return None
            item = choices[index]
        except (ValueError, IndexError):
            # Accept an exact normalized value only; it is still constrained to
            # the card's original choices and cannot inject arbitrary prose.
            if option in choices:
                item = option
            else:
                return None
        selected.append(item)
    if not selected:
        return None
    if record.get("multi_select"):
        return json.dumps(selected, ensure_ascii=False), ", ".join(selected)
    if len(selected) != 1:
        return None
    return selected[0], selected[0]


async def _resolve_gateway_clarify(clarify_id: str, payload: str) -> Any:
    from tools.clarify_gateway import resolve_gateway_clarify

    return await _await_if_needed(resolve_gateway_clarify(clarify_id, payload))


def _build_clarify_confirmed_card(question: str, selected: str) -> dict[str, Any] | None:
    try:
        from ..cardkit import build_clarify_confirmed_card

        return build_clarify_confirmed_card(question=question, selected=selected)
    except Exception:  # noqa: BLE001 - optional CardKit builder boundary
        _logger.warning("clarify card: could not construct confirmed card", exc_info=True)
        return None


def _build_clarify_confirmed_response(question: str, selected: str) -> Any:
    return _callback_card_response(_build_clarify_confirmed_card(question, selected))


def _build_clarify_submitted_response(question: str, selected: str, clarify_id: str) -> Any:
    """Build the retryable callback card for a real resolver failure.

    ``build_clarify_submitted_card`` intentionally has no ``choices``
    parameter.  Keeping this live error/retry path prevents the old keyword
    mismatch from being hidden by tests that only inspect JSON builders.
    """
    try:
        from ..cardkit import build_clarify_submitted_card

        card = build_clarify_submitted_card(
            question=question,
            selected=selected,
            clarify_id=clarify_id,
        )
        return _callback_card_response(card)
    except Exception:  # noqa: BLE001 - optional CardKit builder boundary
        _logger.warning("clarify card: could not construct submitted retry card", exc_info=True)
        return None


async def _handle_clarify_card_action(
    adapter_instance: Any,
    data: Any,
    clarify_action: str,
    action_value: dict[str, Any],
) -> Any:
    """Atomically resolve one Clarify id; never batch or defer it."""
    if clarify_action == "multi_selection_pending":
        # The picker emits this on every selection change.  It must be
        # swallowed (rather than delegated to native /card) and must never
        # resolve a partially selected multi-answer.
        return None
    if clarify_action not in {"select", "multi_submit", "input_submit", "button_submit", "retry_submit"}:
        return None
    clarify_id = _as_text(action_value.get("clarify_id")) if isinstance(action_value, dict) else ""
    if not clarify_id or not _interactive_operator_authorized(adapter_instance, data):
        return None

    _event, action, _context = _event_parts(data)
    expired_record: dict[str, Any] | None = None
    with _clarify_lock:
        record = _hydrate_legacy_clarify_locked(clarify_id)
        if record is None or record.get("state") != "PENDING":
            return None
        if not _clarify_scope_matches(record, data):
            _logger.warning("clarify card: rejected callback outside stored chat/thread boundary")
            return None

        # Hermes normally retires its gateway entry at the one-hour timeout.
        # If that lifecycle callback was missed, do not let an old native card
        # resolve the host entry indefinitely.  Pop while holding the same
        # lock used by normal resolution so a concurrent click/retire cannot
        # claim this record after it has crossed the defensive retention TTL.
        if _clarify_record_is_expired(record):
            expired_record = _pop_clarify_record_locked(clarify_id)
        else:
            if clarify_action in {"select", "multi_submit"}:
                if clarify_action == "multi_submit" and not record.get("multi_select"):
                    return None
                payload_and_display = _clarify_choice_payload(
                    record,
                    action,
                    action_value,
                    # A form submit must use the form value exclusively.  Mixing
                    # a stale/forged action.option into the submitted field could
                    # otherwise add an answer the user did not select.
                    form_only=clarify_action == "multi_submit",
                )
            elif clarify_action == "retry_submit":
                previous = _as_text(record.get("selection"))
                payload_and_display = (previous, _as_text(record.get("display_selection")) or previous) if previous else None
            elif clarify_action == "input_submit":
                payload = _as_text(getattr(action, "input_value", None))
                if payload and record.get("multi_select"):
                    payload_and_display = (json.dumps([payload], ensure_ascii=False), payload)
                else:
                    payload_and_display = (payload, payload) if payload else None
            else:  # button_submit — legacy submitted cards carry form_value.
                form_value = getattr(action, "form_value", None) or {}
                payload = _field(form_value, "clarify_input")
                if payload and record.get("multi_select"):
                    payload_and_display = (json.dumps([payload], ensure_ascii=False), payload)
                else:
                    payload_and_display = (payload, payload) if payload else None

            if payload_and_display is None:
                return None
            payload, display = payload_and_display
            # Atomic PENDING -> RESOLVING claim before the first await.  It protects
            # double-click/replay even when the client delivers callbacks concurrently.
            record["state"] = "RESOLVING"
            record["selection"] = payload
            record["display_selection"] = display
            _clarify_selections[clarify_id] = payload

    if expired_record is not None:
        _unregister_gateway_card(expired_record.get("card_msg_id", ""))
        expired_card = _build_clarify_retired_card(
            question=_as_text(expired_record.get("question")),
            notice="Clarification expired.",
        )
        await _update_card_safely(expired_record.get("card_msg_id", ""), expired_card)
        return _callback_card_response(expired_card)

    try:
        resolved = await _resolve_gateway_clarify(clarify_id, payload)
        if resolved is False:
            raise RuntimeError("resolve_gateway_clarify returned False")
    except Exception:  # noqa: BLE001 - resolver boundary must preserve retry state
        _logger.warning("clarify card: resolver failed; preserving card for a later retry", exc_info=True)
        with _clarify_lock:
            if _clarify_records.get(clarify_id) is record:
                record["state"] = "PENDING"
        return _build_clarify_submitted_response(
            record.get("question", ""),
            display,
            clarify_id,
        )

    # The host may synchronously call retire_clarify_card while resolving.  If
    # it already popped this exact record, do not overwrite its retired card.
    with _clarify_lock:
        if _clarify_records.get(clarify_id) is not record:
            return None
        _pop_clarify_record_locked(clarify_id)

    confirmed_card = _build_clarify_confirmed_card(record.get("question", ""), display)
    # Hermes' non-native callback dispatch intentionally discards this
    # function's return value.  The server-side update is therefore the
    # authoritative way to turn the rendered card inert after a successful
    # resolve; a CallbackCard return below is only a best-effort supplement.
    if confirmed_card is not None:
        await _update_card_safely(record.get("card_msg_id", ""), confirmed_card)

    _schedule_clarify_split(adapter_instance, data)
    _unregister_gateway_card(record.get("card_msg_id", ""))
    return _callback_card_response(confirmed_card)


def _fallback_clarify_retired_card(*, question: str, notice: Any) -> dict[str, Any]:
    label = _as_text(notice) or "已结束"
    return {
        "schema": "2.0",
        "config": {"streaming_mode": False},
        "body": {
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**{question}" + "**"}},
                {"tag": "div", "text": {"tag": "plain_text", "content": label}},
            ]
        },
    }


def _build_clarify_retired_card(*, question: str, notice: Any) -> dict[str, Any]:
    try:
        from ..cardkit import build_clarify_retired_card

        return build_clarify_retired_card(question=question, notice=notice)
    except Exception:  # noqa: BLE001 - optional CardKit builder boundary
        _logger.debug("clarify card: CardKit retired builder unavailable", exc_info=True)
        return _fallback_clarify_retired_card(question=question, notice=notice)


def _retire_notice_is_rejected(notice: Any) -> bool:
    text = _as_text(getattr(notice, "value", None)) or _as_text(notice)
    return "TEXT_REJECTED_PROSE" in text.upper() or "TEXT_REJECTED_SELECTION" in text.upper()


async def retire_clarify_card(self_feishu: Any, clarify_id: str, notice: Any = "") -> None:
    """Host lifecycle callback, attached directly to FeishuAdapter classes.

    Snapshot and remove local state *before the first await*.  A late callback
    therefore cannot resolve a replaced/new Clarify, even if card update fails.
    """
    clarify = _as_text(clarify_id)
    if not clarify or _retire_notice_is_rejected(notice):
        return
    with _clarify_lock:
        record = _pop_clarify_record_locked(clarify)
    if record is None:
        # Unknown ids are an intentional idempotent no-op: no text and no call
        # to a hypothetical original method.
        return

    _unregister_gateway_card(record.get("card_msg_id", ""))
    card_msg_id = _as_text(record.get("card_msg_id"))
    if not card_msg_id:
        return
    retired = _build_clarify_retired_card(question=record.get("question", ""), notice=notice)
    await _update_card_safely(card_msg_id, retired)
    return


def _wrap_handle_card_action_event(original_method: Callable) -> Callable:
    """Route only our markers while preserving Hermes' native action paths."""

    async def _wrapped(self_feishu: Any, data: Any) -> Any:
        _event, action, _context = _event_parts(data)
        action_value = getattr(action, "value", {}) or {}
        if not isinstance(action_value, dict):
            # Existing v2.6 behavior suppressed opaque callbacks to avoid the
            # generic /card synthetic command.  Keep it rather than guessing.
            return None

        if "hermes_slash_confirm_action" in action_value:
            return await _handle_slash_confirm_card_action(self_feishu, data, action_value)

        if "hermes_clarify_action" in action_value:
            return await _handle_clarify_card_action(
                self_feishu,
                data,
                _as_text(action_value.get("hermes_clarify_action")),
                action_value,
            )

        # Card 2.0 form-submit buttons intentionally have no ``behaviors``
        # marker.  Identify this one native path by the actual rendered card
        # id plus its documented button/form shape; never infer it from a
        # form name or optional action.value payload.
        clarify_id = _native_multi_form_submit_clarify_id(data)
        if clarify_id:
            return await _handle_clarify_card_action(
                self_feishu,
                data,
                "multi_submit",
                {"clarify_id": clarify_id},
            )

        # These are Hermes' own action routes.  Do not let the new marker
        # router intercept or reorder them; the stale SDK bound-method path
        # reaches this dynamically patched method too.
        if action_value.get("hermes_action") or action_value.get("hermes_update_prompt_action"):
            return await _call_original_card_handler(original_method, self_feishu, data)

        # No plugin marker: preserve the prior /card suppression behavior.
        return None

    return _wrapped
