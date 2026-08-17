"""aiduPOP Studio (Visual Card Studio) — Zero-dependency HTTP Backend.

Optional visual configuration tool for open-source users. Not used by the
production gateway.

Provides:
- GET  /api/config      -> Read effective config (BUBBLE_WAVE theme merged with user overrides)
- POST /api/config      -> Deep-merge UI-managed keys into config.yaml & trigger Config().reload()
- GET  /api/presets     -> Return built-in visual presets (Bubble Wave, Classic, Minimal)
- GET  /api/health      -> Liveness/version probe
- POST /api/restart     -> Restart gateway via systemctl --user (best-effort, honestly reported)
- Static file serving from studio/web/ directory

Safety design:
- Writes only the whitelisted keys the UI manages; every other key (credentials,
  print_strategy, reactions, user-authored keys) is preserved via deep merge.
- Refuses to write when the existing config cannot be parsed, so a corrupt or
  unreadable config.yaml is never overwritten (protects feishu credentials).
- Binds to loopback only and rejects cross-origin / non-local Host requests
  (guards against CSRF / DNS-rebinding against a config-writing local service).
"""

from __future__ import annotations

import copy
import http.server
import json
import logging
import os
import re
import shutil
import socketserver
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

# Relative imports from within hermes_lark_streaming package
try:
    from ..cardkit.theme import BUBBLE_WAVE
    from ..config import Config, _get_hermes_config_path
    from ..config.reader import _TEXT_SIZE_VALUES
except (ImportError, ValueError):
    # Fallback when running directly as standalone script
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from cardkit.theme import BUBBLE_WAVE
    from config import Config, _get_hermes_config_path
    from config.reader import _TEXT_SIZE_VALUES

_logger = logging.getLogger("aidupop_studio")

# ── 预设主题库 (Presets) ──────────────────────────────────────────────
PRESETS: dict[str, dict[str, Any]] = {
    "bubble_wave": {
        "id": "bubble_wave",
        "name": "泡波样式 (Bubble Wave)",
        "desc": "出厂默认。浪潮泡泡视觉，活泼可爱",
        "theme": copy.deepcopy(BUBBLE_WAVE),
    },
    "classic_workflow": {
        "id": "classic_workflow",
        "name": "经典工作流 (Classic Workflow)",
        "desc": "沉稳干练。标准工具 Emoji 与工作流视觉",
        "theme": {
            "round_icon": "💭",
            "collapse_icon": "⚡",
            "tool_icons": {
                "skill": "🏮",
                "read": "📖",
                "write": "🪄",
                "web_search": "🔭",
                "web_fetch": "📥",
                "grep": "🔍",
                "glob": "🗂️",
                "exec": "⚡",
                "browser": "🌐",
                "agent": "🐒",
                "check": "🛡️",
                "analyze": "📊",
                "fallback": "✨",
            },
            "panel": {
                "model_prefix": "⚕",
                "rounds_icon": "💭",
                "tools_icon": "🛠️",
                "elapsed_icon": "⏱",
                "separator": " · ",
            },
            "footer": {
                "model_prefix": "⚕",
                "reasoning_icon": "💭",
            },
        },
    },
    "cyber_minimal": {
        "id": "cyber_minimal",
        "name": "极简极客 (Cyber Minimal)",
        "desc": "高冷克制。几何符号与极客质感",
        "theme": {
            "round_icon": "◈",
            "collapse_icon": "▾",
            "tool_icons": {
                "skill": "◆",
                "read": "▤",
                "write": "✎",
                "web_search": "⌕",
                "web_fetch": "↓",
                "grep": "≡",
                "glob": "⊞",
                "exec": "▶",
                "browser": "◎",
                "agent": "❖",
                "check": "✓",
                "analyze": "∿",
                "fallback": "※",
            },
            "panel": {
                "model_prefix": "M:",
                "rounds_icon": "R:",
                "tools_icon": "T:",
                "elapsed_icon": "E:",
                "separator": " | ",
            },
            "footer": {
                "model_prefix": "M:",
                "reasoning_icon": "RS:",
            },
        },
    },
}


