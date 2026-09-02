"""aiduPOP v2.3.0 Visual Studio (WebUI) Unit & API Tests.

Covers the safety-critical backend behaviours added in the v2.3.0 audit:
- deep-merge write that never drops unrelated / user-authored keys
- refusal to write when existing config cannot be parsed (credential safety)
- server-side payload validation (structure + emoji-only icon values)
- preset integrity and static asset presence
- loopback-only Host gate

v2.3.1 additions:
- text_sizes values validated against the runtime CardKit 2.0 whitelist
  (regression guard: the UI default "normal_v2" is NOT a valid value and
  used to corrupt card creation when saved)
- backup rotation keeps only the newest 20 copies
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from hermes_lark_streaming.cardkit.theme import BUBBLE_WAVE
from hermes_lark_streaming.studio.server import (
    PRESETS,
    ConfigReadError,
    StudioRequestHandler,
    _is_emoji_value,
    _merge_ui_plugin_section,
    _validate_ui_payload,
    read_full_config,
    write_plugin_config,
)


@pytest.fixture
def temp_hermes_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """隔离测试环境的 HERMES_HOME."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg_file = tmp_path / "config.yaml"
    initial_yaml = {
        "feishu": {"app_id": "cli_test", "app_secret": "sec_test"},
        "hermes_lark_streaming": {
            "enabled": True,
            "print_strategy": "delay",
            "max_reasoning_rounds": 30,
            "theme": {
                "round_icon": "🌊",
                "reactions": {"🙆🏻‍♀️": "Done"},
            },
        },
    }
    cfg_file.write_text(yaml.safe_dump(initial_yaml, allow_unicode=True), encoding="utf-8")
    return tmp_path


class TestStudioPresets:
    def test_presets_structure(self) -> None:
        assert "bubble_wave" in PRESETS
        assert "classic_workflow" in PRESETS
        assert "cyber_minimal" in PRESETS
        bw = PRESETS["bubble_wave"]["theme"]
        assert bw["round_icon"] == "🌊"
        assert bw["collapse_icon"] == "💦"
        assert bw["tool_icons"]["exec"] == "👩🏻‍💻"


class TestStudioConfigIO:
    def test_read_full_config(self, temp_hermes_home: Path) -> None:
        cfg = read_full_config()
        assert cfg["feishu"]["app_id"] == "cli_test"
        assert cfg["hermes_lark_streaming"]["enabled"] is True

    def test_read_missing_file_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))  # no config.yaml written
        assert read_full_config() == {}

    def test_write_preserves_credentials_and_unrelated_keys(
        self, temp_hermes_home: Path
    ) -> None:
        ok, _ = write_plugin_config(
            {
                "enabled": True,
                "theme": {"round_icon": "🌸", "tool_icons": {"skill": "🎁"}},
            }
        )
        assert ok is True

        updated = read_full_config()
        plugin = updated["hermes_lark_streaming"]
        # credentials survive
        assert updated["feishu"]["app_id"] == "cli_test"
        # UI change applied
        assert plugin["theme"]["round_icon"] == "🌸"
        assert plugin["theme"]["tool_icons"]["skill"] == "🎁"
        # unrelated plugin keys survive
        assert plugin["print_strategy"] == "delay"
        assert plugin["max_reasoning_rounds"] == 30
        # user-authored sibling theme key survives
        assert plugin["theme"]["reactions"] == {"🙆🏻‍♀️": "Done"}

    def test_write_creates_backup(self, temp_hermes_home: Path) -> None:
        ok, _ = write_plugin_config({"enabled": True})
        assert ok is True
        backup_dir = temp_hermes_home / "backups" / "studio_config_baks"
        assert backup_dir.exists()
        assert len(list(backup_dir.glob("config.yaml.bak_*"))) >= 1

    def test_backup_rotation_keeps_newest_20(self, temp_hermes_home: Path) -> None:
        # v2.3.1: 预置 25 份旧备份，再保存一次 → 轮转后只剩最近 20 份
        backup_dir = temp_hermes_home / "backups" / "studio_config_baks"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for i in range(25):
            old = backup_dir / f"config.yaml.bak_{1000 + i}"
            old.write_text("old: true", encoding="utf-8")
            os.utime(old, (1000 + i, 1000 + i))

        ok, _ = write_plugin_config({"enabled": True})
        assert ok is True
        remaining = list(backup_dir.glob("config.yaml.bak_*"))
        assert len(remaining) == 20
        # 最新的一份是本次写入生成的（时间戳最大）
        assert max(int(p.name.rsplit("_", 1)[1]) for p in remaining) > 1024

    def test_refuses_write_when_config_unparseable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        cfg_file = tmp_path / "config.yaml"
        # malformed YAML with live-looking credentials
        cfg_file.write_text("feishu: {app_id: 'x'\n  bad: [unbalanced", encoding="utf-8")
        before = cfg_file.read_text(encoding="utf-8")

        with pytest.raises(ConfigReadError):
            read_full_config()

        ok, msg = write_plugin_config({"enabled": True, "theme": {"round_icon": "🌸"}})
        assert ok is False
        assert "拒绝写入" in msg
        # original file untouched
        assert cfg_file.read_text(encoding="utf-8") == before


