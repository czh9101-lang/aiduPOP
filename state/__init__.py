"""State sub-package — session, text, tooluse, linear, and linear_split modules.

Re-exports key names from sub-modules for convenient access:
    from hermes_lark_streaming.state import CardSession, TextState, ToolUseTracker, LinearState, Segment
"""

from .session import CardSession  # noqa: F401
from .text import TextState, split_reasoning_text, strip_reasoning_tags, extract_thinking_content  # noqa: F401
from .tooluse import ToolUseTracker, ToolStep, ToolSession, redact_inline_secrets  # noqa: F401
from .linear import LinearState, Segment  # noqa: F401
from .linear_split import (  # noqa: F401
    _ELEMENT_THRESHOLD,
    _FOOTER_RESERVE,
    _estimate_segment_elements,
    _estimate_tool_elements,
    _find_tool_split_offset,
    _simplify_segments_for_complete,
    _tool_segment_end,
    _count_images_in_text,
)
