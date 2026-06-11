# 🧠 hermes-lark-streaming — LLM 快速上手指南

> **Purpose**: 项目技能卡片。阅读后应能理解架构、关键设计决策、常见陷阱，并高效修改代码。

---

## 1. 项目概述

**hermes-lark-streaming** 是 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的飞书/Lark CardKit v2.0 流式卡片插件。AI 对话过程中实时更新飞书交互卡片（打字效果、统一面板、工具步骤、推理过程、完成态统计等）。

| 属性 | 值 |
|------|-----|
| 版本 | 1.0.3 (DEV) | 协议 | MIT | Python | ≥3.11 | 与上游 | ⚠️ **不兼容** |

---

## 2. 架构全景

```
用户消息 → GatewayRunner._handle_message ── [Hook 0: on_feishu_normalize]
               ▼
        _handle_message_with_agent ── [Hook 1/8/9: started/aborted/interrupted]
               ▼
        _run_agent ── [Hook 2: on_message_completed]
               ▼
        AIAgent.run_conversation ── [inject_time 前缀注入]
            ├─ stream_delta_callback ── [Hook 4: on_answer_delta]
            ├─ reasoning_callback ──── [Hook 6: on_reasoning_delta]
            ├─ tool_progress_callback [Hook 3: on_tool_updated]
            └─ background_review_cb ── [Hook 7: on_background_review]
Cron: _deliver_result ── [Hook 10: on_cron_deliver] (async)
Background: _run_background_task ── [Hook 1/2]
```

调用链: `patching → hooks → controller → mixin → cardkit → feishu → flush`

---

## 3. 文件地图与职责

