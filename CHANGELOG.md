# 更新日志 / Changelog

## v0.18.1 (2026-06-08)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | GatewayRunner 补丁失败导致流式卡片完全无效 | 插件 `register()` 在 Hermes 加载 `gateway.run` 模块之前运行，`from gateway.run import GatewayRunner` 失败后直接报错放弃，导致三个关键补丁（`_handle_message`、`_handle_message_with_agent`、`_run_agent`）从未被应用。Termux 等环境下此问题100%复现——网关卡片能创建但无流式效果、无打字机、无工具面板 | 新增延迟补丁机制：当 `gateway.run` 不可用时启动后台线程，每 2 秒轮询一次，60 秒超时。一旦 `gateway.run` 可导入立即应用 GatewayRunner 补丁。补丁摘要日志从 `GatewayRunner=✗` 改为 `GatewayRunner=pending (delayed poll)`，成功后记录 `GatewayRunner patched (delayed) ✓` |
| 2 | Bug | `edit_message()` 拦截器每次调用都报 TypeError | `_intercepted_edit` 签名缺少 `chat_id` 参数，Hermes 关键字传参时 `chat_id` 掉入 `**kwargs`，fallback 调用原始函数时参数错位。异常被 `except Exception` 默默吃掉，功能不受影响但每次都刷错误日志 | `_intercepted_edit` 新增 `chat_id` 显式参数；fallback 调用 `orig_edit(self_feishu, chat_id, message_id, content, ...)` 参数对齐；网关卡片更新路径 `card_info.get("chat_id", chat_id)` 兼容旧注册数据 |
| 3 | Bug | 拆卡后封存的卡片跑马灯不停 | `build_linear_complete_card` 和 `build_complete_card` 生成的完成态卡片未设置 `streaming_mode: False`。当 `cardkit_close_streaming` API 调用失败时（被 `except Exception` 吞掉），卡片流式模式未关闭，跑马灯一直转 | 完成态卡片（schema 2.0）显式设置 `"streaming_mode": False`，即使 `close_streaming` 失败也能通过 `cardkit_update` 停止跑马灯 |
| 4 | Change | 线性模式中断面板显示在内容最前面 | `build_linear_complete_card` 中中断/错误面板添加在 segment 循环之前，占据卡片最顶部。在线性模式下，中断只是状态通知（"已停止"），不应抢占已生成内容的位置 | 线性模式中断面板移到内容之后、页脚之前；非线性格式 `build_complete_card` 保持不变（面板在顶部，默认折叠不影响阅读） |
| 5 | Change | 中断/错误面板不受 `panel_expanded` 配置控制 | `_build_error_panel` 硬编码 `expanded=True`，无论用户配置 `panel_expanded` 为 `true` 还是 `false`，中断面板始终展开 | 中断/错误面板改为 `expanded=panel_expanded`，与推理面板、工具面板行为一致，受配置控制 |
| 6 | Test | 新增 v0.18.1 修复测试 | 新功能需测试覆盖 | 新增 `test_complete_card_has_streaming_mode_false`、`test_linear_complete_card_has_streaming_mode_false`、`test_error_panel_respects_panel_expanded`、`test_linear_error_panel_respects_panel_expanded`、`test_apply_gateway_runner_patches_function_exists`、`test_intercepted_edit_has_chat_id_param`；更新 `test_error_message_before_segments` → `test_error_message_after_segments_in_linear_mode`（断言反转） |

## v0.18.0 (2026-06-07)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Docs | 插件更新命令修正 | README 中更新命令使用 `hermes plugins install`，但正确的更新命令是 `hermes plugins update` | 中英文 README 的更新章节统一改为 `hermes plugins update hermes-lark-streaming` + `hermes gateway restart`；移除关于 `install` 支持覆盖安装的说明 |
| 2 | Feature | 启动时配置诊断日志 | Termux 等纯网关卡片环境下问题排查困难，缺少关键配置值的日志 | `plugin.py:register()` 新增配置诊断日志：启动时输出 enabled、linear、gateway_cards、inject_time、print_strategy、card_ttl、footer_fields 等关键配置值，方便通过 `grep hermes_lark_streaming agent.log` 快速确认插件运行状态 |
| 3 | Feature | 网关卡片路径决策点日志 | `_wrap_feishu_adapter_send` 中网关消息走卡片还是纯文本的决策过程无日志，出问题时无法判断走了哪条路径 | 新增决策点 info 日志：① 进入网关内部路径时记录 `gateway_send: entering gateway-internal path`；② gateway_cards 关闭时记录 `gateway_cards disabled, falling back`；③ controller 未启用时记录 `controller not enabled`；④ 降级为纯文本时记录 `plain text fallback`；⑤ 网关卡片投递失败从 debug 升级为 info |
| 4 | Feature | FeishuClient 初始化诊断日志 | 飞书客户端初始化成功/失败时无日志，凭据配置错误难以定位 | `_ensure_init()` 成功时记录 `FeishuClient initialized`（含 app_id 前缀和 base_url）；失败时记录 `FeishuClient init failed: credentials not configured`（含 app_id 和 env_app_id 是否存在） |
| 5 | Chore | 网关卡片投递日志增强 | `gateway card delivered` 日志缺少内容长度信息 | 新增 `content_len=%d` 字段到 `_do_gateway_deliver` 的成功投递日志 |

