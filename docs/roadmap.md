# hermes-lark-streaming 优化路线图

> 基于 v1.1.0 全量代码审计 + 46 小时生产日志分析
> 更新：2026-06-18 | 当前版本：1.1.0

---

## 已完成（v1.1.0）

### P0 紧急问题（8 个，全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P0-1 | 并发限制调用了不存在的 `on_message_interrupted` | 改为 `on_interrupted` + 日志改 WARNING |
| P0-2 | base_url 没传给 lark SDK | 加 `.domain(config.base_url)` |
| P0-3 | 配置热更新只对 `enabled` 生效 | mtime 检查移到 `_plugin_sec()` + `/aowen config reload` |
| P0-4 | pyproject.toml 缺 5 个子包 | 补全 feishu*/config*/aowen*/plugin*/flush* |
| P0-5 | unregister() 只清理配置 | 加 session 清理 |
| P0-6 | theme.py 死代码（187 行） | 删除 |
| P0-7 | 13 个 JSON 模板从未被读取 | 删除 |
| P0-8 | persist_user_timestamp TypeError | inspect.signature 检测 |
| P1-L | monitor/ → aowen/ 重命名 | 完成 |

---

## v1.2.0 — 稳定性提升版（P1）

> 目标：清理僵尸代码、修异常处理、补关键测试、解决生产持续问题

### 阶段一：僵尸代码清理（~800 行）

| 任务 | 说明 |
|------|------|
| 删 3 个未被调用函数 | `_resolve_hermes_agent_module`、`_detect_hermes_layout`、`_do_complete_with_fallback` |
| 删 7 处 unused imports | controller/core.py、patching/adapter.py、patching/gateway.py 等 |
| 删 4 处永不执行 else 分支 | controller/core.py 中 `session.linear` 始终为 True 的 else |
| 删 4 个死 cardkit builder | `build_streaming_card`、`build_complete_card`、`build_linear_complete_card`、`build_streaming_tool_use_pending_panel` |
| 删 2 个死 panel builder | `_build_tool_panel`、`_build_reasoning_panel` |
| 删 3 个死 element ID 常量 | `REASONING_ELEMENT_ID`、`REASONING_TEXT_ELEMENT_ID`、`TOOL_PANEL_ELEMENT_ID` |
| 删 6 个死 i18n key | `clarify_question`、`background_review`、`thinking`、`tool_use`、`tool_pending`、`steps` |
| 删 4 个死 CardSession 字段 | `last_tool_use_update`、`reasoning_text`、`reasoning_start`、`reasoning_dirty` |
| 删 3 个死 UnifiedLinearState 字段 | `_counter`、`bg_review_panel_id`、`bg_review_panel_added` |
| 删僵尸常量 | `CARDKIT_RATE_LIMITED` |
| 删僵尸测试 | `TestBuildStreamingCard`、`TestBuildCompleteCard`、`TestBuildLinearCompleteCard` |

### 阶段二：异常处理修复

| 任务 | 说明 |
|------|------|
| 15+ 处 `except Exception` 改 WARNING | patching/gateway.py、controller/core.py |
| 单独捕获 AttributeError/TypeError/KeyError | 这些通常是 bug，不应静默 |
| API 调用加 10 秒 timeout | feishu/client.py FeishuClient.__init__ |
| UnavailableGuard 加 asyncio.Lock | feishu/guard.py terminate() 无锁竞态 |

### 阶段三：重复代码抽取

| 任务 | 说明 |
|------|------|
| 抽 `_build_panel(state, session)` | build_unified_panel 在 seal 路径调用 4+ 次 |
| 抽 `_build_seal_summary(state)` | summary_text 计算重复 4 处 |
| 抽 `_gateway_card_intercept` 装饰器 | _intercepted_* 4 函数模式重复 |
| 合并 `_wrap_run_conversation` 两份 | gateway.py vs __init__.py |
| 抽 `_extract_agent_metrics(agent_ref)` | cache tokens 提取重复 2 处 |
| `_enforce_card_element_limit` 单一实现 | cards.py vs linear_mixin.py |
| 抽 `_fallback_write_answer_via_partial_update` | 300313 fallback 3 处 |
| 统一 `_get_hermes_config_path` | plugin/__init__.py + config/reader.py |
| 统一 `_format_code_block` | elements.py + tooluse.py |

