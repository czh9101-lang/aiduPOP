"""CardKit v2.0 — Primitive element builders: panels, footers, helpers."""



from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .i18n import _LOCALES, _T, _i18n, _t
from .md import (
    _downgrade_tables,
    _split_long_text,
    optimize_markdown_style,
)


__all__ = [
    # Element ID constants
    'STREAMING_ELEMENT_ID',
    'REASONING_ELEMENT_ID',
    'REASONING_TEXT_ELEMENT_ID',
    'TOOL_PANEL_ELEMENT_ID',
    '_LOADING_ELEMENT_ID',
    '_LOADING_HINT_ELEMENT_ID',
    '_LOADING_IMG_KEY',
    '_IMG_MD_PATTERN',
    # Element builders
    '_extract_images_from_markdown',
    '_collapsible_panel',
    '_streaming_element',
    '_loading_element',
    '_loading_hint_element',
    '_build_tool_panel',
    '_build_tool_step_elements',
    '_build_tool_step_title',
    '_build_tool_step_detail',
    '_build_tool_step_output',
    '_tool_status_info',
    '_format_code_block',
    '_longest_backtick_run',
    '_escape_md',
    '_build_reasoning_panel',
    '_build_error_panel',
    '_build_header',
    '_build_background_review_panel',
    '_build_footer_elements',
    'build_preservative_seal_actions',
    '_render_footer_field',
    '_compact',
    '_format_elapsed',
]

# 匹配 markdown 图片语法: ![alt](url)
_HEADER_STATES: dict[str, dict[str, str]] = {
    "streaming": {"template": "blue", "i18n_key": "processing_prefix"},
    "completed": {"template": "green", "i18n_key": "status_completed"},
    "error": {"template": "red", "i18n_key": "status_error"},
    "stopped": {"template": "red", "i18n_key": "status_stopped"},
}


def _build_header(status: str) -> dict[str, Any]:
    """Build card-level header — streaming blue / completed green / stopped red."""
    cfg = _HEADER_STATES.get(status, _HEADER_STATES["completed"])
    en_text, zh_text = _T[cfg["i18n_key"]]
    return {
        "title": {
            "tag": "plain_text",
            "content": en_text,
            "i18n_content": _i18n(en_text, zh_text),
        },
        "template": cfg["template"],
    }


_IMG_MD_PATTERN = re.compile(r"!\[([^\]]*)\]\((img_[^)\s]+)\)")


def _extract_images_from_markdown(text: str) -> tuple[str, list[dict]]:
    """从 markdown 文本中提取已解析的飞书图片，返回 (清理后的文本, img元素列表).

    将 ``![alt](img_v3_xxx)`` 格式的图片从文本中提取为独立的
    Card 2.0 ``tag: "img"`` 元素，图片从文本中移除以避免重复显示。

    仅处理 ``img_`` 前缀的 URL（已上传到飞书的图片 key）。
    """
    images: list[dict] = []

    def _replace(m: re.Match) -> str:
        alt = m.group(1)
        img_key = m.group(2)
        images.append({
            "tag": "img",
            "img_key": img_key,
            "scale_type": "fit_horizontal",
            "alt": {"tag": "plain_text", "content": alt},
            "corner_radius": "8px",
            "preview": True,
        })
        return ""

    cleaned = _IMG_MD_PATTERN.sub(_replace, text)
    # 清理图片移除后可能留下的空行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, images

if TYPE_CHECKING:
    from ..state.linear import Segment

STREAMING_ELEMENT_ID = "streaming_content"
REASONING_ELEMENT_ID = "reasoning_content"
REASONING_TEXT_ELEMENT_ID = "reasoning_text"
TOOL_PANEL_ELEMENT_ID = "tool_panel"
_LOADING_ELEMENT_ID = "loading_icon"
_LOADING_HINT_ELEMENT_ID = "context_loading_hint"
_LOADING_IMG_KEY = "img_v3_02vb_496bec09-4b43-4773-ad6b-0cdd103cd2bg"


