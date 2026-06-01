# 🧠 hermes-lark-streaming — LLM 快速上手指南

> **Purpose**: 本文档是为任何 LLM 模型准备的"项目技能卡片"。阅读本文档后，你应能立即理解项目架构、关键设计决策、常见陷阱，并高效地进行代码修改或功能扩展。

---

## 1. 项目概述

**hermes-lark-streaming** 是 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的飞书/Lark CardKit v2.0 流式卡片插件。它在 AI 对话过程中实时更新飞书交互卡片（打字效果、工具调用面板、推理过程、完成态统计等），而非使用默认的纯文本回复。

| 属性 | 值 |
|------|-----|
| 版本 | 0.15.3 (DEV 分支) |
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
  cron.scheduler._deliver_result ──────── [Hook 10: on_cron_deliver] (async)
               │
               ▼
Background 后台任务:
  _run_background_task ───────────────── [Hook 1: on_message_started]
                                         [Hook 2: on_message_completed]
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
| `monkey_patch.py` | 1380 | 运行时方法替换 | `_resolve_hermes_agent_module()` 3层解析；4组补丁各有 try/except；Cron 补丁全链路 async；时间注入 XML 标签 `<time>`；`_started_msg_ids` 线程安全；`_wrap_cron_deliver` 临时替换 adapter.send（直接 await，不用 `run_coroutine_threadsafe`）；`_wrap_run_background_task` 后台任务卡片；`_thinking_wrapper` 检查 consumed 返回值防重复；关键日志含 `__version__`；`finish_reason` 诊断日志；`_wrap_feishu_adapter_send_image_file`/`_wrap_feishu_adapter_send_image` 图片拦截→卡片会话；递归中断子级 COMPLETE hook 修复 |
| `patch.py` | 229 | Hook 函数层 | `_safe_hook` 统一 enabled 检查 + 异常捕获；`on_cron_deliver` 是 async；`on_message_completed` 传递 cache tokens |
| `controller.py` | 681 | 主控制器(单例) | `CardSession` 状态机（含 `COMPLETING` 状态）；`on_cron_deliver_async` 直接 await；`error_message` 属性；`element_limit_hit` 标志；`_was_aborted` 中断标记；footer 新增 `cache_read_tokens`/`cache_write_tokens` |
| `controller_mixin.py` | 386 | 异步 API 编排 | 状态: IDLE→CREATING→STREAMING→COMPLETING→COMPLETED/FAILED/ABORTED；CardKit→IM PATCH 降级链；300317 幂等处理 |
| `controller_linear_mixin.py` | 800 | 线性模式编排 | 拆卡阈值 180 元素；超限自动拆卡；`element_limit_hit` 标志；segment 按事件顺序扁平排列；answer 估算对齐封卡实际元素数；answer 内部拆分（`split_answer_segment`）；answer 增长时动态重新估算 |
| `cardkit.py` | 712 | 卡片 JSON 构建 | `_downgrade_tables()`；`_build_error_panel()`；`build_cron_card()`；i18n locales；`cache` 字段渲染（💾 缓存命中/总输入 命中率%） |
| `cardkit_i18n.py` | 45 | 中英双语映射 | `_T` dict，`_i18n()` / `_t()` 快捷函数；新增 `cache` 条目 |
| `cardkit_md.py` | 121 | Markdown 处理 | 标题降级、表格降级(≤10)、图片 key 剥离、长文本分块(2400 chars) |
| `config.py` | 190 | 配置读取 | 惰性加载 + 运行时 `_reload_cached()`（5秒TTL缓存）；默认 footer `[status, elapsed, model, compression_exhausted]`（`cache` 需手动添加）；`_get_hermes_config_path()` 动态路径（多 Profile 支持） |
| `feishu.py` | 342 | 飞书 API 客户端 | CardKit v1/v2 + IM API；错误码分类；token 脱敏；`upload_local_image()` 本地文件上传 |
| `flush.py` | 156 | 节流调度器 | CardKit 100ms / IM PATCH 1.5s；互斥锁 + re-flush |
| `linear.py` | 180 | 线性 segment 状态 | `Segment` 数据类；`LinearState` 扁平管理；`split_tool_segment` / `split_answer_segment` 拆分 |
| `text.py` | 111 | 文本增量追踪 | `<think|thinking|thought>` 标签拆分；`TextState` 累积器 |
| `tooluse.py` | 299 | 工具调用追踪 | `ToolStep` / `ToolSession`；敏感信息脱敏 |
| `image.py` | 129 | 异步图片处理 | 下载远程图→上传飞书→替换 img_key；同步 strip + 异步上传 |
| `unavailable_guard.py` | 144 | 消息不可用保护 | 删除/撤回检测；30分钟 TTL 缓存 |
| `plugin.py` | 200 | 插件注册入口 | `register()` 注入配置 + 打补丁；`unregister()` 清理配置；自动备份 config.yaml；`_get_hermes_config_path()` 动态路径（多 Profile 支持）；插件只写 `streaming.footer.show_label`，不迁移用户配置 |
| `__init__.py`(子包) | 23 | 版本号导出 | 从 `plugin.yaml` 动态读取，失败 → warning + "unknown" |
| `__init__.py`(根) | 39 | 桥接模块 | `spec_from_file_location` 桥接到子包，解决 Hermes 加载方式兼容 |
| `setup.py` | 19 | 构建时版本 | 从 `plugin.yaml` 读版本，失败 raise |
| `pyproject.toml` | 30 | 构建配置 | `dynamic = ["version"]`；Python ≥3.11 |

