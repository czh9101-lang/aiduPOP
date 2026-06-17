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

    Uses only div + lark_md + hr tags — all confirmed v2-compatible.
    No column_set/note/text_color (these have v2 compatibility issues).
    """
    m = get_metrics()

    # ── Build metrics text ──
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

    # ── Error code breakdown (if any) ──
    if m["error_codes"]:
        error_lines = [f"  • 错误码 `{code}`: {count} 次" for code, count in sorted(m["error_codes"].items())]
        metrics_lines.append("")
        metrics_lines.append("<font color='orange'>**错误码分布**</font>")
        metrics_lines.extend(error_lines)

    # ── Build card — pure div + lark_md, v2-safe ──
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
                        "content": f"数据更新时间: {time.strftime('%Y-%m-%d %H:%M:%S')}  |  发送 /aowen monitor 刷新",
                    },
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
#
# 当前支持的子命令：
#   /aowen monitor  — 显示监控面板卡片
#   /aowen config   — 查看当前生效配置
#   /aowen status   — 查看插件运行状态（补丁/凭据/会话）
#   /aowen reset    — 重置统计计数器
#   /aowen logs     — 查看最近 HLS 日志
#   /aowen test     — 发测试卡片验证飞书连通性
#   /aowen help     — 显示可用命令列表
#   /aowen          — 同 help
_AOWEN_COMMANDS = {
    "monitor": "显示插件监控面板（卡片创建数、API 调用数、错误码分布等）",
    "config": "查看当前生效的插件配置",
    "status": "查看插件运行状态（补丁应用、飞书凭据、活跃会话等）",
    "reset": "重置统计计数器（方便重新观察一段时间的指标）",
    "logs": "查看最近的 HLS 插件日志（排障用，免 SSH）",
    "test": "发送一张测试卡片，验证飞书连通性",
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
      /aowen config   — show current config
      /aowen status   — show plugin status
      /aowen reset    — reset metrics counters
      /aowen logs     — show recent HLS logs
      /aowen test     — send a test card
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
        parts = text_stripped.split(None, 1)  # split into ["/aowen", "subcommand args"]
        subcommand_raw = parts[1].strip().lower() if len(parts) > 1 else "help"
        # Take only the first word of subcommand (ignore extra args)
        subcommand = subcommand_raw.split()[0] if subcommand_raw else "help"

        _logger.info("HLS: /aowen %s command detected, chat=%s", subcommand, chat_id[:12])

        # Route to subcommand handler
        if subcommand == "monitor":
            return _handle_monitor_command(chat_id)
        elif subcommand == "config":
            return _handle_config_command(chat_id)
        elif subcommand == "status":
            return _handle_status_command(chat_id)
        elif subcommand == "reset":
            return _handle_reset_command(chat_id)
        elif subcommand == "logs":
            return _handle_logs_command(chat_id)
        elif subcommand == "test":
            return _handle_test_command(chat_id)
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


def _handle_config_command(chat_id: str) -> dict:
    """Handle /aowen config — show current effective config."""
    try:
        from ..config import Config
        cfg = Config()

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

        # 飞书凭据（只显示是否配置，不显示值）
        has_creds = bool(cfg.feishu_app_id or cfg.env_app_id)
        config_lines.append(f"  • **feishu_credentials**: `{'已配置' if has_creds else '未配置'}`")

        card = {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {
                "title": {"tag": "plain_text", "content": "⚙️ 当前配置"},
                "template": "blue",
            },
            "body": {
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**hermes-lark-streaming** v{_get_version()}\n\n当前生效配置（修改 config.yaml 后最多 5 秒自动生效）：\n\n" + "\n".join(config_lines),
                        },
                    },
                ],
            },
        }
        _send_card_async(chat_id, card, "config")
        return _skip("/aowen config handled")
    except Exception:
        _logger.error("HLS: config command error", exc_info=True)
        return _skip("config: error")


def _handle_status_command(chat_id: str) -> dict:
    """Handle /aowen status — show plugin runtime status."""
    try:
        from ..controller import get_controller
        from ..patching import _patch_status

        ctrl = get_controller()

        # 补丁状态
        patch_lines = []
        if _patch_status:
            for key, val in _patch_status.items():
                if key in ("version", "hermes_layout"):
                    continue
                icon = "✅" if val in ("✓", "applied") else ("⚠️" if "pending" in str(val) else "❌")
                patch_lines.append(f"  {icon} `{key}`: {val}")
        else:
            patch_lines.append("  ⚠️ 补丁状态不可用（网关未启动或补丁未应用）")

        # 飞书凭据
        ctrl_ready = ctrl.enabled and ctrl._client_ok()
        creds_status = "✅ 已就绪" if ctrl_ready else "❌ 未就绪"

        # 活跃会话
        active_count = sum(1 for s in ctrl._sessions.values() if not s.is_terminal_phase)

        # 版本
        from .. import __version__ as plugin_version

        card = {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🔍 插件状态"},
                "template": "green" if ctrl_ready else "red",
            },
            "body": {
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**版本**: v{plugin_version}\n**飞书客户端**: {creds_status}\n**活跃会话**: {active_count}\n\n**补丁应用状态**:\n" + "\n".join(patch_lines),
                        },
                    },
                ],
            },
        }
        _send_card_async(chat_id, card, "status")
        return _skip("/aowen status handled")
    except Exception:
        _logger.error("HLS: status command error", exc_info=True)
        return _skip("status: error")


def _handle_reset_command(chat_id: str) -> dict:
    """Handle /aowen reset — reset metrics counters."""
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
        "active_sessions": _metrics["active_sessions"],  # 保留当前活跃数
        "started_at": time.time(),  # 重置运行时间起点
    }
    _error_codes.clear()

    _logger.info("HLS: metrics reset (was: created=%d, errors=%d)", old_created, old_errors)

    card = {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔄 统计已重置"},
            "template": "green",
        },
        "body": {
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"统计计数器已重置。\n\n**重置前**:\n  • 卡片创建: {old_created}\n  • API 错误: {old_errors}\n\n现在发送 `/aowen monitor` 查看重置后的数据。",
                    },
                },
            ],
        },
    }
    _send_card_async(chat_id, card, "reset")
    return _skip("/aowen reset handled")


def _handle_logs_command(chat_id: str) -> dict:
    """Handle /aowen logs — show recent HLS logs."""
    try:
        import os
        from pathlib import Path

        hermes_home = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
        log_path = Path(hermes_home) / "logs" / "agent.log"

        if not log_path.exists():
            card = {
                "schema": "2.0",
                "config": {"update_multi": True},
                "header": {"title": {"tag": "plain_text", "content": "📋 日志"}, "template": "orange"},
                "body": {"elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"日志文件不存在: `{log_path}`"}}]},
            }
            _send_card_async(chat_id, card, "logs")
            return _skip("/aowen logs handled")

        # 读取最后 200 行，过滤出 HLS 相关的
        import subprocess
        result = subprocess.run(
            ["tail", "-200", str(log_path)],
            capture_output=True, text=True, timeout=5
        )
        all_lines = result.stdout.strip().split("\n") if result.stdout else []

        # 过滤 HLS 相关日志，最多取最近 30 条
        hls_lines = [l for l in all_lines if "hermes_lark_streaming" in l or "HLS:" in l]
        recent_lines = hls_lines[-30:] if len(hls_lines) > 30 else hls_lines

        if not recent_lines:
            content = "未找到 HLS 插件日志。"
        else:
            # 截取每行前 200 字符避免过长
            truncated = [l[:200] for l in recent_lines]
            content = "```\n" + "\n".join(truncated) + "\n```"

        card = {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📋 最近 {len(recent_lines)} 条 HLS 日志"},
                "template": "blue",
            },
            "body": {
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": content},
                    },
                    {
                        "tag": "div",
                        "text": {"tag": "plain_text", "content": f"来源: {log_path} | 过滤: hermes_lark_streaming / HLS:"},
                    },
                ],
            },
        }
        _send_card_async(chat_id, card, "logs")
        return _skip("/aowen logs handled")
    except Exception:
        _logger.error("HLS: logs command error", exc_info=True)
        return _skip("logs: error")


def _handle_test_command(chat_id: str) -> dict:
    """Handle /aowen test — send a test card to verify Feishu connectivity."""
    card = {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "✅ 测试卡片"},
            "template": "green",
        },
        "body": {
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**测试卡片发送成功！**\n\n如果你看到这张卡片，说明：\n✅ 飞书凭据配置正确\n✅ FeishuClient 初始化正常\n✅ CardKit API 连通\n✅ 插件 /aowen 命令工作正常\n\n**版本**: v{_get_version()}\n**时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                    },
                },
            ],
        },
    }
    _send_card_async(chat_id, card, "test")
    return _skip("/aowen test handled")


def _handle_help_command(chat_id: str) -> dict:
    """Handle /aowen help — send help card listing available commands."""
    card = _build_help_card()
    _send_card_async(chat_id, card, "help")
    return _skip("/aowen help handled")


def _handle_unknown_command(chat_id: str, subcommand: str) -> dict:
    """Handle unknown /aowen subcommand."""
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
    _send_card_async(chat_id, card, "unknown")
    return _skip(f"/aowen {subcommand} unknown")


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
                    "tag": "div",
                    "text": {
                        "tag": "plain_text",
                        "content": "发送 /aowen <命令名> 使用对应功能",
                    },
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
