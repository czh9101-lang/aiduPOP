"""hermes-lark-streaming — Feishu/Lark CardKit v2.0 streaming cards for Hermes Agent."""

from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("hermes-lark-streaming")
except Exception:
    # Fallback: read from plugin.yaml (single source of truth)
    try:
        from pathlib import Path

        _plugin_yaml = Path(__file__).resolve().parent.parent / "plugin.yaml"
        if _plugin_yaml.exists():
            for _line in _plugin_yaml.read_text(encoding="utf-8").splitlines():
                if _line.startswith("version:"):
                    __version__ = _line.split(":", 1)[1].strip().strip('"').strip("'")
                    break
            else:
                __version__ = "unknown"
        else:
            __version__ = "unknown"
    except Exception:
        __version__ = "unknown"

from .plugin import register

__all__ = ["register", "__version__"]
