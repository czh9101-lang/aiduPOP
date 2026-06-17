# 🧠 hermes-lark-streaming — LLM 快速上手指南

> **Purpose**: 项目技能卡片。阅读后应能理解架构、关键设计决策、常见陷阱，并高效修改代码。

---

## 1. 项目概述

**hermes-lark-streaming** 是 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的飞书/Lark CardKit v2.0 流式卡片插件。AI 对话过程中实时更新飞书交互卡片（打字效果、统一面板、工具步骤、推理过程、完成态统计等）。

| 属性 | 值 |
|------|-----|
| 版本 | 1.1.0 (DEV) | 协议 | MIT | Python | ≥3.11 | 与上游 | ⚠️ **不兼容** |

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
| `├ callbacks.py` | ~230 | 回调包装 | 5 个内部 wrapper + `already_streamed` 透传 + 长度去重 |
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
| `└ linear_mixin.py` | ~1600 | 线性模式编排 | 统一面板更新、保留式封卡、卡片级安全网、TTL 延长 |
| **state/** | | **状态与数据子包** | |
| `├ __init__.py` | ~22 | 重导出门面 | CardSession + TextState + UnifiedLinearState + CardPhase + 工具类 |
| `├ phase.py` | ~147 | 卡片生命周期状态机 | `CardPhase`/`TerminalReason`/`CardVisualState` + `PHASE_TRANSITIONS` + 转换/视觉工具函数 |
| `├ session.py` | ~270 | CardSession 数据类 | __slots__ 数据类 + `transition()`/`should_proceed()`/`is_stale_create()` |
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

**4.12 静态卡片表格降级 (v1.1.0)**: Cron/Gateway 静态卡片使用 `_MAX_CRON_TABLES = 5` 表格降级阈值（飞书 Card 2.0 单卡硬限），超过 5 张表格自动降级为代码块渲染。流式卡片仍使用 `_MAX_CARD_TABLES = 20` 阈值不变。

**4.13 标题颜色统一 (v1.1.0)**: 工具步骤标题去掉状态文字（Running/Succeeded/Failed），改为仅用颜色+加粗区分状态：`orange-300` 进行中、`green` 已完成、`red` 失败。推理轮次标题新增 `_build_reasoning_round_title()` 辅助函数，统一格式为 `<font color='颜色'>**加粗文字**</font>`，与工具标题对齐。

**4.14 推理内容缩进 (v1.1.0)**: 推理轮次的思考内容从 `markdown` tag 改为 `div` + `lark_md` + `margin: "0px 0px 0px 22px"`，与工具步骤的 detail/output 缩进对齐。

**4.15 Schema 错误详情提取 (v1.1.0)**: `FeishuAPIError` 新增 `extract_schema_detail()` 方法，从 300315 错误中提取具体非法属性信息（如 `unknown property 'icon' on 'plain_text'`），3 处 schema error 日志新增 `detail:` 字段。

**4.16 并发消息 Epoch 校验 (v1.1.0)**: `on_reasoning`/`on_tool_update`/`on_answer` 三个回调入口添加 epoch 校验，防止用户快速连发多条消息时旧回调污染新卡片内容。

**4.9 统一面板超限压缩 (v1.0.6)**: 飞书卡片2.0硬性限制200个元素/组件，每个带 `tag` 属性的 JSON 对象都算1个元素（包括嵌套的 `standard_icon`、`plain_text`、`lark_md`）。当推理轮次或工具步骤过多导致元素数接近200时，`build_unified_panel` 自动裁剪超出部分，将早期项目折叠为一行提示（`⚡ 还有X轮早期推理、Y步早期操作已折叠`），确保卡片元素永不超限。配置项：`max_tool_steps`（默认20，范围1~100）和 `max_reasoning_rounds`（默认20，范围1~100）。面板标题始终显示实际总数，折叠提示仅影响面板内展示的内容。

**4.10 封口顺序优化 (v1.0.6)**: `_preservative_seal` 中的 `close_streaming` 移到 `batch_update` 之后执行——先写入内容+页脚再关闭流式模式。这样即使封口失败（如 300305 超限），卡片也已包含完整内容和页脚，避免被"冻住"但缺页脚的尴尬状态。

**4.11 卡片级元素安全网 (v1.0.6)**: 第一层裁剪（4.9）按条目数限制，无法精确控制实际元素数（每个工具步1~7个元素、每个推理轮次1~4个元素）。因此安全网上移到卡片层：封卡时已知全部元素（面板+answer+footer+error），通过 `_count_tag_objects` 递归计算总 tag objects，超过195（200-5缓冲）则从面板children头部逐项裁剪，直到总元素数≤195。answer、footer、error panel 永不裁剪。两条封卡路径均覆盖：`_preservative_seal`（逐增量封卡，模拟封卡后元素计数）和 `build_unified_complete_card`（全卡重建，构建后直接调用 `_enforce_card_element_limit`）。

**4.7 卡片生命周期 (v1.0.2)**: 4 阶段渐进式卡片构建：Phase 1 用户消息 → 仅创建 "正在加载上下文..." + 加载图标的占位卡片（2 元素，无面板无回答）；Phase 2 首 LLM token → 删加载提示、通过 `add_elements` 添加统一面板 + 回答元素（1 次 `batch_update`）；Phase 3 流式更新面板内容 + 回答文本；Phase 4 完成 → 添加页脚。

**4.8 卡片摘要更新 (v1.0.3)**: 卡片的 `config.summary` 字段在流式期间显示 "处理中..."。飞书在 `close_streaming` 后将 summary 展示在会话列表中。如果不在完成时更新 summary，会话列表会永久显示 "处理中..."。修复：`cardkit_close_streaming` 新增可选 `summary` 参数，关闭流式模式时同时通过飞书 settings API 更新卡片摘要文本。**Bug #3 关键修复**：`cardkit_close_streaming` 和所有卡片构建器现在同时更新 `summary.content` 和 `summary.i18n_content`（zh_cn + en_us）。飞书根据用户语言偏好显示 `i18n_content.<locale>`——中文用户看到 `zh_cn`。如果只更新 `content` 而不更新 `i18n_content`，中文用户的会话列表会一直显示"处理中..."，即使 `content` 已更新为回答文本。`cards.py` 新增 `_build_summary()` 辅助函数，统一生成包含双语 i18n_content 的 summary 字典。

---

## 5. CardSession 状态机

参考 [openclaw-lark](https://github.com/larksuite/openclaw-lark) 的 `StreamingCardController` 设计，v1.0.3 引入显式状态转换图、终端原因追踪、epoch 机制、统一守卫等增强。

### 5.1 阶段转换图 (PHASE_TRANSITIONS)

```
IDLE ──────► CREATING ──────► STREAMING ──────► COMPLETING ──────► COMPLETED
  │               │                 │                 │
  │               │                 │                 ├→ CREATION_FAILED
  │               │                 │                 │
  ├─► ABORTED     ├─► CREATION_FAIL ├─► ABORTED       ├─► ABORTED
  │               │                 │                 │
  └─► TERMINATED  └─► TERMINATED    └─► TERMINATED    └─► TERMINATED
```

**终端阶段** (吸收态，无出边): `{COMPLETED, CREATION_FAILED, ABORTED, TERMINATED}`

**新阶段**:
- `CREATION_FAILED` — 替代旧的 catch-all `FAILED`，语义更明确：卡片创建失败 → 回退到静态交付
- `TERMINATED` — 消息被删除/撤回 → 立即停止所有更新，独立于 ABORTED 和 CREATION_FAILED

**向后兼容**: `FAILED` 作为 `CREATION_FAILED` 的别名保留，`session.state == FAILED` 仍可工作。

### 5.2 TerminalReason — 终端原因追踪

| TerminalReason | 终端阶段 | 说明 |
|---|---|---|
| `NORMAL` | COMPLETED | 流式正常完成 |
| `ERROR` | COMPLETED | 回复生成期间出错 |
| `ABORT` | ABORTED | 用户主动取消 |
| `UNAVAILABLE` | TERMINATED | 源消息被删除/撤回 |
| `CREATION_FAILED` | CREATION_FAILED | 卡片创建失败 |

### 5.3 CardVisualState — 视觉外观与生命周期分离

| CardVisualState | 对应阶段 | 卡片外观 |
|---|---|---|
| THINKING | IDLE, CREATING | 黄色/中性头部，"思考中..." |
| STREAMING | STREAMING, COMPLETING | 无头部，流式文本，工具面板 |
| COMPLETE | COMPLETED | 绿色头部，可折叠推理，页脚 |
| ERROR | CREATION_FAILED, TERMINATED | 红色头部，错误通知 |
| ABORTED | ABORTED | 橙色头部，"已停止"通知 |

### 5.4 CardSession 新增方法

| 方法 | 说明 |
|---|---|
| `transition(to, source, reason)` | 验证转换合法性，自动设置 terminal_reason/terminal_source，日志记录 |
| `should_proceed(source)` | 统一守卫：终端检查 + UnavailableGuard 检查 |
| `is_terminal_phase` | 属性：是否在终端阶段 |
| `visual_state` | 属性：当前视觉状态 |
| `is_stale_create(epoch)` | Epoch 机制：检查创建回调是否过期 |
| `enter_terminal(reason, source)` | 统一终端入口：设置原因、来源、递增 epoch |

### 5.5 COMPLETING 过渡状态

COMPLETING: 状态转移在 `await` 前同步执行防竞态。COMPLETING 不在 `_TERMINAL` 集合中——`on_answer`/`on_thinking` 在 COMPLETING 期间仍可更新 `unified_state`，避免晚到回调被静默丢弃。

**重要 (v1.0.3)**: COMPLETING 状态转换后，`_do_linear_complete()` 会先 **drain** 剩余脏数据（answer/panel），确保所有内容都发到飞书后，才执行 `mark_completed()` → close streaming → add footer。`_complete_session()` 不再提前调用 `mark_completed()`，避免取消 pending flush timer 导致数据丢失。`_preservative_seal` 的内容完整性守卫从"仅清除标记"升级为"实际flush"——在 `close_streaming` 前先通过 API 将剩余脏数据刷到卡片，避免内容永久丢失。

**重要 (v1.0.5)**: `on_interrupted` 新增 COMPLETING 短路——当旧 session 已在 COMPLETING 状态时，跳过 abort 逻辑（不标记 `_was_aborted`、不设 `ABORTED`、不调 `_complete_session`），让 `_do_linear_complete` 自然走完。但新 session 创建和 `_interrupt_map` 更新仍照常执行。这修复了"正常完成的卡片被新消息覆盖成'已停止'"的 bug：COMPLETING 不在 `TERMINAL_PHASES`，`_get_active_session` 仍返回该 session，导致 `on_interrupted` 误判为"正在回复中被中断"。`on_aborted`（用户 /stop）同样适用 COMPLETING 短路——检测到 COMPLETING 时标记 `_was_aborted = True`，跳过 abort 逻辑，让 `_do_linear_complete` 自然走完。

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
  max_tool_steps: 20           # 统一面板最多显示的工具步骤数（默认20，范围1~100）
  max_reasoning_rounds: 20     # 统一面板最多显示的推理轮次数（默认20，范围1~100）
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

### 10.2 内容重复→consumed 检查 + `already_streamed` 透传
`_thinking_wrapper` 检查 consumed 返回值：卡片消费→return，已消费→dedup 跳过，未消费→原始回调降级。

**关键修复 (v1.0.3 迭代)**：Hermes 的 `interim_assistant_callback` 支持 `already_streamed` kwarg——当 `already_streamed=True` 时，表示 Hermes 已经通过 `stream_delta_callback` 投递了该文本，插件不应再通过 `on_thinking_delta` 处理（否则导致回答文本重复/乱码）。旧版 `_thinking_wrapper` 忽略此参数，一律走 `on_thinking_delta`，造成双重投递。修复：`_thinking_wrapper` 现在检查 `already_streamed`——当 True 时，跳过 `on_thinking_delta`，直接透传给原始回调（供 Hermes 的 `_stream_consumer.on_segment_break()` 使用）。

**关键修复 (v1.0.3 迭代)**：去重机制从精确字符串匹配升级为长度追踪。旧实现存储最后一次流式增量块（`_stream_consumed_texts`），用精确字符串比较判断是否已消费。但 `interim_assistant_callback` 投递的是累积文本（长度与最后一次增量不同），精确匹配永远失败，去重失效。修复：新增 `_stream_consumed_len` 字典，按 eid 追踪已消费文本的总长度，基于长度偏移提取新内容，替代精确字符串匹配。`_linear_on_thinking` 中新增调试日志记录去重决策过程。

### 10.3 外部参数 NoneType 防护
外部字符串做切片/下标时必须防御 None：`(message_id or "?")[:12]`。版本号绝不硬编码 fallback。

### 10.4 contextvars 不跨线程
用 `_thread_local_ctx` 手动传递；`_run_agent` 中设置 thread-local。

### 10.5 card_sent 区分完成与中断
返回 None 两种含义：`card_sent=True`→正常完成抑制文本；`card_sent=False`→真正 abort/error。

### 10.5b on_interrupted 必须 COMPLETING 短路，但不能跳过新 session 和 _interrupt_map
**关键修复 (v1.0.5)**：`on_interrupted` 在旧 session 处于 COMPLETING 状态时，只跳过 abort 逻辑（不标记 `_was_aborted`、不设 `ABORTED`、不调 `_complete_session`），但新 session 创建和 `_interrupt_map` 更新仍照常执行。如果直接 `return` 跳过全部逻辑，新消息可能没有 card session，`_interrupt_map` 未设置会导致 `on_completed` 的消息 ID 重定向失败。`on_aborted`（/stop）同样需要 COMPLETING 短路——用户 /stop 打到正在 drain 的 session 时，直接设 ABORTED 同样会取消 flush、丢失最后一段内容，并触发 double-complete 竞态。修复：`on_aborted` 检测到 COMPLETING 时标记 `_was_aborted = True`（让封卡显示"已停止"），然后 return 跳过 abort 逻辑。

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

### 10.19 关闭流式时必须更新卡片摘要（含 i18n_content）
**关键修复 (v1.0.3)**：卡片 `config.summary` 在流式期间为 "处理中..."，飞书在 `close_streaming` 后将会话列表中的摘要从流式摘要切换为 `config.summary` 值。如果不在 `close_streaming` 时同时更新 summary，会话列表永久显示 "处理中..."——即使卡片内容已完成。修复：`cardkit_close_streaming` 接受可选 `summary` 参数，关闭流式时一并调用飞书 settings API 更新 `config.summary`。调用方（`_preservative_seal`、`_do_linear_complete`、`_do_complete_inner`）从回答文本计算摘要（无回答则使用推理文本回退）并传入。

**Bug #3 关键修复**：`cardkit_close_streaming` 和所有卡片构建器现在同时更新 `summary.content` 和 `summary.i18n_content`（zh_cn + en_us）。飞书根据用户语言偏好显示 `i18n_content.<locale>`——中文用户看到 `zh_cn`。如果只更新 `content` 而不更新 `i18n_content`，中文用户的会话列表会一直显示"处理中..."，即使 `content` 已更新。`cards.py` 新增 `_build_summary()` 辅助函数统一生成包含双语 i18n_content 的 summary 字典。

**v1.0.3 迭代修复**：飞书 CardKit 2.0 文档明确指出，会话列表预览在 `streaming_mode` 从 `true` 变为 `false` 时**原子地**更新为 `config.summary.content`。`summary.content` 必须包含在 `close_streaming` 请求本身中——单独的 `cardkit_update_summary` 调用不可靠。修复：在 `cardkit_close_streaming` 中传递 `summary` 参数（而非 `summary=""`），飞书在 `streaming_mode: false` 转换时原子更新会话列表预览。`cardkit_update_summary` 仅作为 TTL 自动关闭等边界情况的兜底——当 `close_streaming` 因流式已关闭（300309）而跳过时，使用 `cardkit_update_summary` 作为独立调用更新摘要。

### 10.20 `close_streaming` 只能调用一次（`_streaming_closed` 守卫）
**关键修复 (v1.0.3 迭代)**：`close_streaming` 对同一张卡片只能调用一次。重复调用会导致 300317 sequence conflict，因为飞书服务端的 sequence 已在第一次成功调用后递增，后续调用使用的本地 sequence 已过期。v1.0.3 初版中，`_preservative_seal` 的主路径和重试路径都会调用 `close_streaming`——当主路径的 `close_streaming` 成功但后续 `batch_update` 遇到 300317 时，重试路径会再次调用 `close_streaming`，触发二次 300317，形成级联失败。修复：`CardSession` 新增 `_streaming_closed` 布尔标志，所有代码路径（preservative seal、retry、fallback、drain、flush）在调用 `close_streaming` 前检查此标志，成功后设置此标志。遇到 300309 (CARDKIT_STREAMING_CLOSED) 错误时也设置此标志，防止后续重试。

### 10.21 重试路径必须重建变量，不能引用 try 块中的局部变量
**关键修复 (v1.0.3 迭代)**：`_preservative_seal` 的 300317 重试路径引用了 `panel["header"]`，但 `panel` 仅在 try 块中 `close_streaming` 调用之后才赋值。当 `close_streaming` 成功但 `batch_update` 失败（300317）时，`panel` 从未被赋值，导致 `UnboundLocalError`，崩溃了整个恢复路径。修复：重试路径始终从当前状态重建 `panel`（`retry_panel = build_unified_panel(...)`），而非引用 try 块的局部变量。

### 10.22 使用 `session.transition()` 进行验证状态转换
新代码应使用 `session.transition(to, source, reason)` 而非直接赋值 `session.state = to`。`transition()` 会验证转换合法性（参照 `PHASE_TRANSITIONS`），拒绝非法转换并记录日志。直接赋值仍可工作（向后兼容），但无法提供验证保护。终端转换会自动设置 `terminal_reason`/`terminal_source` 并递增 `create_epoch`。

### 10.23 使用 `session.should_proceed()` 替代散落的双重判断
旧模式：`if session.state in _TERMINAL: return` + `if session.guard.should_skip(source): return`。新模式：`if not session.should_proceed(source): return`。统一守卫合并了终端阶段检查和 UnavailableGuard 检查，减少遗漏和重复。

### 10.24 `CREATION_FAILED` 替代旧的 `FAILED`
旧的 `FAILED` 是 catch-all，无法区分"卡片创建失败"和"消息被删除"。新架构拆分为 `CREATION_FAILED`（卡片创建失败 → 回退到静态交付）和 `TERMINATED`（消息被删除/撤回 → 停止更新）。`FAILED` 作为 `CREATION_FAILED` 的别名保留（值均为 `"creation_failed"`），现有代码 `session.state == FAILED` 不受影响。但注意：直接使用字面量 `"failed"` 的代码将不再匹配——必须更新为 `"creation_failed"` 或使用 `FAILED`/`CREATION_FAILED` 常量。

### 10.25 `_maybe_wrap_callbacks` 必须同时检查两个回调的包装标记
**关键修复 (v1.0.3 迭代)**：当 `stream_delta_callback` 为 None（DeepSeek 等模型）时，`_maybe_wrap_callbacks` 中的守卫仅检查 `stream_delta_callback` 是否已有 `_hls_wrapper` 标记，导致 `interim_assistant_callback` 被双重包装。每次调用触发 `on_thinking_delta` 两次，产生内容翻倍（"TheThe user user is is saying saying..."）。修复：守卫同时检查 `stream_delta_callback` AND `interim_assistant_callback` 的 `_hls_wrapper` 标记。同时改进 `_thinking_wrapper` 的去重逻辑：使用基于长度的去重替代精确文本匹配，并处理 Hermes 传入的 `already_streamed` kwargs。

### 10.25b `_linear_on_thinking` 必须检查 `_native_reasoning_active` 防止推理重复
**关键修复 (v1.0.3 迭代)**：当模型提供原生 `reasoning_callback`（如 DeepSeek、QwQ），推理文本通过 `on_reasoning → on_reasoning_delta` 增量投递。同时 `interim_assistant_callback` 也以累积形式投递相同的推理文本。如果没有 `_native_reasoning_active` 守卫，`_linear_on_thinking` 会再次调用 `on_reasoning_delta` 追加累积文本，导致折叠面板中每个 token 重复（"TheThe user user is is saying saying..."）。修复：`_linear_on_thinking` 中检查 `state._native_reasoning_active`——当原生推理回调已激活时，跳过 `on_reasoning_delta` 调用和 `_schedule_linear_flush`。`_native_reasoning_active` 标志由 `on_reasoning` 方法在首次调用时设置为 True。

### 10.26 Epoch 机制防止过期创建回调
卡片创建是异步操作，创建期间会话可能被终止（ABORTED/TERMINATED）。如果创建回调完成后仍执行 `CREATING → STREAMING` 转换，会破坏已终止的会话状态。修复：创建前快照 `epoch = session.create_epoch`，创建后检查 `session.is_stale_create(epoch)`——如果 epoch 已变（终端阶段进入时递增），则跳过转换。

### 10.26 `already_streamed` 透传 + 长度去重 + 线性回答回退
**关键修复 (v1.0.3 迭代)**：三个根因导致回答内容不在飞书流式卡片中显示：

1. **`already_streamed` 忽略**：Hermes 调用 `interim_assistant_callback(text, already_streamed=True)` 时，`_thinking_wrapper` 仍将文本送入 `on_thinking_delta`，导致双重投递/乱码。修复：`already_streamed=True` 时跳过 `on_thinking_delta`，透传给原始回调。

2. **精确字符串去重失败**：旧实现用 `_stream_consumed_texts` 存储最后一次增量块并做精确匹配，但 `interim_assistant_callback` 投递的是累积文本，长度不同，精确匹配永远失败。修复：`_stream_consumed_len` 按 eid 追踪已消费总长度，基于长度偏移提取新内容。

3. **`on_completed` 线性回答回退缺失**：`on_completed()` 只更新 `session.text`（非线性 TextState），从不更新 `session.unified_state.answer_text`（线性模式）。当 `stream_delta_callback` 未能投递回答文本时，线性卡片无回答内容。修复：`on_completed` 新增线性回退——当会话为线性且 `unified_state.answer_text` 为空但 `answer` 参数有值时，将回答文本写入 `unified_state`。

---

## 11. 测试结构

```
tests/
  test_version.py              — 版本号读取逻辑
  test_patch.py                — Hook 函数单元测试
  test_controller.py           — 会话生命周期 + 统一面板模式 + COMPLETING 短路
  test_cardkit.py              — 卡片 JSON 构建
  test_config.py               — 配置读取
  test_flush.py                — 节流调度器
  test_text.py                 — 文本增量追踪
  test_tooluse.py              — 工具调用追踪
  test_linear.py               — 线性模式状态管理
  test_monkey_patch.py         — 时间感知/重入守卫/cron 降级
  test_unavailable_guard.py    — 消息不可用保护
  test_phase.py                — 卡片生命周期状态机（CardPhase/TerminalReason/CardVisualState/转换/epoch）
  test_gateway_card.py         — 网关卡片构建
  test_callback_interception.py — 回调拦截
  test_clarify_card.py         — 交互式明义卡片
  integration/
    test_hermes_compat.py      — Hermes 源码兼容性验证（需 HERMES_SRC_DIR）
```

运行: `HERMES_PYTHON=$(python3 ~/.hermes/plugins/hermes-lark-streaming/__main__.py python) -m pytest tests/`

### CLI 命令参考

| 命令 | 说明 |
|------|------|
| `__main__.py status` | 显示当前配置和凭据状态 |
| `__main__.py verify` | 验证环境兼容性 |
| `__main__.py cleanup` | 清除插件注入的配置（卸载前执行） |
| `__main__.py python` | 自动检测并输出 Hermes Python 解释器路径 |

`python` 命令搜索常见安装路径（Hermes Desktop `~/.hermes/hermes-agent/venv/bin/python3`、CLI/server `/usr/local/lib/hermes-agent/venv/bin/python3`、alternative `/opt/hermes-agent/venv/bin/python3`），找不到则回退到系统 `python3`。用于简化 `status`/`verify`/`cleanup` 命令的 HERMES_PYTHON 设置。

> **Note**: FlushController now supports Python 3.10+ environments without a running event loop (lazy loop resolution). Tests no longer require manual `asyncio.set_event_loop()` setup unless testing explicit event loop behavior.

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
# 自动检测 Hermes Python 路径：
HERMES_PYTHON=$(python3 ~/.hermes/plugins/hermes-lark-streaming/__main__.py python)
$HERMES_PYTHON -m pytest tests/

# 清理 + 重装
HERMES_PYTHON=$(python3 ~/.hermes/plugins/hermes-lark-streaming/__main__.py python)
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
| 卡片创建后状态不对 | `is_stale_create(epoch)` 是否过期 | state/phase.py, state/session.py |
| 回答内容不显示 | `_thinking_wrapper` 的 `already_streamed` 处理 + 去重长度追踪 + `on_completed` 线性回退 | patching/callbacks.py, controller/core.py, controller/linear_mixin.py |
| 非法状态转换被拒绝 | `transition()` 日志 + `PHASE_TRANSITIONS` 合法表 | state/phase.py |
| 消息删除后仍更新 | UnavailableGuard → `TERMINATED` 状态 | feishu/guard.py, state/session.py |
| 卡片创建失败无回退 | `CREATION_FAILED` → `_send_text_fallback` | controller/mixin.py, controller/core.py |
| 统一面板不显示 | `show_reasoning` 配置 + `UNIFIED_PANEL_ELEMENT_ID` | cardkit/elements.py |
| 面板内容不实时更新 | `_pending_flush` + Phase 3 re-flush | controller/linear_mixin.py |
| 流式关闭 (300309) | 卡片 TTL + 主动延长 | controller/linear_mixin.py |
| 封卡后面板状态异常 | 封卡是否更新面板最终状态 | controller/linear_mixin.py |
| /stop 卡片卡死 | on_aborted/on_completed 路径 | patching/gateway.py / patching/adapter.py |
| 完成卡片被覆盖成"已停止" | `on_interrupted`/`on_aborted` 是否错误触发于 COMPLETING 状态 | controller/core.py |
| 页脚早于内容出现 | drain 步骤是否执行 | controller/linear_mixin.py |
| 内容不完整就封卡 | `answer_dirty` 是否在 seal 前被 drain | controller/linear_mixin.py |
| 流式参数报错/频控 | `print_frequency_ms` ≥ 70 | cardkit/cards.py |
| 会话列表永久显示"处理中..." | `close_streaming` 是否传入 `summary` + `i18n_content` 是否同时更新 + `_streaming_closed` 守卫 + 是否使用两步更新（先关流式再更新摘要） | feishu/client.py, cardkit/cards.py, controller/linear_mixin.py |
| 面板思考内容重复（DeepSeek 模型） | `_maybe_wrap_callbacks` 是否同时检查两个回调的 `_hls_wrapper` 标记 + `_thinking_wrapper` 去重逻辑 | patching/callbacks.py |
| 会话列表完成后仍显示"处理中..." | 是否使用两步更新（先关流式，再 `cardkit_update_summary`）+ 流式已关闭时是否仍更新摘要 | feishu/client.py, controller/linear_mixin.py |
| 300317 序列冲突反复出现 | `_streaming_closed` 守卫是否生效 | controller/linear_mixin.py, state/session.py |
| preservative seal 崩溃 (UnboundLocalError) | 重试路径是否重建 panel | controller/linear_mixin.py |
| 反应拦截静默失效 | `add_reaction`/`delete_reaction` 补丁目标是否存在（Hermes 新版本改为 `_add_reaction`/`_remove_reaction`） | patching/__init__.py |
| 卡片超限300305内容重复 | 推理/工具步骤过多导致元素超200上限，`_enforce_card_element_limit` 是否触发裁剪 + `max_tool_steps`/`max_reasoning_rounds` 配置 | cardkit/cards.py, cardkit/elements.py, config/reader.py |

---

*Last updated: 2026-06-17 | Version: 1.1.0*
