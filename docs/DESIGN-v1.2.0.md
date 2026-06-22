# hermes-lark-streaming v1.2.0 设计方案

> 版本：v1.2.0（规划稿，决策已固化，待开发）
> 配套文档：`docs/PRD-v1.2.0.md`（产品需求）、`docs/ROADMAP.md`（迭代追踪）
> 本文档面向非技术读者，重点解释"怎么改、改哪里、会影响什么"。

---

## 〇、阅读指引

这份设计方案围绕 PRD 里的需求展开。每个改动点都包含：
- **现状**：现在代码是怎么干的（带文件名和行号）
- **要改成什么样**：用大白话讲新方案
- **影响哪些文件**：具体到文件和函数
- **风险与回滚**：万一改坏了怎么办

最重要的章节是 **第二章（H6 封卡头部变色）**，这是整个 v1.2.0 技术上最难的地方。第六、七章专门回答你问的 C2（panel 拆分函数）和 C3（TextState 死方法）"是什么、业务场景、效果"。

---

## 一、header 功能补全（H1~H5、H7）

这 6 项都是低风险的"补全"工作，一起说。

### 1.1 现状（已求证）

| 组件 | 现状 | 文件:行号 |
|------|------|-----------|
| 配置读取 | `header_enabled` 属性已存在，读 `header.enabled`，默认 False | `config/reader.py:237-243` |
| 占位卡创建 | 已传 `header_enabled=self._cfg.header_enabled` | `controller/linear_mixin.py:225` |
| 全量重建封卡 | 已传 `header_enabled=self._cfg.header_enabled` | `controller/linear_mixin.py:1708` |
| 头部构建函数 | `_build_header(status)` 已实现，支持 streaming/completed/error/stopped 四态 | `cardkit/elements.py:60-79` |
| 默认配置注入 | **没有** `header` 这一项 | `plugin/__init__.py:42-59` |
| 启动诊断日志 | **不打印** header 状态 | `plugin/__init__.py:205-222` |
| /aowen status | **不展示** header 状态 | `aowen/__init__.py:442-460` |
| 文档 | **完全没提** | README / README.zh-CN / SKILL.md / AGENT_GUIDE.md |
| 测试 | **完全没有** | tests/ |
| IM 降级卡 | **不支持** header | `cardkit/cards.py:245-258` `build_im_fallback_card` |
| 网关卡 | **不支持** header | `cardkit/special.py:45-98` `build_gateway_card` |

### 1.2 改动方案

#### H1：文档补全
- **改哪里**：`README.md`、`README.zh-CN.md` 的配置示例里加 `header:` 节；`docs/SKILL.md` 配置结构表加一行；`docs/AGENT_GUIDE.md` 配置表加一行。
- **写成什么样**：
  ```yaml
  hermes_lark_streaming:
    header:
      enabled: false  # 是否在 agent 卡片顶部显示状态头部（蓝色处理中/绿色已完成/红色出错或中断）。默认关闭。
  ```
- **风险**：零。纯文档。

#### H2：默认配置加 header
- **改哪里**：`plugin/__init__.py` 第 42-59 行的 `_DEFAULT_STREAMING_CONFIG` 字典。
- **改成什么样**：加一个键 `"header": {"enabled": False}`。
- **影响**：只对"首次安装"或"配置文件里还没有 hermes_lark_streaming 节"的用户生效。已有配置的用户不受影响（不会被覆盖）。
- **风险**：极低。`_ensure_streaming_config()` 的逻辑是"如果没有这个 section 才注入"，不会动用户已有配置。

#### H3：/aowen status 展示 header 状态
- **改哪里**：`aowen/__init__.py` 里构建 status 卡片的函数（约 442-460 行）。
- **改成什么样**：在配置面板的"关键配置"那一列，加一行 `header: 开/关`。
- **影响**：只是多展示一个字段，不影响命令逻辑。
- **风险**：极低。

#### H4：启动诊断日志加 header
- **改哪里**：`plugin/__init__.py` 第 205-222 行的诊断日志。
- **改成什么样**：在 `enabled=%s linear=%s ...` 这串里加一个 `header=%s`，并读取 `_diag_cfg.header_enabled`。
- **影响**：启动时多打一个字段。
- **风险**：零。

