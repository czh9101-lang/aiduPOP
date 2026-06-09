# 🧠 hermes-lark-streaming — LLM 快速上手指南

> **Purpose**: 项目技能卡片。阅读后应能理解架构、关键设计决策、常见陷阱，并高效修改代码。

---

## 1. 项目概述

**hermes-lark-streaming** 是 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的飞书/Lark CardKit v2.0 流式卡片插件。AI 对话过程中实时更新飞书交互卡片（打字效果、工具面板、推理过程、完成态统计等）。

| 属性 | 值 |
|------|-----|
| 版本 | 1.0.1 (DEV) | 协议 | MIT | Python | ≥3.11 | 与上游 | ⚠️ **不兼容** |

---

## 2. 架构全景

```
用户消息 → GatewayRunner._handle_message ── [Hook 0: on_feishu_normalize]
               ▼
        _handle_message_with_agent ── [Hook 1/8/9: started/aborted/interrupted]
               ▼
        _run_agent ── [Hook 2: on_message_completed]
               ▼
        AIAgent.run_conversation ── [inject_time 前缀注入]
            ├─ stream_delta_callback ── [Hook 4: on_answer_delta]
            ├─ reasoning_callback ──── [Hook 6: on_reasoning_delta]
            ├─ tool_progress_callback [Hook 3: on_tool_updated]
            └─ background_review_cb ── [Hook 7: on_background_review]
Cron: _deliver_result ── [Hook 10: on_cron_deliver] (async)
Background: _run_background_task ── [Hook 1/2]
```

调用链: `patching → patch → controller → mixin → cardkit → feishu → flush`

---

## 3. 文件地图与职责

