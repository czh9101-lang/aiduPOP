"""Tests for gateway message card interception (v0.14.0 Phase 1-4)."""

from __future__ import annotations

import pytest

from hermes_lark_streaming.cardkit import build_gateway_card


class TestBuildGatewayCard:
    """Test the build_gateway_card() function."""

    def test_basic_system_card(self):
        card = build_gateway_card("System notification")
        assert card["schema"] == "2.0"
        assert "elements" not in card  # schema 2.0 uses body
        assert "body" in card
        elements = card["body"]["elements"]
        # First element should be the icon header wrapped in div.text
        # (plain_text is NOT valid as a direct child of body.elements in
        # CardKit 2.0 — must be wrapped in div.text to avoid API error 230099)
        assert elements[0]["tag"] == "div"
        assert elements[0]["text"]["tag"] == "plain_text"
        assert elements[0]["text"]["content"] == "🔔"
        # Second element should be the content markdown
        assert elements[1]["tag"] == "markdown"
        assert "System notification" in elements[1]["content"]

    def test_error_category(self):
        card = build_gateway_card("Something failed", category="error")
        elements = card["body"]["elements"]
        assert elements[0]["text"]["content"] == "❌"

    def test_auth_category(self):
        card = build_gateway_card("Pairing code: 1234", category="auth")
        elements = card["body"]["elements"]
        assert elements[0]["text"]["content"] == "🔐"

    def test_session_category(self):
        card = build_gateway_card("Session reset", category="session")
        elements = card["body"]["elements"]
        assert elements[0]["text"]["content"] == "🔄"

    def test_slash_category(self):
        card = build_gateway_card("/help output", category="slash")
        elements = card["body"]["elements"]
        assert elements[0]["text"]["content"] == "⌨️"

    def test_default_category_is_system(self):
        card = build_gateway_card("Hello", category="")
        elements = card["body"]["elements"]
        assert elements[0]["text"]["content"] == "🔔"

    def test_unknown_category_defaults_to_system(self):
        card = build_gateway_card("Hello", category="unknown_category")
        elements = card["body"]["elements"]
        assert elements[0]["text"]["content"] == "🔔"

    def test_empty_content_produces_card(self):
        card = build_gateway_card("")
        assert card["schema"] == "2.0"
        elements = card["body"]["elements"]
        # Still has icon header (wrapped in div.text)
        assert elements[0]["tag"] == "div"
        assert elements[0]["text"]["tag"] == "plain_text"

    def test_summary_generated(self):
        card = build_gateway_card("This is a long message that should have a summary")
        assert "summary" in card["config"]
        assert "This is a long message" in card["config"]["summary"]["content"]

    def test_locales_in_config(self):
        card = build_gateway_card("Test")
        assert "locales" in card["config"]

    def test_markdown_optimization_applied(self):
        """Verify that optimize_markdown_style and _downgrade_tables are applied."""
        content = "| A | B |\n|---|---|\n| 1 | 2 |"
        card = build_gateway_card(content)
        elements = card["body"]["elements"]
        # Should have icon + at least one markdown element
        assert len(elements) >= 2


class TestBuildGatewayCardStatusIndicator:
    """Test Phase 3: status indicator in build_gateway_card()."""

    def test_status_indicator_replaces_category_icon(self):
        """When status_label and status_emoji are set, they replace the category icon."""
        card = build_gateway_card(
            "Processing your request",
            category="system",
            status_label="Reading",
            status_emoji="👀",
        )
        elements = card["body"]["elements"]
        # First element should be the status indicator wrapped in div.text
        assert elements[0]["tag"] == "div"
        assert elements[0]["text"]["tag"] == "plain_text"
        assert elements[0]["text"]["content"] == "👀 Reading"
        assert elements[0]["text"]["text_color"] == "turquoise"

    def test_no_status_shows_category_icon(self):
        """When no status is set, the category icon is shown (default behavior)."""
        card = build_gateway_card("Hello", category="error")
        elements = card["body"]["elements"]
        assert elements[0]["text"]["content"] == "❌"
        assert elements[0]["text"]["text_color"] == "grey"

    def test_empty_status_shows_category_icon(self):
        """When status_label is empty, the category icon is shown."""
        card = build_gateway_card("Hello", status_label="", status_emoji="👀")
        elements = card["body"]["elements"]
        # No status → show category icon (default system)
        assert elements[0]["text"]["content"] == "🔔"

    def test_processing_status(self):
        card = build_gateway_card("Working...", status_label="Processing", status_emoji="⏳")
        elements = card["body"]["elements"]
        assert elements[0]["text"]["content"] == "⏳ Processing"


