"""线性模式拆卡/估算辅助函数 — 从 controller_linear_mixin.py 拆分."""

from __future__ import annotations

import re
from typing import Any

from ..cardkit.md import (
    _downgrade_tables,
    _split_long_text,
    optimize_markdown_style,
)
from .linear import Segment

__all__ = [
    "_ELEMENT_THRESHOLD",
    "_FOOTER_RESERVE",
    "_estimate_segment_elements",
    "_estimate_tool_elements",
    "_find_tool_split_offset",
    "_simplify_segments_for_complete",
    "_tool_segment_end",
    "_count_images_in_text",
]

# 匹配 markdown 图片语法: ![alt](img_xxx) — 与 cardkit._IMG_MD_PATTERN 对齐
_IMG_MD_PATTERN = re.compile(r"!\[([^\]]*)\]\((img_[^)\s]+)\)")

_ELEMENT_THRESHOLD = 185  # 拆卡阈值（飞书卡片 2.0 硬上限 200 元素+组件，预留 15 给 footer + 图片 + 封卡波动）
_FOOTER_RESERVE = 15  # footer 元素预留（hr + markdown + 图片 + 封卡降级新增元素等）


def _count_images_in_text(text: str) -> int:
    """统计 markdown 文本中 img_ 前缀的图片数量（与 cardkit._extract_images_from_markdown 对齐）."""
    return len(_IMG_MD_PATTERN.findall(text))


def _estimate_segment_elements(seg: Segment, all_steps: list[dict[str, Any]]) -> int:
    """估算单个 segment 封卡时实际占用的卡片元素数.

    v1.0.0 (方案B): answer 估算固定为 1，对齐保留式封卡的实际行为——
    保留式封卡（close_streaming + batch_update）不重建整卡，
    streaming element 封卡后仍为 1 个元素（不触发 _split_long_text），
    避免 1→N+2M 的估算错位导致过早拆卡（"秒拆"/"第N张卡只有一句话"）。
    只有全量重建封卡（preservative seal 失败时的降级路径）才会触发
    _split_long_text，此时由 300305 reactive 拆卡兜底。
    """
    if seg.type == "reasoning":
        return 4  # collapsible_panel + plain_text + standard_icon + markdown
    elif seg.type == "answer":
        return 1  # 保留式封卡下，answer 永远是 1 个 element
    elif seg.type == "tool":
        return _estimate_tool_elements(
            seg.tool_offset,
            _tool_segment_end(seg, all_steps),
            all_steps,
        )
    return 0


def _tool_segment_end(seg: Segment, all_steps: list[dict[str, Any]]) -> int:
    return seg.tool_end_offset if seg.tool_end_offset else len(all_steps)


def _estimate_tool_elements(start: int, end: int, all_steps: list[dict[str, Any]]) -> int:
    """估算 tool panel 在 [start, end) step 区间内的元素数."""
    steps = all_steps[start:end]
    count = 3  # panel/header 基础元素
    for step in steps:
        count += 3  # title: div + standard_icon + lark_md
        if step.get("detail"):
            count += 2  # detail: div + plain_text
        if step.get("result_block") or step.get("error_block"):
            count += 2  # output: div + lark_md
    return count


def _find_tool_split_offset(
    base_count: int,
    seg: Segment,
    all_steps: list[dict[str, Any]],
) -> int | None:
    """寻找 tool step 拆分点，让当前卡保留尽可能多的 steps."""
    start = seg.tool_offset
    end = _tool_segment_end(seg, all_steps)
    if end - start <= 1:
        return None
    for split_offset in range(end - 1, start, -1):
        estimate = _estimate_tool_elements(start, split_offset, all_steps)
        if base_count + estimate + _FOOTER_RESERVE <= _ELEMENT_THRESHOLD:
            return split_offset
    return None


# _find_answer_split_offset removed in v1.0.0 (方案B)
# answer 估算固定为 1 element，不再需要 answer 内部拆分。
# 保留式封卡下 answer 始终是 1 个 streaming element，
# 只有全量重建封卡时才可能触发 _split_long_text，
# 此时由 300305 reactive 拆卡兜底。


def _simplify_segments_for_complete(
    segments: list[Segment],
    all_tool_steps: list[dict[str, Any]],
    level: int = 1,
) -> list[Segment]:
    """为简化卡片构建精简 segment 列表.

    当封卡因元素超限 (300305) 失败时，构建一个精简版的 segment 列表。

    Level 1 (compact): 保留所有面板类型，截断内容以减少元素
      - reasoning 文本截断至 2000 字符
      - answer 文本截断至 4000 字符
      - tool 保留但精简步骤详情（移除 detail 和 result_block）
    Level 2 (minimal): 移除 reasoning，保留 tool+answer
      - 截断 answer 文本至 4000 字符
      - 保留 tool segment 但精简步骤详情
    """
    simplified = []
    for seg in segments:
        if seg.type == "reasoning":
            if level >= 2:
                # Level 2+: drop reasoning entirely
                continue
            # Level 1: truncate reasoning text
            if seg.text and len(seg.text) > 2000:
                new_seg = Segment(seg.type, seg.el_id)
                new_seg.text = seg.text[:2000] + "\n\n... (truncated)"
                new_seg.text_el_id = seg.text_el_id
                new_seg.created = seg.created
                new_seg.element_estimate = 4
                simplified.append(new_seg)
            else:
                simplified.append(seg)
        elif seg.type == "answer":
            # 截断过长文本
            if len(seg.text) > 4000:
                new_seg = Segment(seg.type, seg.el_id)
                new_seg.text = seg.text[:4000] + "\n\n... (truncated)"
                new_seg.text_el_id = seg.text_el_id
                new_seg.created = seg.created
                new_seg.element_estimate = 1  # 保守估算
                simplified.append(new_seg)
            else:
                simplified.append(seg)
        elif seg.type == "tool":
            # 保留 tool segment 但精简步骤详情
            simplified.append(seg)
        else:
            simplified.append(seg)
    if not simplified:
        # 如果所有 segment 都被过滤，至少保留 answer 的文本
        for seg in segments:
            if seg.type == "answer" and seg.text:
                simple_seg = Segment("answer", seg.el_id)
                simple_seg.text = seg.text[:4000]
                simple_seg.text_el_id = seg.text_el_id
                simple_seg.created = seg.created
                simple_seg.element_estimate = 1
                simplified.append(simple_seg)
                break
    return simplified
