# WideGold Agent 系统技术架构与 Workflow 详细设计 V1

> 本文承接《宽基黄金Agent_产品设计与系统架构方案》与《宽基黄金因子体系与影响准则_V1》，用于正式敲定 V1 工程边界。  
> 本阶段只定义架构、服务、Workflow、Graph、Schema、调用关系与治理机制，不进入具体业务代码实现。  
> 当前已确认：资产范围为沪深300、中证A500、中证500、中证1000、创业板指、科创50、人民币黄金；日常自动分析仅在 A 股收盘后运行一次，同时允许管理员手动“更新 / 分析 / 重算 / 发布”。

---

# 1. 最终架构结论

## 1.1 架构核心

WideGold V1 采用：

```text
Prefect 3
    +
LangGraph
    +
Deterministic Rule Core
    +
PostgreSQL
    +
Redis
    +
FastAPI
    +
React / Vite
```

职责严格分离：

```text
Prefect
= 系统级流程编排、调度、重试、补跑、运行状态、管理员手动触发

LangGraph
= 只有需要 LLM 推理和条件路由的 Agent 工作流

Deterministic Rule Core
= 因子状态、事件衰减、冲突规则、相关组封顶、资产评分、Confidence

FastAPI
= Web/API 边界，不执行重型分析任务

PostgreSQL
= 唯一事实库（Source of Truth）

Redis
= 缓存、锁、限流、短期状态，不保存不可恢复的投资事实

React
= Dashboard + 管理后台
```

系统必须遵循：

> **LLM 不能直接生成最终 Asset Score；最终分数必须由确定性评分引擎计算。**

---

# 2. 模型体系正式改为三级能力路由

用户已确认模型能力按：

```text
Qwen
  ↓
DeepSeek
  ↓
第三方 GPT
```

进行分级。

这不是简单的“失败时依次换模型”，而是同时包含两套机制：

```text
能力分层（Capability Routing）
+
异常升级（Escalation / Fallback）
```

---

## 2.1 三层模型职责

### L1 — Qwen：高频、低成本结构化任务

默认别名：

```yaml
llm_profiles:
  fast:
    provider: qwen
    model_alias: qwen_fast
```

建议承担：

- 新闻初步分类；
- 文本清洗辅助；
- 新闻摘要；
- Event 初次抽取；
- 标题 → 主题标签；
- Entity / Date / Region 抽取；
- 低风险 Structured Output；
- 用户解释中的轻量改写。

当前阿里云 Model Studio 的 Qwen 接口支持 OpenAI-compatible Chat / Responses 接口，因此 Provider 层可以与其他 OpenAI-compatible 模型统一抽象，而不要求业务代码直接依赖 DashScope SDK。[^1]

V1 默认候选：

```text
qwen3.8-flash
```

但代码中禁止写死具体型号，只引用：

```text
qwen_fast
```

---

### L2 — DeepSeek：日常核心推理

默认别名：

```yaml
llm_profiles:
  reasoning:
    provider: deepseek
    model_alias: deepseek_reasoning
```

承担：

- Event → Factor Mapping；
- 影响方向判断；
- 事件 strength / novelty / priced-in 的解释性判断；
- 多来源矛盾分析；
- Factor impact reasoning；
- 日报主要解释；
- 中等复杂度 Scenario；
- Factor Research 的初步综合。

DeepSeek 当前官方 API 支持 OpenAI-compatible 格式、JSON Output 和 Tool Calls，因此同样可以归入统一 Provider Adapter。[^2]

V1 默认候选：

```text
deepseek-v4-pro
```

业务层只引用：

```text
deepseek_reasoning
```

---

### L3 — 第三方 GPT：专家升级层

当前配置：

```text
Base URL:
https://ca.memofun.net/v1

Model:
gpt-5.6-sol
```

默认别名：

```text
gpt_expert
```

承担：

- 高冲突 Event Review；
- DeepSeek 结果不稳定时的仲裁；
- 重要政策复杂传导；
- Factor Research 深度综合；
- 新 Factor Candidate 研究；
- 高复杂度 Scenario；
- 管理员显式要求“专家复核”。

推理档：

```text
normal expert      → medium
important review   → high
deep research      → high / xhigh
```

`max` 不进入 V1 默认生产链。

---

## 2.2 模型路由不是“三个模型每次都调用”

禁止：

```text
Qwen 跑一次
↓
DeepSeek 再跑一次
↓
GPT 再跑一次
↓
三模型投票
```

这会使成本和复杂度无意义增加。

V1 推荐：

```text
普通新闻
  ↓
Qwen Extract
  ↓
schema / confidence pass?
  ├─ YES → 后续
  └─ NO  → DeepSeek

Event → Factor
  ↓
DeepSeek
  ↓
conflict / low confidence?
  ├─ NO  → Rule Validator
  └─ YES → GPT Expert Review
```

因此 GPT 是 **按条件升级**，而不是默认必经节点。

---

## 2.3 Model Router 输入 Schema

```yaml
ModelRequest:
  task_type:
    - extraction
    - classification
    - reasoning
    - explanation
    - research
    - expert_review

  preferred_tier:
    - L1
    - L2
    - L3

  reasoning_effort:
    - minimal
    - low
    - medium
    - high
    - xhigh

  require_json: true
  require_tools: false

  max_latency_ms: optional
  fallback_allowed: true
```

输出：

```yaml
ModelExecution:
  provider:
  model_alias:
  resolved_model:
  request_id:
  latency_ms:
  input_tokens:
  output_tokens:
  schema_valid:
  retry_count:
  fallback_from:
  error_code:
```

所有模型调用必须写入 `intel.llm_runs`，用于：

- 成本分析；
- 错误追踪；
- 不同模型质量比较；
- 后续调整路由。

---

# 3. 最终服务边界

V1 不做微服务泛滥，采用 **模块化单体 + 独立运行时组件**。

最终 Docker 服务建议为：

```text
widegold-web
widegold-api

prefect-server
prefect-services
prefect-worker

postgres
redis

nginx
```

没有独立：

```text
widegold-worker
```

原因：

> Prefect Worker 就是后台业务执行进程，它加载与 FastAPI 相同的 WideGold Python package。

这样避免：

```text
FastAPI worker
+
自定义 worker
+
Prefect worker
```

三套执行逻辑重复。

Prefect Flow 本身支持运行状态跟踪、重试、timeout、参数验证、远程 deployment 调用以及调度运行，适合作为系统级异步执行边界。[^3]

