# hermes-lark-streaming 迭代追踪（ROADMAP）

> 本文档记录每个版本"做了什么、没做什么、延后了什么"，方便你随时掌握项目进度。
> 从 v1.2.0 开始，每个版本配套有 `PRD-vX.Y.Z.md`（需求）和 `DESIGN-vX.Y.Z.md`（设计）。
> 历史版本（v1.0.x ~ v1.1.3）没有独立的 PRD/DESIGN，本表从 `docs/CHANGELOG.md` 回溯整理。

---

## 图例说明

| 标记 | 含义 |
|------|------|
| ✅ 已完成 | 该版本已发布，此项已实现 |
| ⏳ 计划中 | 已纳入当前规划版本，待开发 |
| 🔬 调研中 | 需先调研/求证，可行才纳入 |
| ⏭️ 延后 | 明确推迟到未来版本 |
| ❌ 不做 | 明确排除，不会实现 |

---

## 一、当前版本状态

| 版本 | 状态 | 发布日期 | 基线 |
|------|------|----------|------|
| v1.1.3 | ✅ 已发布（最新稳定版） | 2026-06-21 | — |
| **v1.2.0** | ✅ **已实施**（代码完成+测试通过，待真飞书E2E验证+发布） | 待定 | v1.1.3 |
| v1.3.0 | 预告（部分项已从 v1.2.0 延后过来） | 待定 | v1.2.0 |

---

## 二、v1.2.0 计划（当前重点）

> 详细需求见 `docs/PRD-v1.2.0.md`，详细设计见 `docs/DESIGN-v1.2.0.md`。

### 2.1 header 功能补全【你的核心诉求】

| 编号 | 内容 | 状态 | 风险 | 决策 |
|------|------|------|------|------|
| H1 | `header.enabled` 写进所有文档（README/README.zh-CN/SKILL/AGENT_GUIDE） | ✅ 已完成 | 零 | 已确认做 |
| H2 | 默认配置注入加 `header: {enabled: false}` | ✅ 已完成 | 极低 | 已确认做 |
| H3 | `/aowen status` 展示 header 开关状态 | ✅ 已完成 | 极低 | 已确认做 |
| H4 | 启动诊断日志加 header 状态 | ✅ 已完成 | 零 | 已确认做 |
| H5 | 补 header 单元/集成测试 | ✅ 已完成 | 零 | 已确认做 |
| H6 | 封卡后头部颜色变色 | ✅ 已完成 | **中** | **方案 B**：开了 header 改用全量重建封卡 |
| H7 | 降级路径 IM 卡也支持 header | ✅ 已完成 | 低 | **方案 A**：IM 卡也加 header |

**背景**：你以为 header 不可配置，其实代码里已写了一半（配置项+builder+controller 接线都在），但无文档、无测试、封卡不变色、降级不覆盖。v1.2.0 补全成"真正能用"。

### 2.2 日志与体验优化

| 编号 | 内容 | 状态 | 风险 | 决策 |
|------|------|------|------|------|
| L1 | "streaming closed" 日志去重（同卡只打 1 条 INFO） | ✅ 已完成 | 极低 | 已确认做 |
| L2 | 流式元素心跳保活（防长对话被飞书超时关闭） | 🔬 调研中 | 中 | **先调研再定**：可行则纳入 v1.2.0，不可行推迟 v1.3.0。v1.2.0 发布时若仍未调研完成，推迟到 v1.3.0 |

**背景**：生产 2.5 天 0 报错，但 1 个 12 分钟长对话末尾产生 15 条重复日志 + 丢失实时打字机效果（内容未丢）。

### 2.3 代码清理

| 编号 | 内容 | 状态 | 风险 | 决策 |
|------|------|------|------|------|
| C1 | 删除 `CardVisualState` 死代码（定义了从没被读） | ✅ 已完成 | 低 | 已确认做 |
| C2 | `build_panel_header/children` 单独入口 | ✅ 已完成 | 零 | **保守**：保留不动，补注释说明（详见 DESIGN 第六章） |
| C3 | `TextState` 6 个死方法精简 | ⏭️ 延后到 v1.3.0 | — | v1.2.0 不做（详见 DESIGN 第七章） |

