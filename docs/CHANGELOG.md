## v1.1.0 (2026-06-17)

**重大版本：基于 roadmap.md 全面优化，涵盖阶段 0-3 共 25 个任务 + 6 项细节修复。**

### v1.1.0 细节修复（第二轮）

| 类型 | 问题/功能 | 原因 | 修复/说明 |
|------|-----------|------|-----------|
| 🔧 Fix | AGENT_GUIDE.md show_reasoning 配置位置错误 | 文档把 show_reasoning 放在 hermes_lark_streaming 节下，实际代码读的是 display.platforms.feishu.show_reasoning 或 display.show_reasoning | 文档拆分为"hermes_lark_streaming 节"和"display 节"两个表格，明确 show_reasoning 在 display 下 |
| 🔧 Fix | 还原页脚默认值 | v1.1.0 第一轮误改了 footer.fields 默认值（新增 tokens/context 第二行），用户未要求改默认值 | 还原为 `[[status, elapsed, model, cost, compression_exhausted]]` 单行 |
| 🏗️ Architecture | 根目录文件模块化 | hermes_adapter.py/monitor.py/plugin.py/conftest.py 散落在根目录，不便维护 | hermes_adapter.py → patching/hermes_adapter.py；monitor.py → monitor/__init__.py；plugin.py → plugin/__init__.py；conftest.py 合并到 tests/conftest.py |
| 📝 Docs | README 补充新功能文档 | v1.1.0 新增的监控面板/主题/热更新/doctor 命令未在 README 记录 | 中英文 README 各新增"v1.1.0 New Features"章节 |
| ✨ Feature | e2e 测试统一 runner（mock/真飞书自动切换） | 原设计需要两套 runner（mock vs real），且真飞书模式需要手动获取 message_id | 单一 E2ETestRunner：有 FEISHU_E2E_APP_ID+APP_SECRET+CHAT_ID 环境变量→真飞书（自动发文本消息获取 anchor message_id）；无→mock。测试代码完全一致 |
| 📝 Docs | e2e 环境变量获取方式详细说明 | 用户不知道怎么获取 app_id/app_secret/chat_id | tests/e2e/.env.example 详细说明每个值的获取步骤（飞书开放平台路径、API 调用方式、webhook 事件方式） |

### 🔴 生产 Bug 修复

| 类型 | 问题/功能 | 原因 | 修复/说明 |
|------|-----------|------|-----------|
| 🐛 Bug Fix | 300313 "not find elementID" 短回复卡片闪烁 | add_elements 后 1s 内 stream_element 返回 300313（飞书服务端元素持久化传播延迟），源码无此错误码处理，drain 8 轮全失败后 full rebuild 导致卡片闪烁 | 新增 `CARDKIT_ELEMENT_NOT_FOUND = 300313` 常量 + `is_element_not_found_error()` 判断；stream_element 内置 200ms×3 次专用重试；drain/seal 阶段 300313 时 fallback 到 `partial_update_element` 写入 answer（绕过 stream_element） |
| ✨ Feature | stream_element 成功日志 | 生产日志 22h 内 0 次成功的 stream_element 日志，无法判断是否工作 | 新增 INFO 级 `HLS: stream_element OK` 日志，记录 card/element/len/seq |

### 阶段 0：稳定性与可观测性

| 类型 | 问题/功能 | 原因 | 修复/说明 |
|------|-----------|------|-----------|
| 🔧 Fix | 日志前缀混乱 | HLS_DIAG/HLS_WRAP/HLS_CALLED/HLS_FIX 四个前缀无规范，22 处散落 | 统一为 `HLS:` 前缀；诊断日志全部降为 DEBUG；WARNING 只留给功能受损 |
| ✨ Feature | card_trace_id | 同一张卡片的日志散落不同时间点，靠 msg_id 人工串联 | CardSession 新增 `card_trace_id`（msg_id 后 6 位），关键生命周期日志统一带 trace |
| ✨ Feature | 启动补丁应用报告 | Hermes 升级后补丁静默失效，无结构化状态 | `apply_patches()` 结束时记录 `_patch_status` 字典（6 个补丁目标 + Hermes layout） |
| ✨ Feature | `doctor` 命令 | 用户排障需要手动跑多个命令 | `__main__.py doctor`：6 步检查（版本/Python/配置/凭据/补丁状态/日志路径） |
| 🔧 Fix | 文档错误 | ISSUES_TEMPLATE 让用户 grep `gateway.log`（实际在 `agent.log`）；AGENT_GUIDE 配置项名写错 | 修正日志路径；配置项名对齐代码（`enabled`/`flush_interval_ms`/`card_ttl_sec` 等） |
| 🔧 Fix | 19 处 `except Exception: pass` | 异常被静默吞掉，排查时看不到 | 替换为 `_logger.debug("HLS: suppressed exception", exc_info=True)` |