---

# 4. 服务职责

## 4.1 `widegold-web`

技术：

```text
React
TypeScript
Vite
ECharts
TanStack Query
```

职责：

### 普通用户

- 今日 Dashboard；
- 资产卡片；
- 资产详情；
- Factor contribution；
- 今日重要事件；
- 历史 Score；
- Confidence；
- 风险说明；
- Scenario 入口。

### Admin

- 系统运行状态；
- “更新并分析”；
- “重新分析”；
- “仅更新数据”；
- 手动发布时间；
- Workflow Runs；
- 数据源健康；
- Factor Registry；
- Rule / Weight Version；
- Candidate Factor；
- Research Approval；
- 历史重放。

Web 不直接连接 Prefect/PostgreSQL。

全部经过 FastAPI。

---

## 4.2 `widegold-api`

FastAPI 只负责：

```text
Auth
Query
Command
Validation
Permission
Trigger Prefect
Return Run ID
```

禁止在 API 请求内：

- 拉取几十个数据源；
- 执行 LangGraph；
- 执行评分全流程；
- 大量回测；
- 深度研究。

FastAPI 官方文档也建议重型后台计算使用独立任务/队列系统，而不是依赖 Web 进程内部的 BackgroundTasks。[^4]

### API 边界

```text
/api/v1/public/*
/api/v1/assets/*
/api/v1/events/*
/api/v1/scores/*
/api/v1/scenarios/*

/api/v1/admin/runs/*
/api/v1/admin/data/*
/api/v1/admin/analysis/*
/api/v1/admin/factors/*
/api/v1/admin/research/*
/api/v1/admin/versions/*
```

---

## 4.3 `prefect-worker`

职责：

- 执行所有系统 Flow；
- 数据抓取；
- LangGraph 调用；
- 因子计算；
- Score；
- Report；
- Backfill；
- Research Flow。

它加载：

```text
widegold-core
```

而不是复制一份业务代码。

---

## 4.4 `prefect-server / prefect-services`

职责：

- Flow orchestration；
- Deployment；
- schedule；
- states；
- retry；
- task run；
- observability。

Prefect 与业务数据分库存储。

---

## 4.5 `postgres`

一个 PostgreSQL 实例，建议三个逻辑数据库：

```text
widegold
prefect
langgraph
```

原因：

```text
widegold
→ 业务表由 Alembic 管理

prefect
→ Prefect 自己管理 schema

langgraph
→ LangGraph checkpointer 自己管理 checkpoint 表
```

LangGraph 官方提供 PostgreSQL checkpointer，并将 PostgreSQL saver 作为生产 workload 的 checkpoint 方案。[^5]

因此三者不共享 migration ownership。

---

## 4.6 `redis`

Redis 不承担业务真相。

用途：

```text
provider cache
API cache
distributed lock
rate limit
idempotency short lock
Prefect messaging
temporary progress
```

原则：

```text
Redis down
→ 可以恢复

PostgreSQL widegold down
→ 核心系统不可工作
```

---

# 5. 推荐代码目录

采用 monorepo。

```text
widegold-agent/
│
├── apps/
│   ├── api/
│   │   ├── main.py
│   │   ├── dependencies.py
│   │   ├── middleware/
│   │   └── routers/
│   │       ├── auth.py
│   │       ├── dashboard.py
│   │       ├── assets.py
│   │       ├── events.py
│   │       ├── scores.py
│   │       ├── scenarios.py
│   │       └── admin/
│   │           ├── analysis.py
│   │           ├── data.py
│   │           ├── runs.py
│   │           ├── factors.py
│   │           ├── research.py
│   │           └── versions.py
│   │
│   └── web/
│       ├── src/
│       │   ├── pages/
│       │   ├── components/
│       │   ├── features/
│       │   ├── api/
│       │   ├── hooks/
│       │   └── types/
│       └── ...
│
├── src/
│   └── widegold/
│       │
│       ├── domain/
│       │   ├── assets/
│       │   ├── factors/
│       │   ├── events/
│       │   ├── scores/
│       │   ├── research/
│       │   └── common/
│       │
│       ├── schemas/
│       │   ├── common.py
│       │   ├── market.py
│       │   ├── macro.py
│       │   ├── news.py
│       │   ├── events.py
│       │   ├── factors.py
│       │   ├── scores.py
│       │   ├── reports.py
│       │   ├── workflows.py
│       │   └── llm.py
│       │
│       ├── data/
│       │   ├── registry.py
│       │   ├── service.py
│       │   ├── normalize.py
│       │   ├── validators.py
│       │   └── providers/
│       │       ├── base.py
│       │       ├── akshare/
│       │       ├── official/
│       │       ├── market/
│       │       ├── macro/
│       │       ├── rates/
│       │       ├── fx/
│       │       ├── gold/
│       │       └── news/
│       │
│       ├── llm/
│       │   ├── router.py
│       │   ├── profiles.py
│       │   ├── structured.py
│       │   ├── retry.py
│       │   └── providers/
│       │       ├── base.py
│       │       ├── qwen.py
│       │       ├── deepseek.py
│       │       └── openai_compatible.py
│       │
│       ├── engine/
│       │   ├── factor_state/
│       │   │   ├── normalize.py
│       │   │   ├── continuous.py
│       │   │   ├── macro.py
│       │   │   └── policy.py
│       │   ├── impact/
│       │   │   ├── event_decay.py
│       │   │   └── impact.py
│       │   ├── rules/
│       │   │   ├── conflict.py
│       │   │   ├── correlation.py
│       │   │   ├── group_cap.py
│       │   │   └── overrides.py
│       │   ├── scoring/
│       │   │   ├── direction.py
│       │   │   ├── asset_score.py
│       │   │   └── contribution.py
│       │   └── confidence/
│       │       ├── coverage.py
│       │       ├── agreement.py
│       │       └── confidence.py
│       │
│       ├── graphs/
│       │   ├── event_intelligence/
│       │   │   ├── graph.py
│       │   │   ├── state.py
│       │   │   ├── nodes.py
│       │   │   └── routing.py
│       │   ├── factor_research/
│       │   ├── explanation/
│       │   └── scenario/
│       │
│       ├── workflows/
│       │   └── prefect/
│       │       ├── analysis_flow.py
│       │       ├── data_flow.py
│       │       ├── review_flow.py
│       │       ├── research_flow.py
│       │       ├── replay_flow.py
│       │       └── deployments.py
│       │
│       ├── repositories/
│       │   ├── assets.py
│       │   ├── observations.py
│       │   ├── news.py
│       │   ├── events.py
│       │   ├── factors.py
│       │   ├── scores.py
│       │   ├── research.py
│       │   └── runs.py
│       │
│       ├── db/
│       │   ├── session.py
│       │   ├── models/
│       │   └── migrations/
│       │
│       ├── auth/
│       ├── audit/
│       └── settings/
│
├── configs/
│   ├── assets.yaml
│   ├── factors.yaml
│   ├── weights.yaml
│   ├── rules.yaml
│   ├── data_sources.yaml
│   ├── models.yaml
│   └── schedules.yaml
│
├── prompts/
│   ├── event/
│   ├── research/
│   ├── explanation/
│   └── scenario/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── replay/
│   └── fixtures/
│
├── infra/
│   ├── docker/
│   ├── nginx/
│   └── postgres/
│
├── scripts/
│
├── docker-compose.yml
├── pyproject.toml
├── uv.lock
├── .env.example
└── README.md
```