def _collapsible_panel(
    *,
    expanded: bool,
    title_el: dict,
    elements: list[dict],
    vertical_spacing: str = "4px",
    icon_position: str = "right",
) -> dict:
    icon_el = {
        "tag": "standard_icon",
        "token": "down-small-ccm_outlined",
        "size": "16px 16px",
    }
    if icon_position == "right":
        icon_el["color"] = "grey"
    return {
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": {
            "title": title_el,
            "vertical_align": "center",
            "icon": icon_el,
            "icon_position": icon_position,
            "icon_expanded_angle": -180,
        },
        "border": {"color": "grey", "corner_radius": "5px"},
        "vertical_spacing": vertical_spacing,
        "padding": "8px 8px 8px 8px",
        "elements": elements,
    }


def _streaming_element(content: str = "", *, element_id: str = STREAMING_ELEMENT_ID) -> dict:
    return {
        "tag": "markdown",
        "content": content,
        "text_align": "left",
        "text_size": "normal_v2",
        "margin": "0px 0px 0px 0px",
        "element_id": element_id,
    }


def _loading_element() -> dict:
    return {
        "tag": "markdown",
        "content": " ",
        "icon": {
            "tag": "custom_icon",
            "img_key": _LOADING_IMG_KEY,
            "size": "16px 16px",
        },
        "element_id": _LOADING_ELEMENT_ID,
    }


def _loading_hint_element() -> dict:
    """上下文加载占位元素 — 首卡创建后插入，首字即显时删除."""
    return {
        "tag": "div",
        "icon": {
            "tag": "standard_icon",
            "token": "time_outlined",
            "size": "16px 16px",
        },
        "text": {
            "tag": "lark_md",
            "content": _T["loading_context"][0],
            "i18n_content": _t("loading_context"),
        },
        "element_id": _LOADING_HINT_ELEMENT_ID,
    }


def _build_tool_panel(
    steps: list[dict],
    elapsed_ms: float = 0,
    *,
    expanded: bool = True,
    element_id: str | None = TOOL_PANEL_ELEMENT_ID,
) -> dict:
    en_t, zh_t = _T["tool_use"]
    en_parts, zh_parts = [en_t], [zh_t]
    if steps:
        tpl_en, tpl_zh = _T["steps"]
        en_parts.append(tpl_en.format(len(steps), "s" if len(steps) > 1 else ""))
        zh_parts.append(tpl_zh.format(len(steps), ""))
    if elapsed_ms > 0:
        en_parts.append(f"({_format_elapsed(elapsed_ms)})")
        zh_parts.append(f"({_format_elapsed(elapsed_ms)})")

    children: list[dict] = []
    for s in steps:
        children.extend(_build_tool_step_elements(s))

    panel = _collapsible_panel(
        expanded=expanded,
        title_el={
            "tag": "plain_text",
            "content": f"🛠️ {' · '.join(en_parts)}",
            "i18n_content": _i18n(f"🛠️ {' · '.join(en_parts)}", f"🛠️ {' · '.join(zh_parts)}"),
            "text_color": "grey",
            "text_size": "notation",
        },
        elements=children,
    )
    if element_id:
        panel["element_id"] = element_id
    return panel


def _build_tool_step_elements(step: dict) -> list[dict]:
    elements: list[dict] = [_build_tool_step_title(step)]
    detail = _build_tool_step_detail(step)
    if detail:
        elements.append(detail)
    output = _build_tool_step_output(step)
    if output:
        elements.append(output)
    return elements


def _build_tool_step_title(step: dict) -> dict:
    status = step.get("status", "running")
    status_info = _tool_status_info(status)
    title = step.get("title", step.get("name", "tool"))
    content = f"**{_escape_md(title)}** · <font color='{status_info['color']}'>{status_info['label']}</font>"
    return {
        "tag": "div",
        "icon": {
            "tag": "standard_icon",
            "token": step.get("icon", "tool_02"),
            "color": "grey",
        },
        "text": {
            "tag": "lark_md",
            "content": content,
            "text_size": "notation",
        },
    }


def _build_tool_step_detail(step: dict) -> dict | None:
    detail = step.get("detail", "").strip()
    if not detail:
        return None
    return {
        "tag": "div",
        "margin": "0px 0px 0px 22px",
        "text": {
            "tag": "plain_text",
            "content": detail,
            "text_color": "grey",
            "text_size": "notation",
        },
    }


