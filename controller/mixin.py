"""异步卡片 API 编排 — 创建、更新、完成卡片的重试/降级逻辑.

v1.1.0 (Task 1.1+1.2): The non-linear ControllerMixin methods
(``_do_create_card``, ``_do_update_card``, ``_do_tool_use_status_update``,
``_do_reasoning_update``, ``_do_complete``, ``_do_complete_inner``) were
removed. The plugin now uses the linear path exclusively — when CardKit
v2 creation fails, it falls back directly to ``build_im_fallback_card``
(handled inside ``_do_create_linear_card``), NOT to the legacy
"segmented CardKit v1 cards" path.

This module now contains only the shared gateway / cron delivery
helpers used by both legacy code paths and the linear path.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from ..cardkit import (
    build_cron_card,
    build_gateway_card,
)

# ── Phase constants — single source of truth ─────────────────────────
from ..state.phase import (
    CardPhase,
    _TERMINAL,
)

# Re-export as module-level names for backward compatibility.
# Existing code that does ``from .mixin import IDLE`` continues
# to work.  New code should import from ``..state.phase`` directly.
IDLE = CardPhase.IDLE
CREATING = CardPhase.CREATING
STREAMING = CardPhase.STREAMING
COMPLETING = CardPhase.COMPLETING
COMPLETED = CardPhase.COMPLETED
CREATION_FAILED = CardPhase.CREATION_FAILED
ABORTED = CardPhase.ABORTED
TERMINATED = CardPhase.TERMINATED

if TYPE_CHECKING:
    from ..config import Config
    from ..state.session import CardSession
    from ..feishu import FeishuClient

_logger = logging.getLogger("hermes_lark_streaming")

__all__ = [
    "ControllerMixin",
    "IDLE",
    "CREATING",
    "STREAMING",
    "COMPLETING",
    "COMPLETED",
    "CREATION_FAILED",
    "TERMINATED",
    "ABORTED",
    "_TERMINAL",
]


class ControllerMixin:
    """异步卡片 API 操作 — 由 StreamCardController 继承.

    v1.1.0: All non-linear card-creation / update / complete methods
    were removed (Task 1.1+1.2). What remains here are the shared
    gateway and cron delivery helpers that are independent of the
    streaming-card lifecycle.
    """

    _client: FeishuClient | None
    _cfg: Config
    _ensure_init: Callable[[], Coroutine[Any, Any, None]]
    _cleanup: Callable[[str], None]
    _flush_deferred_background_reviews: Callable[[CardSession], None]

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