| 文件 | 行数 | 职责 | 关键点 |
|------|------|------|--------|
| **patching/** | | **运行时拦截子包** | |
| `├ __init__.py` | ~770 | 入口 + 共享状态 + 编排 | `apply_patches()` + 模块解析 + 延迟补丁 + FeishuClient 预热 |
| `├ hooks.py` | ~230 | Hook 函数层 | `_safe_hook` 统一 enabled 检查 + 异常捕获 |
| `├ gateway.py` | ~890 | GatewayRunner 包装 | 6 个 wrapper + 时间前缀注入 + cron/background |
| `├ callbacks.py` | ~230 | 回调包装 | 5 个内部 wrapper 防重复消费 |
| `└ adapter.py` | ~1030 | FeishuAdapter 包装 | send/edit/reaction/clarify 包装 + gateway card 注册 |
| **cardkit/** | | **卡片构建子包** | |
| `├ __init__.py` | ~5 | 重导出门面 | `from .elements/cards/special import *` |
| `├ elements.py` | ~680 | 原始元素构建器 | 统一面板 + answer streaming + footer |
| `├ cards.py` | ~440 | 卡片组装器 | streaming/complete/IM-fallback 卡片 |
| `├ special.py` | ~410 | 专用卡片类型 | cron/gateway/clarify 三态卡片 |
| `├ i18n.py` | 62 | 中英双语映射 | `_T` dict + `_i18n()`/`_t()`；`agent_process`（值 "agent loop"）/`rounds`/`tools_count`/`round_n` |
| `└ md.py` | 121 | Markdown 处理 | 标题/表格降级、长文本分块 |
| **controller/** | | **主控制器子包** | |
| `├ __init__.py` | ~20 | 重导出门面 | StreamCardController + CardSession + 状态常量 |
| `├ core.py` | ~720 | 主控制器(单例) | 管理生命周期，导入 CardSession |
| `├ mixin.py` | ~580 | 异步 API 编排 | 状态机 + CardKit→IM PATCH 降级链 |
| `└ linear_mixin.py` | ~1050 | 线性模式编排 | 统一面板更新、保留式封卡、TTL 延长 |
| **state/** | | **状态与数据子包** | |
| `├ __init__.py` | ~20 | 重导出门面 | CardSession + TextState + UnifiedLinearState + 工具类 |
| `├ session.py` | ~100 | CardSession 数据类 | __slots__ 数据类，独立于 controller |
| `├ linear.py` | ~300 | 统一面板状态 | `ReasoningRound` 数据类 + `UnifiedLinearState` 扁平管理 |
| `├ text.py` | ~111 | 文本增量追踪 | `<think|thinking|thought>` 标签拆分 |
| `└ tooluse.py` | ~299 | 工具调用追踪 | `ToolStep`/`ToolSession`，敏感信息脱敏 |
| **feishu/** | | **飞书 API 客户端子包** | |
| `├ __init__.py` | ~48 | 重导出门面 | `FeishuClient`, `UnavailableGuard` 等 |
| `├ client.py` | ~450 | 飞书 API 客户端 | CardKit v1/v2 + IM API，错误码分类 + 瞬态重试 + 预热支持 |
| `└ guard.py` | ~144 | 消息不可用保护 | 删除/撤回检测，30分钟 TTL |
| **flush/** | | **节流调度子包** | |
| `├ __init__.py` | ~21 | 重导出门面 | `FlushController`, `CARDKIT_MS`, `PATCH_MS` |
| `└ controller.py` | ~185 | 节流调度器 | CardKit 100ms / IM PATCH 1.5s，互斥锁 + re-flush |
| **config/** | | **配置读取子包** | |
| `├ __init__.py` | ~9 | 重导出门面 | `Config`, `_get_hermes_config_path` |
| `└ reader.py` | ~270 | 配置读取 | `_plugin_sec()` 惰性加载 + 5秒 TTL 缓存 |
| `plugin.py` | ~250 | 插件注册入口 | `register()`/`unregister()`，自动备份 config，FeishuClient 预热 |

---

## 4. 关键设计决策

**4.1 版本号唯一真值源**: `plugin.yaml` → 运行时读取(失败→"unknown") / 构建时读取(失败→raise)；`pyproject.toml` 用 `dynamic`。

**4.2 Monkey Patch 非 AST 注入**: 运行时方法替换，不修改源文件，卸载即恢复。代价：需自计时替代不可访问的局部变量。

**4.3 模块解析 + 线程安全**: `_resolve_hermes_agent_module()` 3层解析解决 Apple Silicon 冲突；`_started_msg_ids` 线程安全追踪中断；`threading.local()` 重入守卫；根 `__init__.py` 条件导入（relative 优先，absolute 兜底兼容 pytest）。

**4.4 异步 + 双重补丁**: Cron 全链路异步化(禁止 `run_coroutine_threadsafe().result()`)；`run_conversation` 模块级+实例级双重补丁；Cron/后台临时替换 `adapter.send`（卡片替换纯文本）。

**4.5 时间感知格式**: XML 标签 `<time>HH:MM:SS</time>`，LLM 不模仿，无日期/时区后缀。

**4.6 统一面板架构 (v1.0.2)**: 所有推理轮次和工具步骤放在 1 个可折叠面板中（图标 `robot_filled`），回答使用 1 个流式元素。无论对话多长，卡片始终只有 3–4 个元素。面板标题动态显示 `agent loop · N rounds · M tools · Xs`（中英文统一使用 "agent loop"）。`display.show_reasoning` 控制推理内容是否出现在面板中。`panel_events` 时间线记录事件发生顺序，面板内容按时间线交错渲染（reasoning→tool→reasoning→tool），而非全部推理后再全部工具。

**4.7 卡片生命周期 (v1.0.2)**: 4 阶段渐进式卡片构建：Phase 1 用户消息 → 仅创建 "正在加载上下文..." + 加载图标的占位卡片（2 元素，无面板无回答）；Phase 2 首 LLM token → 删加载提示、通过 `add_elements` 添加统一面板 + 回答元素（1 次 `batch_update`）；Phase 3 流式更新面板内容 + 回答文本；Phase 4 完成 → 添加页脚。

---

## 5. CardSession 状态机

```
IDLE → CREATING → STREAMING → COMPLETING → COMPLETED
                    │              │
                    ├→ FAILED      └→ (card_sent=True → suppress Hermes reply)
                    └→ ABORTED
```

COMPLETING: 状态转移在 `await` 前同步执行防竞态。终态: `{COMPLETED, FAILED, ABORTED}`（**注意**: COMPLETING 是过渡状态，不在 `_TERMINAL` 集合中。v1.0.3 修复：移除 COMPLETING 出 `_TERMINAL`，使 `on_answer`/`on_thinking` 在 COMPLETING 期间仍可更新 `unified_state`，避免晚到回调被静默丢弃）

**重要 (v1.0.3)**: COMPLETING 状态转换后，`_do_linear_complete()` 会先 **drain** 剩余脏数据（answer/panel），确保所有内容都发到飞书后，才执行 `mark_completed()` → close streaming → add footer。`_complete_session()` 不再提前调用 `mark_completed()`，避免取消 pending flush timer 导致数据丢失。`_preservative_seal` 的内容完整性守卫从"仅清除标记"升级为"实际flush"——在 `close_streaming` 前先通过 API 将剩余脏数据刷到卡片，避免内容永久丢失。

---

## 6. 卡片 API 降级链

```
CardKit v2 Streaming → CardKit v2 Create+Patch → IM Create+Patch → Hermes 纯文本
```

`FeishuClient` 首次成功后锁定通道。

---

## 7. 统一面板架构

v1.0.2 引入统一面板架构，取代旧的分段式设计。

**核心思想**: 1 个可折叠面板承载所有推理轮次和工具步骤，1 个流式元素承载回答文本。无论对话多长，卡片元素总数恒为 3–4 个。

**旧架构问题**:
- 每个 reasoning round 创建独立面板（4 元素/面板），元素数随对话线性增长
- 接近飞书 200 元素硬限，导致：100% 封卡失败率、频繁拆卡、级联故障链、流式关闭 (300309)

**统一面板结构**:
```
┌─ 统一面板 (robot_filled) ──────────────────────────────┐
│ agent loop · 3 rounds · 5 tools · 12.5s                  │
│                                                          │
│ Round 1 (3.2s)                                          │
│   推理内容...                                             │
│   search("query") ✓ 1.2s                                │
│   read("file.py") ✓ 0.8s                                │
│                                                          │
│ Round 2 (4.1s)                                          │
│   推理内容...                                             │
│   write("file.py") ✓ 2.1s                               │
│                                                          │
│ Round 3 (5.2s)                                          │
│   推理内容...                                             │
│   run("test") ✓ 3.0s                                    │
│   run("lint") ✓ 1.5s                                    │
└──────────────────────────────────────────────────────────┘

┌─ 回答流式元素 ──────────────────────────────────────────┐
│ 这是 AI 的回答文本，流式更新...                            │
└──────────────────────────────────────────────────────────┘
```

**元素 ID**:
- `UNIFIED_PANEL_ELEMENT_ID` — 统一面板
- `ANSWER_ELEMENT_ID` — 回答流式元素

**卡片生命周期 (4 Phases)**:
- **Phase 1** — 用户发送消息 → 创建占位卡片，仅含"正在加载上下文..." + 加载图标（2 个元素，无面板、无回答元素）
- **Phase 2** — 首 LLM token 到达 → 删除"正在加载上下文..."，通过 `add_elements` 添加统一面板 + 回答元素（1 次 `batch_update`）
- **Phase 3** — 流式更新面板内容（推理/工具）+ 回答文本
- **Phase 4** — 完成 → 添加页脚

**性能优化**:
- Phase 1 占位卡片仅含 2 元素，Phase 2 首内容时同一 batch_update 删加载提示 + 加面板/回答（零额外 API 开销）
- FeishuClient 在插件注册时预热 — 首条消息节省 ~50-100ms
- 默认刷新间隔从 500ms 降为 200ms — 文字更快出现

**TTL 延长**: 当卡片接近 540s 生存时间时，自动延长 TTL 600s，防止 300309 流式关闭。

**保留式封卡**: 封卡时仅删除实际存在的元素（如加载提示），更新统一面板为最终状态（非流式、按配置展开）。不再有渐进降级（compact seal / minimal seal），因为元素数永远不会超限。

---

## 8. 配置结构

```yaml
hermes_lark_streaming:
  enabled: true
  linear: true
  panel_expanded: false
  streaming_panel_expanded: false
  print_strategy: delay            # "fast" 或 "delay"
  flush_interval_ms: 100           # 70~2000ms（默认 100，打字机效果优化）
  card_ttl_sec: 600
  inject_time: false
  footer:
    show_label: false
    fields: [status, elapsed, model, cost, compression_exhausted]
```

---

## 9. Hook 索引 (11 个注入点)

| # | Hook | 签名 | 说明 |
|---|------|------|------|
| 0 | `on_feishu_normalize` | sync | 修正飞书引用消息虚假 thread_id |
| 1 | `on_message_started` | sync | 创建 CardSession |
| 2 | `on_message_completed` | sync→bool | 完成态卡片，返回是否已发卡片 |
| 3 | `on_tool_updated` | sync | 工具调用状态更新 |
| 4 | `on_answer_delta` | sync | AI 回复增量文本 |
| 5 | `on_thinking_delta` | sync | 思考内容（被跳过防重复） |
| 6 | `on_reasoning_delta` | sync | 原生推理增量 |
| 7 | `on_background_review_message` | sync | 后台审查通知 |
| 8 | `on_message_aborted` | sync | 消息异常终止 |
| 9 | `on_message_interrupted` | sync | 新消息打断旧消息 |
| 10 | `on_cron_deliver` | **async** | Cron 推送卡片 |
| 11 | `on_message_completed`(bg) | sync | 后台任务卡片（复用 Hook 2） |

---

## 10. 常见陷阱与经验教训

### 10.1 事件循环死锁
在 async 函数中绝不用 `run_coroutine_threadsafe().result()`，直接 `await`。

### 10.2 内容重复→consumed 检查
`_thinking_wrapper` 检查 consumed 返回值：卡片消费→return，已消费→dedup 跳过，未消费→原始回调降级。

### 10.3 外部参数 NoneType 防护
外部字符串做切片/下标时必须防御 None：`(message_id or "?")[:12]`。版本号绝不硬编码 fallback。

### 10.4 contextvars 不跨线程
用 `_thread_local_ctx` 手动传递；`_run_agent` 中设置 thread-local。

### 10.5 card_sent 区分完成与中断
返回 None 两种含义：`card_sent=True`→正常完成抑制文本；`card_sent=False`→真正 abort/error。

### 10.6 FlushController 线程安全
worker 线程必须用 `call_soon_threadsafe()`，`call_soon()` 不唤醒事件循环→flush 永不执行。

### 10.7 统一面板消除元素爆炸 + 按时间线交错渲染
v1.0.2 之前，每个 reasoning round / tool segment 创建独立面板（4 元素/面板），元素数随对话线性增长接近 200 硬限。统一面板架构将所有内容集中在 1 个面板 + 1 个回答元素 = 3–4 元素恒定，彻底消除拆卡和渐进降级需求。v1.0.2 中期修复：`panel_events` 时间线记录事件发生顺序，面板内容按时间线交错渲染（reasoning→tool→reasoning→tool），而非全部推理后再全部工具。

### 10.8 幂等守卫 = 同步状态转移 + 错误码容错
COMPLETING 状态同步转移 + 300317 容错，适用于异步回调竞态。

### 10.9 Monkey patch 签名确认
必须确认目标是类方法还是模块级函数；签名不匹配 = 静默失败。

### 10.10 封卡只删除实际存在的元素
v1.0.2 之前，保留式封卡盲目删除所有已知元素 ID（包括已被飞书删除的 `context_loading_hint`），导致 100% 的 300314 失败。现在封卡只删除卡片上实际存在的元素，消除了此问题。

### 10.11 状态标志必须在 API 成功后设置
`_loading_hint_removed` 等标志必须在 `batch_update` API 调用成功后才设置，不能在调用前设置。如果在 API 调用前设置，一旦 API 失败（如 sequence conflict），标志已设但实际未生效，后续不会再重试，导致"正在加载上下文..."永久残留。

### 10.12 性能参数应可配置
性能敏感参数不应硬编码。默认 100ms 刷新间隔（可配置 70~2000ms，最低 70ms 对齐飞书官方 `print_frequency_ms` 默认值）。v1.0.3 新增：仅回答文本变化时使用 70ms 快流节流（对齐飞书官方 `print_frequency_ms` 默认值），面板变化时使用正常 100ms 间隔。所有流式参数已验证 ≥ 官方默认值：`print_frequency_ms=70`、`print_step=1`、`_ANSWER_FAST_STREAM_MS=70ms`、`CARDKIT_MS=80ms`。

### 10.13 卡片生命周期 4 阶段渐进构建
Phase 1 占位卡片仅含 "正在加载上下文..." + 加载图标（2 元素），Phase 2 首 LLM token 时同一 `batch_update` 删除加载提示 + 添加面板/回答元素（零额外 API 开销），Phase 3 流式更新，Phase 4 添加页脚。初始卡片不预分配面板和回答元素。**关键修复 (v1.0.2)**：Phase 2 完成后不再立即 return，而是检查是否有新的 dirty 数据，有则 fall-through 到 Phase 3；Phase 3 完成后也检查是否有新数据到达，有则调度 re-flush，确保面板内容实时更新。

### 10.14 主动 TTL 延长
当卡片生存时间接近 540s 时，自动延长 600s，防止 300309 流式关闭。不要等到超时再处理。

### 10.15 卡片未就绪时的延迟 flush
当推理/工具 delta 在卡片创建完成之前到达（`card_message_ready=False`），`_schedule_linear_flush` 不再丢弃 flush 请求，而是标记 `_pending_flush`。卡片创建完成后立即执行延迟的 flush，确保用户无需等待下一个事件就能看到内容。

### 10.16 向后兼容别名带弃用警告
`LinearState` → `UnifiedLinearState`、`Segment` → `ReasoningRound`、`session.linear_state` → `session.unified_state`：均保留向后兼容别名，但访问时打印弃用警告，方便渐进迁移。

### 10.17 完成前排空剩余脏数据（防页脚早于内容出现）
**关键修复 (v1.0.3)**：当 `on_completed` 触发时，可能还有最后一批 answer/panel 数据尚未刷新到飞书（因为 flush 是节流的，pending timer 还没到期）。如果在关闭流式模式前不把这些数据发出去，用户会看到页脚出现在内容输出完之前。修复方案：`_complete_session()` 不再提前调用 `flush.mark_completed()`（这会取消 pending timer 丢数据），而是在 `_do_linear_complete()` 中增加 **drain 步骤**：显式检查 `answer_dirty`/`panel_dirty`/`tool_steps_dirty`，如有脏数据则直接调用 `stream_element`/`batch_update` 刷出，然后再 `mark_completed()` → close streaming → add footer。Drain 循环使用 20ms yield（而非 sleep(0)），给 worker 线程足够时间送达最后的回调。

**关键修复 (v1.0.3 迭代)**：`_preservative_seal` 的内容完整性守卫从"仅清除标记"升级为"实际flush"。旧实现只打 warning 日志然后清除 `answer_dirty`/`panel_dirty`/`tool_steps_dirty` 标记，但不清空实际内容。由于 `close_streaming` 后 `stream_element` 无法再调用，未flush的内容永久丢失——这就是"页脚早于答复内容出现"的根本原因。新实现在 `close_streaming` 之前，先通过 `cardkit_batch_update` 和 `cardkit_stream_element` 实际将剩余脏数据刷到卡片上，确保所有内容到达飞书后才封卡。

### 10.18 流式参数不低于官方推荐值
`print_frequency_ms`（飞书客户端打字机渲染间隔）官方默认 70ms，`print_step`（每次渲染字符数）官方默认 1。这两个值不可低于官方推荐，否则可能导致渲染不稳定或频控问题。服务端 flush 间隔（`_ANSWER_FAST_STREAM_MS`）也应与 `print_frequency_ms` 对齐（70ms），避免过度缓冲。

---

## 11. 测试结构

```
tests/
  test_version.py              — 版本号读取逻辑
  test_patch.py                — Hook 函数单元测试
  test_controller.py           — 会话生命周期 + 统一面板模式
  test_cardkit.py              — 卡片 JSON 构建
  test_config.py               — 配置读取
  test_flush.py                — 节流调度器
  test_text.py                 — 文本增量追踪
  test_unified.py              — 统一面板状态管理
  test_tooluse.py              — 工具调用追踪
  test_monkey_patch.py         — 时间感知/重入守卫/cron 降级
  test_unavailable_guard.py    — 消息不可用保护
  test_gateway_card.py         — 网关卡片构建
  test_callback_interception.py — 回调拦截
```

运行: `HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/`

---

## 12. 开发环境

```bash
# 克隆
git clone -b DEV https://gitee.com/Aowen-Nowor/hermes-lark-streaming.git

# 安装到 Hermes
hermes plugins install /path/to/hermes-lark-streaming

# 查看日志
grep hermes_lark_streaming ~/.hermes/logs/agent.log

# 运行测试（需要 Hermes venv 的 Python，因为依赖 lark-oapi）
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pytest tests/

# 清理 + 重装
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON ~/.hermes/plugins/hermes-lark-streaming/__main__.py cleanup
hermes plugins uninstall hermes-lark-streaming
hermes plugins install https://gitee.com/Aowen-Nowor/hermes-lark-streaming
hermes gateway restart
```

---

## 13. 版本历史

详见 [CHANGELOG.md](CHANGELOG.md)

---

## 14. 快速定位问题

| 症状 | 检查 | 文件 |
|------|------|------|
| 卡片不出现 | `grep "GatewayRunner" agent.log` | patching/__init__.py |
| 内容重复 | `interim_assistant_callback` 是否被包裹 | patching/callbacks.py |
| Cron 推送纯文本 | `grep "cron" agent.log` | patching/gateway.py |
| 后台任务纯文本 | `grep "background" agent.log` | patching/gateway.py |
| 页脚无 cache 字段 | `cache_read_tokens` 是否提取 | patching/callbacks.py |
| 页脚无 cost 字段 | `session_estimated_cost_usd` 是否提取 | patching/gateway.py |
| tokens 缺推理数 | `session_reasoning_tokens` 是否提取 | patching/callbacks.py |
| Apple Silicon 报错 | `grep "conversation_loop" agent.log` | patching/__init__.py |
| 版本号 unknown | plugin.yaml 路径 | `__init__.py` |
| 页脚耗时为 0 | `_msg_start_time` 设置 | patching/gateway.py |
| 消息删除后仍更新 | UnavailableGuard | feishu/guard.py |
| 统一面板不显示 | `show_reasoning` 配置 + `UNIFIED_PANEL_ELEMENT_ID` | cardkit/elements.py |
| 面板内容不实时更新 | `_pending_flush` + Phase 3 re-flush | controller/linear_mixin.py |
| 流式关闭 (300309) | 卡片 TTL + 主动延长 | controller/linear_mixin.py |
| 封卡后面板状态异常 | 封卡是否更新面板最终状态 | controller/linear_mixin.py |
| /stop 卡片卡死 | on_aborted/on_completed 路径 | patching/gateway.py / patching/adapter.py |
| 页脚早于内容出现 | drain 步骤是否执行 | controller/linear_mixin.py |
| 内容不完整就封卡 | `answer_dirty` 是否在 seal 前被 drain | controller/linear_mixin.py |
| 流式参数报错/频控 | `print_frequency_ms` ≥ 70 | cardkit/cards.py |

---

*Last updated: 2026-06-11 | Version: 1.0.3*
