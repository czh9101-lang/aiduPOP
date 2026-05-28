"""hermes-lark-streaming — Feishu/Lark CardKit v2.0 streaming cards for Hermes Agent."""

import logging
from pathlib import Path

_logger = logging.getLogger("hermes_lark_streaming")

_plugin_yaml = Path(__file__).resolve().parent.parent / "plugin.yaml"
if _plugin_yaml.exists():
    for _line in _plugin_yaml.read_text(encoding="utf-8").splitlines():
        if _line.startswith("version:"):
            __version__ = _line.split(":", 1)[1].strip().strip('"').strip("'")
            break
    else:
        __version__ = "unknown"
        _logger.warning("plugin.yaml exists but no 'version:' field found")
else:
    __version__ = "unknown"
    _logger.warning("plugin.yaml not found at %s — installation may be broken", _plugin_yaml)

from .plugin import register

__all__ = ["register", "__version__"]
