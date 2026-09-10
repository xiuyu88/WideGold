# WideGold V1 数据模型与接口契约设计

> 本文在《WideGold V1 系统技术架构与 Workflow 详细设计》基础上，进一步固定数据库模型、Pydantic Schema、REST API、Prefect Flow 参数、LangGraph State、Enum、错误码、版本和审计契约。  
> 本文目标是让下一阶段可以直接进入代码骨架生成，而不再讨论宏观架构。

---

# 1. 已锁定的产品与系统决策

以下选择视为 V1 已批准：

```text
D1 = B
正式收盘分析时间：18:10 Asia/Shanghai

D2 = B
管理员手动分析：Preview → Explicit Publish

D3 = A
Factor Research：SearchProvider 抽象 + 搜索 API

D4 = A
第三方 GPT 专家层失败：
回退 DeepSeek high
```

其他已批准系统决策：

```text
Backend         FastAPI
Workflow        Prefect 3
Agent Workflow  LangGraph
Database        PostgreSQL
Cache           Redis
ORM             SQLAlchemy 2
Migration       Alembic
Schema          Pydantic v2
Frontend        React + Vite + TypeScript
Chart           ECharts
Package         uv
Python          3.11
Deploy          Docker Compose
Architecture    Modular Monolith
```

模型能力分级：

```text
L1  Qwen
L2  DeepSeek
L3  Third-party GPT / gpt-5.6-sol
```

---

# 2. 数据域与 PostgreSQL Schema

WideGold 业务库采用以下 Schema：

```text
iam
reference
raw
market
intel
decision
research
ops
scenario
```

其中：

```text
iam
= 用户、角色、会话

reference
= Asset / Factor / Weight / Rule / Source / Version

raw
= 原始新闻、公告、原始响应

market
= 行情、宏观、利率、汇率、ETF、两融、市场广度

intel
= 新闻聚类、结构化事件、事件因子映射、LLM调用

decision
= Factor State、资产评分、贡献、Confidence、Report

research
= Factor Research、证据、Candidate、审批

ops
= Analysis Run、Provider Health、Audit、Publish

scenario
= 用户情景推演结果
```

---

# 3. 通用字段规范

## 3.1 ID

所有业务实体统一：

```text
UUID v7 优先
```

如果当前库或 Python 生态实现不方便，可以 V1 使用 UUID4。

禁止：

```text
自增 ID 作为跨系统唯一业务标识
```

自增 bigint 可以作为内部 surrogate key，但 API 对外全部使用 UUID。

---

## 3.2 时间字段

统一规则：

```text
PostgreSQL:
TIMESTAMPTZ

API:
ISO-8601 + timezone

系统默认业务时区:
Asia/Shanghai
```

禁止保存无时区 datetime。

---

## 3.3 金额 / 比率 / 得分

```text
价格:
NUMERIC / DOUBLE PRECISION 按数据类型决定

FactorState:
DOUBLE PRECISION
范围 -100 ~ +100

Confidence:
DOUBLE PRECISION
范围 0 ~ 100

Probability / confidence:
DOUBLE PRECISION
范围 0 ~ 1
```

---

## 3.4 JSON

允许 JSONB 的场景：

```text
不稳定第三方 metadata
asset_override
risk_flags details
raw provider response metadata
LLM provider metadata
```

禁止把所有结构化业务字段都塞进 JSONB。

---

# 4. Enum 契约

建议同时定义：

```text
Python Enum
PostgreSQL CHECK / VARCHAR
TypeScript union
```

第一阶段不强依赖 PostgreSQL ENUM，避免 migration 复杂。

---

## 4.1 UserRole

```text
ADMIN
USER
```

---

## 4.2 TriggerType

```text
SCHEDULED
ADMIN_MANUAL
SYSTEM_RETRY
REPLAY
```

---

## 4.3 AnalysisRunMode

```text
FULL_REFRESH
REANALYZE
DATA_ONLY
REPLAY
```

---

## 4.4 PublishMode

```text
AUTO
PREVIEW_ONLY
EXPLICIT_PUBLISH
```

规则：

```text
Scheduled FULL_REFRESH
→ AUTO

Admin FULL_REFRESH
→ PREVIEW_ONLY

Admin REANALYZE
→ PREVIEW_ONLY
```

---

## 4.5 AnalysisStatus

```text
PENDING
RUNNING
DATA_READY
EVENTS_READY
FACTORS_READY
SCORED
QUALITY_FAILED
PREVIEW_READY
PUBLISHED
FAILED
CANCELLED
SKIPPED
```

---

## 4.6 DataStatus

```text
VALID
STALE
UNAVAILABLE
PARTIAL
CONFLICTED
```

---

## 4.7 FactorLifecycle

```text
CANDIDATE
RESEARCH
SHADOW
APPROVED
DEPRECATED
```

---

## 4.8 EvidenceGrade

```text
A
B
C
D
```

---

## 4.9 SourceTier

```text
S
A
B
C
D
```

---

## 4.10 Horizon

```text
TACTICAL
SWING
STRATEGIC
```

---

## 4.11 EventVerificationStatus

```text
UNVERIFIED
PARTIALLY_VERIFIED
VERIFIED
REJECTED
```

---

## 4.12 ModelTier

```text
L1
L2
L3
```

---

## 4.13 ModelTaskType

```text
EXTRACTION
CLASSIFICATION
REASONING
EXPLANATION
RESEARCH
EXPERT_REVIEW
```

---

# 5. IAM 数据模型

## 5.1 `iam.users`

字段：

```text
user_id              UUID PK
username             VARCHAR(64) UNIQUE NOT NULL
password_hash        TEXT NOT NULL
display_name         VARCHAR(128)
email                VARCHAR(255) NULL
is_active            BOOLEAN DEFAULT TRUE
created_at           TIMESTAMPTZ
updated_at           TIMESTAMPTZ
last_login_at        TIMESTAMPTZ NULL
```

---

## 5.2 `iam.roles`

```text
role_code            VARCHAR(32) PK
name                 VARCHAR(64)
```

默认：

```text
ADMIN
USER
```

---

## 5.3 `iam.user_roles`

```text
user_id              UUID FK
role_code            VARCHAR(32) FK
PRIMARY KEY(user_id, role_code)
```

---

# 6. Reference 数据模型

## 6.1 `reference.assets`

```text
asset_id              VARCHAR(64) PK
name                  VARCHAR(128)
asset_class           VARCHAR(32)
currency              VARCHAR(16)
index_code            VARCHAR(32) NULL
exchange              VARCHAR(32) NULL
active                BOOLEAN
display_order         INT
metadata_json         JSONB
created_at            TIMESTAMPTZ
updated_at            TIMESTAMPTZ
```

V1：

```text
CSI300
CSI_A500
CSI500
CSI1000
CHINEXT
STAR50
RMB_GOLD
```

---

## 6.2 `reference.factor_definitions`

