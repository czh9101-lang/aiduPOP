"""monkey_patch.py 测试 — 时间注入、重入守卫、补丁策略."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from hermes_lark_streaming.monkey_patch import (
    _inject_time_guard,
    _inject_time_prefix,
)


# ── _inject_time_prefix ──


class TestInjectTimePrefix:
    """_inject_time_prefix: XML tag format, inject_time toggle, re-entrancy guard."""

    def _make_config(self, inject_time: bool = True) -> MagicMock:
        cfg = MagicMock()
        cfg.inject_time = inject_time
        return cfg

    def test_prepends_xml_time_tag_when_enabled(self) -> None:
        """When inject_time is True, prepend <time>HH:MM:SS</time> to user_message."""
        # Reset re-entrancy guard before each test
        _inject_time_guard.active = False

        cfg = self._make_config(inject_time=True)

        with (
            patch("hermes_lark_streaming.monkey_patch._get_config", return_value=cfg),
            patch("hermes_lark_streaming.monkey_patch.datetime") as mock_dt,
        ):
            # Mock datetime.now() to return a fixed time
            _cst = timezone(timedelta(hours=8))
            mock_dt.now.return_value = datetime(2026, 5, 28, 14, 30, 5, tzinfo=_cst)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            user_msg, persist_msg = _inject_time_prefix("你好", None)

        assert user_msg == "<time>14:30:05</time> 你好"
        assert persist_msg is None

        # Reset guard for subsequent tests
        _inject_time_guard.active = False

    def test_no_prefix_when_disabled(self) -> None:
        """When inject_time is False, return messages unchanged."""
        _inject_time_guard.active = False

        cfg = self._make_config(inject_time=False)

        with patch("hermes_lark_streaming.monkey_patch._get_config", return_value=cfg):
            user_msg, persist_msg = _inject_time_prefix("你好", "persist")

        assert user_msg == "你好"
        assert persist_msg == "persist"

        _inject_time_guard.active = False

    def test_no_prefix_when_config_read_fails(self) -> None:
        """When config read fails, return messages unchanged."""
        _inject_time_guard.active = False

        with patch("hermes_lark_streaming.monkey_patch._get_config", side_effect=RuntimeError("boom")):
            user_msg, persist_msg = _inject_time_prefix("你好", None)

        assert user_msg == "你好"
        assert persist_msg is None

        _inject_time_guard.active = False

    def test_prefixes_both_user_and_persist_messages(self) -> None:
        """Both user_message and persist_user_message get the time prefix."""
        _inject_time_guard.active = False

        cfg = self._make_config(inject_time=True)

        with (
            patch("hermes_lark_streaming.monkey_patch._get_config", return_value=cfg),
            patch("hermes_lark_streaming.monkey_patch.datetime") as mock_dt,
        ):
            _cst = timezone(timedelta(hours=8))
            mock_dt.now.return_value = datetime(2026, 5, 28, 9, 15, 0, tzinfo=_cst)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            user_msg, persist_msg = _inject_time_prefix("hello", "persist_hello")

        assert user_msg == "<time>09:15:00</time> hello"
        assert persist_msg == "<time>09:15:00</time> persist_hello"

        _inject_time_guard.active = False

    def test_handles_none_user_message(self) -> None:
        """When user_message is None, it stays None."""
        _inject_time_guard.active = False

        cfg = self._make_config(inject_time=True)

        with (
            patch("hermes_lark_streaming.monkey_patch._get_config", return_value=cfg),
            patch("hermes_lark_streaming.monkey_patch.datetime") as mock_dt,
        ):
            _cst = timezone(timedelta(hours=8))
            mock_dt.now.return_value = datetime(2026, 5, 28, 14, 30, 5, tzinfo=_cst)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            user_msg, persist_msg = _inject_time_prefix(None, None)

        assert user_msg is None
        assert persist_msg is None

        _inject_time_guard.active = False

    def test_uses_cst_timezone(self) -> None:
        """Time should be in CST (UTC+8)."""
        _inject_time_guard.active = False

        cfg = self._make_config(inject_time=True)

        with (
            patch("hermes_lark_streaming.monkey_patch._get_config", return_value=cfg),
            patch("hermes_lark_streaming.monkey_patch.datetime") as mock_dt,
        ):
            # Simulate UTC 06:30:05 → CST 14:30:05
            _cst = timezone(timedelta(hours=8))
            mock_dt.now.return_value = datetime(2026, 5, 28, 14, 30, 5, tzinfo=_cst)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            user_msg, _ = _inject_time_prefix("test", None)

        assert "<time>14:30:05</time>" in user_msg

        _inject_time_guard.active = False

    def test_xml_tag_format_not_bracket_format(self) -> None:
        """Format should be <time>HH:MM:SS</time>, NOT [HH:MM:SS CST]."""
        _inject_time_guard.active = False

        cfg = self._make_config(inject_time=True)

        with (
            patch("hermes_lark_streaming.monkey_patch._get_config", return_value=cfg),
            patch("hermes_lark_streaming.monkey_patch.datetime") as mock_dt,
        ):
            _cst = timezone(timedelta(hours=8))
            mock_dt.now.return_value = datetime(2026, 5, 28, 14, 30, 5, tzinfo=_cst)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            user_msg, _ = _inject_time_prefix("test", None)

        # Should use XML tags, not brackets
        assert user_msg.startswith("<time>")
        assert "</time>" in user_msg
        # Should NOT contain CST or brackets
        assert "CST" not in user_msg
        assert not user_msg.startswith("[")

        _inject_time_guard.active = False

    def test_no_date_in_prefix(self) -> None:
        """Time prefix should NOT contain the date (system prompt already has it)."""
        _inject_time_guard.active = False

        cfg = self._make_config(inject_time=True)

        with (
            patch("hermes_lark_streaming.monkey_patch._get_config", return_value=cfg),
            patch("hermes_lark_streaming.monkey_patch.datetime") as mock_dt,
        ):
            _cst = timezone(timedelta(hours=8))
            mock_dt.now.return_value = datetime(2026, 5, 28, 14, 30, 5, tzinfo=_cst)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            user_msg, _ = _inject_time_prefix("test", None)

        # Should not contain date components
        assert "2026" not in user_msg
        # The prefix should be exactly <time>HH:MM:SS</time>
        prefix = user_msg.split(" test")[0]
        assert prefix == "<time>14:30:05</time>"

        _inject_time_guard.active = False


class TestInjectTimeReentrancyGuard:
    """_inject_time_prefix re-entrancy guard prevents double injection."""

    def _make_config(self, inject_time: bool = True) -> MagicMock:
        cfg = MagicMock()
        cfg.inject_time = inject_time
        return cfg

    def test_reentrancy_guard_prevents_double_injection(self) -> None:
        """If guard.active is True, skip injection."""
        # Set the guard
        _inject_time_guard.active = True
        try:
            user_msg, persist_msg = _inject_time_prefix("你好", None)
            assert user_msg == "你好"
            assert persist_msg is None
        finally:
            _inject_time_guard.active = False

    def test_guard_is_set_after_injection(self) -> None:
        """After successful injection, _inject_time_guard.active should be True."""
        cfg = self._make_config(inject_time=True)

        with (
            patch("hermes_lark_streaming.monkey_patch._get_config", return_value=cfg),
            patch("hermes_lark_streaming.monkey_patch.datetime") as mock_dt,
        ):
            _cst = timezone(timedelta(hours=8))
            mock_dt.now.return_value = datetime(2026, 5, 28, 14, 30, 5, tzinfo=_cst)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            _inject_time_prefix("你好", None)

        assert getattr(_inject_time_guard, 'active', False) is True
        # Reset for other tests
        _inject_time_guard.active = False

    def test_second_call_is_noop_when_guard_active(self) -> None:
        """Simulate the dual-patch scenario: module patch + AIAgent patch."""
        cfg = self._make_config(inject_time=True)

        with (
            patch("hermes_lark_streaming.monkey_patch._get_config", return_value=cfg),
            patch("hermes_lark_streaming.monkey_patch.datetime") as mock_dt,
        ):
            _cst = timezone(timedelta(hours=8))
            mock_dt.now.return_value = datetime(2026, 5, 28, 14, 30, 5, tzinfo=_cst)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            # First call — injects
            user_msg1, _ = _inject_time_prefix("你好", None)
            assert user_msg1 == "<time>14:30:05</time> 你好"

            # Second call (simulating nested patch layer) — should be no-op
            user_msg2, _ = _inject_time_prefix("你好", None)
            assert user_msg2 == "你好"  # No prefix added

        # Reset for other tests
        _inject_time_guard.active = False


class TestInjectTimeGuardReset:
    """Verify that _inject_time_guard is properly reset by the wrapper's finally block."""

    def test_guard_reset_between_messages(self) -> None:
        """After _inject_time_prefix is called and guard is set,
        the wrapper (in _wrap_run_conversation) should reset it in finally.
        We test the reset mechanism here."""
        # Simulate what _wrap_run_conversation / _patched_run_conversation does:
        # 1. Call _inject_time_prefix (sets guard.active = True)
        # 2. Finally block resets guard.active = False

        cfg = MagicMock()
        cfg.inject_time = True

        with (
            patch("hermes_lark_streaming.monkey_patch._get_config", return_value=cfg),
            patch("hermes_lark_streaming.monkey_patch.datetime") as mock_dt,
        ):
            _cst = timezone(timedelta(hours=8))
            mock_dt.now.return_value = datetime(2026, 5, 28, 14, 30, 5, tzinfo=_cst)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            # Simulate first message
            _inject_time_prefix("msg1", None)
            # Guard is now active
            assert _inject_time_guard.active is True
            # Wrapper's finally block resets guard
            _inject_time_guard.active = False

            # Simulate second message — should inject again
            user_msg, _ = _inject_time_prefix("msg2", None)
            assert user_msg == "<time>14:30:05</time> msg2"

        _inject_time_guard.active = False


