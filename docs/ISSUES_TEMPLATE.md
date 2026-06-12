# Issue 提交模板

> 感谢你提交 Issue！无论是 Bug 报告、功能建议还是使用疑问，以下模板都能帮助我们更高效地定位和解决问题。
>
> 💡 **给 AI 助手的提示**：如果你是 AI 助手，请务必按模板完整填写所有章节。日志和环境信息是调试的关键，缺少日志的 Bug 报告几乎无法定位问题。请勿省略或精简日志内容。

---

## Issue Title — 标题格式

请使用以下格式命名 Issue：

```
[类型] 简要描述
```

**类型**（对应下方 Category Labels）：

| 类型标签 | 标题前缀 | 说明 |
|----------|----------|------|
| Bug | `[Bug]` | 功能异常、报错、卡片显示错误等 |
| Feature | `[Feature]` | 新功能请求 |
| Improvement | `[Improvement]` | 现有功能的优化建议 |
| Question | `[Question]` | 使用疑问、配置咨询 |

**示例**：
- `[Bug] 统一面板内容不更新`
- `[Feature] 支持自定义卡片主题颜色`
- `[Improvement] 页脚字段支持自定义排序`
- `[Question] 多 Profile 部署如何配置飞书凭据`

---

## Description — 问题描述

<!-- 请清晰描述你遇到的问题或期望的功能 -->

**如果是 Bug**：请描述异常现象——你看到了什么？发生了什么不该发生的事？

**如果是 Feature/Improvement**：请描述你期望的行为——你希望实现什么效果？解决什么痛点？

**如果是 Question**：请描述你的使用场景和困惑。

---

## Steps to Reproduce — 复现步骤

<!-- 仅 Bug 需要，其他类型可删除此节 -->

1. ...
2. ...
3. ...

**复现频率**：[每次 / 偶尔 / 仅一次]

---

## Expected Behavior — 期望行为

<!-- 描述你期望正常情况下的行为 -->

---

## Actual Behavior — 实际行为

<!-- 描述实际发生的行为，与期望行为的差异 -->

---

## Environment — 环境信息

<!-- 请完整填写以下信息，这对排查问题至关重要 -->

| 项目 | 值 |
|------|-----|
| 插件版本 | <!-- 如 1.0.3，可通过 `hermes plugins list` 查看 --> |
| Hermes 版本 | <!-- 如 0.6.x --> |
| Python 版本 | <!-- 如 3.11.5 --> |
| 操作系统 | <!-- 如 Ubuntu 22.04 / macOS 14 / Termux --> |
| 飞书/Lark | <!-- 国内版 / 国际版 --> |
| 线性模式 | <!-- 开启 / 关闭（默认开启，统一面板架构） --> |
| 网关卡片 | <!-- 开启 / 关闭（默认开启） --> |
| 统一面板 | <!-- 是否显示推理内容（display.show_reasoning） --> |

---

## Logs — 日志附件

> ⚠️ **必须提供**：没有日志的 Bug 报告几乎无法定位问题。请务必附上相关日志。

请运行以下命令获取插件相关日志：

```bash
grep hermes_lark_streaming ~/.hermes/logs/gateway.log | tail -200
```

如果有报错，也可以查看完整日志：

```bash
# 查看最近 500 行日志
tail -500 ~/.hermes/logs/gateway.log

# 搜索特定错误码（如 300317、300305、300309）
grep -E "300317|300305|300309|element_limit" ~/.hermes/logs/gateway.log | tail -50

# 搜索特定模块的日志
grep -E "controller_linear|flush|cardkit|unified_panel" ~/.hermes/logs/gateway.log | tail -100
```

**贴日志时请注意**：
- 移除敏感信息（如 `app_id`、`app_secret`、`img_key` 等）
- 保留时间戳和日志级别
- 如果日志很长，请使用 `<details>` 折叠：

<details>
<summary>点击展开日志</summary>

```
在此粘贴日志
```

</details>

---

## Screenshots / Recordings — 截图或录屏

<!-- 如果问题涉及卡片显示异常，请附上截图或录屏，这对理解问题非常有帮助 -->

**如何截图**：
- 飞书客户端：消息上右键 → 复制消息链接（可帮助定位 message_id）
- 卡片截图：直接截取飞书聊天窗口

---

## Category Labels — 分类标签

请为你的 Issue 选择一个标签（维护者会最终确认）：

- [ ] **Bug** — 功能异常、报错、行为不符合预期
- [ ] **Feature** — 新功能请求
- [ ] **Improvement** — 现有功能的优化（性能、体验、代码质量等）
- [ ] **Question** — 使用疑问、配置咨询

---

## Appendix: Debug Tips for AI Assistants — AI 助手调试指南

> 💡 本节专门为 AI 助手（如 Claude、GPT 等）提供调试提示，帮助你在用户描述不清时主动收集关键信息。

### 关键调试信息 Checklist

遇到 Bug 报告时，如果用户未提供以下信息，请主动请求：