```text
factor_id              VARCHAR(64) PK
name                   VARCHAR(128)
family                 VARCHAR(32)
category               VARCHAR(64)

semantic_positive      TEXT
semantic_negative      TEXT

evidence_grade         VARCHAR(8)
lifecycle              VARCHAR(16)

correlation_group      VARCHAR(64)
update_frequency       VARCHAR(32)
max_age_seconds        BIGINT NULL

logic_version          VARCHAR(32)

production_enabled     BOOLEAN

created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

---

## 6.3 `reference.asset_factor_weights`

```text
weight_id              UUID PK

asset_id               VARCHAR(64) FK
factor_id              VARCHAR(64) FK

horizon                VARCHAR(16)

sensitivity            DOUBLE PRECISION
max_contribution       DOUBLE PRECISION NULL

weight_version         VARCHAR(32)

effective_from         TIMESTAMPTZ
effective_to           TIMESTAMPTZ NULL

rationale              TEXT
approved_by            UUID NULL
approved_at            TIMESTAMPTZ NULL

created_at             TIMESTAMPTZ
```

约束：

```text
同一 asset/factor/horizon/effective time
只能有一个 production active weight
```

---

## 6.4 `reference.data_sources`

```text
source_id              VARCHAR(64) PK
name                   VARCHAR(128)
source_type            VARCHAR(32)
source_tier            VARCHAR(8)

provider_key           VARCHAR(64)
base_url               TEXT NULL

priority               INT
active                 BOOLEAN

license_notes          TEXT NULL
metadata_json          JSONB

created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

---

## 6.5 `reference.config_versions`

```text
config_version_id      UUID PK

config_type            VARCHAR(32)
version                VARCHAR(32)

status                 VARCHAR(16)

content_hash           VARCHAR(128)
content_json           JSONB

effective_from         TIMESTAMPTZ
effective_to           TIMESTAMPTZ NULL

created_by             UUID NULL
approved_by            UUID NULL

created_at             TIMESTAMPTZ
approved_at            TIMESTAMPTZ NULL
```

`config_type`：

```text
FACTOR_SCHEMA
FACTOR_LOGIC
WEIGHTS
EVENT_RULES
PROMPTS
DATA_DEFINITION
SCORING_ENGINE
MODEL_ROUTING
```

---

# 7. Raw 数据模型

## 7.1 `raw.documents`

```text
document_id            UUID PK

source_id              VARCHAR(64)
source_url             TEXT NULL

document_type          VARCHAR(32)
title                  TEXT
raw_text               TEXT

language               VARCHAR(16)

published_at           TIMESTAMPTZ NULL
retrieved_at           TIMESTAMPTZ

content_hash           VARCHAR(128)

raw_metadata           JSONB

created_at             TIMESTAMPTZ
```

唯一性建议：

```text
UNIQUE(source_id, content_hash)
```

---

## 7.2 `raw.provider_payloads`

只对必要的数据源留档。

```text
payload_id             UUID PK
source_id              VARCHAR(64)
dataset                VARCHAR(64)
request_key            VARCHAR(256)
retrieved_at           TIMESTAMPTZ
checksum               VARCHAR(128)
payload_json           JSONB
expires_at             TIMESTAMPTZ NULL
```

---

# 8. Market 数据模型

## 8.1 `market.prices_daily`

```text
instrument_id          VARCHAR(64)
trade_date             DATE

open                   DOUBLE PRECISION
high                   DOUBLE PRECISION
low                    DOUBLE PRECISION
close                  DOUBLE PRECISION

volume                 DOUBLE PRECISION NULL
turnover               DOUBLE PRECISION NULL

source_id              VARCHAR(64)
ingest_ts              TIMESTAMPTZ

PRIMARY KEY(instrument_id, trade_date, source_id)
```

---

## 8.2 `market.macro_observations`

这是最重要的 point-in-time 表之一。

```text
observation_id         UUID PK

series_id              VARCHAR(64)
observation_date       DATE

release_ts             TIMESTAMPTZ
ingest_ts              TIMESTAMPTZ

value                  DOUBLE PRECISION

revision_vintage       VARCHAR(64)
definition_version     VARCHAR(64)

is_preliminary         BOOLEAN

source_id              VARCHAR(64)

metadata_json          JSONB
```

索引：

```text
(series_id, observation_date)
(series_id, release_ts)
```

---

## 8.3 `market.rates_daily`

```text
series_id
trade_date
value
source_id
ingest_ts
```

---

## 8.4 `market.fx_daily`

```text
pair
trade_date
open
high
low
close
source_id
ingest_ts
```

---

## 8.5 `market.etf_flows`

```text
etf_id
trade_date
shares
shares_change
estimated_flow
aum
source_id
ingest_ts
```

注意：

```text
flow 不能简单由 AUM 变化替代
```

---

## 8.6 `market.margin_daily`

```text
market_code
trade_date
financing_balance
financing_buy
securities_lending_balance
source_id
ingest_ts
```

---

## 8.7 `market.breadth_daily`

```text
trade_date
universe

advancers
decliners
unchanged

new_highs
new_lows

limit_up
limit_down

turnover
median_return
equal_weight_return
cap_weight_return

source_id
ingest_ts
```

---

## 8.8 `market.index_exposures`

```text
asset_id
as_of_date
exposure_type
exposure_code
weight
source_id
source_version
ingest_ts
```

`exposure_type`：

```text
INDUSTRY
SIZE
STYLE
TOP_HOLDING
```

---

# 9. Intel 数据模型

## 9.1 `intel.news_clusters`

```text
cluster_id             UUID PK

analysis_date          DATE

canonical_title        TEXT

first_seen_at          TIMESTAMPTZ
last_seen_at           TIMESTAMPTZ

document_count         INT

cluster_hash           VARCHAR(128)

status                 VARCHAR(16)

created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

---

## 9.2 `intel.cluster_documents`

```text
cluster_id             UUID
document_id            UUID
similarity_score       DOUBLE PRECISION NULL

PRIMARY KEY(cluster_id, document_id)
```

---

## 9.3 `intel.events`

```text
event_id               UUID PK
cluster_id             UUID NULL

analysis_run_id        UUID

event_type             VARCHAR(64)
canonical_title        TEXT

published_at           TIMESTAMPTZ
effective_at           TIMESTAMPTZ NULL
ingest_at              TIMESTAMPTZ

verification_status    VARCHAR(32)
source_tier            VARCHAR(8)

strength               SMALLINT
confidence             DOUBLE PRECISION
novelty                DOUBLE PRECISION
priced_in              DOUBLE PRECISION
implementation         DOUBLE PRECISION

horizon                VARCHAR(16)
half_life_days         DOUBLE PRECISION

parent_event_id        UUID NULL

graph_version          VARCHAR(32)
prompt_version         VARCHAR(32)

status                 VARCHAR(16)

created_at             TIMESTAMPTZ
```

检查约束：

```text
strength 1..5
confidence 0..1
novelty 0..1
priced_in 0..1
implementation 0..1
half_life_days >= 0
```

---

## 9.4 `intel.event_factor_links`

```text
link_id                UUID PK

event_id               UUID
factor_id              VARCHAR(64)

direction              SMALLINT
mapping_confidence     DOUBLE PRECISION

reason_tags            JSONB
asset_override_json    JSONB NULL

