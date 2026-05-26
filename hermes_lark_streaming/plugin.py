"""Plugin entry point — register(ctx) is discovered by Hermes plugin system.

On registration, this plugin ensures ``config.yaml`` has a top-level
``streaming`` section with the minimal required defaults, so that
streaming cards work out of the box without manual configuration.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from hermes_cli.plugins import PluginContext

_logger = logging.getLogger("hermes_lark_streaming")

_HERMES_CONFIG_PATH = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "config.yaml"

# Default streaming config injected into config.yaml on first load
_DEFAULT_STREAMING_CONFIG = {
    "enabled": True,
    "linear": True,
    "panel_expanded": False,
    "card_ttl_sec": 600,
    "footer": {
        "fields": [
            ["status", "elapsed", "model"],
            ["tokens", "context"],
        ],
        "show_label": True,
    },
}


def _ensure_streaming_config() -> None:
    """Ensure config.yaml has a top-level ``streaming`` section.

    Only writes when the top-level ``streaming`` key is missing entirely.
    Does NOT overwrite existing user configuration.
    """
    if not _HERMES_CONFIG_PATH.exists():
        _logger.warning("config.yaml not found at %s, skipping config injection", _HERMES_CONFIG_PATH)
        return

    try:
        text = _HERMES_CONFIG_PATH.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}

        if "streaming" not in raw:
            raw["streaming"] = dict(_DEFAULT_STREAMING_CONFIG)
            with open(_HERMES_CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            _logger.info(
                "Injected top-level streaming config into %s",
                _HERMES_CONFIG_PATH,
            )
        else:
            _logger.debug("Top-level streaming config already exists, skipping")
    except Exception:
        _logger.exception("Failed to inject streaming config into config.yaml")


def register(ctx: "PluginContext") -> None:
    """Register hermes-lark-streaming as a Hermes plugin.

    Applies runtime monkey patches to GatewayRunner, AIAgent, and
    Scheduler so that streaming CardKit v2.0 cards are sent during
    Feishu conversations — no source file modification required.
    """
    # Ensure streaming config section exists in config.yaml
    _ensure_streaming_config()

    _logger.info("hermes-lark-streaming: applying runtime patches...")
    try:
        from .monkey_patch import apply_patches

        apply_patches()
        _logger.info("hermes-lark-streaming: patches applied successfully")
    except Exception:
        _logger.exception("hermes-lark-streaming: failed to apply patches")
