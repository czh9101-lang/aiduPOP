"""CardSession — 单条消息的卡片会话状态."""

from __future__ import annotations

import asyncio
import time
from threading import Lock
from typing import TYPE_CHECKING, Any

# Session state constants — defined here to avoid circular imports with controller.mixin
IDLE = "idle"
FAILED = "failed"

from ..flush import PATCH_MS, FlushController
from .text import TextState
from .tooluse import ToolUseTracker
from ..unavailable_guard import UnavailableGuard

if TYPE_CHECKING:
    from .linear import LinearState


class CardSession:
    """单条消息的卡片会话状态."""

    __slots__ = (
        "_card_ready",
        "_first_answer_time",
        "_first_flush_done",
        "_loading_hint_removed",
        "_loop",
        "_was_aborted",
        "anchor_id",
        "card_id",
        "card_msg_id",
        "chat_id",
        "created_at",
        "deferred_background_review_closed",
        "deferred_background_review_lock",
        "deferred_background_reviews",
        "element_count",
        "element_limit_hit",
        "error_message",
        "flush",
        "footer",
        "guard",
        "last_tool_use_update",
        "linear",
        "linear_state",
        "message_id",
        "reasoning_dirty",
        "reasoning_panel_added",
        "reasoning_start",
        "reasoning_text",
        "sequence",
        "split_disabled",
        "split_index",
        "state",
        "text",
        "tool_panel_added",
        "tool_use",
        "use_cardkit",
    )

    def __init__(
        self,
        message_id: str,
        chat_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.message_id = message_id
        self.anchor_id: str | None = None
        self.chat_id = chat_id
        self.state = IDLE
        self.card_msg_id: str | None = None
        self.card_id: str | None = None
        self.use_cardkit: bool = False
        self.text = TextState()
        self.tool_use = ToolUseTracker()
        self.flush = FlushController(throttle_ms=PATCH_MS)
        self.reasoning_text = ""
        self.reasoning_start: float = 0.0
        self.reasoning_dirty = False
        self.reasoning_panel_added = False
        self.footer: dict[str, Any] = {}
        self.sequence = 1
        self._loop = loop
        self.last_tool_use_update = 0.0
        self.created_at = time.time()
        self.deferred_background_review_closed = False
        self.deferred_background_reviews: list[tuple[str, Any]] = []
        self.deferred_background_review_lock = Lock()

        self.guard = UnavailableGuard(
            reply_to_message_id=message_id,
            get_card_message_id=lambda: self.card_msg_id,
            on_terminate=lambda: setattr(self, "state", FAILED),
        )

        self.tool_panel_added = False
        self.linear = False
        self.linear_state: LinearState | None = None
        self.element_count: int = 0
        self.element_limit_hit = False
        self.split_disabled = False
        self.split_index: int = 0
        self._was_aborted: bool = False
        self.error_message: str = ""
        self._first_flush_done: bool = False
        self._first_answer_time: float = 0.0
        self._loading_hint_removed: bool = False
        self._card_ready: asyncio.Event = asyncio.Event()