created_at             TIMESTAMPTZ
```

---

## 9.5 `intel.llm_runs`

```text
llm_run_id             UUID PK

analysis_run_id        UUID NULL
graph_name             VARCHAR(64) NULL
node_name              VARCHAR(64) NULL

task_type              VARCHAR(32)
model_tier             VARCHAR(8)

provider               VARCHAR(64)
model_alias            VARCHAR(64)
resolved_model         VARCHAR(128)

reasoning_effort       VARCHAR(16)

request_id             VARCHAR(128) NULL

started_at             TIMESTAMPTZ
finished_at            TIMESTAMPTZ

latency_ms             INT

input_tokens           INT NULL
output_tokens          INT NULL

schema_valid           BOOLEAN

retry_count            INT
fallback_from          VARCHAR(64) NULL

status                 VARCHAR(16)
error_code             VARCHAR(64) NULL
error_message          TEXT NULL

metadata_json          JSONB
```

---

# 10. Decision 数据模型

## 10.1 `decision.factor_states`

```text
factor_state_id        UUID PK

analysis_run_id        UUID
factor_id              VARCHAR(64)

as_of_ts               TIMESTAMPTZ

state                  DOUBLE PRECISION

level_score            DOUBLE PRECISION NULL
trend_score            DOUBLE PRECISION NULL
surprise_score         DOUBLE PRECISION NULL
event_score            DOUBLE PRECISION NULL

reliability            DOUBLE PRECISION
coverage               DOUBLE PRECISION

status                 VARCHAR(16)

logic_version          VARCHAR(32)
data_vintage           VARCHAR(64) NULL

created_at             TIMESTAMPTZ
```

---

## 10.2 `decision.asset_scores`

```text
asset_score_id         UUID PK

analysis_run_id        UUID
asset_id               VARCHAR(64)

as_of_ts               TIMESTAMPTZ

tactical_score         DOUBLE PRECISION
swing_score            DOUBLE PRECISION
strategic_score        DOUBLE PRECISION

direction_score        DOUBLE PRECISION
asset_score            DOUBLE PRECISION

label                  VARCHAR(32)

confidence             DOUBLE PRECISION
coverage               DOUBLE PRECISION

weight_version         VARCHAR(32)
logic_version          VARCHAR(32)
scoring_engine_version VARCHAR(32)

published              BOOLEAN DEFAULT FALSE

supersedes_score_id    UUID NULL

created_at             TIMESTAMPTZ
published_at           TIMESTAMPTZ NULL
```

---

## 10.3 `decision.score_contributions`

```text
contribution_id        UUID PK

asset_score_id         UUID
factor_id              VARCHAR(64)

factor_state           DOUBLE PRECISION
sensitivity            DOUBLE PRECISION
reliability            DOUBLE PRECISION

raw_contribution       DOUBLE PRECISION
after_conflict         DOUBLE PRECISION
after_group_cap        DOUBLE PRECISION

rank_positive          INT NULL
rank_negative          INT NULL

explanation_key        VARCHAR(128) NULL
```

---

## 10.4 `decision.risk_flags`

```text
risk_flag_id           UUID PK

asset_score_id         UUID

risk_code              VARCHAR(64)
severity               VARCHAR(16)
message                TEXT

related_factor_ids     JSONB
related_event_ids      JSONB

created_at             TIMESTAMPTZ
```

---

## 10.5 `decision.reports`

```text
report_id              UUID PK

analysis_run_id        UUID
report_type            VARCHAR(32)

asset_id               VARCHAR(64) NULL

title                  TEXT
summary                TEXT
body_markdown          TEXT

model_execution_id     UUID NULL

prompt_version         VARCHAR(32)

published              BOOLEAN
created_at             TIMESTAMPTZ
```

---

# 11. Research 数据模型

## 11.1 `research.requests`

```text
research_request_id    UUID PK

request_type           VARCHAR(32)

target_factor_id       VARCHAR(64) NULL
title                  TEXT
question               TEXT

requested_by           UUID
created_at             TIMESTAMPTZ

status                 VARCHAR(16)

prefect_flow_run_id    UUID NULL
langgraph_thread_id    VARCHAR(128) NULL
```

---

## 11.2 `research.evidence_items`

```text
evidence_id            UUID PK

research_request_id    UUID

source_type            VARCHAR(32)
source_tier            VARCHAR(8)

title                  TEXT
url                    TEXT NULL

published_at           TIMESTAMPTZ NULL
retrieved_at           TIMESTAMPTZ

supports               BOOLEAN NULL

claim_summary          TEXT
evidence_summary       TEXT

quality_score          DOUBLE PRECISION

metadata_json          JSONB
```

---

## 11.3 `research.factor_candidates`

```text
candidate_id           UUID PK

research_request_id    UUID

target_factor_id       VARCHAR(64) NULL

candidate_type         VARCHAR(32)

hypothesis             TEXT
mechanism              TEXT

evidence_grade         VARCHAR(8)

proposed_config_patch  JSONB

validation_required    JSONB

status                 VARCHAR(16)

production_enabled     BOOLEAN DEFAULT FALSE

created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

`candidate_type`：

```text
NEW_FACTOR
LOGIC_CHANGE
SOURCE_CHANGE
WEIGHT_HYPOTHESIS
CONFLICT_RULE
```

---

## 11.4 `research.approval_records`

```text
approval_id            UUID PK
candidate_id           UUID

action                 VARCHAR(16)

reviewer_id            UUID
comment                TEXT

created_at             TIMESTAMPTZ
```

action：

```text
APPROVE_SHADOW
APPROVE_PRODUCTION
REVISE
REJECT
DEPRECATE
```

---

# 12. Ops 数据模型

## 12.1 `ops.analysis_runs`

这是整个系统最重要的审计表。

```text
analysis_run_id        UUID PK

prefect_flow_run_id    UUID NULL

analysis_date          DATE
as_of_ts               TIMESTAMPTZ
data_cutoff_ts         TIMESTAMPTZ

trigger_type           VARCHAR(32)
run_mode               VARCHAR(32)
publish_mode           VARCHAR(32)

status                 VARCHAR(32)

requested_by           UUID NULL
base_run_id            UUID NULL

factor_schema_version  VARCHAR(32)
factor_logic_version   VARCHAR(32)
weight_version         VARCHAR(32)
event_rule_version     VARCHAR(32)
prompt_version         VARCHAR(32)
scoring_version        VARCHAR(32)
data_definition_version VARCHAR(32)
model_routing_version  VARCHAR(32)

started_at             TIMESTAMPTZ
finished_at            TIMESTAMPTZ NULL

coverage               DOUBLE PRECISION NULL

error_code             VARCHAR(64) NULL
error_summary          TEXT NULL

created_at             TIMESTAMPTZ
```

---

## 12.2 `ops.data_fetch_runs`

```text
fetch_run_id           UUID PK

analysis_run_id        UUID

dataset                VARCHAR(64)
provider               VARCHAR(64)

started_at
finished_at

status

rows_count
cache_hit
fallback_used

error_code
error_message
```

---

## 12.3 `ops.provider_health`

```text
provider_key
checked_at
status
latency_ms
last_success_at
consecutive_failures
details_json
```