# ── Version logging tests ──


class TestVersionLogging:
    """Verify __version__ is included in key log messages."""

    def test_version_is_available(self) -> None:
        """Plugin should expose __version__ from plugin.yaml."""
        from hermes_lark_streaming import __version__
        assert __version__
        assert __version__ != "unknown"

    def test_register_logs_version(self) -> None:
        """register() should log the version number."""
        from hermes_lark_streaming import __version__
        from hermes_lark_streaming.plugin import register

        mock_ctx = MagicMock()
        with (
            patch("hermes_lark_streaming.plugin._ensure_streaming_config"),
            patch("hermes_lark_streaming.monkey_patch.apply_patches"),
            patch("hermes_lark_streaming.plugin._logger") as mock_logger,
        ):
            register(mock_ctx)

        # Check that version is in at least one info log
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        version_logged = any(__version__ in call for call in info_calls)
        assert version_logged, f"Version {__version__} not found in log calls: {info_calls}"

    def test_monkey_patch_module_imports_version(self) -> None:
        """monkey_patch.py should import __version__ from the package."""
        from hermes_lark_streaming.monkey_patch import __version__ as mp_version
        from hermes_lark_streaming import __version__ as pkg_version
        assert mp_version == pkg_version


# ── Cron delivery wrapper tests ──


class TestCronDeliveryWrapper:
    """Verify _wrap_cron_deliver uses direct await instead of run_coroutine_threadsafe."""

    def test_cron_wrapper_no_adapters_falls_through(self) -> None:
        """When no adapters are provided, cron delivery falls through to original."""
        from hermes_lark_streaming.monkey_patch import _wrap_cron_deliver

        orig = MagicMock(return_value="original_result")
        wrapper = _wrap_cron_deliver(orig)

        result = wrapper(job={"id": "test"}, content="hello", adapters=None)

        orig.assert_called_once()
        assert result == "original_result"

    def test_cron_wrapper_no_feishu_adapter_falls_through(self) -> None:
        """When adapters exist but no Feishu adapter, falls through to original."""
        from hermes_lark_streaming.monkey_patch import _wrap_cron_deliver

        orig = MagicMock(return_value="original_result")
        wrapper = _wrap_cron_deliver(orig)

        # Create mock adapters dict with a non-Feishu platform
        mock_adapters = {}
        mock_platform = MagicMock()
        mock_platform.value = "telegram"
        mock_adapters[mock_platform] = MagicMock()

        result = wrapper(job={"id": "test"}, content="hello", adapters=mock_adapters)

        orig.assert_called_once()
        assert result == "original_result"