## v0.17.0 (2026-06-07)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Feature | 网关卡片图片升级为 Card 2.0 结构 | `build_gateway_card()` 中的图片元素使用旧版字段 `mode: "fit_horizontal"` + `compact_width: False`，不符合 Card 2.0 规范，未来可能被废弃 | 升级为 Card 2.0 标准：`scale_type: "fit_horizontal"` + `alt` + `corner_radius: "8px"` + `preview: true`；移除旧版 `mode` 和 `compact_width` 字段 |
| 2 | Feature | 完成态卡片图片独立渲染 | AI 回复中的图片以 markdown `![alt](img_key)` 嵌入，飞书 markdown 渲染图片效果一般（不支持圆角、点击放大、裁剪控制等） | 新增 `_extract_images_from_markdown()` 函数：完成态卡片构建时提取 `![alt](img_v3_xxx)` 图片为独立 `tag: "img"` Card 2.0 元素，支持 `scale_type`、`corner_radius`、`preview` 等控制；图片从 markdown 文本中移除避免重复显示；`build_complete_card`（cardkit 模式）和 `build_linear_complete_card` 均支持；非 cardkit 模式保持 markdown 内嵌不变 |
| 3 | Change | 页脚缓存字段去掉💾 emoji | 页脚 cache 字段显示 `💾 136.3K/137.4K (99%)`，emoji 增加视觉噪音，与其他字段风格不一致 | 移除💾前缀，改为纯数字格式 `136.3K/137.4K (99%)`，与 elapsed、tokens 等字段保持一致 |
| 4 | Change | 时间注入 → 时间感知模式 | "时间注入"名称不够直观，"时间感知"更准确描述功能本质 | 所有面向用户文档统一重命名为"时间感知模式 / Time Awareness Mode"；配置项名 `inject_time` 不变（避免破坏用户配置） |
| 5 | Docs | 新增插件更新说明 | README 仅有安装和卸载说明，缺少更新步骤 | 中英文 README 新增"更新 / Update"章节：`hermes plugins install hermes-lark-streaming` + `hermes gateway restart`；Footer 配置文档中 `show_label` 移到 `fields` 前面 |
| 6 | Test | 新增图片提取和 Card 2.0 字段测试 | 新功能需测试覆盖 | 新增 `TestExtractImagesFromMarkdown`（7 个测试）：无图片、单图、多图、非 img_key、混合、空 alt、空行清理；新增 `TestCompleteCardImageExtraction`（4 个测试）：cardkit 模式提取、非 cardkit 保留、线性模式提取、多图提取；更新 `TestCacheFooterField`（5 个测试）：移除💾断言；更新 `test_image_media_element`：验证 Card 2.0 字段、排除旧版字段 |

## v0.16.0 (2026-06-03)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Feature | 流式态卡片面板展开/折叠可配置 | `panel_expanded` 只控制完成态卡片的面板状态，流式态（对话进行中）的面板展开状态写死为 True，无法配置 | 新增 `streaming_panel_expanded` 配置项（默认 `true`，保持现有行为）。设为 `false` 后，流式态的推理面板和工具面板默认折叠，用户可手动点击展开。与 `panel_expanded`（完成态面板）独立配置 |
| 2 | Feature | 流式卡片上屏策略可配置 | 飞书 CardKit 2.0 支持两种上屏策略：`fast`（新内容到达时，未上屏的旧内容立即全部上屏）和 `delay`（旧内容继续按打字机效果输出，全部完成后再开始新内容上屏，更丝滑）。之前写死为 `fast` | 新增 `print_strategy` 配置项，可选 `"fast"` 或 `"delay"`（默认 `"delay"`，更丝滑的阅读体验）。无效值自动回退为 `"delay"` |
| 3 | Change | 网关卡片头部去掉分类 emoji | 网关卡片（slash 命令回复、错误、通知等）顶部会显示分类 emoji（🔔系统、❌错误、🔐授权、🔄会话、⌨️命令），但这不是 Hermes 原生消息内容，属于插件额外添加的视觉修饰 | 移除 `_CATEGORY_ICONS` 字典和 `build_gateway_card()` 中的 emoji 头部元素。网关卡片现在只显示 Hermes 原生消息内容，无额外修饰。状态指示器（Reaction 拦截产生的 "👀 Reading" 等）仍然保留 |
| 4 | Test | 新增配置和卡片测试 | 新功能需测试覆盖 | 新增 `TestStreamingPanelExpanded`（3 个测试）、`TestPrintStrategy`（4 个测试）；新增 `test_reasoning_panel_collapsed_in_streaming`、`test_print_strategy_delay`、`test_print_strategy_fast`、`test_no_category_icon_header`、`test_status_indicator_still_works`；更新 12 个网关卡片测试（移除 emoji 头部断言） |

