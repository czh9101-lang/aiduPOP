"""CardKit v2.0 — Card assemblers: streaming, complete, and linear cards."""



from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .elements import (
    ANSWER_ELEMENT_ID,
    REASONING_ELEMENT_ID,
    STREAMING_ELEMENT_ID,
    TOOL_PANEL_ELEMENT_ID,
    UNIFIED_PANEL_ELEMENT_ID,
    _LOADING_ELEMENT_ID,
    _LOADING_HINT_ELEMENT_ID,
    _build_background_review_panel,
    _build_error_panel,
    _build_footer_elements,
    _build_header,
    _build_reasoning_panel,
    _build_tool_panel,
    _build_unified_panel_placeholder,
    _collapsible_panel,
    _extract_images_from_markdown,
    _loading_element,
    _loading_hint_element,
    _streaming_element,
    build_unified_panel,
)
from .i18n import _LOCALES, _T, _i18n, _t
from .md import (
    _downgrade_tables,
    _split_long_text,
    optimize_markdown_style,
)

if TYPE_CHECKING:
    from ..state.linear import ReasoningRound, Segment



__all__ = [
    'build_streaming_tool_use_pending_panel',
    'build_streaming_card_v2',
    'build_im_fallback_card',
    'build_streaming_card',
    'build_complete_card',
    'build_linear_complete_card',
    'build_unified_complete_card',
]

def _build_summary(text: str) -> dict[str, Any]:
    """Build a summary dict with both content and i18n_content.

    Feishu CardKit 2.0 displays ``i18n_content.<locale>`` for users
    whose Feishu language matches that locale, and falls back to
    ``content`` otherwise.  If we only update ``content`` but not
    ``i18n_content``, Chinese users continue seeing the old
    "处理中..." in the conversation list even after close_streaming
    succeeds — the exact bug reported as "会话列表永久显示处理中".
    """
    truncated = text[:120].replace("\n", " ").replace("```", "").strip()
    return {
        "content": truncated,
        "i18n_content": _i18n(truncated, truncated),
    }


def build_streaming_tool_use_pending_panel() -> dict[str, Any]:
    return _collapsible_panel(
        expanded=False,
        title_el={
            "tag": "plain_text",
            "content": _T["tool_pending"][0],
            "i18n_content": _t("tool_pending"),
            "text_color": "grey",
            "text_size": "notation",
        },
        elements=[],
    )


def build_streaming_card_v2(
    *,
    tool_steps: list[dict] | None = None,
    elapsed_ms: float = 0,
    show_tool_use: bool = True,
    show_reasoning: bool = False,
    show_streaming_element: bool = True,
    streaming_panel_expanded: bool = True,
    print_strategy: str = "delay",
    header_enabled: bool = False,
    include_unified_panel: bool = True,
    include_loading_hint: bool = True,
    include_answer_element: bool = True,
) -> dict[str, Any]:
    """CardKit 2.0 流式占位卡片 — placeholder card for streaming mode.

    Card lifecycle (v1.0.2+):
        Phase 1 — User sends message → Create placeholder card with only
        "正在加载上下文..." + loading icon (no panel, no answer element).
        Phase 2 — First LLM token arrives → Delete loading hint, add
        unified panel + answer element via ``add_elements``.
        Phase 3 — Stream reasoning/tool content in panel, stream answer text.
        Phase 4 — Complete → Add footer.

    Parameters
    ----------
    include_unified_panel : bool
        When ``True``, adds the unified panel placeholder element.
        In the new lifecycle (default for linear mode), this is ``False``
        — the panel is added dynamically when the first content arrives.
    include_loading_hint : bool
        When ``True`` (default), adds the context-loading hint element.
        This hint is removed once the first LLM token arrives.
    include_answer_element : bool
        When ``True``, adds the answer streaming element to the initial card.
        In the new lifecycle, this is ``False`` — the answer element is
        added alongside the panel when the first content arrives.
    """
    elements: list[dict] = []

    # ── Unified panel placeholder (linear mode — single panel for reasoning+tools) ──
    if include_unified_panel:
        elements.append(_build_unified_panel_placeholder(expanded=streaming_panel_expanded))

    # ── Streaming answer element ──
    if show_streaming_element and include_answer_element:
        elements.append(_streaming_element(element_id=ANSWER_ELEMENT_ID))

    # ── Loading hint (context loading placeholder, removed on first LLM token) ──
    if include_loading_hint:
        elements.append(_loading_hint_element())

    # ── Loading spinner ──
    elements.append(_loading_element())

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "streaming_mode": True,
            "streaming_config": {
                "print_frequency_ms": {"default": 70},
                "print_step": {"default": 1},
                "print_strategy": print_strategy,
            },
            "locales": _LOCALES,
            "summary": {
                "content": _T["processing"][0],
                "i18n_content": _t("processing"),
            },
        },
        "body": {"elements": elements},
    }
    if header_enabled:
        card["header"] = _build_header("streaming")
    return card