---

# 6. 目录设计原则

不要按照：

```text
agents/
utils/
services/
misc/
```

简单堆文件。

推荐分成：

```text
domain
data
engine
graphs
workflows
repositories
schemas
```

其中最重要的边界：

```text
data/
= 获得事实

graphs/
= 理解非结构化事实

engine/
= 计算判断

workflows/
= 决定什么时候、以什么顺序运行

repositories/
= 保存/读取事实和判断
```

---

# 7. PostgreSQL 业务数据库模块

`widegold` 内建议用 PostgreSQL Schema 做逻辑分区。

```text
iam
reference
raw
market
intel
decision
research
ops
```

---

## 7.1 `iam`

账户和权限。

```text
users
roles
user_roles
sessions / refresh_tokens
```

V1 只需要：

```text
ADMIN
USER
```

不做复杂 RBAC。

---

## 7.2 `reference`

长期定义和版本。

### `assets`

```text
asset_id
name
asset_class
currency
index_code
active
metadata_json
```

### `factor_definitions`

```text
factor_id
name
family
category
semantic_positive
semantic_negative
evidence_grade
lifecycle
correlation_group
update_frequency
max_age
logic_version
```

### `asset_factor_weights`

```text
asset_id
factor_id
horizon
sensitivity
max_contribution
weight_version
effective_from
effective_to
rationale
```

### `data_sources`

```text
source_id
source_type
tier
base_url
provider
priority
active
license_notes
```

### `config_versions`

记录：

```text
factor_logic
weights
event_rules
prompts
data_definition
scoring_engine
```

---

## 7.3 `raw`

保存可追溯原始内容。

### `raw_documents`

新闻、公告、政策正文。

```text
document_id
source_id
source_url
title
content_hash
published_at
retrieved_at
raw_text
raw_metadata
```

### `raw_payloads`

对关键 API 返回值按需保留。

不建议所有行情 API 全量 JSON 永久保留。

---

## 7.4 `market`

所有结构化时间序列事实。

### `market_prices`

```text
instrument_id
observation_ts
open
high
low
close
volume
turnover
source_id
ingest_ts
```

### `macro_observations`

必须 point-in-time。

```text
series_id
observation_date
release_ts
ingest_ts
value
revision_vintage
definition_version
source_id
```

### `rates`

### `fx_rates`

### `etf_flows`

### `margin_data`

### `market_breadth`

### `index_exposures`

```text
asset_id
as_of_date
industry_code
weight
source_version
```

---

## 7.5 `intel`

LLM/事件理解结果。

### `news_clusters`

```text
cluster_id
as_of_date
canonical_title
first_seen_at
last_seen_at
document_count
status
```

### `cluster_documents`

### `events`

```text
event_id
cluster_id
event_type
canonical_title

published_at
effective_at
ingest_at

verified
source_tier

direction
strength
confidence
novelty
priced_in
implementation

horizon
half_life_days
parent_event_id

graph_version
prompt_version
model_execution_id
```

### `event_factor_links`

```text
event_id
factor_id
direction
mapping_confidence
asset_override_json
reason_tags
```

### `llm_runs`

前述 ModelExecution。

---

## 7.6 `decision`

生产判断结果。

### `factor_states`

```text
factor_id
as_of_ts

factor_state
level_score
trend_score
surprise_score
event_score

reliability
coverage
status

logic_version
data_vintage_id
analysis_run_id
```

### `asset_scores`

```text
asset_id
as_of_ts

tactical_score
swing_score
strategic_score

direction_score
asset_score
label
confidence
coverage

analysis_run_id
weight_version
logic_version
scoring_engine_version

published
supersedes_score_id
```

### `score_contributions`

这是可解释性最重要的表。

```text
asset_score_id
factor_id
factor_state
sensitivity
reliability
raw_contribution
capped_contribution
rank_positive
rank_negative
```

### `risk_flags`

### `reports`

---

## 7.7 `research`

因子研究和动态升级。

```text
research_requests
evidence_items
factor_candidates
rule_candidates
weight_candidates
research_reviews
approval_records
```

生产 Rule/Weight 不由 Research Graph 直接修改。

---

## 7.8 `ops`

系统自己的审计。

### `analysis_runs`

这是 WideGold 自己的 run 概念，不依赖 Prefect UI 才能理解。

```text
analysis_run_id
prefect_flow_run_id

trigger_type
run_mode

analysis_date
data_cutoff_ts
started_at
finished_at

status

requested_by
base_run_id

factor_logic_version
weight_version
prompt_version
scoring_version

coverage
error_summary
```

### `data_fetch_runs`

### `provider_health`

### `audit_logs`

### `publish_history`

---

# 8. “一次分析”的核心标识

任何分析都必须有：

```text
analysis_run_id
```

并固定以下上下文：

```yaml
AnalysisContext:
  analysis_run_id:
  analysis_date:
  as_of_ts:
  data_cutoff_ts:

  trigger_type:
    scheduled
    admin_manual

  run_mode:
    full_refresh
    reanalyze
    data_only
    replay

  requested_by:

  versions:
    factor_logic:
    weights:
    prompts:
    scoring:
    data_definition:
```

这样以后才能准确回答：

> “9月10日 18:10 当时为什么给中证1000打 68.4？”

---

# 9. 定时分析频率正式确定

用户已确认：

```text
自动：
A股收盘后 1 次

人工：
管理员点击触发
```

不做：

