"""Monitoring metrics + /aowen command system.

v1.1.0: Monitoring via /aowen commands in Feishu.
Users send "/aowen <command>" and the plugin replies with a card
directly (bypassing Hermes AI via pre_gateway_dispatch hook).

Commands:
  /aowen help           — show available commands
  /aowen status         — plugin status + config (in collapsible panel)
  /aowen monitor        — metrics dashboard
  /aowen monitor reset  — reset metrics counters
  /aowen                — same as /aowen help

All card headers are plain text without emoji (per user requirement).
All cards use only v2-compatible tags: div, lark_md, plain_text, hr,
collapsible_panel.
"""

from __future__ import annotations

import logging
import time
from typing import Any

_logger = logging.getLogger("hermes_lark_streaming")

# ── Metrics collector (global singleton) ──

_metrics: dict[str, Any] = {
    "cards_created": 0,
    "cards_completed": 0,
    "cards_failed": 0,
    "cards_aborted": 0,
    "api_calls": 0,
    "api_errors": 0,
    "stream_element_calls": 0,
    "stream_element_failures": 0,
    "batch_update_calls": 0,
    "full_rebuilds": 0,
    "active_sessions": 0,
    "started_at": time.time(),
}

_error_codes: dict[int, int] = {}  # error_code → count


def record_card_created() -> None:
    _metrics["cards_created"] += 1

def record_card_completed() -> None:
    _metrics["cards_completed"] += 1

def record_card_failed() -> None:
    _metrics["cards_failed"] += 1

def record_card_aborted() -> None:
    _metrics["cards_aborted"] += 1

def record_api_call(operation: str) -> None:
    _metrics["api_calls"] += 1
    if operation == "cardkit_stream_element":
        _metrics["stream_element_calls"] += 1
    elif operation == "cardkit_batch_update":
        _metrics["batch_update_calls"] += 1

def record_api_error(code: int, operation: str = "") -> None:
    _metrics["api_errors"] += 1
    _error_codes[code] = _error_codes.get(code, 0) + 1
    if operation == "cardkit_stream_element":
        _metrics["stream_element_failures"] += 1

def record_full_rebuild() -> None:
    _metrics["full_rebuilds"] += 1

def set_active_sessions(count: int) -> None:
    _metrics["active_sessions"] = count

def get_metrics() -> dict[str, Any]:
    """Get current metrics snapshot."""
    uptime = time.time() - _metrics["started_at"]
    return {
        **_metrics,
        "uptime_seconds": round(uptime, 1),
        "uptime_human": _format_uptime(uptime),
        "error_codes": dict(_error_codes),
    }


