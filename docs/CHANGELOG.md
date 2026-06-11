## v1.0.3 (2026-06-11)

### 🐛 Bug Fixes

- **CRITICAL: Fixed content loss on card seal — dirty data now flushed before close_streaming**: Root cause was `_preservative_seal`'s "content completeness guard" only logging a warning and **clearing dirty flags** (`answer_dirty`, `panel_dirty`, `tool_steps_dirty`) without actually flushing the remaining content to Feishu. Since `stream_element` cannot be called after `close_streaming`, the unflushed content was permanently lost — causing the "footer appears before answer content finishes" bug. Fix: the content completeness guard now **actually flushes** remaining panel content and answer text via `cardkit_batch_update` and `cardkit_stream_element` BEFORE calling `close_streaming`. This is the primary fix for the premature finalization bug.
- **Drain loop improved — longer yield and more rounds**: The drain loop in `_do_linear_complete` previously used `asyncio.sleep(0)` between iterations, which only yields to the event loop's task queue but doesn't give enough time for worker thread callbacks to deliver their last updates. Changed to `asyncio.sleep(0.020)` (20ms) to allow worker thread `call_soon_threadsafe` callbacks to be processed. Also increased `_MAX_DRAIN_ROUNDS` from 5 to 8 for more robust content draining.
- **Streaming parameters hardened — `flush_interval_ms` minimum raised from 50ms to 70ms**: Aligned with Feishu CardKit official `print_frequency_ms` default (70ms). This prevents users from configuring server-side flush intervals below the client-side rendering interval, which could cause over-buffering or frequency control issues. All streaming parameters now verified ≥ official defaults: `print_frequency_ms=70`, `print_step=1`, `_ANSWER_FAST_STREAM_MS=70ms`, `CARDKIT_MS=80ms`.
- **Fixed premature card finalization — `COMPLETING` removed from `_TERMINAL` state set**: Root cause was `COMPLETING` being in `_TERMINAL`, causing `_get_active_session()` to return `None` during the COMPLETING state. Late-arriving `on_answer`/`on_thinking` callbacks were silently dropped, resulting in incomplete answer content on the card when the footer appeared. Fix: (1) Removed `COMPLETING` from `_TERMINAL` — it is a transitional state, not a true terminal state. Now `on_answer`/`on_thinking` can still update `unified_state` during COMPLETING, while `_schedule_linear_flush` still refuses to schedule new flushes (the drain handles it). (2) Enhanced drain loop in `_do_linear_complete`: iterative drain with yield between rounds to catch late-arriving content from the agent worker thread. (3) Content completeness guard in `_preservative_seal` now **flushes** (not just clears) dirty data before close_streaming. (4) `_schedule_card_update` (non-linear mode) now also explicitly blocks during COMPLETING state.
- **Fixed streaming parameters below official defaults**: `print_frequency_ms` raised from 10ms to 70ms (official Feishu CardKit default). The previous value was too aggressive and could cause rendering instability. Per Feishu documentation, the default streaming update interval is 70ms and default step is 1 character.
- **Fixed premature card finalization — drain step added**: Root cause was `_complete_session()` calling `flush.mark_completed()` prematurely, which cancelled the pending flush timer and dropped the last chunk of answer text. Fix: removed premature `mark_completed()` from `_complete_session()`, and added a **drain step** in `_do_linear_complete()` that explicitly flushes any remaining dirty answer/panel data BEFORE closing streaming and adding the footer. This ensures ALL content reaches Feishu before the card is sealed.
- **Answer-only fast-stream throttle aligned to official default**: `_ANSWER_FAST_STREAM_MS` raised from 50ms to 70ms, matching the official `print_frequency_ms` default. Server-side flush interval and client-side render interval now work in harmony.

### ✨ Typewriter Effect (打字机效果)

Streaming card output now renders character-by-character instead of chunk-by-chunk, matching the Feishu CardKit v2.0 documentation behavior:

- `print_frequency_ms` set to 70ms (official default) — Feishu client renders 1 character every 70ms
- `print_step` set to 1 (official default) — one character per render tick
- Default `flush_interval_ms` reduced from 200ms to 100ms — content reaches the card faster
- Flush interval range widened: 70–2000ms (was 100–2000ms)
- Answer-only flush uses 70ms fast-stream throttle (aligned with `print_frequency_ms`)

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
| `flush_interval_ms` range | 100–2000 | 70–2000 |
| `print_frequency_ms` (CardKit) | 10 | 70 |
| `_ANSWER_FAST_STREAM_MS` (internal) | 50ms | 70ms |