- 晨间自动分析；
- 盘中轮询分析；
- 每5分钟新闻重评；
- 盘中 push。

---

# 10. 推荐自动执行时间

技术上建议预设：

```text
Asia/Shanghai 18:10
```

原因：

- A 股收盘数据已经稳定；
- 给交易所、基金/ETF、融资融券等日终数据留出缓冲；
- 中债收益率曲线等部分日终数据相对 15:00 有延迟；
- 不必为了“15:01立即出结果”牺牲数据完整度。

但 **18:10 是推荐值，不属于必须写死的架构约束**。

配置：

```yaml
schedule:
  timezone: Asia/Shanghai
  daily_close_cron: "10 18 * * 1-5"
```

同时还要有 A 股交易日 Calendar Guard：

```text
cron trigger
  ↓
is_cn_trading_day?
  ├─ NO → Skipped
  └─ YES → execute
```

不能只靠周一至周五，因为存在法定节假日和调休。

---

# 11. 顶层 Prefect Flow 最终收敛为 4 个

不是七八个顶层 Deployment。

## F1 `analysis_pipeline`

**核心生产 Flow。**

同时服务：

```text
每日收盘自动分析
+
管理员“更新并分析”
+
管理员“重新分析”
+
管理员“仅更新数据”
```

通过参数决定行为。

---

## F2 `factor_research_flow`

管理员手动触发。

用于：

- 深度研究现有 Factor；
- 新 Factor；
- 新 Rule；
- 证据升级；
- Candidate 生成。

不会自动改变生产配置。

---

## F3 `replay_backfill_flow`

管理员手动触发。

用于：

- 补历史缺失数据；
- 修复某天数据；
- point-in-time 重放；
- 评分版本比较；
- Regression Test。

---

## F4 `calibration_review_flow`

V1 可以只允许管理员手动运行。

后续再变成季度自动任务。

用于：

- T+1/T+5/T+20；
- IC / rank IC；
- hit rate；
- Score calibration；
- Factor attribution；
- Candidate weight proposal。

---

# 12. F1：Analysis Pipeline

## 12.1 Flow 输入

```yaml
AnalysisRunRequest:
  analysis_date: date | null

  trigger_type:
    scheduled
    admin_manual

  run_mode:
    full_refresh
    reanalyze
    data_only

  publish_mode:
    auto
    preview_only
    explicit_publish

  force_refresh: false

  base_run_id: null

  selected_assets: null
  selected_factors: null

  requested_by: null
```

---

## 12.2 Flow 主结构

```text
analysis_pipeline
│
├─ 01 create_analysis_context
│
├─ 02 acquire_run_lock
│
├─ 03 trading_day_guard
│
├─ 04 load_version_snapshot
│
│
├─ 05 collect_structured_data
│      ├─ CN market
│      ├─ macro if new
│      ├─ rates
│      ├─ FX
│      ├─ gold
│      ├─ ETF
│      ├─ margin
│      └─ breadth
│
├─ 06 collect_documents
│      ├─ official policy
│      ├─ macro releases
│      └─ financial news
│
├─ 07 normalize_and_validate
│
├─ 08 build_news_clusters
│
├─ 09 event_intelligence_graph
│
├─ 10 calculate_factor_states
│
├─ 11 apply_event_decay
│
├─ 12 apply_conflict_rules
│
├─ 13 calculate_asset_scores
│
├─ 14 calculate_confidence
│
├─ 15 run_quality_gates
│
├─ 16 explanation_graph
│
├─ 17 persist_snapshot
│
├─ 18 settle_matured_reviews
│
└─ 19 publish_or_preview
```

---

# 13. 三种管理员按钮如何映射同一 Flow

## “更新并分析”

```yaml
run_mode: full_refresh
publish_mode: preview_only
force_refresh: true
```

行为：

```text
重新抓数据
→ 重新抓新闻
→ 重新聚类
→ Event Graph
→ Factor State
→ Score
→ Explanation
→ Preview
```

默认不直接替换当前公开结果。

管理员确认后：

```text
POST /admin/runs/{run_id}/publish
```

---

## “重新分析”

```yaml
run_mode: reanalyze
base_run_id: ...
publish_mode: preview_only
```

行为：

```text
不重新抓取事实
↓
固定 data_cutoff
固定 source snapshot
↓
重新执行：
Event / Factor / Score / Explanation
```

适合：

- Prompt 修正；
- 模型切换；
- Weight Version 比较；
- Rule Version 比较。

---

## “仅更新数据”

```yaml
run_mode: data_only
```

执行到：

```text
normalize_and_validate
```

结束。

不生成新 AssetScore。

---

# 14. Scheduled Run 与 Manual Run 的发布策略

推荐：

## Scheduled

```text
质量门通过
→ auto publish

质量门失败
→ 保存 run
→ 不覆盖昨天结果
→ Dashboard 标记“今日分析异常/待更新”
```

## Manual

```text
永远先 Preview
→ 管理员 explicit publish
```

原因：

> 管理员手动运行经常是为了测试模型、规则和配置，不应该一次点击就悄悄覆盖用户看到的正式结论。

---

# ==15. Quality Gate==

==分析流程不能“只要没抛 Exception 就发布”。==

建议：

```yaml
QualityGate:
  minimum_data_coverage: 0.70
  critical_source_missing: false
  score_schema_valid: true
  all_target_assets_scored: true
  unresolved_critical_event_conflict: false
```

具体阈值以后校准。

Fail 时：

```text
Flow 可以是 CompletedWithWarnings / FailedQualityGate
但 production publication = false
```

---

# 16. Prefect Task 粒度

不建议：

```text
每一个 HTTP GET 都是 Prefect task
```

也不建议：

```text
整个分析全塞进一个 Task
```

合适粒度：

```text
fetch_market_bundle
fetch_macro_bundle
fetch_gold_bundle
fetch_global_bundle
fetch_news_bundle

normalize_market_bundle
cluster_documents
invoke_event_graph

calculate_factor_states
calculate_scores
calculate_confidence

generate_reports
publish_snapshot
```

Prefect Tasks 适合 retry/cache/concurrency，Flow 适合组合和远程运行。[^3]

---

# 17. 数据 Provider 调用边界

统一协议：

```python
Provider
  fetch(request) -> ProviderResult
```

结构：

```yaml
ProviderRequest:
  dataset:
  start:
  end:
  as_of:
  force_refresh:
  params:

ProviderResult:
  provider:
  source_id:
  retrieved_at:
  observation_range:
  rows:
  checksum:
  cache_hit:
  fallback_used:
  warnings:
```