### 2.4 文档勘误

| 编号 | 内容 | 状态 | 风险 | 决策 |
|------|------|------|------|------|
| D1 | CHANGELOG v1.1.0 mtime 描述勘误 | ✅ 已完成 | 零 | **不补 mtime 检测**，只纠正文档。mtime 是 v1.1.0 内部明确删除的设计（提交 0d468cd） |

### 2.5 小改进（延后）

| 编号 | 内容 | 状态 | 决策 |
|------|------|------|------|
| M1 | prune 日志显示更长 msg_id（避免前缀碰撞误读） | ⏭️ 延后到 v1.3.0 | 影响不大，往后放 |
| M2 | on_completed 重复调用日志降级为 DEBUG | ⏭️ 延后到 v1.3.0 | 影响不大，往后放 |
| M3 | `_sessions` 字典加并发锁 | ⏭️ 延后到 v1.3.0 | 生产从未出问题，往后放 |

### 2.6 明确不做（v1.2.0 排除项）

| 内容 | 理由 |
|------|------|
| 不改 CardKit 主流程（创建/流式/增量封卡） | 生产 100% 成功，动了风险太大 |
| 不改降级方案（drain fallback / IM 降级） | 生产 1 次触发且成功，保留作保险 |
| 不动并发限流 | 生产 0 次触发，保留作保险 |
| 不重构 monkey patching | 7 次重启全部成功 |
| 不做 header 自定义文案/图标 | v1.2.0 只做"开关+状态色"，自定义留未来 |
| 不补 mtime 自动检测 | v1.1.0 已明确删除的设计，尊重原决策 |

---

## 三、v1.3.0 预告（从 v1.2.0 延后的项）

> 这些项已明确从 v1.2.0 延后，计划在 v1.3.0 处理。v1.3.0 的 PRD/DESIGN 待 v1.2.0 发布后编写。

| 编号 | 内容 | 来源 | 预估风险 |
|------|------|------|----------|
| C3 | `TextState` 6 个死方法精简（on_partial/is_dirty/mark_flushed/completed_text/accumulated/last_flushed） | v1.2.0 决策 6 延后 | 低（有测试保护） |
| M1 | prune 日志显示更长 msg_id | v1.2.0 决策 7 延后 | 极低 |
| M2 | on_completed 重复调用日志降级 | v1.2.0 决策 7 延后 | 极低 |
| M3 | `_sessions` 字典加并发锁 | v1.2.0 决策 7 延后 | 低 |
| L2（备选） | 流式心跳保活（若 v1.2.0 调研不通过） | v1.2.0 决策 3 备选 | 中 |
| 待评估 | 5 秒节流 mtime 检测（若未来想要"改配置自动生效"） | v1.2.0 决策 4 留口 | 低 |

---

## 四、历史版本回溯（v1.0.0 ~ v1.1.3）

> 以下从 `docs/CHANGELOG.md` 回溯整理。历史版本无独立 PRD/DESIGN，只有 CHANGELOG。

### v1.1.3（2026-06-21）✅ 已发布

| 类型 | 内容 |
|------|------|
| 🐛 P0 Bug | CardKit 创建失败降级到 IM 卡片后内容全丢（降级代码设 linear=False + unified_state=None 导致内容写入通道断裂）。修复：降级保留 unified_state + linear=True，新增 IM 降级 flush/seal 用 update_card 全量更新 |
| ✨ 测试 | IM 降级路径新增 5 个测试覆盖 |

### v1.1.2（2026-06-20）✅ 已发布

| 类型 | 内容 |
|------|------|
| ✨ 兼容 | Hermes v0.17.0 兼容性验证（_run_agent 新增 persist_user_message 参数） |
| 📝 文档 | inject_time 与 message_timestamps 关系说明 |
| 🔧 修复 | hermes-integration-test cron 时间调整（避开整点高负载） |

### v1.1.1（2026-06-20）✅ 已发布

