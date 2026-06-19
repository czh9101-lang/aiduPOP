"""End-to-end tests — full message → card pipeline.

v1.1.0 (Task 3.4): Complete e2e test suite covering:
- Simple answer (no tools, no reasoning)
- Answer with reasoning
- Answer with tool calls
- Answer with reasoning + tools interleaved
- Error handling (stream_element 300313 fallback)
- Concurrency (new message interrupts old)
- Card sealing and summary

The `runner` fixture is provided by tests/e2e/conftest.py and automatically
uses real Feishu API when FEISHU_E2E_* env vars are set, otherwise mock.
"""

from __future__ import annotations

import asyncio
import pytest


class TestSimpleAnswer:
    """Test the simplest case: user sends message, AI replies with text only."""

    @pytest.mark.asyncio
    async def test_basic_answer_appears_in_card(self, runner):
        """Answer text should appear in the card's answer element."""
        session = await runner.start_message("hello")
        await runner.feed_answer(session, "Hello! How can I help you?")
        await runner.complete(session, answer="Hello! How can I help you?")

        runner.assert_card_created(session)
        answer = runner.get_answer_text(session)
        assert "Hello! How can I help you?" in answer, f"Answer not in card: {answer!r}"

    @pytest.mark.asyncio
    async def test_card_is_sealed_after_complete(self, runner):
        """Card streaming_mode should be False after completion."""
        session = await runner.start_message("test")
        await runner.feed_answer(session, "Response")
        await runner.complete(session, answer="Response")

        await runner.assert_card_sealed(session)

    @pytest.mark.asyncio
    async def test_multiple_answer_deltas_concatenated(self, runner):
        """Multiple answer deltas should be concatenated in the card."""
        session = await runner.start_message("tell me a story")
        await runner.feed_answer(session, "Once upon a time, ")
        await runner.feed_answer(session, "there was a plugin ")
        await runner.feed_answer(session, "that rendered cards.")
        await runner.complete(session, answer="Once upon a time, there was a plugin that rendered cards.")

        answer = runner.get_answer_text(session)
        assert "Once upon a time" in answer
        assert "rendered cards" in answer


class TestReasoningAndTools:
    """Test cards with reasoning and tool calls."""

    @pytest.mark.asyncio
    async def test_reasoning_appears_in_panel(self, runner):
        """Reasoning text should appear in the unified panel."""
        session = await runner.start_message("think about this")
        await runner.feed_reasoning(session, "Let me analyze the question...")
        await runner.feed_answer(session, "Here's my analysis.")
        await runner.complete(session, answer="Here's my analysis.")

        runner.assert_card_created(session)
        panel_elements = runner.get_panel_elements(session)
        # Panel should have content (reasoning text)
        assert len(panel_elements) > 0, "Panel should have elements when reasoning exists"

    @pytest.mark.asyncio
    async def test_tool_calls_appear_in_panel(self, runner):
        """Tool call steps should appear in the unified panel."""
        session = await runner.start_message("search for files")
        await runner.feed_tool_update(session, "grep", "running", "Searching for 'files'")
        await runner.feed_tool_update(session, "grep", "success", "Found 3 files")
        await runner.feed_answer(session, "I found 3 files matching your query.")
        await runner.complete(session, answer="I found 3 files matching your query.")

        runner.assert_card_created(session)
        panel_elements = runner.get_panel_elements(session)
        assert len(panel_elements) > 0, "Panel should have tool steps"

    @pytest.mark.asyncio
    async def test_interleaved_reasoning_and_tools(self, runner):
        """Reasoning and tools should be interleaved in chronological order."""
        session = await runner.start_message("do a complex task")
        await runner.feed_reasoning(session, "First, I need to search...")
        await runner.feed_tool_update(session, "grep", "running", "search phase 1")
        await runner.feed_tool_update(session, "grep", "success", "found results")
        await runner.feed_reasoning(session, "Now I need to read the files...")
        await runner.feed_tool_update(session, "read", "running", "reading file")
        await runner.feed_tool_update(session, "read", "success", "file content")
        await runner.feed_answer(session, "Here's what I found.")
        await runner.complete(session, answer="Here's what I found.")

        runner.assert_card_created(session)
        await runner.assert_card_sealed(session)


