"""CardKit v2.0 — Specialized cards: cron, gateway, clarify, confirmation."""

from __future__ import annotations

import ast
from typing import Any

from .i18n import _LOCALES, _T, _i18n, _t
from .elements import _escape_md
from .md import (
    _MAX_CRON_TABLES,
    _downgrade_tables,
    _split_long_text,
    optimize_markdown_style,
)

__all__ = [
    'build_cron_card',
    'build_gateway_card',
    'build_slash_confirm_card',
    'build_slash_confirm_resolved_card',
    'build_clarify_card',
    'build_clarify_submitted_card',
    'build_clarify_confirmed_card',
    'build_clarify_retired_card',
    'normalize_clarify_choices',
]

_CLARIFY_DICT_FIELD_PRIORITY = (
    "label", "description", "text", "title",
    "name", "path", "value", "id",
)

_CLARIFY_MAX_CHOICE_LEN = 80

def _normalize_choice(choice: Any) -> str:
    """Normalize clarify choice to readable string. Handles: plain string,
    dict-repr string (parsed via ast.literal_eval), real dict. Never raises."""
    if choice is None:
        return ""
    if not isinstance(choice, str):
        if isinstance(choice, dict):
            return _extract_readable_from_dict(choice)
        if isinstance(choice, (list, tuple)):
            parts = [_normalize_choice(x) for x in choice]
            return " ".join(p for p in parts if p)[:_CLARIFY_MAX_CHOICE_LEN]
        choice = str(choice)

    text = choice.strip()
    if not text:
        return ""

    # Parse dict-repr strings: starts with { ends with }.
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            extracted = _extract_readable_from_dict(parsed)
            if extracted:
                text = extracted

    if len(text) > _CLARIFY_MAX_CHOICE_LEN:
        text = text[: _CLARIFY_MAX_CHOICE_LEN - 1] + "…"

    return text

def _extract_readable_from_dict(d: dict) -> str:
    """Extract readable string field from dict (priority order, strings only)."""
    for field in _CLARIFY_DICT_FIELD_PRIORITY:
        val = d.get(field)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""

def normalize_clarify_choices(choices: list[str] | None) -> list[str]:
    """Normalize choices for display + AI resolution. Filters empty."""
    if not choices:
        return []
    normalized = []
    for c in choices:
        n = _normalize_choice(c)
        if n:
            normalized.append(n)
    return normalized

def build_cron_card(content: str) -> dict[str, Any]:
    """Cron 推送用的极简静态卡片 — schema 2.0，仅 markdown 内容."""
    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {"locales": _LOCALES},
        "body": {"elements": []},
    }
    if not content.strip():
        return card
    summary = content[:120].replace("\n", " ").replace("```", "").strip()
    if summary:
        card["config"]["summary"] = {"content": summary}
    for chunk in _split_long_text(_downgrade_tables(optimize_markdown_style(content), limit=_MAX_CRON_TABLES)):
        if chunk.strip():
            card["body"]["elements"].append({"tag": "markdown", "content": chunk})
    return card

def build_gateway_card(content: str, *, category: str = "", status_label: str = "", status_emoji: str = "") -> dict[str, Any]:
    """Gateway-internal message card — lightweight, static, no streaming. For slash
    command replies, auth, session, errors. category retained for reaction routing."""
    elements: list[dict] = []

    if status_label and status_emoji:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": f"{status_emoji} {status_label}",
                "text_color": "turquoise",
                "text_size": "notation",
            },
        })

    if content.strip():
        for chunk in _split_long_text(_downgrade_tables(optimize_markdown_style(content), limit=_MAX_CRON_TABLES)):
            if chunk.strip():
                elements.append({"tag": "markdown", "content": chunk})

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {"locales": _LOCALES},
        "body": {"elements": elements},
    }

    summary = content[:120].replace("\n", " ").replace("```", "").strip() if content.strip() else ""
    if summary:
        card["config"]["summary"] = {"content": summary}

    return card


