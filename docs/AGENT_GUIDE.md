# AGENT_GUIDE.md — hermes-lark-streaming

> Machine-readable reference for automated agents, CI scripts, and AI assistants.
> Last updated: 2026-06-16 | Version: 1.0.7

## Quick Facts

| Item | Value |
|------|-------|
| Name | hermes-lark-streaming |
| Version | 1.0.7 |
| License | MIT |
| Python | >=3.11 |
| Dependencies | lark-oapi>=1.4.0, PyYAML>=6.0 |
| Plugin kind | standalone |
| Repository | https://gitee.com/Aowen-Nowor/hermes-lark-streaming (DEV branch) |

## Install

```bash
# As Hermes directory plugin (recommended)
hermes plugins add /path/to/hermes-lark-streaming

# Via pip
pip install hermes-lark-streaming
```

## Uninstall

```bash
# Directory plugin
hermes plugins remove hermes-lark-streaming

# Pip
pip uninstall hermes-lark-streaming
```

## Update

```bash
cd /path/to/hermes-lark-streaming
git pull origin DEV
hermes plugins reload hermes-lark-streaming
```

## Required Credentials

| Env Var | Description |
|---------|-------------|
| `FEISHU_APP_ID` | Feishu App ID |
| `FEISHU_APP_SECRET` | Feishu App Secret |

## Configuration (config.yaml)

| Key | Default | Range | Description |
|-----|---------|-------|-------------|
| `streaming_mode` | `true` | bool | Enable/disable streaming card output |
| `show_reasoning` | `true` | bool | Show reasoning/thinking panels |
| `max_tool_steps` | `20` | 1–100 | Max tool steps in unified panel before collapse |
| `max_reasoning_rounds` | `20` | 1–100 | Max reasoning rounds in unified panel before collapse |
| `card_duration_sec` | `600` | >0 | Session TTL in seconds |

## Provided Hooks

- `on_feishu_normalize`
- `on_message_started`
- `on_message_completed`
- `on_message_aborted`
- `on_message_interrupted`
- `on_answer_delta`
- `on_thinking_delta`
- `on_reasoning_delta`
- `on_tool_updated`
- `on_background_review_message`
- `on_cron_deliver`

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Cards not appearing | Missing credentials | Set `FEISHU_APP_ID` + `FEISHU_APP_SECRET` |
| 300305 element limit | Too many tool steps | Reduce `max_tool_steps` / `max_reasoning_rounds` |
| 300315 schema error | Invalid card property | Check Feishu Card 2.0 schema docs |
| Content truncated | Static card table limit | Static cards (cron/gateway) auto-downgrade tables >5 |
| Streaming stuck | TTL expired | Increase `card_duration_sec` |

## Version Check

```bash
python -c "from hermes_lark_streaming import __version__; print(__version__)"
# Or
grep 'version:' plugin.yaml
```