Provider 不负责：

- 因子判断；
- 新闻影响评级；
- Asset Score。

---

# 18. Data Fallback

每个 dataset 配置：

```yaml
datasets:
  cn_index_daily:
    providers:
      - official_exchange
      - akshare_primary
      - vendor_fallback

  us_real_yield:
    providers:
      - fred
      - fallback_vendor
```

执行：

```text
Primary
 ↓ fail / stale / invalid
Secondary
 ↓ fail
Cached latest valid
 ↓
mark unavailable
```

禁止：

```text
抓不到 → LLM 自己补一个数字
```

---

# 19. LangGraph 最终保留 4 个 Graph

```text
G1 Event Intelligence
G2 Factor Research
G3 Explanation
G4 Scenario
```

其中生产每天必经：

```text
G1
G3
```

G2 管理员研究时运行。

G4 用户主动询问时运行。

---

# 20. G1 Event Intelligence Graph

## 20.1 Graph 目标

输入：

```text
一个 news cluster / official document group
```

输出：

```text
零个、一个或多个 Structured Event
```

不输出资产最终分。

---

## 20.2 State

```yaml
EventGraphState:
  analysis_context:

  cluster:
    cluster_id:
    documents:

  source_assessment:
  extracted_event:
  mapped_factors:
  impact_assessment:

  deterministic_validation:
  conflict_flags:

  escalation:
    required:
    reason:

  expert_review:

  final_events:
  errors:
```

---

## 20.3 Nodes

```text
START
  ↓
load_cluster
  ↓
source_precheck
  ↓
extract_event_qwen
  ↓
schema_validator
  │
  ├─ invalid → repair_qwen
  │              ↓
  │         still invalid?
  │              ↓
  │        deepseek_extract
  │
  ↓
deduplicate_event
  ↓
map_factors_deepseek
  ↓
assess_event_deepseek
  ↓
rule_validator
  ↓
conflict_detector
  │
  ├─ low conflict ────────────┐
  │                           │
  └─ high conflict            │
         ↓                    │
   expert_review_gpt           │
         ↓                    │
   merge_expert_result         │
         └────────────┬───────┘
                      ↓
              finalize_event
                      ↓
                     END
```

---

# 21. G1 各节点输入输出

## `extract_event_qwen`

输入：

```yaml
EventExtractionInput:
  title:
  documents:
  published_times:
  source_types:
```

输出：

```yaml
EventExtractionOutput:
  event_detected: bool
  event_type:
  canonical_title:
  entities:
  geography:
  effective_at:
  facts:
  uncertainties:
```

不得输出：

```text
黄金 85 分
中证1000 70 分
```

---

## `map_factors_deepseek`

输入：

```yaml
FactorMappingInput:
  extracted_event:
  allowed_factor_registry:
  allowed_reason_tags:
```

输出：

```yaml
FactorMappingOutput:
  mappings:
    - factor_id:
      direction:
      mapping_confidence:
      reason_tags:
  unmapped_reasons:
```

模型只能从 Registry 允许的 Factor ID 中选。

---

## `assess_event_deepseek`

输出：

```yaml
EventImpactAssessment:
  strength: 1..5
  confidence: 0..1
  novelty: 0..1
  priced_in: 0..1
  implementation: 0..1
  horizon:
  half_life_days:
  asset_override_candidates:
  reasoning_summary:
```

---

## `rule_validator`

纯 Python。

检查：

```text
Factor ID 合法?
Direction 合法?
Source Tier 满足生产要求?
未验证传闻?
half-life 在配置范围?
asset override 是否允许?
priced-in 是否过于自信?
```

---

## `conflict_detector`

优先纯规则。

例如：

```text
同一 cluster：
来源之间关键数字不一致

同一事件：
mapping direction 冲突

通胀↑：
但模型直接声明黄金强多，
未解释 real yield / USD

政策：
只有表态，
implementation 却给 1.0
```

---

## `expert_review_gpt`

只有：

```text
critical_conflict == true
OR
mapping_confidence < threshold
OR
high_impact_event == true AND evidence disagreement
```

才触发。

---

# 22. G2 Factor Research Graph

这是准则维护系统。

```text
START
 ↓
normalize_research_request
 ↓
build_research_plan
 ↓
collect_evidence
 ↓
extract_evidence_qwen
 ↓
synthesize_deepseek
 ↓
search_contradictions
 ↓
evaluate_evidence_grade
 ↓
expert_synthesis_gpt
 ↓
build_candidate
 ↓
human_interrupt
 ↓
approve / revise / reject
 ↓
save_candidate
 ↓
END
```

LangGraph persistence 支持 checkpoint、恢复、human-in-the-loop 和 fault tolerance，因此 Research Graph 使用 PostgreSQL checkpointer。[^6]

---

## 22.1 Research 输出

```yaml
FactorResearchCandidate:
  candidate_id:
  target_factor_id:
  candidate_type:
    new_factor
    logic_change
    source_change
    weight_hypothesis
    conflict_rule

  hypothesis:
  mechanism:
  evidence_grade:
  supportive_evidence:
  contradictory_evidence:
  limitations:

  proposed_config_patch:

  validation_required:
    - walk_forward
    - sensitivity
    - manual_review

  production_enabled: false
```

永远：

```text
production_enabled = false
```

直到审批。

---

# 23. G3 Explanation Graph

## 23.1 原则

Explanation Agent：

```text
不能重新分析市场
不能新增事实
不能改变 Score
不能改变 Factor State
```

它只能读取：

```text
Asset Score
Factor Contribution
Event
Risk Flags
Confidence
Evidence
```

生成用户友好解释。

---

## 23.2 Graph

```text
START
 ↓
load_score_context
 ↓
select_top_factors
 ↓
build_fact_pack
 ↓
explain_deepseek
 ↓
factuality_guard
 ↓
optional_qwen_simplify
 ↓
final_explanation
 ↓
END
```

若：

```text
Explanation 中出现 Fact Pack 没有的数字
```

Factuality Guard 直接失败重生成。

---

## 23.3 输出

```yaml
AssetExplanation:
  asset_id:
  summary:
  direction_reason:
  top_positive:
  top_negative:
  biggest_risk:
  confidence_explanation:
  regime_note:
  user_level: beginner
```

---

# 24. G4 Scenario Graph

Scenario 与正式 Score 严格分开。