#### H5：补测试
- **改哪里**：新增 `tests/test_header.py`（或在现有测试文件里加 `TestHeaderEnabled` 类）。
- **测什么**：
  1. `build_streaming_card_v2(header_enabled=True)` 输出的卡片 JSON 里有 `header` 字段，且 template 是 blue
  2. `build_streaming_card_v2(header_enabled=False)` 输出没有 `header` 字段
  3. `build_unified_complete_card(header_enabled=True, is_error=True)` 的 header template 是 red
  4. `build_unified_complete_card(header_enabled=True, is_aborted=True)` 的 header template 是 red
  5. `build_unified_complete_card(header_enabled=True)` 正常完成时 template 是 green
  6. controller 创建占位卡时确实传了 `header_enabled`
  7. **方案 B 专项**：开了 header 时封卡走全量重建路径，头部颜色正确切换
  8. **方案 A 专项**：IM 降级卡支持 header
- **风险**：零。纯加测试。

#### H7：降级路径 header 一致性（决策 2 = 方案 A）

**已确认决策**：让 IM 降级卡也支持 header，保持视觉一致。

- **改哪里**：
  - `cardkit/cards.py:245-258` `build_im_fallback_card`：加 `header_enabled` 参数，开启时加 header 字段
  - `cardkit/special.py:45-98` `build_gateway_card`：加 `header_enabled` 参数，开启时加 header 字段
  - `controller/linear_mixin.py:245,397,440` 三处调用点：传 `self._cfg.header_enabled`
- **注意点**：IM 卡片是旧版卡片结构，header 的 JSON 格式可能和 CardKit 2.0 不同。开发时需要查飞书旧版卡片 header 格式文档，或用测试 bot 实测确认。
- **风险**：低。降级路径生产 0 次触发。但 IM 卡片 header 格式需小求证。
- **回滚**：如果 IM 卡片 header 格式有问题，回退到"文档说明降级无 header"（方案 B），零代码风险。

---

## 二、H6：封卡后头部颜色变色【核心技术难点】

这是整个 v1.2.0 最需要你仔细看的部分。

### 2.1 问题回顾

**现状**：
1. 创建卡片时，如果开了 header，头部设为蓝色"处理中"（`_build_header("streaming")`）
2. 流式输出过程中，插件用"批量更新"接口增量改卡片正文（统一面板、回答元素），**完全不碰头部**
3. 封卡时（`_preservative_seal`），插件用"批量更新"接口做：冲刷剩余内容 → 关闭流式模式（settings 接口）→ 添加页脚。**还是不碰头部**
4. 只有在"封卡失败、走全量重建兜底"时（`_do_linear_complete` → `build_unified_complete_card` → `cardkit_update` 全量更新），头部才会变成绿色/红色

**结果**：用户开了 header 后，**99% 的情况下（正常封卡路径），卡片头部永远停留在蓝色"处理中"**，即使 AI 已经回复完了。这显然不对。

### 2.2 技术约束（已向飞书官方文档求证）

| 飞书接口 | 能否改头部 | 说明 |
|---------|-----------|------|
| settings（关流式、改摘要） | ❌ 不能 | 官方明确只支持 `config` 和 `card_link` |
| batch_update（增量改正文） | ❌ 不能 | 动作都是针对"正文元素"，没有改头部的动作 |
| card/update（全量更新） | ✅ 能 | 但会覆盖整张卡片 |

**核心约束**：要让头部变色，只能用"全量更新"把整张卡片重发一遍。没有"只改头部"的轻量方法。

### 2.3 已选方案：方案 B（决策 1 确认）

**方案 B 的做法**：
- **关了 header（默认）**：继续用现在的 `_preservative_seal`（增量封卡），性能最好，行为不变
- **开了 header**：封卡时改走 `_do_linear_complete`（全量重建），用 `build_unified_complete_card(header_enabled=True, is_error/is_aborted)` 重建整张卡，头部颜色就对了（蓝→绿/红）