def build_im_fallback_card() -> dict[str, Any]:
    return {
        "config": {
            "wide_screen_mode": True,
            "update_multi": True,
            "locales": _LOCALES,
        },
        "elements": [
            {
                "tag": "markdown",
                "content": _T["processing_prefix"][0],
                "i18n_content": _t("processing_prefix"),
            },
        ],
    }


def build_streaming_card(
    *,
    tool_steps: list[dict] | None = None,
    reasoning_text: str = "",
    text: str = "",
) -> dict[str, Any]:
    """IM PATCH 降级路径的流式更新卡片."""
    elements: list[dict] = []

    if reasoning_text:
        elements.append(
            {
                "tag": "markdown",
                "content": f"{_T['thinking'][0]}\n\n{reasoning_text}",
                "i18n_content": _i18n(
                    f"{_T['thinking'][0]}\n\n{reasoning_text}",
                    f"{_T['thinking'][1]}\n\n{reasoning_text}",
                ),
            }
        )

    if tool_steps:
        elements.append(_build_tool_panel(tool_steps))

    elements.append({"tag": "markdown", "content": _downgrade_tables(optimize_markdown_style(text)) if text else " "})

    return {
        "config": {
            "wide_screen_mode": True,
            "update_multi": True,
            "locales": _LOCALES,
        },
        "elements": elements,
    }


def build_complete_card(
    *,
    text: str = "",
    reasoning_text: str = "",
    reasoning_elapsed_ms: float = 0,
    tool_steps: list[dict] | None = None,
    tool_elapsed_ms: float = 0,
    footer_data: dict | None = None,
    has_cardkit: bool = False,
    is_error: bool = False,
    is_aborted: bool = False,
    error_message: str = "",
    footer_fields: list[list[str]] | None = None,
    footer_show_label: bool = True,
    panel_expanded: bool = False,
    header_enabled: bool = False,
) -> dict[str, Any]:
    """完成态卡片 — 含 header、reasoning 面板、footer."""
    elements: list[dict] = []

    if reasoning_text:
        elements.append(_build_reasoning_panel(reasoning_text, reasoning_elapsed_ms, expanded=panel_expanded))

    if tool_steps:
        elements.append(_build_tool_panel(tool_steps, tool_elapsed_ms, expanded=panel_expanded))

    # ── 错误/中断面板 ──
    # 可折叠面板，与推理面板、工具面板视觉风格一致
    if error_message:
        elements.append(_build_error_panel(
            error_message, is_aborted=is_aborted, expanded=panel_expanded,
        ))

    content = _downgrade_tables(optimize_markdown_style(text or _T["done"][0]))
    # ── 提取已解析图片为独立 img 元素（Card 2.0 独立渲染，更清晰） ──
    if has_cardkit:
        content, img_elements = _extract_images_from_markdown(content)
        elements.extend(img_elements)
    for chunk in _split_long_text(content):
        if chunk.strip():
            elements.append({"tag": "markdown", "content": chunk})

    elements.extend(
        _build_footer_elements(
            footer_data,
            is_error,
            is_aborted,
            fields=footer_fields,
            show_label=footer_show_label,
        )
    )

    summary_text = (text or reasoning_text or "")[:120]
    summary_text = summary_text.replace("\n", " ").replace("```", "").strip()

    card: dict[str, Any] = {
        "config": {
            "wide_screen_mode": True,
            "update_multi": True,
            "locales": _LOCALES,
        },
    }
    if summary_text:
        card["config"]["summary"] = _build_summary(summary_text)

    if header_enabled:
        if is_error:
            card["header"] = _build_header("error")
        elif is_aborted:
            card["header"] = _build_header("stopped")
        else:
            card["header"] = _build_header("completed")

    if has_cardkit:
        card["schema"] = "2.0"
        card["config"]["streaming_mode"] = False
        card["body"] = {"elements": elements}
    else:
        card["elements"] = elements

    return card