```text
用户假设
 ↓
scenario_parser
 ↓
factor_shock_builder
 ↓
rule_engine simulation
 ↓
scenario_score_delta
 ↓
explanation
```

输出：

```yaml
ScenarioResult:
  scenario_id:
  assumption:
  factor_shocks:
  asset_impacts:
  conditional_branches:
  explanation:
  is_hypothetical: true
```

绝不能写回：

```text
decision.asset_scores
```

而写：

```text
scenario_runs
```

---

# 25. Schema 层统一使用 Pydantic

系统关键边界全部使用 Pydantic Model：

```text
Provider → DataService
LangGraph Node → Node
Graph → Engine
Prefect → Use Case
API → Prefect
Engine → Repository
API → Frontend
```

不要在这些边界传裸 `dict`。

---

# 26. 核心 Schema 清单

第一阶段建议至少固定：

```text
AnalysisRunRequest
AnalysisContext
VersionSnapshot

ProviderRequest
ProviderResult
ObservationBatch
DataQualityResult

NewsDocument
NewsCluster

EventExtractionOutput
FactorMappingOutput
EventImpactAssessment
StructuredEvent

FactorState
FactorStateBatch

ConflictResult
CorrelationGroupResult

AssetScoreInput
FactorContribution
AssetScore
ConfidenceResult

AssetExplanation
DailyReport

ModelRequest
ModelExecution

ResearchRequest
EvidenceItem
FactorResearchCandidate

ScenarioRequest
ScenarioResult
```

---

# 27. `StructuredEvent` 最终 Schema

```yaml
StructuredEvent:
  event_id: UUID
  cluster_id: UUID

  event_type: Enum
  canonical_title: str

  published_at: datetime
  effective_at: datetime | null

  verified: bool
  source_tier: S|A|B|C|D

  factors:
    - factor_id:
      direction: -1|1
      mapping_confidence: float

  strength: int
  confidence: float
  novelty: float
  priced_in: float
  implementation: float

  horizon: tactical|swing|strategic
  half_life_days: float

  asset_overrides: dict

  reason_tags: list[str]
  evidence_document_ids: list[UUID]

  model_execution_ids: list[UUID]
  graph_version: str
  prompt_version: str
```

---

# 28. `FactorState`

```yaml
FactorState:
  factor_id:
  as_of_ts:

  state: float          # -100..100

  components:
    level:
    trend:
    surprise:
    event:

  reliability: float
  coverage: float

  status:
    valid
    stale
    unavailable
    conflicted

  evidence_refs:
  event_ids:

  logic_version:
  data_vintage:
```

---

# 29. `AssetScore`

```yaml
AssetScore:
  asset_id:
  as_of_ts:

  horizons:
    tactical:
    swing:
    strategic:

  direction_score:
  score:
  label:

  confidence:
  coverage:

  contributions:
    - factor_id:
      factor_state:
      sensitivity:
      raw:
      after_caps:
      rank:

  top_positive:
  top_negative:

  risk_flags:

  versions:
    factor_logic:
    weights:
    scoring:
```

---

# 30. 模块之间的最终调用关系

核心原则：

```text
调用方向只能向内
```

推荐：

```text
                 API
                  │
                  ↓
              Use Cases
                  │
                  ↓
               Prefect
                  │
        ┌─────────┼─────────┐
        ↓         ↓         ↓
       Data     Graphs     Engine
        │         │         │
        └─────────┼─────────┘
                  ↓
             Repositories
                  ↓
              PostgreSQL
```

---

# 31. 禁止反向依赖

例如：

```text
Engine
```

不应该：

- import FastAPI；
- import Prefect；
- import React；
- 调用 LLM；
- 自己 HTTP 抓新闻。

它只接受结构化输入。

因此 Engine 可以独立跑：

```text
pytest
backtest
replay
notebook
production flow
```

同一个结果。

这对金融系统非常重要。

---

# 32. 每日完整调用链

```text
Prefect schedule
  ↓
analysis_pipeline()
  ↓
AnalysisContext
  ↓
DataService
  ↓
Provider Registry
  ↓
PostgreSQL market/raw
  ↓
News Cluster
  ↓
Event Intelligence LangGraph
  ↓
intel.events
  ↓
Factor State Engine
  ↓
decision.factor_states
  ↓
Conflict Rule Engine
  ↓
Correlation / Group Cap
  ↓
Asset Score Engine
  ↓
Confidence Engine
  ↓
decision.asset_scores
  ↓
Explanation LangGraph
  ↓
decision.reports
  ↓
Quality Gate
  ↓
Publish
  ↓
FastAPI query
  ↓
React Dashboard
```

---

# 33. 管理员“更新并分析”调用链

```text
Admin React
  ↓
POST /api/v1/admin/analysis/run
  ↓
FastAPI:
  auth ADMIN
  validate request
  ↓
Prefect Deployment API
  ↓
create Flow Run
  ↓
return analysis_run_id + prefect_flow_run_id
  ↓
Frontend polling / SSE status
  ↓
Preview result
  ↓
Admin Publish
```

FastAPI 不等待分析完成。

---

# 34. Run 状态模型

WideGold 内建议：

```text
PENDING
RUNNING
DATA_READY
EVENTS_READY
SCORED
QUALITY_FAILED
PREVIEW_READY
PUBLISHED
FAILED
CANCELLED
```

Prefect 仍有自己的 State。

两者区别：

```text
Prefect State
= 技术执行状态

WideGold Analysis Status
= 业务分析生命周期
```

不要混为一张表。

---

# 35. 幂等与重复运行

必须保证管理员连点两次不会污染数据库。

核心 Key：

```text
analysis_date
run_mode
version_snapshot
data_cutoff_ts
```

使用：

```text
Redis short lock
+
PostgreSQL unique / transaction
```

原始 Observation 推荐 UPSERT。

Score 永远新建 snapshot，不覆盖旧 Score。

发布关系：

```text
score_v1
 ↓ superseded by
score_v2
```

旧结果永久可追溯。

---

# 36. Version Snapshot

一次 Flow 启动时立即冻结：

```yaml
VersionSnapshot:
  factor_schema_version:
  factor_logic_version:
  weight_version:
  event_rule_version:
  prompt_version:
  scoring_engine_version:
  data_definition_version:

  qwen_model_alias:
  deepseek_model_alias:
  gpt_model_alias:
```

Flow 中途即使管理员修改配置：

```text
当前 Run 不受影响
下一 Run 才生效
```

---

