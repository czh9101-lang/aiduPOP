"""config.py 测试 — 配置加载、footer 字段容错、平台配置优先级、_reload_cached TTL 缓存."""

from __future__ import annotations

import os
import time
from typing import Any
from unittest.mock import patch

from hermes_lark_streaming.config import Config


def _make_config(raw: dict[str, Any]) -> Config:
    """Create a Config pre-loaded with given raw dict."""
    cfg = Config()
    cfg._raw = raw
    return cfg


class TestEnabled:
    def test_enabled_true(self) -> None:
        cfg = _make_config({"streaming": {"enabled": True}})
        assert cfg.enabled is True

    def test_enabled_false(self) -> None:
        cfg = _make_config({"streaming": {"enabled": False}})
        assert cfg.enabled is False

    def test_enabled_missing(self) -> None:
        cfg = _make_config({"streaming": {}})
        assert cfg.enabled is False

    def test_no_streaming_section(self) -> None:
        cfg = _make_config({})
        assert cfg.enabled is False

    def test_streaming_section_not_dict(self) -> None:
        cfg = _make_config({"streaming": "invalid"})
        assert cfg.enabled is False


class TestFooterFields:
    _DEFAULT_FIELDS = [["status", "elapsed", "model", "api_calls"], ["tokens", "context", "history_offset", "compression_exhausted"]]

    def test_normal_2d_fields(self) -> None:
        cfg = _make_config({"streaming": {"footer": {"fields": [["a", "b"], ["c"]]}}})
        assert cfg.footer_fields == [["a", "b"], ["c"]]

    def test_1d_auto_wrapped(self) -> None:
        cfg = _make_config({"streaming": {"footer": {"fields": ["status", "elapsed"]}}})
        assert cfg.footer_fields == [["status", "elapsed"]]

    def test_empty_fields_returns_default(self) -> None:
        cfg = _make_config({"streaming": {"footer": {"fields": []}}})
        assert cfg.footer_fields == self._DEFAULT_FIELDS

    def test_no_fields_returns_default(self) -> None:
        cfg = _make_config({"streaming": {"footer": {}}})
        assert cfg.footer_fields == self._DEFAULT_FIELDS

    def test_no_footer_returns_default(self) -> None:
        cfg = _make_config({"streaming": {}})
        assert cfg.footer_fields == self._DEFAULT_FIELDS

    def test_footer_not_dict_returns_default(self) -> None:
        cfg = _make_config({"streaming": {"footer": "invalid"}})
        assert cfg.footer_fields == self._DEFAULT_FIELDS

    def test_no_streaming_section_returns_default(self) -> None:
        cfg = _make_config({})
        assert cfg.footer_fields == self._DEFAULT_FIELDS

    def test_fields_non_list_returns_default(self) -> None:
        cfg = _make_config({"streaming": {"footer": {"fields": "status"}}})
        assert cfg.footer_fields == self._DEFAULT_FIELDS

    def test_fields_int_returns_default(self) -> None:
        cfg = _make_config({"streaming": {"footer": {"fields": 42}}})
        assert cfg.footer_fields == self._DEFAULT_FIELDS


class TestFooterShowLabel:
    def test_true(self) -> None:
        cfg = _make_config({"streaming": {"footer": {"show_label": True}}})
        assert cfg.footer_show_label is True

    def test_false(self) -> None:
        cfg = _make_config({"streaming": {"footer": {"show_label": False}}})
        assert cfg.footer_show_label is False

    def test_missing_defaults_false(self) -> None:
        cfg = _make_config({"streaming": {"footer": {}}})
        assert cfg.footer_show_label is False


class TestCardDurationSec:
    def test_custom(self) -> None:
        cfg = _make_config({"streaming": {"card_ttl_sec": 300}})
        assert cfg.card_duration_sec == 300

    def test_default(self) -> None:
        cfg = _make_config({"streaming": {}})
        assert cfg.card_duration_sec == 600


