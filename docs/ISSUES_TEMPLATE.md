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
- `[Bug] 拆卡后旧卡片跑马灯不停`
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
| 插件版本 | <!-- 如 0.19.1，可通过 `hermes plugins list` 查看 --> |
| Hermes 版本 | <!-- 如 0.6.x --> |
| Python 版本 | <!-- 如 3.11.5 --> |
| 操作系统 | <!-- 如 Ubuntu 22.04 / macOS 14 / Termux --> |
| 飞书/Lark | <!-- 国内版 / 国际版 --> |
| 线性模式 | <!-- 开启 / 关闭（默认开启） --> |
| 网关卡片 | <!-- 开启 / 关闭（默认开启） --> |

---

## Logs — 日志附件

> ⚠️ **必须提供**：没有日志的 Bug 报告几乎无法定位问题。请务必附上相关日志。

请运行以下命令获取插件相关日志：

```bash
grep hermes_lark_streaming ~/.hermes/logs/agent.log | tail -200
```

如果有报错，也可以查看完整日志：

```bash
# 查看最近 500 行日志
tail -500 ~/.hermes/logs/agent.log

# 搜索特定错误码（如 300317、300305）
grep -E "300317|300305|element_limit" ~/.hermes/logs/agent.log | tail -50

# 搜索特定模块的日志
grep -E "controller_linear|flush|cardkit" ~/.hermes/logs/agent.log | tail -100
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
   grep hermes_lark_streaming ~/.hermes/logs/agent.log | tail -200
   ```

2. **插件版本**：不同版本的 Bug 和功能差异很大，必须确认版本号：
   ```bash
   hermes plugins list
   ```

3. **飞书客户端类型**：国内版和国际版 API 有差异，某些功能表现不同。

4. **卡片状态**：用户看到的具体卡片内容（截图 > 文字描述）。

### 常见问题快速定位

| 症状 | 优先检查 | 相关日志关键词 |
|------|----------|----------------|
| 卡片不出现 | 补丁是否成功应用 | `apply_patches`、`GatewayRunner` |
| 内容重复 | 回调是否被双重包装 | `_maybe_wrap_callbacks`、`consumed` |
| 跑马灯不停 | 元素超限 / flush 失败 | `element_limit`、`300305`、`_handle_linear_flush_error` |
| Cron 推送纯文本 | Cron 补丁是否生效 | `cron`、`_wrap_cron_deliver` |
| 图片不显示 | ImageResolver 状态 | `image`、`img_key`、`ImageResolver` |
| 封卡失败 | 序列冲突 / 元素超限 | `300317`、`300305`、`_preservative_seal` |
| 中断后卡片异常 | card_sent 传播 | `_wrap_run_agent`、`ABORTED`、`card_sent` |
| 配置不生效 | config.yaml 路径 | `config`、`HERMES_HOME`、`_get_hermes_config_path` |

### 日志分析要点

- **WARNING 级别**：通常是关键错误信号（如 `finish_reason=content_filter`、`init failed`）
- **`code=300317`**：飞书序列冲突，表示并发更新卡片，需关注是否触发幂等处理
- **`code=300305`**：元素超限，需关注渐进降级是否生效（compact seal → minimal seal）
- **`card_sent=True/False`**：影响 Hermes 是否发送纯文本回复，是排查重复消息的关键
- **版本号**：日志中 `v{__version__}` 前缀帮助确认是哪个版本产生的日志

---

*感谢你的耐心填写！完整的信息能大幅缩短问题定位时间。*