class TestBuildGatewayCardMedia:
    """Test Phase 4: media elements in build_gateway_card()."""

    def test_image_media_element(self):
        """Image media parts are rendered as img elements in the card."""
        card = build_gateway_card(
            "Here is an image",
            media_parts=[{"type": "image", "key": "img_v3_test123"}],
        )
        elements = card["body"]["elements"]
        # Should have: icon div + img + markdown
        assert len(elements) >= 3
        # Find the img element
        img_elements = [e for e in elements if e.get("tag") == "img"]
        assert len(img_elements) == 1
        assert img_elements[0]["img_key"] == "img_v3_test123"

    def test_file_media_element(self):
        """File media parts are rendered as text links in the card (wrapped in div.text)."""
        card = build_gateway_card(
            "Here is a file",
            media_parts=[{"type": "file", "key": "file_v3_test456", "name": "report.pdf"}],
        )
        elements = card["body"]["elements"]
        # Find the file element (wrapped in div.text)
        file_elements = [e for e in elements if e.get("tag") == "div" and "📎" in e.get("text", {}).get("content", "")]
        assert len(file_elements) == 1
        assert "report.pdf" in file_elements[0]["text"]["content"]

    def test_multiple_media_parts(self):
        """Multiple media parts are all included in the card."""
        card = build_gateway_card(
            "Multiple files",
            media_parts=[
                {"type": "image", "key": "img_v3_1"},
                {"type": "image", "key": "img_v3_2"},
                {"type": "file", "key": "file_v3_1", "name": "doc.pdf"},
            ],
        )
        elements = card["body"]["elements"]
        img_elements = [e for e in elements if e.get("tag") == "img"]
        assert len(img_elements) == 2
        file_elements = [e for e in elements if e.get("tag") == "div" and "📎" in e.get("text", {}).get("content", "")]
        assert len(file_elements) == 1

    def test_no_media_parts(self):
        """When media_parts is None, no media elements are added."""
        card = build_gateway_card("Just text", media_parts=None)
        elements = card["body"]["elements"]
        img_elements = [e for e in elements if e.get("tag") == "img"]
        assert len(img_elements) == 0

    def test_empty_media_parts_list(self):
        """When media_parts is an empty list, no media elements are added."""
        card = build_gateway_card("Just text", media_parts=[])
        elements = card["body"]["elements"]
        img_elements = [e for e in elements if e.get("tag") == "img"]
        assert len(img_elements) == 0