---

## 4. 关键设计决策 (Key Design Decisions)

### 4.1 版本号：plugin.yaml 为唯一真值源

```
plugin.yaml (唯一版本号: "0.15.3")
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

通过 `_started_msg_ids` 集合追踪：如果返回 None 时集合中还有其他 msg_id，说明是中断而非正常完成。v0.11.0 起，所有操作加 `threading.Lock` 保护，确保并发消息安全。

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
IDLE → CREATING → STREAMING → COMPLETING → COMPLETED
                    │              │
                    ├→ FAILED      └→ (card_sent=True → suppress Hermes reply)
                    └→ ABORTED
```

- **IDLE**: 初始状态
- **CREATING**: 正在创建卡片（CardKit API / IM API）
- **STREAMING**: 正在流式更新
- **COMPLETING**: 正在完成（状态转移在 `await` 之前同步执行，防止 hermes 双调竞态）；`_TERMINAL` 成员，后续 `on_answer`/`on_reasoning` 等被跳过；`_do_update_card` 仍允许此状态以刷出待发文本
- **COMPLETED**: 终态，卡片已发送
- **FAILED**: API 错误，降级为 Hermes 默认回复
- **ABORTED**: 消息被中断/删除

终态集合: `{COMPLETING, COMPLETED, FAILED, ABORTED}`

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
- Tool segment 按 step 边界拆分，Answer segment 按文本块边界拆分
- 超了就拆，拆完还超继续拆

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
      - [status, elapsed, model, compression_exhausted]
    show_label: false
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
| 10 | `on_cron_deliver` | `cron.scheduler._deliver_result` | **async** | Cron 推送卡片 |
| 11 | `on_message_completed` (bg) | `_run_background_task` | sync | 后台任务卡片（复用 Hook 2，task_id 作为 message_id） |

---

## 10. 常见陷阱与经验教训

### 10.1 事件循环死锁
**❌ 错误**: 在 async 函数中调用 `run_coroutine_threadsafe(coro, loop).result(timeout=30)`
**✅ 正确**: 直接 `await coro`

### 10.2 内容重复显示
**原因**: `interim_assistant_callback` 和 `stream_delta_callback` 处理同一段文本
**解决** (v0.15.0): `_thinking_wrapper` 检查 `on_thinking_delta` 返回值：卡片消费文字时 `return`（不调原始回调）；文字已被 `stream_delta_callback` 消费时 dedup 跳过；仅在卡片未消费时才调原始回调作为降级

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
v0.11.0 起，超限时自动触发拆卡（而非仅打日志），设置 `element_limit_hit` 标志后跳过新增段，拆卡成功后重置标志和元素计数。
v0.12.2 起，answer 估算对齐封卡实际元素数（按 `_split_long_text` 分块数），answer 具备内部拆分能力（`split_answer_segment`），拆卡判断不再因估算偏低而失效。