def _format_uptime(seconds: float) -> str:
    """Format uptime as human-readable string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m {int(seconds % 60)}s"
    hours = int(minutes // 60)
    return f"{hours}h {minutes % 60}m"


def _get_version() -> str:
    """Get plugin version from plugin.yaml."""
    try:
        from pathlib import Path
        yaml_path = Path(__file__).resolve().parent.parent / "plugin.yaml"
        if yaml_path.exists():
            for line in yaml_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "unknown"


# ── Card builders ──

def build_monitor_card() -> dict[str, Any]:
    """Build monitor metrics card (v2-safe: div + lark_md + hr only)."""
    m = get_metrics()

    def _fmt(label: str, value: Any, color: str = "default") -> str:
        color_map = {
            "default": None,
            "error": "red",
            "warning": "orange",
            "success": "green",
        }
        font_color = color_map.get(color)
        if font_color:
            return f"  • **{label}**: <font color='{font_color}'>{value}</font>"
        return f"  • **{label}**: {value}"

    metrics_lines = [
        _fmt("卡片创建", m["cards_created"]),
        _fmt("已完成", m["cards_completed"], "success"),
        _fmt("失败", m["cards_failed"], "error"),
        _fmt("已停止", m["cards_aborted"]),
        _fmt("API 调用", m["api_calls"]),
        _fmt("API 错误", m["api_errors"], "error" if m["api_errors"] > 0 else "default"),
        _fmt("流式调用", m["stream_element_calls"]),
        _fmt("流式失败", m["stream_element_failures"], "error" if m["stream_element_failures"] > 0 else "default"),
        _fmt("批量更新", m["batch_update_calls"]),
        _fmt("全卡重建", m["full_rebuilds"], "warning" if m["full_rebuilds"] > 0 else "default"),
        _fmt("活跃会话", m["active_sessions"]),
        _fmt("运行时间", m["uptime_human"]),
    ]

    if m["error_codes"]:
        error_lines = [f"  • 错误码 `{code}`: {count} 次" for code, count in sorted(m["error_codes"].items())]
        metrics_lines.append("")
        metrics_lines.append("<font color='orange'>**错误码分布**</font>")
        metrics_lines.extend(error_lines)

    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "插件监控面板"},
            "template": "blue",
        },
        "body": {
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**版本**: v{_get_version()}  |  **运行时间**: {m['uptime_human']}",
                    },
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "\n".join(metrics_lines),
                    },
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "plain_text",
                        "content": f"数据更新时间: {time.strftime('%Y-%m-%d %H:%M:%S')}  |  发送 /aowen monitor 刷新  |  发送 /aowen monitor reset 重置",
                    },
                },
            ],
        },
    }


def build_status_card() -> dict[str, Any]:
    """Build status card with config in collapsible panel (v2-safe)."""
    try:
        from ..controller import get_controller
        from ..patching import _patch_status
        from ..config import Config

        ctrl = get_controller()
        cfg = Config()

        # ── Status section ──
        patch_lines = []
        if _patch_status:
            for key, val in _patch_status.items():
                if key in ("version", "hermes_layout"):
                    continue
                icon = "[OK]" if val in ("✓", "applied") else ("[!]" if "pending" in str(val) else "[X]")
                patch_lines.append(f"  {icon} `{key}`: {val}")
        else:
            patch_lines.append("  [!] 补丁状态不可用（网关未启动或补丁未应用）")

        ctrl_ready = ctrl.enabled and ctrl._client_ok()
        creds_status = "已就绪" if ctrl_ready else "未就绪"
        active_count = sum(1 for s in ctrl._sessions.values() if not s.is_terminal_phase)

        from .. import __version__ as plugin_version

        status_text = (
            f"**版本**: v{plugin_version}\n"
            f"**飞书客户端**: {creds_status}\n"
            f"**活跃会话**: {active_count}\n\n"
            f"**补丁应用状态**:\n" + "\n".join(patch_lines)
        )

        # ── Config section (for collapsible panel) ──
        config_lines = [
            f"  • **enabled**: `{cfg.enabled}`",
            f"  • **linear**: `{cfg.linear}`",
            f"  • **gateway_cards**: `{cfg.gateway_cards}`",
            f"  • **inject_time**: `{cfg.inject_time}`",
            f"  • **flush_interval_ms**: `{cfg.flush_interval_ms}`",
            f"  • **card_ttl_sec**: `{cfg.card_duration_sec}`",
            f"  • **print_strategy**: `{cfg.print_strategy}`",
            f"  • **panel_expanded**: `{cfg.panel_expanded}`",
            f"  • **streaming_panel_expanded**: `{cfg.streaming_panel_expanded}`",
            f"  • **show_reasoning**: `{cfg.show_reasoning}`",
            f"  • **max_tool_steps**: `{cfg.max_tool_steps}`",
            f"  • **max_reasoning_rounds**: `{cfg.max_reasoning_rounds}`",
            f"  • **footer_show_label**: `{cfg.footer_show_label}`",
            f"  • **footer_fields**: `{cfg.footer_fields}`",
        ]
        has_creds = bool(cfg.feishu_app_id or cfg.env_app_id)
        config_lines.append(f"  • **feishu_credentials**: `{'已配置' if has_creds else '未配置'}`")

        config_text = "当前生效配置（修改 config.yaml 后最多 60 秒自动生效）：\n\n" + "\n".join(config_lines)

        # ── Build card with collapsible panel for config ──
        return {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {
                "title": {"tag": "plain_text", "content": "插件状态"},
                "template": "green" if ctrl_ready else "red",
            },
            "body": {
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": status_text},
                    },
                    {"tag": "hr"},
                    {
                        "tag": "collapsible_panel",
                        "expanded": False,
                        "header": {
                            "title": {"tag": "plain_text", "content": "当前配置（点击展开）"},
                        },
                        "elements": [
                            {
                                "tag": "div",
                                "text": {"tag": "lark_md", "content": config_text},
                            },
                        ],
                    },
                ],
            },
        }
    except Exception:
        _logger.error("HLS: build_status_card error", exc_info=True)
        return {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": "插件状态"}, "template": "red"},
            "body": {"elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "状态卡片构建失败，请查看日志"}}]},
        }


def build_help_card() -> dict[str, Any]:
    """Build help card listing all /aowen commands."""
    commands = [
        ("`/aowen help`", "显示本帮助信息"),
        ("`/aowen status`", "查看插件状态 + 当前配置（折叠面板）"),
        ("`/aowen monitor`", "查看监控面板（卡片创建数、API 调用数等）"),
        ("`/aowen monitor reset`", "重置监控统计计数器"),
        ("`/aowen`", "同 `/aowen help`"),
    ]
    command_lines = [f"  • {cmd} — {desc}" for cmd, desc in commands]

    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "插件命令帮助"},
            "template": "blue",
        },
        "body": {
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**hermes-lark-streaming** v{_get_version()}\n\n所有命令以 `/aowen` 开头，不经过 Hermes AI，直接由插件处理：\n\n" + "\n".join(command_lines),
                    },
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "plain_text",
                        "content": "发送 /aowen <命令名> 使用对应功能",
                    },
                },
            ],
        },
    }


def build_reset_card() -> dict[str, Any]:
    """Build reset confirmation card."""
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "统计已重置"},
            "template": "green",
        },
        "body": {
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "统计计数器已重置。\n\n现在发送 `/aowen monitor` 查看重置后的数据。",
                    },
                },
            ],
        },
    }


# ── pre_gateway_dispatch hook handler ──

# /aowen 是插件的命令前缀，所有 /aowen 开头的命令都由此插件处理
# 不经过 Hermes agent，直接返回卡片
#
# 命令体系：
#   /aowen              — 同 help
#   /aowen help         — 显示命令列表
#   /aowen status       — 插件状态 + 配置（折叠面板）
#   /aowen monitor      — 监控面板
#   /aowen monitor reset — 重置统计


def handle_pre_gateway_dispatch(event: Any, gateway: Any = None, **kwargs) -> dict | None:
    """Handle /aowen commands — intercept and reply with cards."""
    try:
        text = getattr(event, "text", "") or ""
        text_stripped = text.strip()

        # Only handle commands starting with /aowen (case-insensitive)
        if not text_stripped.lower().startswith("/aowen"):
            return None

        # Only handle on Feishu platform
        source = getattr(event, "source", None)
        platform = getattr(getattr(source, "platform", None), "value", "")
        if platform != "feishu":
            return None

        chat_id = getattr(source, "chat_id", "") if source else ""
        if not chat_id:
            _logger.warning("HLS: /aowen command but no chat_id")
            return None

        # Parse subcommand: /aowen <subcommand> [args]
        # /aowen              → help
        # /aowen help         → help
        # /aowen status       → status
        # /aowen monitor      → monitor
        # /aowen monitor reset → monitor reset
        parts = text_stripped.split(None, 2)  # ["/aowen", "sub", "arg"]
        subcommand = parts[1].strip().lower() if len(parts) > 1 else "help"
        sub_arg = parts[2].strip().lower() if len(parts) > 2 else ""

        _logger.info("HLS: /aowen %s %s command detected, chat=%s", subcommand, sub_arg, chat_id[:12])

        # Route to subcommand handler
        if subcommand == "help" or subcommand == "":
            _send_card_async(chat_id, build_help_card(), "help")
            return _skip("/aowen help handled")

        elif subcommand == "status":
            _send_card_async(chat_id, build_status_card(), "status")
            return _skip("/aowen status handled")

        elif subcommand == "monitor":
            if sub_arg == "reset":
                # /aowen monitor reset
                _do_reset()
                _send_card_async(chat_id, build_reset_card(), "reset")
                return _skip("/aowen monitor reset handled")
            else:
                # /aowen monitor
                _send_card_async(chat_id, build_monitor_card(), "monitor")
                return _skip("/aowen monitor handled")

        else:
            # Unknown command
            card = {
                "schema": "2.0",
                "config": {"update_multi": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "未知命令"},
                    "template": "orange",
                },
                "body": {
                    "elements": [
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": f"未知命令: `/aowen {subcommand}`\n\n发送 `/aowen help` 查看可用命令列表",
                            },
                        },
                    ],
                },
            }
            _send_card_async(chat_id, card, "unknown")
            return _skip(f"/aowen {subcommand} unknown")

    except Exception:
        _logger.debug("HLS: /aowen handler error", exc_info=True)
        return None


def _do_reset() -> None:
    """Reset metrics counters."""
    global _metrics, _error_codes

    old_created = _metrics["cards_created"]
    old_errors = _metrics["api_errors"]

    _metrics = {
        "cards_created": 0,
        "cards_completed": 0,
        "cards_failed": 0,
        "cards_aborted": 0,
        "api_calls": 0,
        "api_errors": 0,
        "stream_element_calls": 0,
        "stream_element_failures": 0,
        "batch_update_calls": 0,
        "full_rebuilds": 0,
        "active_sessions": _metrics["active_sessions"],
        "started_at": time.time(),
    }
    _error_codes.clear()

    _logger.info("HLS: metrics reset (was: created=%d, errors=%d)", old_created, old_errors)


def _send_card_async(chat_id: str, card: dict, cmd_name: str) -> None:
    """Send a card to chat asynchronously (fire-and-forget)."""
    import asyncio
    from ..controller import get_controller

    ctrl = get_controller()
    if not ctrl.enabled or not ctrl._client_ok():
        _logger.warning("HLS: /aowen %s but controller not ready", cmd_name)
        return

    async def _send():
        try:
            await ctrl._client.send_card_to_chat(chat_id, card)
            _logger.info("HLS: %s card sent to chat=%s", cmd_name, chat_id[:12])
        except Exception:
            _logger.error("HLS: failed to send %s card", cmd_name, exc_info=True)

    loop = asyncio.get_event_loop()
    loop.create_task(_send())


def _skip(reason: str) -> dict:
    return {"action": "skip", "reason": reason}


__all__ = [
    "record_card_created",
    "record_card_completed",
    "record_card_failed",
    "record_card_aborted",
    "record_api_call",
    "record_api_error",
    "record_full_rebuild",
    "set_active_sessions",
    "get_metrics",
    "build_monitor_card",
    "build_status_card",
    "build_help_card",
    "handle_pre_gateway_dispatch",
]