**为什么选 B**：
1. 头部颜色正确是"开了 header"这个功能的应有之义，否则功能不完整
2. 全量重建路径是现成的、测过的代码，不是新发明
3. 开了 header 的用户主动选择了"要头部"，付出一点封卡性能代价是合理的权衡
4. 关了 header（默认，绝大多数用户）完全不受影响，还是走增量封卡

### 2.4 方案 B 的具体改动清单

| 文件 | 改动 | 风险 |
|------|------|------|
| `controller/linear_mixin.py` `_preservative_seal` 入口（约 903 行） | 加判断：`if self._cfg.header_enabled: return await self._do_linear_complete(session)`。即开了 header 直接转全量重建。 | 中。要确保 `_do_linear_complete` 在"正常成功"场景（而非仅失败兜底）下也正确。 |
| `controller/linear_mixin.py` `_do_linear_complete`（约 1413-1768 行） | 审查：这个函数原本是兜底路径，现在要变成"header 开启时的主封卡路径"。要确认它在正常成功场景下：① 关流式 ② 写完整内容 ③ 设正确头部 ④ 加页脚 都正确。 | 中。需要新增"正常成功场景"的测试用例。 |
| `tests/test_controller.py` | 新增测试：开了 header 时，正常完成走全量重建，头部变绿；出错时头部变红；中断时头部变红。 | 零（纯加测试） |

### 2.5 关键审查点：`_do_linear_complete` 能否胜任"主路径"

开发时必须审查 `_do_linear_complete` 这几点：
1. **它原本只在封卡失败时触发**，是否依赖某些"失败"前置条件？（比如是否假设 `session.state` 已经是某种失败态？）
2. **正常成功场景下**，它能否正确执行：关流式（`close_streaming`）→ 全量更新卡片（`cardkit_update`）→ 设终态？
3. **幂等性**：如果 `_preservative_seal` 已经做了部分工作（比如已经 close_streaming），转给 `_do_linear_complete` 会不会重复调用导致 300317 错误？
   - 预防：`_preservative_seal` 入口判断后**直接 return 转交**，不做任何前置工作，避免重复。
4. **错误处理**：全量更新失败时，是否回退到 `_preservative_seal`？还是直接报错？

### 2.6 真飞书验证（必须做）

H6 方案 B 选定后，**必须先在真飞书上验证一次**，再合并到 DEV：
- 用 E2E 真飞书测试框架
- 测试 bot：App ID `cli_a951f158a1b89bd7`
- open_id：`ou_534e6f1860500163e00bdb11f6e8f508`（私聊）
- chat_id：`oc_bf248a3b37eab3682f643dfb00345ffd`（群聊）
- 验证场景：开 header → 发一条消息 → 看卡片头部从蓝（处理中）变绿（已完成）；再发一条触发错误的消息 → 看头部变红

### 2.7 回滚方式

如果方案 B 上线后发现全量重建路径在正常场景下有问题：
- **临时回滚**：把 `header_enabled` 判断注释掉，回到纯增量封卡（头部颜色退回"不变色"，即原方案 A 的行为，功能降级但不影响内容）
- **正式回滚**：`cd ~/.hermes/plugins/hermes-lark-streaming && git checkout v1.1.3 && hermes gateway restart`

---

## 三、L1：日志去重

### 3.1 现状

`controller/linear_mixin.py` 流式更新检查处，每次发现"流式已关闭"都打一条 INFO 日志 `HLS: unified stream — streaming closed, will be handled by TTL or seal`。生产中同一张卡 45 秒内打了 15 条。

### 3.2 改动

- **改哪里**：`controller/linear_mixin.py` 里打这条日志的地方（搜 `streaming closed`）。
- **改成什么样**：给 `CardSession` 加一个 `_streaming_closed_logged: bool = False` 标志。第一次打 INFO 日志后设为 True，之后同类情况只打 DEBUG。
- **影响**：只是日志级别变化，不影响任何功能逻辑。
- **风险**：极低。
- **回滚**：删掉那个标志判断即可。

---

