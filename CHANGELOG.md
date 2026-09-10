# 更新日志

## V1 GitHub Release — 2026-09-11

- 完成 Windows 10 + Docker Compose 的整体工程闭环。
- API/包版本为 `1.0.0`。
- 统一免费 External Bridge 升级至 `2026.09.11-free-sources-v3.1`。
- DR007 免费方案使用 ChinaMoney FDR007 fixing proxy，并明确 proxy 语义。
- Eastmoney 盈利预测支持实际多页分页，免费快照可本地积累。
- WGC 央行黄金与供需 Adapter 支持公开数据降级路径。
- External 免费公网请求增加有界超时、重试、缓存和 negative backoff。
- Windows Docker E2E 端口解析改为多层 fallback，避免 API URL 为空。
- Prefect UI 增加宿主机可访问 API URL。
- Runtime Config、管理员 Bootstrap、JSONB 日期序列化、Nginx Docker DNS 等首次部署问题完成工程加固。
- LLM 路由保留低成本优先方案；Live LLM extraction 的真实兼容性问题记录为已知限制。
- 增加中文 GitHub README、总体技术方案、代码导读、部署使用、测试验收、数据因子、已知限制和运维速查。
- 增加 `quickstart` / `quicktest` 最小运行脚本和 GitHub Actions 基础 CI。

## RC3

- 首次完整 Win10 Docker Production Smoke。
- Vite 类型、DB bootstrap、Admin Seed、JSONB date、Nginx DNS、Prefect UI、E2E cold start 加固。

## RC2

- Machine Output Contract、External Contract、NBS 工业利润、Quality Gate fresh coverage、asset-scoped Factor 修复。