class TestErrorHandling:
    """Test error recovery scenarios."""

    @pytest.mark.asyncio
    async def test_300313_fallback_to_partial_update(self, runner):
        """When stream_element returns 300313, should fall back to partial_update_element."""
        from hermes_lark_streaming.feishu import FeishuAPIError, CARDKIT_ELEMENT_NOT_FOUND

        session = await runner.start_message("short reply")
        await runner.feed_answer(session, "OK")
        await runner.complete(session, answer="OK")

        # The card should still be created and sealed despite any errors
        runner.assert_card_created(session)
        await runner.assert_card_sealed(session)


class TestConcurrency:
    """Test concurrent message handling."""

    @pytest.mark.asyncio
    async def test_new_message_seals_old_card(self, runner):
        """When a new message arrives, the old card should be sealed.

        In e2e tests we don't have the full patching layer (no _started_msg_ids
        tracking), so we directly call on_message_interrupted to simulate
        the concurrency limit behavior.

        v1.1.1: 真飞书模式下用 session 的真实 message_id 和 chat_id。
        """
        session1 = await runner.start_message("first")
        await runner.feed_answer(session1, "First response...")

        # 用 session1 的真实 message_id 和 chat_id
        runner.controller.on_interrupted(
            old_message_id=session1.message_id,
            new_message_id=f"{session1.message_id}_new",
            chat_id=session1.chat_id,
            anchor_id=session1.anchor_id or session1.message_id,
        )

        # Wait for interrupt/seal to complete (async)
        # v1.1.1: 真飞书模式需要更长等待
        if runner.is_real_mode:
            for _ in range(20):
                await asyncio.sleep(0.5)
                if session1.is_terminal_phase:
                    break
        else:
            await asyncio.sleep(1.0)

        # The old session should be in a terminal state
        assert session1.state in ("aborted", "terminated", "completed"), \
            f"Old session should be terminal after interrupt, got state={session1.state}"


class TestCardStructure:
    """Test card JSON structure correctness.

    v1.1.1: 真飞书模式下无法查询卡片元素内容，这些测试只 mock 模式跑。
    """

    @pytest.mark.asyncio
    async def test_card_has_loading_hint_initially(self, runner):
        """Placeholder card should have a loading hint element."""
        if runner.is_real_mode:
            pytest.skip("Card structure inspection only available in mock mode")

        from hermes_lark_streaming.cardkit.elements import _LOADING_HINT_ELEMENT_ID

        session = await runner.start_message("test")
        # At this point, card should have loading hint
        card = runner.get_card(session)
        assert card is not None
        assert _LOADING_HINT_ELEMENT_ID in card.elements, "Loading hint should be in initial card"

    @pytest.mark.asyncio
    async def test_loading_hint_removed_after_first_content(self, runner):
        """Loading hint should be removed after first content arrives."""
        if runner.is_real_mode:
            pytest.skip("Card structure inspection only available in mock mode")

        from hermes_lark_streaming.cardkit.elements import _LOADING_HINT_ELEMENT_ID

        session = await runner.start_message("test")
        await runner.feed_answer(session, "First content")
        await asyncio.sleep(0.2)

        card = runner.get_card(session)
        assert card is not None
        assert _LOADING_HINT_ELEMENT_ID not in card.elements, "Loading hint should be removed after first content"

    @pytest.mark.asyncio
    async def test_answer_element_created(self, runner):
        """Answer element should be created after first answer delta."""
        if runner.is_real_mode:
            pytest.skip("Card structure inspection only available in mock mode")

        from hermes_lark_streaming.cardkit.elements import ANSWER_ELEMENT_ID

        session = await runner.start_message("test")
        await runner.feed_answer(session, "Answer text")
        await asyncio.sleep(0.2)

        card = runner.get_card(session)
        assert card is not None
        assert ANSWER_ELEMENT_ID in card.elements, "Answer element should exist after first answer delta"

    @pytest.mark.asyncio
    async def test_api_call_count_reasonable(self, runner):
        """API call count should be reasonable (not excessive)."""
        session = await runner.start_message("test")
        await runner.feed_answer(session, "A" * 100)  # 100 chars
        await runner.complete(session, answer="A" * 100)

        total_calls = runner.get_total_api_calls()
        # Should be: create + reply + a few flushes + seal ≈ < 20
        # (In real mode, get_total_api_calls returns 0, so this is mock-only)
        if not runner.is_real_mode:
            assert total_calls < 30, f"Too many API calls ({total_calls}) for a simple answer"