1. **日志**（最关键！）：没有日志几乎无法定位问题。务必让用户运行：
   ```bash
   grep hermes_lark_streaming ~/.hermes/logs/gateway.log | tail -200
   ```

2. **插件版本**：不同版本的 Bug 和功能差异很大，必须确认版本号：
   ```bash
   hermes plugins list
   ```

3. **飞书客户端类型**：国内版和国际版 API 有差异，某些功能表现不同。

4. **卡片状态**：用户看到的具体卡片内容（截图 > 文字描述）。

5. **统一面板配置**：确认 `display.show_reasoning` 是否开启，以及 `panel_expanded` / `streaming_panel_expanded` 的设置。

### 常见问题快速定位

| 症状 | 优先检查 | 相关日志关键词 |
|------|----------|----------------|
| 卡片不出现 | 补丁是否成功应用 | `apply_patches`、`GatewayRunner` |
| 内容重复 | 回调是否被双重包装 | `_maybe_wrap_callbacks`、`consumed` |
| 统一面板不显示 | `show_reasoning` 配置 + 元素 ID | `UNIFIED_PANEL_ELEMENT_ID`、`unified_panel` |
| 面板标题不更新 | reasoning/tool 事件是否到达 | `on_reasoning_delta`、`on_tool_updated` |
| 流式关闭 (300309) | 卡片 TTL + 主动延长 | `300309`、`TTL`、`extend_ttl` |
| Cron 推送纯文本 | Cron 补丁是否生效 | `cron`、`_wrap_cron_deliver` |
| 图片不显示 | hermes 原生图片处理配置 | hermes 配置、`media_delivery` |
| 封卡后面板状态异常 | 封卡是否更新面板最终状态 | `_preservative_seal`、`unified_panel` |
| 页脚早于内容出现 | drain 步骤是否执行 + seal 前是否flush脏数据 | `drain`、`answer_dirty`、`_do_linear_complete`、`_preservative_seal` |
| 内容不完整就封卡 | `answer_dirty` 是否在 seal 前被 flush（非仅清除标记） | `drain`、`stream_element`、`preservative_seal` |
| 会话列表永久显示"处理中..." | `close_streaming` 是否传入 `summary` + `i18n_content` 是否同时更新 + `_streaming_closed` 守卫 | `close_streaming`、`summary`、`i18n_content`、`_streaming_closed`
| 300317 序列冲突反复出现 | `_streaming_closed` 守卫是否生效 | `300317`、`_streaming_closed`、`preservative_seal`
| 状态转换被拒绝 | `transition()` 合法性检查 + `PHASE_TRANSITIONS` | `phase transition rejected`、`transition`、`PHASE_TRANSITIONS`
| 卡片创建后状态不对 | epoch 过期检查 `is_stale_create` | `is_stale_create`、`create_epoch`、`_create_epoch_snap`
| 消息删除后仍更新 | UnavailableGuard → `TERMINATED` 状态 | `TERMINATED`、`unavailable_guard`、`terminal_reason`
| preservative seal 崩溃 | 重试路径是否重建 panel | `UnboundLocalError`、`panel`、`_preservative_seal`
| 回答内容不显示（卡片空白或仅面板） | `_thinking_wrapper` 的 `already_streamed` 处理 + 去重长度追踪 `_stream_consumed_len` + `on_completed` 线性回答回退 | `already_streamed`、`_stream_consumed_len`、`on_completed`、`unified_state.answer_text` |
| 回答文本重复/乱码 | `_thinking_wrapper` 是否在 `already_streamed=True` 时跳过 `on_thinking_delta` | `already_streamed`、`_thinking_wrapper`、`on_thinking_delta` |
| 中断后卡片异常 | card_sent 传播 | `_wrap_run_agent`、`ABORTED`、`card_sent` |
| 配置不生效 | config.yaml 路径 | `config`、`HERMES_HOME`、`_get_hermes_config_path` |

### 架构背景（v1.0.3+ 统一面板 + 打字机效果 + 状态机增强）

