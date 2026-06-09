"""CardKit v2.0 — Card assemblers: streaming, complete, and linear cards."""



from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .cardkit_elements import (
    REASONING_ELEMENT_ID,
    STREAMING_ELEMENT_ID,
    TOOL_PANEL_ELEMENT_ID,
    _build_background_review_panel,
    _build_error_panel,
    _build_footer_elements,
    _build_reasoning_panel,
    _build_tool_panel,
    _collapsible_panel,
    _extract_images_from_markdown,
    _loading_element,
    _streaming_element,
)
from .cardkit_i18n import _LOCALES, _T, _i18n, _t
from .cardkit_md import (
    _downgrade_tables,
    _split_long_text,
    optimize_markdown_style,
)

if TYPE_CHECKING:
    from .linear import Segment



__all__ = [
    'build_streaming_tool_use_pending_panel',
    'build_streaming_card_v2',
    'build_im_fallback_card',
    'build_streaming_card',
    'build_complete_card',
    'build_linear_complete_card',
    'build_linear_compact_seal_card',
]

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
) -> dict[str, Any]:
    """CardKit 2.0 流式占位卡片 — 含工具面板 + streaming + loading 元素."""
    elements: list[dict] = []

    if show_reasoning:
        elements.append(
            _build_reasoning_panel(" ", expanded=streaming_panel_expanded, element_id=REASONING_ELEMENT_ID)
        )

    if show_tool_use:
        if tool_steps:
            elements.append(_build_tool_panel(tool_steps, elapsed_ms, expanded=streaming_panel_expanded))
        else:
            elements.append(build_streaming_tool_use_pending_panel())

    if show_streaming_element:
        elements.append(_streaming_element())
    elements.append(_loading_element())

    return {
        "schema": "2.0",
        "config": {
            "streaming_mode": True,
            "streaming_config": {
                "print_frequency_ms": {"default": 15},
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

    summary = (text or reasoning_text or "")[:120]
    summary = summary.replace("\n", " ").replace("```", "").strip()

    card: dict[str, Any] = {
        "config": {
            "wide_screen_mode": True,
            "update_multi": True,
            "locales": _LOCALES,
        },
    }
    if summary:
        card["config"]["summary"] = {"content": summary}

    if has_cardkit:
        card["schema"] = "2.0"
        card["config"]["streaming_mode"] = False
        card["body"] = {"elements": elements}
    else:
        card["elements"] = elements

    return card


def build_linear_complete_card(
    *,
    segments: list[Segment],
    all_tool_steps: list[dict],
    footer_data: dict | None = None,
    is_error: bool = False,
    is_aborted: bool = False,
    error_message: str = "",
    footer_fields: list[list[str]] | None = None,
    footer_show_label: bool = True,
    panel_expanded: bool = False,
    partial: bool = False,
    bg_review_messages: list[str] | None = None,
) -> dict[str, Any]:
    """线性模式完成态卡片 — 按 segments 顺序渲染.

    partial=True 时，在卡片底部添加"内容未完"提示（用于拆卡封存的非末尾卡片）。
    """
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
        card["config"]["summary"] = {"content": summary}
    card["body"] = {"elements": elements}
    return card


def build_linear_compact_seal_card(
    *,
    segments: list[Segment],
    all_tool_steps: list[dict],
    panel_expanded: bool = False,
    partial: bool = False,
) -> dict[str, Any]:
    """Compact seal card — preserves all panel types but truncates content to reduce elements.

    Used when the full seal_card exceeds the 200-element limit (300305).
    Keeps reasoning/tool/answer panels but with truncated content to reduce elements.

    Progressive degradation level 1: full seal → compact seal → minimal seal.
    """
    elements: list[dict] = []
    has_answer = False

    _MAX_REASONING_CHARS = 2000
    _MAX_ANSWER_CHARS = 4000

    for seg in segments:
        if seg.type == "reasoning":
            if seg.text:
                truncated_text = seg.text[:_MAX_REASONING_CHARS]
                if len(seg.text) > _MAX_REASONING_CHARS:
                    truncated_text += "\n\n... (truncated)"
                elements.append(_build_reasoning_panel(
                    truncated_text, seg.elapsed_ms, expanded=panel_expanded,
                    element_id=None, text_element_id=None,
                ))
        elif seg.type == "tool":
            start = seg.tool_offset
            end = seg.tool_end_offset if seg.tool_end_offset else len(all_tool_steps)
            steps = all_tool_steps[start:end]
            if steps:
                # Compact: only keep step titles, remove detail and result_block
                compact_steps = []
                for s in steps:
                    compact_steps.append({
                        "name": s.get("name", "tool"),
                        "title": s.get("title", s.get("name", "tool")),
                        "status": s.get("status", "success"),
                        "icon": s.get("icon", "tool_02"),
                    })
                elements.append(_build_tool_panel(compact_steps, expanded=panel_expanded, element_id=None))
        elif seg.type == "answer" and seg.text:
            has_answer = True
            content = _downgrade_tables(optimize_markdown_style(seg.text))
            content, img_elements = _extract_images_from_markdown(content)
            # Don't add img elements in compact mode to save element count
            truncated = content[:_MAX_ANSWER_CHARS]
            if len(content) > _MAX_ANSWER_CHARS:
                truncated += "\n\n... (truncated)"
            for chunk in _split_long_text(truncated):
                if chunk.strip():
                    elements.append({"tag": "markdown", "content": chunk})

    if not has_answer:
        elements.append({"tag": "markdown", "content": _T["done"][0]})

    # ── Partial indicator (split card: content continues) ──
    if partial:
        elements.append({"tag": "hr"})
        en_text, zh_text = _T["partial_continues"]
        elements.append({
            "tag": "markdown",
            "content": f"▸ {en_text} ↩",
            "i18n_content": _i18n(f"▸ {en_text} ↩", f"▸ {zh_text} ↩"),
        })

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "wide_screen_mode": True,
            "update_multi": True,
            "streaming_mode": False,
            "locales": _LOCALES,
        },
    }
    card["body"] = {"elements": elements}
    return card
