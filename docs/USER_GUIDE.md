# aiduPOP 用户指南（中文）

> 本文原为 `README.zh-CN.md` 独有章节的抢救稿（2026-09-02）。
> 原文件是上游 `hermes-lark-streaming` 的文档孤儿：标题沿袭上游、安装链接指向
> 上游仓、含第三方 PII。中文主文档以 [README.md](../README.md) 为准，本文仅保留
> 「安装运维 / 命令 / 配置 / 凭据」四块主文档没有的实操内容。

## 更新 / 卸载 / 验证安装

### 更新

```bash
hermes plugins update hermes-lark-streaming
hermes gateway restart
```

### 卸载

```bash
# 1. 先清理注入的配置（插件代码还在时执行）
HERMES_PYTHON=$(python3 ~/.hermes/plugins/hermes-lark-streaming/__main__.py python)
$HERMES_PYTHON ~/.hermes/plugins/hermes-lark-streaming/__main__.py cleanup

# 2. 卸载插件
hermes plugins uninstall hermes-lark-streaming

# 3. 重启网关
hermes gateway restart
```

### 验证安装

```bash
hermes plugins list
HERMES_PYTHON=$(python3 ~/.hermes/plugins/hermes-lark-streaming/__main__.py python)
$HERMES_PYTHON ~/.hermes/plugins/hermes-lark-streaming/__main__.py status
$HERMES_PYTHON ~/.hermes/plugins/hermes-lark-streaming/__main__.py verify
$HERMES_PYTHON ~/.hermes/plugins/hermes-lark-streaming/__main__.py doctor
```

## /aowen 命令

在飞书中发送 `/aowen` 系列命令，插件直接回复卡片（不经过 Hermes AI）：

| 命令 | 说明 |
|------|------|
| `/aowen help` | 显示所有命令列表 |
| `/aowen status` | 查看插件状态 + 当前配置（折叠面板展示） |
| `/aowen monitor` | 查看监控面板（卡片创建数、API 调用数、错误码分布等） |
| `/aowen monitor reset` | 重置监控统计计数器 |
| `/aowen config reload` | 修改配置后立即生效，或重启网关生效 |
| `/aowen` | 同 `/aowen help` |

## 配置说明

### 时间感知模式（`inject_time`）

```yaml
hermes_lark_streaming:
  inject_time: false   # 开启后每条用户消息前加 <time>HH:MM:SS</time> 前缀
```

开启后 AI 无需调用 `date` 工具即可感知当前时间，Prefix Cache 安全（每条约 6 tokens）。详见 [SKILL.md](SKILL.md)。

### 推理面板显示

```yaml
display:
  show_reasoning: true  # 在统一面板中显示推理内容
```

### 统一面板超限压缩

飞书卡片2.0 **硬性限制200个元素**，超出会报错 `300305` 并触发文本兜底（内容重复）。
代码内置卡片级元素安全网：封卡前递归计算实际元素总数，超过 195（200-5 缓冲）
自动从面板最老项目开始裁剪；answer、footer、error panel 永不裁剪。

| 组成部分 | 元素数 |
|---------|--------|
| 面板容器 | 1 |
| 面板标题 | 2 |
| 每个推理轮次（最大） | 4 |
| 每个工具步骤（最大） | 7 |
| 折叠提示（触发时） | 1 |
| 回答文本 | 1~3 |
| 页脚 | 2 |

20 轮推理 + 20 步工具 ≈ 223 个元素，超出 200；故默认
`max_tool_steps=20` + `max_reasoning_rounds=20` 配合折叠机制保证不超限：

```yaml
hermes_lark_streaming:
  max_tool_steps: 20           # 默认20，范围1~100
  max_reasoning_rounds: 20     # 默认20，范围1~100
```

### 飞书凭据

插件复用 Hermes 已配置的飞书凭据，**无需单独配置**。Hermes 安装时已写入
`~/.hermes/.env`（`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_DOMAIN`）。
Hermes 飞书渠道正常工作，插件即正常工作。

飞书 App ID / Secret 在飞书开放平台创建应用后，于「凭证与基础信息」页面获取。

> **兼容性**：与上游 `hermes-lark-streaming` 插件不兼容 —— 如已安装原版，请先卸载再安装本插件。
> 卡片元素硬限制 200 个 Tag 对象，插件内置安全网自动裁剪超限内容（见上节）。

## 升级提醒

如从 v1.0.1 及以下版本升级，请先按卸载流程卸载老版本再重装，禁止直接更新升级。