def build_linear_complete_card(
    *,
    segments: list[Segment] | None = None,
    all_tool_steps: list[dict] | None = None,
    footer_data: dict | None = None,
    is_error: bool = False,
    is_aborted: bool = False,
    error_message: str = "",
    footer_fields: list[list[str]] | None = None,
    footer_show_label: bool = True,
    panel_expanded: bool = False,
    partial: bool = False,
    bg_review_messages: list[str] | None = None,
    header_enabled: bool = False,
    # ── UnifiedLinearState parameters (new code path) ──
    reasoning_rounds: list | None = None,
    current_reasoning_text: str = "",
    tool_steps: list[dict] | None = None,
    tool_elapsed_ms: float = 0,
    answer_text: str = "",
    show_reasoning: bool = True,
) -> dict[str, Any]:
    """线性模式完成态卡片.

    Supports two code paths:

    **Legacy path** (``segments`` provided):
        Iterates over Segment objects to build the card — used by the
        old segment-based LinearState.

    **Unified path** (``reasoning_rounds`` provided):
        Uses ``build_unified_panel()`` to render all reasoning rounds
        and tool steps inside a single collapsible panel, followed by
        the answer text.  This is the preferred path for
        ``UnifiedLinearState``.
    """
    # ── Unified code path ──
    if reasoning_rounds is not None:
        return _build_linear_complete_unified(
            reasoning_rounds=reasoning_rounds,
            current_reasoning_text=current_reasoning_text,
            tool_steps=tool_steps or [],
            tool_elapsed_ms=tool_elapsed_ms,
            answer_text=answer_text,
            show_reasoning=show_reasoning,
            footer_data=footer_data,
            is_error=is_error,
            is_aborted=is_aborted,
            error_message=error_message,
            footer_fields=footer_fields,
            footer_show_label=footer_show_label,
            panel_expanded=panel_expanded,
            partial=partial,
            bg_review_messages=bg_review_messages,
            header_enabled=header_enabled,
        )

    # ── Legacy segment-based code path ──
    assert segments is not None, "segments is required when reasoning_rounds is not provided"
    assert all_tool_steps is not None, "all_tool_steps is required when reasoning_rounds is not provided"
    elements: list[dict] = []
    has_answer = False

    for seg in segments:
        if seg.type == "reasoning":
            if seg.text:
                elements.append(_build_reasoning_panel(
                    seg.text, seg.elapsed_ms, expanded=panel_expanded,
                    element_id=None, text_element_id=None,
                ))
        elif seg.type == "tool":
            start = seg.tool_offset
            end = seg.tool_end_offset if seg.tool_end_offset else len(all_tool_steps)
            steps = all_tool_steps[start:end]
            if steps:
                elements.append(_build_tool_panel(steps, expanded=panel_expanded, element_id=None))
        elif seg.type == "answer" and seg.text:
            has_answer = True
            content = _downgrade_tables(optimize_markdown_style(seg.text))
            # ── 提取已解析图片为独立 img 元素 ──
            content, img_elements = _extract_images_from_markdown(content)
            elements.extend(img_elements)
            for chunk in _split_long_text(content):
                if chunk.strip():
                    elements.append({"tag": "markdown", "content": chunk})

    if not has_answer:
        elements.append({"tag": "markdown", "content": _T["done"][0]})

    # ── 错误/中断面板（线性模式下放在内容之后） ──
    if error_message:
        elements.append(_build_error_panel(
            error_message, is_aborted=is_aborted, expanded=panel_expanded,
        ))

    # ── Background review panel (completed card) ──
    if bg_review_messages:
        elements.append(_build_background_review_panel(
            bg_review_messages,
            expanded=panel_expanded,
        ))

    # ── Partial indicator (split card: content continues) ──
    if partial:
        elements.append({"tag": "hr"})
        en_text, zh_text = _T["partial_continues"]
        elements.append({
            "tag": "markdown",
            "content": f"▸ {en_text} ↩",
            "i18n_content": _i18n(f"▸ {en_text} ↩", f"▸ {zh_text} ↩"),
        })

    elements.extend(
        _build_footer_elements(
            footer_data,
            is_error,
            is_aborted,
            fields=footer_fields,
            show_label=footer_show_label,
        )
    )

    summary_text = ""
    for seg in reversed(segments):
        if seg.type in ("answer", "reasoning") and seg.text:
            summary_text = seg.text
            break
    summary = summary_text[:120].replace("\n", " ").replace("```", "").strip()

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "wide_screen_mode": True,
            "update_multi": True,
            "streaming_mode": False,
            "locales": _LOCALES,
        },
    }
    if summary:
        card["config"]["summary"] = _build_summary(summary)
    if header_enabled:
        if is_error:
            card["header"] = _build_header("error")
        elif is_aborted:
            card["header"] = _build_header("stopped")
        else:
            card["header"] = _build_header("completed")
    card["body"] = {"elements": elements}
    return card