---

## 12.4 `ops.audit_logs`

```text
audit_id               UUID PK

actor_user_id          UUID NULL

action                 VARCHAR(64)
resource_type          VARCHAR(64)
resource_id            VARCHAR(128)

before_json            JSONB NULL
after_json             JSONB NULL

request_id             VARCHAR(128) NULL

created_at             TIMESTAMPTZ
```

必须记录：

```text
ADMIN_RUN_ANALYSIS
ADMIN_PUBLISH
ADMIN_APPROVE_FACTOR
ADMIN_APPROVE_WEIGHT
ADMIN_CHANGE_MODEL_ROUTING
ADMIN_CHANGE_CONFIG
```

---

## 12.5 `ops.publish_history`

```text
publish_id             UUID PK

analysis_run_id        UUID
published_by           UUID NULL

publish_type           VARCHAR(32)

previous_run_id        UUID NULL

created_at             TIMESTAMPTZ
```

---

# 13. Scenario 数据模型

## 13.1 `scenario.runs`

```text
scenario_run_id        UUID PK

user_id                UUID NULL

created_at             TIMESTAMPTZ

assumption_text        TEXT

base_analysis_run_id   UUID

status                 VARCHAR(16)

graph_thread_id        VARCHAR(128) NULL

result_json            JSONB
```

Scenario 结果禁止写入正式：

```text
decision.asset_scores
```

---

# 14. Pydantic Schema 基线

所有 API / Graph / Engine 边界使用强类型 Pydantic。

---

## 14.1 `VersionSnapshot`

```python
class VersionSnapshot(BaseModel):
    factor_schema: str
    factor_logic: str
    weights: str
    event_rules: str
    prompts: str
    scoring: str
    data_definition: str
    model_routing: str
```

---

## 14.2 `AnalysisRunRequest`

```python
class AnalysisRunRequest(BaseModel):
    analysis_date: date | None = None

    trigger_type: TriggerType
    run_mode: AnalysisRunMode

    publish_mode: PublishMode

    force_refresh: bool = False

    base_run_id: UUID | None = None

    selected_assets: list[str] | None = None
    selected_factors: list[str] | None = None

    requested_by: UUID | None = None
```

规则：

```text
REANALYZE
必须提供 base_run_id

SCHEDULED
不能 requested_by

ADMIN_MANUAL
必须 requested_by
```

---

## 14.3 `AnalysisContext`

```python
class AnalysisContext(BaseModel):
    analysis_run_id: UUID

    analysis_date: date
    as_of_ts: datetime
    data_cutoff_ts: datetime

    trigger_type: TriggerType
    run_mode: AnalysisRunMode
    publish_mode: PublishMode

    requested_by: UUID | None

    versions: VersionSnapshot
```

---

## 14.4 `ProviderRequest`

```python
class ProviderRequest(BaseModel):
    dataset: str

    start: date | datetime | None
    end: date | datetime | None

    as_of: datetime

    force_refresh: bool = False

    params: dict[str, Any] = {}
```

---

## 14.5 `ProviderResult`

```python
class ProviderResult(BaseModel):
    provider: str
    source_id: str

    retrieved_at: datetime

    rows_count: int

    checksum: str | None

    cache_hit: bool
    fallback_used: bool

    status: DataStatus

    warnings: list[str]

    data: list[dict[str, Any]]
```

内部可以进一步拆 dataset-specific schema。

---

# 15. News / Event Schema

## 15.1 `NewsDocument`

```python
class NewsDocument(BaseModel):
    document_id: UUID

    source_id: str
    source_tier: SourceTier

    url: str | None

    title: str
    content: str

    published_at: datetime | None
    retrieved_at: datetime

    language: str
```

---

## 15.2 `NewsCluster`

```python
class NewsCluster(BaseModel):
    cluster_id: UUID

    canonical_title: str

    documents: list[NewsDocument]

    first_seen_at: datetime
    last_seen_at: datetime
```

---

## 15.3 `EventExtractionOutput`

```python
class EventExtractionOutput(BaseModel):
    event_detected: bool

    event_type: str | None

    canonical_title: str | None

    entities: list[str]

    geography: list[str]

    effective_at: datetime | None

    facts: list[str]

    uncertainties: list[str]
```

---

## 15.4 `FactorMapping`

```python
class FactorMapping(BaseModel):
    factor_id: str

    direction: Literal[-1, 1]

    mapping_confidence: float

    reason_tags: list[str]
```

---

## 15.5 `FactorMappingOutput`

```python
class FactorMappingOutput(BaseModel):
    mappings: list[FactorMapping]

    unmapped_reasons: list[str]
```

---

## 15.6 `EventImpactAssessment`

```python
class EventImpactAssessment(BaseModel):
    strength: int
    confidence: float
    novelty: float
    priced_in: float
    implementation: float

    horizon: Horizon

    half_life_days: float

    asset_override_candidates: dict[str, float]

    reasoning_summary: str
```

---

## 15.7 `StructuredEvent`

```python
class StructuredEvent(BaseModel):
    event_id: UUID
    cluster_id: UUID | None

    event_type: str
    canonical_title: str

    published_at: datetime
    effective_at: datetime | None

    verification_status: EventVerificationStatus
    source_tier: SourceTier

    factors: list[FactorMapping]

    strength: int

    confidence: float
    novelty: float
    priced_in: float
    implementation: float

    horizon: Horizon
    half_life_days: float

    asset_overrides: dict[str, float]

    reason_tags: list[str]

    evidence_document_ids: list[UUID]

    model_execution_ids: list[UUID]

    graph_version: str
    prompt_version: str
```

---

# 16. Factor State Schema

```python
class FactorStateComponents(BaseModel):
    level: float | None
    trend: float | None
    surprise: float | None
    event: float | None
```

```python
class FactorState(BaseModel):
    factor_id: str

    as_of_ts: datetime

    state: float

    components: FactorStateComponents

    reliability: float
    coverage: float

    status: DataStatus

    evidence_refs: list[str]
    event_ids: list[UUID]

    logic_version: str
    data_vintage: str | None
```

约束：

```text
state ∈ [-100,100]
reliability ∈ [0,1]
coverage ∈ [0,1]
```

---

# 17. Scoring Schema

## 17.1 `FactorContribution`

```python
class FactorContribution(BaseModel):
    factor_id: str

    factor_state: float
    sensitivity: float
    reliability: float

    raw_contribution: float
    after_conflict: float
    after_group_cap: float

    rank_positive: int | None
    rank_negative: int | None
```

---

## 17.2 `ConfidenceResult`

```python
class ConfidenceResult(BaseModel):
    confidence: float

    coverage_score: float
    source_quality_score: float
    agreement_score: float
    stability_score: float
    context_certainty_score: float

    penalties: list[str]
    caps_applied: list[str]
```

---

## 17.3 `AssetScore`

```python
class AssetScore(BaseModel):
    asset_id: str

    as_of_ts: datetime

    tactical_score: float
    swing_score: float
    strategic_score: float

    direction_score: float
    score: float

    label: str

    confidence: ConfidenceResult

    contributions: list[FactorContribution]

    top_positive: list[str]
    top_negative: list[str]

    risk_flags: list[str]

    versions: VersionSnapshot
```

