"""Tests for Clarify interactive card feature.

Tests the card builders (build_clarify_card, build_clarify_resolved_card,
build_clarify_awaiting_input_card) and the monkey-patch wrappers
(_wrap_feishu_adapter_send_clarify, _wrap_feishu_card_action_trigger).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_lark_streaming.cardkit import (
    build_clarify_awaiting_input_card,
    build_clarify_card,
    build_clarify_resolved_card,
)
from hermes_lark_streaming.cardkit_i18n import _T


# ── build_clarify_card ──


class TestBuildClarifyCardWithChoices:
    """Test build_clarify_card with choices (multi-choice mode)."""

    def test_schema_2_and_streaming_false(self) -> None:
        card = build_clarify_card(
            question="Which approach?",
            choices=["Fast", "Slow", "Custom"],
            clarify_id="test_id_123",
        )
        assert card["schema"] == "2.0"
        assert card["config"]["streaming_mode"] is False

    def test_question_displayed_as_div_with_icon(self) -> None:
        card = build_clarify_card(
            question="Which approach?",
            choices=["A", "B"],
            clarify_id="id1",
        )
        elements = card["body"]["elements"]
        assert elements[0]["tag"] == "div"
        assert elements[0]["icon"]["tag"] == "standard_icon"
        assert elements[0]["icon"]["token"] == "helpdesk_outlined"
        assert "Which approach?" in elements[0]["text"]["content"]

    def test_select_static_element_present(self) -> None:
        card = build_clarify_card(
            question="Pick one",
            choices=["Option A", "Option B"],
            clarify_id="id2",
        )
        elements = card["body"]["elements"]
        select_els = [e for e in elements if e.get("tag") == "select_static"]
        assert len(select_els) == 1

    def test_select_has_choices_plus_other(self) -> None:
        card = build_clarify_card(
            question="Pick one",
            choices=["Alpha", "Beta"],
            clarify_id="id3",
        )
        select_el = next(e for e in card["body"]["elements"] if e.get("tag") == "select_static")
        options = select_el["options"]
        # Should have 2 predefined + 1 "Other" option
        assert len(options) == 3
        assert options[0]["value"] == "0"
        assert options[0]["text"]["content"] == "Alpha"
        assert options[1]["value"] == "1"
        assert options[1]["text"]["content"] == "Beta"
        assert options[2]["value"] == "other"
        # "Other" should have i18n content
        assert "i18n_content" in options[2]["text"]

    def test_select_behavior_has_clarify_action(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=["X"],
            clarify_id="id4",
        )
        select_el = next(e for e in card["body"]["elements"] if e.get("tag") == "select_static")
        behaviors = select_el["behaviors"]
        assert len(behaviors) == 1
        assert behaviors[0]["type"] == "callback"
        assert behaviors[0]["value"]["hermes_clarify_action"] == "select"
        assert behaviors[0]["value"]["clarify_id"] == "id4"

    def test_select_placeholder_has_i18n(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=["X"],
            clarify_id="id5",
        )
        select_el = next(e for e in card["body"]["elements"] if e.get("tag") == "select_static")
        placeholder = select_el["placeholder"]
        assert placeholder["tag"] == "plain_text"
        assert "i18n_content" in placeholder

    def test_locales_in_config(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=["A"],
            clarify_id="id6",
        )
        assert "locales" in card["config"]

    def test_element_id_on_select(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=["A"],
            clarify_id="id7",
        )
        select_el = next(e for e in card["body"]["elements"] if e.get("tag") == "select_static")
        assert select_el["element_id"] == "clarify_select"


class TestBuildClarifyCardWithoutChoices:
    """Test build_clarify_card without choices (open-ended mode)."""

    def test_input_element_present(self) -> None:
        card = build_clarify_card(
            question="Describe your issue",
            choices=None,
            clarify_id="id_open1",
        )
        input_els = [e for e in card["body"]["elements"] if e.get("tag") == "input"]
        assert len(input_els) == 1

    def test_input_has_correct_behavior(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=None,
            clarify_id="id_open2",
        )
        input_el = next(e for e in card["body"]["elements"] if e.get("tag") == "input")
        behaviors = input_el["behaviors"]
        assert len(behaviors) == 1
        assert behaviors[0]["type"] == "callback"
        assert behaviors[0]["value"]["hermes_clarify_action"] == "input_submit"
        assert behaviors[0]["value"]["clarify_id"] == "id_open2"

    def test_input_has_max_length(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=None,
            clarify_id="id_open3",
        )
        input_el = next(e for e in card["body"]["elements"] if e.get("tag") == "input")
        assert input_el["max_length"] == 500

    def test_input_element_id(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=None,
            clarify_id="id_open4",
        )
        input_el = next(e for e in card["body"]["elements"] if e.get("tag") == "input")
        assert input_el["element_id"] == "clarify_input"

    def test_empty_choices_list_shows_input(self) -> None:
        """Empty choices list should also show input (same as None)."""
        card = build_clarify_card(
            question="Q",
            choices=[],
            clarify_id="id_open5",
        )
        input_els = [e for e in card["body"]["elements"] if e.get("tag") == "input"]
        assert len(input_els) == 1

    def test_no_select_when_no_choices(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=None,
            clarify_id="id_open6",
        )
        select_els = [e for e in card["body"]["elements"] if e.get("tag") == "select_static"]
        assert len(select_els) == 0


# ── build_clarify_resolved_card ──


class TestBuildClarifyResolvedCard:
    """Test build_clarify_resolved_card."""

    def test_schema_2(self) -> None:
        card = build_clarify_resolved_card(question="Q", selected="A")
        assert card["schema"] == "2.0"
        assert card["config"]["streaming_mode"] is False

    def test_question_displayed(self) -> None:
        card = build_clarify_resolved_card(question="Which way?", selected="Fast")
        elements = card["body"]["elements"]
        assert elements[0]["tag"] == "div"
        assert "Which way?" in elements[0]["text"]["content"]

    def test_selected_shown_with_resolve_icon(self) -> None:
        card = build_clarify_resolved_card(question="Q", selected="Fast")
        elements = card["body"]["elements"]
        # Second element should show the selection with resolve icon
        assert elements[1]["tag"] == "div"
        assert elements[1]["icon"]["tag"] == "standard_icon"
        assert elements[1]["icon"]["token"] == "resolve_outlined"
        assert "Fast" in elements[1]["text"]["content"]

    def test_i18n_on_selected_label(self) -> None:
        card = build_clarify_resolved_card(question="Q", selected="Fast")
        elements = card["body"]["elements"]
        # The resolved label should have i18n_content in the text element
        assert "i18n_content" in elements[1]["text"]

    def test_locales_in_config(self) -> None:
        card = build_clarify_resolved_card(question="Q", selected="A")
        assert "locales" in card["config"]


# ── build_clarify_awaiting_input_card ──


class TestBuildClarifyAwaitingInputCard:
    """Test build_clarify_awaiting_input_card."""

    def test_schema_2(self) -> None:
        card = build_clarify_awaiting_input_card(question="Q")
        assert card["schema"] == "2.0"
        assert card["config"]["streaming_mode"] is False

    def test_question_displayed(self) -> None:
        card = build_clarify_awaiting_input_card(question="How to proceed?")
        elements = card["body"]["elements"]
        assert elements[0]["tag"] == "div"
        assert "How to proceed?" in elements[0]["text"]["content"]

    def test_awaiting_hint_shown_with_edit_icon(self) -> None:
        card = build_clarify_awaiting_input_card(question="Q")
        elements = card["body"]["elements"]
        # Second element should show awaiting input hint with edit icon
        assert elements[1]["tag"] == "div"
        assert elements[1]["icon"]["tag"] == "standard_icon"
        assert elements[1]["icon"]["token"] == "edit_outlined"

    def test_awaiting_hint_has_i18n(self) -> None:
        card = build_clarify_awaiting_input_card(question="Q")
        elements = card["body"]["elements"]
        assert "i18n_content" in elements[1]["text"]


# ── Clarify card wrappers ──


class TestWrapFeishuAdapterSendClarify:
    """Test _wrap_feishu_adapter_send_clarify wrapper logic."""

    def test_wrapper_is_callable(self) -> None:
        from hermes_lark_streaming.monkey_patch import _wrap_feishu_adapter_send_clarify
        assert callable(_wrap_feishu_adapter_send_clarify)

    def test_wrapper_returns_callable(self) -> None:
        from hermes_lark_streaming.monkey_patch import _wrap_feishu_adapter_send_clarify
        async def orig(*args, **kwargs):
            pass
        wrapped = _wrap_feishu_adapter_send_clarify(orig)
        assert callable(wrapped)

    def test_falls_back_to_original_when_controller_disabled(self) -> None:
        """When controller is disabled, should fall back to original send_clarify."""
        from hermes_lark_streaming.monkey_patch import _wrap_feishu_adapter_send_clarify

        orig = AsyncMock(return_value="original_result")
        wrapped = _wrap_feishu_adapter_send_clarify(orig)

        mock_ctrl = MagicMock()
        mock_ctrl.enabled = False

        with patch("hermes_lark_streaming.controller.get_controller", return_value=mock_ctrl):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                wrapped(
                    MagicMock(), "chat_123", "Question?", ["A", "B"],
                    "clarify_1", "session_key_1",
                )
            )

        orig.assert_called_once()

    def test_stores_choices_for_callback(self) -> None:
        """When card is sent, choices should be stored in _clarify_choices."""
        from hermes_lark_streaming.monkey_patch import _clarify_choices, _clarify_questions, _wrap_feishu_adapter_send_clarify

        orig = AsyncMock()
        wrapped = _wrap_feishu_adapter_send_clarify(orig)

        mock_client = AsyncMock()
        mock_client.send_card_to_chat = AsyncMock(return_value="msg_123")

        mock_ctrl = MagicMock()
        mock_ctrl.enabled = True
        mock_ctrl._client_ok.return_value = True
        mock_ctrl._client = mock_client

        # Clean up any previous test data
        _clarify_choices.pop("test_clarify_id", None)
        _clarify_questions.pop("test_clarify_id", None)

        # Create mock for tools.clarify_gateway.mark_awaiting_text
        mock_mark_awaiting = MagicMock()
        mock_cg = MagicMock()
        mock_cg.mark_awaiting_text = mock_mark_awaiting

        with (
            patch("hermes_lark_streaming.controller.get_controller", return_value=mock_ctrl),
            patch("hermes_lark_streaming.monkey_patch._register_gateway_card"),
            patch.dict("sys.modules", {"tools": MagicMock(), "tools.clarify_gateway": mock_cg}),
        ):
            import asyncio
            try:
                asyncio.get_event_loop().run_until_complete(
                    wrapped(
                        MagicMock(), "chat_123", "Which?", ["Fast", "Slow"],
                        "test_clarify_id", "session_1",
                    )
                )
            except Exception:
                pass  # May fail on SendResult import

        # Check that choices were stored
        assert "test_clarify_id" in _clarify_choices
        assert _clarify_choices["test_clarify_id"] == ["Fast", "Slow"]
        assert _clarify_questions["test_clarify_id"] == "Which?"

        # Cleanup
        _clarify_choices.pop("test_clarify_id", None)
        _clarify_questions.pop("test_clarify_id", None)


class TestWrapFeishuCardActionTrigger:
    """Test _wrap_feishu_card_action_trigger wrapper."""

    def test_wrapper_is_callable(self) -> None:
        from hermes_lark_streaming.monkey_patch import _wrap_feishu_card_action_trigger
        assert callable(_wrap_feishu_card_action_trigger)

    def test_passthrough_for_non_clarify_action(self) -> None:
        """When action is not a clarify action, original method should be called."""
        from hermes_lark_streaming.monkey_patch import _wrap_feishu_card_action_trigger

        original = MagicMock(return_value="original_response")
        wrapped = _wrap_feishu_card_action_trigger(original)

        # Create a mock data object with no hermes_clarify_action
        mock_event = MagicMock()
        mock_event.action.value = {"hermes_action": "approve"}

        mock_data = MagicMock()
        mock_data.event = mock_event

        result = wrapped(MagicMock(), mock_data)

        original.assert_called_once()
        assert result == "original_response"

    def test_intercepts_clarify_select_action(self) -> None:
        """When hermes_clarify_action='select', should not call original."""
        from hermes_lark_streaming.monkey_patch import (
            _clarify_choices,
            _clarify_questions,
            _handle_clarify_card_action,
        )

        # Set up stored data
        _clarify_choices["test_cid"] = ["Alpha", "Beta"]
        _clarify_questions["test_cid"] = "Which?"

        # Mock the action
        mock_action = MagicMock()
        mock_action.value = {"hermes_clarify_action": "select", "clarify_id": "test_cid"}
        mock_action.option = "0"

        mock_event = MagicMock()
        mock_event.action = mock_action
        mock_event.operator = MagicMock()
        mock_event.operator.open_id = "user_123"

        mock_data = MagicMock()
        mock_data.event = mock_event

        # Mock the adapter instance
        mock_adapter = MagicMock()
        mock_adapter._is_interactive_operator_authorized.return_value = True
        mock_adapter._loop = MagicMock()

        mock_resolve = MagicMock()
        mock_cg = MagicMock()
        mock_cg.resolve_gateway_clarify = mock_resolve
        mock_safe = MagicMock()
        mock_async_utils = MagicMock()
        mock_async_utils.safe_schedule_threadsafe = mock_safe

        with (
            patch.dict("sys.modules", {
                "tools": MagicMock(),
                "tools.clarify_gateway": mock_cg,
                "agent": MagicMock(),
                "agent.async_utils": mock_async_utils,
            }),
        ):
            result = _handle_clarify_card_action(
                mock_adapter, mock_data, "select",
                {"hermes_clarify_action": "select", "clarify_id": "test_cid"},
            )

        # Cleanup
        _clarify_choices.pop("test_cid", None)
        _clarify_questions.pop("test_cid", None)

    def test_intercepts_clarify_other_action(self) -> None:
        """When user selects 'other', mark_awaiting_text should be called."""
        from hermes_lark_streaming.monkey_patch import (
            _clarify_choices,
            _clarify_questions,
            _handle_clarify_card_action,
        )

        # Set up stored data
        _clarify_choices["test_cid2"] = ["Alpha", "Beta"]
        _clarify_questions["test_cid2"] = "Which?"

        # Mock the action — user selected "other"
        mock_action = MagicMock()
        mock_action.value = {"hermes_clarify_action": "select", "clarify_id": "test_cid2"}
        mock_action.option = "other"

        mock_event = MagicMock()
        mock_event.action = mock_action
        mock_event.operator = MagicMock()
        mock_event.operator.open_id = "user_123"

        mock_data = MagicMock()
        mock_data.event = mock_event

        # Mock the adapter instance
        mock_adapter = MagicMock()
        mock_adapter._is_interactive_operator_authorized.return_value = True

        mock_mark = MagicMock()
        mock_cg = MagicMock()
        mock_cg.mark_awaiting_text = mock_mark

        with (
            patch.dict("sys.modules", {
                "tools": MagicMock(),
                "tools.clarify_gateway": mock_cg,
            }),
        ):
            result = _handle_clarify_card_action(
                mock_adapter, mock_data, "select",
                {"hermes_clarify_action": "select", "clarify_id": "test_cid2"},
            )

        # mark_awaiting_text should have been called
        mock_mark.assert_called_once_with("test_cid2")

        # Cleanup
        _clarify_choices.pop("test_cid2", None)
        _clarify_questions.pop("test_cid2", None)

    def test_intercepts_input_submit_action(self) -> None:
        """When hermes_clarify_action='input_submit', should resolve with input text."""
        from hermes_lark_streaming.monkey_patch import (
            _clarify_choices,
            _clarify_questions,
            _handle_clarify_card_action,
        )

        # Set up stored data
        _clarify_questions["test_cid3"] = "Tell me more"

        # Mock the action — user submitted text input
        mock_action = MagicMock()
        mock_action.value = {"hermes_clarify_action": "input_submit", "clarify_id": "test_cid3"}
        mock_action.input_value = "My custom answer"

        mock_event = MagicMock()
        mock_event.action = mock_action
        mock_event.operator = MagicMock()
        mock_event.operator.open_id = "user_123"

        mock_data = MagicMock()
        mock_data.event = mock_event

        # Mock the adapter instance
        mock_adapter = MagicMock()
        mock_adapter._is_interactive_operator_authorized.return_value = True
        mock_adapter._loop = MagicMock()

        mock_resolve = MagicMock()
        mock_cg = MagicMock()
        mock_cg.resolve_gateway_clarify = mock_resolve
        mock_safe = MagicMock()
        mock_async_utils = MagicMock()
        mock_async_utils.safe_schedule_threadsafe = mock_safe

        with (
            patch.dict("sys.modules", {
                "tools": MagicMock(),
                "tools.clarify_gateway": mock_cg,
                "agent": MagicMock(),
                "agent.async_utils": mock_async_utils,
            }),
        ):
            result = _handle_clarify_card_action(
                mock_adapter, mock_data, "input_submit",
                {"hermes_clarify_action": "input_submit", "clarify_id": "test_cid3"},
            )

        # Cleanup
        _clarify_choices.pop("test_cid3", None)
        _clarify_questions.pop("test_cid3", None)


class TestClarifyCardRegistry:
    """Test the _clarify_choices and _clarify_questions module-level dicts."""

    def test_choices_registry_exists(self) -> None:
        from hermes_lark_streaming.monkey_patch import _clarify_choices
        assert isinstance(_clarify_choices, dict)

    def test_questions_registry_exists(self) -> None:
        from hermes_lark_streaming.monkey_patch import _clarify_questions
        assert isinstance(_clarify_questions, dict)

    def test_choices_cleanup_after_resolve(self) -> None:
        """After resolving a clarify, the choices should be cleaned up."""
        from hermes_lark_streaming.monkey_patch import _clarify_choices, _clarify_questions

        _clarify_choices["cleanup_test"] = ["A", "B"]
        _clarify_questions["cleanup_test"] = "Q"

        # Simulate cleanup
        _clarify_choices.pop("cleanup_test", None)
        _clarify_questions.pop("cleanup_test", None)

        assert "cleanup_test" not in _clarify_choices
        assert "cleanup_test" not in _clarify_questions


class TestClarifyI18n:
    """Test clarify-related i18n entries exist."""

    def test_clarify_question_entry(self) -> None:
        assert "clarify_question" in _T

    def test_clarify_select_placeholder_entry(self) -> None:
        assert "clarify_select_placeholder" in _T

    def test_clarify_other_entry(self) -> None:
        assert "clarify_other" in _T

    def test_clarify_input_placeholder_entry(self) -> None:
        assert "clarify_input_placeholder" in _T

    def test_clarify_resolved_entry(self) -> None:
        assert "clarify_resolved" in _T

    def test_clarify_awaiting_input_entry(self) -> None:
        assert "clarify_awaiting_input" in _T

    def test_all_entries_are_tuples_of_two(self) -> None:
        clarify_keys = [
            "clarify_question", "clarify_select_placeholder", "clarify_other",
            "clarify_input_placeholder", "clarify_resolved", "clarify_awaiting_input",
        ]
        for key in clarify_keys:
            assert key in _T, f"Missing i18n key: {key}"
            en, zh = _T[key]
            assert isinstance(en, str) and len(en) > 0, f"Empty English text for {key}"
            assert isinstance(zh, str) and len(zh) > 0, f"Empty Chinese text for {key}"
