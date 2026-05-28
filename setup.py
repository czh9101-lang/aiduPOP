"""Dynamic version provider — reads from plugin.yaml (single source of truth)."""

from pathlib import Path

from setuptools import setup

_plugin_yaml = Path(__file__).resolve().parent / "plugin.yaml"
_version = "0.0.0"

if _plugin_yaml.exists():
    for _line in _plugin_yaml.read_text(encoding="utf-8").splitlines():
        if _line.startswith("version:"):
            _version = _line.split(":", 1)[1].strip().strip('"').strip("'")
            break

setup(version=_version)
