# CHANGELOG Archive — Pre-1.0.0

> 归档版本记录。详细内容仅作历史参考，不维护更新。

---

## v0.19.1 (2026-06-08)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Feature | 保留式封卡（Preservative Seal） | 封卡时 `build_linear_complete_card` 调用 `_split_long_text()`（每2400字符一块）+ `_extract_images_from_markdown()`（每张图2个嵌套元素），导致1个流式streaming元素爆炸成N+2M个元素，超过飞书200元素限制，封卡失败 | 新增 `_preservative_seal()` 方法：`close_streaming` + `batch_update` 增量更新（删loading、加partial指示器/footer），保留已有streaming元素不动，避免元素爆炸。所有封卡场景（拆卡封存 `_do_linear_split` + 完成封存 `_do_linear_complete_inner`）统一先尝试保留式封卡，失败后降级为全量重建（含渐进降级 compact seal → minimal seal） |
| 2 | Feature | Clarify 交互卡片三态设计 | 原 Clarify 卡片只有两态（待选择 + 已确认），选择后网络丢包导致 hermes 未收到回答时卡片死锁，用户无法重试 | 重构为三态卡片：① **待选择态**（`helpdesk_outlined` 图标）：markdown 全量展示选项 A. B. C. + `select_static` 快速选择下拉框（无"其他"选项）+ `input` 自定义输入框（始终显示，支持 Enter + 按钮提交）；② **已提交态**（`lock_outlined` 图标，软锁定）：显示用户选择内容 + "已提交，等待确认..." + 「重试提交」按钮（CallBackCard 即时返回，重试重新发送同一选择而非重新选择）；③ **已确认态**（`resolve_filled` 图标，硬锁定）：显示用户选择内容 + "已确认" + 无操作按钮（服务端 `update_card` 更新）。移除 `build_clarify_resolved_card()` 和 `build_clarify_awaiting_input_card()`，新增 `build_clarify_submitted_card()` 和 `build_clarify_confirmed_card()`；移除 `selected_option == "other"` 分支和 `mark_awaiting_text` 调用；新增 `_clarify_answers` / `_clarify_card_info` 存储；新增 `_schedule_clarify_resolve_and_confirm()` 统一处理 resolve + 服务端确认更新 |
| 3 | Bug | `streaming_panel_expanded` 默认值不一致 | `config.py` 属性默认值为 `False`，但 `plugin.py` 中 `_DEFAULT_STREAMING_CONFIG` 写入 config.yaml 的初始值为 `True`，导致重新安装后流式态卡片面板默认展开 | 修改 `plugin.py` 第57行 `_DEFAULT_STREAMING_CONFIG` 中 `streaming_panel_expanded: True` → `streaming_panel_expanded: False` |
| 4 | Bug | CHANGELOG/SKILL.md 日期错误 | 多个版本的发布日期标注为6月9日-12日等未来日期 | 修正 v0.18.2/v0.18.3/v0.18.4 的日期为6月8日 |
| 5 | Bug | 流式卡片空转43秒无内容 | AI 加载上下文期间（最长数十秒），卡片创建后只有跑马灯，用户看不到任何内容提示，以为无响应 | 新增上下文加载占位提示：首卡创建后立即 `batch_update` 插入 `⏳ 正在加载上下文...`（`time_outlined` 图标 + i18n 双语），首字即显时在同一批 `batch_update` 中自动删除占位提示（零额外 API 开销）；拆卡新卡不插入占位提示（`_loading_hint_removed` 标志控制）；封卡时兜底删除占位提示。新增 `_loading_hint_element()` / `_LOADING_HINT_ELEMENT_ID` / `_loading_hint_removed` 字段 / `loading_context` i18n 条目 |
| 6 | Bug | 拆卡封卡失败——第二张卡跑马灯不停、无 footer | `_do_linear_split` 先建新卡再封旧卡，并发操作导致 sequence conflict（300317）被误判为幂等成功（`return True`），`close_streaming` + `batch_update` 静默失败 | ① 调整 `_do_linear_split()` 执行顺序：**先封旧卡再建新卡**，消除并发冲突；② 修复 `_preservative_seal()` 的 sequence conflict 处理：不再直接 `return True`，改为重试（最多 2 次，每次递增 sequence），重试全部失败才降级为全量重建 |
| 7 | Bug | Clarify 卡片回调无事件循环时 `resolve_gateway_clarify` 不调用 | `_handle_clarify_card_action` 的 retry/select/input/button 路径在 `adapter._loop is None` 时跳过 resolve 调用，导致 Clarify 回调在无事件循环环境下无法完成 resolve | 为所有四个回调路径（retry_submit、select、input_submit、button_submit）新增 `else` 分支：当 `loop is None` 时同步调用 `resolve_gateway_clarify`，确保无事件循环时 Clarify 仍能正常完成 |
| 8 | Chore | 飞书通知推送缺少版本号 | `scripts/notify_feishu.py` 推送的飞书卡片没有版本号信息，无法区分是哪个版本的推送 | 从 `plugin.yaml` 动态读取版本号，在飞书通知卡片首行显示 `**版本**: v0.19.1` |
| 9 | Fix | `delete_element` → `delete_elements` API 迁移 | 飞书 Card 2.0 更新元素删除 API，`delete_element` 需传入 `element_ids` 数组而非 `element_id` 单值 | ① `cardkit.py` 保留式封卡占位提示/加载图标删除改为 `delete_elements` + `element_ids`；② `controller_linear_mixin.py` 首字即显加载提示删除改为 `delete_elements` + `element_ids` |
| 10 | Fix | 加载占位提示仅等 answer 才删除，reasoning/tool 期间不删 | 首段是 reasoning 或 tool 时加载提示一直留在卡片上，用户看到"⏳ 加载上下文"与推理内容同时存在，认知冲突 | `controller_linear_mixin.py` 条件从 `seg.type == "answer"` 放宽为 `seg.type in ("reasoning", "tool", "answer")`，任何内容段首次出现即删除加载提示 |
| 11 | Fix | `monkey_patch.py` 引用已删除的 `build_clarify_resolved_card` | Clarify 三态重构移除了 `build_clarify_resolved_card()`，`_schedule_confirm_card` 仍引用该函数名导致 `ImportError` | `monkey_patch.py` 改为导入 `build_clarify_confirmed_card`，移除不存在的 `choices` 参数 |
| 12 | Chore | Clarify 交互卡片清理 — emoji 移除 + 图标替换 | Clarify 已确认态残留 `✓` emoji；待选择态使用 `helpdesk_outlined`（耳机图标），语义不匹配提问场景 | ① `build_clarify_confirmed_card` 移除 `✓ emoji`，纯文本显示 Confirmed/已确认；② `build_clarify_card` 待选择态图标 `helpdesk_outlined` → `info_outlined`（蓝色 ℹ️） |
| 13 | Bug | 拆卡封卡 300305 元素超限渐进降级未触发 — 旧卡 seal 失败 | `cardkit_update` 返回 `code=300305` 直报，代码只检查 `230099 + ErrCode:11310` 格式，条件不匹配，渐进降级永不执行，旧卡永留 loading 动画 | ① 新增 `CARDKIT_ELEMENT_LIMIT_DIRECT = 300305` 常量 + `is_element_limit_error()` 辅助函数，同时匹配两种错误格式；② 更新 `_do_linear_split` / `_do_linear_complete_inner` / `_handle_linear_flush_error_async` / `controller_mixin` 共 4 处元素超限检查，全部改用 `is_element_limit_error()` |

