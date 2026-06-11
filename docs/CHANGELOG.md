## v1.0.2 (2026-06-11)

### 🏗️ Architecture: Unified Panel (Breaking Change)

**Complete redesign of the card element architecture** — replaces the old segment-based approach with a single unified collapsible panel that holds all reasoning rounds and tool steps.

#### Why
The old architecture created a separate collapsible panel for each reasoning round and tool call segment, causing element count to explode near Feishu's 200-element card limit. This led to:
- 100% preservative seal failure rate (300314 element not found)
- Frequent card splitting (22 times in production logs)
- Cascade failures: seal → full rebuild → 300305 → compact → minimal → split
- Streaming mode closure (300309) when card TTL exceeded 600s
- Users reporting cards feel significantly slower than native Hermes messages

#### What Changed

**Unified Panel Architecture:**
- **1 unified panel** = 1 card element for ALL reasoning + tool calls (was: N panels = N×4 elements)
- **1 answer streaming element** for the answer text
- **3-4 total elements** regardless of conversation length (was: 50-100+ elements)
- Panel icon: `robot_filled`, reasoning round icon: `robot-add_outlined` (replacing emoji)
- Panel title: `Agent Process · N rounds · M tools · Xs` (dynamic stats)
- `display.show_reasoning` config still controls whether reasoning content appears in the panel

**Performance Optimizations:**
- Initial card pre-allocates all slots (2 API calls instead of 3) — saves ~150-200ms
- Loading hint embedded in initial card JSON — eliminates 1 separate API call
- FeishuClient pre-warming at plugin registration — saves ~50-100ms on first message
- Default flush interval reduced from 500ms to 200ms — faster text appearance

**Bug Fixes:**
- **Element existence tracking**: Preservative seal now only deletes elements that actually exist on the card, eliminating 100% failure rate caused by deleting already-removed `context_loading_hint`
- **Proactive TTL extension**: When card approaches 540s lifetime, automatically extends TTL by 600s, preventing 300309 streaming closure
- Preservative seal now updates the unified panel to its final state (non-streaming, expanded per config) during seal, ensuring consistent visual presentation
- **CLI `__main__.py` fix**: Running `$HERMES_PYTHON ~/.hermes/plugins/hermes-lark-streaming/__main__.py status` no longer fails with "attempted relative import with no known parent package". Root cause: when Python runs `__main__.py` directly, `__package__` is `None`, making relative imports (`from .config import Config`) impossible even after `_bootstrap_package()` registers the package. Fix: (1) CLI command handlers now use absolute imports (`from hermes_lark_streaming.config import Config`), which work because `_bootstrap_package()` guarantees the package is in `sys.modules`; (2) after bootstrap, `__package__` is set to `"hermes_lark_streaming"` as a belt-and-suspenders measure. Updated README docs to recommend `python /path/to/__main__.py` for directory plugin installs

**Removed Code:**
- `state/linear_split.py` — No longer needed (no element counting/splitting)
- `_do_linear_split`, `_maybe_rollover_tool_segment` — No more card splitting
- `build_linear_compact_seal_card` — No more progressive degradation
- Element counting fields (`element_count`, `element_limit_hit`, `split_disabled`, `split_index`) removed from `CardSession`

**Migration Notes:**
- `LinearState` → `UnifiedLinearState` (backward-compat alias maintained)
- `session.linear_state` → `session.unified_state` (backward-compat property maintained with deprecation warning)
- `Segment` class → `ReasoningRound` (backward-compat alias maintained with deprecation warning)
- New i18n keys: `agent_process`, `rounds`, `tools_count`, `round_n`
- New element IDs: `UNIFIED_PANEL_ELEMENT_ID`, `ANSWER_ELEMENT_ID`
- Default `flush_interval_ms` changed from 500 to 200

---

## v1.0.1 (2026-06-10)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Feature | **页脚新增 `cost` 字段** | 用户需要直观看到每次对话花了多少钱，以及这个数字的可信度 | 新增 `cost` 页脚字段，显示预估费用 + 可信度后缀：`$0.023 (est.)`（估算）、`$0.023 (actual)`（实报）、`Free`（免费）；费用未知时不显示。数据来源：`agent.session_estimated_cost_usd` + `agent.session_cost_status` |
| 2 | Feature | **`tokens` 字段增强：显示推理 token** | 使用 DeepSeek/Claude thinking 等思考型模型时，推理 token 可能占大部分消耗，但现有 `tokens` 字段只显示输入/输出，推理消耗完全不可见 | 当 `reasoning_tokens > 0` 时，`tokens` 字段显示 `↑ 2.1K ↓ 850 💭 3.2K`；普通模型（推理 token 为 0）显示不变。数据来源：`agent.session_reasoning_tokens` |
| 3 | Feature | **默认页脚字段更新** | 新增 `cost` 字段对用户有价值，应默认展示 | 默认页脚从 `[status, elapsed, model, compression_exhausted]` 变更为 `[status, elapsed, model, cost, compression_exhausted]` |

---