class TestStudioValidation:
    def test_emoji_value_detector(self) -> None:
        assert _is_emoji_value("🌊") is True
        assert _is_emoji_value("👩🏻‍💻") is True
        assert _is_emoji_value("tool_02") is False  # feishu token
        assert _is_emoji_value("abc") is False
        assert _is_emoji_value("") is False

    def test_rejects_ascii_token_in_tool_icons(self) -> None:
        ok, why = _validate_ui_payload({"theme": {"tool_icons": {"read": "read_outlined"}}})
        assert ok is False
        assert "tool_icons" in why

    def test_rejects_bad_footer_matrix(self) -> None:
        ok, _ = _validate_ui_payload({"footer": {"fields": ["model"]}})  # not 2-D
        assert ok is False

    def test_rejects_non_dict_theme(self) -> None:
        ok, _ = _validate_ui_payload({"theme": "oops"})
        assert ok is False

    def test_accepts_valid_payload(self) -> None:
        ok, why = _validate_ui_payload(
            {
                "theme": {"round_icon": "🌊", "tool_icons": {"read": "📖"}},
                "footer": {"show_label": True, "fields": [["model", "tokens"]]},
                "max_tool_steps": 20,
            }
        )
        assert ok is True
        assert why == ""

    def test_write_rejects_invalid_payload(self, temp_hermes_home: Path) -> None:
        ok, _ = write_plugin_config({"theme": {"tool_icons": {"read": "not_emoji"}}})
        assert ok is False

    def test_rejects_invalid_text_size_value(self) -> None:
        # v2.3.1 回归守卫：normal_v2 是历史直写默认，不在 CardKit 2.0 白名单内，
        # 写入后会让网关建卡抛 ValueError（所有消息失去流式卡片）——必须拒写。
        ok, why = _validate_ui_payload({"text_sizes": {"body": "normal_v2"}})
        assert ok is False
        assert "text_sizes.body" in why

    def test_rejects_non_string_text_size(self) -> None:
        ok, _ = _validate_ui_payload({"text_sizes": {"panel": 12}})
        assert ok is False

    def test_accepts_valid_text_sizes(self) -> None:
        ok, why = _validate_ui_payload(
            {"text_sizes": {"body": "large", "panel": "notation", "notice": "small"}}
        )
        assert ok is True
        assert why == ""

    def test_write_rejects_invalid_text_sizes(
        self, temp_hermes_home: Path
    ) -> None:
        before = (temp_hermes_home / "config.yaml").read_text(encoding="utf-8")
        ok, msg = write_plugin_config({"text_sizes": {"body": "normal_v2"}})
        assert ok is False
        assert "text_sizes" in msg
        # 配置文件原样未动
        assert (temp_hermes_home / "config.yaml").read_text(encoding="utf-8") == before


class TestStudioMerge:
    def test_merge_only_touches_ui_keys(self) -> None:
        existing = {
            "print_strategy": "fast",
            "secret_flag": 123,
            "theme": {"reactions": {"a": "b"}, "round_icon": "old"},
        }
        merged = _merge_ui_plugin_section(existing, {"theme": {"round_icon": "🌸"}})
        assert merged["print_strategy"] == "fast"
        assert merged["secret_flag"] == 123
        assert merged["theme"]["reactions"] == {"a": "b"}
        assert merged["theme"]["round_icon"] == "🌸"


class TestStudioSecurity:
    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("127.0.0.1:8765", True),
            ("localhost:8765", True),
            ("[::1]:8765", True),
            ("", True),
            ("evil.example.com", False),
            ("192.168.1.10:8765", False),
        ],
    )
    def test_host_gate(self, host: str, expected: bool) -> None:
        handler = StudioRequestHandler.__new__(StudioRequestHandler)
        handler.headers = {"Host": host}
        assert handler._host_is_local() is expected


class TestStudioStaticFiles:
    def test_web_assets_exist(self) -> None:
        web_dir = Path(__file__).resolve().parent.parent / "studio" / "web"
        assert (web_dir / "index.html").is_file()
        assert (web_dir / "css" / "style.css").is_file()
        assert (web_dir / "js" / "app.js").is_file()
        assert (web_dir / "js" / "card_preview.js").is_file()
        assert (web_dir / "js" / "emoji_data.js").is_file()
        assert (web_dir / "js" / "hexbg.js").is_file()

    def test_html_contains_brand_elements(self) -> None:
        web_dir = Path(__file__).resolve().parent.parent / "studio" / "web"
        html = (web_dir / "index.html").read_text(encoding="utf-8")
        # v2.4.1：徽章版本由 /api/health 运行时注入（单一真相源），
        # 静态 html 里断言占位元素存在，不再锁版本字面量。
        for token in ("aiduPOP", "爱嘟波泡卡", "badgeVersion", "monkey²",
                      "爱嘟", "唯吾", "智助", "aidu", "I do", "AI do", 'id="hexBg"'):
            assert token in html

    def test_no_header_config_remains(self) -> None:
        web_dir = Path(__file__).resolve().parent.parent / "studio" / "web"
        html = (web_dir / "index.html").read_text(encoding="utf-8")
        assert "标题栏 Header" not in html
        app_js = (web_dir / "js" / "app.js").read_text(encoding="utf-8")
        assert "theme_header_icon" not in app_js
