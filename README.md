<h1 align="center">hermes-lark-streaming</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Project-Vibe%20Coding-ff69b4" alt="Vibe Coding">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-4caf50.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.11+-3776AB.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/version-1.0.6-ff9800.svg" alt="Version">
</p>

<p align="center">
<a href="mailto:zhengyu.pu@petalmail.com"><img src="https://img.shields.io/badge/Email-zhengyu.pu%40petalmail.com-9C27B0?logo=gmail&logoColor=white" alt="Email"></a>
<a href="https://applink.feishu.cn/client/message/link/open?token=AmoQJk5dwczIahKlW78ADLU%3D"><img src="https://img.shields.io/badge/The_Only_Official_Group-China-red" alt="The Only Official Group"></a>
<a href="https://larkcommunity.feishu.cn/wiki/DKkpwgMcJiglIhk88N4cqJEan5f?from=from_copylink"><img src="https://img.shields.io/badge/docs-Knowledge_Base-3370FF?logo=feishu&logoColor=white" alt="Knowledge Base"></a>
</p>

<p align="center">
English | <a href="README.zh-CN.md">中文版</a>
</p>

Feishu/Lark CardKit v2.0 streaming cards plugin for Hermes Agent — real-time AI response display with typing effect, unified collapsible panel, chronological reasoning/tool display, and more.

> Based on [Cheerwhy/hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming) v0.7.0, with extensive refactoring and optimizations
>
> ⚠️ **Incompatible with the upstream plugin** — if you have the original `Cheerwhy/hermes-lark-streaming` installed, please uninstall it first before installing this version.

---

## Effect Preview

<table align="center">
  <tr>
    <td><img src="assets/img1.png" width="200px" /></td>
    <td><img src="assets/img2.png" width="200px" /></td>
    <td><img src="assets/img3.png" width="200px" /></td>
    <td><img src="assets/img4.png" width="200px" /></td>
  </tr>
</table>

---

## Quick Start

### Prerequisites

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) (running, with Feishu platform configured)
- Hermes CLI with plugin system support (`hermes plugins` command available)

### Installation
> The plugin automatically reads the `HERMES_HOME` environment variable to locate the installation path (`~/.hermes` by default). No extra steps are needed for non-default paths.

**Gitee**
> Choose either SSH or HTTPS:
```bash
# Gitee (SSH)
hermes plugins install git@gitee.com:Aowen-Nowor/hermes-lark-streaming.git
# Gitee (HTTPS)
hermes plugins install https://gitee.com/Aowen-Nowor/hermes-lark-streaming
```
**GitHub**
> Choose either SSH or HTTPS:
```bash
# GitHub (SSH)
hermes plugins install git@github.com:Aowen-Nowor/hermes-lark-streaming.git
# GitHub (HTTPS)
hermes plugins install https://github.com/Aowen-Nowor/hermes-lark-streaming
```

Enter `Y` when prompted to enable the plugin, then restart the gateway:

```bash
hermes gateway restart
```

### Update

```bash
hermes plugins update hermes-lark-streaming
hermes gateway restart
```

### Uninstallation

```bash
# 1. Clean up injected config (while plugin code is still available)
# Auto-detect Hermes Python path:
HERMES_PYTHON=$(python3 ~/.hermes/plugins/hermes-lark-streaming/__main__.py python)
$HERMES_PYTHON ~/.hermes/plugins/hermes-lark-streaming/__main__.py cleanup

# 2. Remove plugin
hermes plugins uninstall hermes-lark-streaming

# 3. Restart gateway
hermes gateway restart
```

### Verify Installation

```bash
hermes plugins list
grep hermes_lark_streaming ~/.hermes/logs/agent.log
# Auto-detect Hermes Python path:
HERMES_PYTHON=$(python3 ~/.hermes/plugins/hermes-lark-streaming/__main__.py python)
$HERMES_PYTHON ~/.hermes/plugins/hermes-lark-streaming/__main__.py status
$HERMES_PYTHON ~/.hermes/plugins/hermes-lark-streaming/__main__.py verify
```

> **Note**: If auto-detection fails, manually set `HERMES_PYTHON` to your Hermes venv Python:
> - **Hermes Desktop**: `~/.hermes/hermes-agent/venv/bin/python3`
> - **CLI/Server install**: `/usr/local/lib/hermes-agent/venv/bin/python3`
> - **Alternative**: `/opt/hermes-agent/venv/bin/python3`

> **Troubleshooting**: If no card effect appears, check: (1) `hermes plugins list` shows enabled; (2) no `*.bak` directories under `~/.hermes/plugins/`; (3) Feishu credentials are configured.

---

## Configuration

All settings go under the `hermes_lark_streaming:` section in `~/.hermes/config.yaml`. The plugin auto-injects defaults on first load; run `cleanup` before uninstalling to remove them.

