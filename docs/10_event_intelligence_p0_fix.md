# Event Intelligence P0 修复说明（V1.1）

> 对应问题：真实 Live Analysis 中出现 `documents=40`、`llm_calls=78`、`events=0`，
> 并伴随大量 `llm_fallback_exhausted:extraction`。

---

## 1. 根因

### 1.1 决定性根因：结构化输出的 Schema 从未发给模型

`llm/providers/http_compatible.py` 的请求体只设置了 `response_format: {"type": "json_object"}`，
`schema` 参数**仅**用于本地的 `schema.model_validate_json(content)`。全仓库此前没有任何一处调用
`model_json_schema()`，模型从头到尾不知道要输出哪些字段。

同时 `EventExtractionOutput` 等全部继承 `StrictModel`，即 `extra="forbid"`。两者叠加的结果是：

```text
模型返回合法 JSON
→ 字段名由模型自行发挥
→ Pydantic extra=forbid 拒绝
→ 包装成 LLMProviderError
→ 换下一个 alias
→ 链路耗尽
→ events = 0
```

### 1.2 调用次数的算术交叉验证

```text
40 篇文档，聚类阈值 0.62
→ 真实财经标题之间很少互相相似
→ 约 39 个 cluster

extraction 路由 = [qwen_fast, deepseek_fast, deepseek_reasoning]
QWEN_API_KEY 为空 → _provider() 抛 RuntimeError → 被静默 continue，不计入 executions
→ 每个 cluster 实际发出 2 次 HTTP

39 × 2 = 78
```

与日志中的 `llm_calls = 78` 完全吻合。这说明 78 次调用**全部消耗在第一个节点 extract 上**，
`map_factors` / `assess` 一次都没有执行到，因此不是"新闻质量不好所以没有事件"，
而是一个 100% 确定性的契约失败。

> 附带修正：代码中并不存在 `extraction → repair → fallback` 的多轮放大逻辑。
> 调用量放大来自 **cluster 数 × alias 数**，不是 repair。

### 1.3 配置不生效是设计使然

`scripts/seed_reference.py` 的 `_ensure_config_row()`：若该 config_type 已存在 ACTIVE 行，
新 seed 进来的版本一律写成 `APPROVED`。因此"改 YAML + 重启 Docker"在运行时等于什么都没做。

`activate-final-config.ps1` 的 `try/catch` 只做 `Write-Warning`，管理员密码失败时脚本仍正常退出。

`documents = 40` 是直接证据：新版 `max_documents` 是 12，而代码兜底默认值恰好是 40。

### 1.4 掩盖真相的次要因素

- `deepseek_reasoning` 开启 thinking，但 `max_tokens=1200` 是推理与回答共用预算，
  容易 `finish_reason=length` 返回空 content，最终同样表现为"schema 失败"。
- `error_code` 恒为 `LLM_PROVIDER_CALL_FAILED`，400 / 401 / 404 与"schema 不合格"
  在 `intel.llm_runs` 里长得一模一样。
- 每个非事件新闻此前仍会跑满 `map_factors` → `assess`，并且 `NO_EVENT` / `NO_FACTOR_MAPPING`
  会被 `validate_event` 记为 error，进而触发 `detect_conflict` 升级，调用最昂贵的
  `expert_review` 别名。也就是说，一条完全无关的行情播报最坏会花掉 4 次调用。

---

## 2. 修复内容

### 2.1 LLM 传输层（`llm/providers/http_compatible.py`）

| 修复 | 说明 |
| --- | --- |
| Schema 下发 | `schema.model_json_schema()` 作为独立 system turn 随请求发出 |
| 容错解析 | 剥离 Markdown 围栏、截取首尾大括号，容忍前后缀说明文字 |
| 空响应识别 | content 为空时明确报错并带上 `finish_reason`，指向 token 预算问题 |
| 错误分类 | `LLM_HTTP_400/401/404/429/5xx`、`LLM_TRANSPORT_ERROR`、`LLM_SCHEMA_INVALID` |
| 原地 repair | 校验失败时把错误回灌给同一模型重试一次，失败才降级 alias |
| 瞬时重试 | 429 / 5xx / 超时按指数退避重试，识别 `Retry-After` |
| 推理预算 | thinking 开启时额外预留 headroom，避免推理 token 吃掉回答预算 |

### 2.2 路由与降级（`llm/service.py`）