## v1.0.0 (2026-06-10)

| # | 类型 | 问题/功能 | 原因 | 修复/说明 |
|---|------|-----------|------|-----------|
| 1 | Bug | **MEDIA 文件上传被静默丢弃** | `FeishuAdapter.send()` 拦截器在 agent 路径下（`card_sent=True`）直接抑制整个 send，但 `extract_media()` 已提取出 `_media_parts`（图片/文件）却从未使用——媒体文件被静默丢弃 | ~~已废弃~~：所有 MEDIA 相关代码已在 v1.0.0 中移除，MEDIA 域现在完全由 hermes 负责（见条目 14） |
| 2 | Analysis | **CardKit streaming_mode 迁移可行性** | 两个参考项目（Cheerwhy/openclaw-lark）均使用原生流式模式，需确认我们项目是否需要迁移 | 经完整源码审查确认：**我们的项目已经在使用 CardKit 原生流式模式**（`cardkit_stream_element` + `cardkit_batch_update`），"线性模式"是内容组织策略而非不同的 API 机制。我们的实现比参考项目更高级（支持 card splitting、preservative seal、渐进降级），无需迁移 |
| 3 | Analysis | **配置/阈值变更审查** | 参考项目有不同配置值（如 `_MAX_CARD_TABLES=3`），需确认哪些需要变更 | 经完整审查：所有配置已与参考项目对齐，无需修改。确认保持：`_MAX_CARD_TABLES=20`、`_MAX_CHUNK_CHARS=2400`、`_ELEMENT_THRESHOLD=185`、`_FOOTER_RESERVE=15`、`flush_interval_ms=100` |
| 4 | Refactor | `monkey_patch.py` 2693行单文件过大 | 所有运行时拦截逻辑集中在一个文件，维护和定位困难 | 拆分为 `patching/` 子包：`__init__.py`（入口+共享状态+编排）、`gateway.py`（GatewayRunner包装器）、`callbacks.py`（回调拦截）、`adapter.py`（FeishuAdapter包装器） |
| 5 | Refactor | `cardkit.py` 1371行单文件过大 | 5种卡片类型的构建逻辑混在一起 | 拆分为 `cardkit/` 子包：`__init__.py`（重导出门面）、`elements.py`（基础元素）、`cards.py`（卡片组装）、`special.py`（特殊卡片）、`i18n.py`、`md.py` |
| 6 | Refactor | `controller_linear_mixin.py` 1410行过大，拆卡/估算逻辑与核心flush逻辑耦合 | 拆卡判断、元素估算等独立功能与核心流式逻辑混在一起 | 拆分为 `controller/` 子包（`core.py`、`mixin.py`、`linear_mixin.py`）+ `state/` 子包（`linear.py`、`text.py`、`tooluse.py`、`session.py`、`linear_split.py`） |
| 7 | Refactor | 配置节 `streaming:` → `hermes_lark_streaming:` | 配置节名 `streaming` 与 Hermes 原生 `display.streaming` 易混淆，且不够明确标识归属 | 全局重命名：`config.py` `_streaming_sec()` → `_plugin_sec()`；`plugin.py` 注入/清理逻辑；所有测试用例；README/SKILL.md 文档。**注意**：仅改配置节名，`streaming_panel_expanded` 等字段名不变 |
| 8 | Refactor | 移除所有向后兼容 shim 文件 | 18 个顶层 shim 文件（`monkey_patch.py`、`cardkit.py`、`controller.py` 等）仅为历史版本兼容而保留，增加代码冗余和维护负担 | 删除所有 shim 文件，更新测试用例导入路径为新子包路径（`patching.`、`cardkit.`、`controller.`、`state.`）。移除 `config.py`/`plugin.py` 中 `_HERMES_CONFIG_PATH` 向后兼容常量 |
| 9 | Docs | 根目录文档散乱 | CHANGELOG.md、SKILL.md 直接放在根目录，缺少归类 | 创建 `docs/` 文件夹，将 CHANGELOG.md、SKILL.md 移入，新增 `docs/ISSUES_TEMPLATE.md`（AI Issue 提交模板） |
| 10 | Docs | CHANGELOG.md 冗长（460行），历史版本细节过多 | v0.18.3 及更早版本的详细修复过程对当前维护价值低 | 精简为184行：最新3版保留详细表格，旧版每版1-3行总结。1.0.0 之前版本归档至 `docs/CHANGELOG.archive.md` |
| 11 | Docs | SKILL.md 冗长（507行），版本历史与 CHANGELOG 重复 | 版本历史、Roadmap（全已完成）占大量篇幅；陷阱章节叙事过多 | 精简为269行：删除版本历史和 Roadmap，陷阱章节只保留结论和经验教训 |
| 12 | Docs | README 安装命令格式不清晰 | 3条安装命令堆在一起，分不清哪个平台 | 按 `# gitee (SSH)` / `# github (SSH)` / `# github (HTTPS)` 分组标注 |
| 13 | Test | 测试用例与代码结构对齐 | 配置节重命名、模块拆包后测试引用需同步；`element_count=177` 在 `_ELEMENT_THRESHOLD=185` 下无有效拆分点导致 6 个拆卡测试失败 | 所有 `{"streaming": ...}` → `{"hermes_lark_streaming": ...}`；修正拆卡测试 `element_count` 为 169~170（保证拆分点存在）；更新 oversized tool 连续拆卡测试为单次拆卡行为；更新所有测试导入路径 |
| 14 | 🗑️ Removed | **ImageResolver 移除** | 插件不再处理 `![url]` markdown 图片，图片由 hermes 完全负责 | 移除 `image.py`（ImageResolver）及所有图片解析/上传/替换逻辑；图片现在由 hermes 原生处理 |
| 15 | 🗑️ Removed | **MEDIA 域代码全部移除** | 插件不再处理 MEDIA 域，媒体文件由 hermes 完全负责 | 移除 MEDIA FIX 透传、`extract_media` 调用、`media_delivery_allow_dirs` 桥接、gateway 卡片中 `media_parts` 参数；MEDIA 域 100% 由 hermes 负责 |
| 16 | 🐛 Fixed | **中断 anchor_id 错误** | 中断时新卡片错误引用旧消息的 anchor_id，导致新卡关联到错误的消息 | 新卡片现在正确引用新消息的 anchor_id |
| 17 | 🐛 Fixed | **中断+拆卡竞态条件** | `on_interrupted` 在进行中的 flush 完成前就中止旧会话，导致并发 `card_id` 操作 | `on_interrupted` 现在等待进行中的 flush 完成后再中止旧会话，防止并发 `card_id` 操作 |
| 18 | 🐛 Fixed | **answer 估算错位导致过早拆卡** | answer 估算按全量重建封卡的 `_split_long_text` 分块数计算（answer 24000 字符 → 估算 10 elements），但保留式封卡后 answer 仍为 1 element，估算与实际严重错位导致"第N张卡只有一句话" | **方案B**: answer 估算固定为 1 element（对齐保留式封卡实际行为）；移除 Step 0 动态重估和 answer 内部拆分逻辑；300305 reactive 拆卡作为兜底 |
| 19 | 🐛 Fixed | **/stop 与占位卡片路径不统一** | `on_aborted` 与 `on_completed(aborted=True)` 两条封卡路径不统一，占位卡片（跑马灯状态）和有内容卡片被区别对待 | 统一封卡路径：无论卡片有无内容，/stop 时都走 `_preservative_seal`（关闭流式 + 添加停止标记）；占位卡片是流式卡片生命周期的正常阶段；新增 gateway.py 卡片卡死检测 + adapter.py /stop 响应拦截 |
| 20 | Refactor | **目录结构扁平化** | 嵌套 `hermes_lark_streaming/` 子目录使根 `__init__.py` 需 `importlib` 桥接，与 Hermes 官方插件约定不一致 | 移除嵌套子目录，核心代码直接位于 repo root；根 `__init__.py` 改为实际包初始化（非桥接）；对齐 Hermes 插件规范（如 spotify、google_meet） |
| 21 | Refactor | **Logger 名称对齐** | `logging.getLogger("hermes_lark_streaming")` 不在 Hermes `COMPONENT_PREFIXES` 前缀内，日志路由到 `agent.log`；修改 `config.yaml` 的 `logging.level` 不影响插件日志级别 | Logger 名称保持 `hermes_lark_streaming`，从 Hermes root logger 继承级别（由 `config.yaml` `logging.level` 设定）；日志留在 `agent.log`、不路由到 `gateway.log`；无需显式 `setLevel()`——级别自动跟随 Hermes 配置 |
| 22 | Refactor | **模块结构重组 — 单文件归入子包** | `feishu.py`、`flush.py`、`config.py`、`patch.py`、`unavailable_guard.py` 散落在仓库根目录，根目录 .py 文件过多，结构不清晰 | `feishu.py` + `unavailable_guard.py` → `feishu/` 子包（client.py + guard.py）；`flush.py` → `flush/` 子包（controller.py）；`config.py` → `config/` 子包（reader.py）；`patch.py` → `patching/hooks.py`（归入已有 patching 子包）；根目录仅保留 `__init__.py`、`__main__.py`、`plugin.py` 三个入口文件；所有子包 `__init__.py` 提供 re-export，对外接口不变 |
| 23 | Refactor | **跨子包导入规范化** | 桥接模块移除后需统一导入方式 | 跨子包用 `..`（如 `from ..config import Config`），同子包用 `.`；根 `__init__.py` 条件导入（先 relative 后 absolute，兼容 pytest） |
| 24 | Test | **测试基础设施对齐** | 目录结构变更后 pytest 无法定位包 | 新增 `conftest.py` 在 repo root 注册包到 `sys.modules`（镜像 Hermes `_load_directory_module`）；更新 `pyproject.toml` pytest 配置 |

---

📎 **1.0.0 之前的版本记录已归档**：[CHANGELOG.archive.md](CHANGELOG.archive.md)
