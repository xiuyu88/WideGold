# WideGold V1 最新编码计划与完成状态

## 1. 当前阶段判断

WideGold 已经完成“工程骨架”阶段，当前是 **production-shaped V1**。核心架构不再调整，后续工作集中在真实金融数据覆盖、因子专项算法、生产 E2E 和后验校准。

原则：

```text
不为 Coverage 造假
不拿近似数据冒充定义不同的指标
不让单个 Provider/MCP/LLM 故障拖垮系统
不允许 Runtime Config 无版本热改
不允许 LLM 直接产生最终 Asset Score
```

## 2. 已完成

### A. 基础设施【完成】

- Docker Compose
- PostgreSQL
- Redis
- Alembic migration
- automatic db-migrate
- db-seed
- LangGraph checkpoint DB setup
- Prefect Server / Worker / deployment bootstrap
- Nginx / API / Web
- Docker healthcheck
- Docker log rotation
- Windows/Linux startup scripts
- backup/restore scripts

### B. 数据模型与 Point-in-Time【完成】

- Indicator Registry
- IndicatorObservation
- release_ts / ingest_ts / revision_vintage / definition_version
- RawDocument
- Event / FactorState / AssetScore / Contribution
- AnalysisRun / RunEvent / Audit
- Calibration Report
- duplicate-vintage protection
- replay cutoff semantics

### C. 容错与降级【完成】

- Provider retry
- fallback
- circuit breaker
- last-known-good
- VALID / STALE / PARTIAL / UNAVAILABLE / CONFLICTED
- stale reliability decay
- Asset-weighted Coverage
- Quality Gate
- previous Published snapshot retained on failure
- External Push/Pull contract validation
- single News Cluster LLM failure isolation

### D. Agent / LLM【完成 V1 主体】

- Qwen L1
- DeepSeek L2
- GPT Expert L3
- GPT failure → DeepSeek high
- structured output validation
- Event Intelligence Graph
- Explanation Graph
- Scenario Graph
- Factor Research Graph
- PostgreSQL checkpointer infrastructure
- news clustering / event dedup

### E. Prefect【完成 V1 主体】

- close analysis deployment
- 18:10 Asia/Shanghai
- CN trading calendar guard
- admin manual trigger
- individual indicator collection tasks
- news collection
- clustering
- per-cluster Event task
- Factor calculation
- Factor Resolution
- Score / Quality
- Explanation
- Replay
- Calibration
- Research deployment

### F. Runtime Config Governance【完成】

- YAML bootstrap
- PostgreSQL config_versions
- DRAFT / APPROVED / ACTIVE / DEPRECATED
- immutable version content
- content hash
- prospective cross-config validation
- transaction activation
- advisory lock
- reference table materialization
- runtime cache generation invalidation
- per-run VersionSnapshot freeze

### G. Auth / Admin / Observability【完成 V1 主体】

- ADMIN / USER
- HttpOnly session
- Redis session
- bootstrap admin
- Audit log
- Business Trace
- SSE incremental trace
- Runtime diagnostics
- Provider diagnostics
- External/MCP diagnostics
- Runtime Config UI
- Audit UI
- Factor Health API/UI
- Asset History
- Event Evidence

### H. Replay / Calibration【完成第一版】

- stored point-in-time replay
- no network backfill of unavailable historical news
- T+1/T+5/T+20
- directional hit rate
- Score/Return Spearman
- cross-asset Rank IC
- Factor Contribution IC
- score bucket forward returns
- calibration persistence

## 3. 当前真实数据覆盖

已接入的来源/能力包括 FRED、AKShare 及派生序列、NBS 工业利润原生 Adapter、External normalized bridge。

仍明确依赖 External/MCP：

```text
CN_DR007
CN_INDEX_EARNINGS_REV
CN_FOREIGN_ACTIVITY
GLOBAL_CENTRAL_BANK_GOLD
GOLD_SUPPLY_DEMAND
```

配置自检还会报告资产级 Provider coverage gap，例如创业板/科创50精确指数估值缺口。

## 4. 当前 Factor Calculator 状态

27/27 因子都有 Calculator Registry。

已经专项化的重点逻辑包括：

- EQ01 资金面：level + 5期 + 20期；
- EQ02 信用货币：结构/背离告警；
- EQ04 增长：多信号背离；
- EQ05 价格—利润：利润优先、PPI确认；
- EQ07 ERP：Earnings Yield - 10Y CGB；
- EQ09 人民币：人民币特异性压力，不机械用 USD/CNY；
- G05 通胀：real yield / USD 条件交互；
- G06 央行需求：全球/中国代理区分；
- G08 CFTC：拥挤非线性；
- G09 上海金：本地价格 vs 美元金价×USD/CNY 派生溢价。

`quality_flags` 已能贯穿到 Asset risk flags。

## 5. 下一轮编码优先级

### P1 — Release Stabilization【本轮完成】

- Mock Runner stdout 冻结为 `widegold.analysis.v1` 单 JSON；
- WideGold structured logs 改写 stderr；
- External Bridge 冻结为 `widegold.external-indicator.v1`；
- 剩余 5 个 External 指标语义契约 + fixture + 回归测试；
- CN industrial profit NBS 原生 Adapter（YTD、release_ts、Replay 防未来）；
- Quality Gate 增加 fresh weighted coverage，阻止 LKG-only 新发布；
- Release Gate 脚本化。

### P2 — 专项 Calculator 深化

优先对高敏感度/高权重因子验证：

```text
EQ01 EQ02 EQ03 EQ04 EQ05 EQ06 EQ07 EQ10
G01 G02 G04 G05 G06 G07 G08 G09 G11
```

每个 Calculator 要补：

- 数据最低需求；
- level/trend/surprise 分量；
- freshness；
- partial data semantics；
- quality flags；
- unit/contract tests。

### P3 — Production Docker E2E【当前发布阻塞项】

在有 Docker daemon 的主机执行：

```text
cold start
migration
seed
admin login
Prefect dispatch
partial provider failure
bad MCP payload
LLM cluster failure
Quality Gate fail
preview
explicit publish
restart
backup/restore
```

### P4 — Calibration-driven refinement

累计足够历史后：

- Factor effectiveness；
- sensitivity matrix review；
- Confidence calibration；
- score threshold calibration；
- weight candidate proposal；
- walk-forward approval。

## 6. 暂不引入

V1 继续不引入：

```text
Kafka
RabbitMQ
Celery
Kubernetes
Elasticsearch
MongoDB
Milvus/Qdrant
Service Mesh
自动在线权重学习
全量 token trace 前端推送
```

除非真实负载证明现有架构不够。

## 7. V1 完成定义

V1 可以进入长期试运行，需要同时满足：

```text
27 Factor Registry完整
所有核心因子有明确数据语义
External缺失能降级
Docker cold-start通过
18:10 Prefect定时通过
Admin Preview/Publish通过
Point-in-Time Replay通过
Factor Health可观测
Provider故障演练通过
LLM fallback演练通过
DB backup/restore通过
连续运行稳定性测试通过
```

当前最大剩余风险已不是架构，而是**真实数据质量、口径覆盖和实际 Docker 环境集成**。