- 未配置的 Provider 记入 `skipped` 并写进异常信息，可区分"没配 Key"与"被上游拒绝"。
- 异常信息带 `alias / resolved_model / error_code`，不再是一串裸文本。
- `structured_call_sync` 在已有事件循环时改为在独立线程执行，不再直接抛错。

### 2.3 事件图条件路由（`graphs/event_intelligence/`）

```text
extract
 ├─ event_detected = false ────────────────► finalize（共 1 次调用）
 └─ map_factors
     ├─ mappings 为空 ─────────────────────► finalize（共 2 次调用）
     └─ assess → validate → conflict
         ├─ 不需要升级 ─────────────────────► finalize（共 3 次调用）
         └─ expert_review ─────────────────► finalize（共 4 次调用）
```

- Expert Review 只对 `strength >= 3` 且存在冲突/校验标记的事件触发；
  弱事件带标记时降级为 `PARTIALLY_VERIFIED` / `UNVERIFIED`，不再花钱复核。
- `DirectEventGraph` 与 LangGraph 编译图共用同一组路由谓词，两条执行路径不可能分叉。

### 2.4 成本上限（`services/news_clustering.py`、`settings/app.py`）

- `WIDEGOLD_EVENT_MAX_CLUSTERS`（默认 15）是**环境级硬上限**，故意不走数据库，
  这样 Runtime Config 再漂移也压不垮成本。
- `news.yaml:max_clusters`（1.3.0 起为 12）是可调旋钮，实际生效值取两者较小者。
- 截断时按（文档数、源等级、新鲜度）择优保留，而不是随意截断。

### 2.5 可观测性（`services/analysis.py`）

`EVENT_INTELLIGENCE` 阶段的 trace 新增 `llm_failures`：

```json
{
  "failed_calls": 24,
  "by_alias_error": {"deepseek_fast/deepseek-v4-flash:LLM_HTTP_404": 12},
  "sample_error": "HTTP 404 from deepseek for model deepseek-v4-flash: ..."
}
```

下次再出问题，不用连数据库就能在前端看到原因。

### 2.6 运维脚本

```powershell
# 只读：YAML 版本 vs 数据库 ACTIVE 版本漂移表
docker compose exec api python scripts/sync_runtime_config.py --check

# 把仓库 YAML 版本激活为运行时配置（走 stage + activate，不依赖管理员密码）
docker compose exec api python scripts/sync_runtime_config.py --activate-all

# 逐 alias 发一次极小真实调用，验证模型名 / 推理参数 / Schema 契约
docker compose exec api python scripts/llm_probe.py
docker compose exec api python scripts/llm_probe.py --task reasoning
```

---

## 3. 升级与验证顺序

```powershell
# 1. 重建镜像
docker compose up -d --build

# 2. 检查配置漂移（此处应能看到 NEWS_COLLECTION / MODEL_ROUTING 的 DRIFT）
docker compose exec api python scripts/sync_runtime_config.py --check

# 3. 激活仓库版本
docker compose exec api python scripts/sync_runtime_config.py --activate-all

# 4. 再次确认全部 ok
docker compose exec api python scripts/sync_runtime_config.py --check

# 5. LLM 契约探针（几乎不花 token）
docker compose exec api python scripts/llm_probe.py

# 6. 基础 E2E
.\scripts\docker-e2e.ps1

# 7. 一次 Live Analysis
.\scripts\docker-e2e.ps1 -Analysis
```

第 5 步的判读：

| 输出 | 含义 | 处理 |
| --- | --- | --- |
| `[skip] ... not configured` | 该 Provider 没配 Key | 正常，只要至少一个 alias `[ok]` |
| `LLM_HTTP_404` / `400` | 模型名或 thinking 参数被上游拒绝 | 改 `configs/models.yaml` 后 `--activate MODEL_ROUTING` |
| `LLM_HTTP_401` / `403` | Key 或 base_url 错误 | 检查 `.env` |
| `LLM_HTTP_429` | 限流 | 已自动退避；持续出现需降低并发或换档 |
| `LLM_SCHEMA_INVALID` | Schema 已下发但模型仍给不出合规输出 | 提示词层面问题，考虑放宽 Schema 或换模型 |
| `[ok]` | 契约成立 | 继续第 6 步 |

第 7 步的预期：

```text
clusters   ≤ 12
llm_calls  ≈ 12 ~ 40
events     > 0
event_processing_complete = true
```