### 阶段 1：去重与降复杂度

| 类型 | 问题/功能 | 原因 | 修复/说明 |
|------|-----------|------|-----------|
| 🏗️ Architecture | 删除非线性 ControllerMixin 主路径 | 631 行代码几乎不用（CardKit 创建失败时直接降级到 IM 卡片） | 删除 `_do_create_card`/`_do_update_card`/`_do_tool_use_status_update`/`_do_reasoning_update`/`_do_complete`/`_do_complete_inner`（−407 行）；core.py 始终走线性路径 |
| 🔧 Fix | 去重机制 5 层叠加 | `_hls_wrapper` + `already_streamed` + `_stream_consumed_len` + `_native_reasoning_active` + `_force_rewrap`，逻辑难追踪 | 移除 `_native_reasoning_active`（用 `bool(state._current_reasoning)` 代替）和 `_force_rewrap`（用 `_resolve_eid()` ContextVar 重解析代替）；简化 late_reasoning_wrapper（58→17 行） |
| 🔧 Fix | 状态机 8 个布尔标志位 | `_panel_element_created`/`_answer_element_created`/`_loading_hint_removed` 等标志位组合爆炸 | 合并为 `_creation_stages: set[str]`（含 `"panel"`/`"answer"`/`"hint_removed"`），24 处机械替换 |
| 🏗️ Architecture | 删除 backward-compat 别名 | `LinearState`/`Segment`/`linear_state`/`FAILED`/`LinearControllerMixin` 占据维护成本 | 全部删除，源码引用改为 `UnifiedLinearState`/`ReasoningRound`/`unified_state`/`CREATION_FAILED`/`UnifiedControllerMixin` |
| ✨ Feature | 拆分 build_unified_panel | 每次 flush 重建整个 panel JSON | 拆为 `build_panel_header()` + `build_panel_children()`，支持只重建 children |

### 阶段 2：性能与体验

| 类型 | 问题/功能 | 原因 | 修复/说明 |
|------|-----------|------|-----------|
| ✨ Feature | 错误卡片友好化 | 错误卡片直接显示技术细节（如 `300315 unknown property 'icon'`） | 改为"AI 回复出错，请重试"+ 调试 ID + 可折叠技术详情 |
| 🔧 Fix | 页脚默认字段 | 用户最常问"用了多少 token"但 tokens 默认不显示 | 默认页脚新增第二行 `[tokens, context]` |
| ✨ Feature | 并发限流 | 同一 chat_id 多张活跃卡片竞争 API 调用 | `on_message_started` 时 seal 同 chat_id 的旧活跃卡片为"被新消息取代" |

### 阶段 3：长期架构演进

| 类型 | 问题/功能 | 原因 | 修复/说明 |
|------|-----------|------|-----------|
| 🏗️ Architecture | Hermes 适配层 | Hermes 内部接口散落在 patching/__init__.py，升级时改多处 | 新建 `hermes_adapter.py`，`HermesCompat` 类封装所有 Hermes 内部模块访问 |
| ✨ Feature | 版本探测 + 适配 | Hermes 升级后无法自动选择正确的适配实现 | `HermesCompat._detect_version()` 探测 Hermes 版本，`_resolve_modules()` 3 层策略解析 conversation_loop |
| ✨ Feature | 完整端到端测试框架 | 无"发消息→看卡片 JSON"全链路测试 | `tests/e2e/` 新增 MockFeishuServer + E2ETestRunner + 14 个测试用例（简单回答/推理/工具/错误处理/并发/卡片结构） |
| ✨ Feature | 配置项运行时热更新 | 改配置要重启网关 | `Config.reload()` 清缓存 + mtime 自动检测 + `on_reload` 回调注册 |
| ✨ Feature | 多卡片样式主题 | 卡片颜色/图标硬编码 | `cardkit/theme.py` 提供 3 个预设主题（default/dark/compact）+ 用户自定义覆盖 |
| ✨ Feature | 监控面板 | 无实时插件健康指标 | `monitor.py` 轻量 HTTP 服务器（aiohttp），`/` HTML 仪表盘 + `/metrics` JSON + `/health` 健康检查；配置 `monitor.enabled/port/host` |

