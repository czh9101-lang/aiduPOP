"""v1.2.0 远程诊断功能测试.

覆盖：
- 错误事件环形缓冲区（record/get/clear）
- build_diagnose_card 生成结构
- /aowen diagnose 命令路由
- _build_error_panel 传 card_trace_id（错误卡片显示调试 ID + /aowen diagnose 提示）
"""

from __future__ import annotations

from hermes_lark_streaming.aowen import (
    _metrics,
    build_diagnose_card,
    clear_diagnostic_events,
    get_diagnostic_events,
    record_api_error,
    record_card_failed,
    record_diagnostic_event,
    record_full_rebuild,
)
from hermes_lark_streaming.cardkit.elements import _build_error_panel


class TestDiagnosticEventBuffer:
    """错误事件环形缓冲区."""

    def setup_method(self) -> None:
        clear_diagnostic_events()

    def teardown_method(self) -> None:
        clear_diagnostic_events()

    def test_record_and_get_event(self) -> None:
        """记录一条事件并读取."""
        record_diagnostic_event("api_error", code=300309, operation="cardkit_stream_element", trace_id="abc123")
        events = get_diagnostic_events()
        assert len(events) == 1
        assert events[0]["type"] == "api_error"
        assert events[0]["code"] == 300309
        assert events[0]["operation"] == "cardkit_stream_element"
        assert events[0]["trace"] == "abc123"

    def test_trace_id_truncated_to_8(self) -> None:
        """trace_id 截断到 8 字符（脱敏）."""
        record_diagnostic_event("api_error", trace_id="abcdefghijklmnop")
        events = get_diagnostic_events()
        assert len(events[0]["trace"]) == 8

    def test_detail_truncated_to_80(self) -> None:
        """detail 截断到 80 字符."""
        record_diagnostic_event("card_failed", detail="x" * 200)
        events = get_diagnostic_events()
        assert len(events[0]["detail"]) == 80

    def test_no_sensitive_data_stored(self) -> None:
        """确认不存消息内容/用户ID/chatID（只有结构化字段）."""
        record_diagnostic_event("api_error", code=300309, operation="test", trace_id="xyz", detail="some error")
        events = get_diagnostic_events()
        ev = events[0]
        # 只应有这些字段
        assert set(ev.keys()) == {"time", "type", "code", "operation", "trace", "detail"}
        # 不应有 msg_id/chat_id/user_id/answer_text 等
        for key in ("msg_id", "chat_id", "user_id", "answer_text", "message", "content"):
            assert key not in ev

    def test_ring_buffer_max_30(self) -> None:
        """环形缓冲区最多 30 条."""
        for i in range(35):
            record_diagnostic_event("api_error", code=i, detail=f"event {i}")
        events = get_diagnostic_events()
        assert len(events) == 30
        # 最老的被挤出，保留最新 30 条（code 5~34）
        assert events[0]["code"] == 5
        assert events[-1]["code"] == 34

    def test_clear_events(self) -> None:
        """clear 清空缓冲区."""
        record_diagnostic_event("api_error")
        assert len(get_diagnostic_events()) == 1
        clear_diagnostic_events()
        assert len(get_diagnostic_events()) == 0

    def test_record_api_error_logs_diagnostic_event(self) -> None:
        """record_api_error 自动记录诊断事件."""
        clear_diagnostic_events()
        record_api_error(300313, "cardkit_stream_element")
        events = get_diagnostic_events()
        assert len(events) == 1
        assert events[0]["type"] == "api_error"
        assert events[0]["code"] == 300313

    def test_record_card_failed_logs_diagnostic_event(self) -> None:
        """record_card_failed 自动记录诊断事件."""
        clear_diagnostic_events()
        record_card_failed()
        events = get_diagnostic_events()
        assert len(events) == 1
        assert events[0]["type"] == "card_failed"

    def test_record_full_rebuild_logs_diagnostic_event(self) -> None:
        """record_full_rebuild 自动记录诊断事件."""
        clear_diagnostic_events()
        record_full_rebuild()
        events = get_diagnostic_events()
        assert len(events) == 1
        assert events[0]["type"] == "full_rebuild"


