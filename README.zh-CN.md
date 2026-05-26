<h1 align="center">hermes-lark-streaming</h1>

<p align="center">
  <img src="https://img.shields.io/badge/项目-Vibe%20Coding-ff69b4" alt="Vibe Coding">
  <a href="https://larkcommunity.feishu.cn/wiki/DKkpwgMcJiglIhk88N4cqJEan5f?from=from_copylink"><img src="https://img.shields.io/badge/docs-知识库-3370FF?logo=feishu&logoColor=white" alt="知识库文档"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-4caf50.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.11+-3776AB.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/version-0.8.5-ff9800.svg" alt="Version">
</p>

<p align="center">
<a href="https://applink.feishu.cn/client/message/link/open?token=AmoQJk5dwczIahKlW78ADLU%3D"><img src="https://img.shields.io/badge/官方唯一交流群-中国-red" alt="官方交流群"></a>
</p>

<p align="center">
<a href="README.md">English</a> | 中文版
</p>

为 Hermes Agent 提供飞书/Lark CardKit v2.0 流式消息卡片插件 — 实时 AI 响应展示，支持打字机效果、工具面板、推理过程等。

> 本仓库由 [Cheerwhy/hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming) Fork 而来

---

## 功能特性

- **流式卡片** — AI 响应实时在交互式卡片中展示，带打字机效果
- **线性模式** — 在单张卡片中动态渲染思考、工具调用和答案内容，自动处理飞书 200 元素限制并支持拆卡
- **思考过程** — 展示模型推理/思考内容
- **工具调用** — 实时工具调用状态和进度，带标准图标和结果/错误块
- **CardKit v2.0** — 优先使用飞书 CardKit 流式 API，自动降级到 IM PATCH
- **终端卡片** — 完成后显示完整结果，包括 token 使用量、耗时、上下文信息
- **消息保护** — 检测消息被删除/撤回后自动终止更新，避免无效 API 调用
- **图片解析** — 自动识别 markdown 图片引用，下载并上传替换为飞书 img_key
- **中断处理** — 处理 /stop 命令和消息中断，显示中断状态卡片并自动开启新会话
- **Cron 推送** — 定时任务结果以飞书卡片形式推送，保留 Markdown 渲染
- **多语言** — 内置中英文双语卡片文本（状态、工具面板、思考标签等），根据飞书客户端语言自动切换
- **插件生命周期** — 通过 `hermes plugins install/uninstall` 安装/卸载，无需修改源文件
- **运行时补丁** — 使用 monkey patching 而非 AST 注入，不修改磁盘上的源文件

![功能预览](assets/img1.png)

---

## 快速开始

### 前置要求

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)（已运行，已配置飞书平台）
- Python >= 3.11
- Hermes CLI 支持插件系统（可用 `hermes plugins` 命令）

### 安装

```bash
hermes plugins install https://gitee.com/Aowen-Nowor/hermes-lark-streaming
```

提示时输入 `Y` 启用插件，然后重启网关：

```bash
hermes gateway restart
```

### 卸载

```bash
# 1. 先清理注入的配置（插件代码还在，可以跑）
python -m hermes_lark_streaming cleanup

# 2. 卸载插件
hermes plugins uninstall hermes-lark-streaming

# 3. 重启网关
hermes gateway restart
```

### 验证安装

```bash
# 检查插件状态
python -m hermes_lark_streaming status

# 验证环境兼容性
python -m hermes_lark_streaming verify

# 查看网关日志
grep hermes-lark-streaming ~/.hermes/logs/gateway.log
```

---

## 配置说明

所有配置项位于 `~/.hermes/config.yaml` 的 `streaming:` 节下。

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

  footer:
    fields:
      - status
      - elapsed
      - model
      - tokens
      - context
    show_label: true         # 是否显示字段标签（true/false）
```

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

## 架构设计

本插件使用**运行时 monkey patching**而非 AST 源码注入。当插件通过 `hermes plugins install` 加载时，会在运行时包装 `GatewayRunner`、`AIAgent` 和 `Scheduler` 的方法 — **不修改磁盘上的任何源文件**。

### 注入点

```
hermes-agent
  │
  ├─ gateway/run.py              ← hermes-lark-streaming 在导入时包装
  │   └─ GatewayRunner._handle_message          → NORMALIZE
  │   └─ GatewayRunner._handle_message_with_agent → START + ABORT + INTERRUPT
  │   └─ GatewayRunner._run_agent               → COMPLETE + context
  │
  ├─ agent/conversation_loop.py
  │   └─ run_conversation                       → 包装所有 6 个回调（模块级）
  │
  ├─ run_agent.py
  │   └─ AIAgent.run_conversation               → 包装所有 6 个回调（实例级双重保险）
  │       ├─ stream_delta_callback              → ANSWER
  │       ├─ interim_assistant_callback         → THINKING
  │       ├─ tool_progress_callback             → TOOL
  │       ├─ reasoning_callback                 → REASONING
  │       └─ background_review_callback         → BACKGROUND_REVIEW
  │
  ├─ cron/scheduler.py
  │   └─ Scheduler._deliver_result              → CRON（仅飞书）
  │
  └─ hermes-lark-streaming 插件
      ├─ plugin.yaml          — 清单（名称、版本、hooks）
      ├─ __init__.py          — 包根目录 + register 导出
      ├─ __main__.py          — CLI（status / verify）
      ├─ plugin.py            — register(ctx) 入口点（Hermes 插件发现）
      ├─ monkey_patch.py      — 运行时 monkey patching（方法包装器）
      ├─ patch.py             — 11 个 hook 函数（由包装器调用）
      ├─ config.py            — 配置读取器
      ├─ controller.py        — StreamCardController（会话管理）
      ├─ controller_mixin.py  — 重试/降级混入（非线性模式）
      ├─ controller_linear_mixin.py — 线性模式卡片编排
      ├─ feishu.py            — 飞书 Open API 客户端（lark-oapi SDK）
      ├─ cardkit.py           — CardKit v2.0 卡片构建器
      ├─ cardkit_i18n.py      — 中英文国际化
      ├─ cardkit_md.py        — Markdown 处理
      ├─ linear.py            — 线性模式状态追踪
      ├─ text.py              — 增量文本累加器
      ├─ tooluse.py           — 工具调用追踪和可视化
      ├─ image.py             — 异步图片上传
      ├─ flush.py             — 节流刷新调度器
      └─ unavailable_guard.py — 消息不可用保护