### 量化改进

| 指标 | v1.0.7 | v1.1.0 |
|------|--------|--------|
| 源代码行数（不含测试） | ~12,600 | ~13,165（+565，新增 4 个功能文件） |
| 已知错误码覆盖 | 11（缺 300313） | 12（含 300313） |
| 去重机制层数 | 5 | 2 |
| 状态机布尔标志位 | 8 | 5（+1 set） |
| `except Exception: pass` | 19 | 1（doctor 的 stat 调用） |
| 处理飞书错误码的文件数 | 5 | 集中到 feishu/client.py |
| Monkey patch 目标数 | 8+（散落） | 8+（集中到 HermesCompat） |
| 端到端测试 | 0 | 14 个用例 |
| 新增功能文件 | — | hermes_adapter.py, monitor.py, cardkit/theme.py, tests/e2e/ |

---

## v1.0.7 (2026-06-16)

| 类型 | 问题/功能 | 原因 | 修复/说明 |
|------|-----------|------|-----------|
| 🐛 Bug Fix | Cron/Gateway 静态卡片表格超限 | `build_cron_card()` 和 `build_gateway_card()` 调用 `_downgrade_tables()` 时不传 `limit`，默认用 20。但飞书静态卡片（非流式）硬限 5 张表格，超限被截断或报错 | 新增 `_MAX_CRON_TABLES = 5` 常量，Cron/Gateway 卡片改用 `limit=_MAX_CRON_TABLES`；流式卡片仍用 20 阈值 |
| 🔧 Fix | 工具步骤标题显示冗余状态文字 | 工具步骤标题有状态文字（Running/Succeeded/Failed），应去掉，只靠颜色区分；推理轮次标题没加粗、没颜色区分 | `_tool_status_info()` 去掉 label，`running` 颜色改 `orange-300`；`_build_tool_step_title()` 改为颜色+加粗统一格式；新增 `_build_reasoning_round_title()` 辅助函数统一推理标题渲染（orange-300 进行中、green 已完成、red 失败） |
| 🔧 Fix | 推理内容缺少缩进 | 推理轮次的思考内容（markdown tag）和标题左对齐，没有缩进；而工具步骤的 detail/output 都有 22px 缩进 | 推理内容从 `markdown` tag 改为 `div` + `lark_md` + `margin: "0px 0px 0px 22px"`，与工具内容对齐 |
| 🔧 Fix | Schema Error 300315 日志缺少关键细节 | 飞书返回 300315 错误时包含具体哪个属性非法，但日志只记录整个异常字符串，需人工翻找 | `FeishuAPIError` 新增 `extract_schema_detail()` 方法，3 处 schema error 日志新增 `detail:` 字段，一眼可见非法属性 |
| 🐛 Bug Fix | 并发消息可能污染新卡片内容 | 用户快速连发多条消息时，旧消息的回调可能在旧 session 上继续写入，导致新卡片内容被污染 | `on_reasoning`/`on_tool_update`/`on_answer` 三个回调入口添加 epoch 校验，检测到 stale epoch 自动跳过 |
| ✨ Feature | 新增 AGENT_GUIDE.md | Agent（AI 助手、自动化脚本）需要高信息密度文档了解安装/配置/排障，现有 README token 消耗高 | 新增 `docs/AGENT_GUIDE.md`，~2KB 高密度机器可读文档 |

## v1.0.6 (2026-06-15)