def build_slash_confirm_card(
    *,
    title: str,
    message: str,
    session_key: str,
    confirm_id: str,
) -> dict[str, Any]:
    """Build a native pending slash confirmation card.

    Feishu rejects the legacy action-plus-button combination for this callback
    path. Keep this deliberately to one schema-2.0 select_static element: the
    selected option is returned by Feishu while the callback value carries the
    opaque session and confirmation identifiers.
    """
    en_once, zh_once = _T["slash_confirm_once"]
    en_cancel, zh_cancel = _T["slash_confirm_cancel"]
    en_placeholder, zh_placeholder = _T["slash_confirm_select_placeholder"]

    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "icon": {
                "tag": "standard_icon",
                "token": "info_outlined",
                "size": "20px 20px",
                "color": "blue",
            },
            "text": {
                "tag": "lark_md",
                "content": f"**{_escape_md(title)}**",
            },
        },
    ]
    if message:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": _escape_md(message),
            },
        })

    # Do not replace this select with an action/button group: that schema has
    # been rejected by Feishu IM in the deployed Hermes callback path.
    elements.append({
        "tag": "select_static",
        "element_id": "slash_confirm_select",
        "placeholder": {
            "tag": "plain_text",
            "content": en_placeholder,
            "i18n_content": _i18n(en_placeholder, zh_placeholder),
        },
        "options": [
            {
                "text": {
                    "tag": "plain_text",
                    "content": en_once,
                    "i18n_content": _i18n(en_once, zh_once),
                },
                "value": "once",
            },
            {
                "text": {
                    "tag": "plain_text",
                    "content": en_cancel,
                    "i18n_content": _i18n(en_cancel, zh_cancel),
                },
                "value": "cancel",
            },
        ],
        "behaviors": [{
            "type": "callback",
            "value": {
                "hermes_slash_confirm_action": "select",
                "session_key": session_key,
                "confirm_id": confirm_id,
            },
        }],
    })

    return {
        "schema": "2.0",
        "config": {
            "streaming_mode": False,
            "locales": _LOCALES,
        },
        "body": {"elements": elements},
    }


def build_slash_confirm_resolved_card(
    *,
    title: str,
    message: str,
    choice: str,
) -> dict[str, Any]:
    """Build the immutable terminal state for a slash confirmation."""
    choice_text = str(choice).strip()
    if choice_text == "once":
        en_choice, zh_choice = _T["slash_confirm_resolved_once"]
        icon_token, icon_color = "resolve_filled", "green"
    elif choice_text == "cancel":
        en_choice, zh_choice = _T["slash_confirm_resolved_cancel"]
        icon_token, icon_color = "info_outlined", "grey"
    else:
        # choice normally comes from the two fixed options above, but keep the
        # builder safe and diagnosable if an old card supplies another value.
        safe_choice = _escape_md(choice_text) if choice_text else "-"
        en_template, zh_template = _T["slash_confirm_resolved"]
        en_choice = en_template.format(safe_choice)
        zh_choice = zh_template.format(safe_choice)
        icon_token, icon_color = "info_outlined", "grey"

    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "icon": {
                "tag": "standard_icon",
                "token": icon_token,
                "size": "20px 20px",
                "color": icon_color,
            },
            "text": {
                "tag": "lark_md",
                "content": f"**{_escape_md(title)}**",
            },
        },
    ]
    if message:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": _escape_md(message),
            },
        })
    elements.append({
        "tag": "div",
        "icon": {
            "tag": "standard_icon",
            "token": icon_token,
            "size": "16px 16px",
            "color": icon_color,
        },
        "text": {
            "tag": "lark_md",
            "content": en_choice,
            "i18n_content": _i18n(en_choice, zh_choice),
        },
    })

    return {
        "schema": "2.0",
        "config": {
            "streaming_mode": False,
            "locales": _LOCALES,
        },
        "body": {"elements": elements},
    }


