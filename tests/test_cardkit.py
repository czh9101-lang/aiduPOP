"""cardkit.py 测试 — markdown 优化、表格处理、卡片构建."""

from __future__ import annotations

from hermes_lark_streaming.cardkit import (
    REASONING_ELEMENT_ID,
    REASONING_TEXT_ELEMENT_ID,
    TOOL_PANEL_ELEMENT_ID,
    _build_error_panel,
    _build_footer_elements,
    _build_reasoning_panel,
    _build_tool_panel,
    _compact,
    _escape_md,
    _format_elapsed,
    _longest_backtick_run,
    _render_footer_field,
    build_complete_card,
    build_im_fallback_card,
    build_linear_complete_card,
    build_streaming_card,
    build_streaming_card_v2,
)
from hermes_lark_streaming.cardkit_md import (
    _downgrade_tables,
    _find_tables_outside_code_blocks,
    _split_long_text,
    _strip_invalid_image_keys,
    optimize_markdown_style,
)
from hermes_lark_streaming.linear import Segment

# --- Markdown 优化 ---


class TestOptimizeMarkdownStyle:
    def test_h1_downgraded_to_h4(self) -> None:
        assert "#### Title" in optimize_markdown_style("# Title")

    def test_h2_downgraded_to_h5(self) -> None:
        assert "##### Sub" in optimize_markdown_style("## Sub")

    def test_h3_downgraded_to_h5(self) -> None:
        assert "##### Deep" in optimize_markdown_style("### Deep")

    def test_h4_h5_h6_unchanged(self) -> None:
        text = "#### H4\n##### H5\n###### H6"
        result = optimize_markdown_style(text)
        assert "#### H4" in result
        assert "##### H5" in result

    def test_heading_in_code_block_preserved(self) -> None:
        text = "```\n# Should not change\n```"
        assert "# Should not change" in optimize_markdown_style(text)

    def test_blank_line_compression(self) -> None:
        result = optimize_markdown_style("a\n\n\n\n\nb")
        assert "\n\n\n" not in result

    def test_invalid_image_key_removed(self) -> None:
        text = "![alt](not_img_key)"
        assert "not_img_key" not in optimize_markdown_style(text)

    def test_valid_img_key_preserved(self) -> None:
        text = "![alt](img_v3_abc123)"
        assert "img_v3_abc123" in optimize_markdown_style(text)

    def test_no_headings_unchanged(self) -> None:
        text = "plain text\nanother line"
        assert optimize_markdown_style(text) == text

    def test_mixed_headings_and_code(self) -> None:
        text = "# Title\n```\n# Code heading\n```\n## Sub"
        result = optimize_markdown_style(text)
        assert "#### Title" in result
        assert "# Code heading" in result


class TestStripInvalidImageKeys:
    def test_no_images_unchanged(self) -> None:
        assert _strip_invalid_image_keys("no images") == "no images"

    def test_img_prefix_kept(self) -> None:
        assert "img_v3_test" in _strip_invalid_image_keys("![a](img_v3_test)")

    def test_non_img_removed(self) -> None:
        assert "http://example.com/img.png" not in _strip_invalid_image_keys("![a](http://example.com/img.png)")


# --- 表格处理 ---


class TestFindTablesOutsideCodeBlocks:
    def test_no_tables(self) -> None:
        assert _find_tables_outside_code_blocks("no tables here") == []

    def test_single_table(self) -> None:
        text = "| A | B |\n|---|---|\n| 1 | 2 |"
        results = _find_tables_outside_code_blocks(text)
        assert len(results) == 1

    def test_table_inside_code_block_ignored(self) -> None:
        text = "```\n| A | B |\n|---|---|\n| 1 | 2 |\n```"
        assert _find_tables_outside_code_blocks(text) == []

    def test_mixed(self) -> None:
        table = "| A | B |\n|---|---|\n| 1 | 2 |"
        text = f"{table}\n\n```\n{table}\n```"
        results = _find_tables_outside_code_blocks(text)
        assert len(results) == 1


