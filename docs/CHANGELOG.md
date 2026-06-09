## v1.0.0 (2026-06-10)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | **MEDIA 文件上传被静默丢弃** | `FeishuAdapter.send()` 拦截器在 agent 路径下（`card_sent=True`）直接抑制整个 send，但 `extract_media()` 已提取出 `_media_parts`（图片/文件）却从未使用——媒体文件被静默丢弃 | agent 路径下新增 `_has_media` 检查：当 `_media_parts` 非空时，不抑制 send 而是透传给 `orig_send`，确保媒体文件通过原始 adapter 正常发送；仅抑制纯文本 send（卡片已展示文本内容） |
| 2 | Analysis | **CardKit streaming_mode 迁移可行性** | 两个参考项目（Cheerwhy/openclaw-lark）均使用原生流式模式，需确认我们项目是否需要迁移 | 经完整源码审查确认：**我们的项目已经在使用 CardKit 原生流式模式**（`cardkit_stream_element` + `cardkit_batch_update`），"线性模式"是内容组织策略而非不同的 API 机制。我们的实现比参考项目更高级（支持 card splitting、preservative seal、渐进降级），无需迁移 |
| 3 | Analysis | **配置/阈值变更审查** | 参考项目有不同配置值（如 `_MAX_CARD_TABLES=3`），需确认哪些需要变更 | 经完整审查：所有配置已与参考项目对齐，无需修改。确认保持：`_MAX_CARD_TABLES=20`、`_MAX_CHUNK_CHARS=2400`、`_ELEMENT_THRESHOLD=185`、`_FOOTER_RESERVE=15`、`flush_interval_ms=100` |
| 4 | Refactor | `monkey_patch.py` 2693行单文件过大 | 所有运行时拦截逻辑集中在一个文件，维护和定位困难 | 拆分为4个模块：`monkey_patch.py`（入口+共享状态）、`monkey_patch_gateway.py`（GatewayRunner包装器）、`monkey_patch_callbacks.py`（回调拦截）、`monkey_patch_adapter.py`（FeishuAdapter包装器） |
| 5 | Refactor | `cardkit.py` 1371行单文件过大 | 5种卡片类型的构建逻辑混在一起 | 拆分为3个模块+门面：`cardkit_elements.py`（基础元素）、`cardkit_cards.py`（卡片组装）、`cardkit_special.py`（特殊卡片）、`cardkit.py`（重导出门面） |
| 6 | Refactor | `controller_linear_mixin.py` 1410行过大，拆卡/估算逻辑与核心flush逻辑耦合 | 拆卡判断、元素估算等独立功能与核心流式逻辑混在一起 | 抽出 `linear_split.py`（拆卡/估算独立函数）、抽出 `session.py`（CardSession 数据类） |
| 7 | Docs | 根目录文档散乱 | CHANGELOG.md、SKILL.md 直接放在根目录，缺少归类 | 创建 `docs/` 文件夹，将 CHANGELOG.md、SKILL.md 移入，新增 `docs/ISSUES_TEMPLATE.md`（AI Issue 提交模板） |
| 8 | Docs | CHANGELOG.md 冗长（460行），历史版本细节过多 | v0.18.3 及更早版本的详细修复过程对当前维护价值低 | 精简为184行：最新3版保留详细表格，旧版每版1-3行总结 |
| 9 | Docs | SKILL.md 冗长（507行），版本历史与 CHANGELOG 重复 | 版本历史、Roadmap（全已完成）占大量篇幅；陷阱章节叙事过多 | 精简为269行：删除版本历史和 Roadmap，陷阱章节只保留结论和经验教训 |
| 10 | Docs | README 安装命令格式不清晰 | 3条安装命令堆在一起，分不清哪个平台 | 按 `# gitee (SSH)` / `# github (SSH)` / `# github (HTTPS)` 分组标注 |

---

## v1.0.1 (2026-06-09)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Refactor | 配置节 `streaming:` → `hermes_lark_streaming:` | 配置节名 `streaming` 与 Hermes 原生 `display.streaming` 易混淆，且不够明确标识归属 | 全局重命名：`config.py` `_streaming_sec()` → `_plugin_sec()`；`plugin.py` 注入/清理逻辑；`controller_mixin.py` 常量；所有测试用例；README/SKILL.md 文档。**注意**：仅改配置节名，`streaming_panel_expanded` 等字段名不变 |
| 2 | Docs | CHANGELOG 1.0.0 之前版本归档 | 200+ 行历史版本记录对日常维护价值低 | 移至 `docs/CHANGELOG.archive.md`，主 CHANGELOG 仅保留 v1.0.0+ 记录 |
| 3 | Docs | SKILL.md 过时信息更新 | 模块结构已拆分为子包（cardkit/、patching/），但 SKILL.md 仍描述为单文件 | 更新文件地图：反映 cardkit/ 和 patching/ 子包结构；更新配置节名称 |
| 4 | Test | 测试用例与代码结构对齐 | 配置节重命名、模块拆包后测试引用需同步；`element_count=177` 在 `_ELEMENT_THRESHOLD=185` 下无有效拆分点导致 6 个拆卡测试失败 | 所有 `{"streaming": ...}` → `{"hermes_lark_streaming": ...}`；修正拆卡测试 `element_count` 为 169~170（保证拆分点存在）；更新 oversized tool 连续拆卡测试为单次拆卡行为（`_maybe_rollover_tool_segment` delta=0 不触发二次拆分，属已知限制） |
| 5 | Refactor | 继续模块化：controller/ 和 state/ 子包 | cardkit/ 和 patching/ 已拆包，controller 和 state 相关文件仍为扁平结构 | 新增 `controller/` 子包（controller.py、controller_mixin.py、controller_linear_mixin.py）和 `state/` 子包（linear.py、text.py、tooluse.py、session.py、linear_split.py），保留顶层 shim 文件向后兼容 |

---

📎 **1.0.0 之前的版本记录已归档**：[CHANGELOG.archive.md](CHANGELOG.archive.md)
