<h1 align="center">hermes-lark-streaming</h1>

<p align="center">
  <img src="https://img.shields.io/badge/项目-Vibe%20Coding-ff69b4" alt="Vibe Coding">
  <a href="https://larkcommunity.feishu.cn/wiki/DKkpwgMcJiglIhk88N4cqJEan5f?from=from_copylink"><img src="https://img.shields.io/badge/docs-知识库-3370FF?logo=feishu&logoColor=white" alt="知识库文档"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-4caf50.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.11+-3776AB.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/version-0.11.0-ff9800.svg" alt="Version">
</p>

<p align="center">
<a href="https://applink.feishu.cn/client/message/link/open?token=AmoQJk5dwczIahKlW78ADLU%3D"><img src="https://img.shields.io/badge/官方唯一交流群-中国-red" alt="官方交流群"></a>
</p>

<p align="center">
<a href="README.md">English</a> | 中文版
</p>

为 Hermes Agent 提供飞书/Lark CardKit v2.0 流式消息卡片插件 — 实时 AI 响应展示，支持打字机效果、工具面板、推理过程等。

> 基于 [Cheerwhy/hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming) v0.7.0 版本 fork 后进行改造和优化
>
> ⚠️ **与上游插件不兼容** — 如已安装原版 `Cheerwhy/hermes-lark-streaming`，请先卸载后再安装本插件。

---

## 功能特性

- **流式卡片** — AI 响应实时在交互式卡片中展示，带打字机效果
- **线性模式** — 单卡片动态渲染思考、工具调用和答案，支持自动拆卡
- **CardKit v2.0** — 优先使用飞书 CardKit 流式 API，自动降级到 IM PATCH
- **终端卡片** — 完成后显示完整结果，包括 token 使用量、耗时等信息
- **消息保护** — 检测消息被删除/撤回后自动终止更新
- **图片解析** — 自动识别 markdown 图片引用，下载并上传为飞书 img_key
- **中断处理** — 处理 /stop 命令和消息中断，显示中断状态并自动开启新会话
- **多语言** — 内置中英文双语卡片文本，根据飞书客户端语言自动切换
- **插件管理** — 通过 `hermes plugins` 安装/卸载，无需修改源文件
- **运行时补丁** — 使用 monkey patching，不修改磁盘上的源文件

![功能预览](assets/img1.png)

---

## 快速开始

### 前置要求

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)（已运行，已配置飞书平台）
- Hermes CLI 支持插件系统（可用 `hermes plugins` 命令）

### 安装

```bash
hermes plugins install https://gitee.com/Aowen-Nowor/hermes-lark-streaming
```
或
```bash
hermes plugins install https://github.com/Aowen-Nowor/hermes-lark-streaming
```

提示时输入 `Y` 启用插件，然后重启网关：

```bash
hermes gateway restart
```

### 卸载

```bash
# 1. 先清理注入的配置（插件代码还在时执行）
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m hermes_lark_streaming cleanup

# 2. 卸载插件
hermes plugins uninstall hermes-lark-streaming

# 3. 重启网关
hermes gateway restart
```

> **为什么不用 `python3 -m`？** Hermes 运行在自建的虚拟环境中，系统 `python3` 没有插件的依赖（如 `PyYAML`、`lark-oapi`），因此 `python3 -m hermes_lark_streaming` 大概率会失败。请使用 `HERMES_PYTHON`（Hermes 虚拟环境的 Python）。若 Hermes 安装在非默认路径，请相应调整。

### 验证安装

```bash
# 检查插件状态
hermes plugins list

# 查看日志
grep hermes_lark_streaming ~/.hermes/logs/agent.log

# 验证插件配置和凭据（使用 Hermes 的 Python）
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m hermes_lark_streaming status
```