class TestDowngradeTables:
    def test_within_limit_unchanged(self) -> None:
        table = "| A | B |\n|---|---|\n| 1 | 2 |"
        text = f"{table}\n\n{table}\n\n{table}"
        assert _downgrade_tables(text) == text

    def test_over_limit_downgraded(self) -> None:
        table = "| A | B |\n|---|---|\n| 1 | 2 |"
        # _MAX_CARD_TABLES = 10, so 12 tables triggers downgrade
        text = "\n\n".join([table] * 12)
        result = _downgrade_tables(text)
        assert result.count("```") >= 4  # 超限表格被包装为代码块


# --- 文本拆分 ---


class TestSplitLongText:
    def test_short_text_not_split(self) -> None:
        assert _split_long_text("short") == ["short"]

    def test_long_text_split_at_paragraph(self) -> None:
        chunk = "x" * 1200
        text = f"{chunk}\n\n{chunk}\n\n{chunk}"
        parts = _split_long_text(text, limit=2000)
        assert len(parts) > 1

    def test_no_paragraph_break_falls_back_to_newline(self) -> None:
        lines = ["word " * 100 for _ in range(30)]
        text = "\n".join(lines)
        parts = _split_long_text(text, limit=500)
        assert len(parts) > 1

    def test_exact_limit_not_split(self) -> None:
        text = "a" * 2400
        assert len(_split_long_text(text)) == 1


# --- 工具面板 ---

_STEP_RUNNING = {
    "name": "read",
    "title": "Read",
    "status": "running",
    "detail": "",
    "output": "",
    "error": "",
    "icon": "icon",
    "elapsed_ms": 0,
    "result_block": None,
    "error_block": None,
}
_STEP_SUCCESS = {**_STEP_RUNNING, "status": "success", "output": "ok", "elapsed_ms": 100}


class TestBuildToolPanel:
    def test_empty_steps(self) -> None:
        panel = _build_tool_panel([])
        assert panel["element_id"] == TOOL_PANEL_ELEMENT_ID
        assert "Tool use" in panel["header"]["title"]["content"]

    def test_with_steps(self) -> None:
        panel = _build_tool_panel([_STEP_SUCCESS], elapsed_ms=500)
        assert panel["element_id"] == TOOL_PANEL_ELEMENT_ID

    def test_with_elapsed(self) -> None:
        panel = _build_tool_panel([_STEP_RUNNING], elapsed_ms=3000)
        title = panel["header"]["title"]["content"]
        assert "3.0s" in title


# --- Footer ---


class TestBuildFooterElements:
    def test_empty_data_renders_default_status(self) -> None:
        # 默认字段包含 "status"，总是会渲染
        result = _build_footer_elements({})
        assert len(result) >= 2
        assert "Completed" in result[1]["content"]

    def test_status_completed(self) -> None:
        result = _build_footer_elements({"duration": 5})
        assert len(result) >= 2  # hr + markdown 元素
        assert "Completed" in result[1]["content"]

    def test_status_error(self) -> None:
        result = _build_footer_elements({}, is_error=True)
        assert "red" in result[1]["content"]

    def test_status_aborted(self) -> None:
        result = _build_footer_elements({}, is_aborted=True)
        assert "Stopped" in result[1]["content"]

    def test_elapsed_displayed(self) -> None:
        result = _build_footer_elements({"duration": 12.5}, fields=[["elapsed"]])
        assert "12.5s" in result[1]["content"]

    def test_model_displayed(self) -> None:
        result = _build_footer_elements({"model": "claude-3"}, fields=[["model"]])
        assert "claude-3" in result[1]["content"]

    def test_context_displayed(self) -> None:
        result = _build_footer_elements(
            {"context_used": 50000, "context_max": 200000},
            fields=[["context"]],
        )
        assert "50.0K" in result[1]["content"]
        assert "25%" in result[1]["content"]

    def test_tokens_displayed(self) -> None:
        result = _build_footer_elements(
            {"input_tokens": 1000, "output_tokens": 500},
            fields=[["tokens"]],
        )
        assert "↑" in result[1]["content"]
        assert "↓" in result[1]["content"]

    def test_show_label(self) -> None:
        result = _build_footer_elements(
            {"duration": 5},
            fields=[["elapsed"]],
            show_label=True,
        )
        assert "Elapsed" in result[1]["content"]

    def test_multi_row_fields(self) -> None:
        result = _build_footer_elements(
            {"duration": 5, "model": "gpt"},
            fields=[["elapsed"], ["model"]],
        )
        assert "\n" in result[1]["content"]

    def test_none_footer_data_renders_status(self) -> None:
        result = _build_footer_elements(None)
        assert len(result) >= 2

    def test_no_matching_fields(self) -> None:
        assert _build_footer_elements({}, fields=[["tokens"]]) == []

    def test_compression_exhausted_displayed(self) -> None:
        result = _build_footer_elements(
            {"compression_exhausted": True},
            fields=[["compression_exhausted"]],
        )
        assert len(result) >= 2
        assert "Context Full" in result[1]["content"]

    def test_api_calls_displayed(self) -> None:
        result = _build_footer_elements(
            {"api_calls": 5},
            fields=[["api_calls"]],
        )
        assert len(result) >= 2
        assert "5" in result[1]["content"]

    def test_history_offset_displayed(self) -> None:
        result = _build_footer_elements(
            {"history_offset": 10},
            fields=[["history_offset"]],
        )
        assert len(result) >= 2
        assert "10" in result[1]["content"]


