## v1.0.3 (2026-06-12)

### 🏗️ Architecture: Card Lifecycle State Machine Optimization

参考 [openclaw-lark](https://github.com/larksuite/openclaw-lark) 插件设计美学，对卡片生命周期和状态机制进行全面优化。**不照搬代码，参考设计原则，适配我们插件的猴子补丁架构**。

- **显式状态转换图 (`PHASE_TRANSITIONS`)**: 定义了每个阶段的合法后继阶段，终端阶段（COMPLETED / CREATION_FAILED / ABORTED / TERMINATED）无出边——吸收态。非法转换被拒绝并记录日志，避免隐式状态漂移。
- **`CardPhase` 类**: 新增 `CREATION_FAILED`（替代旧的 catch-all `FAILED`，语义更明确——卡片创建失败 → 回退到静态交付）和 `TERMINATED`（消息被删除/撤回 → 立即停止所有更新，独立于 ABORTED 和 FAILED）。向后兼容：`FAILED` 作为 `CREATION_FAILED` 的别名保留。
- **`TerminalReason` 类**: 追踪会话进入终端阶段的**原因**（normal / error / abort / unavailable / creation_failed），而非仅知道"已结束"。
- **`CardVisualState` 类**: 将**视觉外观**（thinking / streaming / complete / error / aborted）与**生命周期阶段**分离。多个阶段可映射到同一视觉状态，卡片构建逻辑更清晰。
- **`CardSession.transition()`**: 验证转换合法性，自动设置 `terminal_reason`/`terminal_source`，日志记录所有转换。现有代码 `session.state = "streaming"` 仍可工作（无验证直接赋值），新代码推荐使用 `session.transition()`。
- **`CardSession.should_proceed()`**: 统一守卫——合并终端阶段检查 + UnavailableGuard 检查，替代散落的 `session.state in _TERMINAL` 和 `session.guard.should_skip()` 双重判断。
- **`CardSession.is_stale_create(epoch)`**: Epoch 机制——终端阶段进入时递增 `create_epoch`，卡片创建回调检查 epoch 是否过期，防止过期回调污染状态（竞态场景：创建期间会话被终止）。
- **`CardSession.enter_terminal()`**: 统一终端入口——设置 `terminal_reason`、`terminal_source`，递增 `create_epoch`。首次调用保留原因（幂等），后续调用不覆盖。
- **`CardSession._on_guard_terminate()`**: UnavailableGuard 回调现在设置 `TERMINATED` 状态（而非旧的 `FAILED`），语义更精确——消息被删除/撤回不应视为"卡片创建失败"。
- **新增 `state/phase.py` 模块**: `CardPhase`、`TerminalReason`、`CardVisualState`、`PHASE_TRANSITIONS`、`TERMINAL_PHASES`、`PHASE_TO_VISUAL`、`is_legal_transition()`、`get_visual_state()` 的唯一定义源。
- **新增 `tests/test_phase.py`**: 88 个测试覆盖所有状态机增强功能——常量、转换表、`transition()`、`should_proceed()`、`is_terminal_phase`/`visual_state` 属性、`enter_terminal()`、`is_stale_create()`、`_on_guard_terminate()`、向后兼容性。
- **`controller/mixin.py`**: 阶段常量从 `state.phase` 导入（唯一定义源），`_do_create_card` 使用 epoch 快照 + stale-create 守卫，失败路径使用 `CREATION_FAILED` + `enter_terminal()`。
- **`controller/linear_mixin.py`**: `_do_create_linear_card` 使用 epoch 快照 + stale-create 守卫，`_schedule_linear_flush` 使用 `session.should_proceed()`，`_do_unified_flush` 使用 `session.is_terminal_phase`。
- **`controller/core.py`**: `_get_active_session` 使用 `session.is_terminal_phase` 属性，`on_completed` 检查 `CREATION_FAILED`/`TERMINATED` 作为 yield-to-gateway 条件。
- **`patching/gateway.py`**: 悬挂会话检测更新为包含 `"creation_failed"` 和 `"terminated"`（替代旧的 `"failed"`）。
- **`controller/__init__.py`** / **`state/__init__.py`**: 导出 `CREATION_FAILED` 和 `TERMINATED`。

### 🐛 Bug Fixes

- **CRITICAL: Fixed conversation list permanently showing "处理中..." for Chinese users (Bug #3)**: Root cause was `cardkit_close_streaming` only updating `summary.content` but NOT `summary.i18n_content`. Feishu CardKit 2.0 displays `i18n_content.<locale>` based on the user's language preference — for Chinese users, it shows `i18n_content.zh_cn`. Since `i18n_content` was never updated from the initial "处理中...", the conversation list always displayed "处理中..." even after `close_streaming` succeeded and `content` was updated. Fix: (1) `cardkit_close_streaming` in `feishu/client.py` now updates BOTH `content` and `i18n_content` (zh_cn + en_us) when closing streaming. (2) All card builders (`build_complete_card`, `build_linear_complete_card`, `_build_linear_complete_unified`, `build_unified_complete_card`) now use a shared `_build_summary()` helper that includes both `content` and `i18n_content`. (3) Added regression test class `TestSummaryI18nContent` with 4 tests covering all code paths.
- **CRITICAL: Fixed duplicate `close_streaming` calls causing 300317 sequence conflicts**: Root cause was `_preservative_seal` and its retry path both calling `cardkit_close_streaming` for the same card. When the first call succeeded but the subsequent `batch_update` hit a 300317 conflict, the retry would call `close_streaming` AGAIN — but the card's server-side sequence had already advanced from the first successful close, causing a second 300317. This cascading failure left the card stuck in streaming mode, making the Feishu conversation list permanently show "处理中..." (processing). Fix: Added `_streaming_closed` guard flag to `CardSession` — `close_streaming` is now called exactly ONCE per card lifecycle. All code paths (preservative seal, retry, fallback, drain, flush) check and set this flag.
- **CRITICAL: Fixed `UnboundLocalError: 'panel'` in preservative seal retry path**: When `close_streaming` succeeded but `batch_update` failed with 300317, the retry path referenced `panel["header"]` — but `panel` was only assigned in the try block AFTER the close_streaming call that already failed. Since close_streaming succeeded (the 300317 came from batch_update), `panel` was never assigned. This crashed the fallback recovery path, leaving the card stuck in streaming mode with no way to close it. Fix: Retry path now always rebuilds the panel from current state instead of referencing the variable from the try block.
- **Fixed session list permanently showing "处理中..." after reply completion**: The combination of (1) duplicate close_streaming → 300317, and (2) UnboundLocalError crashing the fallback, meant that `close_streaming` with summary update never succeeded. The card remained in streaming mode on Feishu's server, so the conversation list kept showing "处理中..." even after the reply was fully rendered. Both root causes are now fixed.
- **Fixed fallback path not updating card summary**: When preservative seal failed and fell back to full card rebuild, the `close_streaming` call in the fallback path could fail silently (300309 or 300317) without updating the card summary. Now uses the same `_streaming_closed` guard and always provides the summary parameter.
- **Fixed all code paths setting `_streaming_closed` on CARDKIT_STREAMING_CLOSED errors**: When any API call returns error code 300309 (streaming already closed), the `_streaming_closed` flag is now set. Previously, only the drain path caught this error, leaving the flag unset and causing subsequent code to attempt redundant `close_streaming` calls.
- **CRITICAL: Fixed answer content not appearing in Feishu streaming cards**: Three root causes: (1) `_thinking_wrapper` in `patching/callbacks.py` ignored Hermes's `already_streamed` kwarg — when Hermes called `interim_assistant_callback(text, already_streamed=True)`, our wrapper still processed the text through `on_thinking_delta`, causing doubled/garbled answer text. Fix: `_thinking_wrapper` now checks `already_streamed`; when True, skips `on_thinking_delta` and passes through to the original callback (for Hermes `_stream_consumer.on_segment_break()`). (2) Dedup mechanism used exact string match on the last stream delta chunk (`_stream_consumed_texts`), which failed when `interim_assistant_callback` delivers accumulated text (different length than the last delta). Fix: replaced exact-match dedup with length-based tracking — track total consumed text length per eid (`_stream_consumed_len`) instead of storing the last chunk. (3) `on_completed()` in `controller/core.py` only updated `session.text` (non-linear TextState) with the answer parameter, never `session.unified_state.answer_text` (linear mode). When `stream_delta_callback` failed to deliver answer text, the linear card had no answer content. Fix: added linear answer fallback in `on_completed` — when session is linear and `unified_state.answer_text` is empty but answer param is provided, populate `unified_state` with the answer text. (4) Added debug logging for dedup decisions in `_linear_on_thinking`. Files changed: `patching/callbacks.py`, `controller/core.py`, `controller/linear_mixin.py`, `tests/test_callback_interception.py`.

### ✨ Typewriter Effect (打字机效果)

Streaming card output now renders character-by-character instead of chunk-by-chunk, matching the Feishu CardKit v2.0 documentation behavior:

- `print_frequency_ms` set to 70ms (official default) — Feishu client renders 1 character every 70ms
- `print_step` set to 1 (official default) — one character per render tick
- Default `flush_interval_ms` reduced from 200ms to 100ms — content reaches the card faster
- Flush interval range widened: 70–2000ms (was 100–2000ms)
- Answer-only flush uses 70ms fast-stream throttle (aligned with `print_frequency_ms`)

### 🐛 Bug Fixes (earlier in v1.0.3)

- **Fixed content loss on card seal — dirty data now flushed before close_streaming**: Root cause was `_preservative_seal`'s "content completeness guard" only logging a warning and **clearing dirty flags** (`answer_dirty`, `panel_dirty`, `tool_steps_dirty`) without actually flushing the remaining content to Feishu. Since `stream_element` cannot be called after `close_streaming`, the unflushed content was permanently lost — causing the "footer appears before answer content finishes" bug. Fix: the content completeness guard now **actually flushes** remaining panel content and answer text via `cardkit_batch_update` and `cardkit_stream_element` BEFORE calling `close_streaming`. This is the primary fix for the premature finalization bug.
- **Drain loop improved — longer yield and more rounds**: The drain loop in `_do_linear_complete` previously used `asyncio.sleep(0)` between iterations, which only yields to the event loop's task queue but doesn't give enough time for worker thread callbacks to deliver their last updates. Changed to `asyncio.sleep(0.020)` (20ms) to allow worker thread `call_soon_threadsafe` callbacks to be processed. Also increased `_MAX_DRAIN_ROUNDS` from 5 to 8 for more robust content draining.
- **Streaming parameters hardened — `flush_interval_ms` minimum raised from 50ms to 70ms**: Aligned with Feishu CardKit official `print_frequency_ms` default (70ms). This prevents users from configuring server-side flush intervals below the client-side rendering interval, which could cause over-buffering or frequency control issues. All streaming parameters now verified ≥ official defaults: `print_frequency_ms=70`, `print_step=1`, `_ANSWER_FAST_STREAM_MS=70ms`, `CARDKIT_MS=80ms`.
- **Fixed premature card finalization — `COMPLETING` removed from `_TERMINAL` state set**: Root cause was `COMPLETING` being in `_TERMINAL`, causing `_get_active_session()` to return `None` during the COMPLETING state. Late-arriving `on_answer`/`on_thinking` callbacks were silently dropped, resulting in incomplete answer content on the card when the footer appeared. Fix: (1) Removed `COMPLETING` from `_TERMINAL` — it is a transitional state, not a true terminal state. Now `on_answer`/`on_thinking` can still update `unified_state` during COMPLETING, while `_schedule_linear_flush` still refuses to schedule new flushes (the drain handles it). (2) Enhanced drain loop in `_do_linear_complete`: iterative drain with yield between rounds to catch late-arriving content from the agent worker thread. (3) Content completeness guard in `_preservative_seal` now **flushes** (not just clears) dirty data before close_streaming. (4) `_schedule_card_update` (non-linear mode) now also explicitly blocks during COMPLETING state.
- **Fixed streaming parameters below official defaults**: `print_frequency_ms` raised from 10ms to 70ms (official Feishu CardKit default). The previous value was too aggressive and could cause rendering instability. Per Feishu documentation, the default streaming update interval is 70ms and default step is 1 character.
- **Answer-only fast-stream throttle aligned to official default**: `_ANSWER_FAST_STREAM_MS` raised from 50ms to 70ms, matching the official `print_frequency_ms` default. Server-side flush interval and client-side render interval now work in harmony.
- **Fixed card summary stuck at "处理中..." after completion**: The card's `config.summary` field was never updated after streaming completed. During streaming, the summary showed "处理中..."; after `close_streaming`, Feishu displays the summary in the conversation list. Without updating it, the conversation list would forever show "处理中..." even though the card was completed. Fix: `cardkit_close_streaming` in `feishu/client.py` now accepts an optional `summary` parameter. When closing streaming mode, the method also updates the card's summary text via the Feishu settings API. The `_preservative_seal` and `_do_linear_complete` methods in `controller/linear_mixin.py` and `_do_complete_inner` in `controller/mixin.py` now compute the summary from the answer text (or reasoning text as fallback) and pass it to `close_streaming`.

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
