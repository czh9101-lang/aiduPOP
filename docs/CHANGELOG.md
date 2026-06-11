## v1.0.3 (2026-06-11)

### ✨ Typewriter Effect (打字机效果)

Streaming card output now renders character-by-character instead of chunk-by-chunk, matching the Feishu CardKit v2.0 documentation behavior:

- `print_frequency_ms` reduced from 15ms to 10ms — Feishu client renders 1 character every 10ms (100 chars/sec)
- Default `flush_interval_ms` reduced from 200ms to 100ms — content reaches the card faster
- Flush interval range widened: 50–2000ms (was 100–2000ms)
- Answer-only flush uses 50ms fast-stream throttle — when only answer text is dirty (no panel changes), the throttle drops to 50ms for smooth typewriter output

### 🚀 Performance Optimization

- **Deferred markdown optimization**: During streaming, answer text is sent raw (no `optimize_markdown_style`/`_downgrade_tables` processing). Full markdown optimization is applied only at seal time. This eliminates the biggest CPU cost per flush cycle.
- **Reduced gap timers**: `LONG_GAP_MS` 2.0s → 1.0s, `BATCH_AFTER_GAP_MS` 300ms → 100ms — content appears 200ms sooner after a pause
- **Faster transient retries**: `_TRANSIENT_RETRY_DELAYS` reduced from (0.15, 0.5, 1.0) to (0.1, 0.3, 0.6)
- **Smart logging**: `cardkit_stream_element` debug log only emitted when API call takes > 200ms
- **Markdown early returns**: `optimize_markdown_style` skips processing for short text (< 100 chars without markdown structure); `_downgrade_tables` skips when no `|` character exists

### 🧹 Code Cleanup

- **Removed emoji prefixes** from all panel titles: unified panel (🤖), reasoning panel (💭), tool panel (🛠️), background review panel (🔄)
- **Removed non-unified panel code path** from `build_streaming_card_v2()` — unified panel is always used now
- **Deleted `build_linear_compact_seal_card()`** — dead code since card splitting no longer exists
- **Cleaned up `__all__` exports** in `cardkit/elements.py` — removed internal-only names (`_build_reasoning_panel`, `_build_tool_panel`, `REASONING_ELEMENT_ID`, `REASONING_TEXT_ELEMENT_ID`, `TOOL_PANEL_ELEMENT_ID`)
- **Removed legacy test cases** for deleted compact seal card and non-unified panel path

### 🔧 Configuration Changes

| Parameter | Old Default | New Default |
|-----------|-------------|-------------|
| `flush_interval_ms` | 200 | 100 |
| `flush_interval_ms` range | 100–2000 | 50–2000 |