## v0.15.5 (2026-06-06)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | 中断场景：卡片显示"已停止"但仍出现重复纯文本消息 | `_wrap_handle_message_with_agent` 检查 `card_sent` 后，若 `card_sent=False` 但 controller 中已存在卡会话（如 ABORTED 终态），说明卡片实际已创建可见，但 `card_sent` 因上下文传播链路复杂未能正确设置。此时 Hermes 仍会通过 `FeishuAdapter.send()` 发送纯文本回复，导致卡片+纯文本重复 | `_wrap_handle_message_with_agent` 新增卡会话存在性检查：当 `card_sent=False` 但 controller 的 `_sessions` 中存在对应 `card_msg_id` 的会话时，也设置 `card_sent=True` 并返回 None 抑制 Hermes 回复 |
| 2 | Bug | `_wrap_feishu_adapter_send` 未拦截中断后的纯文本回复 | 当 `card_sent=False`（传播失败）但卡会话已存在（ABORTED 终态）时，`FeishuAdapter.send()` 的 Agent 路径不会抑制文本，导致纯文本作为独立消息发送 | `_wrap_feishu_adapter_send` 的 Agent 路径新增卡会话存在性检查：`card_sent=False` 时查询 controller 的 `_sessions`，若存在 `card_msg_id` 则设置 `card_sent=True` 并返回 `SendResult(success=True)` 抑制纯文本 |
| 3 | Bug | 递归中断父级 ABORTED COMPLETE 后 Hermes 仍发送纯文本 | `_wrap_run_agent` Step 2 父级 ABORTED COMPLETE 只设置了 `_saved_parent_ctx["card_sent"] = True` 和 `_original_msg_context_ref["card_sent"] = True`，但未设置 `result["already_sent"] = True`，Hermes 的 `_handle_message_with_agent` 仍认为文本未发送 → 发送纯文本 | `_wrap_run_agent` Step 2 新增 `result["already_sent"] = True`，确保 Hermes 的 gateway 层也跳过文本回复 |
| 4 | Perf | 日志量过大：高频回调场景下 info 级别日志过多 | `_maybe_wrap_callbacks` 的 `HLS_CALLED`、`HLS_WRAP` 守卫检查等日志使用 `_logger.info`，每次消息触发多次，在高频场景下产生大量不必要的日志输出 | 高频/低价值日志从 `_logger.info` 降级为 `_logger.debug`：`HLS_CALLED`、`HLS_WRAP` guard check、guard SKIP、递归中断日志、父级 COMPLETE hook 日志 |
| 5 | Perf | 启动延迟过长：`_schedule_direct_patch` 5 秒等待 | `_schedule_direct_patch` 使用 `time.sleep(5)` 等待 Hermes 加载完成，实际 2 秒已足够，多余的 3 秒增加了插件生效延迟 | `time.sleep(5)` → `time.sleep(2)`；日志消息同步更新 |
| 6 | Test | 新增中断卡会话存在性检查测试 | Bug 1-3 修复需测试覆盖 | 新增 `test_card_session_existence_check_in_handle_message`：验证 `_wrap_handle_message_with_agent` 存在卡会话存在性检查逻辑；新增 `test_card_session_existence_check_in_feishu_adapter_send`：验证 `_wrap_feishu_adapter_send` 存在卡会话存在性检查逻辑；新增 `test_parent_aborted_complete_sets_already_sent`：验证 Step 2 设置 `result["already_sent"] = True`；新增 `test_startup_delay_is_2s`：验证启动延迟从 5s 降为 2s |

