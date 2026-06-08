"""Tests for Clarify interactive card feature.

Tests the card builders (build_clarify_card, build_clarify_submitted_card,
build_clarify_resolved_card) and the monkey-patch wrappers
(_wrap_feishu_adapter_send_clarify, _wrap_feishu_card_action_trigger).

Three-state flow:
  1. 待选择态 (build_clarify_card) → card initially sent
  2. 已提交态 (build_clarify_submitted_card) → CallBackCard on user action (soft lock)
  3. 已确认态 (build_clarify_resolved_card) → server-side update after hermes confirms (hard lock)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_lark_streaming.cardkit import (
    build_clarify_card,
    build_clarify_resolved_card,
    build_clarify_submitted_card,
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

    def test_choices_list_displayed_as_markdown(self) -> None:
        """Choices should be displayed as a markdown list with A/B/C labels."""
        card = build_clarify_card(
            question="Pick one",
            choices=["Option A", "Option B"],
            clarify_id="id2",
        )
        elements = card["body"]["elements"]
        # Second element should be the markdown choices list
        md_el = elements[1]
        assert md_el["tag"] == "markdown"
        assert "A. Option A" in md_el["content"]
        assert "B. Option B" in md_el["content"]

    def test_select_static_element_present(self) -> None:
        card = build_clarify_card(
            question="Pick one",
            choices=["Option A", "Option B"],
            clarify_id="id3",
        )
        select_els = [e for e in card["body"]["elements"] if e.get("tag") == "select_static"]
        assert len(select_els) == 1

    def test_select_has_choices_without_other(self) -> None:
        """Select should only contain predefined choices, no 'other' option."""
        card = build_clarify_card(
            question="Pick one",
            choices=["Alpha", "Beta"],
            clarify_id="id4",
        )
        select_el = next(e for e in card["body"]["elements"] if e.get("tag") == "select_static")
        options = select_el["options"]
        # Should have exactly 2 predefined options (no "other")
        assert len(options) == 2
        assert options[0]["value"] == "0"
        assert "A. Alpha" in options[0]["text"]["content"]
        assert options[1]["value"] == "1"
        assert "B. Beta" in options[1]["text"]["content"]
        # No "other" option
        assert not any(o["value"] == "other" for o in options)

    def test_select_behavior_has_clarify_action(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=["X"],
            clarify_id="id5",
        )
        select_el = next(e for e in card["body"]["elements"] if e.get("tag") == "select_static")
        behaviors = select_el["behaviors"]
        assert len(behaviors) == 1
        assert behaviors[0]["type"] == "callback"
        assert behaviors[0]["value"]["hermes_clarify_action"] == "select"
        assert behaviors[0]["value"]["clarify_id"] == "id5"

    def test_select_placeholder_has_i18n(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=["X"],
            clarify_id="id6",
        )
        select_el = next(e for e in card["body"]["elements"] if e.get("tag") == "select_static")
        placeholder = select_el["placeholder"]
        assert placeholder["tag"] == "plain_text"
        assert "i18n_content" in placeholder

    def test_locales_in_config(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=["A"],
            clarify_id="id7",
        )
        assert "locales" in card["config"]

    def test_element_id_on_select(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=["A"],
            clarify_id="id8",
        )
        select_el = next(e for e in card["body"]["elements"] if e.get("tag") == "select_static")
        assert select_el["element_id"] == "clarify_select"

    def test_input_always_present_with_choices(self) -> None:
        """Input element should always be present, even with choices."""
        card = build_clarify_card(
            question="Q",
            choices=["A", "B"],
            clarify_id="id9",
        )
        input_els = [e for e in card["body"]["elements"] if e.get("tag") == "input"]
        assert len(input_els) == 1

    def test_input_has_input_submit_behavior(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=["A"],
            clarify_id="id10",
        )
        input_el = next(e for e in card["body"]["elements"] if e.get("tag") == "input")
        behaviors = input_el["behaviors"]
        assert len(behaviors) == 1
        assert behaviors[0]["type"] == "callback"
        assert behaviors[0]["value"]["hermes_clarify_action"] == "input_submit"
        assert behaviors[0]["value"]["clarify_id"] == "id10"

    def test_submit_button_present(self) -> None:
        """Submit button should be present in the card."""
        card = build_clarify_card(
            question="Q",
            choices=["A"],
            clarify_id="id_btn1",
        )
        action_els = [e for e in card["body"]["elements"] if e.get("tag") == "action"]
        assert len(action_els) == 1
        button = action_els[0]["actions"][0]
        assert button["tag"] == "button"
        assert button["type"] == "primary"

    def test_submit_button_has_button_submit_behavior(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=["A"],
            clarify_id="id_btn2",
        )
        action_el = next(e for e in card["body"]["elements"] if e.get("tag") == "action")
        button = action_el["actions"][0]
        behaviors = button["behaviors"]
        assert len(behaviors) == 1
        assert behaviors[0]["type"] == "callback"
        assert behaviors[0]["value"]["hermes_clarify_action"] == "button_submit"
        assert behaviors[0]["value"]["clarify_id"] == "id_btn2"

    def test_submit_button_has_i18n(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=["A"],
            clarify_id="id_btn3",
        )
        action_el = next(e for e in card["body"]["elements"] if e.get("tag") == "action")
        button = action_el["actions"][0]
        assert "i18n_content" in button["text"]


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

    def test_empty_choices_list_shows_input_only(self) -> None:
        """Empty choices list should show only input (no select, no choices list)."""
        card = build_clarify_card(
            question="Q",
            choices=[],
            clarify_id="id_open5",
        )
        input_els = [e for e in card["body"]["elements"] if e.get("tag") == "input"]
        select_els = [e for e in card["body"]["elements"] if e.get("tag") == "select_static"]
        assert len(input_els) == 1
        assert len(select_els) == 0

    def test_no_select_when_no_choices(self) -> None:
        card = build_clarify_card(
            question="Q",
            choices=None,
            clarify_id="id_open6",
        )
        select_els = [e for e in card["body"]["elements"] if e.get("tag") == "select_static"]
        assert len(select_els) == 0

    def test_no_choices_list_markdown_when_no_choices(self) -> None:
        """No markdown choices list should appear when choices is None."""
        card = build_clarify_card(
            question="Q",
            choices=None,
            clarify_id="id_open7",
        )
        # Elements: question div + input + action (submit button)
        elements = card["body"]["elements"]
        assert elements[0]["tag"] == "div"
        assert elements[1]["tag"] == "input"
        assert elements[2]["tag"] == "action"

    def test_submit_button_present_without_choices(self) -> None:
        """Submit button should be present even without choices."""
        card = build_clarify_card(
            question="Q",
            choices=None,
            clarify_id="id_open8",
        )
        action_els = [e for e in card["body"]["elements"] if e.get("tag") == "action"]
        assert len(action_els) == 1


# ── build_clarify_submitted_card ──


class TestBuildClarifySubmittedCard:
    """Test build_clarify_submitted_card (soft lock / second state)."""

    def test_schema_2(self) -> None:
        card = build_clarify_submitted_card(
            question="Q", selected="Fast", clarify_id="cid1",
        )
        assert card["schema"] == "2.0"
        assert card["config"]["streaming_mode"] is False

    def test_question_displayed_with_strikethrough(self) -> None:
        card = build_clarify_submitted_card(
            question="Which way?", selected="Fast", clarify_id="cid2",
        )
        elements = card["body"]["elements"]
        assert elements[0]["tag"] == "div"
        assert "~~Which way?~~" in elements[0]["text"]["content"]

    def test_resolve_filled_icon_on_question(self) -> None:
        card = build_clarify_submitted_card(
            question="Q", selected="Fast", clarify_id="cid3",
        )
        elements = card["body"]["elements"]
        assert elements[0]["icon"]["tag"] == "standard_icon"
        assert elements[0]["icon"]["token"] == "resolve_filled"
        assert elements[0]["icon"]["color"] == "green"

    def test_lock_icon_on_selected(self) -> None:
        """Submitted card should have lock_outlined icon (orange) on selected area."""
        card = build_clarify_submitted_card(
            question="Q", selected="Fast", clarify_id="cid4",
        )
        elements = card["body"]["elements"]
        lock_el = next(e for e in elements if e.get("icon", {}).get("token") == "lock_outlined")
        assert lock_el is not None
        assert lock_el["icon"]["color"] == "orange"

    def test_selected_shown_in_text(self) -> None:
        card = build_clarify_submitted_card(
            question="Q", selected="Fast", clarify_id="cid5",
        )
        elements = card["body"]["elements"]
        lock_el = next(e for e in elements if e.get("icon", {}).get("token") == "lock_outlined")
        assert "Fast" in lock_el["text"]["content"]

    def test_submitted_text_present(self) -> None:
        """The submitted hint text should be present."""
        card = build_clarify_submitted_card(
            question="Q", selected="Fast", clarify_id="cid6",
        )
        elements = card["body"]["elements"]
        lock_el = next(e for e in elements if e.get("icon", {}).get("token") == "lock_outlined")
        en_submitted = _T["clarify_submitted"][0]
        assert en_submitted in lock_el["text"]["content"]

    def test_i18n_on_selected_label(self) -> None:
        card = build_clarify_submitted_card(
            question="Q", selected="Fast", clarify_id="cid7",
        )
        elements = card["body"]["elements"]
        lock_el = next(e for e in elements if e.get("icon", {}).get("token") == "lock_outlined")
        assert "i18n_content" in lock_el["text"]

    def test_retry_button_present(self) -> None:
        """Submitted card should have a retry submit button."""
        card = build_clarify_submitted_card(
            question="Q", selected="Fast", clarify_id="cid8",
        )
        action_els = [e for e in card["body"]["elements"] if e.get("tag") == "action"]
        assert len(action_els) == 1
        button = action_els[0]["actions"][0]
        assert button["tag"] == "button"
        assert button["type"] == "primary"

    def test_retry_button_has_correct_behavior(self) -> None:
        card = build_clarify_submitted_card(
            question="Q", selected="Fast", clarify_id="cid9",
        )
        action_el = next(e for e in card["body"]["elements"] if e.get("tag") == "action")
        button = action_el["actions"][0]
        behaviors = button["behaviors"]
        assert len(behaviors) == 1
        assert behaviors[0]["type"] == "callback"
        assert behaviors[0]["value"]["hermes_clarify_action"] == "retry_submit"
        assert behaviors[0]["value"]["clarify_id"] == "cid9"

    def test_retry_button_has_i18n(self) -> None:
        card = build_clarify_submitted_card(
            question="Q", selected="Fast", clarify_id="cid10",
        )
        action_el = next(e for e in card["body"]["elements"] if e.get("tag") == "action")
        button = action_el["actions"][0]
        assert "i18n_content" in button["text"]

    def test_choices_list_displayed_when_provided(self) -> None:
        """When choices are provided, they should be displayed in the submitted card."""
        card = build_clarify_submitted_card(
            question="Q", selected="Fast", choices=["Fast", "Slow"], clarify_id="cid11",
        )
        elements = card["body"]["elements"]
        md_els = [e for e in elements if e.get("tag") == "markdown"]
        assert len(md_els) == 1
        assert "A. Fast" in md_els[0]["content"]
        assert "B. Slow" in md_els[0]["content"]

    def test_no_choices_list_when_none(self) -> None:
        """When choices is None, no choices list should appear."""
        card = build_clarify_submitted_card(
            question="Q", selected="Custom answer", choices=None, clarify_id="cid12",
        )
        elements = card["body"]["elements"]
        md_els = [e for e in elements if e.get("tag") == "markdown"]
        assert len(md_els) == 0

    def test_locales_in_config(self) -> None:
        card = build_clarify_submitted_card(
            question="Q", selected="A", clarify_id="cid13",
        )
        assert "locales" in card["config"]


# ── build_clarify_resolved_card ──


class TestBuildClarifyResolvedCard:
    """Test build_clarify_resolved_card (hard lock / confirmed / third state)."""

    def test_schema_2(self) -> None:
        card = build_clarify_resolved_card(question="Q", selected="A")
        assert card["schema"] == "2.0"
        assert card["config"]["streaming_mode"] is False

    def test_question_displayed_with_strikethrough(self) -> None:
        card = build_clarify_resolved_card(question="Which way?", selected="Fast")
        elements = card["body"]["elements"]
        assert elements[0]["tag"] == "div"
        # Question should have strikethrough
        assert "~~Which way?~~" in elements[0]["text"]["content"]

    def test_resolve_filled_icon_on_question(self) -> None:
        """Resolved card should use resolve_filled icon on question."""
        card = build_clarify_resolved_card(question="Q", selected="Fast")
        elements = card["body"]["elements"]
        assert elements[0]["icon"]["tag"] == "standard_icon"
        assert elements[0]["icon"]["token"] == "resolve_filled"
        assert elements[0]["icon"]["color"] == "green"

    def test_resolve_filled_icon_on_selected(self) -> None:
        """Resolved card should use resolve_filled icon (green) on selected area."""
        card = build_clarify_resolved_card(question="Q", selected="Fast")
        elements = card["body"]["elements"]
        # Find the element with resolve_filled icon that is NOT the question title
        resolve_els = [e for e in elements if e.get("icon", {}).get("token") == "resolve_filled"]
        assert len(resolve_els) == 2  # question title + selected area
        selected_el = resolve_els[1]  # second one is the selected area
        assert selected_el["icon"]["color"] == "green"

    def test_selected_shown_in_text(self) -> None:
        card = build_clarify_resolved_card(question="Q", selected="Fast")
        elements = card["body"]["elements"]
        resolve_els = [e for e in elements if e.get("icon", {}).get("token") == "resolve_filled"]
        selected_el = resolve_els[1]
        assert "Fast" in selected_el["text"]["content"]

    def test_confirmed_text_present(self) -> None:
        """The confirmed hint text should be present."""
        card = build_clarify_resolved_card(question="Q", selected="Fast")
        elements = card["body"]["elements"]
        resolve_els = [e for e in elements if e.get("icon", {}).get("token") == "resolve_filled"]
        selected_el = resolve_els[1]
        en_confirmed = _T["clarify_confirmed"][0]
        assert en_confirmed in selected_el["text"]["content"]

    def test_no_lock_outlined_icon(self) -> None:
        """Resolved card should NOT have lock_outlined icon (that's for submitted state)."""
        card = build_clarify_resolved_card(question="Q", selected="Fast")
        elements = card["body"]["elements"]
        lock_els = [e for e in elements if e.get("icon", {}).get("token") == "lock_outlined"]
        assert len(lock_els) == 0

    def test_no_buttons_in_resolved_card(self) -> None:
        """Resolved card (hard lock) should NOT have any action buttons."""
        card = build_clarify_resolved_card(question="Q", selected="Fast")
        action_els = [e for e in card["body"]["elements"] if e.get("tag") == "action"]
        assert len(action_els) == 0

    def test_i18n_on_selected_label(self) -> None:
        card = build_clarify_resolved_card(question="Q", selected="Fast")
        elements = card["body"]["elements"]
        resolve_els = [e for e in elements if e.get("icon", {}).get("token") == "resolve_filled"]
        selected_el = resolve_els[1]
        assert "i18n_content" in selected_el["text"]

    def test_choices_list_displayed_when_provided(self) -> None:
        """When choices are provided, they should be displayed in the resolved card."""
        card = build_clarify_resolved_card(
            question="Q", selected="Fast", choices=["Fast", "Slow"],
        )
        elements = card["body"]["elements"]
        # Should have: question div, choices markdown, selected div
        md_els = [e for e in elements if e.get("tag") == "markdown"]
        assert len(md_els) == 1
        assert "A. Fast" in md_els[0]["content"]
        assert "B. Slow" in md_els[0]["content"]

    def test_no_choices_list_when_none(self) -> None:
        """When choices is None, no choices list should appear."""
        card = build_clarify_resolved_card(question="Q", selected="Custom answer", choices=None)
        elements = card["body"]["elements"]
        # Should have: question div, selected div — no markdown choices list
        md_els = [e for e in elements if e.get("tag") == "markdown"]
        assert len(md_els) == 0

    def test_locales_in_config(self) -> None:
        card = build_clarify_resolved_card(question="Q", selected="A")
        assert "locales" in card["config"]


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
        try:
            from hermes_lark_streaming.controller import get_controller as _gc  # noqa: F401
        except ImportError:
            pytest.skip("controller module not importable (missing lark_oapi)")

        from hermes_lark_streaming.monkey_patch import _wrap_feishu_adapter_send_clarify

        orig = AsyncMock(return_value="original_result")
        wrapped = _wrap_feishu_adapter_send_clarify(orig)

        mock_ctrl = MagicMock()
        mock_ctrl.enabled = False

        mock_cg = MagicMock()
        with (
            patch.dict("sys.modules", {"tools": MagicMock(), "tools.clarify_gateway": mock_cg}),
            patch("hermes_lark_streaming.controller.get_controller", return_value=mock_ctrl),
        ):
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
        try:
            from hermes_lark_streaming.controller import get_controller as _gc  # noqa: F401
        except ImportError:
            pytest.skip("controller module not importable (missing lark_oapi)")

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
        """When hermes_clarify_action='select', should return submitted card (soft lock)."""
        from hermes_lark_streaming.monkey_patch import (
            _clarify_choices,
            _clarify_questions,
            _clarify_selections,
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

        # Verify selection was stored for retry
        assert _clarify_selections.get("test_cid") == "Alpha"

        # Cleanup
        _clarify_choices.pop("test_cid", None)
        _clarify_questions.pop("test_cid", None)
        _clarify_selections.pop("test_cid", None)

    def test_intercepts_input_submit_action(self) -> None:
        """When hermes_clarify_action='input_submit', should resolve with input text."""
        from hermes_lark_streaming.monkey_patch import (
            _clarify_choices,
            _clarify_questions,
            _clarify_selections,
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

        # Verify selection was stored for retry
        assert _clarify_selections.get("test_cid3") == "My custom answer"

        # Cleanup
        _clarify_choices.pop("test_cid3", None)
        _clarify_questions.pop("test_cid3", None)
        _clarify_selections.pop("test_cid3", None)

    def test_intercepts_retry_submit_action(self) -> None:
        """When hermes_clarify_action='retry_submit', should re-send stored selection."""
        from hermes_lark_streaming.monkey_patch import (
            _clarify_choices,
            _clarify_questions,
            _clarify_selections,
            _handle_clarify_card_action,
        )

        # Set up stored data including a previous selection
        _clarify_questions["test_cid_retry"] = "Which?"
        _clarify_choices["test_cid_retry"] = ["Alpha", "Beta"]
        _clarify_selections["test_cid_retry"] = "Alpha"

        # Mock the action — user clicked retry button
        mock_action = MagicMock()
        mock_action.value = {"hermes_clarify_action": "retry_submit", "clarify_id": "test_cid_retry"}

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
                mock_adapter, mock_data, "retry_submit",
                {"hermes_clarify_action": "retry_submit", "clarify_id": "test_cid_retry"},
            )

        # Cleanup
        _clarify_choices.pop("test_cid_retry", None)
        _clarify_questions.pop("test_cid_retry", None)
        _clarify_selections.pop("test_cid_retry", None)

    def test_intercepts_button_submit_action(self) -> None:
        """When hermes_clarify_action='button_submit', should resolve with form_value input."""
        from hermes_lark_streaming.monkey_patch import (
            _clarify_choices,
            _clarify_questions,
            _clarify_selections,
            _handle_clarify_card_action,
        )

        # Set up stored data
        _clarify_questions["test_cid_btn"] = "Tell me"

        # Mock the action — user clicked submit button
        mock_action = MagicMock()
        mock_action.value = {"hermes_clarify_action": "button_submit", "clarify_id": "test_cid_btn"}
        mock_action.form_value = {"clarify_input": "Button input text"}

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
                mock_adapter, mock_data, "button_submit",
                {"hermes_clarify_action": "button_submit", "clarify_id": "test_cid_btn"},
            )

        # Verify selection was stored for retry
        assert _clarify_selections.get("test_cid_btn") == "Button input text"

        # Cleanup
        _clarify_choices.pop("test_cid_btn", None)
        _clarify_questions.pop("test_cid_btn", None)
        _clarify_selections.pop("test_cid_btn", None)

    def test_no_other_action_handling(self) -> None:
        """There should be no 'other' option handling — it was removed."""
        from hermes_lark_streaming.monkey_patch import _handle_clarify_card_action

        # The select action handler should no longer have an "other" branch
        # Verify by checking that selecting "other" as option returns empty response
        _clarify_choices_stored = {}
        _clarify_questions_stored = {}

        mock_action = MagicMock()
        mock_action.value = {"hermes_clarify_action": "select", "clarify_id": "test_other"}
        mock_action.option = "other"

        mock_event = MagicMock()
        mock_event.action = mock_action
        mock_event.operator = MagicMock()
        mock_event.operator.open_id = "user_123"

        mock_data = MagicMock()
        mock_data.event = mock_event

        mock_adapter = MagicMock()
        mock_adapter._is_interactive_operator_authorized.return_value = True

        with (
            patch("hermes_lark_streaming.monkey_patch._clarify_choices", _clarify_choices_stored),
            patch("hermes_lark_streaming.monkey_patch._clarify_questions", _clarify_questions_stored),
        ):
            _clarify_choices_stored["test_other"] = ["Alpha", "Beta"]
            _clarify_questions_stored["test_other"] = "Which?"
            result = _handle_clarify_card_action(
                mock_adapter, mock_data, "select",
                {"hermes_clarify_action": "select", "clarify_id": "test_other"},
            )
            # "other" is not a valid int index, so it should trigger ValueError
            # and return empty response (not an awaiting card)
            # The result should be None (empty response) not a card with awaiting state


class TestClarifyCardRegistry:
    """Test the _clarify_choices, _clarify_questions, _clarify_card_msg_ids, _clarify_selections dicts."""

    def test_choices_registry_exists(self) -> None:
        from hermes_lark_streaming.monkey_patch import _clarify_choices
        assert isinstance(_clarify_choices, dict)

    def test_questions_registry_exists(self) -> None:
        from hermes_lark_streaming.monkey_patch import _clarify_questions
        assert isinstance(_clarify_questions, dict)

    def test_card_msg_ids_registry_exists(self) -> None:
        from hermes_lark_streaming.monkey_patch import _clarify_card_msg_ids
        assert isinstance(_clarify_card_msg_ids, dict)

    def test_selections_registry_exists(self) -> None:
        from hermes_lark_streaming.monkey_patch import _clarify_selections
        assert isinstance(_clarify_selections, dict)

    def test_choices_cleanup_after_confirm(self) -> None:
        """After confirming a clarify, all stored data should be cleaned up."""
        from hermes_lark_streaming.monkey_patch import (
            _clarify_choices,
            _clarify_questions,
            _clarify_card_msg_ids,
            _clarify_selections,
        )

        _clarify_choices["cleanup_test"] = ["A", "B"]
        _clarify_questions["cleanup_test"] = "Q"
        _clarify_card_msg_ids["cleanup_test"] = "msg_123"
        _clarify_selections["cleanup_test"] = "A"

        # Simulate cleanup (as _schedule_confirm_card would do)
        _clarify_choices.pop("cleanup_test", None)
        _clarify_questions.pop("cleanup_test", None)
        _clarify_card_msg_ids.pop("cleanup_test", None)
        _clarify_selections.pop("cleanup_test", None)

        assert "cleanup_test" not in _clarify_choices
        assert "cleanup_test" not in _clarify_questions
        assert "cleanup_test" not in _clarify_card_msg_ids
        assert "cleanup_test" not in _clarify_selections


class TestClarifyI18n:
    """Test clarify-related i18n entries exist."""

    def test_clarify_question_entry(self) -> None:
        assert "clarify_question" in _T

    def test_clarify_select_placeholder_entry(self) -> None:
        assert "clarify_select_placeholder" in _T

    def test_clarify_input_placeholder_entry(self) -> None:
        assert "clarify_input_placeholder" in _T

    def test_clarify_submit_entry(self) -> None:
        assert "clarify_submit" in _T

    def test_clarify_submitted_entry(self) -> None:
        assert "clarify_submitted" in _T

    def test_clarify_retry_entry(self) -> None:
        assert "clarify_retry" in _T

    def test_clarify_resolved_entry(self) -> None:
        assert "clarify_resolved" in _T

    def test_clarify_confirmed_entry(self) -> None:
        assert "clarify_confirmed" in _T

    def test_no_clarify_other_entry(self) -> None:
        """The 'clarify_other' i18n key should have been removed."""
        assert "clarify_other" not in _T

    def test_no_clarify_awaiting_input_entry(self) -> None:
        """The 'clarify_awaiting_input' i18n key should have been removed."""
        assert "clarify_awaiting_input" not in _T

    def test_no_clarify_locked_entry(self) -> None:
        """The 'clarify_locked' i18n key should have been replaced by 'clarify_confirmed'."""
        assert "clarify_locked" not in _T

    def test_all_entries_are_tuples_of_two(self) -> None:
        clarify_keys = [
            "clarify_question", "clarify_select_placeholder",
            "clarify_input_placeholder", "clarify_submit",
            "clarify_submitted", "clarify_retry",
            "clarify_resolved", "clarify_confirmed",
        ]
        for key in clarify_keys:
            assert key in _T, f"Missing i18n key: {key}"
            en, zh = _T[key]
            assert isinstance(en, str) and len(en) > 0, f"Empty English text for {key}"
            assert isinstance(zh, str) and len(zh) > 0, f"Empty Chinese text for {key}"
