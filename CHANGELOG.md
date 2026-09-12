# 更新日志

## V1.2 — 全链路代码复查修复 — 2026-09-12

- **黄金置信度惩罚从不生效**：权益冲突规则此前对所有资产无差别触发，`confidence.py` 用
  `asset_id != "RMB_GOLD"` 做补偿，结果把黄金自己的两条惩罚一起丢掉，而 trace 仍显示扣分。
  现在规则本身按资产族限定，惩罚不再被吞掉。
- **ResilientExecutor 超时对同步 Provider 无效**：同步调用在事件循环线程里执行，
  `wait_for` 无法介入。改为在专用线程池执行并显式复制上下文，`timeout_seconds` 真正生效。
- sensitivity 符号处理统一为按绝对值判断相关性（scoring / quality / confidence 此前三处不一致）。
- `degraded_factor_coverage` 的 0.72 阈值改为读取 `resilience.yaml`，不再硬编码。
- `latest_published` 改为按分析日期排序；`POST /runs/{id}/publish` 对回溯发布返回 409，
  需显式 `allow_backdated=true`。
- `save_snapshot` 重复保存同一 run 时先清理旧的 asset_scores / score_contributions。
- `ContextVar` 在 indicator 并发采集与 LLM 同步桥接处显式跨线程传递，冻结配置快照不再丢失。
- `circuit_breakers` 全局状态加锁。
- 每个资产的 horizon 结果只计算一次（此前算分与算贡献各跑一遍，共 6 次），数值不变。
- `latest_factor_states` 增加 4320 小时扫描下界。
- `resilience.yaml` → 1.3.0：`require_all_assets_scoreable` 改为 `true`，
  Quality Gate 不再允许"一个资产数据够就放行全部七个资产"。
- 新增 6 个回归测试（`tests/test_engine_consistency.py`）。

> 升级提示：`resilience.yaml` 版本变更，需执行
> `docker compose exec api python scripts/sync_runtime_config.py --activate-all` 后才生效。

## V1.1 — Event Intelligence P0 修复 — 2026-09-12

修复"78 次 LLM 调用产出 0 个事件"的生产缺陷，并按生产标准加固整条 LLM 调用链。

- **根因修复**：结构化输出的 JSON Schema 此前从未随请求发给模型，而所有事件 Schema 都是
  `extra="forbid"`，导致每次响应都校验失败并耗尽整条 fallback 链。现在 Schema 作为独立
  system turn 随请求下发。
- 响应解析容错：支持 Markdown 围栏与前后缀说明文字；空 content 明确报错并指出 token 预算问题。
- 错误分类：`LLM_HTTP_4xx` / `LLM_HTTP_5xx` / `LLM_TRANSPORT_ERROR` / `LLM_SCHEMA_INVALID`
  分别落库，配置类错误与模型输出类错误不再混为一谈。
- 同一模型上一次原地 repair 重试（回灌校验错误），失败才降级到下一个 alias。
- 429 / 5xx / 网络抖动增加有界退避重试，并识别 `Retry-After`。
- thinking 开启时为推理 token 预留额外预算，避免 `finish_reason=length` 导致空响应。
- 未配置的 Provider 记入 `skipped` 并写入异常信息，可区分"没配 Key"与"被上游拒绝"。
- **事件图条件路由**：非事件新闻只花 1 次调用，无因子映射的事件只花 2 次，
  Expert Review 仅对 strength ≥ 3 且存在冲突的事件触发。单簇最坏 4 次、典型 1 次。
- 新增 `WIDEGOLD_EVENT_MAX_CLUSTERS` 环境级硬上限与 `news.yaml:max_clusters`，
  按（文档数、源等级、新鲜度）择优截断，DB Runtime Config 漂移也无法绕过成本上限。
- Run Trace 的 `EVENT_INTELLIGENCE` 阶段新增 `llm_failures`（alias/model:error_code 直方图
  与样例错误原文），失败原因无需连数据库即可看到。
- 新增 `scripts/sync_runtime_config.py`：检查并修复 YAML 与 DB ACTIVE 版本漂移。
  此前 `db-seed` 对已有 ACTIVE 的配置一律写成 APPROVED，改 YAML 重启在运行时不生效。
- 新增 `scripts/llm_probe.py`：逐 alias 发一次极小真实调用，验证模型名、推理参数与 Schema 契约。
- `structured_call_sync` 在已有事件循环时改为在独立线程执行，不再直接抛错。
- `configs/news.yaml` → 1.3.0；事件图 `graph_version` / `prompt_version` → 1.1.0。

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
