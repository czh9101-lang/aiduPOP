# 🧠 hermes-lark-streaming — LLM 快速上手指南

> **Purpose**: 本文档是为任何 LLM 模型准备的"项目技能卡片"。阅读本文档后，你应能立即理解项目架构、关键设计决策、常见陷阱，并高效地进行代码修改或功能扩展。

---

## 1. 项目概述

**hermes-lark-streaming** 是 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的飞书/Lark CardKit v2.0 流式卡片插件。它在 AI 对话过程中实时更新飞书交互卡片（打字效果、工具调用面板、推理过程、完成态统计等），而非使用默认的纯文本回复。

| 属性 | 值 |
|------|-----|
| 版本 | 0.10.2 (DEV 分支) |
| 仓库 | `https://gitee.com/Aowen-Nowor/hermes-lark-streaming` |
| 协议 | MIT |
| Python | ≥3.11 |
| 基于 | Cheerwhy/hermes-lark-streaming v0.7.0，大规模重构 |
| 与上游关系 | ⚠️ **不兼容**，需先卸载原版 |

---

## 2. 架构全景

```
用户消息 → Hermes Gateway (gateway.run)
               │
               ▼
        GatewayRunner._handle_message ──── [Hook 0: on_feishu_normalize]
               │
               ▼
        _handle_message_with_agent ──────── [Hook 1: on_message_started]
               │                              [Hook 8: on_message_aborted]
               │                              [Hook 9: on_message_interrupted]
               ▼
        _run_agent ──────────────────────── [Hook 2: on_message_completed]
               │
               ▼
        AIAgent.run_conversation ────────── [inject_time 前缀注入]
               │
               ├─ stream_delta_callback ──── [Hook 4: on_answer_delta]
               ├─ reasoning_callback ──────── [Hook 6: on_reasoning_delta]
               ├─ tool_progress_callback ─── [Hook 3: on_tool_updated]
               └─ background_review_callback [Hook 7: on_background_review_message]
               
Cron 定时推送:
  Scheduler._deliver_result ──────────── [Hook 10: on_cron_deliver] (async)
```

### 核心调用链

```
monkey_patch.py (运行时拦截)
    → patch.py (Hook 函数，检查 enabled + 调用 controller)
        → controller.py (主控制器，单例，管理 CardSession 生命周期)
            → controller_mixin.py (异步卡片 API 编排：创建/更新/降级/重试)
            → controller_linear_mixin.py (线性单卡模式：segment 管理/拆卡/完成)
                → cardkit.py (CardKit v2.0 JSON 构建)
                → feishu.py (飞书 Open API 客户端)
                → flush.py (节流调度器)
```

---

## 3. 文件地图与职责

| 文件 | 行数 | 职责 | 关键点 |
|------|------|------|--------|
| `monkey_patch.py` | 980 | 运行时方法替换 | `_resolve_hermes_agent_module()` 3层解析；4组补丁各有 try/except；Cron 补丁全链路 async；时间注入 XML 标签 `<time>` |
| `patch.py` | 229 | Hook 函数层 | `_safe_hook` 统一 enabled 检查 + 异常捕获；`on_cron_deliver` 是 async |
| `controller.py` | 636 | 主控制器(单例) | `CardSession` 状态机；`on_cron_deliver_async` 直接 await；`error_message` 属性 |
| `controller_mixin.py` | 386 | 异步 API 编排 | 状态: IDLE→CREATING→STREAMING→COMPLETED/FAILED/ABORTED；CardKit→IM PATCH 降级链 |
| `controller_linear_mixin.py` | 730 | 线性模式编排 | 拆卡阈值 180 元素；segment 按事件顺序扁平排列 |
| `cardkit.py` | 701 | 卡片 JSON 构建 | `_downgrade_tables()`；`_build_error_panel()`；`build_cron_card()`；i18n locales |
| `cardkit_i18n.py` | 44 | 中英双语映射 | `_T` dict，`_i18n()` / `_t()` 快捷函数 |
| `cardkit_md.py` | 121 | Markdown 处理 | 标题降级、表格降级(≤10)、图片 key 剥离、长文本分块(2400 chars) |
| `config.py` | 161 | 配置读取 | 惰性加载 + 运行时 `_reload()`；`inject_time` 每次磁盘重读 |
| `feishu.py` | 291 | 飞书 API 客户端 | CardKit v1/v2 + IM API；错误码分类；token 脱敏 |
| `flush.py` | 156 | 节流调度器 | CardKit 100ms / IM PATCH 1.5s；互斥锁 + re-flush |
| `linear.py` | 158 | 线性 segment 状态 | `Segment` 数据类；`LinearState` 扁平管理 |
| `text.py` | 111 | 文本增量追踪 | `<think|thinking|thought>` 标签拆分；`TextState` 累积器 |
| `tooluse.py` | 299 | 工具调用追踪 | `ToolStep` / `ToolSession`；敏感信息脱敏 |
| `image.py` | 129 | 异步图片处理 | 下载远程图→上传飞书→替换 img_key；同步 strip + 异步上传 |
| `unavailable_guard.py` | 144 | 消息不可用保护 | 删除/撤回检测；30分钟 TTL 缓存 |
| `plugin.py` | 193 | 插件注册入口 | `register()` 注入配置 + 打补丁；`unregister()` 清理配置；自动备份 config.yaml |
| `__init__.py`(子包) | 23 | 版本号导出 | 从 `plugin.yaml` 动态读取，失败 → warning + "unknown" |
| `__init__.py`(根) | 39 | 桥接模块 | `spec_from_file_location` 桥接到子包，解决 Hermes 加载方式兼容 |
| `setup.py` | 19 | 构建时版本 | 从 `plugin.yaml` 读版本，失败 raise |
| `pyproject.toml` | 30 | 构建配置 | `dynamic = ["version"]`；Python ≥3.11 |