# Feishu standard_icon tokens look like "tool_02" / "edit_outlined"; putting an
# emoji into such a token renders blank, so emoji icons must NOT be pure ASCII.
_ASCII_ONLY = re.compile(r"^[\x20-\x7e]+$")


def _is_emoji_value(value: Any) -> bool:
    """True if value is a non-empty emoji/pictographic string (not a pure-ASCII token)."""
    return isinstance(value, str) and bool(value.strip()) and _ASCII_ONLY.match(value) is None


def _validate_ui_payload(cfg: dict[str, Any]) -> tuple[bool, str]:
    """Server-side structural guard for the POST /api/config payload.

    Keeps a malformed UI request from corrupting the gateway config. Emoji
    slots are re-checked here so the server never trusts client validation.
    """
    theme = cfg.get("theme")
    if theme is not None:
        if not isinstance(theme, dict):
            return False, "theme 必须是对象"
        tool_icons = theme.get("tool_icons")
        if tool_icons is not None:
            if not isinstance(tool_icons, dict):
                return False, "theme.tool_icons 必须是对象"
            for key, val in tool_icons.items():
                if val in ("", None):
                    continue
                if not _is_emoji_value(val):
                    return False, f"theme.tool_icons.{key} 仅接受 Emoji（收到: {val!r}）"
        for icon_key in ("round_icon", "collapse_icon"):
            val = theme.get(icon_key)
            if val not in (None, "") and not _is_emoji_value(val):
                return False, f"theme.{icon_key} 仅接受 Emoji（收到: {val!r}）"
        panel = theme.get("panel")
        if panel is not None and not isinstance(panel, dict):
            return False, "theme.panel 必须是对象"
        tfooter = theme.get("footer")
        if tfooter is not None and not isinstance(tfooter, dict):
            return False, "theme.footer 必须是对象"

    footer = cfg.get("footer")
    if footer is not None:
        if not isinstance(footer, dict):
            return False, "footer 必须是对象"
        fields = footer.get("fields")
        if fields is not None:
            if not isinstance(fields, list):
                return False, "footer.fields 必须是数组"
            for row in fields:
                if not isinstance(row, list) or not all(isinstance(f, str) for f in row):
                    return False, "footer.fields 必须是二维字符串数组"

    text_sizes = cfg.get("text_sizes")
    if text_sizes is not None:
        if not isinstance(text_sizes, dict):
            return False, "text_sizes 必须是对象"
        # v2.3.1: 取值必须与运行时白名单一致（config.reader._TEXT_SIZE_VALUES）。
        # 非法值（如历史默认 normal_v2）写入后会让网关建卡抛 ValueError，
        # 导致所有消息静默失去流式卡片——必须在写入前拦截。
        for role, size in text_sizes.items():
            if not isinstance(size, str) or size not in _TEXT_SIZE_VALUES:
                return False, (
                    f"text_sizes.{role} 不是合法的 CardKit 2.0 字号（收到: {size!r}）"
                )

    for int_key in ("max_tool_steps", "flush_interval_ms", "print_step"):
        if int_key in cfg and not isinstance(cfg[int_key], (int, float)):
            return False, f"{int_key} 必须是数字"

    return True, ""


class ConfigReadError(Exception):
    """Raised when config.yaml exists but cannot be read/parsed.

    Signals that a write MUST be refused so an unreadable config (which may
    still hold live feishu credentials) is never clobbered.
    """


# Keys the Studio UI is allowed to manage. Any other key in the plugin section
# (max_reasoning_rounds, print_strategy, reactions, user-authored keys, ...) is
# preserved untouched via deep merge.
UI_MANAGED_KEYS: tuple[str, ...] = (
    "enabled",
    "linear",
    "panel_expanded",
    "streaming_panel_expanded",
    "max_tool_steps",
    "flush_interval_ms",
    "print_step",
    "theme",
    "footer",
    "text_sizes",
)

# Nested theme keys the UI manages. Sibling theme keys authored by the user
# (e.g. "reactions") survive because we merge per-key rather than replacing
# the whole "theme" mapping.
UI_MANAGED_THEME_KEYS: tuple[str, ...] = (
    "tool_icons",
    "round_icon",
    "collapse_icon",
    "panel",
    "footer",
)