> **排障提示**：安装后若无卡片效果，请检查：(1) `hermes plugins list` 显示插件已启用；(2) `~/.hermes/plugins/` 下无备份目录干扰（删除 `*.bak` 目录）；(3) 飞书凭据已配置（见[飞书凭据](#飞书凭据)）。

---

## 配置说明

所有配置项位于 `~/.hermes/config.yaml` 的 `streaming:` 节下。

> **自动注入**：插件首次加载时，会自动在 `config.yaml` 顶层添加 `streaming:` 配置段（使用下方默认值）。卸载时，请先运行 `cleanup` 命令（见[卸载](#卸载)）清除该配置段。

> **注意**：Hermes 原生也有 `display.streaming: false` 配置项，该配置控制 **CLI/TUI 终端**输出（响应是否在终端流式显示），与本插件的流式卡片**无关**。本插件只读取 `streaming:` 节。

### 插件启用配置

在 `~/.hermes/config.yaml` 中启用本插件：

```yaml
plugins:
  enabled:
    - hermes-lark-streaming
  disabled: []
```

如果 `plugins` 节不存在，需要手动添加。安装后也可以通过以下命令启用：

```bash
hermes plugins enable hermes-lark-streaming
```

禁用插件：

```bash
hermes plugins disable hermes-lark-streaming
hermes gateway restart
```

### 可用配置项

```yaml
streaming:
  enabled: true              # 启用流式卡片
  linear: true               # 线性模式：单卡片原地更新，支持自动拆卡
  panel_expanded: false      # 完成态卡片中面板（工具、推理）是否保持展开
  card_ttl_sec: 600         # 卡片存活检测超时（秒）
  inject_time: false         # 在用户消息前注入当前时间（详见下方"时间注入"说明）

  footer:
    fields:
      - [status, elapsed, model, api_calls]
      - [tokens, context, history_offset, compression_exhausted]
      # 可用字段说明：
      #   status      — 回复状态（✅ 已完成 / ❌ 出错 / 🛑 已停止）
      #   elapsed     — AI 回复耗时
      #   model       — 使用的模型名称
      #   api_calls   — 本轮对话的 API 调用次数
      #   tokens      — Token 用量（↑ 输入 ↓ 输出）
      #   context     — 上下文窗口用量（已用/总量 百分比）
      #   history_offset — 对话历史偏移量；值越大对话越长，值突然变小说明发生了上下文压缩
      #   compression_exhausted — 上下文已满，即使压缩也无法适应上下文窗口时显示（⚠ 上下文已满）
      # 每个内层列表为页脚的一行，字段仅在有值时显示
    show_label: true         # 是否显示字段标签（true/false）
```

### 时间注入（`inject_time`）

开启 `streaming.inject_time: true` 后，插件会在每条用户消息前添加当前时间，让 AI 模型无需调用 `date` 工具即可感知当前时间。

**格式**：`<time>HH:MM:SS</time> <原始消息>`（例：`<time>14:30:05</time> 你好`）

**为什么用 XML 标签？**
- LLM 普遍理解 XML 标签是结构化元数据，不会在回复中模仿该格式
- 方括号格式（``[14:30:05 CST]``）可能被部分模型忽略，或在回复中学样也加时间前缀
- 不含日期，因为 Hermes 系统提示词中已包含当前日期
- 不含时区后缀，因为系统提示词已确定时区上下文

**核心特性**：
- **Prefix Cache 安全**：时间前缀与原始消息一起写入对话数据库，确保下轮从 DB 加载的历史与上轮 API 收到的一致，从而保证前缀缓存一致性——**所有场景下零额外缓存影响**（全程开启、全程关闭、中途开启/关闭）。
- **Token 开销**：每条 user message ≈ 6 tokens；N 轮对话累计 ≈ (N-1)×6 tokens。
- **副作用**：会话查看器（如 Hermes Web UI）中用户消息将显示时间前缀。
- **边界情况处理**：群聊中 gateway 已设置 `persist_user_message`（observed_group_context）时，时间前缀同时添加到 `persist_user_message`，避免时间前缀丢失。

### 飞书凭据

插件按以下优先级读取凭据：

| 优先级 | 来源 | 示例 |
|--------|------|------|
| 1 | 环境变量 | `FEISHU_APP_ID`、`FEISHU_APP_SECRET` |
| 2 | 文件 | `~/.hermes/.env` |
| 3 | 配置文件 | `streaming.feishu.app_id` |

```bash
# ~/.hermes/.env 示例
FEISHU_APP_ID=cli_xxxxxx
FEISHU_APP_SECRET=xxxxxx
FEISHU_BASE_URL=https://open.feishu.cn/open-apis
```

### 推理面板显示

推理面板的可见性由 `display.show_reasoning` 或 `display.platforms.feishu.show_reasoning` 控制：

```yaml
display:
  show_reasoning: true  # 在飞书卡片中显示推理面板
```

---

## 开发者指南

> 📖 **[SKILL.md](SKILL.md)** — LLM 快速上手指南。阅读本文档后，你应能立即理解项目架构、关键设计决策、常见陷阱，并高效地进行代码修改或功能扩展。

---

## 更新日志

> 完整版本历史请查看 [CHANGELOG.md](CHANGELOG.md)

---

## 致谢