---

# 18. Explanation Schema

```python
class AssetExplanation(BaseModel):
    asset_id: str

    summary: str

    direction_reason: str

    top_positive: list[str]
    top_negative: list[str]

    biggest_risk: str

    confidence_explanation: str

    user_level: Literal["beginner"] = "beginner"

    source_event_ids: list[UUID]
    source_factor_ids: list[str]
```

Explanation Graph 只能基于这些输入生成。

---

# 19. LLM 路由 Schema

## 19.1 `ModelRequest`

```python
class ModelRequest(BaseModel):
    task_type: ModelTaskType

    preferred_tier: ModelTier

    reasoning_effort: Literal[
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh"
    ]

    require_json: bool = True
    require_tools: bool = False

    fallback_allowed: bool = True

    timeout_seconds: int | None = None
```

---

## 19.2 `ModelExecution`

```python
class ModelExecution(BaseModel):
    execution_id: UUID

    provider: str
    model_alias: str
    resolved_model: str

    tier: ModelTier

    task_type: ModelTaskType

    reasoning_effort: str

    latency_ms: int

    input_tokens: int | None
    output_tokens: int | None

    schema_valid: bool

    retry_count: int

    fallback_from: str | None

    status: str

    error_code: str | None
```

---

# 20. 模型路由规则 V1

## 20.1 L1 Qwen

默认任务：

```text
EXTRACTION
CLASSIFICATION
轻量 EXPLANATION
```

---

## 20.2 L2 DeepSeek

默认任务：

```text
REASONING
Factor Mapping
Event Impact
Conflict Analysis
日报解释
中等 Scenario
```

---

## 20.3 L3 GPT

只有以下条件进入：

```text
critical_conflict
high_impact_event + evidence_disagreement
DeepSeek schema repeated failure
DeepSeek low confidence
管理员 Expert Review
Factor Research expert synthesis
```

---

## 20.4 GPT 失败回退规则

已确认：

```text
GPT medium/high
    ↓ fail
DeepSeek high
    ↓
final structured output
```

若 DeepSeek high 仍失败：

```text
Event:
mark REVIEW_FAILED
production contribution = 0

Research:
keep draft
等待人工审核

Scenario:
返回“无法可靠完成该情景分析”
```

禁止自动跳过 validator。

---

# 21. SearchProvider 契约

Factor Research 使用：

```text
SearchProvider
```

抽象接口：

```python
class SearchRequest(BaseModel):
    query: str

    domains: list[str] | None = None

    date_from: date | None = None
    date_to: date | None = None

    language: str | None = None

    max_results: int = 10
```

```python
class SearchResult(BaseModel):
    title: str
    url: str

    snippet: str | None

    published_at: datetime | None

    source_domain: str

    provider_rank: int

    metadata: dict[str, Any]
```

```python
class SearchResponse(BaseModel):
    provider: str
    query: str
    results: list[SearchResult]
```

V1 只规定接口，不锁死搜索供应商。

配置：

```yaml
search:
  provider: ${SEARCH_PROVIDER}
  api_key: ${SEARCH_API_KEY}
  base_url: ${SEARCH_BASE_URL}
```

---

# 22. Prefect Flow Contract

---

## 22.1 `analysis_pipeline`

### 入参

```python
AnalysisRunRequest
```

### 输出

```python
class AnalysisRunResult(BaseModel):
    analysis_run_id: UUID

    status: AnalysisStatus

    published: bool

    score_ids: list[UUID]

    warnings: list[str]

    quality_gate_passed: bool
```

---

## 22.2 `factor_research_flow`

输入：

```python
class ResearchRequest(BaseModel):
    request_type: Literal[
        "new_factor",
        "factor_review",
        "rule_review",
        "weight_hypothesis",
        "source_review"
    ]

    target_factor_id: str | None

    question: str

    requested_by: UUID

    expert_review_required: bool = True
```

输出：

```python
class ResearchFlowResult(BaseModel):
    research_request_id: UUID

    candidate_ids: list[UUID]

    status: str

    human_review_required: bool
```

---

## 22.3 `replay_backfill_flow`

输入：

```python
class ReplayRequest(BaseModel):
    start_date: date
    end_date: date

    mode: Literal[
        "data_backfill",
        "point_in_time_replay",
        "version_compare"
    ]

    version_snapshot: VersionSnapshot | None

    selected_assets: list[str] | None

    requested_by: UUID
```

---

## 22.4 `calibration_review_flow`

输入：

```python
class CalibrationRequest(BaseModel):
    start_date: date
    end_date: date

    horizons: list[int] = [1, 5, 20]

    requested_by: UUID

    generate_weight_candidates: bool = True
```

输出：

```python
class CalibrationResult(BaseModel):
    report_id: UUID

    factor_metrics: dict
    score_metrics: dict

    weight_candidate_ids: list[UUID]
```

---

# 23. Prefect Schedule Contract

当前批准：

```yaml
analysis_schedule:
  timezone: Asia/Shanghai
  cron: "10 18 * * 1-5"
  deployment: close_analysis
```

运行前：

```text
CNTradingCalendarGuard
```

逻辑：

```text
若非A股交易日
→ AnalysisStatus = SKIPPED
→ 不产生 Score
```

管理员手动触发：

```text
不受交易日 guard 强制阻止
```

但若选择“正式发布到当天”，API 应给警告。

---

# 24. Analysis Pipeline Task Contract

建议固定以下 Task：

```text
create_analysis_context
acquire_run_lock
check_trading_calendar

load_version_snapshot

fetch_cn_market_bundle
fetch_cn_macro_bundle
fetch_rates_bundle
fetch_fx_bundle
fetch_gold_bundle
fetch_flow_bundle
fetch_news_bundle

normalize_market_data
validate_data_quality

build_news_clusters
invoke_event_graph

calculate_factor_states
apply_event_decay
apply_conflict_rules
apply_correlation_caps

calculate_asset_scores
calculate_confidence

run_quality_gate

invoke_explanation_graph

persist_analysis_snapshot
settle_matured_reviews

publish_analysis
```

---

# 25. LangGraph G1 State Contract

```python
class EventGraphState(TypedDict):
    analysis_context: AnalysisContext

    cluster: NewsCluster

    extracted_event: EventExtractionOutput | None

    factor_mapping: FactorMappingOutput | None

    impact_assessment: EventImpactAssessment | None

    deterministic_validation: dict | None

    conflict_flags: list[str]

    escalation_required: bool
    escalation_reason: str | None

    expert_review: dict | None

    final_events: list[StructuredEvent]

    errors: list[str]
```

---

# 26. Event Graph Node Contract

固定节点：

```text
load_cluster
source_precheck
extract_event_qwen
schema_validate_extraction
repair_extraction_qwen
extract_event_deepseek_fallback

deduplicate_event

map_factors_deepseek
assess_event_deepseek

rule_validator
conflict_detector

expert_review_gpt
expert_review_deepseek_fallback

merge_review
finalize_event
```

---

# 27. Event Graph Routing