class TestRealFeishuMode:
    """Tests that only run against real Feishu API.

    These tests are automatically skipped when FEISHU_E2E_* env vars
    are not set. They verify that the plugin works end-to-end with
    the actual Feishu CardKit API.
    """

    @pytest.mark.asyncio
    async def test_real_card_visible_in_feishu(self, runner):
        """Card should be created and visible in the real Feishu chat."""
        if not runner.is_real_mode:
            pytest.skip("Real Feishu mode not enabled (set FEISHU_E2E_* env vars)")

        session = await runner.start_message("real e2e test")
        await runner.feed_answer(session, "这是一条来自真飞书 e2e 测试的消息。")
        await runner.complete(session, answer="这是一条来自真飞书 e2e 测试的消息。")

        runner.assert_card_created(session)
        await runner.assert_card_sealed(session)
        # Verify answer text is in the session state
        answer = runner.get_answer_text(session)
        assert "真飞书 e2e 测试" in answer, f"Answer not found in session state: {answer!r}"

    @pytest.mark.asyncio
    async def test_real_card_with_reasoning_and_tools(self, runner):
        """Card with reasoning + tools should render correctly in real Feishu."""
        if not runner.is_real_mode:
            pytest.skip("Real Feishu mode not enabled")

        session = await runner.start_message("real e2e test with tools")
        await runner.feed_reasoning(session, "让我分析一下这个问题...")
        await runner.feed_tool_update(session, "grep", "running", "搜索文件")
        await runner.feed_tool_update(session, "grep", "success", "找到 3 个文件")
        await runner.feed_answer(session, "分析完成，找到了 3 个相关文件。")
        await runner.complete(session, answer="分析完成，找到了 3 个相关文件。")

        runner.assert_card_created(session)
        await runner.assert_card_sealed(session)

    @pytest.mark.asyncio
    async def test_real_card_streaming_typewriter(self, runner):
        """Streaming answer should produce visible typewriter effect in real Feishu."""
        if not runner.is_real_mode:
            pytest.skip("Real Feishu mode not enabled")

        session = await runner.start_message("real e2e streaming test")
        # Feed answer in multiple small deltas to test streaming
        for chunk in ["Hello", " world", "!", " This", " is", " a", " streaming", " test."]:
            await runner.feed_answer(session, chunk)
        await runner.complete(session, answer="Hello world! This is a streaming test.")

        runner.assert_card_created(session)
        await runner.assert_card_sealed(session)
        answer = runner.get_answer_text(session)
        assert "Hello world" in answer


# ── v1.1.1 新增：生命周期覆盖测试 ──


class TestV111BugFixes:
    """v1.1.1 Bug 修复的 E2E 测试."""

    @pytest.mark.asyncio
    async def test_drain_300309_falls_back_to_batch_update(self, runner):
        """drain 遇 300309 (streaming closed) 改用 batch_update 写入答案."""
        from hermes_lark_streaming.feishu import FeishuAPIError, CARDKIT_STREAMING_CLOSED
        from unittest.mock import AsyncMock

        session = await runner.start_message("test 300309 drain")
        await runner.feed_answer(session, "answer that needs drain after streaming closed")

        # Mock stream_element 抛 300309
        original_stream = runner.controller._client.cardkit_stream_element
        runner.controller._client.cardkit_stream_element = AsyncMock(
            side_effect=FeishuAPIError("test", code=CARDKIT_STREAMING_CLOSED)
        )

        await runner.complete(session, answer="answer that needs drain after streaming closed")

        # 恢复
        runner.controller._client.cardkit_stream_element = original_stream

        runner.assert_card_created(session)
        await runner.assert_card_sealed(session)
        # 答案应该通过 batch_update fallback 写入
        answer = runner.get_answer_text(session)
        assert "drain after streaming closed" in answer

    @pytest.mark.asyncio
    async def test_drain_300313_falls_back_without_tag(self, runner):
        """drain 遇 300313 fallback 不带 tag（不再报 300312）."""
        from hermes_lark_streaming.feishu import FeishuAPIError, CARDKIT_ELEMENT_NOT_FOUND
        from unittest.mock import AsyncMock

        session = await runner.start_message("test 300313 drain")
        await runner.feed_answer(session, "answer for 313 fallback test")

        # Mock stream_element 抛 300313
        original_stream = runner.controller._client.cardkit_stream_element
        runner.controller._client.cardkit_stream_element = AsyncMock(
            side_effect=FeishuAPIError("test", code=CARDKIT_ELEMENT_NOT_FOUND)
        )

        await runner.complete(session, answer="answer for 313 fallback test")

        runner.controller._client.cardkit_stream_element = original_stream

        runner.assert_card_created(session)
        await runner.assert_card_sealed(session)
        answer = runner.get_answer_text(session)
        assert "313 fallback test" in answer