| 文件 | 行数 | 职责 | 关键点 |
|------|------|------|--------|
| **patching/** | | **运行时拦截子包** | |
| `├ __init__.py` | ~770 | 入口 + 共享状态 + 编排 | `apply_patches()` + 模块解析 + 延迟补丁 |
| `├ gateway.py` | ~890 | GatewayRunner 包装 | 6 个 wrapper + 时间前缀注入 + cron/background |
| `├ callbacks.py` | ~230 | 回调包装 | 5 个内部 wrapper 防重复消费 |
| `└ adapter.py` | ~1030 | FeishuAdapter 包装 | send/edit/reaction/clarify 包装 + gateway card 注册 |
| `monkey_patch.py` | ~7 | 向后兼容 shim | `from .patching import *` |
| **cardkit/** | | **卡片构建子包** | |
| `├ __init__.py` | ~5 | 重导出门面 | `from .elements/cards/special import *` |
| `├ elements.py` | ~680 | 原始元素构建器 | streaming/reasoning/tool/error 面板 + footer |
| `├ cards.py` | ~440 | 卡片组装器 | streaming/complete/linear/IM-fallback 卡片 |
| `├ special.py` | ~410 | 专用卡片类型 | cron/gateway/clarify 三态卡片 |
| `├ i18n.py` | 58 | 中英双语映射 | `_T` dict + `_i18n()`/`_t()` |
| `└ md.py` | 121 | Markdown 处理 | 标题/表格降级、图片 key 剥离、长文本分块 |
| `cardkit.py` | ~7 | 向后兼容 shim | `from .cardkit import *` |
| `patch.py` | 229 | Hook 函数层 | `_safe_hook` 统一 enabled 检查 + 异常捕获 |
| `controller.py` | ~720 | 主控制器(单例) | 管理生命周期，导入 CardSession |
| `session.py` | ~110 | CardSession 数据类 | 37 字段 `__slots__`，独立于 controller |
| `controller_mixin.py` | ~580 | 异步 API 编排 | 状态机 + CardKit→IM PATCH 降级链 |
| `controller_linear_mixin.py` | ~1250 | 线性模式编排 | 拆卡(阈值185)、渐进降级封卡、segment 管理 |
| `linear_split.py` | ~170 | 拆分/估算逻辑 | 独立函数，预估元素数 + 查找拆分偏移 |
| `config.py` | ~270 | 配置读取 | `_plugin_sec()` 惰性加载 + 5秒 TTL 缓存 |
| `feishu.py` | ~450 | 飞书 API 客户端 | CardKit v1/v2 + IM API，错误码分类 |
| `flush.py` | ~185 | 节流调度器 | CardKit 500ms / IM PATCH 1.5s，互斥锁 + re-flush |
| `linear.py` | ~180 | 线性 segment 状态 | `Segment` 数据类 + `LinearState` 扁平管理 |
| `text.py` | ~111 | 文本增量追踪 | `<think|thinking|thought>` 标签拆分 |
| `tooluse.py` | ~299 | 工具调用追踪 | `ToolStep`/`ToolSession`，敏感信息脱敏 |
| `image.py` | ~129 | 异步图片处理 | 下载远程图→上传飞书→替换 img_key |
| `unavailable_guard.py` | ~144 | 消息不可用保护 | 删除/撤回检测，30分钟 TTL |
| `plugin.py` | ~250 | 插件注册入口 | `register()`/`unregister()`，自动备份 config |

---

## 4. 关键设计决策

**4.1 版本号唯一真值源**: `plugin.yaml` → 运行时读取(失败→"unknown") / 构建时读取(失败→raise)；`pyproject.toml` 用 `dynamic`。

**4.2 Monkey Patch 非 AST 注入**: 运行时方法替换，不修改源文件，卸载即恢复。代价：需自计时替代不可访问的局部变量。

**4.3 模块解析 + 线程安全**: `_resolve_hermes_agent_module()` 3层解析解决 Apple Silicon 冲突；`_started_msg_ids` 线程安全追踪中断；`threading.local()` 重入守卫；根 `__init__.py` 桥接。

**4.4 异步 + 双重补丁**: Cron 全链路异步化(禁止 `run_coroutine_threadsafe().result()`)；`run_conversation` 模块级+实例级双重补丁；Cron/后台临时替换 `adapter.send`（卡片替换纯文本）。

**4.5 时间感知格式**: XML 标签 `<time>HH:MM:SS</time>`，LLM 不模仿，无日期/时区后缀。

---

## 5. CardSession 状态机

```
IDLE → CREATING → STREAMING → COMPLETING → COMPLETED
                    │              │
                    ├→ FAILED      └→ (card_sent=True → suppress Hermes reply)
                    └→ ABORTED
```

COMPLETING: 状态转移在 `await` 前同步执行防竞态。终态: `{COMPLETING, COMPLETED, FAILED, ABORTED}`

---

## 6. 卡片 API 降级链

```
CardKit v2 Streaming → CardKit v2 Create+Patch → IM Create+Patch → Hermes 纯文本
```

`FeishuClient` 首次成功后锁定通道。

---

## 7. 线性模式

单卡按事件顺序渲染: `[Reasoning] → [Tool] → [Answer] → ...`。Segment 扁平排列，仅按元素数量超阈值拆卡（Trigger A，阈值 185/200），Tool 按 step 拆分，Answer 按文本块拆分。相邻同类型段不再强制拆卡（Trigger B 已移除，修复"秒拆"bug）。

---

## 8. 配置结构

```yaml
hermes_lark_streaming:
  enabled: true
  linear: true
  panel_expanded: false
  streaming_panel_expanded: false
  print_strategy: delay            # "fast" 或 "delay"
  flush_interval_ms: 500           # 100~2000ms
  card_ttl_sec: 600
  inject_time: false
  footer:
    show_label: false
    fields: [status, elapsed, model, compression_exhausted]
```

---

## 9. Hook 索引 (11 个注入点)

| # | Hook | 签名 | 说明 |
|---|------|------|------|
| 0 | `on_feishu_normalize` | sync | 修正飞书引用消息虚假 thread_id |
| 1 | `on_message_started` | sync | 创建 CardSession |
| 2 | `on_message_completed` | sync→bool | 完成态卡片，返回是否已发卡片 |
| 3 | `on_tool_updated` | sync | 工具调用状态更新 |
| 4 | `on_answer_delta` | sync | AI 回复增量文本 |
| 5 | `on_thinking_delta` | sync | 思考内容（被跳过防重复） |
| 6 | `on_reasoning_delta` | sync | 原生推理增量 |
| 7 | `on_background_review_message` | sync | 后台审查通知 |
| 8 | `on_message_aborted` | sync | 消息异常终止 |
| 9 | `on_message_interrupted` | sync | 新消息打断旧消息 |
| 10 | `on_cron_deliver` | **async** | Cron 推送卡片 |
| 11 | `on_message_completed`(bg) | sync | 后台任务卡片（复用 Hook 2） |

---

## 10. 常见陷阱与经验教训

### 10.1 事件循环死锁
在 async 函数中绝不用 `run_coroutine_threadsafe().result()`，直接 `await`。

### 10.2 内容重复→consumed 检查
`_thinking_wrapper` 检查 consumed 返回值：卡片消费→return，已消费→dedup 跳过，未消费→原始回调降级。

### 10.3 外部参数 NoneType 防护
外部字符串做切片/下标时必须防御 None：`(message_id or "?")[:12]`。版本号绝不硬编码 fallback。

### 10.4 contextvars 不跨线程
用 `_thread_local_ctx` 手动传递；`_run_agent` 中设置 thread-local。

### 10.5 card_sent 区分完成与中断
返回 None 两种含义：`card_sent=True`→正常完成抑制文本；`card_sent=False`→真正 abort/error。

### 10.6 FlushController 线程安全
worker 线程必须用 `call_soon_threadsafe()`，`call_soon()` 不唤醒事件循环→flush 永不执行。

### 10.7 元素估算必须对齐实际渲染
飞书卡片 2.0 硬上限 200 元素+组件（API 错误码 300307/300305），拆卡阈值 185（预留 15 给 footer+图片+封卡波动）。估算对齐封卡分块数+图片计数，否则拆卡判断形同虚设。

### 10.8 幂等守卫 = 同步状态转移 + 错误码容错
COMPLETING 状态同步转移 + 300317 容错，适用于异步回调竞态。

### 10.9 Monkey patch 签名确认
必须确认目标是类方法还是模块级函数；签名不匹配 = 静默失败。

### 10.10 封卡超限→渐进降级
全量封卡→compact seal（截断保留面板）→minimal seal（仅 answer 文本）。降级不应一步跳到最简方案。

### 10.11 性能参数应可配置
性能敏感参数不应硬编码。默认 500ms 刷新间隔（可配置 100~2000ms）。

### 10.12 即时反馈
首卡插入加载提示占位符，首段 answer 到达时同一 batch_update 移除（零额外 API 开销）。

### 10.13 序列冲突≠幂等成功
300317 表示 sequence 不一致，必须重试或降级。拆卡时先封旧卡再建新卡。

---

## 11. 测试结构

```
tests/
  test_version.py              — 版本号读取逻辑
  test_patch.py                — Hook 函数单元测试
  test_controller.py           — 会话生命周期 + 线性模式
  test_cardkit.py              — 卡片 JSON 构建
  test_config.py               — 配置读取
  test_flush.py                — 节流调度器
  test_text.py                 — 文本增量追踪
  test_image.py                — 图片解析
  test_linear.py               — 线性 segment 管理
  test_tooluse.py              — 工具调用追踪
  test_monkey_patch.py         — 时间感知/重入守卫/cron 降级
  test_unavailable_guard.py    — 消息不可用保护
  test_gateway_card.py         — 网关卡片构建
  test_callback_interception.py — 回调拦截
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

## 13. 版本历史

详见 [CHANGELOG.md](CHANGELOG.md)

---

## 14. 快速定位问题

| 症状 | 检查 | 文件 |
|------|------|------|
| 卡片不出现 | `grep "GatewayRunner" agent.log` | patching/__init__.py |
| 内容重复 | `interim_assistant_callback` 是否被包裹 | patching/callbacks.py |
| Cron 推送纯文本 | `grep "cron" agent.log` | patching/gateway.py |
| 后台任务纯文本 | `grep "background" agent.log` | patching/gateway.py |
| 页脚无 cache 字段 | `cache_read_tokens` 是否提取 | patching/callbacks.py |
| Apple Silicon 报错 | `grep "conversation_loop" agent.log` | patching/__init__.py |
| 版本号 unknown | plugin.yaml 路径 | `__init__.py` |
| 页脚耗时为 0 | `_msg_start_time` 设置 | patching/gateway.py |
| 消息删除后仍更新 | UnavailableGuard | unavailable_guard.py |
| 拆卡后超元素 | answer 估算对齐 | linear_split.py |
| 卡片卡死不更新 | 元素超限无限重试 | controller_linear_mixin.py |

---

*Last updated: 2026-06-09 | Version: 1.0.1*