```text
extract_qwen
  ↓
schema valid?
  ├─ yes → deduplicate
  └─ no
       ↓
   repair_qwen
       ↓
   valid?
     ├─ yes
     └─ no → deepseek_extract
```

后半：

```text
deepseek assess
   ↓
rule validator
   ↓
critical conflict?
   ├─ no → finalize
   └─ yes
         ↓
       GPT expert
         ↓ fail?
         ├─ no → merge → finalize
         └─ yes → DeepSeek high
                     ↓
                  merge/fail
```

---

# 28. G2 Research Graph State

```python
class ResearchGraphState(TypedDict):
    request: ResearchRequest

    search_plan: list[str]

    search_results: list[SearchResult]

    evidence_items: list[dict]

    supportive_evidence: list[dict]
    contradictory_evidence: list[dict]

    synthesis: dict | None

    evidence_grade: str | None

    expert_synthesis: dict | None

    candidate: dict | None

    human_decision: dict | None

    errors: list[str]
```

---

# 29. Research Graph Interrupt Contract

在：

```text
build_candidate
```

之后：

```text
interrupt
```

等待管理员：

```python
class ResearchReviewCommand(BaseModel):
    action: Literal[
        "approve_shadow",
        "approve_production",
        "revise",
        "reject"
    ]

    reviewer_id: UUID

    comment: str | None

    patch_override: dict | None
```

注意：

```text
approve_production
```

仍需要系统验证：

```text
candidate validation complete?
walk-forward passed?
required evidence grade satisfied?
```

不能只凭一个管理员点击绕过系统规则。

---

# 30. G3 Explanation Graph State

```python
class ExplanationGraphState(TypedDict):
    asset_score: AssetScore

    top_events: list[StructuredEvent]

    factor_definitions: list[dict]

    fact_pack: dict

    draft: AssetExplanation | None

    factuality_errors: list[str]

    final: AssetExplanation | None
```

---

# 31. Explanation Guard

禁止生成 Fact Pack 之外的新数字。

检查：

```text
所有百分比
所有分数
所有具体数值
所有政策日期
```

如果输出中出现未在 Fact Pack 出现的 numeric fact：

```text
reject
→ regenerate
```

---

# 32. G4 Scenario Graph State

```python
class ScenarioRequest(BaseModel):
    assumption: str

    base_analysis_run_id: UUID | None

    selected_assets: list[str] | None

    user_id: UUID | None
```

```python
class ScenarioGraphState(TypedDict):
    request: ScenarioRequest

    parsed_assumption: dict

    proposed_factor_shocks: list[dict]

    validated_factor_shocks: list[dict]

    simulated_scores: list[dict]

    conditional_branches: list[dict]

    explanation: str | None

    errors: list[str]
```

---

# 33. REST API Contract

API base：

```text
/api/v1
```

---

# 34. Public / User API

## `GET /dashboard/current`

返回当前 Published Analysis Snapshot。

Response：

```json
{
  "analysis_run_id": "...",
  "analysis_date": "2026-09-10",
  "as_of": "2026-09-10T18:23:00+08:00",
  "assets": [],
  "market_environment": {},
  "top_events": [],
  "status": "PUBLISHED"
}
```

---

## `GET /assets`

返回资产列表。

---

## `GET /assets/{asset_id}/current`

返回：

```text
Asset Score
Confidence
Top Positive
Top Negative
Risk Flags
Explanation
```

---

## `GET /assets/{asset_id}/history`

Query：

```text
start
end
```

返回历史正式 Published Score。

---

## `GET /events`

Query：

```text
date
factor_id
asset_id
event_type
```

---

## `GET /events/{event_id}`

返回：

```text
Event
Sources
Factor Mapping
Impact
```

---

## `POST /scenarios`

运行 Scenario。

V1 可以同步等待短任务。

若超过超时阈值，再升级异步。

---

# 35. Admin Analysis API

## `POST /admin/analysis/run`

Body：

```json
{
  "run_mode": "FULL_REFRESH",
  "publish_mode": "PREVIEW_ONLY",
  "force_refresh": true
}
```

行为：

```text
创建 ops.analysis_runs
↓
触发 Prefect Deployment
↓
立即返回 202
```

Response：

```json
{
  "analysis_run_id": "...",
  "prefect_flow_run_id": "...",
  "status": "PENDING"
}
```

---

## `POST /admin/analysis/reanalyze`

Body：

```json
{
  "base_run_id": "...",
  "publish_mode": "PREVIEW_ONLY"
}
```

---

## `POST /admin/data/update`

映射：

```text
DATA_ONLY
```

---

## `GET /admin/runs`

查看 Analysis Runs。

---

## `GET /admin/runs/{run_id}`

包含：

```text
status
Prefect run
provider health
warnings
quality gate
score preview
```

---

## `POST /admin/runs/{run_id}/publish`

已批准：

```text
Preview → Explicit Publish
```

检查：

```text
ADMIN
PREVIEW_READY
quality_gate_passed
没有已撤销状态
```

发布时：

```text
新的 run 变 Published
旧 published run 保留
旧 score published=false
supersedes_score_id 建立关系
audit log
```

---

## `POST /admin/runs/{run_id}/cancel`

尝试取消 Prefect Flow Run。

---

# 36. Admin Factor API

## `GET /admin/factors`

---

## `GET /admin/factors/{factor_id}`

包含：

```text
definition
current weights
current state
evidence
versions
candidate changes
```

---

## `GET /admin/config/versions`

---

## `POST /admin/config/activate`

只允许激活：

```text
APPROVED
```

版本。

所有修改写 Audit。

---

# 37. Admin Research API

## `POST /admin/research`

触发 Factor Research Flow。

---

## `GET /admin/research/{id}`

---

## `POST /admin/research/{id}/review`

Body：

```text
ResearchReviewCommand
```

---

# 38. HTTP 状态码约定

```text
200
成功查询

201
同步创建成功

202
异步任务已创建

204
成功但无 body

400
非法参数

401
未登录

403
权限不足

404
资源不存在

409
状态冲突 / 重复运行 / 版本冲突

422
Schema validation

429
Rate limit

500
未知内部错误

502
第三方 Provider / LLM upstream error

503
关键服务不可用
```

---

# 39. Error Code Contract

所有错误统一：

```python
class APIError(BaseModel):
    code: str
    message: str

    detail: dict | None

    request_id: str

    retryable: bool
```

---

## 39.1 Analysis

```text
ANALYSIS_ALREADY_RUNNING
ANALYSIS_RUN_NOT_FOUND
ANALYSIS_INVALID_STATE
ANALYSIS_QUALITY_GATE_FAILED
ANALYSIS_NOT_PUBLISHABLE
```

---

## 39.2 Data

```text
DATA_PROVIDER_UNAVAILABLE
DATA_PROVIDER_TIMEOUT
DATA_INVALID_SCHEMA
DATA_STALE
DATA_CRITICAL_MISSING
DATA_POINT_IN_TIME_VIOLATION
```

---

## 39.3 LLM

