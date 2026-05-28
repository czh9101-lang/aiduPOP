<h1 align="center">hermes-lark-streaming</h1>

<p align="center">
  <img src="https://img.shields.io/badge/项目-Vibe%20Coding-ff69b4" alt="Vibe Coding">
  <a href="https://larkcommunity.feishu.cn/wiki/DKkpwgMcJiglIhk88N4cqJEan5f?from=from_copylink"><img src="https://img.shields.io/badge/docs-知识库-3370FF?logo=feishu&logoColor=white" alt="知识库文档"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-4caf50.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.11+-3776AB.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/version-0.11.0-ff9800.svg" alt="Version">
</p>

<p align="center">
<a href="https://applink.feishu.cn/client/message/link/open?token=AmoQJk5dwczIahKlW78ADLU%3D"><img src="https://img.shields.io/badge/官方唯一交流群-中国-red" alt="Official Group"></a>
</p>

<p align="center">
English | <a href="README.zh-CN.md">中文版</a>
</p>

Feishu/Lark CardKit v2.0 streaming cards plugin for Hermes Agent — real-time AI response display with typing effect, tool panels, reasoning, and more.

> Based on [Cheerwhy/hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming) v0.7.0, with extensive refactoring and optimizations
>
> ⚠️ **Incompatible with the upstream plugin** — if you have the original `Cheerwhy/hermes-lark-streaming` installed, please uninstall it first before installing this version.

---

## Features

- **Streaming Cards** — Real-time AI response display in interactive cards with typing effect
- **Linear Mode** — Dynamically renders thinking, tool calls, and answer content in a single card, with automatic card splitting for Feishu's 200-element limit
- **Thinking Process** — Display model reasoning/thinking content
- **Tool Calls** — Real-time tool call status and progress with standard icons and result/error blocks
- **CardKit v2.0** — Prioritizes Feishu CardKit streaming API, auto-falls back to IM PATCH
- **Terminal Cards** — Shows complete results including token usage, elapsed time, and context info
- **Message Protection** — Auto-terminates updates when messages are deleted/recalled, avoiding invalid API calls
- **Image Parsing** — Auto-detects markdown image references, downloads and uploads to replace with Feishu img_key
- **Interrupt Handling** — Handles /stop command and message interrupts, displays interrupt status card and auto-starts new session
- **Cron Push** — Scheduled task results pushed as Feishu CardKit cards with Markdown rendering
- **i18n** — Built-in Chinese/English bilingual card text (status, tool panel, thinking labels, etc.), auto-switches based on Feishu client language
- **Error/Interrupt Display** — Errors and /stop interrupts show as collapsible red/orange panels in card body (not just footer), with 🛑 Stopped / ❌ Error status
- **Time Injection** — Optionally prepend current time to each user message, so the AI model can perceive the current time without calling the `date` tool
- **Config Backup** — Automatically backs up config.yaml before first modification (config.yaml.YYYYMMDD_HHMMSS.hermes-lark-streaming)
- **Plugin Lifecycle** — Install/uninstall via `hermes plugins install/uninstall`, no source file modification required
- **Runtime Patches** — Uses monkey patching instead of AST injection, does not modify source files on disk

![Feature Preview](assets/img1.png)

---

## Quick Start

### Prerequisites

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) (running, with Feishu platform configured)
- Hermes CLI with plugin system support (`hermes plugins` command available)

### Installation

> 插件会自动读取 Hermes 的 `HERMES_HOME` 环境变量定位安装路径（默认 `~/.hermes`），非默认路径下无需额外操作。

```bash
hermes plugins install https://gitee.com/Aowen-Nowor/hermes-lark-streaming
```
or

```bash
hermes plugins install https://github.com/Aowen-Nowor/hermes-lark-streaming
```

Enter `Y` when prompted to enable the plugin, then restart the gateway:

```bash
hermes gateway restart
```

### Uninstallation

```bash
# 1. Clean up injected config (while plugin code is still available)
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m hermes_lark_streaming cleanup

# 2. Remove plugin
hermes plugins uninstall hermes-lark-streaming

# 3. Restart gateway
hermes gateway restart
```

> **Why not `python3 -m`?** Hermes runs in its own virtual environment. The system `python3` does not have the plugin's dependencies (e.g. `PyYAML`, `lark-oapi`), so `python3 -m hermes_lark_streaming` will likely fail. Use `HERMES_PYTHON` (Hermes venv's Python) instead. If Hermes is installed in a non-default path, adjust accordingly.

### Verify Installation

```bash
# Check plugin status
hermes plugins list

# View gateway logs
grep hermes_lark_streaming ~/.hermes/logs/agent.log

# Verify plugin config & credentials (uses Hermes's Python)
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m hermes_lark_streaming status
```

