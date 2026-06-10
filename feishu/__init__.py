"""Feishu/Lark API client sub-package.

Re-exports key names for convenient access:
    from hermes_lark_streaming.feishu import FeishuClient, FeishuClientConfig, FeishuAPIError
    from hermes_lark_streaming.feishu import UnavailableGuard, MSG_NOT_FOUND
"""

from .client import (  # noqa: F401
    FeishuClient,
    FeishuClientConfig,
    FeishuAPIError,
    is_element_limit_error,
    CARDKIT_RATE_LIMITED,
    CARDKIT_CONTENT_FAILED,
    CARDKIT_ELEMENT_LIMIT,
    CARDKIT_ELEMENT_LIMIT_DIRECT,
    CARDKIT_STREAMING_CLOSED,
    CARDKIT_SEQUENCE_CONFLICT,
    MSG_NOT_FOUND,
    CARDKIT_TRANSIENT_CODES,
)
from .guard import (  # noqa: F401
    UnavailableGuard,
    mark_unavailable,
    is_unavailable,
    extract_api_code,
    is_terminal_api_code,
)

__all__ = [
    "FeishuClient",
    "FeishuClientConfig",
    "FeishuAPIError",
    "is_element_limit_error",
    "CARDKIT_RATE_LIMITED",
    "CARDKIT_CONTENT_FAILED",
    "CARDKIT_ELEMENT_LIMIT",
    "CARDKIT_ELEMENT_LIMIT_DIRECT",
    "CARDKIT_STREAMING_CLOSED",
    "CARDKIT_SEQUENCE_CONFLICT",
    "MSG_NOT_FOUND",
    "CARDKIT_TRANSIENT_CODES",
    "UnavailableGuard",
    "mark_unavailable",
    "is_unavailable",
    "extract_api_code",
    "is_terminal_api_code",
]