def _build_linear_complete_unified(
    *,
    reasoning_rounds: list,
    current_reasoning_text: str = "",
    tool_steps: list[dict],
    tool_elapsed_ms: float = 0,
    answer_text: str = "",
    show_reasoning: bool = True,
    footer_data: dict | None = None,
    is_error: bool = False,
    is_aborted: bool = False,
    error_message: str = "",
    footer_fields: list[list[str]] | None = None,
    footer_show_label: bool = True,
    panel_expanded: bool = False,
    partial: bool = False,
    bg_review_messages: list[str] | None = None,
    header_enabled: bool = False,
) -> dict[str, Any]:
    """Build linear complete card using unified panel (internal helper)."""
    elements: list[dict] = []

    # ── Unified panel (reasoning + tools in a single collapsible) ──
    has_reasoning_or_tools = reasoning_rounds or current_reasoning_text or tool_steps
    if has_reasoning_or_tools:
        elements.append(build_unified_panel(
            reasoning_rounds=reasoning_rounds,
            current_reasoning_text=current_reasoning_text,
            tool_steps=tool_steps,
            tool_elapsed_ms=tool_elapsed_ms,
            show_reasoning=show_reasoning,
            expanded=panel_expanded,
        ))

    # ── Answer text ──
    if answer_text:
        content = _downgrade_tables(optimize_markdown_style(answer_text))
        content, img_elements = _extract_images_from_markdown(content)
        elements.extend(img_elements)
        for chunk in _split_long_text(content):
            if chunk.strip():
                elements.append({"tag": "markdown", "content": chunk})
    else:
        elements.append({"tag": "markdown", "content": _T["done"][0]})

    # ── 错误/中断面板 ──
    if error_message:
        elements.append(_build_error_panel(
            error_message, is_aborted=is_aborted, expanded=panel_expanded,
        ))

    # ── Background review panel ──
    if bg_review_messages:
        elements.append(_build_background_review_panel(
            bg_review_messages,
            expanded=panel_expanded,
        ))

    # ── Partial indicator (split card: content continues) ──
    if partial:
        elements.append({"tag": "hr"})
        en_text, zh_text = _T["partial_continues"]
        elements.append({
            "tag": "markdown",
            "content": f"▸ {en_text} ↩",
            "i18n_content": _i18n(f"▸ {en_text} ↩", f"▸ {zh_text} ↩"),
        })

    elements.extend(
        _build_footer_elements(
            footer_data,
            is_error,
            is_aborted,
            fields=footer_fields,
            show_label=footer_show_label,
        )
    )

    # ── Summary ──
    summary_text = answer_text
    if not summary_text and reasoning_rounds:
        summary_text = reasoning_rounds[-1].text if reasoning_rounds else ""
    summary = summary_text[:120].replace("\n", " ").replace("```", "").strip() if summary_text else ""

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "wide_screen_mode": True,
            "update_multi": True,
            "streaming_mode": False,
            "locales": _LOCALES,
        },
    }
    if summary:
        card["config"]["summary"] = _build_summary(summary)
    if header_enabled:
        if is_error:
            card["header"] = _build_header("error")
        elif is_aborted:
            card["header"] = _build_header("stopped")
        else:
            card["header"] = _build_header("completed")
    card["body"] = {"elements": elements}
    return card