def _build_tool_step_output(step: dict) -> dict | None:
    error_block = step.get("error_block")
    result_block = step.get("result_block")

    lines: list[str] = []
    if error_block:
        lines.append("**Error**")
        lines.append(
            error_block.get("fenced")
            or _format_code_block(error_block.get("content", ""), error_block.get("language", "text"))
        )
    elif result_block:
        lines.append("**Result**")
        lines.append(
            result_block.get("fenced")
            or _format_code_block(result_block.get("content", ""), result_block.get("language", "json"))
        )

    if not lines:
        return None

    return {
        "tag": "div",
        "margin": "0px 0px 0px 22px",
        "text": {
            "tag": "lark_md",
            "content": "\n".join(lines),
            "text_size": "notation",
        },
    }


def _tool_status_info(status: str) -> dict[str, str]:
    return {
        "running": {"label": "Running", "color": "turquoise"},
        "success": {"label": "Succeeded", "color": "green"},
        "error": {"label": "Failed", "color": "red"},
    }.get(status, {"label": status.capitalize(), "color": "grey"})


def _format_code_block(content: str, language: str) -> str:
    normalized = content.replace("\r\n", "\n").strip()
    fence = "`" * max(3, _longest_backtick_run(normalized) + 1)
    return f"{fence}{language}\n{normalized}\n{fence}"


def _longest_backtick_run(value: str) -> int:
    matches = re.findall(r"`+", value)
    return max((len(m) for m in matches), default=0)


def _escape_md(value: str) -> str:
    return re.sub(r"([`*_{}\[\]<>])", r"\\\1", value.replace("\\", "\\\\"))


def _build_reasoning_panel(
    text: str, elapsed_ms: float = 0, *, expanded: bool = False, element_id: str | None = None,
    text_element_id: str | None = REASONING_TEXT_ELEMENT_ID,
) -> dict:
    if elapsed_ms > 0:
        d = _format_elapsed(elapsed_ms)
        en_label, zh_label = _T["thought_for"][0].format(d), _T["thought_for"][1].format(d)
    elif not text.strip():
        en_label, zh_label = _T["thinking_panel"]
    else:
        en_label, zh_label = _T["thought"]
    panel = _collapsible_panel(
        expanded=expanded,
        title_el={
            "tag": "plain_text",
            "content": f"💭 {en_label}",
            "i18n_content": _i18n(f"💭 {en_label}", f"💭 {zh_label}"),
            "text_color": "grey",
            "text_size": "notation",
        },
        elements=[{
            "tag": "markdown",
            "content": text,
            "text_size": "notation",
            **({"element_id": text_element_id} if text_element_id else {}),
        }],
        vertical_spacing="8px",
    )
    if element_id:
        panel["element_id"] = element_id
    return panel


def _build_error_panel(
    error_message: str,
    *,
    is_aborted: bool = False,
    expanded: bool = True,
) -> dict:
    """Build a collapsible error/interrupt panel — visually consistent with
    reasoning and tool panels.

    - Error (API failure, tool crash): red border, expanded by default
    - Interrupt (/stop or new message): orange border, expanded by default
    """
    if is_aborted:
        en_label, zh_label = _T["interrupt_panel"]
        border_color = "orange"
    else:
        en_label, zh_label = _T["error_panel"]
        border_color = "red"

    panel = _collapsible_panel(
        expanded=expanded,
        title_el={
            "tag": "plain_text",
            "content": en_label,
            "i18n_content": _i18n(en_label, zh_label),
            "text_color": "red" if not is_aborted else "orange",
            "text_size": "notation",
        },
        elements=[{
            "tag": "markdown",
            "content": error_message,
            "text_size": "notation",
        }],
        vertical_spacing="8px",
    )
    # Override border color to red/orange for visual emphasis
    panel["border"]["color"] = border_color
    return panel