### 阶段四：生产持续问题修复

| 任务 | 说明 |
|------|------|
| 修 `pruning stale session` | 同 msg_id 旧 session 未及时 cleanup（25 次/46h） |
| 修 `on_message_started missing message_id` | auto-resume 路径（5 次） |
| 硬编码 phase 字符串改用 TERMINAL_PHASES | patching/gateway.py 5 处 |
| INFO 日志精简 | 成功路径 20+ 条 INFO → 1-2 条 |

### 阶段五：关键测试补充

| 任务 | 说明 |
|------|------|
| feishu/client.py 单元测试 | 重试/错误码分类 |
| aowen/__init__.py 单元测试 | /aowen 命令路由 |
| 并发场景测试 | 同 chat 连发消息 |
| pip 安装路径 e2e 测试 | 验证 pyproject.toml 修复 |

---

## v1.3.0 — 体验优化版（P2）

> 目标：补全辅助命令、修文档不一致、修 UX 细节、加 CI 质量门禁

### 文档不一致修复

| 问题 | 位置 |
|------|------|
| README 还宣传 SKILL.md 有"陷阱"章节 | README.md + README.zh-CN.md |
| AGENT_GUIDE 漏 pre_gateway_dispatch 钩子 | docs/AGENT_GUIDE.md |
| CHANGELOG monitor 描述错（已改为 aowen） | docs/CHANGELOG.md |
| CHANGELOG 说 FAILED 别名删了，实际还在 | docs/CHANGELOG.md + SKILL.md |

### /aowen 命令补全

| 命令 | 说明 |
|------|------|
| `/aowen logs` | 拉取最近 N 条 HLS 日志 |
| `/aowen health` | 主动 ping 飞书 API 验证 token + 网络 |
| `/aowen sessions` | 列出当前活跃 CardSession |
| `/aowen version` | 单独显示版本 |

### 其他改进

| 任务 | 说明 |
|------|------|
| 推理过程单轮长度截断 | 每轮 reasoning 截断到 2000 字（可配置） |
| 硬编码中文走 i18n | 折叠提示、错误友好文案、footer label |
| 配置 schema 校验 | 防止 `enabled: "false"` 字符串变 True |
| CI 加 ruff lint | pyproject.toml 有 ruff 依赖但 CI 没用 |
| CI 加 Python 3.12/3.13 矩阵 | 目前只测 3.11 |
| 移除 wide_screen_mode | v2 已 deprecated，增加 JSON 噪声 |
| asyncio.get_event_loop() 改 get_running_loop() | Python 3.12+ 弃用 |
| 补全 _DEFAULT_STREAMING_CONFIG | 首次安装注入所有可调项 |

---

## v1.4.0 — 架构演进版（P3）

> 目标：架构层面优化，可能引入不兼容变更

| 任务 | 风险 | 说明 |
|------|------|------|
| 重命名 controller/mixin.py | 低 | 改为 gateway_helpers.py 或合并到 linear_mixin.py |
| 评估移除 TextState | 中 | linear 模式下半死，统一到 UnifiedLinearState |
| 流式 flush 增量优化 | 中 | 新工具步骤用 add_elements 而非整 panel 重建 |
| tooluse 改用 call_id | 中 | 同名工具并发时 name 匹配会错 |
| 移除 _thread_local_ctx | 低 | 完全依赖 contextvars |
| Python 版本放宽到 3.9+ | 低 | 扩大兼容性 |

---

## 版本路线图总结

```
v1.1.0（已完成）— P0 紧急修复 + monitor→aowen 重命名
v1.2.0          — P1 僵尸代码清理 + 异常处理 + 重复代码 + 测试
v1.3.0          — P2 文档修复 + 命令补全 + UX 细节 + CI 门禁
v1.4.0          — P3 架构演进
```

---

## 回滚方式

```bash
cd ~/.hermes/plugins/hermes-lark-streaming
git fetch --tags
git checkout v旧版本
hermes gateway restart
```