## v0.15.4 (2026-06-05)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | 图片完全消失：卡片内没有、直接发送的也没有 | v0.15.3 新增的 `_wrap_feishu_adapter_send_image_file` / `_wrap_feishu_adapter_send_image` 拦截器存在三个根本性缺陷：① 注入 `![image](file://path)` 后被 `_strip_invalid_image_keys()` 移除（仅保留 `img_` 前缀的 URL）；② `ImageResolver._IMG_PATTERN` 仅匹配 `http(s)://` URL，不匹配 `file://`；③ `_schedule_card_update` 对终态 session 直接跳过。同时拦截成功后立即 `return SendResult(success=True)` 抑制了原独立发送，图片唯一的展示路径（卡片）又无法渲染 → 图片完全消失 | 移除 `send_image_file()` / `send_image()` 的 monkey-patching：这两个方法仅在 Agent 使用 `send_message` 工具发送 `<MEDIA>` 图片时调用，属于**独立 MEDIA 发送**，不是 AI 流式回复中的图片。AI 回复中的图片已通过 markdown → ImageResolver → 卡片渲染管线正确处理。独立 MEDIA 发送应作为独立图片消息投递（恢复 v0.15.3 前的行为）；`_wrap_feishu_adapter_send` 非字符串内容路径同步修复：移除 `card_sent=True` 时抑制图片的逻辑，确保图片始终透传 |
| 2 | Bug | 中断场景：卡片显示"已停止"但仍出现重复纯文本消息 | `_wrap_run_agent` 递归中断时创建新上下文，用 `_saved_parent_ctx = dict(ctx)` 保存父级上下文**副本**。当父级 ABORTED COMPLETE 触发后设置 `_saved_parent_ctx["card_sent"] = True`，但 `_wrap_handle_message_with_agent` 使用的是原始 `msg_context` 字典（不是副本），`card_sent` 仍为 `False` → Hermes 的文本回复未被抑制 → 重复发送纯文本 | 新增 `_original_msg_context_ref` 引用：在创建子级上下文时捕获对原始 `msg_context` 字典的引用，并在父级 ABORTED COMPLETE 时同步设置 `_original_msg_context_ref["card_sent"] = True`，确保 `_wrap_handle_message_with_agent` 能正确检测到卡片已发送，抑制重复文本。`_original_msg_context_ref` 通过上下文字典的 `_original_msg_context_ref` 键在递归层级间传播 |
| 3 | Test | 中断 card_sent 传播测试 | v0.15.4 新增 `_original_msg_context_ref` 机制 | 新增 `test_parent_card_sent_propagated_to_original_msg_context`：验证 `_wrap_run_agent` 中 `_original_msg_context_ref` 存在且 `card_sent` 被传播到原始 msg_context |