def build_clarify_card(
    *,
    question: str,
    choices: list[str] | None = None,
    clarify_id: str = "",
    multi_select: bool = False,
) -> dict[str, Any]:
    """构建 Clarify 待选择态卡片 (State 1: Pending). 三态: 标题/选项列表/快速选择下拉/
    自定义输入. choices 经 normalize + escape for lark_md. 单选使用 select_static；多选
    使用飞书原生 multi_select_static + 根级 form，避免伪造 select_static 多选字段."""
    elements: list[dict] = []

    elements.append({
        "tag": "div",
        "icon": {
            "tag": "standard_icon",
            "token": "info_outlined",
            "size": "20px 20px",
            "color": "blue",
        },
        "text": {
            "tag": "lark_md",
            "content": f"**{_escape_md(question)}**",
        },
    })

    # Defense in depth: adapter also normalizes, but card builders must be safe.
    normalized_choices = normalize_clarify_choices(choices)

    if normalized_choices:
        option_lines = []
        for i, choice in enumerate(normalized_choices):
            label = chr(ord("A") + i) if i < 26 else str(i + 1)
            option_lines.append(f"{label}. {_escape_md(choice)}")
        options_md = "\n".join(option_lines)
        elements.append({
            "tag": "markdown",
            "content": options_md,
        })

        # All dropdown labels are plain_text, never lark_md.
        options: list[dict] = []
        for i, choice in enumerate(normalized_choices):
            label = chr(ord("A") + i) if i < 26 else str(i + 1)
            options.append({
                "text": {"tag": "plain_text", "content": f"{label}. {choice}"},
                "value": str(i),
            })

        en_placeholder, zh_placeholder = _T["clarify_select_placeholder"]
        if multi_select:
            # CardKit 2.0 defines native multi-select as multi_select_static.
            # It requires a form container and a submit button; a bare
            # select_static with a made-up "multi_select" field is invalid.
            en_submit, zh_submit = _T["clarify_multi_submit"]
            elements.append({
                "tag": "form",
                "name": "clarify_multi_form",
                "element_id": "clarify_multi_form",
                "elements": [
                    {
                        "tag": "multi_select_static",
                        "element_id": "clarify_multi_select",
                        "name": "clarify_multi_select",
                        "placeholder": {
                            "tag": "plain_text",
                            "content": en_placeholder,
                            "i18n_content": _i18n(en_placeholder, zh_placeholder),
                        },
                        "options": options,
                    },
                    {
                        "tag": "button",
                        "element_id": "clarify_multi_submit",
                        "name": "clarify_multi_submit",
                        "form_action_type": "submit",
                        "type": "primary",
                        "text": {
                            "tag": "plain_text",
                            "content": en_submit,
                            "i18n_content": _i18n(en_submit, zh_submit),
                        },
                    },
                ],
            })
        else:
            select_el: dict[str, Any] = {
                "tag": "select_static",
                "element_id": "clarify_select",
                "placeholder": {
                    "tag": "plain_text",
                    "content": en_placeholder,
                    "i18n_content": _i18n(en_placeholder, zh_placeholder),
                },
                "options": options,
                "behaviors": [{
                    "type": "callback",
                    "value": {
                        "hermes_clarify_action": "select",
                        "clarify_id": clarify_id,
                    },
                }],
            }
            elements.append(select_el)

    en_input_ph, zh_input_ph = _T["clarify_input_placeholder"]
    input_el: dict[str, Any] = {
        "tag": "input",
        "element_id": "clarify_input",
        "placeholder": {
            "tag": "plain_text",
            "content": en_input_ph,
            "i18n_content": _i18n(en_input_ph, zh_input_ph),
        },
        "max_length": 500,
        "name": "clarify_input",
        "behaviors": [{
            "type": "callback",
            "value": {
                "hermes_clarify_action": "input_submit",
                "clarify_id": clarify_id,
            },
        }],
    }
    elements.append(input_el)

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "streaming_mode": False,
            "locales": _LOCALES,
        },
        "body": {"elements": elements},
    }
    return card