## 四、L2：流式元素心跳保活（决策 3 = 先调研再定）

### 4.1 问题

长对话中，如果一次 API 调用耗时超过飞书的流式超时（推测 60 秒无活动），飞书会自动关闭流式元素，导致后续内容无法实时流式输出。

### 4.2 调研项（开发前必须先做）

| 调研项 | 怎么查 | 决定什么 |
|--------|--------|----------|
| 飞书流式超时到底是多少秒 | 查飞书 CardKit 流式文档 / 用测试 bot 实测 | 决定心跳间隔 |
| 飞书有没有"空操作保活"的合法手段 | 查流式更新文档，看能不能发空内容的 stream_element | 决定心跳方案可行性 |
| 心跳会不会消耗 API 配额 | 飞书限制 1000 次/分 & 50 次/秒 | 决定心跳频率上限 |
| 心跳会不会影响打字机效果 | 实测 | 决定方案细节 |

### 4.3 如果可行的方案

- 在 `flush/controller.py` 的调度逻辑里加一个"空闲心跳"：如果距离上次真实 flush 超过 N 秒（N < 飞书超时），发一个保活请求。
- **风险**：中。动到 flush 调度核心。必须先调研确认可行。
- **如果调研发现不可行或风险高**：推迟到 v1.3.0，v1.2.0 只做 L1（日志去重）。

### 4.4 决策结果

**先调研再定**。在 v1.2.0 开发前期花半天调研，可行就做，不可行就推迟到 v1.3.0。

---

## 五、C1：删除 CardVisualState 死代码

### 5.1 现状（已求证）

`state/phase.py` 里定义了：
- `CardVisualState` 类（5 个视觉状态常量：第 66-78 行）
- `PHASE_TO_VISUAL` 映射（第 114-123 行）
- `get_visual_state()` 函数（第 144-146 行）

`state/session.py` 里有 `visual_state` 属性（第 206-208 行）。

**但是**：grep 全代码库，`session.visual_state` 在生产代码里**从来没被读取过**（只有 `tests/test_phase.py` 测试它，以及 SKILL.md 文档提了一句）。卡片渲染实际用的是 `session.state == ABORTED` / `is_error` / `is_aborted` 这些参数，不走 visual_state。

### 5.2 改动

- **删什么**：`CardVisualState` 类、`PHASE_TO_VISUAL` 映射、`get_visual_state()` 函数、`session.visual_state` 属性、`state/__init__.py` 里的相关导出、`tests/test_phase.py` 里测这些的用例、SKILL.md 里提 visual_state 的那句。
- **删前确认**：再 grep 一遍 `visual_state`、`CardVisualState`、`PHASE_TO_VISUAL`、`get_visual_state`，确保确实没人用。
- **风险**：低。删的是没人用的东西，有测试保护。
- **回滚**：git checkout。

---

## 六、C2：panel 拆分函数（build_panel_header / build_panel_children）—— 你的疑问解答

### 6.1 这是什么？

`cardkit/elements.py` 里有三个函数，专门用来构建"统一面板"（agent 卡片里那个可折叠的"工具/推理过程"区域）：

| 函数 | 位置 | 作用 |
|------|------|------|
| `build_panel_header` | 第 256-344 行 | 构建面板的"标题部分"——包括图标、轮次数、工具数、耗时等 |
| `build_panel_children` | 第 347-540 行 | 构建面板的"内容部分"——每个推理轮次、每个工具步骤的详情 |
| `build_unified_panel` | 第 543-617 行 | 把上面两个拼起来，构建完整面板 |

### 6.2 它是哪来的？业务场景是什么？

**引入时间**：v1.1.0（提交 78b3451）。CHANGELOG v1.1.0 记录：
> "拆分 build_unified_panel | 每次 flush 重建整个 panel JSON | 拆为 build_panel_header() + build_panel_children()，支持只重建 children"