```

### 依赖项

| 包名 | 版本 | 用途 |
|------|------|------|
| `lark-oapi` | >= 1.4.0 | 飞书 Open API SDK |
| `PyYAML` | >= 6.0 | YAML 配置解析 |

---

## 线性模式

线性模式是默认模式，在单张动态更新的卡片中渲染所有内容（思考、工具调用、答案）。

### 工作原理

1. **分段渲染**：内容按段组织（reasoning、answer、tool）
2. **自动拆卡**：当接近飞书 200 元素限制时，卡片自动拆分
3. **平滑过渡**：旧卡片被封存，新卡片继续对话

### 优势

- 更好的用户体验，连续滚动
- 在单一视图中保留上下文
- 自动处理飞书元素限制

### 拆卡行为

插件监控元素数量，在以下情况自动拆卡：
- 元素数量超过 180（为 footer 和波动预留 20）
- 工具段可在步骤边界拆分
- 答案段在溢出时触发拆卡

---

## 技术细节

### 运行时 Monkey Patching

插件通过包装 Hermes 核心类的方法来实现功能注入，而非修改源文件：

- **线程安全**：使用 `contextvars.ContextVar` 传播消息上下文，并支持 `threading.local()` 作为 fallback（用于线程池场景）
- **幂等性**：通过函数属性标记（`_hls_wrapper`）防止重复包装
- **双重保险**：同时在模块级别和实例级别应用 patch，确保回调被正确拦截
- **延迟加载**：5 秒延迟后重试直接 patch，确保 Hermes 模块完全加载

### 消息保护（UnavailableGuard）

检测消息被删除/撤回后终止更新管道：

- 监控飞书 API 错误码（231003、1000023、230011）
- 缓存不可用消息状态（30 分钟 TTL）
- 避免对已删除消息的无效 API 调用

### 节流刷新（FlushController）

控制卡片更新频率，避免 API 限流：

- CardKit 模式：50ms 节流
- IM PATCH 模式：150ms 节流
- 等待机制：确保所有更新在完成前刷新

### 文本状态管理（TextState）

增量文本累加和边界检测：

- 追踪已刷新和待刷新文本
- 推理标签分离（`<thinking>`/`</thinking>`）
- 避免重复渲染相同内容

---

## 常见问题

### 插件加载失败

**问题**：安装后无卡片效果，Hermes 仍回复纯文本

**解决**：
1. 检查插件是否正确安装：`hermes plugins list`
2. 查看网关日志：`grep hermes-lark-streaming ~/.hermes/logs/gateway.log`
3. 验证飞书凭据：`python -m hermes_lark_streaming status`
4. 检查是否存在备份目录干扰：`ls -la ~/.hermes/plugins/ | grep bak`，如有则删除

### 卡片内容重复

**问题**：回复文本在卡片中出现两次

**原因**：Hermes 在同一会话中多次调用 `run_conversation`，导致回调被重复包装

**解决**：插件已内置防重复包装守卫（v0.8.0-fix2），通过 `_hls_wrapper` 属性标记已包装的回调

### 卡片元素超限

**问题**：长对话导致卡片更新失败

**解决**：启用线性模式（`streaming.linear: true`），插件会自动拆卡处理

### 第二条及后续消息无流式更新

**问题**：第一条消息正常，后续消息只显示跑马灯和 Done

**原因**：`contextvars.ContextVar` 不跨线程传播，线程池中无法读取消息上下文

**解决**：插件已通过 `threading.local()` 作为 fallback（v0.8.0-fix4/fix5），确保跨线程场景下上下文正确传递

---

## 更新日志

### v0.8.5 (2026-05-26)

- 修复 `_set_thread_local_ctx()` 未定义问题
- 添加双重保险：直接 patch `AIAgent.run_conversation` 方法（5秒延迟执行）
- 修复线程池场景下上下文传递问题

### v0.8.0-fix4 (2026-05-26)

- 恢复 `apply_patches()` 中对 `conversation_loop.run_conversation` 的 patch
- 引入 `threading.local()` 作为 `contextvars` 的 fallback 存储
- 在 `_wrap_run_agent` 中将 msg context 复制到 thread-local

### v0.8.0-fix3 (2026-05-25)

- 修复 `monkey_patch.py` 中 `setattr` 缩进错误导致的语法异常

### v0.8.0-fix2 (2026-05-25)

- 修复回调重复包装导致内容重复的问题
- 添加防重复包装守卫

### v0.8.0-fix1 (2026-05-25)

- 修复插件根目录缺少 `__init__.py` 导致加载失败的问题

### v0.8.0

- 重构为运行时 monkey patching 架构
- 新增线性模式支持
- 新增 UnavailableGuard 消息保护
- 新增 FlushController 节流刷新
- 新增 TextState 回复边界检测

---

## 致谢