> **Troubleshooting**: If no card effect appears after installation, check: (1) `hermes plugins list` shows the plugin as enabled; (2) no backup directory exists under `~/.hermes/plugins/` (remove any `*.bak` directories); (3) Feishu credentials are configured (see [Feishu Credentials](#feishu-credentials)).

---

## Configuration

All configuration items are located under the `streaming:` section in `~/.hermes/config.yaml`.

> **Auto-injection**: When this plugin is first loaded, it automatically adds the `streaming:` section to your `config.yaml` top-level with the defaults below. On uninstall, run the `cleanup` command (see [Uninstallation](#uninstallation)) first to remove this section.

> **Note**: Hermes also has a native `display.streaming: false` config which controls **CLI/TUI terminal** output. This is unrelated to this plugin's streaming cards.

### Plugin Enable Configuration

Enable this plugin in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - hermes-lark-streaming
  disabled: []
```

If the `plugins` section doesn't exist, add it manually. You can also enable via command after installation:

```bash
hermes plugins enable hermes-lark-streaming
```

To disable:

```bash
hermes plugins disable hermes-lark-streaming
hermes gateway restart
```

### Available Configuration Options

```yaml
streaming:
  enabled: true              # Enable streaming cards
  linear: true               # Linear mode: single card in-place update with auto card splitting
  panel_expanded: false      # Keep panels (tools, reasoning) expanded in completed cards
  card_ttl_sec: 600         # Card alive detection timeout (seconds)
  inject_time: false         # Inject current time before user messages (see Time Injection below)

  footer:
    fields:
      - [status, elapsed, model, api_calls]
      - [tokens, context, history_offset, compression_exhausted]
      # Available fields:
      #   status      — Reply status (✅ Completed / ❌ Error / 🛑 Stopped)
      #   elapsed     — AI response elapsed time
      #   model       — Model name used
      #   api_calls   — Number of API calls in this session
      #   tokens      — Token usage (↑ input ↓ output)
      #   context     — Context window usage (used/total percentage)
      #   history_offset — Conversation history offset; larger = longer history, sudden decrease = context compression
      #   compression_exhausted — Context window is full, compression can no longer fit (⚠ Context Full)
      # Each inner list is one row in the footer; fields only shown when they have values
    show_label: true         # Show field labels (true/false)
```

### Time Injection (`inject_time`)

When `streaming.inject_time: true`, the plugin prepends the current time to each user message so the AI model can perceive the current time without calling the `date` tool.

**Format**: `<time>HH:MM:SS</time> <original message>` (e.g., `<time>14:30:05</time> Hello`)

**Why XML-style tags?**
- LLMs universally understand XML tags as structured metadata — they won't mimic the format in their responses
- Bracket-prefixed time (``[14:30:05 CST]``) can be ignored as noise by some models, or worse, mimicked in their output
- Date is omitted because Hermes's system prompt already contains the current date
- Timezone suffix is omitted because the system prompt establishes timezone context

**Key characteristics**:
- **Prefix cache safe**: The time prefix is written to the conversation database along with the original message, ensuring that the history loaded from the DB in the next turn matches what the API received. This preserves prefix cache consistency — **zero extra cache impact** in all scenarios (always-on, always-off, mid-session enable/disable).
- **Token overhead**: ~6 tokens per user message; cumulative ~(N-1)×6 tokens for an N-turn conversation.
- **Side effect**: Conversation viewer (e.g., Hermes web UI) will show the time prefix in user messages.
- **Edge case handling**: In group chats where `persist_user_message` is already set by the gateway (observed_group_context), the time prefix is also added to `persist_user_message` to avoid losing it.

### Feishu Credentials

The plugin reads credentials in the following priority order:

| Priority | Source | Example |
|----------|--------|---------|
| 1 | Environment Variables | `FEISHU_APP_ID`, `FEISHU_APP_SECRET` |
| 2 | File | `~/.hermes/.env` |
| 3 | Config File | `streaming.feishu.app_id` |

```bash
# ~/.hermes/.env example
FEISHU_APP_ID=cli_xxxxxx
FEISHU_APP_SECRET=xxxxxx
FEISHU_BASE_URL=https://open.feishu.cn/open-apis
```

### Reasoning Panel Display

The reasoning panel visibility is controlled by `display.show_reasoning` or `display.platforms.feishu.show_reasoning`:

```yaml
display:
  show_reasoning: true  # Show reasoning panel in Feishu cards
```

---

## Developer Guide

> 📖 **[SKILL.md](SKILL.md)** — LLM 快速上手指南 / Quick-start knowledge card for LLMs. Read this document to immediately understand the project architecture, key design decisions, common pitfalls, and efficiently make code changes or extend features.

---

## Changelog

> See [CHANGELOG.md](CHANGELOG.md) for full version history.

---

## Acknowledgments
