"""Monitoring metrics + Feishu card command.

v1.1.0: Monitoring via /monitor command instead of HTTP server.
Users send "/monitor" in Feishu, plugin intercepts it (via
pre_gateway_dispatch hook) and replies with a metrics card.

This avoids running a background HTTP server — zero memory overhead
when not in use, and users get metrics in Feishu where they already are.

Metrics are collected globally throughout the plugin's lifetime:
  - record_card_created/completed/failed/aborted
  - record_api_call/error
  - record_full_rebuild
  - set_active_sessions
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


# ── Monitor card builder ──

def build_monitor_card() -> dict[str, Any]:
    """Build a Feishu CardKit v2.0 card showing current metrics.

    Returns a card dict suitable for FeishuClient.send_card_to_chat().
    """
    m = get_metrics()

    # ── Build metric items as a grid of cards ──
    def _metric_item(label: str, value: Any, color: str = "default") -> dict:
        text_color = {
            "default": "default",
            "error": "red",
            "warning": "orange",
            "success": "green",
        }.get(color, "default")
        return {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**{label}**\n{value}",
            },
            "text_color": text_color,
        }

    items = [
        _metric_item("卡片创建", m["cards_created"]),
        _metric_item("已完成", m["cards_completed"], "success"),
        _metric_item("失败", m["cards_failed"], "error"),
        _metric_item("已停止", m["cards_aborted"]),
        _metric_item("API 调用", m["api_calls"]),
        _metric_item("API 错误", m["api_errors"], "error" if m["api_errors"] > 0 else "default"),
        _metric_item("流式调用", m["stream_element_calls"]),
        _metric_item("流式失败", m["stream_element_failures"], "error" if m["stream_element_failures"] > 0 else "default"),
        _metric_item("批量更新", m["batch_update_calls"]),
        _metric_item("全卡重建", m["full_rebuilds"], "warning" if m["full_rebuilds"] > 0 else "default"),
        _metric_item("活跃会话", m["active_sessions"]),
        _metric_item("运行时间", m["uptime_human"]),
    ]

    # ── Error code breakdown (if any) ──
    error_elements: list[dict] = []
    if m["error_codes"]:
        error_lines = []
        for code, count in sorted(m["error_codes"].items()):
            error_lines.append(f"  • 错误码 `{code}`: {count} 次")
        error_elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "**错误码分布**\n" + "\n".join(error_lines),
            },
            "text_color": "orange",
        })

    # ── Build card ──
    card = {
        "schema": "2.0",
        "config": {
            "update_multi": True,
        },
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "📊 插件监控面板",
            },
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
                    "tag": "column_set",
                    "flex_mode": "none",
                    "background_style": "default",
                    "columns": [
                        {
                            "tag": "column",
                            "elements": [items[i]],
                            "width": "weighted",
                            "weight": 1,
                        }
                        for i in range(0, min(4, len(items)))
                    ],
                },
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "background_style": "default",
                    "columns": [
                        {
                            "tag": "column",
                            "elements": [items[i]],
                            "width": "weighted",
                            "weight": 1,
                        }
                        for i in range(4, min(8, len(items)))
                    ],
                },
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "background_style": "default",
                    "columns": [
                        {
                            "tag": "column",
                            "elements": [items[i]],
                            "width": "weighted",
                            "weight": 1,
                        }
                        for i in range(8, min(12, len(items)))
                    ],
                },
                *error_elements,
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": f"数据更新时间: {time.strftime('%Y-%m-%d %H:%M:%S')}  |  发送 /monitor 刷新",
                        }
                    ],
                },
            ],
        },
    }
    return card


def _get_version() -> str:
    """Get plugin version from plugin.yaml."""
    try:
        from pathlib import Path
        import os
        yaml_path = Path(__file__).resolve().parent.parent / "plugin.yaml"
        if yaml_path.exists():
            for line in yaml_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "unknown"


# ── pre_gateway_dispatch hook handler ──

# /aowen 是插件的命令前缀，所有 /aowen 开头的命令都由此插件处理
# 不经过 Hermes agent，直接返回卡片
# 当前支持的子命令：
#   /aowen monitor  — 显示监控面板卡片
#   /aowen help     — 显示可用命令列表
_AOWEN_COMMANDS = {
    "monitor": "显示插件监控面板（卡片创建数、API 调用数、错误码分布等）",
    "help": "显示 /aowen 系列命令列表",
}


def handle_pre_gateway_dispatch(event: Any, gateway: Any = None, **kwargs) -> dict | None:
    """Handle /aowen commands — intercept and reply with cards.

    This is registered as a pre_gateway_dispatch hook callback. When the
    user sends "/aowen <subcommand>" in Feishu, this function:
    1. Detects the /aowen prefix
    2. Routes to the appropriate subcommand handler
    3. Sends a card to the chat
    4. Returns {"action": "skip"} to prevent the message from reaching the agent

    Returns None for non-/aowen messages (normal dispatch continues).

    Supported commands:
      /aowen monitor  — show metrics card
      /aowen help     — show available commands
      /aowen          — same as /aowen help
    """
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

        # Parse subcommand
        # /aowen          → help
        # /aowen monitor  → monitor
        # /aowen help     → help
        parts = text_stripped.split(None, 1)  # split into ["/aowen", "subcommand args"]
        subcommand = parts[1].strip().lower() if len(parts) > 1 else "help"
        # Take only the first word of subcommand (ignore extra args)
        subcommand = subcommand.split()[0] if subcommand else "help"

        _logger.info("HLS: /aowen %s command detected, chat=%s", subcommand, chat_id[:12])

        # Route to subcommand handler
        if subcommand == "monitor":
            return _handle_monitor_command(chat_id)
        elif subcommand == "help" or subcommand == "":
            return _handle_help_command(chat_id)
        else:
            return _handle_unknown_command(chat_id, subcommand)

    except Exception:
        _logger.debug("HLS: /aowen handler error", exc_info=True)
        return None


def _handle_monitor_command(chat_id: str) -> dict:
    """Handle /aowen monitor — send metrics card."""
    import asyncio
    from ..controller import get_controller

    ctrl = get_controller()
    if not ctrl.enabled or not ctrl._client_ok():
        _logger.warning("HLS: /aowen monitor but controller not ready")
        return {"action": "skip", "reason": "monitor: controller not ready"}

    card = build_monitor_card()

    async def _send_card():
        try:
            await ctrl._client.send_card_to_chat(chat_id, card)
            _logger.info("HLS: monitor card sent to chat=%s", chat_id[:12])
        except Exception:
            _logger.error("HLS: failed to send monitor card", exc_info=True)

    loop = asyncio.get_event_loop()
    loop.create_task(_send_card())

    return {"action": "skip", "reason": "/aowen monitor handled"}


def _handle_help_command(chat_id: str) -> dict:
    """Handle /aowen help — send help card listing available commands."""
    import asyncio
    from ..controller import get_controller

    ctrl = get_controller()
    if not ctrl.enabled or not ctrl._client_ok():
        _logger.warning("HLS: /aowen help but controller not ready")
        return {"action": "skip", "reason": "help: controller not ready"}

    card = _build_help_card()

    async def _send_card():
        try:
            await ctrl._client.send_card_to_chat(chat_id, card)
            _logger.info("HLS: help card sent to chat=%s", chat_id[:12])
        except Exception:
            _logger.error("HLS: failed to send help card", exc_info=True)

    loop = asyncio.get_event_loop()
    loop.create_task(_send_card())

    return {"action": "skip", "reason": "/aowen help handled"}


def _handle_unknown_command(chat_id: str, subcommand: str) -> dict:
    """Handle unknown /aowen subcommand."""
    import asyncio
    from ..controller import get_controller

    ctrl = get_controller()
    if not ctrl.enabled or not ctrl._client_ok():
        return {"action": "skip", "reason": "unknown: controller not ready"}

    card = {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "❓ 未知命令"},
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

    async def _send_card():
        try:
            await ctrl._client.send_card_to_chat(chat_id, card)
        except Exception:
            _logger.error("HLS: failed to send unknown command card", exc_info=True)

    loop = asyncio.get_event_loop()
    loop.create_task(_send_card())

    return {"action": "skip", "reason": f"/aowen {subcommand} unknown"}


def _build_help_card() -> dict:
    """Build a help card listing all /aowen commands."""
    command_lines = []
    for cmd, desc in _AOWEN_COMMANDS.items():
        command_lines.append(f"  • `/aowen {cmd}` — {desc}")

    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📖 插件命令帮助"},
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
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "发送 /aowen <命令名> 使用对应功能",
                        }
                    ],
                },
            ],
        },
    }


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
    "handle_pre_gateway_dispatch",
]
