# 更新日志 / Changelog

## v0.12.3 (2026-05-29)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | GitHub Actions CI 测试失败：async 测试函数无法运行 | `pyproject.toml` 的 `dev` 依赖缺少 `pytest-asyncio`，CI 环境安装 `pytest` 后无法识别 `async def` 测试函数 | 添加 `pytest-asyncio>=0.21.0` 到 `dev` 可选依赖；添加 `asyncio_mode = "auto"` 到 pytest 配置，自动发现 async 测试 |
| 2 | Bug | 多 Profile 部署场景下流式卡片不工作 | `config.py` 和 `plugin.py` 中的 `_HERMES_CONFIG_PATH` 在模块导入时读取 `HERMES_HOME`，但多 Profile 场景下 `HERMES_HOME` 在插件导入后才被 `_apply_profile_override()` 设置，导致路径错误 | 新增 `_get_hermes_config_path()` 函数：每次调用时动态读取 `HERMES_HOME` 环境变量，确保始终使用正确的配置路径；`Config._load()`、`Config._reload_cached()`、`_backup_config()`、`_ensure_streaming_config()`、`_cleanup_config()` 均改用动态路径；保留 `_HERMES_CONFIG_PATH` 常量用于向后兼容 |

## v0.12.2 (2026-05-29)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | 拆卡后依旧超元素：Answer 估算恒为 1，拆卡判断失效 | `_estimate_segment_elements` 对 answer 恒返回 1，但封卡时 answer 会被 `_split_long_text` 拆成 N 个 markdown 元素；流式阶段判断"不超限"，封卡时实际超限 | 修正 answer 估算：按封卡时 `_split_long_text` 实际分块数计算元素数，确保流式阶段拆卡判断基于封卡真实元素数 |
| 2 | Bug | 单个 Answer 超大时无法内部拆分，只能强行塞入当前卡 | 只有 Tool segment 有内部拆分能力（`split_tool_segment`），Answer segment 缺少对应的拆分机制 | 新增 `split_answer_segment`：按文本块边界拆分 answer segment；新增 `_find_answer_split_offset`：找到当前卡能容纳的最大文本块数；在 `_do_linear_flush` 中增加 answer 内部拆分触发逻辑（对标 tool 的内部拆分） |
| 3 | Bug | 已创建的 Answer 文本增长后估算不更新，可能导致拆卡延迟 | answer 创建时 `element_estimate = 1`，后续文本增长不再重新估算，`element_count` 中的旧值偏低 | 在 `_do_linear_flush` 步骤 0 增加 answer 估算动态更新：每次 flush 前对已创建的 dirty answer segment 重新估算并更新 `element_count`；增长后超限则触发 answer 内部拆分 + 拆卡 |
| 4 | Bug | 拆卡后相邻 Answer segment 不会触发继续拆卡 | `_do_linear_flush` 中只在相邻 tool segment 边界触发拆卡，相邻 answer segment 被忽略 | 扩展拆卡触发条件：相邻 answer segment 也触发拆卡（与 tool segment 一致） |