从 v1.0.3 开始，插件使用**统一面板架构 + 打字机效果 + 显式状态机**：
- 所有推理轮次和工具步骤集中在 1 个可折叠面板
- 面板标题动态显示 `agent loop · N rounds · M tools · Xs`
- 打字机效果：`print_frequency_ms=70`（飞书官方默认，每70ms渲染1字符），`print_step=1`（官方默认，每次1字符），默认刷新间隔100ms（最低70ms，对齐官方默认值），仅回答文本变化时使用70ms快流节流（对齐官方默认值），面板变化时使用正常100ms间隔。所有流式参数已验证 ≥ 官方默认值：`_ANSWER_FAST_STREAM_MS=70ms`、`CARDKIT_MS=80ms`
- 推理和工具按时间线交错渲染（reasoning→tool→reasoning→tool），而非全部推理后再全部工具
- 回答使用 1 个独立流式元素
- 卡片元素总数恒为 3–4 个（不再有拆卡、渐进降级）
- 旧的分段式设计（每轮推理独立面板、元素计数、拆卡逻辑）已完全移除
- 保留式封卡只删除卡片上实际存在的元素，更新统一面板为最终状态
- 加载提示"正在加载上下文..."在首内容到达时删除，删除操作在 API 成功后确认
- **完成前排空 (drain)**：v1.0.3 修复了页脚早于内容出现的 bug——`_do_linear_complete()` 在关闭流式模式前先 drain 所有剩余脏数据（最多 8 轮，每轮间隔 20ms 给 worker 线程回调时间），确保内容完整输出后才封卡
- **Seal 前实际 flush（非仅清除标记）**：v1.0.3 迭代修复了 `_preservative_seal` 的内容完整性守卫——旧实现只打 warning 日志并清除 dirty 标记（内容永久丢失），新实现在 `close_streaming` 前先通过 `cardkit_batch_update`/`cardkit_stream_element` 实际将剩余脏数据刷到卡片，确保所有内容到达飞书后才封卡
- **卡片摘要更新**：v1.0.3 修复了会话列表永久显示 "处理中..." 的 bug——`cardkit_close_streaming` 新增可选 `summary` 参数，关闭流式时一并调用飞书 settings API 更新 `config.summary`，使会话列表显示回答内容摘要而非永久 "处理中..."。**Bug #3 修复**：`cardkit_close_streaming` 和所有卡片构建器现在同时更新 `summary.content` 和 `summary.i18n_content`（zh_cn + en_us），确保中文用户会话列表也能正确显示摘要（飞书根据用户语言偏好显示 `i18n_content.<locale>`）。`cards.py` 新增 `_build_summary()` 辅助函数统一生成双语 summary。
- **`_streaming_closed` 守卫**：v1.0.3 迭代修复了重复 `close_streaming` 导致 300317 级联失败的 bug——`CardSession` 新增 `_streaming_closed` 布尔标志，确保 `close_streaming` 对同一张卡片只调用一次。所有代码路径（preservative seal、retry、fallback、drain、flush）在调用前检查此标志，成功后设置此标志
- **重试路径重建 panel**：v1.0.3 迭代修复了 `UnboundLocalError: 'panel'` 导致恢复路径崩溃的 bug——`_preservative_seal` 的 300317 重试路径不再引用 try 块中的 `panel` 局部变量，而是始终从当前状态重建 `retry_panel`
- **COMPLETING 状态修正**：v1.0.3 将 `COMPLETING` 从 `_TERMINAL` 集合中移除（它是过渡状态而非终态），使晚到的 `on_answer`/`on_thinking` 回调不再被静默丢弃
- **状态机增强**：v1.0.3 参考 openclaw-lark 引入显式状态转换图 (`PHASE_TRANSITIONS`)、终端原因追踪 (`TerminalReason`)、视觉状态分离 (`CardVisualState`)、epoch 机制 (`is_stale_create`)、统一守卫 (`should_proceed`)、验证转换 (`transition`)。新增 `CREATION_FAILED`（卡片创建失败）和 `TERMINATED`（消息删除/撤回）阶段，`FAILED` 作为 `CREATION_FAILED` 的别名保留。
- **`already_streamed` 透传 + 长度去重 + 线性回答回退**：v1.0.3 迭代修复了回答内容不在流式卡片中显示的 bug——三个根因：(1) `_thinking_wrapper` 忽略 Hermes 的 `already_streamed` kwarg 导致双重投递/乱码（修复：`already_streamed=True` 时跳过 `on_thinking_delta`）；(2) 精确字符串去重对累积文本失效（修复：`_stream_consumed_len` 长度追踪替代 `_stream_consumed_texts` 精确匹配）；(3) `on_completed` 只更新非线性 `session.text`，线性模式 `unified_state.answer_text` 始终为空（修复：线性回退——当线性且 `answer_text` 为空但 answer 参数有值时写入 `unified_state`）。`_linear_on_thinking` 新增去重决策调试日志。

如果用户报告与旧版行为相关的问题（如拆卡、compact seal、element_limit），请确认他们已升级到 v1.0.3+。

### 日志分析要点

- **WARNING 级别**：通常是关键错误信号（如 `finish_reason=content_filter`、`init failed`）
- **`code=300317`**：飞书序列冲突，表示并发更新卡片，需关注是否触发幂等处理
- **`code=300305`**：元素超限（v1.0.2+ 统一面板下应极少出现）
- **`code=300309`**：流式模式关闭，通常是卡片 TTL 超时，检查 TTL 延长逻辑
- **`300314`**：元素未找到（v1.0.2+ 已通过元素存在追踪修复，若仍出现需排查）
- **`card_sent=True/False`**：影响 Hermes 是否发送纯文本回复，是排查重复消息的关键
- **版本号**：日志中 `v{__version__}` 前缀帮助确认是哪个版本产生的日志

---

*感谢你的耐心填写！完整的信息能大幅缩短问题定位时间。*