def read_full_config() -> dict[str, Any]:
    """读取完整的 ~/.hermes/config.yaml.

    Returns ``{}`` only when the file genuinely does not exist. If the file
    exists but is unreadable or malformed, raises :class:`ConfigReadError` so
    callers never overwrite a config they failed to parse.
    """
    cfg_path = _get_hermes_config_path()
    if not cfg_path.exists():
        return {}
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigReadError(f"无法读取 {cfg_path}: {e}") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigReadError(f"{cfg_path} YAML 解析失败: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigReadError(f"{cfg_path} 顶层结构必须是映射，实际为 {type(data).__name__}")
    return data


def _merge_ui_plugin_section(
    existing_plugin: dict[str, Any], ui_cfg: dict[str, Any]
) -> dict[str, Any]:
    """Deep-merge UI-managed keys into the existing plugin section.

    Only keys in :data:`UI_MANAGED_KEYS` are touched; every other key is kept
    as-is. The ``theme`` mapping is merged per :data:`UI_MANAGED_THEME_KEYS`,
    preserving user-authored sibling keys such as ``reactions``.
    """
    merged = copy.deepcopy(existing_plugin) if isinstance(existing_plugin, dict) else {}

    for key in UI_MANAGED_KEYS:
        if key not in ui_cfg:
            continue
        if key == "theme":
            incoming_theme = ui_cfg.get("theme")
            if not isinstance(incoming_theme, dict):
                continue
            base_theme = merged.get("theme")
            base_theme = copy.deepcopy(base_theme) if isinstance(base_theme, dict) else {}
            for tkey in UI_MANAGED_THEME_KEYS:
                if tkey not in incoming_theme:
                    continue
                tval = incoming_theme[tkey]
                if isinstance(tval, dict) and isinstance(base_theme.get(tkey), dict):
                    base_theme[tkey] = {**base_theme[tkey], **tval}
                else:
                    base_theme[tkey] = tval
            merged["theme"] = base_theme
        else:
            merged[key] = ui_cfg[key]

    return merged


def _prune_backups(backup_dir: Path, keep: int = 20) -> None:
    """v2.3.1: 备份轮转——只保留最近 ``keep`` 份，防止无限堆积."""
    backups = sorted(
        backup_dir.glob("config.yaml.bak_*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[keep:]:
        try:
            old.unlink()
        except OSError:
            pass  # 清理失败不影响主流程


def write_plugin_config(ui_cfg: dict[str, Any]) -> tuple[bool, str]:
    """安全写回 ~/.hermes/config.yaml，并触发热重载.

    Deep-merges the UI-managed keys into the existing plugin section so that
    unrelated keys and other top-level sections (feishu credentials, etc.) are
    preserved. Refuses to write if the existing config cannot be parsed.
    """
    if not isinstance(ui_cfg, dict):
        return False, "无效的配置载荷：期望一个 JSON 对象"

    valid, why = _validate_ui_payload(ui_cfg)
    if not valid:
        return False, why

    cfg_path = _get_hermes_config_path()

    # 1. 读取整树；读失败即拒写，绝不覆盖无法解析的配置（保护凭证）
    try:
        full_cfg = read_full_config()
    except ConfigReadError as e:
        return False, f"拒绝写入：现有配置无法解析，请先修复。{e}"

    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    # 2. 备份现有配置（放在 ~/.hermes/backups/，绝不留在 plugins 目录）
    if cfg_path.exists():
        backup_dir = cfg_path.parent / "backups" / "studio_config_baks"
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_file = backup_dir / f"config.yaml.bak_{int(time.time())}"
            shutil.copy2(cfg_path, backup_file)
            _prune_backups(backup_dir)
        except OSError as e:
            _logger.warning("Studio: backup creation failed: %s", e)

    # 3. 深合并 UI 管理的键，保留其余一切
    existing_plugin = full_cfg.get("hermes_lark_streaming")
    if not isinstance(existing_plugin, dict):
        existing_plugin = {}
    full_cfg["hermes_lark_streaming"] = _merge_ui_plugin_section(existing_plugin, ui_cfg)

    # 4. 原子写回（先写临时文件再替换，避免写一半损坏）
    try:
        yaml_content = yaml.safe_dump(
            full_cfg,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        tmp_path = cfg_path.with_suffix(cfg_path.suffix + ".tmp")
        tmp_path.write_text(yaml_content, encoding="utf-8")
        os.replace(tmp_path, cfg_path)
    except OSError as e:
        return False, f"写入 config.yaml 失败: {e}"

    # 5. 触发内存单例热重载
    try:
        Config().reload()
        return True, "配置已成功保存并实时热加载生效"
    except Exception as e:  # noqa: BLE001 - reload best-effort; disk write already succeeded
        _logger.debug("Studio: reload after save failed", exc_info=True)
        return True, f"配置已保存，但内存热重载触发异常（重启网关即可生效）: {e}"


class StudioRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Studio 核心 HTTP 请求路由与静态文件托管.

    Loopback-only by design. Requests whose Host header is not a local address
    are rejected to blunt DNS-rebinding, and no permissive CORS header is sent
    so other origins cannot read/POST to this config-writing service.
    """

    server_version = "aiduPOP-Studio/2.3.1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        web_dir = Path(__file__).resolve().parent / "web"
        super().__init__(*args, directory=str(web_dir), **kwargs)

    # ── security gate ────────────────────────────────────────────────
    def _host_is_local(self) -> bool:
        """Accept only requests addressed to a loopback Host."""
        host = self.headers.get("Host", "")
        hostname = host.rsplit(":", 1)[0].strip("[]") if host else ""
        return hostname in ("127.0.0.1", "localhost", "::1", "")

    def _reject_if_nonlocal(self) -> bool:
        if self._host_is_local():
            return False
        self._send_json(403, {"ok": False, "error": "Forbidden: non-local Host rejected"})
        return True

    def _path_only(self) -> str:
        """Path without query string, so /api/config?x=1 still routes."""
        return self.path.split("?", 1)[0]

    def do_GET(self) -> None:
        if self._reject_if_nonlocal():
            return
        route = self._path_only()
        if route == "/api/config":
            self._handle_get_config()
        elif route == "/api/presets":
            self._handle_get_presets()
        elif route == "/api/health":
            self._send_json(200, {"status": "ok", "version": "2.3.1", "time": time.time()})
        elif route.startswith("/api/"):
            self._send_json(404, {"ok": False, "error": "Not Found"})
        else:
            # 托管静态文件
            super().do_GET()

    def do_POST(self) -> None:
        if self._reject_if_nonlocal():
            return
        route = self._path_only()
        if route == "/api/config":
            self._handle_post_config()
        elif route == "/api/restart":
            self._handle_restart_gateway()
        else:
            self._send_json(404, {"ok": False, "error": "Not Found"})

    def _handle_get_config(self) -> None:
        """获取当前 aiduPOP 生效配置."""
        try:
            full = read_full_config()
        except ConfigReadError as e:
            self._send_json(500, {"ok": False, "error": f"读取配置失败: {e}"})
            return

        plugin_cfg = full.get("hermes_lark_streaming", {})
        if not isinstance(plugin_cfg, dict):
            plugin_cfg = {}

        # 合并默认主题（BUBBLE_WAVE）与用户覆盖，供 UI 显示"当前生效值"
        merged_theme = copy.deepcopy(BUBBLE_WAVE)
        user_theme = plugin_cfg.get("theme", {})
        if isinstance(user_theme, dict):
            for k, v in user_theme.items():
                if isinstance(v, dict) and isinstance(merged_theme.get(k), dict):
                    merged_theme[k].update(v)
                else:
                    merged_theme[k] = v

        footer_cfg = plugin_cfg.get("footer")
        if not isinstance(footer_cfg, dict):
            footer_cfg = {"show_label": False, "fields": []}
        text_sizes_cfg = plugin_cfg.get("text_sizes")
        if not isinstance(text_sizes_cfg, dict):
            text_sizes_cfg = {"body": "normal_v2", "panel": "notation", "notice": "notation"}

        response_data = {
            "enabled": plugin_cfg.get("enabled", True),
            "linear": plugin_cfg.get("linear", True),
            "panel_expanded": plugin_cfg.get("panel_expanded", False),
            "streaming_panel_expanded": plugin_cfg.get("streaming_panel_expanded", False),
            "max_tool_steps": plugin_cfg.get("max_tool_steps", 20),
            "flush_interval_ms": plugin_cfg.get("flush_interval_ms", 200),
            "print_step": plugin_cfg.get("print_step", 4),
            "footer": footer_cfg,
            "text_sizes": text_sizes_cfg,
            "theme": merged_theme,
            "raw_user_theme": user_theme if isinstance(user_theme, dict) else {},
            "config_path": str(_get_hermes_config_path()),
        }
        self._send_json(200, {"ok": True, "data": response_data})

    def _handle_get_presets(self) -> None:
        """返回预设列表."""
        self._send_json(200, {"ok": True, "data": PRESETS})

    def _handle_post_config(self) -> None:
        """接收前端保存的配置."""
        try:
            content_len = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send_json(400, {"ok": False, "error": "Invalid Content-Length"})
            return
        if content_len <= 0 or content_len > 1_000_000:
            self._send_json(400, {"ok": False, "error": "Empty or oversized payload"})
            return
        try:
            body = self.rfile.read(content_len).decode("utf-8")
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as e:
            self._send_json(400, {"ok": False, "error": f"Invalid JSON payload: {e}"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Payload must be a JSON object"})
            return

        ok, msg = write_plugin_config(payload)
        if ok:
            self._send_json(200, {"ok": True, "message": msg})
        else:
            self._send_json(400, {"ok": False, "error": msg})

    def _handle_restart_gateway(self) -> None:
        """尝试重启 hermes-gateway（尽力而为，如实回报）."""
        try:
            res = subprocess.run(
                ["systemctl", "--user", "restart", "hermes-gateway"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            self._send_json(
                200,
                {
                    "ok": True,
                    "restarted": False,
                    "message": "本机未安装 systemctl（如本地开发/macOS），未执行重启；配置改动通过热重载已生效。",
                },
            )
            return
        except subprocess.TimeoutExpired:
            self._send_json(504, {"ok": False, "error": "重启超时（systemctl 10s 未返回）"})
            return
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"执行重启异常: {e}"})
            return

        if res.returncode == 0:
            self._send_json(
                200,
                {"ok": True, "restarted": True, "message": "网关服务重启成功 (systemctl --user restart hermes-gateway)"},
            )
        else:
            self._send_json(
                500,
                {"ok": False, "error": f"重启失败 (exit {res.returncode}): {res.stderr.strip() or '无错误输出'}"},
            )

    def _send_json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # 简化日志输出
        _logger.debug("Studio HTTP: %s", format % args)


class _StudioServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_studio_server(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    """启动 aiduPOP Studio 独立服务器. 返回进程退出码."""
    try:
        httpd = _StudioServer((host, port), StudioRequestHandler)
    except OSError as e:
        print(f"\n[aiduPOP Studio] 无法在 {host}:{port} 启动服务：{e}")
        print("  端口可能被占用。请用 --port 指定其它端口，例如: aidupop studio --port 8770\n")
        return 1

    with httpd:
        url = f"http://{host}:{port}"
        print("\n=======================================================")
        print("  🎨 aiduPOP Visual Studio (v2.3.1)")
        print("  🌊 爱嘟波泡卡 · 流式卡片可视化工作坊")
        print("  -----------------------------------------------------")
        print(f"  🌐 服务地址: {url}")
        print(f"  📂 配置文件: {_get_hermes_config_path()}")
        print("  🔒 仅监听本机回环地址（loopback-only）")
        print("  ⌨️  按 Ctrl+C 退出工作坊")
        print("=======================================================\n")

        if open_browser:
            import webbrowser
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001 - browser launch is best-effort
                _logger.debug("Studio: webbrowser.open failed", exc_info=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStudio 服务已平稳停止。")
    return 0


def main() -> int:
    """Console-script entry point for ``aidupop-studio``."""
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="aiduPOP Visual Studio (v2.3.1)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen (default: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    parsed = parser.parse_args()
    return run_studio_server(host=parsed.host, port=parsed.port, open_browser=not parsed.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