def build_clarify_submitted_card(*, question: str, selected: str, clarify_id: str = "") -> dict[str, Any]:
    """构建 Clarify 已提交态卡片 (State 2: Submitted/Soft Lock). 标题 + 用户选择 +
    "已提交" 提示 + 重试按钮."""
    # Escape selected for lark_md (rendered inside "已选择: {}" template).
    safe_selected = _escape_md(selected)
    en_selected, zh_selected = _T["clarify_selected"]
    en_sel_label = en_selected.format(safe_selected)
    zh_sel_label = zh_selected.format(safe_selected)

    en_submitted, zh_submitted = _T["clarify_submitted"]
    en_retry, zh_retry = _T["clarify_retry"]

    elements: list[dict] = [
        {
            "tag": "div",
            "icon": {
                "tag": "standard_icon",
                "token": "lock_outlined",
                "size": "20px 20px",
                "color": "orange",
            },
            "text": {
                "tag": "lark_md",
                "content": f"**{_escape_md(question)}**",
            },
        },
        {
            "tag": "div",
            "icon": {
                "tag": "standard_icon",
                "token": "lock_outlined",
                "size": "16px 16px",
                "color": "orange",
            },
            "text": {
                "tag": "lark_md",
                "content": en_sel_label,
                "i18n_content": _i18n(en_sel_label, zh_sel_label),
            },
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"*{en_submitted}*",
                "i18n_content": _i18n(f"*{en_submitted}*", f"*{zh_submitted}*"),
            },
        },
        {
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {
                    "tag": "plain_text",
                    "content": en_retry,
                    "i18n_content": _i18n(en_retry, zh_retry),
                },
                "type": "primary",
                "behaviors": [{
                    "type": "callback",
                    "value": {
                        "hermes_clarify_action": "retry_submit",
                        "clarify_id": clarify_id,
                    },
                }],
            }],
        },
    ]

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "streaming_mode": False,
            "locales": _LOCALES,
        },
        "body": {"elements": elements},
    }
    return card

def build_clarify_confirmed_card(*, question: str, selected: str) -> dict[str, Any]:
    """构建 Clarify 已确认态卡片 (State 3: Confirmed/Hard Lock). 标题 + 选择 + "已确认"."""
    safe_selected = _escape_md(selected)
    en_selected, zh_selected = _T["clarify_selected"]
    en_sel_label = en_selected.format(safe_selected)
    zh_sel_label = zh_selected.format(safe_selected)

    en_confirmed, zh_confirmed = _T["clarify_confirmed"]

    elements: list[dict] = [
        {
            "tag": "div",
            "icon": {
                "tag": "standard_icon",
                "token": "resolve_filled",
                "size": "20px 20px",
                "color": "green",
            },
            "text": {
                "tag": "lark_md",
                "content": f"**{_escape_md(question)}**",
            },
        },
        {
            "tag": "div",
            "icon": {
                "tag": "standard_icon",
                "token": "resolve_filled",
                "size": "16px 16px",
                "color": "green",
            },
            "text": {
                "tag": "lark_md",
                "content": en_sel_label,
                "i18n_content": _i18n(en_sel_label, zh_sel_label),
            },
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": en_confirmed,
                "i18n_content": _i18n(en_confirmed, zh_confirmed),
            },
        },
    ]

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "streaming_mode": False,
            "locales": _LOCALES,
        },
        "body": {"elements": elements},
    }
    return card


def build_clarify_retired_card(*, question: str, notice: str) -> dict[str, Any]:
    """Build the immutable, grey terminal card for a retired Clarify request.

    A retirement can race a callback or occur after the host has timed the
    request out. This card intentionally contains no interactive elements, so
    a rendered old card cannot invite another resolution attempt.
    """
    en_retired, zh_retired = _T["clarify_retired"]
    clean_notice = str(notice).strip()
    en_notice = f"{en_retired}\n{clean_notice}" if clean_notice else en_retired
    zh_notice = f"{zh_retired}\n{clean_notice}" if clean_notice else zh_retired

    return {
        "schema": "2.0",
        "config": {
            "streaming_mode": False,
            "locales": _LOCALES,
        },
        "body": {
            "elements": [
                {
                    "tag": "div",
                    "icon": {
                        "tag": "standard_icon",
                        "token": "info_outlined",
                        "size": "20px 20px",
                        "color": "grey",
                    },
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{_escape_md(question)}**",
                    },
                },
                {
                    "tag": "div",
                    "icon": {
                        "tag": "standard_icon",
                        "token": "info_outlined",
                        "size": "16px 16px",
                        "color": "grey",
                    },
                    # Plain text makes a runtime notice inert even if it
                    # contains Markdown-like content from an upstream host.
                    "text": {
                        "tag": "plain_text",
                        "content": en_notice,
                        "i18n_content": _i18n(en_notice, zh_notice),
                        "text_color": "grey",
                        "text_size": "notation",
                    },
                },
            ],
        },
    }