# 37. Prompt 版本

Prompt 不能只是：

```text
prompts/event.txt
```

需要：

```text
prompt_id
prompt_version
task_type
model_profile
template
output_schema_version
effective_from
checksum
```

至少生产时把：

```text
prompt_version
```

写入 Event / Explanation / Research。

---

# 38. 配置管理

建议：

## YAML 管“默认和版本化规则”

```text
assets.yaml
factors.yaml
weights.yaml
rules.yaml
models.yaml
data_sources.yaml
```

## PostgreSQL 管“当前已批准版本”

原因：

以后 Admin 修改 Candidate Rule 后，需要审批和有效期。

不能只改服务器上的 YAML。

最终模式：

```text
Git YAML
= bootstrap / canonical baseline

DB Config Version
= runtime approved configuration
```

---

# 39. 鉴权建议

V1 不做 OAuth / 微信登录。

推荐：

```text
用户名 + 密码
Argon2/bcrypt hash
HttpOnly Secure Session Cookie
ADMIN / USER
```

同域部署下优先 Cookie Session，而不是前端长期保存 JWT 到 localStorage。

管理员所有：

```text
trigger
publish
rule approval
weight approval
research approval
```

写 Audit Log。

---

# 40. Observability

第一阶段不必引入 ELK / Grafana 全家桶。

先保留：

```text
Prefect UI
+
结构化 JSON logs
+
ops.provider_health
+
ops.analysis_runs
+
intel.llm_runs
```

日志统一字段：

```text
request_id
analysis_run_id
prefect_flow_run_id
graph_thread_id
event_id
asset_id
provider
model_alias
```

以后再接 OpenTelemetry/Grafana。

---

# 41. LangGraph Persistence

生产 Graph：

```text
Event Graph
Research Graph
Scenario Graph
```

均使用 PostgreSQL-backed checkpointer。

但策略不同。

### Event Graph

短生命周期：

```text
保留 checkpoint 30～90 天
```

### Research Graph

长期保留：

```text
支持 human interrupt
支持恢复
支持审计
```

### Explanation Graph

可以不需要完整长期历史，必要时使用 shallow / 短期策略。

LangGraph 当前 Persistence 明确区分 thread-scoped checkpoint 与跨 thread store，checkpoint 可用于恢复、中断、human-in-the-loop 和 fault tolerance。[^6]

---

# 42. 不引入的技术

V1 明确不使用：

```text
Kafka
RabbitMQ
Celery
Kubernetes
MongoDB
Elasticsearch
Milvus
Qdrant
TimescaleDB
LangSmith 强依赖
微服务 RPC
Service Mesh
```

不是这些技术不好，而是目前没有需求证明需要。

---

# 43. V1 Docker Compose

```text
nginx
│
├─ widegold-web
└─ widegold-api
      │
      ├──────── PostgreSQL / widegold
      └──────── Redis

prefect-server
      │
      ├──────── PostgreSQL / prefect
      └──────── Redis
             ↑
       prefect-worker
             │
             ├─ WideGold Core
             ├─ Data Providers
             ├─ LangGraph
             ├─ Qwen
             ├─ DeepSeek
             └─ GPT Compatible API

LangGraph
      │
      └──────── PostgreSQL / langgraph
```

一个 PostgreSQL container 即可建立三个 database。

---

# 44. 开发环境

本地开发建议：

```text
PostgreSQL + Redis
    ↓ Docker

FastAPI
Prefect worker
React
    ↓ 本机运行
```

不要求开发时每次修改代码都重建全部容器。

正式测试：

```text
docker compose up
```

验证 Win10/Linux 一致性。

---

# 45. API 与 Prefect 的关系

API 不 import 然后直接运行：

```python
analysis_pipeline()
```

生产环境推荐：

```text
FastAPI
 ↓
Prefect Deployment
 ↓
Flow Run
```

这样手动触发也有：

- run history；
- cancel；
- retry；
- parameters；
- status；
- UI observability。

Prefect 官方 Flow 文档明确支持通过 deployment 创建 ad-hoc run，并通过 schedule 触发同一 Flow。[^3]

---

# 46. Scenario 是否走 Prefect

普通用户即时 Scenario：

```text
FastAPI
 ↓
Scenario Graph
```

可以直接异步运行，不必一定经过 Prefect。

原因：

- 是一次用户请求；
- 时间较短；
- 不属于日常系统任务。

若 Scenario 后续变成长时间 Deep Research，再改为 Prefect Deployment。

---

# 47. Research 一定走 Prefect

因为 Factor Research：

- 时间长；
- 多次搜索；
- 多模型；
- 可能中断；
- human review；
- 需要 retry。

推荐：

```text
Admin
 ↓
FastAPI
 ↓
Prefect factor_research_flow
 ↓
LangGraph Research Graph
 ↓
interrupt
 ↓
Admin Review
 ↓
resume
```

---

# 48. 测试策略

至少四层。

## Unit

测试 Deterministic Core：

```text
Factor State
Decay
Conflict
Group Cap
Score
Confidence
```

不调用网络和 LLM。

---

## Contract

验证 Provider / LLM Structured Output。

```text
ProviderResult schema
Event schema
FactorMapping schema
```

---

## Integration

```text
Data → Event → Factor → Score
```

使用固定 fixture。

---

## Replay

将准则 V1 五个标准场景变成 Regression Test。

每次：

```text
模型
Prompt
Rule
Weight
```

修改后全部重放。

---

# 49. 第一阶段开发顺序

正式编码时不建议从 UI 开始。

```text
Phase 1
Schemas + Domain + Config Version

Phase 2
PostgreSQL + Repository + Point-in-Time

Phase 3
Provider Registry + Data Validation

Phase 4
Deterministic Factor / Score Core

Phase 5
LLM Router
Qwen / DeepSeek / GPT adapters

Phase 6
Event Intelligence Graph

Phase 7
Prefect Analysis Pipeline

Phase 8
Explanation Graph

Phase 9
FastAPI

Phase 10
Dashboard

Phase 11
Factor Research Graph

Phase 12
Replay / Calibration
```

这样每一步都有可测试输入输出。

---

# 50. 目前已经可以正式敲定的选项

以下建议直接视为 V1 Approved：