---

## 4. 关键设计决策 (Key Design Decisions)

### 4.1 版本号：plugin.yaml 为唯一真值源

```
plugin.yaml (唯一版本号: "0.10.2")
    ├── __init__.py  运行时读取 → 失败: warning + "unknown"
    └── setup.py     构建时读取 → 失败: FileNotFoundError / ValueError
pyproject.toml: dynamic = ["version"] (不存版本号)
```

**规则**: 修改版本号只改 `plugin.yaml`，其他地方都是从它读取。

### 4.2 Monkey Patch 而非 AST 注入

原版 v0.7.0 修改 `gateway/run.py` 源文件（AST 注入），本版改用运行时方法替换：
- 不修改 Hermes 任何源文件
- 卸载即恢复，无需回滚
- 代价：无法访问局部变量（如 `_response_time`），需自计时

### 4.3 `_resolve_hermes_agent_module()` — 3 层解析策略

Apple Silicon 上 PyPI 包 `agent` 遮蔽 Hermes 自身 `agent` 包，导致 `No module named 'agent.conversation_loop'`：

1. **sys.modules 缓存** — Hermes 已导入则直接取，零风险
2. **锚点发现** — 用 `gateway.run` / `run_agent` 的 `__file__` 定位 repo root，`spec_from_file_location` 加载
3. **标准 import** — 最后回退

### 4.4 Cron 推送：全链路异步化

```
旧(死锁): _wrap_cron_deliver(async) → sync on_cron_deliver → run_coroutine_threadsafe().result(30) → 阻塞事件循环 → 30s 超时
新(修复): _wrap_cron_deliver(async) → async on_cron_deliver → on_cron_deliver_async → await _do_cron_deliver() → 无阻塞
```

**教训**: 在事件循环线程中，绝不能用 `run_coroutine_threadsafe().result()` 同步等待协程完成。

### 4.5 自计时替代不可访问的 `_response_time`

`_response_time` 是 `_handle_message_with_agent` 的局部变量，monkey patch 无法访问。解决方案：

```python
# 消息开始时记录
ctx["_msg_start_time"] = time.monotonic()
# 完成时计算
_elapsed = time.monotonic() - ctx["_msg_start_time"]
```

### 4.6 消息中断检测 (`_started_msg_ids`)

Hermes 的 `_handle_message_with_agent` 返回 None 有两种含义：
1. **正常完成**：卡片已发送，Hermes 返回 None 抑制文本回复
2. **中断**：新消息打断旧消息

通过 `_started_msg_ids` 集合追踪：如果返回 None 时集合中还有其他 msg_id，说明是中断而非正常完成。

### 4.7 根 `__init__.py` 桥接

Hermes 用 `spec_from_file_location` 加载插件，会加载仓库根目录的 `__init__.py`。该文件：
1. 将 repo root 加入 `sys.path`
2. 临时从 `sys.modules` 移除桥接模块自身
3. `importlib.import_module("hermes_lark_streaming")` 加载真正的子包
4. 导出 `register` 和 `__version__`

### 4.8 双重补丁 + 重入守卫

`run_conversation` 同时被模块级和 AIAgent 实例级补丁：
- 模块级：拦截所有调用者（v0.10+）
- 实例级：兜底（所有版本）

`_inject_time_prefix` 使用 `threading.local()` 重入守卫防止双重注入。

### 4.9 时间注入格式：XML 标签

时间注入使用 XML 标签格式 `<time>HH:MM:SS</time>`，而非方括号格式 `[HH:MM:SS CST]`：

- **LLM 不模仿**：XML 标签被 LLM 理解为结构化元数据，不会在回复中生成 `<time>` 标签
- **语义清晰**：方括号格式可能被部分模型忽略为噪声，或在回复中学样
- **精简**：不含日期（系统提示词已有）和时区后缀（系统提示词已确定），减少 token 开销