## v0.15.3 (2026-06-05)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | 中断场景：新消息卡片卡在"已停止"状态，永远不完成 | `_wrap_run_agent` 处理递归中断时，只触发了父级(A)的 ABORTED COMPLETE，**从未触发子级(B)的 COMPLETE hook**。B 的卡片会话一直停在 STREAMING 状态，没有完成更新。注释中声称"The child COMPLETE hook has already handled message B"是错误的——子级 COMPLETE 在此代码路径中从未被调用 | 重新设计 `_wrap_run_agent` 的 COMPLETE hook 逻辑：在 `_saved_parent_ctx is not None` 分支中，**先触发子级 B 的 COMPLETE hook**（使用 `result.get("final_response")` 等 B 的结果数据），**再触发父级 A 的 ABORTED COMPLETE**。确保 B 的卡片正常完成并显示回答内容，A 的卡片显示"已停止"中断状态 |
| 2 | Bug | 中断场景：额外出现一个带 🔔 的重复卡片 | B 的 COMPLETE 从未触发 → B 的 `result["already_sent"]` 未设置 → Hermes 的 `_handle_message_with_agent` 认为文本未发送 → 通过 `adapter.send()` 发送 B 的回复文本 → `_wrap_feishu_adapter_send` 拦截后创建新的 gateway 卡片（因为此时 `_msg_ctx` 已被清除或指向 A 的上下文） | 修复 Bug 1 后，B 的 COMPLETE 正确设置 `result["already_sent"] = True` 和 `ctx["card_sent"] = True`，Hermes 不再重复发送文本 |
| 3 | Bug | 中断场景：新卡片引用的是旧消息的文字 | B 的 COMPLETE 从未触发 → B 的卡片停留在 STREAMING 状态 → 卡片内容来自 B 开始前的流式文本（可能包含 A 的残留内容）；加上 `_force_rewrap` 在某些竞态条件下未及时生效 | 修复 Bug 1 后，B 的 COMPLETE 正确传入 B 的 `final_response` 作为回答文本，卡片最终内容与 B 的实际回复一致 |
| 4 | Bug | 图片仍作为纯图片消息发送，未包含在卡片内 | Hermes 通过 `FeishuAdapter.send_image_file()` 和 `send_image()` 发送图片，而非 `send()`。`_wrap_feishu_adapter_send` 只包装了 `send()`，图片发送方法未被拦截 | 新增 `_wrap_feishu_adapter_send_image_file` 和 `_wrap_feishu_adapter_send_image` 拦截器：在 Agent 管道中拦截图片发送 → 上传到飞书获取 img_key → 注入到卡片会话的 ImageResolver 缓存 + 文本中 → 触发卡片更新；不在 Agent 管道中则原样透传。`FeishuClient` 新增 `upload_local_image()` 方法用于上传本地文件。`apply_patches()` 注册图片拦截补丁 |
| 5 | Perf | 卡片中超过 10 个表格时后续表格仍被降级为代码块 | `_MAX_CARD_TABLES = 10` 在复杂场景下不够用，超限表格被降级为 Markdown 源码显示 | `_MAX_CARD_TABLES` 由 10 调整为 20，绝大多数场景不再触发降级 |
| 6 | Chore | README 版本徽章未随版本号更新 | 版本号更新时遗漏了 README.md / README.zh-CN.md 中的 shields.io badge 版本号 | 所有文档中的版本徽章统一更新至当前版本 |
| 7 | Test | 新增中断子级 COMPLETE hook 测试 + 图片拦截测试 | Bug 1-4 的修复需测试覆盖 | 新增 `test_child_complete_hook_fired_before_parent_aborted`：验证子级 COMPLETE 在父级 ABORTED 之前触发；新增 `test_child_complete_includes_result`：验证子级 COMPLETE 使用 B 的结果数据；新增 `TestImageInterception`（4 个测试）：验证 `send_image_file` / `send_image` 拦截器存在且检查 Agent 上下文 |

## v0.15.2 (2026-06-04)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | 网关卡片发送全部失败：Feishu API 230099/200621 `plain_text` 不支持 | `build_gateway_card()` 将 `plain_text` 元素直接放入 CardKit 2.0 `body.elements`，但飞书 CardKit 2.0 schema 中 `plain_text` 不是 `body.elements` 的合法直接子元素，必须包裹在 `div.text` 中 | 将所有 `plain_text` 直接子元素改为 `div.text.plain_text` 结构：① 分类图标头部 ② 状态指示器 ③ 文件链接元素 |
| 2 | Bug | `edit_message()` 报 `unexpected keyword argument 'metadata'` | Hermes StreamConsumer 调用 `edit_message()` 时传入 `metadata` 参数，但原始 `FeishuAdapter.edit_message()` 不接受该参数；插件 fallback 时原样透传 `metadata=metadata` 导致 TypeError | `_wrap_feishu_adapter_edit` 的 fallback 路径移除 `metadata` 参数：先尝试不带 `metadata` 调用原始方法，若仍失败则去掉所有额外 kwargs |
| 3 | Bug | `TypeError: 'NoneType' object is not subscriptable` | `_wrap_run_agent` 中 `ctx.get("message_id", "?")[:12]` 当 `message_id` 键存在但值为 `None` 时，`get()` 返回 `None` 而非默认值 `"?"`（自动恢复会话无 message_id 触发） | 全部改为 `(ctx.get("message_id") or "?")[:12]` 和 `(ctx["message_id"] or "?")[:12]`，防御 `None` 值 |
| 4 | Bug | 中断场景：旧卡片停留在跑马灯状态，未标记"已中断" | `on_interrupted` 仅在 `_wrap_handle_message_with_agent` 返回时检测触发，但递归中断场景中 `_wrap_run_agent` 已创建了新上下文，旧卡片的 ABORTED 完成要等到父级 COMPLETE hook 才触发，与子级 COMPLETE hook 竞态导致旧卡片被 idempotent 跳过 | `_wrap_run_agent` 检测递归中断时立即触发 `on_interrupted(old_msg, new_msg)`，在子级开始处理前就将旧卡片标记为 ABORTED + 触发 `_complete_session`；`on_interrupted` 新增 `_was_aborted = True` + `error_message = "Interrupted by new message"` 确保卡片显示中断面板 |
| 5 | Bug | 中断场景：新卡片引用的是第一条消息的文字，而非第二条 | 递归中断时 agent 对象被复用，`_maybe_wrap_callbacks` 的防重复包装守卫检测到 `stream_delta_callback` 已有 `_hls_wrapper` 标记就跳过，导致回调闭包仍捕获旧 `eid`，新消息的流式文本写入旧卡片会话 | 新增 `_force_rewrap` 标志：递归中断时在上下文中设置 `_force_rewrap=True`；`_maybe_wrap_callbacks` 检测到该标志时强制重新包装回调（更新 `eid` 闭包），确保新消息的文本写入新卡片会话；包装完成后自动清除标志 |
| 6 | Bug | 图片发送为纯图片消息，未包含在卡片中 | `_wrap_feishu_adapter_send` 对非字符串内容（如图片 dict）直接 `return await orig_send()`，在 Agent 管道中图片绕过卡片系统作为独立消息发送 | 新增 `_try_add_image_to_session()` 辅助函数：当 Agent 管道中发送图片时，尝试将 `image_key` 注入卡片会话的 `ImageResolver` 缓存并触发卡片更新；若卡片已发送则抑制独立图片消息（卡片完成时会包含引用的图片） |