| 项目 | 结论 |
|---|---|
| 系统调度 | Prefect 3 |
| Agent 编排 | LangGraph |
| API | FastAPI |
| Schema | Pydantic v2 |
| ORM | SQLAlchemy 2 |
| Migration | Alembic |
| Database | PostgreSQL |
| Cache | Redis |
| Frontend | React + Vite + TypeScript |
| Chart | ECharts |
| Python | 3.11 |
| Package | uv |
| Rule Engine | 自研 deterministic Python core |
| 自动分析 | 收盘后一次 |
| 人工运行 | Admin 点击触发 |
| 模型 L1 | Qwen |
| 模型 L2 | DeepSeek |
| 模型 L3 | 第三方 GPT / gpt-5.6-sol |
| 数据采集 | 非 Agent |
| 最终评分 | 非 LLM |
| 部署 | Docker Compose |
| 架构形态 | 模块化单体 |
| Prefect Worker | 唯一主要异步业务执行进程 |
| LangGraph Persistence | PostgreSQL |
| 生产规则修改 | 人工审核 |
| Point-in-time | 必须 |
| Run snapshot | 不覆盖、可追溯 |

---

# 51. 仍建议由项目负责人敲定的 4 个选项

这些不会阻止架构设计，但在开始编码前最好明确。

## D1：每天几点出正式结果？

### A — 16:30

优点：

- 用户较早看到结果。

缺点：

- 部分日终数据可能尚未完整。

### B — 18:10【推荐】

优点：

- A股数据稳定；
- 给 ETF / 融资 / 债券等日终数据足够缓冲；
- 晚饭前后即可查看。

### C — 21:00

优点：

- 能覆盖更多晚间信息和欧洲交易时段。

缺点：

- “收盘分析”反馈太晚。

**建议：B。**

---

## D2：管理员手动分析是否自动覆盖正式 Dashboard？

### A — 自动覆盖

简单，但风险较大。

### B — 先 Preview，管理员再点 Publish【推荐】

最安全、最适合测试 Rule / Prompt / Model。

### C — 只有指定 Super Admin 才可自动覆盖

适合以后多管理员。

**建议：B。**

---

## D3：Factor Research 的互联网搜索源

这和日常新闻数据源是两个问题。

### A — 单独购买搜索 API

例如采用可替换 `SearchProvider`。

优点：

- 开发最简单；
- 搜索质量容易保证。

### B — 自建 SearXNG

优点：

- 自托管；
- 长期成本较低。

缺点：

- 搜索稳定性和反爬维护更复杂。

### C — 第一阶段只研究人工给定 URL / 文献

最简单，但无法自动深度研究。

**建议：A，并保持 `SearchProvider` 抽象，具体供应商可以后定。**

---

## D4：第三方 GPT 失败时怎么处理高等级任务？

### A — GPT失败 → DeepSeek high 再尝试【推荐】

系统可用性最高。

### B — GPT失败直接标记专家审查失败

最严格。

### C — GPT失败后切另一个第三方 GPT endpoint

以后更适合生产。

**V1 建议 A；V2 可升级 C。**

---

# 52. 推荐最终选择

如果不需要额外调整，我建议：

```text
D1 = B
18:10 Asia/Shanghai

D2 = B
Manual Run → Preview → Explicit Publish

D3 = A
SearchProvider 抽象 + 搜索 API

D4 = A
GPT Failure → DeepSeek High Fallback
```

即：

```text
B / B / A / A
```

---

# 53. 最终系统心智模型

今后开发时，任何功能都先判断它属于哪一层：

```text
事实是什么？
→ Data

非结构化事实意味着什么？
→ LangGraph

根据准则应该怎么算？
→ Deterministic Engine

什么时候运行？
→ Prefect

结果怎么保存？
→ Repository / PostgreSQL

怎么提供给界面？
→ FastAPI

怎么让小白理解？
→ Explanation Graph / React
```

如果某段代码同时回答其中三个以上问题，通常意味着边界正在被破坏。

---

# 54. 最终主链

```text
                18:10 / Admin Trigger
                         │
                      Prefect
                         │
                  AnalysisContext
                         │
            ┌────────────┴────────────┐
            ↓                         ↓
       Structured Data              Documents
            │                         │
            │                 Event Intelligence
            │                      LangGraph
            │                         │
            └────────────┬────────────┘
                         ↓
                   Factor State
                         ↓
                   Conflict Rules
                         ↓
                 Correlation / Caps
                         ↓
                    Asset Score
                         ↓
                    Confidence
                         ↓
                   Quality Gate
                         ↓
                 Explanation Graph
                         ↓
                  Immutable Snapshot
                         ↓
                 Publish / Preview
                         ↓
                      FastAPI
                         ↓
                    Dashboard
                         ↓
              T+1 / T+5 / T+20 Review
```

这就是 WideGold Agent V1 建议进入正式开发的系统框架。

---

# 55. 后续下一份设计文档

在 D1～D4 最终确认后，下一步不建议再扩展架构，而应进入：

**《WideGold V1 数据模型与接口契约设计》**

重点敲定：

```text
1. PostgreSQL DDL 级表结构
2. Pydantic Schema 精确字段
3. REST API Contract
4. Prefect Flow 参数
5. LangGraph State 类型
6. Config YAML 完整 Schema
7. Event / Factor / Score Enum
8. Error Code
9. Version / Audit Contract
```

到那一步完成后，再进入代码骨架生成最合适。

---

# References

[^1]: Alibaba Cloud Model Studio, “[OpenAI-compatible Qwen API](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope),” updated September 4, 2026. Qwen supports OpenAI-compatible interfaces by changing API key, base URL and model name.
[^2]: DeepSeek, “[Your First API Call](https://api-docs.deepseek.com/),” accessed September 2026. The current API supports OpenAI/Anthropic-compatible access; official model documentation also lists JSON Output and Tool Calls support.
[^3]: Prefect, “[Flows](https://docs.prefect.io/v3/concepts/flows),” accessed September 2026. Flows track execution state and support retries, timeouts, deployments, remote/ad-hoc runs and schedules.
[^4]: FastAPI, “[Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/),” accessed September 2026. The documentation notes that heavier background computation can benefit from separate multi-process/server task tooling rather than in-process background tasks.
[^5]: LangChain, “[LangGraph PostgresSaver](https://reference.langchain.com/python/langgraph/checkpoint.postgres/PostgresSaver),” accessed September 2026. PostgresSaver persists LangGraph checkpoints in PostgreSQL and is documented for production checkpoint history.
[^6]: LangChain, “[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence),” accessed September 2026. LangGraph checkpointers support thread-scoped persistence for resumption, human-in-the-loop, time travel and fault tolerance.
