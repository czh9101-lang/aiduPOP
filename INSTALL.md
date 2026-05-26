# hermes-lark-streaming 安装/卸载指南

> Feishu/Lark CardKit v2.0 streaming cards for Hermes Agent

---

## 安装

### 1. 克隆仓库

```bash
git clone https://gitee.com/Aowen-Nowor/hermes-lark-streaming.git
cd hermes-lark-streaming
```

### 2. 安装到 Hermes

```bash
hermes plugins install .
```

安装后确认状态：

```bash
hermes plugins list
# 应看到 hermes-lark-streaming 为 enabled 状态
```

### 3. 重启 Gateway

```bash
hermes gateway restart
```

### 4. 验证

在飞书私聊中发一条消息给 Hermes，确认流式卡片正常展示。

---

## 卸载

### 1. 从 Hermes 卸载插件

```bash
hermes plugins uninstall hermes-lark-streaming
```

### 2. 重启 Gateway

```bash
hermes gateway restart
```

### 3. 验证

```bash
hermes plugins list
# 确认 hermes-lark-streaming 已不在列表中
```

在飞书私聊中发消息给 Hermes，确认恢复到普通文本回复（无流式卡片）。

---

## 排障

| 问题 | 排查 |
|------|------|
| 安装后卡片不展示 | `hermes gateway restart` 是否执行？ |
| 卸载后仍显示卡片 | Gateway 是否已重启？重启后等几秒再试 |
| 插件列表为空 | 检查 `hermes plugins list` 输出，确认安装路径正确 |

---

## 依赖

- Python ≥ 3.11
- Hermes Agent（已安装并配置飞书渠道）
- `lark-oapi >= 1.4.0`
- `PyYAML >= 6.0`