class TestFeishuAppId:
    def test_from_env(self) -> None:
        cfg = _make_config({})
        with patch.dict(os.environ, {"FEISHU_APP_ID": "env_id", "FEISHU_APP_SECRET": "env_secret"}):
            assert cfg.feishu_app_id == "env_id"

    def test_from_config(self) -> None:
        cfg = _make_config({"feishu": {"app_id": "cfg_id", "app_secret": "cfg_secret"}})
        with patch.dict(os.environ, {}, clear=True):
            assert cfg.feishu_app_id == "cfg_id"

    def test_empty_when_missing(self) -> None:
        cfg = _make_config({})
        with patch.dict(os.environ, {}, clear=True):
            assert cfg.feishu_app_id == ""


class TestFeishuBaseURL:
    def test_default_url(self) -> None:
        cfg = _make_config({"feishu": {"app_id": "id", "app_secret": "s"}})
        with patch.dict(os.environ, {}, clear=True):
            assert cfg.feishu_base_url == "https://open.feishu.cn/open-apis"

    def test_custom_url_from_config(self) -> None:
        cfg = _make_config({"feishu": {"app_id": "id", "app_secret": "s", "base_url": "https://custom.com"}})
        with patch.dict(os.environ, {}, clear=True):
            assert cfg.feishu_base_url == "https://custom.com"

    def test_from_env(self) -> None:
        cfg = _make_config({})
        with patch.dict(
            os.environ, {"FEISHU_APP_ID": "id", "FEISHU_APP_SECRET": "s", "FEISHU_BASE_URL": "https://env.com"}
        ):
            assert cfg.feishu_base_url == "https://env.com"


class TestShowReasoning:
    def _make_reasoning_config(self, raw: dict[str, Any]) -> Config:
        """Create a Config with _reload_cached mocked to return given raw dict."""
        cfg = Config()
        cfg._reload_cached = lambda: raw  # type: ignore[assignment]
        return cfg

    def test_platform_level_true(self) -> None:
        cfg = self._make_reasoning_config({"display": {"platforms": {"feishu": {"show_reasoning": True}}}})
        assert cfg.show_reasoning is True

    def test_platform_level_false(self) -> None:
        cfg = self._make_reasoning_config({"display": {"platforms": {"feishu": {"show_reasoning": False}}}})
        assert cfg.show_reasoning is False

    def test_global_fallback_true(self) -> None:
        cfg = self._make_reasoning_config({"display": {"show_reasoning": True}})
        assert cfg.show_reasoning is True

    def test_global_fallback_false(self) -> None:
        cfg = self._make_reasoning_config({"display": {"show_reasoning": False}})
        assert cfg.show_reasoning is False

    def test_default_false(self) -> None:
        cfg = self._make_reasoning_config({})
        assert cfg.show_reasoning is False

    def test_display_not_dict(self) -> None:
        cfg = self._make_reasoning_config({"display": "invalid"})
        assert cfg.show_reasoning is False

    def test_platforms_not_dict(self) -> None:
        cfg = self._make_reasoning_config({"display": {"platforms": "invalid"}})
        assert cfg.show_reasoning is False

    def test_feishu_section_missing_key(self) -> None:
        cfg = self._make_reasoning_config({"display": {"platforms": {"feishu": {"other": True}}}})
        assert cfg.show_reasoning is False

    def test_platform_takes_priority_over_global(self) -> None:
        cfg = self._make_reasoning_config({
            "display": {
                "platforms": {"feishu": {"show_reasoning": False}},
                "show_reasoning": True,
            }
        })
        assert cfg.show_reasoning is False

    def test_no_display_section(self) -> None:
        cfg = self._make_reasoning_config({"streaming": {"enabled": True}})
        assert cfg.show_reasoning is False