### 10.7.1 Answer 估算偏差（v0.12.2 修复）
**问题**: 流式阶段 answer 只占 1 个 streaming markdown element，但封卡时 `_split_long_text` 会将长文本拆成 N 个 markdown 元素。旧代码 `_estimate_segment_elements` 对 answer 恒返回 1，导致流式阶段判断"不超限"，封卡时实际超限——拆卡后依旧超元素。
**解决**: 
- 估算对齐封卡实际：`_estimate_segment_elements` 对 answer 按 `_split_long_text` 实际分块数计算
- 动态更新：`_do_linear_flush` 步骤 0 对已创建的 dirty answer segment 重新估算并更新 `element_count`
- 内部拆分：`split_answer_segment` 按文本块边界拆分 answer segment，对标 tool 的 `split_tool_segment`
- 相邻触发：相邻 answer segment 也触发拆卡（与 tool segment 一致）

**模式**: 估算必须对齐实际渲染行为，否则拆卡判断形同虚设。

### 10.8 on_completed 幂等容错（v0.11.0 新增）
**问题**: Hermes 两条路径（`_process_message_background` 的 finally + `pop_post_delivery_callback`）可能对同一 msg_id 双调 `on_completed`，竞态窗口内两次调用触发飞书 300317 sequence 冲突。
**解决**: 
- 新增 `COMPLETING` 状态，状态转移在 `await` 之前同步执行——第二次调用发现已在 `COMPLETING`，直接返回，不再进入完成流程
- 300317 错误视为幂等成功：设置 `state=COMPLETED` 并返回 `True`
- `_was_aborted` 保存中断标记，供完成方法在 `COMPLETING` 状态下仍能获取中断信息

**模式**: 幂等守卫 = 同步状态转移 + 错误码容错，适用于异步回调竞态场景

### 10.9 Cron 补丁签名不匹配（v0.12.0 修复）
**问题**: `_deliver_result` 是 `cron.scheduler` **模块级函数**，不是 `Scheduler` 类方法。旧代码 `Scheduler._deliver_result = ...` 必然 `AttributeError`。
**解决**: 改为 patch 模块级属性 `cron.scheduler._deliver_result`；采用临时替换 Feishu adapter 的 `send` 方法策略，卡片替换纯文本（无重复消息），失败时自动降级为纯文本。

**模式**: monkey patch 模块级函数时，必须确认目标是类方法还是模块级函数；签名不匹配 = 静默失败。

### 10.10 后台任务卡片 — adapter.send 临时替换（v0.12.0 新增）
**策略**: Cron 和后台任务均采用"临时替换 Feishu adapter.send"策略：
1. 进入包装器时，保存原始 `adapter.send`
2. 替换为卡片版本，卡片成功时返回 `SendResult(success=True)` 让 Hermes 认为发送成功
3. 卡片失败时回退到原始 `send`
4. `finally` 块恢复原始 `send`，线程安全

**关键**: 无重复消息——卡片替换纯文本，不是追加；`card_sent=True` 时抑制 Hermes 原始纯文本回复。

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
  test_monkey_patch.py — 时间注入格式（XML 标签）、重入守卫、prefix cache 一致性、版本日志、cron 投递降级
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
| v0.11.0 | 2026-05-29 | 超限自动拆卡（卡片不再卡死）、拆卡失败+超限死局修复、Config TTL 缓存（减少磁盘读取）、`_started_msg_ids` 线程安全、`on_completed` 状态机+幂等容错（COMPLETING 状态 + 300317 错误处理） |
| v0.12.0 | 2026-05-29 | README 效果图 + Cron 推送卡片补丁修复（adapter.send 临时替换策略）+ `/background` 后台任务卡片 + 页脚 `cache` 缓存命中率字段 + 默认页脚精简（移除 api_calls/history_offset） |
| v0.12.1 | 2026-05-29 | 竞态条件修复（`_card_ready` 同步）+ 错误/状态消息卡片内显示（`interim_assistant_callback` 重新包装）+ `card_sent` 误报修复（文本回退）+ card_id 空值检查 |
| v0.12.2 | 2026-05-29 | 拆卡后依旧超元素修复：answer 估算对齐封卡实际元素数 + answer 内部拆分（`split_answer_segment`）+ answer 增长时动态重新估算 + 相邻 answer segment 拆卡触发 |
| v0.12.3 | 2026-05-29 | CI 测试修复（添加 pytest-asyncio 依赖）+ 多 Profile 部署修复（`_get_hermes_config_path()` 动态读取 HERMES_HOME） |
| v0.12.4 | 2026-05-29 | 默认页脚精简（移除 `cache`、`show_label` 默认 `false`）+ 状态文字去 emoji（✅❌🛑→纯文字）+ `show_label` 重复确认（插件只写 `footer.show_label`，不迁移用户配置）+ 致谢新增 joshcheng820222 + `test_version.py` 版本号动态读取 |
| v0.15.0 | 2026-05-31 | Cron 推送卡片修复（`_card_sending_send` 死锁→直接 await）+ `_thinking_wrapper` 重复消息修复（检查 consumed 返回值）+ 关键日志含版本号 + `finish_reason` 诊断日志（`content_filter` 等异常可排查） |
| v0.15.1 | 2026-06-03 | `_msg_ctx` 泄漏修复（消息处理后清除上下文）+ 递归中断上下文隔离（`_saved_parent_ctx` + `_force_rewrap`）+ 并发消息 `card_sent` 误判修复（每消息独立 `msg_context` 字典） |
| v0.15.2 | 2026-06-04 | 网关卡片 `plain_text` schema 修复 + `edit_message` metadata 参数修复 + `NoneType` 下标防御 + 中断旧卡片立即 ABORTED + `_force_rewrap` 中断回调重包装 + 图片 `_try_add_image_to_session` |
| v0.15.3 | 2026-06-05 | 递归中断子级 COMPLETE hook 修复（核心 bug：B 的卡片永远不完成→重复卡片+错误内容）+ 图片拦截器 `send_image_file`/`send_image`（Agent 管道中图片→卡片会话）+ `upload_local_image()` |