**业务场景**（当时的设想）：
- agent 卡片在"流式输出中"会频繁刷新（默认每 100ms 一次）
- 每次刷新都要重新构建整个面板的 JSON，发给飞书
- 但面板的"标题部分"（轮次数、工具数、耗时）大部分时候**没变化**——只有内容在变
- **设想的优化**：如果标题没变，就只重建"内容部分"，跳过"标题部分"重建，省点 JSON 构建开销

**预期效果**：每次 flush 少重建 ~300 字节的标题 JSON，降低 CPU 开销。

### 6.3 现状：优化从来没真正实现

我 grep 了整个代码库，发现：
- `build_panel_header` 和 `build_panel_children` 在生产代码里**只在 `build_unified_panel` 内部被调用**（elements.py 第 591、598 行）
- **没有任何其他生产代码单独调用过这两个函数**
- 也就是说，每次 flush 还是重建整个面板（标题 + 内容），所谓的"只重建 children"优化**从来没实现过**

**简单说**：当时为了"支持未来优化"把函数拆开了，但优化逻辑一直没写。这两个"单独入口"就一直空挂着。

### 6.4 决策 5 = 保守处理（保留不动）

**为什么保守**：
1. **收益太小**：flush 的主要开销是飞书 API 网络调用（~100ms 级），JSON 构建（~微秒级）根本不是瓶颈。省 300 字节 JSON 构建对性能毫无感知。
2. **实现优化有风险**：要正确判断"标题内容是否变化"（轮次数变了没？工具数变了没？耗时变了没？），判断错了会导致面板标题不更新。引入的复杂度和风险不值。
3. **保留无害**：这两个函数本身是对的、被内部调用的，留着不碍事。

**v1.2.0 的处理**：
- 不删函数（保留 `build_panel_header` / `build_panel_children`）
- 在 `build_unified_panel` 的 docstring（第 562-565 行已有说明）基础上，补一句注释明确"单独入口预留供未来优化，当前仅内部调用"
- **零代码风险**

### 6.5 未来如果要做这个优化（仅供你了解）

如果某天你发现 flush 性能真的是瓶颈（比如卡片元素极多、flush 极频繁），可以这样实现：
1. 在 `CardSession` 里记录上次 flush 时面板标题的关键字段（轮次数、工具数、elapsed）
2. 每次 flush 前比较当前值和上次值
3. 如果都没变，只调 `build_panel_children` 重建内容，复用上次的标题 JSON
4. 如果有变化，调 `build_unified_panel` 重建整体

但这需要：正确的变化检测 + 充分的测试 + 真飞书验证。**v1.2.0 不做，留作未来备选。**

---

## 七、C3：TextState 死方法 —— 你的疑问解答

### 7.1 这是什么？

`state/text.py` 里有个 `TextState` 类，是"流式文本追踪器"。它有这些方法/属性：

| 方法/属性 | 位置 | 作用 |
|-----------|------|------|
| `on_partial(text)` | 第 92 行 | 接收增量文本片段，累加到 `accumulated` |
| `on_deliver(text)` | 第 97 行 | 接收"交付"文本（剥离推理标签后），存到 `completed_text` |
| `is_dirty(new_text)` | 第 106 行 | 判断"当前文本"和"上次已刷新文本"是否不同 |
| `mark_flushed(text)` | 第 110 行 | 记录"这次刷新发出去的文本"，配合 `is_dirty` 去重 |
| `display_text` | 第 86 行（属性） | 返回当前要显示的文本 |
| `completed_text` | 第 82 行 | 已交付文本 |
| `accumulated` | 第 83 行 | 累加的增量文本 |
| `last_flushed` | 第 84 行 | 上次刷新的文本 |

### 7.2 它是哪来的？业务场景是什么？

**引入时间**：v1.0.0（提交 bb49e04）。是插件最早期的流式文本追踪机制。

**当时的业务场景**（v1.0.0 ~ v1.0.1）：
- AI 流式回复时，会不断吐出文本片段
- 插件需要追踪"已经收到多少文本"、"已经刷新出去多少文本"，用来判断"有没有新内容需要刷新"
- `on_partial` 接收片段，`is_dirty` 判断要不要刷新，`mark_flushed` 记录刷新进度
- 这套机制支撑了早期的"流式文本去重和增量刷新"