| 类型 | 内容 |
|------|------|
| 🐛 Bug | drain 遇 300309（streaming closed）直接 skip 答案丢失 → 统一 fallback 用 batch_update+partial_update_element |
| 🐛 Bug | drain/seal fallback 带 tag 导致 300312 → 去掉 tag 只保留 content |
| 🐛 Bug | `_prune_stale_sessions` 误清理 STREAMING session → 只清理终态 session |
| 🔧 修复 | `_release_session_data` 死代码 → 封卡后调用释放重数据 |
| ✨ 功能 | E2E 支持 open_id + chat_id；E2E 时间模拟工具；8 个 E2E 生命周期测试；sync-from-gitee 工作流支持真飞书 E2E |

### v1.1.0（2026-06-17）✅ 已发布

| 类型 | 内容 |
|------|------|
| 🐛 P0-1 | 并发限流调用 on_message_interrupted 方法不存在 → 改为 on_interrupted |
| 🐛 P0-2 | FeishuClient 自定义 base_url 不生效 → __init__ 追加 .domain(config.base_url) |
| 🐛 P0-3 | 部分配置 mtime 热更新失效 → **⚠️ 此条后被撤销**：提交 0d468cd 在 v1.1.0 内部明确删除了 mtime 检测（有意设计，避免高频 stat），CHANGELOG 未同步更新，v1.2.0 勘误 |
| ✨ P0-3 | `/aowen config reload` 命令（秒级生效） |
| 🐛 P0-4 | pyproject.toml packages 遗漏 5 个子包 → 补全 9 个子包 |
| 🐛 P0-5 | unregister 未清理活跃会话 → 新增 ctrl._sessions.clear() |
| 🏗️ P0-6 | 删除 cardkit/theme.py（主题系统从未被引用） |
| 🏗️ P0-7 | 删除 assets/card_templates/（13 个 JSON 未被引用） |
| 🐛 Bug | 300313 "not find elementID" 短回复闪烁 → 新增重试 + fallback |
| ✨ 功能 | stream_element 成功日志；card_trace_id；启动补丁应用报告；doctor 命令；错误卡片友好化；并发限流；Hermes 适配层；版本探测；E2E 测试框架；配置热更新；监控面板；模块化重组；/aowen 卡片视觉重构；/aowen 中断场景提示卡 |
| 🔧 修复 | 日志前缀统一为 HLS:；19 处 except Exception: pass 替换为 debug 日志；删除非线性 ControllerMixin 主路径；去重机制简化；状态机布尔标志合并为 _creation_stages；删除 backward-compat 别名；拆分 build_unified_panel |

### v1.0.7（2026-06-16）✅ 已发布

| 类型 | 内容 |
|------|------|
| 🐛 Bug | Cron/Gateway 静态卡片表格超限 → 新增 _MAX_CRON_TABLES=5 |
| 🔧 修复 | 工具步骤标题冗余状态文字；推理内容缺少缩进；Schema Error 300315 日志缺细节 |
| 🐛 Bug | 并发消息污染新卡片 → 三个回调入口加 epoch 校验 |
| ✨ 功能 | 新增 AGENT_GUIDE.md |

### v1.0.6（2026-06-15）✅ 已发布

| 类型 | 内容 |
|------|------|
| 🐛 Bug | 卡片超限 300305 内容重复 → 统一面板自动裁剪折叠 |
| 🔧 修复 | 封口顺序导致卡片冻住缺页脚 → close_streaming 移到 batch_update 之后 |
| ✨ 功能 | max_tool_steps / max_reasoning_rounds 配置项；卡片级元素安全网（195 阈值精确裁剪） |

### v1.0.5（2026-06-14）✅ 已发布

| 类型 | 内容 |
|------|------|
| 🐛 Bug | notify_feishu 提交消息重复；简单对话空白 agent loop 面板；正常完成卡片被新消息覆盖成"已停止"；.hermes-last-release 被反复覆盖；FeishuAdapter 反应拦截静默失效 |
| 🔧 修复 | 3 个单元测试与 Phase 2 拆分不同步；集成测试在 sync-from-gitee 中 25 个 skipped |
| ✨ 功能 | Hermes Agent 集成测试工作流；飞书卡片模板导出（后于 v1.1.0 P0-7 删除） |

### v1.0.4（2026-06-13）✅ 已发布