---

## 14. 待做事项 (Roadmap)

- [ ] 拆卡首卡片 `partial` 状态显示
- [x] ~~`/background` 后台任务卡片~~（v0.12.0 已实现：流式卡片 + 话题回复 + 抑制纯文本）
- [ ] `background_review` 进度消息放入卡片
- [ ] DEV → master 兼容性回归测试
- [ ] 考虑更多 Hermes 版本的兼容性探测
- [ ] `inject_time` 时区配置化（当前硬编码 CST/UTC+8）
- [x] ~~`_handle_linear_flush_error` 对 `CARDKIT_ELEMENT_LIMIT` 增加超限拆卡~~（v0.11.0 已实现：超限自动触发拆卡 + `element_limit_hit` 标志）
- [x] ~~`on_completed` 被 hermes 双调触发 300317~~（v0.11.0 已实现：COMPLETING 状态机守卫 + 300317 幂等成功 + `_was_aborted` 中断标记）
- [x] ~~拆卡后依旧超元素（answer 估算偏差 + 缺少内部拆分）~~（v0.12.2 已实现：answer 估算对齐封卡实际 + `split_answer_segment` + 动态重新估算 + 相邻 answer 拆卡触发）

---

## 15. 快速定位问题

| 症状 | 检查 | 文件 |
|------|------|------|
| 卡片不出现 | `grep "GatewayRunner" agent.log` 看补丁是否成功 | monkey_patch.py |
| 内容重复 | `interim_assistant_callback` 是否被包裹 | monkey_patch.py `_maybe_wrap_callbacks` |
| Cron 推送纯文本 | `grep "cron" agent.log` 看是否有 patch 失败；v0.15.0 前有死锁 bug | monkey_patch.py `_wrap_cron_deliver` |
| 后台任务纯文本 | `grep "background" agent.log` 看 patch 是否成功 | monkey_patch.py `_wrap_run_background_task` |
| 页脚无 cache 字段 | `cache_read_tokens` 是否从 agent 引用中提取 | monkey_patch.py `_maybe_wrap_callbacks` |
| Apple Silicon 报错 | `grep "conversation_loop" agent.log` | monkey_patch.py `_resolve_hermes_agent_module` |
| 版本号显示 unknown | plugin.yaml 是否存在于正确路径 | `__init__.py` |
| 页脚耗时为 0 | `_msg_start_time` 是否正确设置 | monkey_patch.py `_wrap_handle_message_with_agent` |
| 消息删除后仍在更新 | UnavailableGuard 是否工作 | unavailable_guard.py |
| 拆卡后依旧超元素 | answer 估算是否对齐封卡实际、`split_answer_segment` 是否生效 | controller_linear_mixin.py `_estimate_segment_elements` / `_find_answer_split_offset` |
| 卡片卡死不更新 | 元素超限后无限重试失败 | controller_linear_mixin.py `_handle_linear_flush_error_async` |

---

*Last updated: 2026-05-31 | Version: 0.15.2 DEV*