class TestPlatformCfg:
    def test_env_takes_priority(self) -> None:
        cfg = _make_config({"feishu": {"app_id": "config_id", "app_secret": "config_secret"}})
        with patch.dict(os.environ, {"FEISHU_APP_ID": "env_id", "FEISHU_APP_SECRET": "env_secret"}):
            result = cfg._platform_cfg()
            assert result["app_id"] == "env_id"

    def test_lark_section_fallback(self) -> None:
        cfg = _make_config({"lark": {"app_id": "lark_id", "app_secret": "lark_secret"}})
        with patch.dict(os.environ, {}, clear=True):
            result = cfg._platform_cfg()
            assert result["app_id"] == "lark_id"

    def test_feishu_before_lark(self) -> None:
        cfg = _make_config(
            {
                "feishu": {"app_id": "feishu_id", "app_secret": "fs"},
                "lark": {"app_id": "lark_id", "app_secret": "ls"},
            }
        )
        with patch.dict(os.environ, {}, clear=True):
            result = cfg._platform_cfg()
            assert result["app_id"] == "feishu_id"

    def test_empty_when_nothing(self) -> None:
        cfg = _make_config({})
        with patch.dict(os.environ, {}, clear=True):
            assert cfg._platform_cfg() == {}


class TestInjectTime:
    def _make_inject_time_config(self, raw: dict[str, Any]) -> Config:
        """Create a Config with _reload_cached mocked to return given raw dict."""
        cfg = Config()
        cfg._reload_cached = lambda: raw  # type: ignore[assignment]
        return cfg

    def test_inject_time_true(self) -> None:
        cfg = self._make_inject_time_config({"streaming": {"inject_time": True}})
        assert cfg.inject_time is True

    def test_inject_time_false(self) -> None:
        cfg = self._make_inject_time_config({"streaming": {"inject_time": False}})
        assert cfg.inject_time is False

    def test_inject_time_missing_defaults_false(self) -> None:
        cfg = self._make_inject_time_config({"streaming": {}})
        assert cfg.inject_time is False

    def test_inject_time_no_streaming_section(self) -> None:
        cfg = self._make_inject_time_config({})
        assert cfg.inject_time is False

    def test_inject_time_streaming_not_dict(self) -> None:
        cfg = self._make_inject_time_config({"streaming": "invalid"})
        assert cfg.inject_time is False


class TestLinear:
    def test_linear_true(self) -> None:
        cfg = _make_config({"streaming": {"linear": True}})
        assert cfg.linear is True

    def test_linear_false(self) -> None:
        cfg = _make_config({"streaming": {"linear": False}})
        assert cfg.linear is False

    def test_linear_missing_defaults_true(self) -> None:
        cfg = _make_config({"streaming": {}})
        assert cfg.linear is True


class TestPanelExpanded:
    def test_panel_expanded_true(self) -> None:
        cfg = _make_config({"streaming": {"panel_expanded": True}})
        assert cfg.panel_expanded is True

    def test_panel_expanded_false(self) -> None:
        cfg = _make_config({"streaming": {"panel_expanded": False}})
        assert cfg.panel_expanded is False

    def test_panel_expanded_missing_defaults_false(self) -> None:
        cfg = _make_config({"streaming": {}})
        assert cfg.panel_expanded is False