## v0.12.0 (2026-05-29)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Docs | README 功能特性列表改为效果图展示 | 功能特性文字列表不够直观，效果图一目了然 | 中英文 README 的"功能特性 / Features"节替换为"效果预览 / Effect Preview"，仅保留 img1 效果图 |
| 2 | Bug | Cron 推送卡片从未生效（补丁签名不匹配） | 旧代码 `Scheduler._deliver_result = ...` 必然 `AttributeError`（`_deliver_result` 是模块级函数，不是 `Scheduler` 类方法）；旧 wrapper 签名 `(self, platform_name, chat_id, ...)` 与实际 `(job, content, adapters, loop)` 不匹配 | 改为 patch 模块级函数 `cron.scheduler._deliver_result`；采用临时替换 Feishu adapter 的 `send` 方法策略，卡片替换纯文本（无重复消息），失败时自动降级为纯文本 |
| 3 | Feature | `/background` 后台任务完成后以卡片形式推送 | 后台任务（`/background`、`/bg`、`/btw`）完成后仅发送纯文本"✅ Background task complete" | 新增 `_wrap_run_background_task` 包装器：使用 `task_id` 作为卡片 message_id；支持话题内回复（thread_id 自动传递）；流式效果（思考、工具调用、回答实时更新）；完成后显示终端卡片（含 footer 信息）；自动抑制原始纯文本消息 |
| 4 | Feature | 页脚新增 `cache` 字段，显示缓存命中率 | — | 格式：`💾 136.3K/137.4K (99%)`（缓存命中/总输入 tokens × 命中率%）；默认页脚字段精简为 `[status, elapsed, model, cache, compression_exhausted]`；`api_calls`、`tokens`、`context`、`history_offset` 不再默认显示（仍可在 config.yaml 手动添加）；新增 i18n：`Cache {}` / `缓存 {}` |

## v0.11.0 (2026-05-29)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | 飞书卡片元素超限后卡片"卡死"，后续更新全部失败 | `_handle_linear_flush_error` 收到 `CARDKIT_ELEMENT_LIMIT` 错误后仅打日志，无任何恢复措施；下次 flush 继续往同一张卡塞内容 → 继续超限 → 无限循环直到 AI 输出完成 | 超限时自动触发拆卡：封存当前卡，开新卡继续流式输出；设置 `element_limit_hit` 标志，拆卡前跳过新增段避免继续超限；拆卡成功后重置标志和元素计数 |
| 2 | Bug | 拆卡失败后元素再超限 = 死局 | `split_disabled=True`（拆卡失败降级）后，元素超限无路可走 | 超限拆卡不受 `split_disabled` 限制（`_do_linear_split` 内部已有降级逻辑）；即使拆卡也失败，`element_limit_hit` 标志确保只刷已有段的脏文本，等完成阶段整体重建 |
| 3 | Perf | `inject_time` / `show_reasoning` 每次属性访问都读磁盘 | `_reload()` 每次调用都执行 `Path.read_text()` + `yaml.safe_load()`，流式输出期间每 100ms 可能触发多次，高频场景下不必要 | 新增 `_reload_cached()` 方法，带 5 秒 TTL 缓存：5 秒内复用上次读取结果，避免高频属性访问反复读磁盘；配置变更最多延迟 5 秒生效 |
| 4 | Bug | 并发消息可能漏判中断 | `_started_msg_ids` 是全局 `set`，两个消息同时到达时 `add` / `discard` / 差集运算非原子，可能漏判中断 | 所有 `_started_msg_ids` 操作加 `threading.Lock` 保护，确保并发安全 |
| 5 | Bug | `on_completed` 被 hermes 双调触发 300317 sequence 冲突 | hermes 两条路径（`_process_message_background` 的 finally + `pop_post_delivery_callback`）在同一 msg_id 上调用 `on_completed`，竞态窗口内两次调用触发 300317 | 新增 `COMPLETING` 状态，状态转移在 `await` 之前同步执行防止双调竞态；300317 错误视为幂等成功（设置 `state=COMPLETED` 并返回 `True`）；`_was_aborted` 保存中断标记供完成方法在 `COMPLETING` 状态下获取 |

## v0.10.2 (2026-05-28)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Perf | 时间注入格式 `[HH:MM:SS CST]` 被部分 LLM 忽略或模仿 | 方括号格式缺乏语义标记，某些模型将其视为噪声忽略，或在回复中模仿相同格式 | 改用 XML 标签格式 `<time>HH:MM:SS</time>`：LLM 普遍理解 XML 标签为结构化元数据，不会在回复中模仿；同时移除 CST 时区后缀（系统提示词已含时区上下文）和日期（系统提示词已含当前日期），减少 token 开销 |
| 2 | Perf | 线性模式预填充后已发送 segment 被冗余重刷 | `_do_linear_batch_update` 中 `new_el_ids` 非空时，对已创建的 reasoning/answer segment 强制设 `dirty=True`，即使文本未变更也会触发冗余 `stream_element` 调用 | 仅对自上次 flush 以来文本有实际变更的已创建 segment 设 `dirty=True`，减少不必要的 API 调用 |