# --- 错误面板 ---


class TestBuildErrorPanel:
    def test_error_panel_structure(self) -> None:
        panel = _build_error_panel("something went wrong")
        assert panel["tag"] == "collapsible_panel"
        assert "❌" in panel["header"]["title"]["content"]
        assert "Error" in panel["header"]["title"]["content"]
        assert panel["border"]["color"] == "red"
        assert panel["expanded"] is True

    def test_aborted_panel_structure(self) -> None:
        panel = _build_error_panel("stopped by user", is_aborted=True)
        assert panel["tag"] == "collapsible_panel"
        assert "🛑" in panel["header"]["title"]["content"]
        assert "Interrupted" in panel["header"]["title"]["content"]
        assert panel["border"]["color"] == "orange"

    def test_error_message_in_elements(self) -> None:
        panel = _build_error_panel("my error detail")
        inner = panel["elements"][0]
        assert inner["tag"] == "markdown"
        assert "my error detail" in inner["content"]

    def test_expanded_default_true(self) -> None:
        panel = _build_error_panel("err")
        assert panel["expanded"] is True

    def test_custom_expanded(self) -> None:
        panel = _build_error_panel("err", expanded=False)
        assert panel["expanded"] is False


# --- 推理面板 ---


class TestBuildReasoningPanel:
    def test_without_elapsed(self) -> None:
        panel = _build_reasoning_panel("thinking content")
        assert "Thought" in panel["header"]["title"]["content"]
        assert not panel["expanded"]

    def test_with_elapsed(self) -> None:
        panel = _build_reasoning_panel("thoughts", elapsed_ms=5000)
        title = panel["header"]["title"]["content"]
        assert "5.0s" in title

    def test_expanded_true(self) -> None:
        panel = _build_reasoning_panel("text", expanded=True)
        assert panel["expanded"] is True

    def test_expanded_default_false(self) -> None:
        panel = _build_reasoning_panel("text")
        assert panel["expanded"] is False

    def test_element_id_set(self) -> None:
        panel = _build_reasoning_panel("text", element_id=REASONING_ELEMENT_ID)
        assert panel["element_id"] == REASONING_ELEMENT_ID

    def test_element_id_default_none(self) -> None:
        panel = _build_reasoning_panel("text")
        assert "element_id" not in panel

    def test_inner_markdown_has_element_id(self) -> None:
        panel = _build_reasoning_panel("text")
        inner = panel["elements"][0]
        assert inner["element_id"] == REASONING_TEXT_ELEMENT_ID

    def test_title_is_plain_text_grey(self) -> None:
        panel = _build_reasoning_panel("text")
        title = panel["header"]["title"]
        assert title["tag"] == "plain_text"
        assert title["text_color"] == "grey"
        assert title["text_size"] == "notation"

    def test_empty_text_shows_thinking_title(self) -> None:
        panel = _build_reasoning_panel(" ")
        assert "Thinking" in panel["header"]["title"]["content"]

    def test_empty_string_shows_thinking_title(self) -> None:
        panel = _build_reasoning_panel("")
        assert "Thinking" in panel["header"]["title"]["content"]

    def test_with_content_shows_thought_title(self) -> None:
        panel = _build_reasoning_panel("reasoning here")
        assert "Thought" in panel["header"]["title"]["content"]
        assert "Thinking" not in panel["header"]["title"]["content"]


