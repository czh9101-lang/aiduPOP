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
