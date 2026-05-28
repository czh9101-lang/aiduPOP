# 更新日志 / Changelog

## v0.10.0 (2026-05-28)

| # | 类型 | 功能 | 说明 |
|---|------|------|------|
| 1 | Feature | 时间注入（`streaming.inject_time`） | 开启后，每条用户消息前自动添加 `[HH:MM:SS CST] ` 时间前缀，让 AI 模型无需调用 `date` 工具即可感知当前时间 |
| 2 | Bug | `/stop` 后卡片状态显示"已完成"而非"已停止" | 检测 `result.interrupted` / `result.partial`，传入 `aborted=True` 使卡片显示 🛑 已停止 |
| 3 | Feature | 错误/中断消息在卡片正文展示 | `result.error` 和 `result.interrupt_message` 现在会以红色提示块显示在卡片正文中（而非仅页脚） |
| 4 | Feature | 页脚新增 `compression_exhausted` 字段 | 上下文压缩耗尽时显示 ⚠ 已压缩 / ⚠ Compressed，提示用户 AI 可能丢失早期对话 |
| 5 | Chore | 默认页脚字段调整 | `[status, elapsed, model, api_calls]` + `[tokens, context, history_offset, compression_exhausted]`；`show_label` 默认 `true` |

**时间注入实现原理**：
- 注入点：`_wrap_run_conversation` 和 `_apply_direct_agent_patch`（双保险）
- 格式：`[HH:MM:SS CST] <原始消息>`，例：`[14:30:05 CST] 你好`
- 时间前缀同时添加到 `user_message` 和 `persist_user_message`（若已设置），确保 DB 存储的内容与 API 收到的一致
- 双重注入防护：`threading.local()` 标志位 + `finally` 重置，防止两层 patch 同时生效时重复注入

**Prefix Cache 影响**：
- **零额外影响**。DB 存储带时间前缀的版本 → 下轮从 DB 加载的历史 = 上轮 API 收到的 → 前缀字节一致 → cache 命中率不变
- 全程开启/关闭、中途开启/关闭均无额外 cache miss

**Token 开销**：
- 每条 user message ≈ 5 tokens
- N 轮对话累计 ≈ (N-1)×5 tokens

**副作用**：
- 会话查看器（Hermes Web UI）中用户消息将显示时间前缀

**边界情况**：
- 群聊 `observed_group_context` 场景下 gateway 已设置 `persist_user_message`，时间前缀同时添加到 `persist_user_message` 避免丢失

## v0.9.0 (2026-05-27)

| # | 类型 | 问题 | 原因 | 修复 |
|---|------|------|------|------|
| 1 | Bug | 卡片内容重复显示 | `interim_assistant_callback` 和 `stream_delta_callback` 包裹同一段文本，原版有 `already_streamed` 守卫防重，monkey patch 无法访问该参数 | 去掉 `interim_assistant_callback` 的 `_thinking_wrapper` 包裹，思考内容仍由 `reasoning_callback`（原生模型推理）处理 |
| 2 | Bug | 页脚耗时(elapsed)始终不显示 | `_response_time` 是 `_handle_message_with_agent` 的局部变量，不在 `_run_agent` 返回的 `agent_result` 中，`result.get("_response_time", 0)` 永远返回 0，`duration=0` 时 `_render_footer_field` 返回 None 不渲染 | 使用 `time.monotonic()` 自计时，在消息开始时记录 `_msg_start_time`，完成时计算差值作为耗时 |
| 3 | Bug | CLI 命令 `python -m hermes_lark_streaming` 报模块找不到 | 非标准安装路径下 `hermes_lark_streaming` 不在 `sys.path` 中 | `__main__.py` 新增 `_ensure_importable()` 函数，自动搜索 HERMES_HOME/plugins、site-packages 等常见路径；各子命令添加 ImportError 容错；简化 usage 信息 |
| 4 | Bug | 卡片中超过 3 个表格时后续表格显示为 Markdown 源码 | `_MAX_CARD_TABLES = 3` 过于保守，超限表格被降级为代码块 | `_MAX_CARD_TABLES` 由 3 调整为 10，绝大多数场景不再触发降级 |
| 5 | Feature | 页脚新增 `api_calls` 和 `history_offset` 字段 | — | 全链路传递：`monkey_patch.py` → `patch.py` → `controller.py` → `cardkit.py` → `cardkit_i18n.py`；用户在 `config.yaml` 的 `streaming.footer.fields` 中添加 `"api_calls"` / `"history_offset"` 即可启用；中英双语支持（API / 轮次）。`history_offset` 含义：值越大 → 对话历史越长，AI 已有更多上下文；值突然变小 → 发生了上下文压缩，早期对话被摘要替代 |

## v0.8.6 (2026-05-26)

| # | 问题 | 原因 | 修复 |
|---|------|------|------|
| 1 | 安装后无卡片效果 | 插件 Config 读不到顶层 `streaming` 配置，`enabled` 始终为 `False` | `register()` 自动注入顶层 `streaming` 配置段 |
| 2 | 配置文件格式错误 | `footer.fields` 被序列化为二维数组格式 | `_prepare_config()` 展平为一维列表后写入 |
| 3 | 卸载后配置残留 | Hermes 的 `plugins uninstall` 只删目录不调 `unregister` | 新增 `cleanup` 命令，先清配置再卸载 |

## v0.8.5 (2026-05-26)

| # | 问题 | 原因 | 修复 |
|---|------|------|------|
| fix1 | 插件加载失败 | 仓库缺少根目录 `__init__.py` | 新增根目录 `__init__.py` 桥接导入 |
| fix2 | 卡片内容重复 | 回调被多次包装，每段文本被处理两次 | 防重复包装守卫 `_hls_wrapped` 标记 |
| fix3 | 语法异常 | `setattr` 错位缩进到 `except` 内部 | 修复缩进位置 |
| fix4 | 后续消息无流式更新 | `contextvars` 不跨线程，`_set_thread_local_ctx()` 未定义 | 引入 `threading.local()` fallback |
| fix5 | 重启后所有消息无流式更新 | 备份目录干扰命名空间 + `_set_thread_local_ctx()` 未定义 | 删除备份目录 + 定义 `_thread_local_ctx` + 双重保险直接 patch |