def _build_background_review_panel(
    messages: list[str],
    *,
    expanded: bool = True,
    element_id: str | None = None,
) -> dict[str, Any]:
    """构建后台审查进度面板（可折叠）."""
    en_title, zh_title = _T["bg_review_panel"]
    children: list[dict] = []
    for msg in messages:
        children.append({
            "tag": "markdown",
            "content": msg,
        })
    if not messages:
        children.append({"tag": "markdown", "content": " "})
    panel = _collapsible_panel(
        expanded=expanded,
        title_el={
            "tag": "plain_text",
            "content": f"🔄 {en_title}",
            "i18n_content": _i18n(f"🔄 {en_title}", f"🔄 {zh_title}"),
            "text_color": "grey",
            "text_size": "notation",
        },
        elements=children,
    )
    if element_id:
        panel["element_id"] = element_id
    return panel


def _build_footer_elements(
    footer_data: dict | None,
    is_error: bool = False,
    is_aborted: bool = False,
    fields: list[list[str]] | None = None,
    show_label: bool = False,
) -> list[dict]:
    if fields is None:
        fields = [["status", "elapsed", "context", "model"]]

    data = footer_data or {}
    en_lines: list[str] = []
    zh_lines: list[str] = []
    for row in fields:
        en_parts: list[str] = []
        zh_parts: list[str] = []
        for field in row:
            en, zh = _render_footer_field(field, data, is_error, is_aborted, show_label)
            if en:
                en_parts.append(en)
                if zh:
                    zh_parts.append(zh)
        if en_parts:
            en_lines.append(" · ".join(en_parts))
            zh_lines.append(" · ".join(zh_parts))

    if not en_lines:
        return []

    en_content = "\n".join(en_lines)
    zh_content = "\n".join(zh_lines)
    if is_error:
        en_content = f"<font color='red'>{en_content}</font>"
        zh_content = f"<font color='red'>{zh_content}</font>"

    return [
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": en_content,
            "i18n_content": _i18n(en_content, zh_content),
            "text_size": "notation",
        },
    ]


def build_preservative_seal_actions(
    *,
    partial: bool = False,
    footer_data: dict | None = None,
    is_error: bool = False,
    is_aborted: bool = False,
    error_message: str = "",
    footer_fields: list[list[str]] | None = None,
    footer_show_label: bool = False,
) -> list[dict]:
    """构建保留式封卡的 batch_update actions.

    生成增量操作：删除 loading icon + 添加 partial indicator 或 footer。
    不重建整卡，避免 1→N+2M 的元素爆炸。

    操作顺序：
    1. insert_before loading_icon: 添加 error panel（如有）
    2. insert_before loading_icon: 添加 partial indicator 或 footer
    3. delete_element: 删除 loading_icon

    所有 add_elements 都用 insert_before loading_icon 定位，
    然后删除 loading_icon，最终效果是新增元素出现在卡片底部。
    """
    actions: list[dict] = []

    # ── Error/interrupt panel (if any) ──
    if error_message:
        actions.append({
            "action": "add_elements",
            "params": {
                "type": "insert_before",
                "target_element_id": _LOADING_ELEMENT_ID,
                "elements": [_build_error_panel(
                    error_message, is_aborted=is_aborted, expanded=True,
                )],
            },
        })

    # ── Partial indicator or footer ──
    if partial:
        en_text, zh_text = _T["partial_continues"]
        partial_elements = [
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": f"▸ {en_text} ↩",
                "i18n_content": _i18n(f"▸ {en_text} ↩", f"▸ {zh_text} ↩"),
            },
        ]
        actions.append({
            "action": "add_elements",
            "params": {
                "type": "insert_before",
                "target_element_id": _LOADING_ELEMENT_ID,
                "elements": partial_elements,
            },
        })
    else:
        footer_elements = _build_footer_elements(
            footer_data,
            is_error=is_error,
            is_aborted=is_aborted,
            fields=footer_fields,
            show_label=footer_show_label,
        )
        if footer_elements:
            actions.append({
                "action": "add_elements",
                "params": {
                    "type": "insert_before",
                    "target_element_id": _LOADING_ELEMENT_ID,
                    "elements": footer_elements,
                },
            })

    # ── Delete context loading hint (if still present) ──
    # 占位提示在首字即显时通常已被删除，但如果卡片在 answer
    # 到来前就被封（如超限拆卡），占位提示可能仍在，需要兜底删除。
    actions.append({
        "action": "delete_elements",
        "params": {
            "element_ids": [_LOADING_HINT_ELEMENT_ID],
        },
    })

    # ── Delete loading icon ──
    actions.append({
        "action": "delete_elements",
        "params": {
            "element_ids": [_LOADING_ELEMENT_ID],
        },
    })

    return actions