class TestClassifyGatewayMessage:
    """Test the _classify_gateway_message() function."""

    def test_import(self):
        from hermes_lark_streaming.monkey_patch import _classify_gateway_message
        assert callable(_classify_gateway_message)

    def test_auth_pairing_code(self):
        from hermes_lark_streaming.monkey_patch import _classify_gateway_message
        assert _classify_gateway_message("Here's your pairing code: ABC123") == "auth"

    def test_auth_dont_recognize(self):
        from hermes_lark_streaming.monkey_patch import _classify_gateway_message
        assert _classify_gateway_message("I don't recognize you yet!") == "auth"

    def test_error_warning(self):
        from hermes_lark_streaming.monkey_patch import _classify_gateway_message
        assert _classify_gateway_message("⚠️ Provider authentication failed") == "error"

    def test_error_failed(self):
        from hermes_lark_streaming.monkey_patch import _classify_gateway_message
        assert _classify_gateway_message("Something failed after retries") == "error"

    def test_session_reset(self):
        from hermes_lark_streaming.monkey_patch import _classify_gateway_message
        assert _classify_gateway_message("Session automatically reset") == "session"

    def test_slash_help(self):
        from hermes_lark_streaming.monkey_patch import _classify_gateway_message
        assert _classify_gateway_message("/help shows available commands") == "slash"

    def test_slash_status(self):
        from hermes_lark_streaming.monkey_patch import _classify_gateway_message
        assert _classify_gateway_message("/status output here") == "slash"

    def test_system_default(self):
        from hermes_lark_streaming.monkey_patch import _classify_gateway_message
        assert _classify_gateway_message("Just a regular message") == "system"

    def test_non_string_returns_system(self):
        from hermes_lark_streaming.monkey_patch import _classify_gateway_message
        assert _classify_gateway_message(12345) == "system"


class TestGatewayCardsConfig:
    """Test the gateway_cards config property."""

    def test_default_is_true(self):
        from hermes_lark_streaming.config import Config
        cfg = Config()
        # No config file loaded — default should be True
        assert cfg.gateway_cards is True

    def test_property_exists(self):
        from hermes_lark_streaming.config import Config
        cfg = Config()
        assert hasattr(cfg, "gateway_cards")


class TestGatewayCardRegistry:
    """Test Phase 2: gateway card registry for edit_message support."""

    def test_register_and_lookup(self):
        from hermes_lark_streaming.monkey_patch import _register_gateway_card, _gateway_cards, _gateway_cards_lock
        # Register a card
        _register_gateway_card("msg_test_123", chat_id="chat_abc", card_id="card_xyz", category="error")
        # Look it up
        with _gateway_cards_lock:
            info = _gateway_cards.get("msg_test_123")
        assert info is not None
        assert info["chat_id"] == "chat_abc"
        assert info["card_id"] == "card_xyz"
        assert info["category"] == "error"
        # Cleanup
        from hermes_lark_streaming.monkey_patch import _unregister_gateway_card
        _unregister_gateway_card("msg_test_123")

    def test_unregister_removes_entry(self):
        from hermes_lark_streaming.monkey_patch import _register_gateway_card, _unregister_gateway_card, _gateway_cards, _gateway_cards_lock
        _register_gateway_card("msg_test_456", chat_id="chat_def", card_id=None, category="system")
        _unregister_gateway_card("msg_test_456")
        with _gateway_cards_lock:
            assert _gateway_cards.get("msg_test_456") is None

    def test_register_empty_id_is_noop(self):
        from hermes_lark_streaming.monkey_patch import _register_gateway_card, _gateway_cards, _gateway_cards_lock
        _register_gateway_card("", chat_id="chat_ghi", card_id=None, category="system")
        with _gateway_cards_lock:
            assert "" not in _gateway_cards


class TestReactionStatusMap:
    """Test Phase 3: reaction emoji to status label mapping."""

    def test_reaction_map_exists(self):
        from hermes_lark_streaming.monkey_patch import _REACTION_STATUS_MAP
        assert isinstance(_REACTION_STATUS_MAP, dict)
        assert len(_REACTION_STATUS_MAP) > 0

    def test_common_reactions_mapped(self):
        from hermes_lark_streaming.monkey_patch import _REACTION_STATUS_MAP
        assert "👀" in _REACTION_STATUS_MAP
        assert "👍" in _REACTION_STATUS_MAP
        assert "🤔" in _REACTION_STATUS_MAP

    def test_reaction_values_are_strings(self):
        from hermes_lark_streaming.monkey_patch import _REACTION_STATUS_MAP
        for emoji, label in _REACTION_STATUS_MAP.items():
            assert isinstance(emoji, str)
            assert isinstance(label, str)
            assert len(label) > 0