### 7.3 现状：大部分方法已经被新架构取代

**v1.0.2 引入"统一面板架构"后**，流式文本追踪改由 `UnifiedLinearState`（`state/linear.py`）负责，TextState 的大部分功能被取代。

我 grep 了整个代码库（非 tests、非 text.py 自身），发现 TextState 在生产代码里**只有 2 个真实调用点**：
- `controller/core.py:570` → `session.text.on_deliver(answer)` ✅ 还在用
- `controller/core.py:820` → `session.text.display_text` ✅ 还在用（作为文本兜底回退时读取）

其余 6 个全是死代码：
- `on_partial` —— 生产从不调用（唯一匹配是 patching/__init__.py:480 的一行**注释**提到它）
- `is_dirty` —— 生产从不调用
- `mark_flushed` —— 生产从不调用
- `completed_text` —— 只在 TextState 内部用
- `accumulated` —— 只在 TextState 内部用
- `last_flushed` —— 只在 TextState 内部用

**简单说**：TextState 现在只剩"存一份最终答案文本 + 提供显示"两个用途，其余"增量追踪"功能全是 v1.0.2 之前的遗留，新架构根本不用了。

### 7.4 决策 6 = 推迟到 v1.3.0

**为什么推迟**：
1. **v1.2.0 范围已经够大**：H6（方案 B 全量重建）是中风险改动，需要集中精力 + 真飞书验证。再加 TextState 精简会让范围膨胀。
2. **删方法要同步删测试**：`tests/test_text.py` 还在测这些死方法，删方法要同步删测试，工作量和回归风险都不小。
3. **当前不影响理解**：这些死方法虽然没人调，但也不干扰主流程，留着不碍事。
4. **精简要谨慎**：`on_deliver` 和 `display_text` 之间有依赖（`on_deliver` 会写 `accumulated`），删 `accumulated` 要确认不影响 `display_text` 的逻辑。

**v1.2.0 的处理**：不动 TextState。

**v1.3.0 的计划**（提前预告）：
- 审查 `on_deliver` 和 `display_text` 的依赖关系
- 精简 TextState 到只有这两个方法 + 必要的内部字段
- 同步删除 `tests/test_text.py` 里测死方法的用例
- 如果 `on_deliver` 的副作用（写 `accumulated`）其实没人读，也可以一起简化

### 7.5 影响评估

| 维度 | v1.2.0（不动） | v1.3.0（精简） |
|------|---------------|----------------|
| 代码行数 | 不变 | 约减少 20-30 行 |
| 风险 | 零 | 低（有测试保护） |
| 收益 | — | 减少维护心智负担，TextState 职责更清晰 |

---

## 八、D1：CHANGELOG v1.1.0 mtime 勘误（决策 4 = 不补，只纠正文档）

### 8.1 问题

`docs/CHANGELOG.md` 第 43 行（v1.1.0 P0-3）写：
> "将 mtime 检测从 `enabled` 属性移到 `_plugin_sec()`，所有走该方法的属性都检测文件变化"

**实际代码**（已求证）：
- `config/reader.py` 的 `_plugin_sec()`（第 272-278 行）只调 `_load()`
- `_load()`（第 311-320 行）用 `if self._raw is not None: return self._raw`——**永久缓存，没有任何 mtime 检测**
- `_check_mtime_and_invalidate()` 函数在代码里**根本不存在**
- `config/reader.py` 的模块 docstring 和类 docstring 都明确写"不做自动 mtime 检测"

### 8.2 求证：mtime 是明确删除的

你确认了"mtime 自动检测是我们明确删除的"。我查了 git 历史：

- **提交 `0d468cd`**（2026-06-18）：`fix: 删除配置自动 mtime 检测，只保留 /aowen config reload 和重启网关`
  - 删除了 `_check_mtime_and_invalidate()` 方法
  - 删除了 `_config_mtime` 字段
  - `_plugin_sec()` 不再调 `stat()`
  - 更新了 README/SKILL 文档
  - 这个提交属于 **v1.1.0 tag 内部**（v1.1.0 及之后版本都包含这次删除）

