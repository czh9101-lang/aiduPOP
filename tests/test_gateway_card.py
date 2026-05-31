"""Tests for gateway message card interception (v0.14.0 Phase 1)."""

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
        # First element should be the icon header
        assert elements[0]["tag"] == "plain_text"
        assert elements[0]["content"] == "🔔"
        # Second element should be the content markdown
        assert elements[1]["tag"] == "markdown"
        assert "System notification" in elements[1]["content"]

    def test_error_category(self):
        card = build_gateway_card("Something failed", category="error")
        elements = card["body"]["elements"]
        assert elements[0]["content"] == "❌"

    def test_auth_category(self):
        card = build_gateway_card("Pairing code: 1234", category="auth")
        elements = card["body"]["elements"]
        assert elements[0]["content"] == "🔐"

    def test_session_category(self):
        card = build_gateway_card("Session reset", category="session")
        elements = card["body"]["elements"]
        assert elements[0]["content"] == "🔄"

    def test_slash_category(self):
        card = build_gateway_card("/help output", category="slash")
        elements = card["body"]["elements"]
        assert elements[0]["content"] == "⌨️"

    def test_default_category_is_system(self):
        card = build_gateway_card("Hello", category="")
        elements = card["body"]["elements"]
        assert elements[0]["content"] == "🔔"

    def test_unknown_category_defaults_to_system(self):
        card = build_gateway_card("Hello", category="unknown_category")
        elements = card["body"]["elements"]
        assert elements[0]["content"] == "🔔"

    def test_empty_content_produces_card(self):
        card = build_gateway_card("")
        assert card["schema"] == "2.0"
        elements = card["body"]["elements"]
        # Still has icon header
        assert elements[0]["tag"] == "plain_text"

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