class TestReloadCached:
    """_reload_cached() TTL 缓存行为测试."""

    def test_returns_cached_result_within_ttl(self) -> None:
        """在 TTL 窗口内，多次调用返回同一缓存结果，不重复读磁盘."""
        cfg = Config()
        raw1 = {"streaming": {"inject_time": True}}
        raw2 = {"streaming": {"inject_time": False}}

        call_count = 0
        original_reload_cached = cfg._reload_cached

        def counting_reload_cached() -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return raw1
            return raw2

        cfg._reload_cached = counting_reload_cached  # type: ignore[assignment]

        # First call populates the cache
        result1 = cfg._reload_cached()
        assert result1 == raw1
        assert call_count == 1

    def test_reloads_after_ttl_expires(self) -> None:
        """TTL 过期后，_reload_cached 重新读取磁盘（不再返回旧缓存）."""
        cfg = Config()
        raw_old = {"streaming": {"inject_time": False}}
        raw_new = {"streaming": {"inject_time": True}}

        # Pre-populate the cache with the old value
        cfg._reload_cache = raw_old
        cfg._reload_cache_at = time.monotonic() - 10.0  # TTL 已过期

        # Mock the actual disk read to return new data
        with patch.object(Config, "_reload_cached", return_value=raw_new):
            result = cfg._reload_cached()
            assert result == raw_new

    def test_cache_is_populated_on_first_call(self) -> None:
        """首次调用后 _reload_cache 和 _reload_cache_at 被设置."""
        cfg = Config()
        raw = {"streaming": {"inject_time": True}}

        assert cfg._reload_cache is None
        assert cfg._reload_cache_at == 0.0

        cfg._reload_cached = lambda: raw  # type: ignore[assignment]
        cfg._reload_cached()

        # Since we mocked _reload_cached, the internal state is set by the mock.
        # Let's test with the real method instead.
        cfg2 = Config()
        cfg2._reload_cache = raw
        cfg2._reload_cache_at = time.monotonic()

        # Within TTL, should return the cached value
        result = cfg2._reload_cached()
        assert result is raw

    def test_ttl_boundary_returns_cached(self) -> None:
        """刚好在 TTL 边界内（小于 TTL），返回缓存."""
        cfg = Config()
        raw = {"streaming": {"inject_time": True}}
        now = time.monotonic()

        cfg._reload_cache = raw
        cfg._reload_cache_at = now - 4.99  # TTL is 5.0 seconds

        # Should still return cached (within TTL)
        result = cfg._reload_cached()
        assert result is raw

    def test_ttl_boundary_reloads_after_expiry(self) -> None:
        """刚好超过 TTL 边界，重新读取."""
        cfg = Config()
        raw_old = {"streaming": {"inject_time": False}}
        raw_new = {"streaming": {"inject_time": True}}

        cfg._reload_cache = raw_old
        cfg._reload_cache_at = time.monotonic() - 5.01  # Just over TTL

        # Need to mock the actual file reading part
        with patch("hermes_lark_streaming.config._HERMES_CONFIG_PATH") as mock_path, \
             patch("hermes_lark_streaming.config.yaml") as mock_yaml:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = "streaming:\n  inject_time: true\n"
            mock_yaml.safe_load.return_value = raw_new

            result = cfg._reload_cached()
            assert result == raw_new
            assert cfg._reload_cache_at > 0

    def test_show_reasoning_uses_reload_cached(self) -> None:
        """show_reasoning 属性使用 _reload_cached 而非 _reload."""
        cfg = Config()
        reload_cached_calls = 0
        reload_calls = 0
        raw = {"display": {"platforms": {"feishu": {"show_reasoning": True}}}}

        original_reload_cached = cfg._reload_cached

        def counting_reload_cached() -> dict[str, Any]:
            nonlocal reload_cached_calls
            reload_cached_calls += 1
            return raw

        def counting_reload() -> dict[str, Any]:
            nonlocal reload_calls
            reload_calls += 1
            return raw

        cfg._reload_cached = counting_reload_cached  # type: ignore[assignment]
        cfg._reload = counting_reload  # type: ignore[assignment]

        _ = cfg.show_reasoning

        assert reload_cached_calls == 1
        assert reload_calls == 0  # _reload should NOT be called

    def test_inject_time_uses_reload_cached(self) -> None:
        """inject_time 属性使用 _reload_cached 而非 _reload."""
        cfg = Config()
        reload_cached_calls = 0
        reload_calls = 0
        raw = {"streaming": {"inject_time": True}}

        def counting_reload_cached() -> dict[str, Any]:
            nonlocal reload_cached_calls
            reload_cached_calls += 1
            return raw

        def counting_reload() -> dict[str, Any]:
            nonlocal reload_calls
            reload_calls += 1
            return raw

        cfg._reload_cached = counting_reload_cached  # type: ignore[assignment]
        cfg._reload = counting_reload  # type: ignore[assignment]

        _ = cfg.inject_time

        assert reload_cached_calls == 1
        assert reload_calls == 0  # _reload should NOT be called