def build_unified_complete_card(
    *,
    reasoning_rounds: list,  # list of ReasoningRound
    current_reasoning_text: str = "",
    tool_steps: list[dict] | None = None,
    tool_elapsed_ms: float = 0,
    answer_text: str = "",
    show_reasoning: bool = True,
    footer_data: dict | None = None,
    is_error: bool = False,
    is_aborted: bool = False,
    error_message: str = "",
    footer_fields: list[list[str]] | None = None,
    footer_show_label: bool = True,
    panel_expanded: bool = False,
    header_enabled: bool = False,
    panel_events: list[tuple[str, int]] | None = None,
    max_tool_steps: int = 20,
    max_reasoning_rounds: int = 20,
) -> dict[str, Any]:
    """Unified panel complete card — single panel for reasoning+tools, plus answer.

    Builds a complete (non-streaming) card with:

    1. **Unified panel** — all reasoning rounds and tool steps in one
       collapsible panel element (``UNIFIED_PANEL_ELEMENT_ID``).
    2. **Answer text** — markdown content with image extraction and
       long-text splitting.
    3. **Error panel** — if ``error_message`` is provided.
    4. **Background review panel** — if ``bg_review_messages`` is provided.
    5. **Footer** — metadata row(s).

    Parameters
    ----------
    reasoning_rounds : list[ReasoningRound]
        Finalised reasoning rounds.
    current_reasoning_text : str
        In-progress reasoning text not yet finalised into a round.
    tool_steps : list[dict] | None
        Tool step dicts consumed by :func:`_build_tool_step_elements`.
    tool_elapsed_ms : float
        Total elapsed time for tool execution in milliseconds.
    answer_text : str
        The main answer / response text.
    show_reasoning : bool
        Whether to render reasoning content inside the unified panel.
    footer_data : dict | None
        Key-value data for the footer row (model, elapsed, context, etc.).
    is_error : bool
        Whether the response ended with an API error.
    is_aborted : bool
        Whether the response was interrupted by the user.
    error_message : str
        Error or interrupt message to display.
    footer_fields : list[list[str]] | None
        Footer field layout (each inner list is a row of field names).
    footer_show_label : bool
        Whether to show labels alongside footer values.
    panel_expanded : bool
        Whether the unified panel starts expanded.
    header_enabled : bool
        Whether to include a card-level header.
    """
    tool_steps = tool_steps or []
    elements: list[dict] = []

    # ── Unified panel ──
    has_reasoning_or_tools = reasoning_rounds or current_reasoning_text or tool_steps
    if has_reasoning_or_tools:
        elements.append(build_unified_panel(
            reasoning_rounds=reasoning_rounds,
            current_reasoning_text=current_reasoning_text,
            tool_steps=tool_steps,
            tool_elapsed_ms=tool_elapsed_ms,
            show_reasoning=show_reasoning,
            expanded=panel_expanded,
            panel_events=panel_events,
            max_tool_steps=max_tool_steps,
            max_reasoning_rounds=max_reasoning_rounds,
        ))

    # ── Answer text ──
    if answer_text:
        content = _downgrade_tables(optimize_markdown_style(answer_text))
        content, img_elements = _extract_images_from_markdown(content)
        elements.extend(img_elements)
        for chunk in _split_long_text(content):
            if chunk.strip():
                elements.append({"tag": "markdown", "content": chunk})
    else:
        elements.append({"tag": "markdown", "content": _T["done"][0]})

    # ── 错误/中断面板 ──
    if error_message:
        elements.append(_build_error_panel(
            error_message, is_aborted=is_aborted, expanded=panel_expanded,
        ))

    # ── Background review panel ──
    bg_review_messages = footer_data.get("bg_review_messages") if footer_data else None
    if bg_review_messages:
        elements.append(_build_background_review_panel(
            bg_review_messages,
            expanded=panel_expanded,
        ))

    # ── Footer ──
    elements.extend(
        _build_footer_elements(
            footer_data,
            is_error,
            is_aborted,
            fields=footer_fields,
            show_label=footer_show_label,
        )
    )

    # ── Summary ──
    summary_text = answer_text
    if not summary_text and reasoning_rounds:
        summary_text = reasoning_rounds[-1].text if reasoning_rounds else ""
    summary = summary_text[:120].replace("\n", " ").replace("```", "").strip() if summary_text else ""

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "wide_screen_mode": True,
            "update_multi": True,
            "streaming_mode": False,
            "locales": _LOCALES,
        },
    }
    if summary:
        card["config"]["summary"] = _build_summary(summary)
    if header_enabled:
        if is_error:
            card["header"] = _build_header("error")
        elif is_aborted:
            card["header"] = _build_header("stopped")
        else:
            card["header"] = _build_header("completed")
    card["body"] = {"elements": elements}
    return card