def _render_footer_field(
    name: str,
    data: dict,
    is_error: bool,
    is_aborted: bool,
    show_label: bool,
) -> tuple[str | None, str | None]:
    if name == "status":
        if is_error:
            return _T["status_error"]
        if is_aborted:
            return _T["status_stopped"]
        return _T["status_completed"]

    if name == "elapsed":
        duration = data.get("duration", 0)
        if isinstance(duration, (int, float)) and duration > 0:
            val = _format_elapsed(duration * 1000)
            if show_label:
                return _T["elapsed"][0].format(val), _T["elapsed"][1].format(val)
            return val, val
        return None, None

    if name == "model":
        v = data.get("model") or None
        return v, v

    if name == "tokens":
        input_t = data.get("input_tokens", 0) or 0
        output_t = data.get("output_tokens", 0) or 0
        reasoning_t = data.get("reasoning_tokens", 0) or 0
        if input_t or output_t:
            v = f"↑ {_compact(input_t)} ↓ {_compact(output_t)}"
            if reasoning_t:
                v += f" 💭 {_compact(reasoning_t)}"
            return v, v
        return None, None

    if name == "context":
        used = data.get("context_used", 0) or 0
        max_c = data.get("context_max", 0) or 0
        if max_c:
            pct = int(used / max_c * 100)
            val = f"{_compact(used)}/{_compact(max_c)} ({pct}%)"
            if show_label:
                return _T["context"][0].format(val), _T["context"][1].format(val)
            return val, val
        return None, None

    if name == "api_calls":
        v = data.get("api_calls", 0) or 0
        if v:
            en_val, zh_val = _T["api_calls"]
            if show_label:
                return f"{en_val} {v}", f"{zh_val} {v}"
            return str(v), str(v)
        return None, None

    if name == "history_offset":
        v = data.get("history_offset", 0) or 0
        if v:
            en_val, zh_val = _T["history_offset"]
            if show_label:
                return f"{en_val} {v}", f"{zh_val} {v}"
            return str(v), str(v)
        return None, None

    if name == "compression_exhausted":
        v = data.get("compression_exhausted", False)
        if v:
            en_val, zh_val = _T["compression_exhausted"]
            return en_val, zh_val
        return None, None

    if name == "cache":
        cache_read = data.get("cache_read_tokens", 0) or 0
        input_total = data.get("input_tokens", 0) or 0
        if cache_read and input_total:
            hit_pct = int(cache_read / input_total * 100)
            v = f"{_compact(cache_read)}/{_compact(input_total)} ({hit_pct}%)"
            if show_label:
                return _T["cache"][0].format(v), _T["cache"][1].format(v)
            return v, v
        return None, None

    if name == "cost":
        cost_usd = data.get("estimated_cost_usd", 0) or 0
        cost_status = data.get("cost_status", "unknown")
        if cost_status == "included":
            return _T["cost_included"]
        if cost_status in ("actual", "estimated") and cost_usd:
            # Format: $0.023 for small values, $1.50 for larger
            if cost_usd < 0.01:
                val = f"${cost_usd:.4f}"
            elif cost_usd < 1:
                val = f"${cost_usd:.3f}"
            else:
                val = f"${cost_usd:.2f}"
            key = "cost_actual" if cost_status == "actual" else "cost_estimated"
            en_val, zh_val = _T[key]
            if show_label:
                return f"Cost {en_val.format(val.lstrip('$'))}", f"费用 {zh_val.format(val.lstrip('$'))}"
            return en_val.format(val.lstrip('$')), zh_val.format(val.lstrip('$'))
        return None, None

    return None, None


def _compact(n: int) -> str:
    if n >= 1_000_000:
        m = n / 1_000_000
        return f"{int(m)}M" if m >= 100 else f"{m:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _format_elapsed(ms: float) -> str:
    seconds = ms / 1000
    return f"{seconds:.1f}s" if seconds < 60 else f"{int(seconds // 60)}m {int(seconds % 60)}s"