```yaml
hermes_lark_streaming:
  enabled: true                    # Enable streaming cards
  linear: true                     # Single-card in-place update (unified panel architecture)
  panel_expanded: false            # Keep panels expanded in completed cards
  streaming_panel_expanded: false  # Keep panels expanded during streaming
  print_strategy: delay            # "fast" (instant) or "delay" (smoother typewriter, default)
  flush_interval_ms: 100           # Card refresh interval in ms (70–2000, default 100)
  card_ttl_sec: 600               # Card alive detection timeout (seconds)
  inject_time: false               # Time awareness mode (see below)

  footer:
    show_label: false              # Show field labels
    fields:
      - [status, elapsed, model, cost, compression_exhausted]
      # Available fields:
      #   status      — Reply status (Completed / Error / Stopped)
      #   elapsed     — AI response elapsed time
      #   model       — Model name used
      #   cost        — Estimated cost with trust indicator ($0.023 est. / $0.023 actual / Free)
      #   compression_exhausted — Context window is full (⚠ Context Full)
      # Fields below are not shown by default — add them to the fields list to enable:
      #   cache       — Cache hit rate (cache_read/total_input hit%)
      #   tokens      — Token usage (↑ input ↓ output 💭 reasoning)
      #   context     — Context window usage (used/total percentage)
      #   api_calls   — Number of API calls in this session
      #   history_offset — Conversation history offset; larger = longer history, sudden decrease = context compression
      # Each inner list is one row in the footer; fields only shown when they have values
```

### Time Awareness Mode (`inject_time`)

When `inject_time: true`, the plugin prepends `<time>HH:MM:SS</time>` to each user message so the AI can perceive the current time without calling `date`. XML tags are used because LLMs understand them as metadata and won't mimic them in output. Prefix-cache safe (~6 tokens/message). See [SKILL.md](docs/SKILL.md) for full details.

### Reasoning Panel Display

```yaml
display:
  show_reasoning: true  # Show reasoning content in the unified panel
```

### 统一面板超限压缩

飞书卡片2.0 **硬性限制200个元素/组件**，超出会报错 `300305 (element exceeds the limit)`，导致卡片封口失败并触发文本兜底（内容重复）。

> **元素计数规则**：每个带 `tag` 属性的 JSON 对象都算1个元素，包括嵌套在内层的 `standard_icon`、`plain_text`、`lark_md` 等。

#### 统一面板各项元素消耗

| 组成部分 | 元素数 | 说明 |
|---------|--------|------|
| 面板容器 | 1 | `collapsible_panel` |
| 面板标题 | 2 | `plain_text` + `standard_icon` |
| 每个推理轮次（最大） | 4 | 标题行 `div`+`standard_icon`+`lark_md` + 推理文本 `markdown` |
| 每个工具步骤（最大） | 7 | 标题行 `div`+`standard_icon`+`lark_md` + 详情行 `div`+`plain_text` + 结果行 `div`+`lark_md` |
| 折叠提示（触发时） | 1 | 1个 `markdown` 元素 |
| 回答文本 | 1~3 | `markdown`，长文本会被拆分 |
| 页脚 | 2 | `hr` + `markdown` |
| 卡片头（启用时） | ~3 | `plain_text` + `standard_icon` |
| 错误面板（有时） | ~4 | `collapsible_panel` + 内部元素 |

**计算示例**：20 轮推理 + 20 步工具 = 20×4 + 20×7 + 固定开销 ≈ 223（超过 200）

因此默认值设为 `max_tool_steps=20` + `max_reasoning_rounds=20`，配合折叠机制确保大多数场景不超限。即使配置值较高或极端情况下元素仍超限，代码内置了**卡片级元素安全网**——封卡时已知全部元素（面板+answer+footer+error），递归计算实际 tag objects 总数，超过195（200-5缓冲）时自动从面板children最老项目开始裁剪，确保卡片元素永远不会超过200。answer、footer、error panel 永不裁剪。

#### 配置项

```yaml
hermes_lark_streaming:
  max_tool_steps: 20           # 统一面板最多显示的工具步骤数（默认20，范围1~100）
  max_reasoning_rounds: 20     # 统一面板最多显示的推理轮次数（默认20，范围1~100）
```

超出限制时，早期项目会被折叠为一行提示，例如：`⚡ 还有 10 轮早期推理、5 步早期操作已折叠`

面板标题始终显示**实际总数**（如"3轮 · 44个工具"），折叠提示仅影响面板内展示的内容。

### Feishu Credentials

| Priority | Source | Example |
|----------|--------|---------|
| 1 | Environment Variables | `FEISHU_APP_ID`, `FEISHU_APP_SECRET` |
| 2 | File | `~/.hermes/.env` |
| 3 | Config File | `hermes_lark_streaming.feishu.app_id` |

```bash
# ~/.hermes/.env example
FEISHU_APP_ID=cli_xxxxxx
FEISHU_APP_SECRET=xxxxxx
FEISHU_BASE_URL=https://open.feishu.cn/open-apis
```

---

## Developer Guide & Changelog

> 📖 **[SKILL.md](docs/SKILL.md)** — LLM quick-start guide. Architecture, key design decisions, common pitfalls, efficient code modification guide.

> For the full version history, see [CHANGELOG.md](docs/CHANGELOG.md)

> ⚠️ **Important Notice:** If upgrading from v1.0.1 or below, please follow the uninstallation process to remove the old version and freshly install the new one. Do NOT upgrade via the update command!

---

## How to Submit Issues
> Please refer to the template [ISSUES_TEMPLATE.md](docs/ISSUES_TEMPLATE.md)

## Acknowledgments

[![joshcheng820222](https://avatars.githubusercontent.com/u/26886147?v=4&s=66)](https://github.com/joshcheng820222) [![xuu1998](https://avatars.githubusercontent.com/u/40609659?v=4&s=66)](https://github.com/xuu1998)
