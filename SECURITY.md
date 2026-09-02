# 安全政策 / Security Policy

## 支持的版本 / Supported Versions

| 版本 | 支持状态 |
|------|----------|
| 2.4.x | ✅ 积极维护 (Active) |
| 2.3.x | ⚠️ 仅严重安全修复 (Critical fixes only) |
| < 2.3.0 | ❌ 不再支持 (End of life) |

## 报告安全漏洞 / Reporting a Vulnerability

如果你在 aiduPOP 中发现了安全漏洞（特别是配置读写、Host 校验、凭据隔离或卡片注入相关问题），
请**不要**直接公开发布 Issue。

请通过 GitHub Security Advisories 私密通道报告：
- 访问仓库的 **Security > Advisories > Report a vulnerability**

我们承诺在 48 小时内确认报告，并在确认后 7 天内提供修复补丁。

## 安全基线设计 / Built-in Safety Guarantees

- **本地工作坊 (Studio)**：默认仅监听回环地址（`127.0.0.1`），严格拒绝非本地 `Host` 请求，防御 DNS-Rebinding 与 CSRF 跨站攻击。
- **配置深合并**：UI 仅读写白名单字段，用户自定义配置与凭据绝不覆写。
- **单行严格脱敏**：内置 `release_scan.py` 七面敏感信息扫描，防止凭据、私有网段与私钥入仓。