## v0.19.0 (2026-06-08)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Perf | 首字即显（First-Token Immediate Flush） | 流式回答首字到达时仍需等待 500ms 节流间隔才刷新卡片，用户感知延迟 0~500ms | `_schedule_linear_flush()` 检测首次内容（`element_count <= 1` + dirty segments），跳过节流直接调用 `flush.flush_now()` 立即刷新；CardSession 新增 `_first_flush_done` 标志，首次后恢复正常节流 |
| 2 | Perf | 完成后释放重数据（Post-Completion Memory Release） | 会话完成后 `linear_state`、`text`、`tool_use` 等重数据仍留在内存中，TTL 期间（默认 600s）持续占用；高并发场景下累积可达数百 MB | 新增 `_release_session_data()` 方法：完成后清空 `linear_state`、`text`、`tool_use`、`reasoning_text`、`footer` 等重数据，仅保留 `message_id`/`state`/`created_at` 等元数据供 TTL 追踪；在 `_do_linear_complete` 和 `_do_complete` 的 finally 块中调用（`_cleanup` 之前） |
| 3 | Perf | 性能指标采集（Performance Telemetry） | 关键路径（卡片创建、flush、stream_element、complete）无计时日志，性能瓶颈无法定位 | 在 `controller_linear_mixin.py` 的 `_do_create_linear_card`/`_do_linear_flush`/`stream_element` 调用/`_do_linear_complete_inner` 中添加 `time.monotonic()` 计时 + `debug` 级别日志；在 `feishu.py` 的 `cardkit_create`/`cardkit_stream_element`/`cardkit_batch_update` 中添加 API 调用计时；在 `controller.py` 的 `on_answer()` 中记录首字到达时间 `_first_answer_time` + TTFB 日志 |
| 4 | Perf | stream_element 异步优化 | `cardkit_stream_element` 使用 `asyncio.to_thread` 包装同步 SDK 调用，增加线程切换开销；lark-oapi 新版已提供 `acontent` 异步方法 | `FeishuClient.__init__` 中探测 `card_element.acontent` 是否存在，缓存为 `_use_async_stream_element`；`cardkit_stream_element` 优先使用原生异步方法，回退到 `asyncio.to_thread` |