# --- 数字格式化 ---


class TestCompact:
    def test_small_number(self) -> None:
        assert _compact(42) == "42"

    def test_thousands(self) -> None:
        assert _compact(1500) == "1.5K"

    def test_millions(self) -> None:
        assert _compact(2_500_000) == "2.5M"

    def test_exact_thousand(self) -> None:
        assert _compact(1000) == "1.0K"

    def test_large_millions(self) -> None:
        assert _compact(250_000_000) == "250M"


class TestFormatElapsed:
    def test_sub_minute(self) -> None:
        assert _format_elapsed(3500) == "3.5s"

    def test_over_minute(self) -> None:
        assert _format_elapsed(125_000) == "2m 5s"

    def test_exactly_one_minute(self) -> None:
        assert _format_elapsed(60_000) == "1m 0s"


# --- 工具函数 ---


class TestEscapeMd:
    def test_escapes_special_chars(self) -> None:
        result = _escape_md("a`b*c{d}e[f]g<h>i")
        assert "\\" in result

    def test_plain_text_unchanged(self) -> None:
        assert _escape_md("hello world") == "hello world"


class TestLongestBacktickRun:
    def test_no_backticks(self) -> None:
        assert _longest_backtick_run("no backticks") == 0

    def test_single(self) -> None:
        assert _longest_backtick_run("a `b` c") == 1

    def test_triple(self) -> None:
        assert _longest_backtick_run("```code```") == 3


# --- 完整卡片构建 ---


class TestBuildStreamingCardV2:
    def test_structure(self) -> None:
        card = build_streaming_card_v2()
        assert card["schema"] == "2.0"
        assert card["config"]["streaming_mode"] is True
        assert card["body"]["elements"]

    def test_with_tool_steps(self) -> None:
        card = build_streaming_card_v2(tool_steps=[_STEP_RUNNING], elapsed_ms=100)
        assert any(e.get("element_id") == TOOL_PANEL_ELEMENT_ID for e in card["body"]["elements"])

    def test_no_tool_use(self) -> None:
        card = build_streaming_card_v2(show_tool_use=False)
        assert not any(e.get("element_id") == TOOL_PANEL_ELEMENT_ID for e in card["body"]["elements"])

    def test_show_reasoning_adds_panel(self) -> None:
        card = build_streaming_card_v2(show_reasoning=True)
        assert any(e.get("element_id") == REASONING_ELEMENT_ID for e in card["body"]["elements"])

    def test_show_reasoning_default_no_panel(self) -> None:
        card = build_streaming_card_v2()
        assert not any(e.get("element_id") == REASONING_ELEMENT_ID for e in card["body"]["elements"])

    def test_reasoning_before_tool_before_answer(self) -> None:
        card = build_streaming_card_v2(
            show_reasoning=True,
            tool_steps=[_STEP_RUNNING],
            elapsed_ms=100,
            show_tool_use=True,
        )
        ids = [e.get("element_id") for e in card["body"]["elements"]]
        reasoning_idx = ids.index(REASONING_ELEMENT_ID)
        tool_idx = ids.index(TOOL_PANEL_ELEMENT_ID)
        assert reasoning_idx < tool_idx

    def test_reasoning_panel_expanded(self) -> None:
        card = build_streaming_card_v2(show_reasoning=True)
        panel = next(e for e in card["body"]["elements"] if e.get("element_id") == REASONING_ELEMENT_ID)
        assert panel["expanded"] is True