## v0.15.1 (2026-06-03)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | 网关内部消息（/status、/help、错误等）仍为纯文本，未转为卡片 | `_msg_ctx` ContextVar 在消息处理完成后从未清除，残留的 `event_message_id` + `card_sent=True` 导致后续 `FeishuAdapter.send()` 调用进入"Agent 抑制路径"，网关内部消息被静默丢弃而非转为卡片 | `_wrap_handle_message_with_agent` 在消息处理完成后（return 前）清除 `_msg_ctx` 和 `_thread_local_ctx`，防止残留上下文泄漏到后续非 Agent 消息 |
| 2 | Bug | 媒体消息卡片包装未生效 | 同上——`_msg_ctx` 泄漏导致 `FeishuAdapter.send()` 的媒体消息也被 Agent 抑制路径丢弃 | 同上——清除 `_msg_ctx` 后媒体消息可正确进入"Gateway-internal path"并被转为包含媒体元素的卡片 |
| 3 | Bug | 用户发送第二条消息打断第一条时，第二条消息被静默忽略 | Hermes 中断消息后通过递归 `_run_agent(_interrupt_depth+1)` 处理新消息，但插件未为新消息创建独立上下文——`_msg_ctx` 仍指向旧消息的上下文字典，导致：① 新消息的回调写入旧消息的卡片；② COMPLETE hook 使用旧 message_id 处理新消息的结果；③ 新消息的卡片会话从未被创建 | `_wrap_run_agent` 检测递归调用（`_interrupt_depth > 0` 且 `event_message_id` 变化）：为递归消息创建全新上下文字典 + 触发 `on_message_started` 创建新卡片会话；保存父级上下文副本，在子级 COMPLETE hook 完成后恢复父级上下文；父级 COMPLETE hook 检测递归场景时以 `aborted=True` 完成旧消息卡片（标记为"已中断"），避免用新消息结果错误完成旧卡片 |
| 4 | Bug | 并发/重叠消息的 `card_sent` 状态误判 | `_wrap_handle_message_with_agent` 通过 `_msg_ctx.get()` 读取 `card_sent`，但当新消息覆盖了 `_msg_ctx` 后，旧消息的返回检查读到了新消息的上下文 | 使用每消息独立 `msg_context` 字典代替 `_msg_ctx.get()`，确保每条消息的 `card_sent` 判断不受并发消息干扰 |