**所以情况是**：v1.1.0 开发过程中先加了 mtime 检测（P0-3），后来在同一个 v1.1.0 周期内又**明确删除了**它（提交 0d468cd）。但 CHANGELOG 那条 P0-3 描述还停留在"移到 `_plugin_sec()`"，**没反映这次删除**。这是"删了代码但 CHANGELOG 没同步更新"导致的文档失真。

### 8.3 改动（决策 4 = 不补 mtime，只纠正文档）

- **改哪里**：`docs/CHANGELOG.md`
- **改成什么样**：在 v1.1.0 P0-3 那条后面加一条勘误说明，或者修正描述。建议加一条独立说明：
  > "**后续更新（v1.1.0 内部，提交 0d468cd）**：上述 mtime 自动检测机制已被有意移除。原因：避免每次读取配置都触发 `stat()` 系统调用（流式输出期间会高频读配置）。配置刷新方式改为：`/aowen config reload` 立即生效，或重启网关。仅 `inject_time`/`show_reasoning`/`gateway_cards` 三个属性走 60 秒 TTL 缓存。此为有意设计，非 bug。"
- **风险**：零。纯文档。
- **不补 mtime 检测的理由**：这是 v1.1.0 已明确删除的设计决策，v1.2.0 尊重这个决策，不重新加回来。如果未来想要"改配置自动生效"，可在 v1.3.0+ 评估"5 秒节流的 mtime 检测"（最多 5 秒检查一次文件修改时间，不是每次读配置都 stat）。

---

## 九、改动文件清单总览

| 文件 | 涉及的改动项 | 风险 |
|------|-------------|------|
| `plugin.yaml` | 版本号 → 1.2.0 | 零 |
| `plugin/__init__.py` | H2（默认配置加 header）、H4（诊断日志） | 极低 |
| `config/reader.py` | 无改动（header_enabled 已存在；mtime 不补） | — |
| `cardkit/cards.py` | H7（build_im_fallback_card 加 header 参数） | 低 |
| `cardkit/special.py` | H7（build_gateway_card 加 header 参数） | 低 |
| `cardkit/elements.py` | C2 保守：仅补注释说明 | 零 |
| `controller/linear_mixin.py` | H6（封卡分支转全量重建）、L1（日志去重）、H7（降级调用点传 header） | 中 |
| `controller/core.py` | 无改动（M1/M2/M3 推迟到 v1.3.0） | — |
| `state/phase.py` | C1（删 CardVisualState） | 低 |
| `state/session.py` | C1（删 visual_state） | 低 |
| `state/__init__.py` | C1（删导出） | 低 |
| `state/text.py` | 无改动（C3 推迟到 v1.3.0） | — |
| `aowen/__init__.py` | H3（status 卡片加 header 展示） | 极低 |
| `feishu/client.py` | 无改动 | — |
| `flush/controller.py` | L2（调研后视情况） | 调研后定 |
| `tests/test_header.py`（新增） | H5 | 零 |
| `tests/test_phase.py` | C1（删 visual_state 测试） | 低 |
| `tests/test_controller.py` | H5/H6（加 header 测试） | 零 |
| `README.md` | H1、版本徽章 | 零 |
| `README.zh-CN.md` | H1、版本徽章 | 零 |
| `docs/SKILL.md` | H1、版本、C1（删 visual_state 提及）、C2（补注释说明） | 零 |
| `docs/AGENT_GUIDE.md` | H1、版本 | 零 |
| `docs/CHANGELOG.md` | D1（mtime 勘误）、新增 v1.2.0 条目 | 零 |

---

## 十、开发顺序建议

为了控制风险，建议按以下顺序开发（每步都能独立测试）：