| 类型 | 问题/功能 | 原因 | 修复/说明 |
|------|-----------|------|-----------|
| 🐛 Bug Fix | 卡片超限300305导致内容重复 | 当 AI 调用大量工具（如44步）时，卡片元素数超过飞书200上限，封口报错300305后触发文本兜底，导致卡片和文本重复展示同一内容 | 统一面板自动裁剪超出的推理轮次和工具步骤，折叠为提示行（`⚡ 还有X步早期操作已折叠`），确保卡片元素永不超限 |
| 🔧 Fix | 封口顺序导致卡片被"冻住"缺页脚 | `_preservative_seal` 中先 `close_streaming` 再 `batch_update`，若封口失败则卡片流式已关闭但缺页脚 | 将 `close_streaming` 移到 `batch_update` 之后执行，先写入内容+页脚再关闭流式模式 |
| ✨ Feature | 新增面板裁剪配置项 | 无法控制统一面板中显示的推理轮次和工具步骤数量 | 新增 `max_tool_steps`（默认20）和 `max_reasoning_rounds`（默认20）配置项，超出部分自动折叠为提示行，范围1~100 |
| ✨ Feature | 卡片级元素安全网 | 面板内部安全网无法感知面板外部元素（answer、footer、error），只能用保守160阈值猜测 | 安全网上移到卡片层：封卡时已知所有元素（面板+answer+footer+error），精确递归计数总 tag objects，超过195（200-5缓冲）自动从面板children最老项开始裁剪；两条封卡路径（`_preservative_seal` 逐增量 + `build_unified_complete_card` 全卡重建）均覆盖 |

## v1.0.5 (2026-06-14)

| 类型 | 问题/功能 | 原因 | 修复/说明 |
|------|-----------|------|-----------|
| 🐛 Bug Fix | notify_feishu.py 提交消息重复 | Gitee MR 合并产生重复提交消息（如 `!42 fix: xxx` 与 `fix: xxx`） | `notify_feishu.py` 新增提交消息去重逻辑，基于规范化消息文本（去除 `!N ` 前缀）去重 |
| 🐛 Bug Fix | 简单对话显示空白 agent loop 面板 | 无工具/推理的简单对话在 Phase 2 创建了空面板，用户看到无内容的可折叠区域 | Phase 2 拆分为两条路径：有面板（工具/推理存在时）和无面板（简单对话）；晚到的推理/工具通过动态添加面板处理 |
| 🐛 Bug Fix | 正常完成的卡片被新消息覆盖成"已停止" | `on_interrupted`/`on_aborted` 不检查 COMPLETING 状态，导致正在收尾（drain）的 session 被误标 ABORTED，触发 fallback 发送 26 字符短文本覆盖完整卡片 | `on_interrupted`/`on_aborted` 入口新增 COMPLETING 短路：仅跳过 abort 逻辑，新 session 创建和 `_interrupt_map` 更新照常执行；`on_aborted` 标记 `_was_aborted` 让封卡显示"已停止"状态 |
| 🔧 Fix | `.hermes-last-release` 被 sync-from-gitee 反复覆盖 | 该文件被 git 追踪，GitHub Actions 写入新版本后，sync-from-gitee 每小时同步将 Gitee 侧的 `none` 覆盖回 GitHub，导致集成测试每天重复运行 | 将 `.hermes-last-release` 从 git 追踪移除（加入 `.gitignore`），改用 GitHub Actions Cache 持久化版本状态，不受同步工作流影响 |
| 🔧 Fix | FeishuAdapter 反应拦截在 Hermes 新版本静默失效 | Hermes 新版本将 `add_reaction`/`delete_reaction` 改为私有方法 `_add_reaction`/`_remove_reaction`，插件补丁使用 `try/except AttributeError` 静默跳过 | 补丁逻辑增加 fallback：先尝试公共方法名，失败后尝试私有方法名，兼容新旧版本 |
| 🔧 Fix | 3 个单元测试与 v1.0.5 Phase 2 拆分不同步 | Phase 2 拆分后简单对话不再创建空面板，新增 `_answer_element_created` 标志，部分测试缺少该标志导致测试路径错误 | 补全 `_answer_element_created = True`；修正 `_panel_element_created` 断言为 `_answer_element_created`；新增简单对话（无面板）完整生命周期测试 |
| 🔧 Fix | 集成测试在 sync-from-gitee 工作流中 25 个 skipped | `pytest tests/` 包含集成测试目录，但该工作流不设置 `HERMES_SRC_DIR`，导致全部 skip | `pyproject.toml` 新增 `norecursedirs = ["tests/integration"]`，集成测试由 `hermes-integration-test.yml` 单独运行 |
| ✨ Feature | Hermes Agent 集成测试工作流 | 需要自动检测 Hermes 新版本并验证插件兼容性 | 新增 GitHub Actions 工作流，每日上海时间 10:00 运行，检查 Hermes 新版本发布、运行兼容性测试、通知飞书 |
| ✨ Feature | 飞书卡片模板导出 | 卡片模板分散在代码中，不便维护和复用 | 所有飞书卡片模板导出至 `assets/card_templates/` 目录，集中管理 |