## v0.15.0 (2026-06-02)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Feature | `edit_message()` 拦截 — 网关卡片可编辑 | Phase 1 发送网关卡片后，Hermes 后续调用 `edit_message()` 更新内容时找不到原始纯文本消息（已被卡片替代），导致更新失败或行为异常 | 新增网关卡片注册表 `_gateway_cards`：`send()` 发送卡片后注册 `card_msg_id → {chat_id, card_id, category}`；`edit_message()` 拦截器查询注册表，若命中则调用 `_do_gateway_card_update()` 更新卡片内容（IM PATCH 模式），而非尝试编辑不存在的纯文本消息；新增 `_do_gateway_card_update()` 方法（controller_mixin.py）：支持 CardKit 和 IM PATCH 两种更新路径 |
| 2 | Feature | Reaction → 卡片状态指示器 | Hermes 通过 emoji reaction（👀/👍/🤔）表示消息处理状态，但在卡片模式下 reaction 不可见或视觉不一致 | 新增 `add_reaction()` / `delete_reaction()` 拦截器：当 reaction 目标是网关卡片时，将 emoji 转为卡片内状态指示器（如 "👀 Reading"、"⏳ Processing"），而非在用户消息上添加 reaction；新增 `_REACTION_STATUS_MAP` 映射表：7 种常见 emoji → 状态标签；新增 `_do_gateway_card_status()` 方法（controller_mixin.py）：更新卡片状态指示器；`build_gateway_card()` 新增 `status_label` / `status_emoji` 参数：当有活动状态时，替换分类图标头部 |
| 3 | Feature | 媒体消息卡片包装 | Hermes 发送的图片/文件消息（`<MEDIA>` 标签）在 Phase 1 中被提取 media 后仅保留文本，图片丢失 | `_wrap_feishu_adapter_send` 新增媒体感知：提取 MEDIA 标签后保留 `_media_parts`，传递给 `_do_gateway_deliver()`；`build_gateway_card()` 新增 `media_parts` 参数：图片渲染为 `img` 元素，文件渲染为 📎 链接文本；`_do_gateway_deliver()` 新增 `media_parts` 参数透传 |
| 4 | Chore | `apply_patches()` 新增 reaction 补丁 | 无法确认 reaction 拦截是否成功应用 | 补丁日志新增 `add_reaction` / `delete_reaction` 补丁状态；汇总日志更新为 `FeishuAdapter=send/edit/reaction` |
| 5 | Test | 新增 Phase 2-4 测试 | 新功能需测试覆盖 | 新增 `TestBuildGatewayCardStatusIndicator`（4 个测试）：状态指示器渲染、空状态回退；新增 `TestBuildGatewayCardMedia`（5 个测试）：图片/文件元素、多 media、空 media；新增 `TestGatewayCardRegistry`（3 个测试）：注册/查询/删除/空 ID；新增 `TestReactionStatusMap`（3 个测试）：映射存在性、常见 emoji、值类型 |

## v0.14.0 (2026-06-01)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Feature | 飞书渠道网关内部消息（slash 命令、错误、通知等）全部转为卡片 | 之前仅 AI 回复和 Cron 消息使用卡片，其他消息（授权/配对、会话生命周期、忙碌确认、网关启停、slash 命令回复、Provider 错误、压缩警告等）均为纯文本，视觉不统一 | 新增 `FeishuAdapter.send()` 拦截层：在类级别 monkey-patch `FeishuAdapter.send()` 和 `edit_message()`，对所有非 Agent 路径的文本消息自动转为 CardKit 卡片；Agent 路径通过 `_msg_ctx` 检测自动跳过（避免与现有 consumed 机制冲突）；Cron/Background 临时 send 替换通过 `_hls_cron_sending` / `_hls_bg_sending` 实例标志安全共存 |
| 2 | Feature | 网关消息卡片分类图标 | 网关消息类型多样，需要视觉区分 | 新增 `build_gateway_card()` 卡片构建器，5 种分类图标：🔔 system、❌ error、🔐 auth、🔄 session、⌨️ slash；新增 `_classify_gateway_message()` 内容分类器，基于关键词自动识别消息类别 |
| 3 | Feature | `streaming.gateway_cards` 配置开关 | 全量接管是重大变更，用户可能希望逐步切换 | 新增 `gateway_cards` 配置项（默认 `true`），设为 `false` 可关闭网关消息卡片化，仅保留 AI 回复和 Cron 卡片 |
| 4 | Chore | `apply_patches()` 日志新增 FeishuAdapter 补丁状态 | 无法确认 FeishuAdapter 拦截层是否成功应用 | 补丁汇总日志新增 `FeishuAdapter=✓/✗` 字段 |