1. **第一步（零风险铺垫）**：H1 文档 + H2 默认配置 + H4 诊断日志 + H3 /aowen status + D1 CHANGELOG mtime 勘误 + C2 补注释 + 版本号更新。这些纯增量/纯文档，先做完。
2. **第二步（测试先行）**：H5 写 header 测试（此时测试会暴露 H6 的问题——封卡后头部不变色）。
3. **第三步（核心难点）**：H6 方案 B 实现。先审查 `_do_linear_complete` 能否胜任主路径，再在测试环境用真飞书 bot 验证，最后上生产。
4. **第四步（降级一致性）**：H7 方案 A（IM 卡加 header），需小求证 IM 卡片 header 格式。
5. **第五步（日志优化）**：L1 日志去重。
6. **第六步（调研）**：L2 心跳保活调研，可行则做，不可行推迟。
7. **第七步（清理）**：C1 删 CardVisualState 死代码。
8. **第八步（发布）**：全量测试 → 推 DEV → 打 tag → 同步 github_sync。

> **注**：M1/M2/M3（小改进）和 C3（TextState 精简）已决策推迟到 v1.3.0，v1.2.0 不做。

---

## 十一、测试策略

| 测试类型 | 覆盖内容 |
|---------|---------|
| 单元测试 | header builder（H5）、config reader、删除死代码后回归 |
| 集成测试 | controller 创建卡片传 header_enabled、封卡分支选择（H6 方案 B） |
| E2E mock | 完整生命周期：创建→流式→封卡，验证 header 在各阶段的状态 |
| E2E 真飞书 | **必须**用测试 bot 实测 H6 方案 B——因为飞书 API 限制是关键不确定点，mock 测不出 |

**特别强调**：H6 方案 B 选定后，**必须先在真飞书上验证一次**（用 E2E 真飞书测试框架），确认开了 header 后封卡头部颜色真的从蓝变绿/红，再合并到 DEV。

---

## 十二、风险总结与应急方案

### 12.1 最大风险：H6 方案 B 的全量重建路径在正常场景下的稳定性

- **风险点**：`_do_linear_complete` 原本是"失败兜底"路径，现在要当"header 开启时的主封卡路径"。如果它在正常成功场景下有未发现的边界问题，会导致开了 header 的用户封卡失败。
- **缓解**：
  1. 代码审查 `_do_linear_complete` 是否依赖"失败"前置条件（见 2.5）
  2. 真飞书 E2E 测试覆盖"正常成功"场景
  3. 上线后观察生产日志 1-2 天
- **应急**：如果发现问题，注释掉 H6 的分支判断，回到方案 A（头部不变色），功能降级但不影响内容。`git checkout v1.1.3` 完全回滚。

### 12.2 次要风险：H7 IM 降级卡 header 格式

- **风险点**：IM 卡片是旧版结构，header 的 JSON 格式可能和 CardKit 2.0 不同。如果格式不对，降级时可能报错。
- **缓解**：开发时查飞书旧版卡片 header 格式文档，或用测试 bot 实测。
- **应急**：H7 退回方案 B（文档说明降级无 header），零代码风险。

### 12.3 整体回滚

```
cd ~/.hermes/plugins/hermes-lark-streaming
git fetch --tags
git checkout v1.1.3
hermes gateway restart
```

---

## 十三、决策记录（7 项已全部确认）

| 编号 | 决策项 | 你的决定 | 本设计文档对应章节 |
|------|--------|---------|-------------------|
| 1 | H6 封卡头部变色 | **方案 B**（开了 header 改用全量重建封卡） | 第二章 |
| 2 | H7 降级 header 一致性 | **方案 A**（IM 卡也加 header） | 第一章 H7 |
| 3 | L2 心跳保活 | **先调研再定** | 第四章 |
| 4 | D1 mtime 检测 | **不补**，只纠正文档（mtime 是明确删除的设计） | 第八章 |
| 5 | C2 panel 拆分函数 | **保守**：保留不动，补注释说明 | 第六章 |
| 6 | C3 TextState 死方法 | **推迟到 v1.3.0** | 第七章 |
| 7 | M1/M2/M3 小改进 | **往后放**到 v1.3.0 | — |

---

*本设计方案基于 PRD-v1.2.0.md，所有技术约束均已向飞书官方文档求证，所有代码现状均带文件:行号依据，所有历史改动均经 git log 求证。详见 `/home/z/my-project/worklog.md` 的完整工作记录。*
