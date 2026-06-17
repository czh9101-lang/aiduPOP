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

## 10. 测试结构

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