数据库兜底查询：

```sql
select model_alias, resolved_model, status, error_code,
       left(error_message, 200) as sample, count(*)
from intel.llm_runs
where analysis_run_id = '<run id>'
group by 1,2,3,4,5
order by 6 desc;
```

---

## 4. 本次未处理的问题

以下属于 P1 及以后，与 P0 无耦合，**建议等 P0 稳定产出 events 之后再动**，
否则两边的变量会混在一起：

- Fresh Data Coverage 偏低（大量因子依赖 LKG，publish floor 50% 未达标）。
- `CN_FOREIGN_ACTIVITY` 免费公开源的天然限制。
- `CN_INDEX_EARNINGS_REV` 需要 7～30 天快照积累。
- Point-in-Time Replay / Walk-forward 校准。


---

## 5. V1.1 代码复查附带修复

P0 修完后对全链路做了一次复查，以下问题一并处理。

### 5.1 确定的 bug

**RMB_GOLD 的 confidence 惩罚被算出来但从不生效。**
`engine/rules.py` 为黄金写了两条惩罚（CFTC 多头拥挤、央行购金仅代理数据），但
`engine/confidence.py` 的 `asset_id != "RMB_GOLD"` 把它们全部丢弃，而
`analysis_stages.py` 仍把惩罚写进 trace 的 `confidence_penalty_by_asset`——
前端显示扣分、实际一分没扣。

真正的问题在于权益冲突规则（EQ01/EQ04/EQ06）此前对所有资产无差别触发，那个排除条件
是为了不让权益冲突污染黄金。现在把规则本身限定为非黄金资产，`confidence.py` 里的
资产例外随之删除，每一条到达那里的惩罚都会被应用。

**ResilientExecutor 的 `timeout_seconds` 对同步 Provider 完全无效。**
`asyncio.wait_for` 包住的是在事件循环线程里同步执行的函数，循环被堵死，超时没有机会触发。
akshare 本身不暴露超时控制，因此一个卡住的公网请求会占死一个 indicator worker
（总共只有 4 个）直到 TCP 层放弃。这与 P4「Manual Preview 超时但 Prefect 最终完成」吻合。

修复方式是把同步 capability 放到**专用线程池**执行：
- 用专用池而不是事件循环的默认 executor，因为 `asyncio.run` 在收尾时会等待默认 executor，
  那样超时刚放弃的调用又会把收尾阻塞回去；
- 显式 `contextvars.copy_context()`，保证冻结的 Runtime Config 快照能跟着过线程边界。

### 5.2 语义不一致

- **sensitivity 符号处理三处不一致**：scoring 用有符号值、quality 用 `abs()`、
  confidence 用 `> 0` 过滤。当前 `weights.yaml` 全为正数所以没爆，但一旦写入负 sensitivity，
  confidence 会静默丢掉那批因子而 coverage 照常计入。统一为 `abs(...) > 0`。
- **publish 阈值 0.72 硬编码**在 `analysis_stages.py`，与 `resilience.yaml` 的
  `minimum_publish_weighted_coverage` 重复。改为读配置。
- **`latest_published` 按发布时间排序**，而 `publish()` 又不会把上一个 PUBLISHED 降级，
  于是手动发布一个 replay 或旧 preview 会把首页覆盖成历史结果。改为按分析日期排序，
  发布时间只用于同日内的 tie-break；同时 `POST /runs/{id}/publish` 在检测到回溯发布时返回
  409，必须显式传 `allow_backdated=true` 才允许。
- **`save_snapshot` 重复保存同一 run 会累积重复的 asset_scores / score_contributions 行**，
  改为先删除旧行再写入。

### 5.3 并发与性能

- `ThreadPoolExecutor` 的工作线程拿不到调用方的 `ContextVar`，indicator 并发采集里
  冻结的 Runtime Config 快照会丢失。当前 Provider 恰好不在线程里读配置所以没爆，
  现已在 indicator collection 和 LLM 同步桥接处显式复制上下文。
- `circuit_breakers` 全局字典在多线程下无锁读改写，已加锁。
- `_direction_for_horizon` 每个资产被调用 6 次（3 次算分 + 3 次算贡献），
  现在每个 horizon 只算一次并复用，7 个资产少掉 21 次重复计算，数值完全不变。
