"""Flush throttle sub-package — FlushController and constants.

Re-exports key names for convenient access:
    from hermes_lark_streaming.flush import FlushController, CARDKIT_MS, PATCH_MS
"""

from .controller import (  # noqa: F401
    FlushController,
    CARDKIT_MS,
    PATCH_MS,
    LONG_GAP_MS,
    BATCH_AFTER_GAP_MS,
)

__all__ = [
    "FlushController",
    "CARDKIT_MS",
    "PATCH_MS",
    "LONG_GAP_MS",
    "BATCH_AFTER_GAP_MS",
]
