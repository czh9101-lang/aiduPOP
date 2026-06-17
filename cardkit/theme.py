"""Card theme system — customizable colors, icons, and layout.

v1.1.0 (Task 3.6): Allows users to customize card appearance via config.yaml:

```yaml
hermes_lark_streaming:
  theme:
    name: default  # or "dark", "compact", or custom
    # Override individual values:
    panel_icon: robot_filled
    panel_border_color: grey
    header_color_success: green
    header_color_error: red
    header_color_aborted: orange
    tool_color_running: orange
    tool_color_success: green
    tool_color_failed: red
    reasoning_color_active: orange
    reasoning_color_done: green
    reasoning_color_failed: red
```

Themes are pre-defined presets that users can extend with overrides.
"""

from __future__ import annotations

from typing import Any

from ..config import Config


# ── Pre-defined theme presets ──

_THEMES: dict[str, dict[str, str]] = {
    "default": {
        "panel_icon": "robot_filled",
        "panel_border_color": "grey",
        "header_color_success": "green",
        "header_color_error": "red",
        "header_color_aborted": "orange",
        "header_color_thinking": "blue",
        "tool_color_running": "orange",
        "tool_color_success": "green",
        "tool_color_failed": "red",
        "reasoning_color_active": "orange",
        "reasoning_color_done": "green",
        "reasoning_color_failed": "red",
        "loading_img_key": "img_v3_02bv_4f0a2a7c-8f9c-4d33-bb8e-c01b3e9a7c2g",
    },
    "dark": {
        "panel_icon": "robot_filled",
        "panel_border_color": "dark_gray",
        "header_color_success": "light_green",
        "header_color_error": "light_red",
        "header_color_aborted": "light_orange",
        "header_color_thinking": "light_blue",
        "tool_color_running": "light_orange",
        "tool_color_success": "light_green",
        "tool_color_failed": "light_red",
        "reasoning_color_active": "light_orange",
        "reasoning_color_done": "light_green",
        "reasoning_color_failed": "light_red",
        "loading_img_key": "img_v3_02bv_4f0a2a7c-8f9c-4d33-bb8e-c01b3e9a7c2g",
    },
    "compact": {
        "panel_icon": "robot_outlined",
        "panel_border_color": "light_gray",
        "header_color_success": "green",
        "header_color_error": "red",
        "header_color_aborted": "orange",
        "header_color_thinking": "blue",
        "tool_color_running": "orange",
        "tool_color_success": "green",
        "tool_color_failed": "red",
        "reasoning_color_active": "orange",
        "reasoning_color_done": "green",
        "reasoning_color_failed": "red",
        "loading_img_key": "img_v3_02bv_4f0a2a7c-8f9c-4d33-bb8e-c01b3e9a7c2g",
    },
}


# ── Cache for resolved theme ──
_cached_theme: dict[str, str] | None = None
_cached_theme_key: str = ""


def get_theme(config: Config | None = None) -> dict[str, str]:
    """Get the resolved theme dict, merging preset with user overrides.

    Caches the result until config changes (v1.1.0 hot reload aware).
    """
    global _cached_theme, _cached_theme_key

    if config is None:
        config = Config()

    # Build cache key from theme name + overrides
    sec = config._plugin_sec() if hasattr(config, "_plugin_sec") else {}
    theme_cfg = sec.get("theme", {}) if isinstance(sec, dict) else {}
    if not isinstance(theme_cfg, dict):
        theme_cfg = {}

    theme_name = theme_cfg.get("name", "default")
    overrides = {k: v for k, v in theme_cfg.items() if k != "name"}

    cache_key = f"{theme_name}:{sorted(overrides.items())}"

    if _cached_theme is not None and cache_key == _cached_theme_key:
        return _cached_theme

    # Start with preset, apply overrides
    base = _THEMES.get(theme_name, _THEMES["default"]).copy()
    base.update(overrides)

    _cached_theme = base
    _cached_theme_key = cache_key
    return base


def invalidate_theme_cache() -> None:
    """Clear the theme cache — called on config reload."""
    global _cached_theme, _cached_theme_key
    _cached_theme = None
    _cached_theme_key = ""


# ── Convenience accessors ──

def panel_icon(config: Config | None = None) -> str:
    return get_theme(config).get("panel_icon", "robot_filled")

def panel_border_color(config: Config | None = None) -> str:
    return get_theme(config).get("panel_border_color", "grey")

def header_color(visual_state: str, config: Config | None = None) -> str:
    """Get header color for a visual state (complete/error/aborted/thinking)."""
    theme = get_theme(config)
    mapping = {
        "complete": "header_color_success",
        "error": "header_color_error",
        "aborted": "header_color_aborted",
        "thinking": "header_color_thinking",
    }
    key = mapping.get(visual_state, "header_color_thinking")
    return theme.get(key, "blue")

def tool_color(status: str, config: Config | None = None) -> str:
    """Get tool step color for a status (running/success/failed)."""
    theme = get_theme(config)
    mapping = {
        "running": "tool_color_running",
        "success": "tool_color_success",
        "failed": "tool_color_failed",
    }
    key = mapping.get(status, "tool_color_running")
    return theme.get(key, "orange")

def reasoning_color(status: str, config: Config | None = None) -> str:
    """Get reasoning round color for a status (active/done/failed)."""
    theme = get_theme(config)
    mapping = {
        "active": "reasoning_color_active",
        "done": "reasoning_color_done",
        "failed": "reasoning_color_failed",
    }
    key = mapping.get(status, "reasoning_color_active")
    return theme.get(key, "orange")

def loading_img_key(config: Config | None = None) -> str:
    return get_theme(config).get(
        "loading_img_key",
        "img_v3_02bv_4f0a2a7c-8f9c-4d33-bb8e-c01b3e9a7c2g",
    )


__all__ = [
    "get_theme",
    "invalidate_theme_cache",
    "panel_icon",
    "panel_border_color",
    "header_color",
    "tool_color",
    "reasoning_color",
    "loading_img_key",
]