## v0.18.4 (2026-06-08)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Feature | CardKit 流式刷新间隔可配置 | `CARDKIT_MS` 硬编码为 100ms，无法根据实际场景调整。100ms 的高频刷新在手机端可能导致飞书客户端卡顿甚至闪退（300~600 次重渲染） | ① `CARDKIT_MS` 默认值从 0.100 改为 0.500（500ms）；② 新增 `streaming.flush_interval_ms` 配置项（默认 500，范围 100~2000ms）；③ `controller_linear_mixin.py` 和 `controller_mixin.py` 改为从配置读取刷新间隔；④ `plugin.py` 默认配置和诊断日志同步更新 |
| 2 | Feature | 拆卡封卡"内容未完"状态显示 | 拆卡后封存的旧卡片无任何提示告诉用户"下面还有内容"，用户可能以为回复被截断 | ① 新增 `partial_continues` i18n 条目；② `build_linear_complete_card` 和 `build_linear_compact_seal_card` 新增 `partial` 参数；③ 拆卡封存时传入 `partial=True`，卡片底部显示 `▸ 内容未完，继续在下一条消息 ↩`；④ 最终完成卡片不传 `partial`，无额外提示 |
| 3 | Feature | 后台审查进度消息放入卡片 | `background_review` 消息（如"检查回复质量"、"更新记忆"）以纯文本发送到聊天，与卡片内容割裂，视觉不统一 | ① 新增 `_build_background_review_panel()` 构建可折叠审查面板；② `LinearState` 新增 `bg_review_messages` / `bg_review_panel_id` / `bg_review_panel_added` 属性；③ 线性模式下审查消息实时推入卡片面板（`defer_background_review` 返回 True 抑制纯文本）；④ 非线性模式仍走原暂存逻辑；⑤ 完成态卡片包含审查面板 |
| 4 | Test | 新增配置、面板测试 | 新功能需测试覆盖 | ① 新增 `test_flush_interval_ms_default`、`test_flush_interval_sec_default`、`test_flush_interval_ms_custom`、`test_flush_interval_ms_clamped`；② 新增 `TestPartialStatusIndicator`（3 个测试）；③ 新增 `TestBackgroundReviewPanel`（3 个测试）；④ 修复 `test_first_call_schedules_delayed_flush` 和 `test_enables_flushing` 适配 500ms 默认值 |

---

### v0.18.x

## v0.18.3 (2026-06-08)

- message_id NoneType 下标崩溃修复（36处防护）、封卡 300305 元素超限渐进降级（compact seal → minimal seal）

## v0.18.2 (2026-06-08)

- 拆卡阈值修正（180→150）、answer 图片元素计数

## v0.18.1 (2026-06-08)

- 更新命令修正、配置/决策点/初始化诊断日志增强

---

### v0.17.x

## v0.17.0 (2026-06-07)