**格式对比**：
- 旧：`[14:30:05 CST] 你好` — 可能被忽略或模仿
- 新：`<time>14:30:05</time> 你好` — 语义清晰、不被模仿

---

## 5. CardSession 状态机

```
IDLE → CREATING → STREAMING → COMPLETED
                    │              │
                    ├→ FAILED      └→ (card_sent=True → suppress Hermes reply)
                    └→ ABORTED
```

- **IDLE**: 初始状态
- **CREATING**: 正在创建卡片（CardKit API / IM API）
- **STREAMING**: 正在流式更新
- **COMPLETED**: 终态，卡片已发送
- **FAILED**: API 错误，降级为 Hermes 默认回复
- **ABORTED**: 消息被中断/删除

终态集合: `{COMPLETED, FAILED, ABORTED}`

---

## 6. 卡片 API 降级链

```
CardKit v2 Streaming (最优)
    ↓ 失败/不可用
CardKit v2 Create + Patch (非流式)
    ↓ 失败
IM Create + Patch (兜底)
    ↓ 失败
Hermes 默认纯文本回复 (最终降级)
```

`FeishuClient` 内部有 `use_cardkit` 标记，首次成功后锁定通道。

---

## 7. 线性模式 (Linear Mode)

线性模式是 v0.10.0 的默认模式，在单张卡片中按事件到达顺序动态渲染内容：

```
[Reasoning Panel] → [Tool Panel] → [Answer Text] → [Tool Panel] → ...
```

- 每个内容段是一个 `Segment`（type: reasoning/answer/tool）
- 扁平排列，无需推断轮次边界
- 当元素数接近 200 上限时自动拆卡（阈值 180）
- 拆卡后首卡片标记 `partial` 状态

---

## 8. 配置结构

`config.yaml` 中的 `streaming` 段：

```yaml
streaming:
  enabled: true
  linear: true
  panel_expanded: false
  card_ttl_sec: 600
  inject_time: false
  footer:
    fields:
      - [status, elapsed, model, api_calls]
      - [tokens, context, history_offset, compression_exhausted]
    show_label: true
```

首次安装时 `plugin.py:register()` 自动注入此段（并备份 config.yaml）。

---

## 9. Hook 索引 (11 个注入点)

| # | Hook | 位置 | 签名 | 说明 |
|---|------|------|------|------|
| 0 | `on_feishu_normalize` | `_handle_message` 入口 | sync | 修正飞书引用消息的虚假 thread_id |
| 1 | `on_message_started` | `_handle_message_with_agent` 入口 | sync | 创建 CardSession |
| 2 | `on_message_completed` | `_run_agent` 返回后 | sync → bool | 完成态卡片，返回是否已发卡片 |
| 3 | `on_tool_updated` | `tool_progress_callback` | sync | 工具调用状态更新 |
| 4 | `on_answer_delta` | `stream_delta_callback` | sync | AI 回复增量文本 |
| 5 | `on_thinking_delta` | (未使用) | sync | 思考内容，目前被跳过防重复 |
| 6 | `on_reasoning_delta` | `reasoning_callback` | sync | 原生模型推理增量 |
| 7 | `on_background_review_message` | `background_review_callback` | sync | 暂存后台审查通知 |
| 8 | `on_message_aborted` | 返回 None (无卡片) | sync | 消息异常终止 |
| 9 | `on_message_interrupted` | 返回 None (有卡片+新消息) | sync | 新消息打断旧消息 |
| 10 | `on_cron_deliver` | `Scheduler._deliver_result` | **async** | Cron 推送卡片 |

---

## 10. 常见陷阱与经验教训

### 10.1 事件循环死锁
**❌ 错误**: 在 async 函数中调用 `run_coroutine_threadsafe(coro, loop).result(timeout=30)`
**✅ 正确**: 直接 `await coro`

### 10.2 内容重复显示
**原因**: `interim_assistant_callback` 和 `stream_delta_callback` 处理同一段文本
**解决**: 不包裹 `interim_assistant_callback`，思考内容由 `reasoning_callback` 处理

### 10.3 版本号硬编码 fallback
**❌ 错误**: `__version__ = "0.10.0"` 作为 fallback
**✅ 正确**: 读取失败时 warning + "unknown"；构建时失败直接 raise

### 10.4 contextvars 不跨线程
**原因**: Python `contextvars.ContextVar` 不自动传播到 worker threads
**解决**: `_thread_local_ctx` 手动传递；`_run_agent` 中设置 thread-local

### 10.5 卡片已发送 vs 消息中断
**关键**: `_handle_message_with_agent` 返回 None 有两种含义，必须区分：
- `card_sent=True` → 正常完成，抑制 Hermes 纯文本回复
- `card_sent=False` → 真正的 abort/error

