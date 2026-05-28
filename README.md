<h1 align="center">hermes-lark-streaming</h1>

<p align="center">
  <img src="https://img.shields.io/badge/项目-Vibe%20Coding-ff69b4" alt="Vibe Coding">
  <a href="https://larkcommunity.feishu.cn/wiki/DKkpwgMcJiglIhk88N4cqJEan5f?from=from_copylink"><img src="https://img.shields.io/badge/docs-知识库-3370FF?logo=feishu&logoColor=white" alt="知识库文档"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-4caf50.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.11+-3776AB.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/version-0.10.0-ff9800.svg" alt="Version">
</p>

<p align="center">
<a href="https://applink.feishu.cn/client/message/link/open?token=AmoQJk5dwczIahKlW78ADLU%3D"><img src="https://img.shields.io/badge/官方唯一交流群-中国-red" alt="Official Group"></a>
</p>

<p align="center">
English | <a href="README.zh-CN.md">中文版</a>
</p>

Feishu/Lark CardKit v2.0 streaming cards plugin for Hermes Agent — real-time AI response display with typing effect, tool panels, reasoning, and more.

> Based on [Cheerwhy/hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming) v0.7.0, with extensive refactoring and optimizations

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
- **Cron Push** — Scheduled task results pushed as Feishu cards with Markdown rendering preserved
- **i18n** — Built-in Chinese/English bilingual card text (status, tool panel, thinking labels, etc.), auto-switches based on Feishu client language
- **Plugin Lifecycle** — Install/uninstall via `hermes plugins install/uninstall`, no source file modification required
- **Runtime Patches** — Uses monkey patching instead of AST injection, does not modify source files on disk

![Feature Preview](assets/img1.png)

---

## Quick Start

### Prerequisites

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) (running, with Feishu platform configured)
- Python >= 3.11
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
# 1. Clean up injected config first (while plugin code is still available)
$(dirname $(readlink -f $(which hermes)))/python -m hermes_lark_streaming cleanup

# 2. Remove plugin files
hermes plugins uninstall hermes-lark-streaming

# 3. Restart gateway
hermes gateway restart
```

### Verify Installation

```bash
# Check plugin status
$(dirname $(readlink -f $(which hermes)))/python -m hermes_lark_streaming status

# Verify environment compatibility
$(dirname $(readlink -f $(which hermes)))/python -m hermes_lark_streaming verify

# View gateway logs
grep hermes_lark_streaming ~/.hermes/logs/agent.log
```

---

## Configuration

All configuration items are located under the `streaming:` section in `~/.hermes/config.yaml`.

> **Auto-injection**: When this plugin is first loaded, it automatically adds the `streaming:` section to your `config.yaml` top-level with the defaults below. On uninstall, this section is automatically removed. You only need to manually edit if you want to override specific values.

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
      - [status, elapsed, model, history_offset]
      - [tokens, context, api_calls]
      # Available fields: status, elapsed, model, tokens, context, api_calls, history_offset
      # Each inner list is one row in the footer
      # history_offset: larger value → longer conversation history; sudden decrease → context compression
      # api_calls: number of API calls in this session
    show_label: true         # Show field labels (true/false)
```

### Time Injection (`inject_time`)

When `streaming.inject_time: true`, the plugin prepends the current time to each user message so the AI model can perceive the current time without calling the `date` tool.

**Format**: `[HH:MM:SS CST] <original message>` (e.g., `[14:30:05 CST] Hello`)

**Key characteristics**:
- **Prefix cache safe**: The time prefix is written to the conversation database along with the original message, ensuring that the history loaded from the DB in the next turn matches what the API received. This preserves prefix cache consistency — **zero extra cache impact** in all scenarios (always-on, always-off, mid-session enable/disable).
- **Token overhead**: ~5 tokens per user message; cumulative ~(N-1)×5 tokens for an N-turn conversation.
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

## FAQ

### Plugin Loading Failed

**Problem**: No card effect after installation, Hermes still replies plain text

**Solution**:
1. Check if plugin is correctly installed: `hermes plugins list`
2. View gateway logs: `grep hermes_lark_streaming ~/.hermes/logs/agent.log`
3. Verify Feishu credentials: `$(dirname $(readlink -f $(which hermes)))/python -m hermes_lark_streaming status`
4. Check for backup directory interference: `ls -la ~/.hermes/plugins/ | grep bak`, delete if exists

### Card Element Limit Exceeded

**Problem**: Card update fails during long conversations

**Solution**: Enable linear mode (`streaming.linear: true`), plugin will auto-split cards

### Table Display Issues

**Problem**: When AI response contains many tables, some tables display as raw Markdown instead of formatted tables

**Cause**: Feishu cards have a limit on the number of table elements; tables exceeding the limit are downgraded to code blocks

**Solution**: v0.9.0 has increased the table downgrade threshold from 3 to 10, which covers most scenarios. If you still encounter this issue, adjust `_MAX_CARD_TABLES` in `cardkit_md.py`

---

## Changelog

> See [CHANGELOG.md](CHANGELOG.md) for full version history.

---

## Acknowledgments