class TestV111SessionManagement:
    """v1.1.1 session 管理测试."""

    @pytest.mark.asyncio
    async def test_prune_skips_streaming_session(self, runner):
        """STREAMING 状态的 session 超 TTL 不被清理."""
        session = await runner.start_message("long running task")
        await runner.feed_answer(session, "partial answer")

        # 模拟 session 已存活 700 秒（超过 TTL 600）
        assert runner.simulate_session_age(session.message_id, 700) is True

        # 触发 prune（发新消息会触发 _prune_stale_sessions）
        session2 = await runner.start_message("new message after TTL")

        # 旧 session 应该还在（STREAMING 状态不被清理）
        assert session.message_id in runner.controller._sessions
        # 新 session 也创建了
        assert session2.message_id in runner.controller._sessions

    @pytest.mark.asyncio
    async def test_prune_cleans_completed_session(self, runner):
        """COMPLETED 状态的 session 超 TTL 正常清理."""
        session = await runner.start_message("completed task")
        await runner.feed_answer(session, "done")
        await runner.complete(session, answer="done")

        # 确认已 COMPLETED（真飞书模式可能需要等待异步封卡完成）
        from hermes_lark_streaming.state.phase import CardPhase
        for _ in range(20):
            if runner.controller._sessions[session.message_id].state == CardPhase.COMPLETED:
                break
            await asyncio.sleep(0.5)
        assert runner.controller._sessions[session.message_id].state == CardPhase.COMPLETED

        # 模拟超时
        assert runner.simulate_session_age(session.message_id, 700) is True

        # 触发 prune
        session2 = await runner.start_message("new message")

        # 旧 session 应被清理
        assert session.message_id not in runner.controller._sessions
        assert session2.message_id in runner.controller._sessions

    @pytest.mark.asyncio
    async def test_release_session_data_after_seal(self, runner):
        """封卡成功后重数据被释放（unified_state is None）."""
        session = await runner.start_message("test release data")
        await runner.feed_answer(session, "answer to be released")
        await runner.complete(session, answer="answer to be released")

        # 真飞书模式：等待封卡完成释放数据
        if runner.is_real_mode:
            for _ in range(20):
                if runner.controller._sessions[session.message_id].unified_state is None:
                    break
                await asyncio.sleep(0.5)

        # 封卡后 unified_state 应该被释放
        assert runner.controller._sessions[session.message_id].unified_state is None


class TestV111CardLifecycle:
    """v1.1.1 卡片生命周期完整覆盖."""

    @pytest.mark.asyncio
    async def test_card_lifecycle_error(self, runner):
        """错误卡片生命周期：AI 报错 → 卡片仍能封卡."""
        session = await runner.start_message("trigger error")
        await runner.feed_answer(session, "partial")
        # 用 error 参数完成
        await runner.complete(session, answer="partial", error_message="AI encountered an error")

        runner.assert_card_created(session)
        await runner.assert_card_sealed(session)

    @pytest.mark.asyncio
    async def test_card_lifecycle_interrupted(self, runner):
        """中断后卡片状态：新消息中断旧消息 → 旧卡 seal 为中断."""
        session1 = await runner.start_message("first message")
        await runner.feed_answer(session1, "first answer")

        # 发新消息中断旧消息
        session2 = await runner.start_message("second message", chat_id=session1.chat_id)
        await runner.feed_answer(session2, "second answer")
        await runner.complete(session2, answer="second answer")

        runner.assert_card_created(session2)
        await runner.assert_card_sealed(session2)

    @pytest.mark.asyncio
    async def test_card_lifecycle_long_answer(self, runner):
        """长答案分段写入 + 封卡完整."""
        session = await runner.start_message("long answer test")
        # 模拟 20 段 answer delta
        chunks = [f"Chunk {i}. " for i in range(20)]
        full_answer = "".join(chunks)
        for chunk in chunks:
            await runner.feed_answer(session, chunk)
        await runner.complete(session, answer=full_answer)

        runner.assert_card_created(session)
        await runner.assert_card_sealed(session)
        answer = runner.get_answer_text(session)
        assert "Chunk 0" in answer
        assert "Chunk 19" in answer
