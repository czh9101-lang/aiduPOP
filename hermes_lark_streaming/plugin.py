"""Plugin entry point — register(ctx) is discovered by Hermes plugin system."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes_cli.plugins import PluginContext

_logger = logging.getLogger("hermes_lark_streaming")


def register(ctx: "PluginContext") -> None:
    """Register hermes-lark-streaming as a Hermes plugin.

    Applies runtime monkey patches to GatewayRunner, AIAgent, and
    Scheduler so that streaming CardKit v2.0 cards are sent during
    Feishu conversations — no source file modification required.
    """
    _logger.info("hermes-lark-streaming: applying runtime patches...")
    try:
        from .monkey_patch import apply_patches

        apply_patches()
        _logger.info("hermes-lark-streaming: patches applied successfully")
    except Exception:
        _logger.exception("hermes-lark-streaming: failed to apply patches")