class TestBuildStreamingCard:
    def test_basic(self) -> None:
        card = build_streaming_card(text="hello")
        assert card["elements"][-1]["content"] == "hello"

    def test_with_tool_steps(self) -> None:
        card = build_streaming_card(tool_steps=[_STEP_RUNNING], text="hello")
        assert len(card["elements"]) >= 2

    def test_reasoning_shown_alongside_answer(self) -> None:
        """旧版 'if reasoning_text and not text' 会在有 answer 时隐藏 reasoning，现已修复."""
        card = build_streaming_card(reasoning_text="thoughts", text="answer")
        assert any("thoughts" in str(e) for e in card["elements"])
        assert any("answer" in str(e) for e in card["elements"])

    def test_reasoning_before_tool_steps(self) -> None:
        card = build_streaming_card(reasoning_text="thoughts", tool_steps=[_STEP_RUNNING], text="answer")
        contents = [str(e) for e in card["elements"]]
        reasoning_idx = next(i for i, c in enumerate(contents) if "thoughts" in c)
        tool_idx = next(i for i, c in enumerate(contents) if TOOL_PANEL_ELEMENT_ID in c)
        assert reasoning_idx < tool_idx


class TestBuildImFallbackCard:
    def test_structure(self) -> None:
        card = build_im_fallback_card()
        assert "config" in card
        assert "elements" in card
        assert len(card["elements"]) >= 1


class TestBuildCompleteCard:
    def test_basic_v1(self) -> None:
        card = build_complete_card(text="done", has_cardkit=False)
        assert "elements" in card
        assert "schema" not in card

    def test_cardkit_v2(self) -> None:
        card = build_complete_card(text="done", has_cardkit=True)
        assert card["schema"] == "2.0"
        assert "body" in card

    def test_with_tool_steps(self) -> None:
        card = build_complete_card(text="done", tool_steps=[_STEP_SUCCESS])
        # v1 卡片使用 elements
        assert len(card["elements"]) >= 1

    def test_with_reasoning(self) -> None:
        card = build_complete_card(text="answer", reasoning_text="thoughts")
        elements = card.get("elements", card.get("body", {}).get("elements", []))
        assert any("thoughts" in str(e) for e in elements)

    def test_reasoning_before_tool_steps_in_complete(self) -> None:
        card = build_complete_card(
            text="answer", reasoning_text="thoughts", tool_steps=[_STEP_SUCCESS]
        )
        elements = card.get("elements", card.get("body", {}).get("elements", []))
        contents = [str(e) for e in elements]
        reasoning_idx = next(i for i, c in enumerate(contents) if "thoughts" in c)
        tool_idx = next(i for i, c in enumerate(contents) if TOOL_PANEL_ELEMENT_ID in c)
        assert reasoning_idx < tool_idx

    def test_summary_truncated(self) -> None:
        long_text = "x" * 200
        card = build_complete_card(text=long_text, has_cardkit=True)
        summary = card["config"].get("summary", {}).get("content", "")
        assert len(summary) <= 120

    def test_footer_present(self) -> None:
        card = build_complete_card(
            text="done",
            footer_data={"duration": 5, "model": "claude"},
        )
        elements = card.get("elements", card.get("body", {}).get("elements", []))
        # 应包含 hr + footer markdown
        assert any(e.get("tag") == "hr" for e in elements)

    def test_default_done_text(self) -> None:
        card = build_complete_card()
        elements = card.get("elements", card.get("body", {}).get("elements", []))
        assert any("完成" in str(e) or "Done" in str(e) for e in elements)

    def test_error_message_adds_error_panel(self) -> None:
        card = build_complete_card(error_message="test error")
        elements = card.get("elements", card.get("body", {}).get("elements", []))
        error_panels = [e for e in elements if e.get("tag") == "collapsible_panel"
                        and "❌" in e.get("header", {}).get("title", {}).get("content", "")]
        assert len(error_panels) == 1
        assert "test error" in error_panels[0]["elements"][0]["content"]

    def test_error_message_with_aborted(self) -> None:
        card = build_complete_card(error_message="stopped", is_aborted=True)
        elements = card.get("elements", card.get("body", {}).get("elements", []))
        error_panels = [e for e in elements if e.get("tag") == "collapsible_panel"
                        and "🛑" in e.get("header", {}).get("title", {}).get("content", "")]
        assert len(error_panels) == 1
        assert "stopped" in error_panels[0]["elements"][0]["content"]