### 10.6 FlushController 线程安全（v0.10.1 修复）
**❌ 错误**: 从 worker 线程调用 `loop.call_soon()` 或 `loop.call_later()`
**✅ 正确**: 使用 `loop.call_soon_threadsafe()` 确保唤醒事件循环
**原因**: `call_soon` 只把回调加入 `_ready` 队列，但不调 `_write_to_self()` 唤醒事件循环。LLM 流式回调在 worker 线程中执行 → `schedule_update` → `call_soon` → 回调入队但事件循环不醒 → flush 永远不执行 → "跑马灯无文字"

### 10.7 Feishu CardKit 元素限制
飞书硬限制 200 元素/卡片。线性模式阈值设为 180（预留 20 给 footer + 波动）。

---

## 11. 测试结构

```
tests/
  test_version.py    — 版本号读取逻辑（plugin.yaml 缺失/无版本字段 fallback）
  test_patch.py      — Hook 函数单元测试
  test_controller.py — 会话生命周期 + 线性模式 dispatch + 集成测试
  test_cardkit.py    — 卡片 JSON 构建
  test_config.py     — 配置读取（含 inject_time 开关）
  test_flush.py      — 节流调度器（含线程安全 call_soon_threadsafe 测试）
  test_text.py       — 文本增量追踪
  test_image.py      — 图片解析
  test_linear.py     — 线性 segment 管理
  test_tooluse.py    — 工具调用追踪
  test_monkey_patch.py — 时间注入格式（XML 标签）、重入守卫、prefix cache 一致性
  test_unavailable_guard.py — 消息不可用保护
```

运行: `HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/`

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
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pytest tests/

# 清理 + 重装
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m hermes_lark_streaming cleanup
hermes plugins uninstall hermes-lark-streaming
hermes plugins install https://gitee.com/Aowen-Nowor/hermes-lark-streaming
hermes gateway restart
```

---

## 13. 版本历史要点

| 版本 | 日期 | 核心变更 |
|------|------|----------|
| v0.8.5 | 2026-05-26 | 初始 fork，修复桥接导入、回调重复、contextvars 跨线程 |
| v0.8.6 | 2026-05-26 | Config 读取修复、配置序列化修复、卸载清理 |
| v0.9.0 | 2026-05-27 | 内容重复修复、页脚耗时修复、CLI 路径修复、表格限制放宽、api_calls/history_offset |
| v0.10.0 | 2026-05-28 | 时间注入、/stop 状态显示、错误面板、compression_exhausted、Apple Silicon 修复、补丁隔离、Cron 死锁修复、Cron 表格降级 |
| v0.10.1 | 2026-05-28 | FlushController 线程安全修复（跑马灯无文字根因）、线性模式首次文字预填充、on_thinking reasoning_dirty 预防性修复 |
| v0.10.2 | 2026-05-28 | 时间注入格式优化为 XML 标签 `<time>` （避免 LLM 忽略或模仿）、线性模式冗余 stream_element 调用优化 |

---

## 14. 待做事项 (Roadmap)

- [ ] 拆卡首卡片 `partial` 状态显示
- [ ] `background_review` 进度消息放入卡片
- [ ] DEV → master 兼容性回归测试
- [ ] 考虑更多 Hermes 版本的兼容性探测
- [ ] `inject_time` 时区配置化（当前硬编码 CST/UTC+8）
- [ ] `_handle_linear_flush_error` 对 `CARDKIT_ELEMENT_LIMIT` 增加断路器（避免无限重试）

---

## 15. 快速定位问题

| 症状 | 检查 | 文件 |
|------|------|------|
| 卡片不出现 | `grep "GatewayRunner" agent.log` 看补丁是否成功 | monkey_patch.py |
| 内容重复 | `interim_assistant_callback` 是否被包裹 | monkey_patch.py `_maybe_wrap_callbacks` |
| Cron 推送纯文本 | `grep "cron" agent.log` 看是否有死锁 | monkey_patch.py `_wrap_cron_deliver` |
| Apple Silicon 报错 | `grep "conversation_loop" agent.log` | monkey_patch.py `_resolve_hermes_agent_module` |
| 版本号显示 unknown | plugin.yaml 是否存在于正确路径 | `__init__.py` |
| 页脚耗时为 0 | `_msg_start_time` 是否正确设置 | monkey_patch.py `_wrap_handle_message_with_agent` |
| 消息删除后仍在更新 | UnavailableGuard 是否工作 | unavailable_guard.py |
| 卡片元素超限 | `_MAX_CARD_TABLES` 和 `_ELEMENT_THRESHOLD` | cardkit_md.py, controller_linear_mixin.py |

---

*Last updated: 2026-05-28 | Version: 0.10.2 DEV*
