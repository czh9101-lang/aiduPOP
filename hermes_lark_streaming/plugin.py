"""Plugin entry point — register(ctx) for Hermes plugin system.

On registration:
- Ensures ``config.yaml`` has a clean top-level ``streaming`` section
  with the minimal required defaults so streaming cards work out of the box.
- Ensures ``hermes-lark-streaming`` is listed in ``plugins.enabled``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from hermes_cli.plugins import PluginContext

_logger = logging.getLogger("hermes_lark_streaming")

_HERMES_CONFIG_PATH = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "config.yaml"

# Default streaming config injected into config.yaml on first load
_DEFAULT_STREAMING_CONFIG: dict[str, Any] = {
    "enabled": True,
    "linear": True,
    "panel_expanded": False,
    "card_ttl_sec": 600,
    "inject_time": False,
    "footer": {
        "fields": [
            ["status", "elapsed", "model", "api_calls"],
            ["tokens", "context", "history_offset", "compression_exhausted"],
        ],
        "show_label": True,
    },
}

# Streaming section as a formatted YAML string with comments.
# Used when injecting the streaming section for the first time,
# because yaml.dump strips all comments.
_STREAMING_YAML_WITH_COMMENTS = """\
streaming:
  enabled: true              # 启用流式卡片
  linear: true               # 线性模式：单卡片原地更新，支持自动拆卡
  panel_expanded: false      # 完成态卡片中面板（工具、推理）是否保持展开
  card_ttl_sec: 600          # 卡片存活检测超时（秒）
  inject_time: false         # 在用户消息前注入当前时间（详见 README"时间注入"说明）

  footer:
    fields:
      - [status, elapsed, model, api_calls]
      - [tokens, context, history_offset, compression_exhausted]
      # 可用字段说明：
      #   status      — 回复状态（✅ 已完成 / ❌ 出错 / 🛑 已停止）
      #   elapsed     — AI 回复耗时
      #   model       — 使用的模型名称
      #   api_calls   — 本轮对话的 API 调用次数
      #   tokens      — Token 用量（↑ 输入 ↓ 输出）
      #   context     — 上下文窗口用量（已用/总量 百分比）
      #   history_offset — 对话历史偏移量；值越大对话越长，值突然变小说明发生了上下文压缩
      #   compression_exhausted — 上下文已满，即使压缩也无法适应上下文窗口时显示（⚠ 上下文已满）
      # 每个内层列表为页脚的一行，字段仅在有值时显示
    show_label: true         # 是否显示字段标签（true/false）
"""


def _prepare_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Pre-process config dict before YAML dump.

    Handles nested dicts recursively. Footer fields are kept as-is
    (2D array for row layout) so the YAML output preserves the
    visual row structure.
    """
    result: dict[str, Any] = {}
    for k, v in cfg.items():
        if isinstance(v, dict):
            result[k] = _prepare_config(v)
        else:
            result[k] = v
    return result


def _ensure_streaming_config() -> None:
    """Ensure ``config.yaml`` has a clean top-level ``streaming`` section."""
    if not _HERMES_CONFIG_PATH.exists():
        _logger.warning("config.yaml not found at %s, skipping config injection", _HERMES_CONFIG_PATH)
        return

    try:
        text = _HERMES_CONFIG_PATH.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        changed = False
        inject_streaming = False

        # Ensure streaming section exists
        if "streaming" not in raw:
            raw["streaming"] = dict(_DEFAULT_STREAMING_CONFIG)
            inject_streaming = True
            changed = True
            _logger.info("Injected top-level streaming config into %s", _HERMES_CONFIG_PATH)

        # Ensure plugins.enabled includes this plugin
        plugins = raw.get("plugins")
        if isinstance(plugins, dict):
            enabled = plugins.get("enabled")
            if isinstance(enabled, list) and "hermes-lark-streaming" not in enabled:
                enabled.append("hermes-lark-streaming")
                changed = True
                _logger.info("Added hermes-lark-streaming to plugins.enabled")

        if changed:
            if inject_streaming:
                # Write streaming section with comments, then append the rest
                # Remove streaming from raw dict so yaml.dump doesn't duplicate it
                streaming_data = raw.pop("streaming", None)
                # Dump the rest of the config (without streaming section)
                other_yaml = yaml.dump(
                    _prepare_config(raw),
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
                # Combine: streaming section with comments + other config
                output = _STREAMING_YAML_WITH_COMMENTS.rstrip("\n") + "\n\n" + other_yaml
                with open(_HERMES_CONFIG_PATH, "w", encoding="utf-8") as f:
                    f.write(output)
                # Restore streaming data to raw for any downstream use
                raw["streaming"] = streaming_data
            else:
                # Only plugins.enabled changed — dump everything normally
                prepped = _prepare_config(raw)
                with open(_HERMES_CONFIG_PATH, "w", encoding="utf-8") as f:
                    yaml.dump(prepped, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except Exception:
        _logger.exception("Failed to ensure streaming config in config.yaml")


def _cleanup_config() -> None:
    """Remove ``streaming`` section and ``plugins.enabled`` entry.

    Called via ``unregister()`` when Hermes plugin system supports it.
    """
    if not _HERMES_CONFIG_PATH.exists():
        return

    try:
        text = _HERMES_CONFIG_PATH.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        changed = False

        if "streaming" in raw:
            del raw["streaming"]
            changed = True
            _logger.info("Removed top-level streaming config from %s", _HERMES_CONFIG_PATH)

        plugins = raw.get("plugins")
        if isinstance(plugins, dict):
            enabled = plugins.get("enabled")
            if isinstance(enabled, list) and "hermes-lark-streaming" in enabled:
                enabled.remove("hermes-lark-streaming")
                changed = True
                _logger.info("Removed hermes-lark-streaming from plugins.enabled")

        if changed:
            with open(_HERMES_CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except Exception:
        _logger.exception("Failed to clean up streaming config / plugins.enabled")


def register(ctx: "PluginContext") -> None:
    """Register hermes-lark-streaming as a Hermes plugin.

    Applies runtime monkey patches to GatewayRunner, AIAgent, and
    Scheduler so that streaming CardKit v2.0 cards are sent during
    Feishu conversations — no source file modification required.
    """
    _ensure_streaming_config()

    _logger.info("hermes-lark-streaming: applying runtime patches...")
    try:
        from .monkey_patch import apply_patches

        apply_patches()
        _logger.info("hermes-lark-streaming: patches applied successfully")
    except Exception:
        _logger.exception("hermes-lark-streaming: failed to apply patches")


def unregister(ctx: "PluginContext") -> None:
    """Unregister hermes-lark-streaming.

    Cleans up the injected streaming config from config.yaml.
    """
    _cleanup_config()
    _logger.info("hermes-lark-streaming: unregistered")