## v0.13.0 (2026-05-31)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | Cron 推送消息仍为纯文本，不是卡片效果 | `_card_sending_send` 内部使用 `run_coroutine_threadsafe + future.result(timeout=30)` 调度卡片投递，由于 `_card_sending_send` 本身已在事件循环上运行，`future.result()` 阻塞事件循环导致 30 秒死锁超时，每次都降级为纯文本 | 改为直接 `await ctrl._do_cron_deliver(...)`：`_card_sending_send` 已在事件循环上被 `safe_schedule_threadsafe` 调度，直接 `await` 即可，无需 `run_coroutine_threadsafe`；新增 `_do_cron_deliver` 诊断日志（chat_id、content_len）；新增 `_card_sending_send` 诊断日志（ctrl.enabled、chat_id、content_len） |
| 2 | Bug | 飞书重复消息：卡片 + 纯文本同时出现 | `_thinking_wrapper` 无条件调用 `_orig_interim()`，当卡片已消费文字时，原始回调仍触发 `_stream_consumer` 发送纯文本 | `_thinking_wrapper` 检查 `on_thinking_delta` 返回值：卡片消费文字时 `return`（不调原始回调）；文字已被 `stream_delta_callback` 消费时 dedup 跳过；仅在卡片未消费时才调原始回调作为降级（与 `_answer_wrapper` 逻辑一致） |
| 3 | Chore | 日志无法确认消息来自哪个版本 | `register()`、`apply_patches()` 等关键日志未包含版本号 | `plugin.py` 的 `register()` 日志加入 `v{__version__}`；`monkey_patch.py` 的 `apply_patches()` 启动日志和汇总日志加入版本号；cron 投递日志加入版本号；新增 `__version__` 导入 |
| 4 | Chore | `content_filter` 等异常 `finish_reason` 无诊断日志 | 模型 API 返回 `finish_reason=content_filter` 时 AI 回复为空，但插件无任何日志记录此异常，排查困难 | `_wrap_run_agent` 的 COMPLETE hook 新增诊断日志：非 `stop` 的 `finish_reason` 记录 WARNING 日志（含版本号、finish_reason、model、msg_id）；agent error 记录 WARNING 日志（含版本号、错误信息、model、msg_id） |
| 5 | Test | 新增版本日志、cron 投递、`__version__` 导入测试 | 新功能需测试覆盖 | 新增 `TestVersionLogging`（3 个测试）：验证 `__version__` 可用、`register()` 日志包含版本号、`monkey_patch` 模块导入版本号；新增 `TestCronDeliveryWrapper`（2 个测试）：验证无 adapters 降级、无飞书 adapter 降级 |

## v0.12.4 (2026-05-29)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Chore | 默认页脚字段精简，`cache` 移出默认列表 | `cache` 字段对多数用户意义不大，默认显示增加视觉噪音 | 默认页脚从 `[status, elapsed, model, cache, compression_exhausted]` 精简为 `[status, elapsed, model, compression_exhausted]`；`cache` 仍可在 config.yaml 手动添加；`show_label` 默认改为 `false`（更简洁） |
| 2 | Chore | 状态文字去掉 emoji（✅❌🛑），只用纯文字 | emoji 在部分飞书客户端/系统上显示不一致或无法渲染 | `status_completed` 从 `✅ Completed` 改为 `Completed`；`status_error` 从 `❌ Error` 改为 `Error`；`status_stopped` 从 `🛑 Stopped` 改为 `Stopped`；错误/中断面板标题同步去掉 emoji |
| 3 | Chore | `show_label` 在用户 `config.yaml` 顶层出现是否由插件导致 | 有用户反馈 `show_label` 同时出现在 `streaming` 顶层和 `streaming.footer` 两处 | 确认插件只写入 `streaming.footer.show_label`，不会向 `streaming` 顶层写入 `show_label`；顶层出现的 `show_label` 可能是用户手动配置或其他用途，插件不做迁移/清理，避免影响用户自定义配置 |
| 4 | Docs | README 致谢新增贡献者 | — | 新增 [joshcheng820222](https://github.com/joshcheng820222)（多 Profile 部署修复贡献） |
| 5 | Chore | `test_version.py` 每次版本更新都需手动改版本号 | 测试文件中版本号硬编码，版本更新时容易遗漏 | 版本号改为从唯一真源 `plugin.yaml` 动态读取，测试无需随版本更新而修改 |

## v0.12.3 (2026-05-29)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | GitHub Actions CI 测试失败：async 测试函数无法运行 | `pyproject.toml` 的 `dev` 依赖缺少 `pytest-asyncio`，CI 环境安装 `pytest` 后无法识别 `async def` 测试函数 | 添加 `pytest-asyncio>=0.21.0` 到 `dev` 可选依赖；添加 `asyncio_mode = "auto"` 到 pytest 配置，自动发现 async 测试 |
| 2 | Bug | 多 Profile 部署场景下流式卡片不工作 | `config.py` 和 `plugin.py` 中的 `_HERMES_CONFIG_PATH` 在模块导入时读取 `HERMES_HOME`，但多 Profile 场景下 `HERMES_HOME` 在插件导入后才被 `_apply_profile_override()` 设置，导致路径错误 | [@joshcheng820222](https://github.com/joshcheng820222) 新增 `_get_hermes_config_path()` 函数：每次调用时动态读取 `HERMES_HOME` 环境变量，确保始终使用正确的配置路径；`Config._load()`、`Config._reload_cached()`、`_backup_config()`、`_ensure_streaming_config()`、`_cleanup_config()` 均改用动态路径；保留 `_HERMES_CONFIG_PATH` 常量用于向后兼容 |

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