## v0.10.1 (2026-05-28)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | 流式卡片跑马灯无文字，等很久才出文字，看到时已完成 | `FlushController.schedule_update` 使用 `call_soon` / `call_later` 从 LLM worker 线程调度到事件循环，这两个方法 **不唤醒事件循环**（缺少 `_write_to_self()`），导致回调虽入队列但永远不被及时处理 | `schedule_update` 改用 `call_soon_threadsafe` 调度到事件循环线程，确保每次 flush 请求立即唤醒事件循环；新增 `_schedule_update_on_loop()` 内部方法 |
| 2 | Perf | 首次文字出现慢 ~200ms | 线性模式创建 answer/reasoning 元素时内容为空，需额外一次 `stream_element` API 调用才出文字 | `batch_update` 时预填充已累积的文本内容，省去首次 `stream_element` 调用 |
| 3 | Bug | `on_thinking` 设置 `reasoning_text` 后未标记 `reasoning_dirty=True`，导致 `_do_update_card` 跳过更新 | 遗漏赋值 | 补充 `session.reasoning_dirty = True`（当前代码路径未激活，预防性修复） |

## v0.10.0 (2026-05-28)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Feature | 时间注入（`streaming.inject_time`） | — | 每条用户消息前自动添加 `[HH:MM:SS CST]` 时间前缀，同时写入 DB 保证前缀缓存一致性；`threading.local()` + `finally` 双重防护 |
| 2 | Bug | `/stop` 后卡片状态显示"已完成"而非"已停止" | `on_message_completed` 未传入中断标记 | 检测 `result.interrupted` / `result.partial`，传入 `aborted=True`，卡片显示 🛑 已停止 |
| 3 | Feature | 错误/中断消息在卡片正文展示 | 原先错误信息仅在页脚显示，不够醒目 | `result.error` 和 `result.interrupt_message` 以可折叠红色/橙色面板显示在卡片正文中，与推理面板、工具面板视觉风格一致 |
| 4 | Feature | 页脚新增 `compression_exhausted` 字段 | — | 上下文压缩耗尽时显示 ⚠ 上下文已满 |
| 5 | Chore | 默认页脚字段调整 | — | 调整为 `[status, elapsed, model, api_calls]` + `[tokens, context, history_offset, compression_exhausted]`；`show_label` 默认 `true` |
| 6 | Feature | 配置文件自动备份 | 卸载后无法恢复原始配置 | 首次修改 `config.yaml` 前自动备份为 `config.yaml.YYYYMMDD_HHMMSS.hermes-lark-streaming`，仅备份一次 |
| 7 | Bug | Apple Silicon Mac 报 `ModuleNotFoundError: No module named 'agent.conversation_loop'` | PyPI 第三方包 `agent` 遮蔽 Hermes 自身的 `agent` 包 | 新增 `_resolve_hermes_agent_module()` 三级模块解析：① sys.modules 缓存 → ② 锚点发现 → ③ 标准 import 回退；模块缺失时安全降级 |
| 8 | Chore | `apply_patches()` 中任何 import 失败导致整个插件崩溃 | V0.9.0 无 try/except，单个模块失败后全部补丁不执行 | 所有 import 包裹 try/except，单个模块补丁失败不影响其他补丁 |
| 9 | Bug | Cron 推送卡片从未生效，每次静默回退为纯文本 | `_wrap_cron_deliver` 为 async，内部同步调用 `on_cron_deliver` → `run_coroutine_threadsafe().result(30)` 阻塞事件循环导致 30 秒死锁超时 | 全链路改为 async：`on_cron_deliver` → `on_cron_deliver_async` → 直接 `await _do_cron_deliver()`，消除阻塞 |
| 10 | Bug | Cron 推送卡片中表格超限后渲染失败 | `build_cron_card` 缺少 `_downgrade_tables()` 调用 | 与 `build_complete_card` / `build_streaming_card` 一致，添加 `_downgrade_tables()` |