class TestBuildDiagnoseCard:
    """诊断报告卡片构建."""

    def setup_method(self) -> None:
        clear_diagnostic_events()
        _metrics["cards_failed"] = 0
        _metrics["api_errors"] = 0

    def teardown_method(self) -> None:
        clear_diagnostic_events()

    def test_card_has_diagnostic_id(self) -> None:
        """卡片包含诊断 ID（diag_<timestamp>_<4hex>）."""
        card = build_diagnose_card()
        # 找到诊断 ID 文本
        card_str = str(card)
        assert "diag_" in card_str
        import re
        m = re.search(r"diag_\d+_[0-9a-f]{4}", card_str)
        assert m, f"诊断 ID 格式不对: {card_str[:200]}"

    def test_card_has_environment_info(self) -> None:
        """卡片包含环境信息（插件版本/Python）."""
        card = build_diagnose_card()
        card_str = str(card)
        assert "插件版本" in card_str
        assert "Python" in card_str

    def test_card_shows_no_events_when_empty(self) -> None:
        """无错误事件时显示'无近期错误事件'."""
        card = build_diagnose_card()
        card_str = str(card)
        assert "无近期错误事件" in card_str

    def test_card_shows_events_when_present(self) -> None:
        """有错误事件时显示事件列表."""
        record_diagnostic_event("api_error", code=300309, operation="test_op", trace_id="abc123")
        card = build_diagnose_card()
        card_str = str(card)
        assert "api_error" in card_str
        assert "300309" in card_str
        assert "test_op" in card_str
        assert "abc123" in card_str

    def test_card_has_usage_hint(self) -> None:
        """卡片包含'如何使用此报告'折叠提示."""
        card = build_diagnose_card()
        card_str = str(card)
        assert "如何使用此报告" in card_str
        assert "/aowen diagnose" in card_str

    def test_card_no_sensitive_data(self) -> None:
        """卡片不含消息内容/用户ID/chatID（脱敏）."""
        record_diagnostic_event("api_error", detail="some error")
        card = build_diagnose_card()
        card_str = str(card)
        # 不应出现完整的 msg_id/chat_id 格式
        assert "om_" not in card_str  # msg_id 前缀
        assert "oc_" not in card_str  # chat_id 前缀
        assert "ou_" not in card_str  # open_id 前缀


class TestErrorPanelWithTraceId:
    """错误卡片显示调试 ID + /aowen diagnose 提示."""

    def test_error_panel_with_trace_id_shows_debug_id(self) -> None:
        """有 card_trace_id 时错误面板显示调试 ID."""
        panel = _build_error_panel("some error", is_aborted=False, card_trace_id="abc123")
        panel_str = str(panel)
        assert "abc123" in panel_str
        assert "调试 ID" in panel_str or "Debug ID" in panel_str

    def test_error_panel_shows_diagnose_hint(self) -> None:
        """错误面板提示用户发 /aowen diagnose."""
        panel = _build_error_panel("some error", is_aborted=False, card_trace_id="abc123")
        panel_str = str(panel)
        assert "/aowen diagnose" in panel_str

    def test_error_panel_without_trace_id_still_shows_diagnose_hint(self) -> None:
        """无 card_trace_id 时仍提示 /aowen diagnose."""
        panel = _build_error_panel("some error", is_aborted=False)
        panel_str = str(panel)
        assert "/aowen diagnose" in panel_str

    def test_aborted_panel_no_diagnose_hint(self) -> None:
        """中断面板（is_aborted=True）不显示 diagnose 提示（中断不是错误）."""
        panel = _build_error_panel("Interrupted", is_aborted=True, card_trace_id="abc123")
        panel_str = str(panel)
        # 中断面板用 error_message 作为 body，不含 friendly 提示
        assert "/aowen diagnose" not in panel_str