## v1.0.4 (2026-06-13)

| 类型 | 问题/功能 | 原因 | 修复/说明 |
|------|-----------|------|-----------|
| 🐛 Bug Fix | GitHub Actions 测试失败 (37/780) | test_phase.py: `asyncio.get_event_loop()` 在 Python 3.10+ 无事件循环时抛 RuntimeError；test_monkey_patch.py: guard 日志从 `_logger.debug("HLS_WRAP: guard check")` 变更为 `_logger.warning("HLS_DIAG: ...")` 但测试未同步；test_version.py: `importlib.reload` 在 Python 3.11+ 要求 `__spec__` 非 None | 1) `FlushController.__init__` 惰性获取事件循环（`_loop=None` + `_get_loop()`）；2) 测试设置 `asyncio.set_event_loop()`；3) 测试断言更新为 `_logger.warning` + `HLS_DIAG`；4) `importlib.reload` 替换为 `spec_from_file_location` 重注册 |
| 🐛 Bug Fix | 验证安装/卸载步骤 HERMES_PYTHON 路径错误 | 文档硬编码 `~/.hermes/hermes-agent/venv/bin/python3`，仅适用于 Hermes Desktop；CLI/服务器安装路径为 `/usr/local/lib/hermes-agent/venv/bin/python3` | 新增 `__main__.py python` 命令自动检测路径；文档改用 `$(python3 ... __main__.py python)` 自动检测；补充手动设置说明 |
| ✨ Feature | 新增 `python` CLI 命令 | 用户需要知道 Hermes venv Python 的路径才能运行 status/verify/cleanup | `__main__.py python` 自动搜索常见安装路径并输出，简化文档指令 |
| 🔧 Fix | FlushController 在无事件循环环境初始化失败 | Python 3.10+ 中 `asyncio.get_event_loop()` 在无事件循环时抛 RuntimeError | `__init__` 捕获双重 RuntimeError 设 `_loop=None`，新增 `_get_loop()` 惰性获取 |

## v1.0.3 (2026-06-12)

