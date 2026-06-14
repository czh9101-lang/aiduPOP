"""CardSession — 单条消息的卡片会话状态."""

from __future__ import annotations

import asyncio
import logging
import time
import warnings
from threading import Lock
from typing import TYPE_CHECKING, Any

# Phase constants — single source of truth in state.phase
from .phase import (
    CardPhase,
    TerminalReason,
    CardVisualState,
    TERMINAL_PHASES,
    _TERMINAL,
    is_legal_transition,
    get_visual_state,
    PHASE_TO_VISUAL,
)

# Backward-compatible aliases — old code imports IDLE / FAILED from this module
IDLE = CardPhase.IDLE
FAILED = CardPhase.FAILED  # DEPRECATED: "creation_failed" — use CardPhase.CREATION_FAILED

from ..flush import PATCH_MS, FlushController
from .linear import UnifiedLinearState
from .text import TextState
from .tooluse import ToolUseTracker
from ..feishu import UnavailableGuard

if TYPE_CHECKING:
    pass

_logger = logging.getLogger("hermes_lark_streaming")


class CardSession:
    """单条消息的卡片会话状态."""

    __slots__ = (
        "_card_ready",
        "_create_epoch_snap",
        "_first_answer_time",
        "_first_flush_done",
        "_loading_hint_removed",
        "_loop",
        "_panel_element_created",
        "_answer_element_created",
        "_pending_flush",
        "_streaming_closed",
        "_was_aborted",
        "anchor_id",
        "card_created_at",
        "card_id",
        "card_msg_id",
        "chat_id",
        "create_epoch",
        "created_at",
        "deferred_background_review_closed",
        "deferred_background_review_lock",
        "deferred_background_reviews",
        "error_message",
        "existing_elements",
        "flush",
        "footer",
        "guard",
        "last_tool_use_update",
        "linear",
        "message_id",
        "reasoning_dirty",
        "reasoning_start",
        "reasoning_text",
        "sequence",
        "state",
        "terminal_reason",
        "terminal_source",
        "text",
        "tool_use",
        "unified_state",
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
        self.state: str = IDLE
        self.card_msg_id: str | None = None
        self.card_id: str | None = None
        self.use_cardkit: bool = False
        self.text = TextState()
        self.tool_use = ToolUseTracker()
        self.flush = FlushController(throttle_ms=PATCH_MS)
        self.reasoning_text = ""
        self.reasoning_start: float = 0.0
        self.reasoning_dirty = False
        self.footer: dict[str, Any] = {}
        self.sequence = 1
        self._loop = loop
        self.last_tool_use_update = 0.0
        self.created_at = time.time()
        self.deferred_background_review_closed = False
        self.deferred_background_reviews: list[tuple[str, Any]] = []
        self.deferred_background_review_lock = Lock()

        # ── State machine enhancements ──
        self.create_epoch: int = 0          # Incremented on terminal phase entry
        self._create_epoch_snap: int = 0    # Snapshotted when creation starts
        self.terminal_reason: str = ""      # Why the session ended
        self.terminal_source: str = ""      # Which code path triggered terminal

        self.guard = UnavailableGuard(
            reply_to_message_id=message_id,
            get_card_message_id=lambda: self.card_msg_id,
            on_terminate=self._on_guard_terminate,
        )

        self.linear = False
        self.unified_state: UnifiedLinearState | None = None
        self.existing_elements: set[str] = set()
        self._panel_element_created: bool = False
        self._answer_element_created: bool = False
        self.card_created_at: float = 0.0
        self._was_aborted: bool = False
        self.error_message: str = ""
        self._first_flush_done: bool = False
        self._first_answer_time: float = 0.0
        self._loading_hint_removed: bool = False
        self._pending_flush: bool = False
        self._streaming_closed: bool = False
        self._card_ready: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # State machine — validated transitions
    # ------------------------------------------------------------------

    def transition(self, to: str, source: str = "", reason: str = "") -> bool:
        """Attempt a validated state transition.

        Returns True if the transition was legal and applied, False if
        rejected.  Illegal transitions are logged but do not raise.

        If *to* is a terminal phase and *reason* is provided, the
        terminal_reason / terminal_source fields are set automatically.
        """
        from_phase = self.state
        if from_phase == to:
            return True  # idempotent

        if not is_legal_transition(from_phase, to):
            _logger.warning(
                "phase transition rejected: %s → %s (source=%s msg=%s)",
                from_phase, to, source, (self.message_id or "?")[:12],
            )
            return False

        self.state = to
        _logger.info(
            "phase transition: %s → %s (source=%s msg=%s)",
            from_phase, to, source, (self.message_id or "?")[:12],
        )

        # Track terminal metadata
        if to in TERMINAL_PHASES:
            self.enter_terminal(
                reason=reason or TerminalReason.ERROR,
                source=source,
            )

        # Snapshot epoch when entering CREATING (for stale-create detection)
        if to == CardPhase.CREATING:
            self._create_epoch_snap = self.create_epoch

        return True

    def should_proceed(self, source: str = "") -> bool:
        """Unified guard: returns True if the session can accept updates.

        Combines:
        1. Terminal phase check — session is done
        2. Unavailable guard — message was deleted/recalled

        Side effect: if the guard detects the message is unavailable,
        it will trigger termination (setting TERMINATED state).
        """
        if self.state in TERMINAL_PHASES:
            return False
        if self.guard.should_skip(source):
            return False
        return True

    @property
    def is_terminal_phase(self) -> bool:
        """Whether the session is in a terminal (absorbing) phase."""
        return self.state in TERMINAL_PHASES

    @property
    def visual_state(self) -> str:
        """Current visual state derived from the lifecycle phase."""
        return get_visual_state(self.state)

    def is_stale_create(self, epoch: int) -> bool:
        """Check if a creation callback is stale (epoch mismatch).

        Usage::

            epoch = session.create_epoch
            # ... await card creation ...
            if session.is_stale_create(epoch):
                return  # Stale callback, ignore
        """
        return epoch != self.create_epoch

    def enter_terminal(self, reason: str, source: str = "") -> None:
        """Enter a terminal phase with reason tracking.

        This is called automatically by ``transition()`` when the target
        is a terminal phase, but can also be called directly when setting
        ``session.state`` without ``transition()`` (legacy code path).
        """
        if self.terminal_reason:
            return  # Already recorded — keep the first reason
        self.terminal_reason = reason
        self.terminal_source = source
        self.create_epoch += 1

    # ------------------------------------------------------------------
    # Internal callbacks
    # ------------------------------------------------------------------

    def _on_guard_terminate(self) -> None:
        """Callback from UnavailableGuard — message was deleted/recalled."""
        if self.state in TERMINAL_PHASES:
            return
        self.state = CardPhase.TERMINATED
        self.enter_terminal(
            reason=TerminalReason.UNAVAILABLE,
            source="unavailable_guard",
        )
        # Signal readiness so awaiters don't deadlock
        self._card_ready.set()

    # ------------------------------------------------------------------
    # Backward compatibility — linear_state → unified_state
    # ------------------------------------------------------------------

    @property
    def linear_state(self) -> UnifiedLinearState | None:
        """DEPRECATED: Use unified_state instead."""
        warnings.warn(
            "linear_state is deprecated; use unified_state instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.unified_state

    @linear_state.setter
    def linear_state(self, value: UnifiedLinearState | None) -> None:
        warnings.warn(
            "linear_state is deprecated; use unified_state instead",
            DeprecationWarning,
            stacklevel=2,
        )
        self.unified_state = value
