"""Monitoring dashboard — lightweight HTTP server for plugin metrics.

v1.1.0 (Task 3.7): Provides real-time visibility into plugin health.

Endpoints:
  GET /          — Simple HTML dashboard (auto-refresh every 5s)
  GET /metrics   — JSON metrics (scrape-friendly)
  GET /health    — Simple health check (200 OK if running)

Configuration (in config.yaml):
  hermes_lark_streaming:
    monitor:
      enabled: false      # default off
      port: 9191          # metrics server port
      host: "127.0.0.1"   # bind address (use 0.0.0.0 for external access)

The server runs in a background asyncio task within the Hermes gateway
process. It shares the event loop with the plugin's card operations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ..config import Config

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


# ── HTTP server ──

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>hermes-lark-streaming Monitor</title>
<style>
body {{ font-family: -apple-system, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; }}
.container {{ max-width: 800px; margin: 0 auto; }}
h1 {{ color: #333; font-size: 24px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin-top: 20px; }}
.card {{ background: white; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.card .label {{ color: #666; font-size: 12px; text-transform: uppercase; }}
.card .value {{ color: #333; font-size: 28px; font-weight: bold; margin-top: 4px; }}
.card .sub {{ color: #999; font-size: 12px; margin-top: 4px; }}
.errors {{ background: #fff3e0; }}
.errors .value {{ color: #e65100; }}
.footer {{ margin-top: 20px; color: #999; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<div class="container">
<h1>hermes-lark-streaming Monitor</h1>
<div class="grid">
  <div class="card"><div class="label">Cards Created</div><div class="value">{cards_created}</div></div>
  <div class="card"><div class="label">Completed</div><div class="value">{cards_completed}</div></div>
  <div class="card errors"><div class="label">Failed</div><div class="value">{cards_failed}</div></div>
  <div class="card"><div class="label">Aborted</div><div class="value">{cards_aborted}</div></div>
  <div class="card"><div class="label">API Calls</div><div class="value">{api_calls}</div></div>
  <div class="card errors"><div class="label">API Errors</div><div class="value">{api_errors}</div></div>
  <div class="card"><div class="label">Stream Calls</div><div class="value">{stream_element_calls}</div><div class="sub">Failures: {stream_element_failures}</div></div>
  <div class="card"><div class="label">Batch Updates</div><div class="value">{batch_update_calls}</div></div>
  <div class="card errors"><div class="label">Full Rebuilds</div><div class="value">{full_rebuilds}</div></div>
  <div class="card"><div class="label">Active Sessions</div><div class="value">{active_sessions}</div></div>
  <div class="card"><div class="label">Uptime</div><div class="value" style="font-size:20px">{uptime_human}</div></div>
</div>
{error_codes_html}
<div class="footer">Auto-refresh every 5s · <a href="/metrics">JSON Metrics</a> · <a href="/health">Health</a></div>
</div>
</body>
</html>"""


async def _handle_root(request: Any) -> Any:
    """Serve the HTML dashboard."""
    m = get_metrics()
    error_codes_html = ""
    if m["error_codes"]:
        items = "".join(
            f"<div class='card'><div class='label'>Error {code}</div><div class='value'>{count}</div></div>"
            for code, count in sorted(m["error_codes"].items())
        )
        error_codes_html = f"<h2 style='margin-top:20px;color:#e65100'>Error Codes</h2><div class='grid'>{items}</div>"

    html = _HTML_TEMPLATE.format(
        cards_created=m["cards_created"],
        cards_completed=m["cards_completed"],
        cards_failed=m["cards_failed"],
        cards_aborted=m["cards_aborted"],
        api_calls=m["api_calls"],
        api_errors=m["api_errors"],
        stream_element_calls=m["stream_element_calls"],
        stream_element_failures=m["stream_element_failures"],
        batch_update_calls=m["batch_update_calls"],
        full_rebuilds=m["full_rebuilds"],
        active_sessions=m["active_sessions"],
        uptime_human=m["uptime_human"],
        error_codes_html=error_codes_html,
    )
    from aiohttp import web
    return web.Response(text=html, content_type="text/html")


async def _handle_metrics(request: Any) -> Any:
    """Serve JSON metrics."""
    from aiohttp import web
    return web.json_response(get_metrics())


async def _handle_health(request: Any) -> Any:
    """Simple health check."""
    from aiohttp import web
    return web.Response(text="OK", status=200)


# ── Server lifecycle ──

_server_task: asyncio.Task | None = None
_app: Any = None


async def start_monitor_server(config: Config) -> None:
    """Start the monitor HTTP server if enabled in config.

    Called from plugin.register() after patches are applied.
    """
    global _server_task, _app

    sec = config._plugin_sec()
    monitor_cfg = sec.get("monitor", {})
    if not isinstance(monitor_cfg, dict):
        monitor_cfg = {}

    if not monitor_cfg.get("enabled", False):
        _logger.debug("HLS: monitor disabled in config")
        return

    port = int(monitor_cfg.get("port", 9191))
    host = str(monitor_cfg.get("host", "127.0.0.1"))

    try:
        from aiohttp import web
    except ImportError:
        _logger.warning("HLS: aiohttp not available, monitor server disabled")
        return

    _app = web.Application()
    _app.router.add_get("/", _handle_root)
    _app.router.add_get("/metrics", _handle_metrics)
    _app.router.add_get("/health", _handle_health)

    async def _run():
        runner = web.AppRunner(_app)
        await runner.setup()
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
        _logger.info("HLS: monitor server started on %s:%d", host, port)
        # Keep running until cancelled
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await runner.cleanup()

    _server_task = asyncio.get_event_loop().create_task(_run())


async def stop_monitor_server() -> None:
    """Stop the monitor server."""
    global _server_task
    if _server_task is not None:
        _server_task.cancel()
        try:
            await _server_task
        except asyncio.CancelledError:
            pass
        _server_task = None
        _logger.info("HLS: monitor server stopped")


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
    "start_monitor_server",
    "stop_monitor_server",
]