# --- 线性完成态卡片 ---


def _seg(seg_type: str, text: str = "", **kwargs: int | float) -> Segment:
    """创建测试用 Segment mock."""
    seg = Segment(seg_type, f"{seg_type}_0")
    seg.text = text
    if seg_type == "reasoning":
        seg.text_el_id = f"{seg_type}_0_text"
    seg.tool_offset = int(kwargs.get("tool_offset", 0))
    seg.tool_end_offset = int(kwargs.get("tool_end_offset", 0))
    seg.elapsed_ms = float(kwargs.get("elapsed_ms", 0.0))
    seg.start_time = float(kwargs.get("start_time", 0.0))
    seg.created = True
    seg.dirty = False
    return seg


class TestBuildLinearCompleteCard:
    def test_empty_segments_and_skipped_reasoning(self) -> None:
        """空 segments 渲染 Done；空 reasoning 被跳过."""
        card = build_linear_complete_card(segments=[], all_tool_steps=[])
        assert card["schema"] == "2.0"
        assert any("Done" in str(e) or "完成" in str(e) for e in card["body"]["elements"])

        card2 = build_linear_complete_card(segments=[_seg("reasoning", "")], all_tool_steps=[])
        assert any("Done" in str(e) or "完成" in str(e) for e in card2["body"]["elements"])

    def test_answer_only_no_done(self) -> None:
        card = build_linear_complete_card(
            segments=[_seg("answer", "hello world")],
            all_tool_steps=[],
        )
        elements = card["body"]["elements"]
        assert any("hello world" in str(e) for e in elements)
        assert not any("Done" in str(e) for e in elements)

    def test_reasoning_before_answer(self) -> None:
        card = build_linear_complete_card(
            segments=[_seg("reasoning", "think"), _seg("answer", "reply")],
            all_tool_steps=[],
        )
        contents = [str(e) for e in card["body"]["elements"]]
        r_idx = next(i for i, c in enumerate(contents) if "think" in c)
        a_idx = next(i for i, c in enumerate(contents) if "reply" in c)
        assert r_idx < a_idx

    def test_tool_segment_uses_steps_slice(self) -> None:
        steps = [_STEP_RUNNING, _STEP_SUCCESS, _STEP_RUNNING]
        card = build_linear_complete_card(
            segments=[_seg("tool", tool_offset=1, tool_end_offset=3)],
            all_tool_steps=steps,
        )
        tool_elements = [e for e in card["body"]["elements"] if e.get("tag") == "collapsible_panel"]
        assert len(tool_elements) == 1
        assert len(tool_elements[0].get("elements", [])) == 2  # steps[1:3]

    def test_three_round_ordering(self) -> None:
        card = build_linear_complete_card(
            segments=[
                _seg("reasoning", "r1"),
                _seg("answer", "a1"),
                _seg("tool", tool_offset=0, tool_end_offset=2),
                _seg("reasoning", "r2"),
                _seg("answer", "a2"),
            ],
            all_tool_steps=[_STEP_SUCCESS, _STEP_RUNNING],
        )
        contents = [str(e) for e in card["body"]["elements"]]
        r1 = next(i for i, c in enumerate(contents) if "r1" in c)
        a1 = next(i for i, c in enumerate(contents) if "a1" in c)
        r2 = next(i for i, c in enumerate(contents) if "r2" in c)
        a2 = next(i for i, c in enumerate(contents) if "a2" in c)
        assert r1 < a1 < r2 < a2

    def test_tool_end_offset_zero_uses_all_steps(self) -> None:
        steps = [_STEP_SUCCESS, _STEP_RUNNING]
        card = build_linear_complete_card(
            segments=[_seg("tool", tool_offset=0, tool_end_offset=0)],
            all_tool_steps=steps,
        )
        inner = next(e for e in card["body"]["elements"] if e.get("tag") == "collapsible_panel")["elements"]
        assert len(inner) == 2

    def test_tool_empty_steps_skipped(self) -> None:
        card = build_linear_complete_card(
            segments=[_seg("tool", tool_offset=5, tool_end_offset=5)],
            all_tool_steps=[_STEP_SUCCESS],
        )
        assert not any(e.get("tag") == "collapsible_panel" for e in card["body"]["elements"])

    def test_summary_truncated_from_last_answer(self) -> None:
        card = build_linear_complete_card(
            segments=[_seg("answer", "short"), _seg("answer", "x" * 200)],
            all_tool_steps=[],
        )
        summary = card["config"].get("summary", {}).get("content", "")
        assert len(summary) <= 120

    def test_error_message_adds_error_panel(self) -> None:
        card = build_linear_complete_card(segments=[], all_tool_steps=[], error_message="test error")
        elements = card["body"]["elements"]
        error_panels = [e for e in elements if e.get("tag") == "collapsible_panel"
                        and "❌" in e.get("header", {}).get("title", {}).get("content", "")]
        assert len(error_panels) == 1
        assert "test error" in error_panels[0]["elements"][0]["content"]

    def test_error_message_before_segments(self) -> None:
        card = build_linear_complete_card(
            segments=[_seg("answer", "hello")],
            all_tool_steps=[],
            error_message="oops",
        )
        contents = [str(e) for e in card["body"]["elements"]]
        error_idx = next(i for i, c in enumerate(contents) if "oops" in c)
        answer_idx = next(i for i, c in enumerate(contents) if "hello" in c)
        assert error_idx < answer_idx


