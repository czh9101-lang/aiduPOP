"""异步卡片 API 编排 — 创建、更新、完成卡片的重试/降级逻辑."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from ..cardkit import (
    STREAMING_ELEMENT_ID,
    build_complete_card,
    build_cron_card,
    build_gateway_card,
    build_im_fallback_card,
    build_streaming_card,
    build_streaming_card_v2,
)
from ..cardkit.elements import (
    REASONING_TEXT_ELEMENT_ID,
    TOOL_PANEL_ELEMENT_ID,
    _build_tool_panel,
)
from ..cardkit.md import (
    _downgrade_tables,
    optimize_markdown_style,
)
from ..feishu import (
    CARDKIT_CONTENT_FAILED,
    CARDKIT_ELEMENT_LIMIT,
    CARDKIT_ELEMENT_LIMIT_DIRECT,
    CARDKIT_RATE_LIMITED,
    CARDKIT_SEQUENCE_CONFLICT,
    CARDKIT_STREAMING_CLOSED,
    FeishuAPIError,
    is_element_limit_error,
)
from ..flush import CARDKIT_MS, PATCH_MS

if TYPE_CHECKING:
    from ..config import Config
    from ..state.session import CardSession
    from ..feishu import FeishuClient

_logger = logging.getLogger("hermes_lark_streaming")

IDLE = "idle"
CREATING = "creating"
STREAMING = "streaming"
COMPLETING = "completing"
COMPLETED = "completed"
FAILED = "failed"
ABORTED = "aborted"

# True terminal states — session is done and will never accept updates.
# COMPLETING is intentionally NOT a terminal state: it is a transitional
# state during which late-arriving on_answer / on_thinking callbacks
# must still be able to update unified_state.  The drain logic in
# _do_linear_complete will flush any remaining dirty data before
# closing streaming.  If COMPLETING were in _TERMINAL, _get_active_session
# would return None and those callbacks would silently drop content,
# causing the "footer appears before content finishes streaming" bug.
_TERMINAL = {COMPLETED, FAILED, ABORTED}

__all__ = [
    "ControllerMixin",
    "IDLE",
    "CREATING",
    "STREAMING",
    "COMPLETING",
    "COMPLETED",
    "FAILED",
    "ABORTED",
    "_TERMINAL",
]


class ControllerMixin:
    """异步卡片 API 操作 — 由 StreamCardController 继承."""

    _client: FeishuClient | None
    _cfg: Config
    _ensure_init: Callable[[], Coroutine[Any, Any, None]]
    _schedule_card_update: Callable[[CardSession], None]
    _cleanup: Callable[[str], None]
    _flush_deferred_background_reviews: Callable[[CardSession], None]

    async def _do_create_card(self, session: CardSession) -> None:
        if session.state != IDLE:
            return
        session.state = CREATING

        try:
            await self._ensure_init()
            assert self._client is not None

            try:
                reply_to_message_id = session.anchor_id or session.message_id
                card = build_streaming_card_v2(
                    show_tool_use=False, show_reasoning=self._cfg.show_reasoning,
                    streaming_panel_expanded=self._cfg.streaming_panel_expanded,
                    print_strategy=self._cfg.print_strategy,
                    header_enabled=self._cfg.header_enabled,
                )
                card_id = await self._client.cardkit_create(card)
                card_msg_id = await self._client.reply_card_by_id(
                    reply_to_message_id,
                    card_id,
                )
                session.card_id = card_id
                session.card_msg_id = card_msg_id
                session.use_cardkit = True
                session.flush.set_throttle(self._cfg.flush_interval_sec)
            except FeishuAPIError:
                card = build_im_fallback_card()
                card_msg_id = await self._client.reply_card(
                    reply_to_message_id,
                    card,
                )
                session.card_msg_id = card_msg_id
                session.use_cardkit = False
                session.flush.set_throttle(PATCH_MS)

            session.flush.set_card_message_ready(True)
            if session.state == CREATING:
                session.state = STREAMING
            # Signal card readiness so _do_complete_inner can proceed
            session._card_ready.set()
            _logger.info(
                "card created: msg=%s cardkit=%s card_id=%s",
                (session.message_id or "?")[:12],
                session.use_cardkit,
                (session.card_id or "")[:12],
            )
        except Exception:
            _logger.exception("_do_create_card failed")
            session.state = FAILED
            # Signal readiness even on failure so awaiters don't deadlock
            session._card_ready.set()

    async def _do_update_card(self, session: CardSession) -> None:
        if session.state not in (CREATING, STREAMING, COMPLETING):
            return
        if not session.card_msg_id:
            return
        if session.guard.should_skip("_do_update_card"):
            return

        display = session.text.display_text
        if not session.text.is_dirty(display) and not session.reasoning_dirty:
            _logger.info(
                "update_card skipped (not dirty): msg=%s len=%d",
                (session.message_id or "?")[:12],
                len(display),
            )
            return

        _logger.info(
            "update_card: msg=%s seq=%d len=%d cardkit=%s",
            (session.message_id or "?")[:12],
            session.sequence + 1,
            len(display),
            session.use_cardkit,
        )

        try:
            assert self._client is not None
            if session.use_cardkit and session.card_id:
                if session.reasoning_dirty and session.reasoning_panel_added:
                    reasoning_content = optimize_markdown_style(session.reasoning_text) or " "
                    session.sequence += 1
                    await self._client.cardkit_stream_element(
                        session.card_id,
                        REASONING_TEXT_ELEMENT_ID,
                        reasoning_content,
                        sequence=session.sequence,
                    )
                    session.reasoning_dirty = False

                optimized = _downgrade_tables(optimize_markdown_style(display))
                session.sequence += 1
                await self._client.cardkit_stream_element(
                    session.card_id,
                    STREAMING_ELEMENT_ID,
                    optimized or " ",
                    sequence=session.sequence,
                )
            else:
                tool_steps = session.tool_use.build_display_steps()
                card = build_streaming_card(
                    tool_steps=tool_steps,
                    reasoning_text=session.reasoning_text if self._cfg.show_reasoning else "",
                    text=display,
                )
                await self._client.update_card(session.card_msg_id, card)

            session.text.mark_flushed(display)
            session.reasoning_dirty = False
        except FeishuAPIError as e:
            if session.guard.terminate("_do_update_card", e):
                return

            if e.code == CARDKIT_RATE_LIMITED:
                _logger.info("rate limited, skipping frame")
                return

            if e.code == CARDKIT_STREAMING_CLOSED:
                _logger.info("streaming mode closed, skipping update: msg=%s", (session.message_id or "?")[:12])
                return

            if is_element_limit_error(e):
                _logger.warning("card element limit exceeded, disabling CardKit streaming")
                session.use_cardkit = False
                session.flush.set_throttle(PATCH_MS)
                return

            _logger.warning("card update failed: %s", e, exc_info=True)

    async def _do_tool_use_status_update(self, session: CardSession) -> None:
        if not session.card_id or session.state in _TERMINAL or session.state == COMPLETING:
            return
        try:
            assert self._client is not None
            tool_steps = session.tool_use.build_display_steps()
            panel = _build_tool_panel(
                tool_steps,
                session.tool_use.elapsed_ms,
                expanded=self._cfg.streaming_panel_expanded,
            )
            if not session.tool_panel_added:
                actions = [
                    {
                        "action": "add_elements",
                        "params": {
                            "type": "insert_before",
                            "target_element_id": STREAMING_ELEMENT_ID,
                            "elements": [panel],
                        },
                    }
                ]
            else:
                actions = [
                    {
                        "action": "update_element",
                        "params": {
                            "element_id": TOOL_PANEL_ELEMENT_ID,
                            "element": panel,
                        },
                    }
                ]
            session.sequence += 1
            _logger.info(
                "tool_update: msg=%s seq=%d action=%s steps=%d",
                (session.message_id or "?")[:12],
                session.sequence,
                "add" if not session.tool_panel_added else "update",
                len(tool_steps),
            )
            await self._client.cardkit_batch_update(
                session.card_id,
                actions,
                sequence=session.sequence,
            )
            session.tool_panel_added = True
        except Exception as e:
            _logger.debug("tool use status update failed: %s", e, exc_info=True)

    async def _do_reasoning_update(self, session: CardSession) -> None:
        if not session.card_id or session.state in _TERMINAL or session.state == COMPLETING:
            return
        if not session.reasoning_dirty:
            return
        try:
            assert self._client is not None
            content = optimize_markdown_style(session.reasoning_text) or " "

            session.sequence += 1
            _logger.info(
                "reasoning_update: msg=%s seq=%d len=%d",
                (session.message_id or "?")[:12],
                session.sequence,
                len(session.reasoning_text),
            )
            await self._client.cardkit_stream_element(
                session.card_id,
                REASONING_TEXT_ELEMENT_ID,
                content,
                sequence=session.sequence,
            )
            session.reasoning_panel_added = True
            session.reasoning_dirty = False
        except Exception as e:
            _logger.debug("reasoning update failed: %s", e, exc_info=True)

    async def _do_complete(self, session: CardSession) -> bool:
        try:
            return await self._do_complete_inner(session)
        finally:
            self._flush_deferred_background_reviews(session)
            self._release_session_data(session)
            self._cleanup(session.message_id)

    async def _do_complete_inner(self, session: CardSession) -> bool:
        if session.guard.should_skip("_do_complete"):
            return False

        await session.flush.wait_for_flush()
        session.flush.mark_completed()

        # ── Wait for card creation to finish ──
        # Same race condition fix as linear mode: when on_completed fires
        # before _do_create_card finishes, card_id/card_msg_id are still None.
        try:
            await asyncio.wait_for(session._card_ready.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            _logger.warning(
                "complete: card creation timed out, msg=%s",
                (session.message_id or "?")[:12],
            )

        # If card creation failed, we cannot render a completion card.
        # Return False so card_sent=False → gateway sends its own text reply.
        if not session.card_id and not session.card_msg_id:
            _logger.info(
                "complete: no card to complete, msg=%s state=%s",
                (session.message_id or "?")[:12],
                session.state,
            )
            session.state = FAILED
            return False

        display = session.text.display_text
        _logger.info(
            "do_complete: msg=%s state=%s display_len=%d cardkit=%s seq=%d",
            (session.message_id or "?")[:12],
            session.state,
            len(display),
            session.use_cardkit,
            session.sequence,
        )
        reasoning_elapsed_ms = 0.0
        if session.reasoning_start:
            reasoning_elapsed_ms = (time.time() - session.reasoning_start) * 1000

        is_error = session.state == FAILED
        # COMPLETING 状态下需通过 _was_aborted 获取中断标记
        is_aborted = getattr(session, "_was_aborted", False) or session.state == ABORTED
        error_message = getattr(session, "error_message", "")
        card = build_complete_card(
            text=display,
            reasoning_text=session.reasoning_text if self._cfg.show_reasoning else "",
            reasoning_elapsed_ms=reasoning_elapsed_ms,
            tool_steps=session.tool_use.build_display_steps(),
            tool_elapsed_ms=session.tool_use.elapsed_ms,
            footer_data=session.footer,
            has_cardkit=session.use_cardkit,
            is_error=is_error,
            is_aborted=is_aborted,
            error_message=error_message,
            footer_fields=self._cfg.footer_fields,
            footer_show_label=self._cfg.footer_show_label,
            panel_expanded=self._cfg.panel_expanded,
            header_enabled=self._cfg.header_enabled,
        )

        for attempt in range(3):
            try:
                assert self._client is not None
                if session.use_cardkit and session.card_id:
                    # Update summary when closing streaming so the
                    # conversation list shows the answer, not "处理中..."
                    complete_summary = (display or "")[:120].replace("\n", " ").replace("```", "").strip()
                    await self._client.cardkit_close_streaming(
                        session.card_id,
                        sequence=session.sequence + 1,
                        summary=complete_summary,
                    )
                    session.sequence += 1
                    await self._client.cardkit_update(
                        session.card_id,
                        card,
                        sequence=session.sequence + 1,
                    )
                    session.sequence += 1
                elif session.card_msg_id:
                    await self._client.update_card(session.card_msg_id, card)
                session.state = COMPLETED
                return True
            except FeishuAPIError as e:
                # 300317 sequence 冲突 → 幂等成功
                # hermes 可能双调 on_completed（finally + pop_post_delivery_callback），
                # 竞态窗口内两次调用触发 300317，表示另一条路径已完成操作。
                if e.code == CARDKIT_SEQUENCE_CONFLICT:
                    _logger.info(
                        "do_complete: 300317 sequence conflict → idempotent success, "
                        "card_id=%s seq=%d",
                        session.card_id,
                        session.sequence,
                    )
                    session.state = COMPLETED
                    return True
                _logger.warning(
                    "cardkit complete attempt %d failed (FeishuAPIError): code=%s msg=%s card_id=%s seq=%d",
                    attempt,
                    e.code,
                    e,
                    session.card_id,
                    session.sequence,
                    exc_info=True,
                )
                if session.guard.terminate("_do_complete", e):
                    return False
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                continue
            except Exception as e:
                _logger.warning(
                    "cardkit complete attempt %d failed: %s: %s card_id=%s card_msg_id=%s seq=%d",
                    attempt,
                    type(e).__name__,
                    e,
                    session.card_id,
                    session.card_msg_id,
                    session.sequence,
                    exc_info=True,
                )
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                continue

        _logger.error(
            "cardkit complete failed after 3 attempts: card_id=%s card_msg_id=%s seq=%d",
            session.card_id,
            session.card_msg_id,
            session.sequence,
        )
        session.state = FAILED
        return False

    async def _do_cron_deliver(self, chat_id: str, content: str) -> None:
        _logger.info("cron _do_cron_deliver: chat=%s content_len=%d", chat_id[:12], len(content))
        await self._ensure_init()
        assert self._client is not None
        card = build_cron_card(content)
        await self._client.send_card_to_chat(chat_id, card)

    async def _do_gateway_deliver(
        self,
        chat_id: str,
        content: str,
        *,
        category: str = "",
    ) -> tuple[str | None, str | None]:
        """Send a gateway-internal message as a card.

        Returns ``(card_msg_id, card_id)`` on success, or ``(None, None)``
        on failure (caller should fall back to the original adapter.send).

        ``card_id`` is the CardKit container ID (for streaming updates),
        ``card_msg_id`` is the Feishu message ID (for edit_message routing).
        """
        try:
            await self._ensure_init()
            assert self._client is not None
            card = build_gateway_card(
                content,
                category=category,
            )
            # Use send_card_to_chat which returns card_msg_id
            card_msg_id = await self._client.send_card_to_chat(chat_id, card)
            _logger.info(
                "gateway card delivered: chat=%s category=%s card_msg_id=%s "
                "content_len=%d",
                chat_id[:12],
                category or "system",
                card_msg_id[:12] if card_msg_id else None,
                len(content),
            )
            return card_msg_id, None  # No card_id for static gateway cards
        except Exception:
            _logger.warning("gateway card delivery failed, caller should fall back", exc_info=True)
            return None, None

    async def _do_gateway_card_update(
        self,
        *,
        chat_id: str,
        card_msg_id: str,
        card_id: str | None = None,
        content: str,
        category: str = "",
    ) -> bool:
        """Update a gateway card's content (called from edit_message interception).

        Returns True on success, False on failure (caller should fall back
        to the original edit_message).

        Strategy:
        - If card_id is available (CardKit container), use cardkit_update.
        - Otherwise, use update_card (IM PATCH mode) with a rebuilt card.
        """
        try:
            await self._ensure_init()
            assert self._client is not None
            card = build_gateway_card(content, category=category)

            if card_id:
                # CardKit container — update via cardkit_update
                await self._client.cardkit_update(card_id, card)
                _logger.info(
                    "gateway card updated (cardkit): card_id=%s category=%s",
                    card_id[:12],
                    category,
                )
                return True
            else:
                # IM PATCH mode — update via update_card
                await self._client.update_card(card_msg_id, card)
                _logger.info(
                    "gateway card updated (im_patch): card_msg_id=%s category=%s",
                    card_msg_id[:12],
                    category,
                )
                return True
        except Exception:
            _logger.debug(
                "gateway card update failed: card_msg_id=%s card_id=%s",
                card_msg_id[:12],
                (card_id or "?")[:12],
                exc_info=True,
            )
            return False

    async def _do_gateway_card_status(
        self,
        *,
        card_msg_id: str,
        card_id: str | None = None,
        status_label: str,
        emoji: str,
        category: str = "",
    ) -> bool:
        """Update a gateway card's status indicator (from reaction interception).

        Returns True on success, False on failure.

        When Hermes adds/removes a reaction (👀, 👍, etc.) on a gateway
        card message, we update the card to show/clear a status indicator
        instead of the emoji reaction.

        The status is shown as a subtle text line at the top of the card,
        replacing the category icon header when a status is active.
        """
        try:
            await self._ensure_init()
            assert self._client is not None
            card = build_gateway_card(
                "",
                category=category,
                status_label=status_label,
                status_emoji=emoji,
            )
            if card_id:
                await self._client.cardkit_update(card_id, card)
            else:
                await self._client.update_card(card_msg_id, card)
            _logger.info(
                "gateway card status: card_msg_id=%s status=%s emoji=%s",
                card_msg_id[:12],
                status_label or "(cleared)",
                emoji or "(none)",
            )
            return True
        except Exception:
            _logger.debug(
                "gateway card status update failed: card_msg_id=%s",
                card_msg_id[:12],
                exc_info=True,
            )
            return False
