"""E2E test framework — orchestrates full message → card pipeline tests.

Usage:
    from tests.e2e.framework import E2ETestRunner

    async def test_simple_answer():
        runner = E2ETestRunner()
        await runner.setup()
        try:
            session = await runner.start_message("hello", chat_id="oc_test")
            await runner.feed_answer(session, "Hello world!")
            await runner.complete(session)
            card = runner.get_card(session)
            assert card is not None
            assert "Hello world!" in runner.get_answer_text(session)
        finally:
            await runner.teardown()
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from .mock_feishu import MockFeishuServer, MockCardState

_logger = logging.getLogger("hermes_lark_streaming")


class E2ETestRunner:
    """Orchestrates end-to-end tests with mock Feishu server.

    Patches FeishuClient to use the mock server, creates real CardSession
    objects, and feeds realistic callback sequences to verify card output.
    """

    def __init__(self) -> None:
        self.mock_server = MockFeishuServer()
        self._patches: list[Any] = []
        self._controller: Any = None
        self._sessions: dict[str, Any] = {}

    async def setup(self) -> None:
        """Set up mock environment — patch FeishuClient, create controller."""
        # Patch FeishuClient to use mock server
        from hermes_lark_streaming.feishu import FeishuClient, FeishuClientConfig

        original_init = FeishuClient.__init__

        def _mock_init(self: FeishuClient, config: FeishuClientConfig) -> None:
            self.config = config
            self._mock_server = self_mock_server  # type: ignore
            self._use_async_stream_element = True

        # We can't easily patch FeishuClient because it's deeply integrated.
        # Instead, create a real controller but replace its _client with a mock.
        from hermes_lark_streaming.controller import StreamCardController
        from hermes_lark_streaming.config import Config

        self._controller = StreamCardController()

        # Force enable
        self._controller._cfg = MagicMock(spec=Config)
        cfg = self._controller._cfg
        cfg.enabled = True
        cfg.linear = True
        cfg.gateway_cards = True
        cfg.inject_time = False
        cfg.flush_interval_ms = 50  # Fast for tests
        cfg.flush_interval_sec = 0.05
        cfg.card_duration_sec = 600
        cfg.print_strategy = "fast"
        cfg.show_reasoning = True
        cfg.panel_expanded = False
        cfg.streaming_panel_expanded = False
        cfg.header_enabled = False
        cfg.max_tool_steps = 20
        cfg.max_reasoning_rounds = 20
        cfg.footer_fields = [["status", "elapsed", "model", "cost"]]
        cfg.footer_show_label = False
        cfg.feishu_app_id = "mock_app_id"
        cfg.feishu_app_secret = "mock_app_secret"
        cfg.feishu_base_url = "https://mock.feishu.cn"
        cfg.env_app_id = ""
        cfg.env_app_secret = ""

        # Initialize with mock client
        self._controller._client = self._create_mock_client()
        self._controller._initialized = True

        # Patch _ensure_init to be a no-op (already initialized)
        self._controller._ensure_init = AsyncMock()

    def _create_mock_client(self) -> Any:
        """Create a mock FeishuClient that delegates to MockFeishuServer."""
        mock_client = MagicMock()
        srv = self.mock_server

        async def _cardkit_create(card):
            return await srv.cardkit_create(card)
        async def _reply_card_by_id(mid, cid):
            return await srv.reply_card_by_id(mid, cid)
        async def _reply_card(mid, card):
            return await srv.reply_card(mid, card)
        async def _cardkit_stream_element(cid, eid, content, *, sequence=0):
            return await srv.cardkit_stream_element(cid, eid, content, sequence=sequence)
        async def _cardkit_batch_update(cid, actions, *, sequence=0):
            return await srv.cardkit_batch_update(cid, actions, sequence=sequence)
        async def _cardkit_close_streaming(cid, *, sequence=0, summary=""):
            return await srv.cardkit_close_streaming(cid, sequence=sequence, summary=summary)
        async def _cardkit_update_summary(cid, summary, *, sequence=0):
            return await srv.cardkit_update_summary(cid, summary, sequence=sequence)
        async def _cardkit_extend_ttl(cid, *, ttl_seconds, sequence=0):
            return await srv.cardkit_extend_ttl(cid, ttl_seconds=ttl_seconds, sequence=sequence)
        async def _cardkit_update(cid, card, *, sequence=0):
            return await srv.cardkit_update(cid, card, sequence=sequence)
        async def _update_card(mid, card):
            return await srv.update_card(mid, card)
        async def _send_card_to_chat(chat_id, card):
            return await srv.send_card_to_chat(chat_id, card)
        async def _reply_text(mid, text):
            return await srv.reply_text(mid, text)

        mock_client.cardkit_create = _cardkit_create
        mock_client.reply_card_by_id = _reply_card_by_id
        mock_client.reply_card = _reply_card
        mock_client.cardkit_stream_element = _cardkit_stream_element
        mock_client.cardkit_batch_update = _cardkit_batch_update
        mock_client.cardkit_close_streaming = _cardkit_close_streaming
        mock_client.cardkit_update_summary = _cardkit_update_summary
        mock_client.cardkit_extend_ttl = _cardkit_extend_ttl
        mock_client.cardkit_update = _cardkit_update
        mock_client.update_card = _update_card
        mock_client.send_card_to_chat = _send_card_to_chat
        mock_client.reply_text = _reply_text

        return mock_client

    async def teardown(self) -> None:
        """Clean up patches and state."""
        for p in self._patches:
            p.stop()
        self.mock_server.reset()
        self._sessions.clear()

    @property
    def controller(self) -> Any:
        return self._controller

    # ── Test flow helpers ──

    async def start_message(
        self,
        message_text: str = "test message",
        *,
        message_id: str = "",
        chat_id: str = "oc_test_chat",
    ) -> Any:
        """Start a new message session — creates placeholder card."""
        if not message_id:
            message_id = f"om_test_{int(time.time()*1000)}"

        loop = asyncio.get_event_loop()
        self._controller.on_message_started(
            message_id=message_id,
            chat_id=chat_id,
            anchor_id=message_id,
        )

        # Wait for card creation
        session = self._controller._sessions.get(message_id)
        if session:
            # Wait for _card_ready
            await asyncio.wait_for(session._card_ready.wait(), timeout=5.0)
        self._sessions[message_id] = session
        return session

    async def feed_answer(self, session: Any, text: str) -> None:
        """Feed an answer text delta to the session."""
        self._controller.on_answer(
            message_id=session.message_id,
            text=text,
        )
        # Allow flush to process
        await asyncio.sleep(0.15)

    async def feed_reasoning(self, session: Any, text: str) -> None:
        """Feed a reasoning delta to the session."""
        self._controller.on_reasoning(
            message_id=session.message_id,
            text=text,
        )
        await asyncio.sleep(0.15)

    async def feed_tool_update(
        self,
        session: Any,
        tool_name: str,
        status: str,
        detail: str = "",
    ) -> None:
        """Feed a tool update to the session."""
        self._controller.on_tool_update(
            message_id=session.message_id,
            tool_name=tool_name,
            status=status,
            detail=detail,
        )
        await asyncio.sleep(0.15)

    async def complete(self, session: Any, answer: str = "") -> None:
        """Complete the message — triggers seal."""
        self._controller.on_completed(
            message_id=session.message_id,
            answer=answer,
        )
        # Wait for seal to complete
        await asyncio.sleep(0.5)

    # ── Verification helpers ──

    def get_card(self, session: Any) -> MockCardState | None:
        """Get the mock card state for a session."""
        if session.card_id:
            return self.mock_server.get_card(session.card_id)
        return None

    def get_answer_text(self, session: Any) -> str:
        """Extract answer text from the card."""
        card = self.get_card(session)
        if card is None:
            return ""
        # Answer is in the answer_content element
        from hermes_lark_streaming.cardkit.elements import ANSWER_ELEMENT_ID
        answer_el = card.elements.get(ANSWER_ELEMENT_ID, {})
        return answer_el.get("content", "")

    def get_panel_elements(self, session: Any) -> list[dict[str, Any]]:
        """Get the panel children from the card."""
        card = self.get_card(session)
        if card is None:
            return []
        from hermes_lark_streaming.cardkit.elements import UNIFIED_PANEL_ELEMENT_ID
        panel = card.elements.get(UNIFIED_PANEL_ELEMENT_ID, {})
        return panel.get("elements", [])

    def get_call_count(self, operation: str) -> int:
        """Count how many times a specific API operation was called."""
        return sum(1 for call in self.mock_server.call_log if call["op"] == operation)

    def get_total_api_calls(self) -> int:
        """Total API calls made during the test."""
        return len(self.mock_server.call_log)

    def assert_card_created(self, session: Any) -> None:
        """Assert that a card was created for this session."""
        assert session.card_id is not None, "Card was not created"
        assert self.mock_server.get_card(session.card_id) is not None, "Card not found in mock server"

    def assert_card_sealed(self, session: Any) -> None:
        """Assert that the card was sealed (streaming closed)."""
        card = self.get_card(session)
        assert card is not None, "Card not found"
        assert not card.streaming_mode, f"Card {session.card_id} was not sealed (streaming_mode still True)"

    def assert_no_errors(self) -> None:
        """Assert no error-level API calls were made."""
        errors = [c for c in self.mock_server.call_log if "error" in str(c).lower()]
        assert not errors, f"API errors occurred: {errors}"