class TestBuildCronCard:
    def test_basic_card_structure(self) -> None:
        from hermes_lark_streaming.cardkit import build_cron_card

        card = build_cron_card("Hello **world**")
        assert card["schema"] == "2.0"
        assert card["body"]["elements"][0]["tag"] == "markdown"
        assert "Hello **world**" in card["body"]["elements"][0]["content"]

    def test_summary_from_content(self) -> None:
        from hermes_lark_streaming.cardkit import build_cron_card

        card = build_cron_card("Line 1\nLine 2\n" + "x" * 200)
        summary = card["config"]["summary"]["content"]
        assert summary.startswith("Line 1 Line 2")
        assert len(summary) <= 120

    def test_empty_content(self) -> None:
        from hermes_lark_streaming.cardkit import build_cron_card

        card = build_cron_card("")
        assert card["body"]["elements"] == []

    def test_table_content_preserved(self) -> None:
        from hermes_lark_streaming.cardkit import build_cron_card

        content = "| A | B |\n|---|---|\n| 1 | 2 |"
        card = build_cron_card(content)
        assert "| A | B |" in card["body"]["elements"][0]["content"]

    def test_many_tables_triggers_downgrade(self) -> None:
        from hermes_lark_streaming.cardkit import build_cron_card

        table = "| A | B |\n|---|---|\n| 1 | 2 |"
        # _MAX_CARD_TABLES = 10, so 12 tables triggers _downgrade_tables
        content = "\n\n".join([table] * 12)
        card = build_cron_card(content)
        # Excess tables are wrapped in code blocks by _downgrade_tables
        combined = " ".join(e["content"] for e in card["body"]["elements"])
        assert "```" in combined

    def test_long_content_split_into_multiple_elements(self) -> None:
        from hermes_lark_streaming.cardkit import build_cron_card

        # Content longer than 2400 chars should be split by _split_long_text
        content = "x" * 3000
        card = build_cron_card(content)
        assert len(card["body"]["elements"]) > 1

    def test_headings_downgraded_in_cron_card(self) -> None:
        from hermes_lark_streaming.cardkit import build_cron_card

        content = "# Title\nSome text"
        card = build_cron_card(content)
        # optimize_markdown_style downgrades h1 → h4
        combined = " ".join(e["content"] for e in card["body"]["elements"])
        assert "#### Title" in combined


# --- Cache footer field ---