- 网关卡片图片 Card 2.0 升级、完成态图片独立渲染（`_extract_images_from_markdown`）
- 页脚缓存去💾 emoji、时间注入→时间感知重命名

---

### v0.16.x

## v0.16.0 (2026-06-03)

- 流式态面板展开/折叠可配置（`streaming_panel_expanded`）、上屏策略可配置（`print_strategy`: fast/delay）
- 网关卡片去分类 emoji

---

### v0.15.x

## v0.15.5 (2026-06-06)

- 中断 card_sent 传播全链路修复：卡会话存在性检查 + `_original_msg_context_ref` + `already_sent`（覆盖 v0.15.1~0.15.4 中断重复文本问题）
- 高频日志 info→debug、启动延迟 5s→2s

## v0.15.4 (2026-06-05)

- 图片独立 MEDIA 发送恢复不拦截（v0.15.3 图片拦截器因 `file://` 不匹配等缺陷回退）

## v0.15.3 (2026-06-05)

- 中断子级 COMPLETE hook 重设计（先触发子级 B 再父级 A ABORTED）、表格上限 10→20

## v0.15.2 (2026-06-04)

- 网关卡片 `plain_text`→`div.text` schema 修复、`edit_message` metadata 兼容
- 中断跑马灯修复（`on_interrupted` 立即触发）、`_force_rewrap` 回调重绑

## v0.15.1 (2026-06-03)

- `_msg_ctx` 泄漏修复（处理后清除，防网关/媒体消息被丢弃）
- 递归中断独立上下文 + `card_sent` 独立字典

## v0.15.0 (2026-06-02)

- 网关卡片可编辑（`edit_message` 拦截 + 注册表）、Reaction→状态指示器、媒体消息卡片包装

---

### v0.14.x

## v0.14.0 (2026-06-01)

- 网关内部消息全部转卡片（`FeishuAdapter.send` 拦截）、`gateway_cards` 配置开关

---

### v0.13.x

## v0.13.0 (2026-05-31)

- Cron 推送死锁修复（直接 await）、`_thinking_wrapper` 重复文本修复
- 版本号日志、`content_filter` 异常 finish_reason 诊断

---

### v0.12.x

## v0.12.4 (2026-05-29)

- 默认页脚精简（cache 移出）、状态文字去 emoji、版本号动态读取

## v0.12.3 (2026-05-29)

- CI async 测试修复（`pytest-asyncio`）、多 Profile 部署路径修复

## v0.12.2 (2026-05-29)

- Answer 估算修正、answer 内部拆分（`split_answer_segment`）、相邻 answer 拆卡触发

## v0.12.0 (2026-05-29)

- Cron 推送卡片补丁签名修复、`/background` 后台任务卡片化、页脚 cache 字段

---

### v0.11.x

## v0.11.0 (2026-05-29)

- 元素超限自动拆卡 + 降级、配置缓存 TTL 5s、并发消息锁保护、`on_completed` 双调竞态修复（COMPLETING 状态 + 300317 幂等）

---

### v0.10.x

## v0.10.2 (2026-05-28)

- 时间注入格式改为 XML 标签（`<time>`）、预填充段冗余刷新优化

## v0.10.1 (2026-05-28)

- `call_soon`→`call_soon_threadsafe` 修复跑马灯无文字、`batch_update` 预填充省首次 API 调用

## v0.10.0 (2026-05-28)

- 时间注入（`inject_time`）、`/stop` 状态修正（已停止）、错误/中断面板展示、配置自动备份
- Apple Silicon `agent` 模块解析、Cron 全链路 async、表格降级补全

---

### v0.9.x

## v0.9.0 (2026-05-27)

- 卡片内容去重（去 `interim_assistant_callback` 包裹）、页脚耗时自计时
- CLI 模块路径修复、表格上限 3→10、页脚新增 `api_calls`/`history_offset`

---

### v0.8.x

## v0.8.6 (2026-05-26)

- 安装后自动注入 `streaming` 配置、`footer.fields` 展平、卸载配置清理

## v0.8.5 (2026-05-26)

- 根目录 `__init__.py` 桥接、回调去重守卫、`setattr` 缩进修复
- `threading.local()` fallback、备份目录干扰修复
