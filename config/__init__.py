"""Configuration sub-package — Hermes config reader.

Re-exports key names for convenient access:
    from hermes_lark_streaming.config import Config, _get_hermes_config_path
"""

from .reader import Config, _get_hermes_config_path  # noqa: F401

__all__ = ["Config", "_get_hermes_config_path"]