```text
LLM_PROVIDER_UNAVAILABLE
LLM_TIMEOUT
LLM_INVALID_JSON
LLM_SCHEMA_VALIDATION_FAILED
LLM_FALLBACK_EXHAUSTED
LLM_EXPERT_REVIEW_FAILED
```

---

## 39.4 Factor / Rule

```text
FACTOR_NOT_FOUND
FACTOR_NOT_APPROVED
FACTOR_STATE_INVALID
RULE_CONFLICT
WEIGHT_VERSION_NOT_FOUND
CONFIG_VERSION_CONFLICT
```

---

## 39.5 Research

```text
RESEARCH_NOT_FOUND
RESEARCH_INVALID_STATE
RESEARCH_EVIDENCE_INSUFFICIENT
RESEARCH_APPROVAL_REQUIRED
```

---

# 40. Quality Gate Contract

```python
class QualityGateResult(BaseModel):
    passed: bool

    data_coverage: float

    all_target_assets_scored: bool

    critical_sources_ok: bool

    unresolved_critical_event_conflict: bool

    schema_integrity_ok: bool

    warnings: list[str]

    failures: list[str]
```

V1 初始配置：

```yaml
quality_gate:
  minimum_data_coverage: 0.70
  require_all_assets: true
  allow_unresolved_critical_event_conflict: false
```

这些值进入：

```text
config version
```

而不是写死 Python。

---

# 41. Publish Contract

发布必须满足：

```text
analysis status = PREVIEW_READY
quality gate = PASS
run not cancelled
versions still valid
```

发布后：

```text
analysis status = PUBLISHED
asset_scores.published = true
reports.published = true
```

Dashboard 永远只查询：

```text
latest PUBLISHED run
```

不要直接查询“最新创建的 run”。

---

# 42. Run Lock Contract

系统避免重复正式分析：

```text
lock key:
widegold:analysis:{analysis_date}:{run_mode}
```

Scheduled FULL_REFRESH：

```text
同一天只允许一个 active
```

Admin Preview：

可以有多个，但：

```text
每个 run 唯一 analysis_run_id
```

---

# 43. Point-in-Time Contract

所有回测、Replay、Reanalyze 必须基于：

```text
data_cutoff_ts
```

查询宏观：

```text
release_ts <= data_cutoff_ts
```

查询新闻：

```text
published_at <= data_cutoff_ts
AND retrieved_at <= allowed replay boundary
```

禁止：

```text
用当前数据库最新值替代历史当时可见值
```

---

# 44. Reanalyze Contract

Reanalyze 目的：

```text
固定事实
改变模型/Prompt/规则/权重
```

必须继承：

```text
base_run.data_cutoff_ts
base_run.analysis_date
```

可改变：

```text
prompt version
model routing version
factor logic version
weight version
scoring version
```

默认不重新抓数据。

---

# 45. Version Contract

每一个生产 Score 必须完整携带：

```text
factor_schema_version
factor_logic_version
weight_version
event_rule_version
prompt_version
scoring_version
data_definition_version
model_routing_version
```

缺任何一个：

```text
不可发布
```

---

# 46. 配置版本的状态

```text
DRAFT
SHADOW
APPROVED
ACTIVE
DEPRECATED
```

只能：

```text
ACTIVE
```

版本进入自动 Scheduled Run。

管理员 Reanalyze 可以显式选择：

```text
APPROVED / SHADOW
```

用于测试。

---

# 47. Config 与 Git

Git：

```text
configs/*.yaml
```

作为：

```text
bootstrap baseline
development canonical config
```

生产：

```text
reference.config_versions
```

作为 runtime source。

启动时：

```text
若 DB 无配置
→ bootstrap YAML

若 DB 已有 ACTIVE
→ DB 优先
```

---

# 48. Audit Contract

以下操作不可删除审计：

```text
管理员登录
手动分析
重新分析
取消分析
Publish
配置激活
Factor 审批
Rule 审批
Weight 审批
模型路由修改
Research Review
```

Audit log 禁止 API 提供普通 delete。

---

# 49. 前端核心 API 消费关系

```text
Dashboard
→ GET /dashboard/current

Asset Page
→ GET /assets/{id}/current
→ GET /assets/{id}/history

Events
→ GET /events

Admin Run
→ POST /admin/analysis/run
→ GET /admin/runs/{id}

Preview
→ GET /admin/runs/{id}

Publish
→ POST /admin/runs/{id}/publish
```

---

# 50. Admin Trigger UX

按钮固定为三个：

## ① 更新并分析

```text
FULL_REFRESH
+
PREVIEW_ONLY
+
force_refresh=true
```

---

## ② 重新分析

选择一个历史 Run：

```text
REANALYZE
+
PREVIEW_ONLY
```

高级参数：

```text
模型版本
Prompt版本
规则版本
权重版本
```

---

## ③ 仅更新数据

```text
DATA_ONLY
```

不产生新资产评分。

---

# 51. 自动收盘分析

正式批准：

```text
18:10 Asia/Shanghai
```

自动：

```text
FULL_REFRESH
AUTO
```

如果 Quality Gate Fail：

```text
不覆盖当前 Published
```

Dashboard：

```text
继续展示上一份 Published
+
顶部提示：
“今日分析尚未成功更新”
```

---

# 52. 数据时效展示

每个因子可以包含：

```text
as_of
last_updated
status
```

用户不用看全部。

Admin 可以看：

```text
最新数据时间
数据源
是否 stale
fallback provider
```

---

# 53. Security Contract

API Key：

```text
.env
Docker Secret
OS environment
```

禁止进入：

```text
Git
DB plaintext config version
Frontend bundle
API response
logs
```

数据库中最多保存：

```text
provider name
secret reference key
```

---

# 54. LLM Secret Contract

```yaml
QWEN_API_KEY
QWEN_BASE_URL

DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL

GPT_COMPAT_API_KEY
GPT_COMPAT_BASE_URL=https://ca.memofun.net/v1
GPT_COMPAT_MODEL=gpt-5.6-sol
```

配置文件只引用 env key：

```yaml
providers:
  gpt_compatible:
    api_key_env: GPT_COMPAT_API_KEY
```

---

# 55. Model Alias Contract

业务代码只允许：

```text
qwen_fast
deepseek_reasoning
deepseek_high
gpt_expert
```

禁止：

```text
业务节点直接写具体 model string
```

真实模型在：

```text
models.yaml
+
runtime config version
```

解析。

---

# 56. V1 模型路由默认表

| Task | Primary | Fallback |
|---|---|---|
| 新闻分类 | qwen_fast | deepseek_reasoning |
| Event Extract | qwen_fast | deepseek_reasoning |
| Factor Mapping | deepseek_reasoning | deepseek_high |
| Event Impact | deepseek_reasoning | gpt_expert |
| Critical Review | gpt_expert | deepseek_high |
| Explanation | deepseek_reasoning | qwen_fast |
| Factor Research Synthesis | deepseek_high | gpt_expert |
| Factor Research Expert | gpt_expert | deepseek_high |
| Scenario | deepseek_reasoning | gpt_expert |

---

# 57. 数据 Provider 优先级

统一：

```text
Official
↓
Stable Public API
↓
AKShare / Aggregator
↓
Commercial Fallback（以后）
↓
Cached Last Valid
↓
Unavailable
```