| 类型 | 内容 |
|------|------|
| 🐛 Bug | GitHub Actions 测试失败 37/780；验证安装 HERMES_PYTHON 路径错误 |
| ✨ 功能 | 新增 `python` CLI 命令；FlushController 惰性获取事件循环 |

### v1.0.3（2026-06-12）✅ 已发布

| 类型 | 内容 |
|------|------|
| 🏗️ 架构 | 卡片生命周期状态机优化（CardPhase/TerminalReason + epoch 机制） |
| 🐛 Bug | 会话列表永久显示"处理中"（i18n_content 未更新）；重复 close_streaming 导致 300317；UnboundLocalError；折叠面板思考内容重复；会话列表完成后仍显示处理中；封卡时内容丢失；页脚早于回答内容出现；流式参数低于官方默认 |
| ✨ 功能 | 打字机效果；延迟 Markdown 优化；间隔计时器优化 |

### v1.0.2 ✅ 已发布

| 类型 | 内容 |
|------|------|
| 🏗️ 架构 | 统一面板架构（消除元素爆炸，50+ 元素 → 3-4 元素恒定） |

### v1.0.1 ✅ 已发布

| 类型 | 内容 |
|------|------|
| 🔧 重构 | streaming → hermes_lark_streaming 重命名；CHANGELOG 归档；模块化 controller/state；测试用例修复 |
| ✨ 功能 | 页脚新增 cost 字段；tokens 增强 reasoning_tokens 显示 |

### v1.0.0 ✅ 已发布

| 类型 | 内容 |
|------|------|
| 🎉 初始 | 目录结构扁平化；日志对齐 hermes_plugins 命名空间；TextState 等基础状态类引入（后于 v1.0.2 统一面板后部分成为死代码，v1.3.0 计划精简） |

---

## 五、长期方向（未排期）

以下为已知但未排期的改进方向，供未来规划参考：

| 方向 | 说明 | 来源 |
|------|------|------|
| header 自定义文案/图标 | 让用户自定义头部标题文本和图标 | v1.2.0 排除项 |
| 5 秒节流 mtime 检测 | 若未来想要"改配置自动生效"，可评估此方案 | v1.2.0 决策 4 留口 |
| panel "只重建 children" 优化 | 若 flush 性能成瓶颈，真正实现 v1.1.0 预留的优化 | v1.2.0 决策 5 / DESIGN 第六章 |
| terminal_reason 统计 | /aowen monitor 展示终端原因分布（利用当前死字段 terminal_reason） | 代码审查 D2 |
| HermesCompat 单例化 | 避免每次调用重新探测 Hermes 版本 | 代码审查 A2 |
| 补丁静默失败进 _patch_status | 让 doctor 和 /aowen status 能看到 background/cron 补丁失败 | 代码审查 A5 |
| 统一走 transition() | 所有状态变更走 transition() 而非直接赋值 | 代码审查 A3 |
| 删除 CardPhase.FAILED 别名 | deprecated 别名清理 | 代码审查 D8 |
| 验证 `<details>` 标签 | 错误卡片用的 `<details><summary>` 需验证飞书 markdown 是否渲染 | 代码审查 C4 |

---

## 六、文档规范（从 v1.2.0 起）

从 v1.2.0 开始，每个版本迭代遵循以下文档规范：

| 文档 | 位置 | 作用 | 何时写 |
|------|------|------|--------|
| `docs/PRD-vX.Y.Z.md` | docs/ | 产品需求（做什么、为什么、验收标准） | 开发前 |
| `docs/DESIGN-vX.Y.Z.md` | docs/ | 技术设计（怎么改、改哪里、风险） | 开发前 |
| `docs/CHANGELOG.md` | docs/ | 发布记录（改了什么） | 发布时 |
| `docs/ROADMAP.md` | docs/ | 本文档，迭代追踪 | 持续更新 |

**历史版本**（v1.0.0 ~ v1.1.3）没有独立 PRD/DESIGN，只有 CHANGELOG。本 ROADMAP 第四章从 CHANGELOG 回溯整理。

---

*最后更新：v1.2.0 规划阶段。下次更新时机：v1.2.0 开发完成并发布后。*