| 类型 | 问题/功能 | 原因 | 修复/说明 |
|------|-----------|------|-----------|
| 🏗️ Architecture | 卡片生命周期状态机优化 | 参考 openclaw-lark 设计，需显式状态转换、终端原因追踪、epoch 机制 | 新增 `CardPhase`/`TerminalReason`/`CardVisualState` + `PHASE_TRANSITIONS` 转换图 + `transition()`/`should_proceed()`/`is_stale_create()`/`enter_terminal()` 方法；`CREATION_FAILED` 替代旧 `FAILED`；新增 `TERMINATED` 阶段；88 个测试覆盖 |
| 🐛 Bug Fix | 会话列表永久显示"处理中..."（中文用户） | `cardkit_close_streaming` 只更新 `summary.content`，未更新 `summary.i18n_content`；飞书根据用户语言显示 `i18n_content.<locale>` | `cardkit_close_streaming` 同时更新 `content` 和 `i18n_content`（zh_cn + en_us）；新增 `_build_summary()` 辅助函数；4 个回归测试 |
| 🐛 Bug Fix | 重复 `close_streaming` 导致 300317 序列冲突 | `_preservative_seal` 主路径和重试路径都调用 `close_streaming`，第二次调用时 sequence 已过期 | `CardSession` 新增 `_streaming_closed` 守卫标志，确保 `close_streaming` 只调用一次 |
| 🐛 Bug Fix | `UnboundLocalError: 'panel'` 导致恢复路径崩溃 | 300317 重试路径引用 try 块中 `panel` 变量，但 `panel` 在 `close_streaming` 之后才赋值 | 重试路径始终从当前状态重建 `retry_panel`，而非引用 try 块局部变量 |
| 🐛 Bug Fix | 折叠面板思考内容重复（DeepSeek 模型） | `stream_delta_callback` 为 None 时，守卫仅检查其 `_hls_wrapper` 标记，`interim_assistant_callback` 被双重包装 | 守卫同时检查两个回调的 `_hls_wrapper` 标记；去重逻辑从精确匹配升级为长度追踪 |
| 🐛 Bug Fix | 会话列表完成后仍显示"处理中..." | 飞书 settings API 不稳定处理同一请求中的 `summary` + `streaming_mode: false` | 两步更新：先 `close_streaming`（不含 summary），再 `cardkit_update_summary`；流式已关闭时仍更新摘要 |
| 🐛 Bug Fix | 封卡时内容丢失 | `_preservative_seal` 的完整性守卫只清除 dirty 标记未实际 flush | 守卫升级为在 `close_streaming` 前实际 flush 剩余脏数据 |
| 🐛 Bug Fix | 页脚早于回答内容出现 | `COMPLETING` 在 `_TERMINAL` 集合中，晚到回调被丢弃 | 移除 `COMPLETING` 出终端集；drain 步骤确保内容完整输出 |
| 🐛 Bug Fix | 流式参数低于官方默认值 | `print_frequency_ms` 为 10ms（官方默认 70ms） | `print_frequency_ms` 提升至 70ms；`_ANSWER_FAST_STREAM_MS` 提升至 70ms；`flush_interval_ms` 范围改为 70–2000ms |
| ✨ Feature | 打字机效果 | 流式卡片输出按字符渲染，匹配飞书 CardKit v2.0 文档行为 | `print_frequency_ms=70`、`print_step=1`、默认 `flush_interval_ms=100ms`、仅回答快流 70ms |
| 🚀 Performance | 延迟 Markdown 优化 | 流式期间每次 flush 都执行 `optimize_markdown_style` 开销大 | 流式期间发送原始文本，仅在封卡时执行完整 Markdown 优化 |
| 🚀 Performance | 间隔计时器优化 | `LONG_GAP_MS` 和 `BATCH_AFTER_GAP_MS` 过长 | `LONG_GAP_MS` 2.0s → 1.0s，`BATCH_AFTER_GAP_MS` 300ms → 100ms；瞬态重试延迟缩减 |

---

## 附录：历史陷阱与经验教训

> 以下内容记录了插件开发过程中遇到的关键陷阱和修复经验，按主题分类。这些经验已融入代码设计，记录于此供后续维护参考。

### A. 异步与线程安全

| # | 陷阱 | 教训 |
|---|------|------|
| A1 | 事件循环死锁 | 在 async 函数中绝不用 `run_coroutine_threadsafe().result()`，直接 `await` |
| A2 | contextvars 不跨线程 | 用 `_thread_local_ctx` 手动传递；`_run_agent` 中设置 thread-local |
| A3 | FlushController 线程安全 | worker 线程必须用 `call_soon_threadsafe()`，`call_soon()` 不唤醒事件循环→flush 永不执行 |

### B. 内容去重

| # | 陷阱 | 教训 |
|---|------|------|
| B1 | `already_streamed` 忽略导致双重投递 | Hermes 调用 `interim_assistant_callback(text, already_streamed=True)` 时，必须跳过 `on_thinking_delta`，直接透传给原始回调 |
| B2 | 精确字符串去重失败 | `interim_assistant_callback` 投递累积文本，与增量块长度不同，精确匹配永远失败。改用 `_stream_consumed_len` 按 eid 追踪已消费总长度 |
| B3 | `_maybe_wrap_callbacks` 双重包装 | 当 `stream_delta_callback` 为 None 时，守卫必须同时检查 `stream_delta_callback` AND `interim_assistant_callback` 的 `_hls_wrapper` 标记 |
| B4 | 推理内容重复（DeepSeek 模型） | 当原生 `reasoning_callback` 已激活时，`_linear_on_thinking` 必须跳过 `on_reasoning_delta`，避免累积文本再次追加 |

### C. 状态机与竞态