## v0.9.0 (2026-05-27)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | 卡片内容重复显示 | `interim_assistant_callback` 和 `stream_delta_callback` 包裹同一段文本，原版有 `already_streamed` 守卫防重，monkey patch 无法访问该参数 | 去掉 `interim_assistant_callback` 的 `_thinking_wrapper` 包裹，思考内容仍由 `reasoning_callback`（原生模型推理）处理 |
| 2 | Bug | 页脚耗时(elapsed)始终不显示 | `_response_time` 是 `_handle_message_with_agent` 的局部变量，不在 `_run_agent` 返回的 `agent_result` 中，`result.get("_response_time", 0)` 永远返回 0，`duration=0` 时 `_render_footer_field` 返回 None 不渲染 | 使用 `time.monotonic()` 自计时，在消息开始时记录 `_msg_start_time`，完成时计算差值作为耗时 |
| 3 | Bug | CLI 命令 `python -m hermes_lark_streaming` 报模块找不到 | 非标准安装路径下 `hermes_lark_streaming` 不在 `sys.path` 中 | `__main__.py` 新增 `_ensure_importable()` 函数，自动搜索 HERMES_HOME/plugins、site-packages 等常见路径；各子命令添加 ImportError 容错；简化 usage 信息 |
| 4 | Bug | 卡片中超过 3 个表格时后续表格显示为 Markdown 源码 | `_MAX_CARD_TABLES = 3` 过于保守，超限表格被降级为代码块 | `_MAX_CARD_TABLES` 由 3 调整为 10，绝大多数场景不再触发降级 |
| 5 | Feature | 页脚新增 `api_calls` 和 `history_offset` 字段 | — | 全链路传递：`monkey_patch.py` → `patch.py` → `controller.py` → `cardkit.py` → `cardkit_i18n.py`；用户在 `config.yaml` 的 `streaming.footer.fields` 中添加 `"api_calls"` / `"history_offset"` 即可启用；中英双语支持（API / 轮次）。`history_offset` 含义：值越大 → 对话历史越长，AI 已有更多上下文；值突然变小 → 发生了上下文压缩，早期对话被摘要替代 |

## v0.8.6 (2026-05-26)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | 安装后无卡片效果 | 插件 Config 读不到顶层 `streaming` 配置，`enabled` 始终为 `False` | `register()` 自动注入顶层 `streaming` 配置段 |
| 2 | Bug | 配置文件格式错误 | `footer.fields` 被序列化为二维数组格式 | `_prepare_config()` 展平为一维列表后写入 |
| 3 | Bug | 卸载后配置残留 | Hermes 的 `plugins uninstall` 只删目录不调 `unregister` | 新增 `cleanup` 命令，先清配置再卸载 |

## v0.8.5 (2026-05-26)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | 插件加载失败 | 仓库缺少根目录 `__init__.py` | 新增根目录 `__init__.py` 桥接导入 |
| 2 | Bug | 卡片内容重复 | 回调被多次包装，每段文本被处理两次 | 防重复包装守卫 `_hls_wrapped` 标记 |
| 3 | Bug | 语法异常 | `setattr` 错位缩进到 `except` 内部 | 修复缩进位置 |
| 4 | Bug | 后续消息无流式更新 | `contextvars` 不跨线程，`_set_thread_local_ctx()` 未定义 | 引入 `threading.local()` fallback |
| 5 | Bug | 重启后所有消息无流式更新 | 备份目录干扰命名空间 + `_set_thread_local_ctx()` 未定义 | 删除备份目录 + 定义 `_thread_local_ctx` + 双重保险直接 patch |