注意：

```text
Cached Last Valid
```

必须标记：

```text
STALE
```

不能伪装最新数据。

---

# 58. Repository Contract

业务代码禁止直接到处：

```python
session.execute(...)
```

必须通过 Repository。

例如：

```text
AssetRepository
FactorRepository
ObservationRepository
EventRepository
ScoreRepository
RunRepository
ResearchRepository
```

---

# 59. Engine Contract

Engine 只能依赖：

```text
Domain Models
Pydantic Schema / dataclass
Config Snapshot
```

不能依赖：

```text
FastAPI
Prefect
LangGraph
HTTP client
SQLAlchemy Session
```

典型接口：

```python
FactorStateEngine.calculate(...)
EventImpactEngine.calculate(...)
ConflictRuleEngine.apply(...)
CorrelationCapEngine.apply(...)
AssetScoreEngine.calculate(...)
ConfidenceEngine.calculate(...)
```

---

# 60. Graph Contract

Graph 可以调用：

```text
LLMRouter
Repository read
SearchProvider（Research）
```

但不直接：

```text
更新生产 Weight
Publish Score
覆盖 Factor Config
```

---

# 61. Prefect Contract

Prefect 可以调用：

```text
DataService
Repository
LangGraph
Engine
PublishService
```

Prefect 是：

```text
application orchestration
```

不是领域逻辑。

---

# 62. API Contract

FastAPI 可以调用：

```text
Query Service
Command Service
Prefect Client
Auth Service
```

不能直接调用：

```text
Score Engine
Event Graph
Data Provider
```

生产分析全部走 Prefect。

---

# 63. Query / Command 分离

推荐轻量 CQRS 思想，不上复杂框架。

Query：

```text
DashboardQueryService
AssetQueryService
EventQueryService
AdminRunQueryService
```

Command：

```text
AnalysisCommandService
PublishCommandService
ResearchCommandService
ConfigCommandService
```

---

# 64. Cache Contract

Redis Cache 可以缓存：

```text
Dashboard latest
Asset current
Provider raw response
Trading calendar
Source health
```

Key 示例：

```text
widegold:dashboard:current
widegold:asset:CSI300:current
widegold:provider:FRED:DFII10:2026-09-10
```

发布新 Snapshot 后：

```text
主动 invalidate
```

---

# 65. Logging Contract

所有日志必须结构化。

字段：

```text
timestamp
level
service
request_id
analysis_run_id
prefect_flow_run_id
graph_thread_id
event_id
asset_id
provider
model_alias
error_code
message
```

---

# 66. 测试 Fixture Contract

至少固定：

```text
fixture_case_01_fed_dovish
fixture_case_02_liquidity_weak_growth
fixture_case_03_a_share_crash_gold_flat
fixture_case_04_inflation_real_yield_up
fixture_case_05_semiconductor_policy
```

这些来自《宽基黄金因子体系与影响准则 V1》。

每次规则或模型更新都必须 Replay。

---

# 67. Definition of Done：数据模型层

只有满足以下条件，数据库层才算完成：

```text
Alembic 可从空库创建所有表

所有 FK/Index 完整

point-in-time 查询可工作

同一天多 Run 可共存

发布与 Preview 可区分

历史 Published 可追溯

Factor/Weight Version 可回放

Audit 不丢

LLM Run 可追踪

Event 可追踪到原始 Documents
```

---

# 68. Definition of Done：API 层

```text
Swagger 完整

所有 request/response Pydantic 化

统一 Error Response

Admin 权限测试

Trigger 返回 202

Publish 状态机完整

无 API 请求执行重型生产分析
```

---

# 69. Definition of Done：Workflow 层

```text
Scheduled 与 Manual 共用同一个 analysis_pipeline

18:10 自动 Deployment 可运行

交易日 Guard 生效

Data Provider Retry/Fallback 生效

Graph 可独立重试

Quality Gate Fail 不发布

Manual 一律 Preview

Publish 必须显式动作
```

---

# 70. Definition of Done：模型路由层

```text
Qwen / DeepSeek / GPT Adapter 可替换

业务只使用 Alias

Structured Output Schema 检查

Qwen失败 → DeepSeek

GPT失败 → DeepSeek high

fallback exhausted 有明确错误

所有调用写 intel.llm_runs
```

---

# 71. 下一阶段代码骨架的生成边界

完成本文后，下一阶段可以直接生成：

```text
pyproject.toml
docker-compose.yml
.env.example

src/widegold/
apps/api/

Pydantic schemas
SQLAlchemy models
Alembic baseline

Repository interfaces
Engine interfaces

LLM Provider interfaces
Model Router skeleton

Prefect Flow skeleton

LangGraph State / Graph skeleton

FastAPI Router skeleton

React route / API client skeleton

tests fixtures
```

但暂时不需要一开始就实现：

```text
全部真实数据 Provider
完整 Factor State 数学逻辑
全部 Prompt
全部 Dashboard 页面
```

建议采用：

```text
Skeleton
→ Stub Provider
→ Fixed Fixture
→ End-to-End Pipeline
→ Real Provider
```

保证架构先跑通。

---

# 72. 最终确认版调用链

```text
                18:10 CN Trading Day
                       │
                     Prefect
                       │
                AnalysisContext
                       │
             Version Snapshot Freeze
                       │
         ┌─────────────┴──────────────┐
         ↓                            ↓
   Structured Data                 Documents
         │                            │
         │                    Event Intelligence
         │                      LangGraph G1
         │                            │
         │                 Qwen → DeepSeek
         │                            │
         │                       conflict?
         │                            │
         │                         GPT L3
         │                            │
         │                     GPT fail?
         │                            │
         │                     DeepSeek high
         │                            │
         └──────────────┬─────────────┘
                        ↓
                  Structured Event
                        ↓
                    Factor State
                        ↓
                   Conflict Rules
                        ↓
                Correlation Group Caps
                        ↓
                    Asset Score
                        ↓
                    Confidence
                        ↓
                   Quality Gate
                        ↓
                Explanation Graph G3
                        ↓
               Immutable Analysis Snapshot
                        ↓
              Scheduled → Auto Publish
              Manual    → Preview
                        ↓
                      FastAPI
                        ↓
                  React Dashboard
```

---

# 73. 当前阶段结论

经过 B/B/A/A 的最终选择后，WideGold V1 的架构已经从“方案设计”进入“接口契约已确定”阶段。

当前不再需要讨论：

```text
用不用 Prefect
用不用 LangGraph
用什么数据库
什么时候自动运行
管理员是否 Preview
模型如何分级
GPT失败怎么办
Research怎么搜索
```

这些都已经完成架构决策。

下一阶段应正式进入：

> **WideGold V1 工程骨架生成与第一条端到端 Mock Pipeline**

建议第一条可执行链只使用固定 Fixture：

```text
Mock Data
→ Mock News
→ Event Graph Stub
→ Factor State
→ Score
→ Confidence
→ Report
→ FastAPI
→ Dashboard JSON
```

当这条链稳定后，再依次替换为真实 Provider 与真实 LLM。