- `latest_factor_states` 全表扫描无时间下界，现加上 4320 小时窗口
  （最大 `max_stale_hours` 为 quarterly 3600h，更早的候选在 factor_resolution 里必然被丢弃）。

### 5.4 质量门策略变更

`resilience.yaml` 的 `require_all_assets_scoreable` 由 `false` 改为 `true`。

原来的 `any()` 语义是：7 个资产里只要有 1 个数据够，整个 Preview 就通过，
另外 6 个照样出分、照样进 Dashboard，只多一个 `insufficient_factor_coverage` 标记。
对投资研判输出而言这与「Quality Gate 是保护机制」的直觉相反。

改为 `all()` 后，任一资产低于 `minimum_preview_weighted_coverage`（0.55）时整个 Run
会落到 `QUALITY_FAILED`，不会产生可发布的快照。`resilience.yaml` 版本随之升到 1.3.0，
**升级后需要执行 `sync_runtime_config.py --activate-all` 才会生效**。


---

## 6. V1.3 细节复查修复

第三轮复查覆盖 API 路由、仓储层、外部 Bridge、指标特征与分发链路，以下问题一并处理。

### 6.1 Point-in-Time 历史被静默截断（影响最大）

`PostgresRepository.get_indicator_history` 先 `LIMIT`、后按 `observation_date` 去重
revision vintage。对修订频繁的宏观序列，`limit=600` 拿到的原始行折叠后可能只剩很少的
观测日；而排序是 `observation_date DESC`，被砍掉的**永远是最早的历史**——正好是
`level_robust` / `yoy_pct_12` 这类 robust 尺度最依赖的部分。

内存仓储是"先去重再截断"，两个后端行为不一致，这也说明 PostgreSQL 这边是实现疏忽。

改为 `DISTINCT ON (observation_date)` 在 SQL 里完成去重，`LIMIT` 作用在去重之后。

### 6.2 External Bridge 的未绑定变量

```python
try:
    result = adapter.fetch(request)
except Exception as exc:
    result = None
    warning = f"adapter_unhandled_error:{...}"
if result is None:
    return {... "warnings": [warning] ...}
```

adapter 如果**不抛异常地返回 None**（某个分支走到函数末尾），`warning` 未绑定，
一个降级数据源会变成 Bridge 的 500。已预置默认值。
同时 `bridge_dropped_release_after_as_of` 不再按行重复追加。

### 6.3 Dashboard 缓存永不过期

`/dashboard/current` 每次读取都会 `set_current_snapshot` 刷新 TTL，只要有流量，
缓存条目就能无限存活——一次失败的失效等于永久陈旧。现在命中缓存直接返回，
只有回源时才写入，TTL 真正成为陈旧上限。

发布路径（手动发布与 AUTO 发布）改为**失效缓存**而不是直接写入刚发布的快照：
配合 `allow_backdated`，刚发布的 run 不一定就是首页该展示的那个，
`latest_published()` 是唯一的裁决者。

### 6.4 分发失败留下永不终结的 Run

`dispatch_analysis` 先 `reserve_run`（状态 PENDING）再调 `run_deployment`。
Prefect 不可用时异常直接上抛，PENDING 的 run 永远留在库里，E2E 轮询和管理端
运行列表会一直等一个已经失败的任务。现在捕获异常、把 run 置为
`FAILED / DISPATCH_FAILED` 再上抛。

### 6.5 其他

- `apps/api/dependencies.py` 的 `require_admin(user=None)` 是死代码，
  一旦有人把它当 `Depends` 用就是无鉴权直通，已删除。
- `level_robust` 的守卫比 `robust_score` 的要求少一个观测：三点序列会被
  静默返回硬编码 0.0（"中性"），与真正的中性无法区分。改为在这种情况下
  用完整窗口作为历史，恢复作者 `or window` 兜底的本意。
- 公共历史与 Calibration 的"每日取最后一条"此前在 `as_of` 完全相等时由数据库行序决定，
  现在 `list_snapshots` 的排序追加 `published_at` / 发布序号作为 tie-break，
  两个后端结果一致且确定。
- `news_akshare` 每行都会构造一个从未使用的 `row._asdict()`，并重复四次
  `columns.get_loc`、每行重复 `text.lower()`，已提到循环外。
- `PostgresRepository.save_snapshot` 的 `quality` 参数与内存仓储签名不一致
  （一个必填、一个可选），已对齐为可选。
