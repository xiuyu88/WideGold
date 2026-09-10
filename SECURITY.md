# 安全说明

## Secret

禁止提交：

- `.env`；
- FRED / Qwen / DeepSeek / GPT API Key；
- PostgreSQL 密码；
- Admin 密码；
- External Bridge 内部 Bearer Key；
- Cookie / Session Token。

仓库只保留 `.env.example` 和 `.env.final.example`。

## 日志

日志不得包含完整 API Key、用户密码、原始 Session Cookie。LLM 日志应以 request id、alias、token count、latency、error code 为主，不应默认记录完整敏感 Prompt/Response。

## 网络暴露

当前 Compose 面向本机/内网开发。若暴露到公网，请至少增加：

- TLS；
- 防火墙；
- Secure Cookie；
- 反向代理访问控制；
- 数据库/Redis 不直接暴露公网；
- API Rate Limit；
- Secret 管理；
- 定期备份。

## 漏洞报告

公开发布时建议通过 GitHub Security Advisory 私下报告高风险漏洞，不要在 Issue 中提交 Secret、真实 Token 或可直接利用的生产凭证。
