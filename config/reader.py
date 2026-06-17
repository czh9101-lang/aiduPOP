"""读取 Hermes 配置，提供本插件所需的配置项.

v1.1.0 (Task 3.5): 支持配置项运行时热更新。
- Config.reload() 方法清除缓存，强制下次属性访问时从磁盘重读
- 文件 mtime 检测：如果 config.yaml 的 mtime 变了，自动失效缓存
- Config.on_reload 回调列表：其他模块可注册回调在配置重载时收到通知
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

import yaml


def _get_hermes_config_path() -> Path:
    """动态获取 Hermes 配置文件路径.

    在多 Profile 场景下，HERMES_HOME 环境变量会在 Gateway 启动时
    通过 _apply_profile_override() 设置。如果在模块导入时就读取
    该变量，可能会读到错误的路径。

    此函数每次调用时都重新读取环境变量，确保始终使用正确的路径。
    """
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "config.yaml"


_RELOAD_CACHE_TTL = 5.0  # 运行时可变配置的缓存 TTL（秒）


class Config:
    """插件配置，惰性读取 Hermes 主配置.

    v1.1.0: 支持热更新。调用 reload() 清除缓存，或依赖自动 mtime 检测。
    """

    def __init__(self) -> None:
        self._raw: dict[str, Any] | None = None
        self._reload_cache: dict[str, Any] | None = None
        self._reload_cache_at: float = 0.0
        self._config_mtime: float = 0.0  # v1.1.0: track file mtime for auto-reload
        self._on_reload_callbacks: list[Callable[[], None]] = []  # v1.1.0

    # ── v1.1.0: Hot reload support (Task 3.5) ──

    def reload(self) -> None:
        """Force reload configuration from disk on next property access.

        Clears all caches and fires registered on_reload callbacks.
        Safe to call from any thread.
        """
        self._raw = None
        self._reload_cache = None
        self._reload_cache_at = 0.0
        _logger = __import__("logging").getLogger("hermes_lark_streaming")
        _logger.info("HLS: config reload triggered — caches cleared")
        for cb in self._on_reload_callbacks:
            try:
                cb()
            except Exception:
                _logger.debug("HLS: on_reload callback failed", exc_info=True)

    def on_reload(self, callback: Callable[[], None]) -> None:
        """Register a callback to be called when config is reloaded."""
        self._on_reload_callbacks.append(callback)

    def _check_mtime_and_invalidate(self) -> None:
        """Check if config.yaml mtime changed; if so, invalidate caches.

        Called on every property access. O(1) stat() call.
        """
        try:
            config_path = _get_hermes_config_path()
            if config_path.exists():
                mtime = config_path.stat().st_mtime
                if mtime != self._config_mtime:
                    if self._config_mtime > 0:  # Not first load
                        _logger = __import__("logging").getLogger("hermes_lark_streaming")
                        _logger.info(
                            "HLS: config.yaml mtime changed (%.0f → %.0f), auto-reloading",
                            self._config_mtime, mtime,
                        )
                        self._raw = None
                        self._reload_cache = None
                        self._reload_cache_at = 0.0
                        for cb in self._on_reload_callbacks:
                            try:
                                cb()
                            except Exception:
                                pass
                    self._config_mtime = mtime
        except Exception:
            pass  # Non-critical — stat failure shouldn't break config access

    @property
    def enabled(self) -> bool:
        """是否启用流式卡片."""
        self._check_mtime_and_invalidate()
        sec = self._plugin_sec()
        return bool(sec.get("enabled", False))

    @property
    def linear(self) -> bool:
        """是否启用线性单卡模式，按事件顺序动态更新卡片元素."""
        sec = self._plugin_sec()
        return bool(sec.get("linear", True))

    @property
    def panel_expanded(self) -> bool:
        """完成态卡片中面板（工具、推理）是否保持展开."""
        sec = self._plugin_sec()
        return bool(sec.get("panel_expanded", False))

    @property
    def streaming_panel_expanded(self) -> bool:
        """流式态卡片中面板（工具、推理）是否保持展开.

        默认 False（保持现有行为：流式态面板收起）。
        与 panel_expanded（完成态面板）独立配置。
        """
        sec = self._plugin_sec()
        return bool(sec.get("streaming_panel_expanded", False))

    @property
    def max_tool_steps(self) -> int:
        """统一面板中最多显示的工具步骤数.

        超出此数量的早期工具步骤会被折叠为一行提示。
        飞书卡片2.0元素上限200，每个工具步骤最多占7个元素（标题3+详情2+结果2）。
        默认20，确保即使在极端情况下也不会超限。
        """
        sec = self._plugin_sec()
        val = sec.get("max_tool_steps", 20)
        return max(1, min(100, int(val)))

    @property
    def max_reasoning_rounds(self) -> int:
        """统一面板中最多显示的推理轮次数.

        超出此数量的早期推理轮次会被折叠为一行提示。
        飞书卡片2.0元素上限200，每个推理轮次最多占4个元素（标题3+文本1）。
        默认20，确保即使在极端情况下也不会超限。
        """
        sec = self._plugin_sec()
        val = sec.get("max_reasoning_rounds", 20)
        return max(1, min(100, int(val)))

    @property
    def print_strategy(self) -> str:
        """流式卡片上屏策略: "fast" 或 "delay".

        - fast: 新内容到达时，未上屏的旧内容立即全部上屏，然后开始新内容上屏（默认）
        - delay: 未上屏的旧内容继续按打字机效果输出，全部完成后才开始新内容上屏（更丝滑）

        默认 "delay"（更丝滑的阅读体验）。
        """
        sec = self._plugin_sec()
        strategy = sec.get("print_strategy", "delay")
        return strategy if strategy in ("fast", "delay") else "delay"

    @property
    def flush_interval_ms(self) -> float:
        """流式卡片刷新间隔（毫秒），用于诊断日志.

        最小值 70ms：对齐飞书 CardKit 官方默认 print_frequency_ms（70ms），
        避免服务端 flush 间隔低于客户端渲染间隔导致过度缓冲或频控问题。
        """
        sec = self._plugin_sec()
        ms = float(sec.get("flush_interval_ms", 100))
        return max(70.0, min(2000.0, ms))

    @property
    def flush_interval_sec(self) -> float:
        """流式卡片刷新间隔（秒），可配置.

        默认 0.1 秒（100ms）。降低此值使打字效果更流畅但增加API调用量和客户端负担；
        提高此值减少API调用量但文字出现稍有延迟。

        注意：此值仅影响 CardKit 流式通道，IM PATCH 降级通道固定为 1.5 秒。
        """
        return self.flush_interval_ms / 1000.0

    @property
    def show_reasoning(self) -> bool:
        """是否展示推理过程（display.platforms.feishu.show_reasoning → display.show_reasoning）.

        通过 TTL 缓存读取，因为 /reasoning 命令会在运行时修改配置文件，
        但不需要每次属性访问都读磁盘。最多延迟 _RELOAD_CACHE_TTL 秒生效。
        """
        display = self._reload_cached().get("display")
        if not isinstance(display, dict):
            return False
        platforms = display.get("platforms")
        if isinstance(platforms, dict):
            feishu = platforms.get("feishu")
            if isinstance(feishu, dict) and "show_reasoning" in feishu:
                return bool(feishu["show_reasoning"])
        return bool(display.get("show_reasoning", False))

    @property
    def feishu_app_id(self) -> str:
        return str(self._platform_cfg().get("app_id", ""))

    @property
    def feishu_app_secret(self) -> str:
        return str(self._platform_cfg().get("app_secret", ""))

    @property
    def feishu_base_url(self) -> str:
        return str(self._platform_cfg().get("base_url", "https://open.feishu.cn/open-apis"))

    @property
    def card_duration_sec(self) -> int:
        """卡片存活检测超时."""
        return int(self._plugin_sec().get("card_ttl_sec", 600))

    @property
    def footer_fields(self) -> list[list[str]]:
        """Footer 字段布局（二维数组）."""
        sec = self._plugin_sec()
        footer = sec.get("footer", {})
        if not isinstance(footer, dict):
            return self._default_footer_fields()
        fields = footer.get("fields")
        if not fields:
            return self._default_footer_fields()
        if not isinstance(fields, list):
            return self._default_footer_fields()
        # 一维数组自动包装为二维
        if fields and isinstance(fields[0], str):
            return [fields]
        return fields

    @property
    def inject_time(self) -> bool:
        """是否在用户消息前注入当前时间，让模型无需调用 date 工具即可感知时间.

        默认关闭。开启后，每条用户消息前会添加 ``<time>HH:MM:SS</time>`` 前缀，
        前缀同时写入 DB（保证 prefix cache 一致性）。
        使用 XML 标签格式而非方括号格式，避免 LLM 忽略或模仿时间前缀。

        通过 TTL 缓存读取，用户运行时修改配置文件后最多延迟
        _RELOAD_CACHE_TTL 秒生效，避免高频访问时反复读磁盘。
        """
        sec = self._reload_cached().get("hermes_lark_streaming")
        if not isinstance(sec, dict):
            return False
        return bool(sec.get("inject_time", False))

    @property
    def footer_show_label(self) -> bool:
        """Footer 是否显示字段标签."""
        sec = self._plugin_sec()
        footer = sec.get("footer", {})
        return bool(footer.get("show_label", False))

    @property
    def header_enabled(self) -> bool:
        """流式卡片和完成态卡片是否显示 header."""
        sec = self._plugin_sec()
        header = sec.get("header", {})
        if not isinstance(header, dict):
            return False
        return bool(header.get("enabled", False))

    @property
    def gateway_cards(self) -> bool:
        """是否将飞书渠道的网关内部消息（slash命令、错误、通知等）转为卡片.

        默认开启。关闭后，网关消息仍以原始文本发送，仅 AI 回复和
        Cron 消息使用卡片。

        通过 TTL 缓存读取，用户运行时修改配置文件后最多延迟
        _RELOAD_CACHE_TTL 秒生效。
        """
        sec = self._reload_cached().get("hermes_lark_streaming")
        if not isinstance(sec, dict):
            return True  # 默认开启
        return bool(sec.get("gateway_cards", True))

    @staticmethod
    def _default_footer_fields() -> list[list[str]]:
        return [["status", "elapsed", "model", "cost", "compression_exhausted"]]

    @property
    def env_app_id(self) -> str:
        return os.environ.get("FEISHU_APP_ID") or os.environ.get("LARK_APP_ID") or ""

    @property
    def env_app_secret(self) -> str:
        return os.environ.get("FEISHU_APP_SECRET") or os.environ.get("LARK_APP_SECRET") or ""

    def _plugin_sec(self) -> dict[str, Any]:
        """Return the ``hermes_lark_streaming`` section from config."""
        raw = self._load()
        sec = raw.get("hermes_lark_streaming")
        if isinstance(sec, dict):
            return sec
        return {}

    def _platform_cfg(self) -> dict[str, Any]:
        """从环境变量或平台配置找飞书凭据."""
        if self.env_app_id and self.env_app_secret:
            return {
                "app_id": self.env_app_id,
                "app_secret": self.env_app_secret,
                "base_url": os.environ.get(
                    "FEISHU_BASE_URL",
                    os.environ.get("LARK_BASE_URL", "https://open.feishu.cn/open-apis"),
                ),
            }
        raw = self._load()
        for key in ("feishu", "lark"):
            pf = raw.get(key)
            if isinstance(pf, dict) and pf.get("app_id"):
                return pf
        return {}

    def _load(self) -> dict[str, Any]:
        if self._raw is not None:
            return self._raw
        # 每次都动态读取 HERMES_HOME，支持多 Profile 场景
        config_path = _get_hermes_config_path()
        if config_path.exists():
            text = config_path.read_text(encoding="utf-8")
            self._raw = yaml.safe_load(text) or {}
        else:
            self._raw = {}
        return self._raw

    def _reload_cached(self) -> dict[str, Any]:
        """带 TTL 缓存的磁盘重读，供运行时可变的配置项使用.

        在 _RELOAD_CACHE_TTL 秒内复用上次读取结果，避免高频属性访问
        （如流式输出期间反复检查 inject_time / show_reasoning）反复读磁盘。
        配置变更最多延迟 _RELOAD_CACHE_TTL 秒生效。
        """
        now = time.monotonic()
        if self._reload_cache is not None and (now - self._reload_cache_at) < _RELOAD_CACHE_TTL:
            return self._reload_cache
        # 每次都动态读取 HERMES_HOME，支持多 Profile 场景
        config_path = _get_hermes_config_path()
        if config_path.exists():
            text = config_path.read_text(encoding="utf-8")
            self._reload_cache = yaml.safe_load(text) or {}
        else:
            self._reload_cache = {}
        self._reload_cache_at = now
        return self._reload_cache