class TestCacheFooterField:
    """_render_footer_field("cache", ...) 缓存命中率字段渲染测试."""

    def test_cache_with_both_tokens(self) -> None:
        """cache_read_tokens + input_tokens 均存在时渲染💾格式."""
        en, zh = _render_footer_field(
            "cache",
            {"cache_read_tokens": 136300, "input_tokens": 137400},
            is_error=False,
            is_aborted=False,
            show_label=False,
        )
        assert en is not None
        assert zh is not None
        assert "💾" in en
        assert "136.3K" in en
        assert "137.4K" in en
        assert "99%" in en

    def test_cache_hit_percentage_calculation(self) -> None:
        """命中率百分比 = cache_read / input_tokens * 100."""
        en, zh = _render_footer_field(
            "cache",
            {"cache_read_tokens": 50000, "input_tokens": 200000},
            is_error=False,
            is_aborted=False,
            show_label=False,
        )
        assert en is not None
        assert "25%" in en
        assert "50.0K" in en
        assert "200.0K" in en

    def test_cache_zero_read_returns_none(self) -> None:
        """cache_read_tokens=0 时返回 (None, None)."""
        en, zh = _render_footer_field(
            "cache",
            {"cache_read_tokens": 0, "input_tokens": 1000},
            is_error=False,
            is_aborted=False,
            show_label=False,
        )
        assert en is None
        assert zh is None

    def test_cache_zero_input_returns_none(self) -> None:
        """input_tokens=0 时返回 (None, None)."""
        en, zh = _render_footer_field(
            "cache",
            {"cache_read_tokens": 500, "input_tokens": 0},
            is_error=False,
            is_aborted=False,
            show_label=False,
        )
        assert en is None
        assert zh is None

    def test_cache_missing_data_returns_none(self) -> None:
        """缺少 cache_read_tokens 或 input_tokens 时返回 (None, None)."""
        en1, zh1 = _render_footer_field(
            "cache", {}, is_error=False, is_aborted=False, show_label=False,
        )
        assert en1 is None

        en2, zh2 = _render_footer_field(
            "cache",
            {"cache_read_tokens": 500},
            is_error=False,
            is_aborted=False,
            show_label=False,
        )
        assert en2 is None

        en3, zh3 = _render_footer_field(
            "cache",
            {"input_tokens": 1000},
            is_error=False,
            is_aborted=False,
            show_label=False,
        )
        assert en3 is None

    def test_cache_show_label_true_english(self) -> None:
        """show_label=True 时英文前缀为 'Cache 💾 ...'."""
        en, zh = _render_footer_field(
            "cache",
            {"cache_read_tokens": 1500, "input_tokens": 2000},
            is_error=False,
            is_aborted=False,
            show_label=True,
        )
        assert en is not None
        assert en.startswith("Cache 💾")
        assert "75%" in en

    def test_cache_show_label_true_chinese(self) -> None:
        """show_label=True 时中文前缀为 '缓存 💾 ...'."""
        en, zh = _render_footer_field(
            "cache",
            {"cache_read_tokens": 1500, "input_tokens": 2000},
            is_error=False,
            is_aborted=False,
            show_label=True,
        )
        assert zh is not None
        assert zh.startswith("缓存 💾")
        assert "75%" in zh

    def test_cache_show_label_false_no_prefix(self) -> None:
        """show_label=False 时无标签前缀，直接💾开头."""
        en, zh = _render_footer_field(
            "cache",
            {"cache_read_tokens": 1500, "input_tokens": 2000},
            is_error=False,
            is_aborted=False,
            show_label=False,
        )
        assert en is not None
        assert en.startswith("💾")
        assert not en.startswith("Cache")

    def test_cache_in_build_footer_elements(self) -> None:
        """cache 字段在 _build_footer_elements 中正常渲染."""
        result = _build_footer_elements(
            {"cache_read_tokens": 136300, "input_tokens": 137400},
            fields=[["cache"]],
        )
        assert len(result) >= 2
        assert "💾" in result[1]["content"]
        assert "99%" in result[1]["content"]

    def test_cache_100_percent(self) -> None:
        """全部命中时显示 100%."""
        en, zh = _render_footer_field(
            "cache",
            {"cache_read_tokens": 1000, "input_tokens": 1000},
            is_error=False,
            is_aborted=False,
            show_label=False,
        )
        assert en is not None
        assert "100%" in en