| # | 陷阱 | 教训 |
|---|------|------|
| C1 | `on_interrupted` 误触发于 COMPLETING | 旧 session 处于 COMPLETING 时，只跳过 abort 逻辑，但新 session 创建和 `_interrupt_map` 更新仍照常执行 |
| C2 | `card_sent` 区分完成与中断 | 返回 None 两种含义：`card_sent=True`→正常完成抑制文本；`card_sent=False`→真正 abort/error |
| C3 | Epoch 机制防止过期创建回调 | 创建前快照 `epoch = session.create_epoch`，创建后检查 `is_stale_create(epoch)`——epoch 已变则跳过转换 |
| C4 | 幂等守卫 | COMPLETING 状态同步转移 + 300317 容错，适用于异步回调竞态 |

### D. 封卡与流式关闭

| # | 陷阱 | 教训 |
|---|------|------|
| D1 | `close_streaming` 重复调用 | 对同一张卡片只能调用一次。重复调用导致 300317 sequence conflict。`CardSession` 新增 `_streaming_closed` 布尔标志 |
| D2 | 重试路径引用 try 块局部变量 | `_preservative_seal` 的 300317 重试路径引用了 `panel["header"]`，但 `panel` 仅在 try 块中赋值。重试路径必须从当前状态重建变量 |
| D3 | 封卡只删除实际存在的元素 | v1.0.2 之前盲目删除所有已知元素 ID，导致 300314 失败。现在只删除 `existing_elements` 中的元素 |
| D4 | 状态标志必须在 API 成功后设置 | `_loading_hint_removed` 等标志在 `batch_update` 成功后才设置，否则 API 失败时标志已设但实际未生效 |
| D5 | 完成前排空剩余脏数据 | `on_completed` 触发时可能还有脏数据未 flush。drain 步骤显式 flush 剩余内容，再 `mark_completed()` → close streaming → add footer |
| D6 | 关闭流式时必须更新摘要（含 i18n_content） | `close_streaming` 时同时更新 `summary.content` 和 `summary.i18n_content`（zh_cn + en_us），否则中文用户会话列表永久显示"处理中..." |

### E. Monkey Patching

| # | 陷阱 | 教训 |
|---|------|------|
| E1 | 签名确认 | 必须确认目标是类方法还是模块级函数；签名不匹配 = 静默失败 |
| E2 | `add_reaction` 改名 | Hermes 新版本将 `add_reaction`/`delete_reaction` 改为 `_add_reaction`/`_remove_reaction`，补丁需 fallback 尝试两种命名 |

### F. 性能与参数

| # | 陷阱 | 教训 |
|---|------|------|
| F1 | 性能参数应可配置 | 性能敏感参数不应硬编码。默认 100ms 刷新间隔（可配置 70~2000ms，最低 70ms 对齐飞书官方 `print_frequency_ms`） |
| F2 | 流式参数不低于官方推荐值 | `print_frequency_ms` 官方默认 70ms，`print_step` 官方默认 1，不可低于此值 |
| F3 | 主动 TTL 延长 | 卡片生存时间接近 540s 时自动延长 600s，防止 300309 流式关闭 |
| F4 | 延迟 Markdown 优化 | 流式期间发送原始文本，仅在封卡时执行完整 Markdown 优化 |
| F5 | 卡片未就绪时的延迟 flush | `card_message_ready=False` 时标记 `_pending_flush`，卡片创建完成后立即执行 |

### G. 架构设计

| # | 陷阱 | 教训 |
|---|------|------|
| G1 | 统一面板消除元素爆炸 | v1.0.2 之前每个 reasoning round 创建独立面板（4 元素/面板），元素数线性增长。统一面板架构集中在 1 个面板 + 1 个回答元素 = 3–4 元素恒定 |
| G2 | 按时间线交错渲染 | `panel_events` 时间线记录事件顺序，面板内容按时间线交错渲染（reasoning→tool→reasoning→tool） |
| G3 | 卡片生命周期 4 阶段渐进构建 | Phase 1 占位卡片（2 元素）→ Phase 2 首 token 添加面板/回答 → Phase 3 流式更新 → Phase 4 添加页脚 |
| G4 | `CREATION_FAILED` 替代 `FAILED` | 旧的 `FAILED` 是 catch-all，拆分为 `CREATION_FAILED`（创建失败）和 `TERMINATED`（消息删除） |
| G5 | 外部参数 NoneType 防护 | 外部字符串做切片/下标时必须防御 None：`(message_id or "?")[:12]` |
