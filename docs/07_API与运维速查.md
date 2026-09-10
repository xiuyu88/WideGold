# WideGold V1 API 与运维速查

## 1. 地址

```text
Dashboard       http://localhost:8080
API Health      http://localhost:8080/health
API Readiness   http://localhost:8080/ready
Prefect UI      http://localhost:4200
External Bridge http://localhost:9100/health
```

API Base：`/api/v1`。

## 2. 常用 API

```text
GET  /api/v1/dashboard/current
GET  /api/v1/assets/{asset_id}/current
GET  /api/v1/assets/{asset_id}/history
GET  /api/v1/events
POST /api/v1/scenarios

POST /api/v1/auth/login
GET  /api/v1/auth/me
POST /api/v1/auth/logout

POST /api/v1/admin/analysis/run
GET  /api/v1/admin/runs/{run_id}
POST /api/v1/admin/runs/{run_id}/publish
POST /api/v1/admin/runs/{run_id}/cancel
GET  /api/v1/admin/runs/{run_id}/trace
GET  /api/v1/admin/runs/{run_id}/trace/stream

GET  /api/v1/admin/factors/health
GET  /api/v1/admin/config/versions
POST /api/v1/admin/config/activate
GET  /api/v1/admin/system/diagnostics
```

## 3. Analysis Status

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

Prefect State 是技术执行状态；WideGold AnalysisStatus 是业务生命周期，两者不要混淆。

## 4. Run 模式

```text
FULL_REFRESH    重新抓数据并分析
REANALYZE       固定事实，重新运行模型/规则/权重
DATA_ONLY       只更新数据
REPLAY          历史 point-in-time 重放
```

管理员手动 Full Refresh 默认 `PREVIEW_ONLY`。

## 5. Docker

```powershell
docker compose ps
docker compose logs -f --tail=300 prefect-worker
docker compose logs -f --tail=200 api
docker compose logs -f --tail=200 external-bridge
docker stats
```

重建单个服务：

```powershell
docker compose build --no-cache external-bridge
docker compose up -d --force-recreate external-bridge
```

## 6. 健康检查

```powershell
curl.exe http://localhost:8080/health
curl.exe http://localhost:8080/ready
curl.exe http://localhost:9100/health
curl.exe http://localhost:4200/api/health
```

`/ready` 可以显示 `ready=true, degraded=true`，表示核心 API 可服务，但部分可选运行能力处于降级。

## 7. 测试

```powershell
python scripts/external_bridge_smoke.py
.\scripts\docker-e2e.ps1
.\scripts\release-gate.ps1 -RequireDocker
```

Live：

```powershell
.\scripts\docker-e2e.ps1 -Analysis
```

## 8. Runtime Config

```text
Git YAML -> Seed -> config_versions -> ACTIVE -> Runtime Loader
```

修改 YAML 后，如果 DB 已经存在 ACTIVE 配置，运行时不会自动偷偷覆盖生产版本。

## 9. Trace

管理员 Trace 是低频业务阶段事件，不推送完整 Prompt/Raw Response/token stream，因此不会成为主要系统负载。

## 10. 故障原则

```text
单个 Provider 失败
→ fallback / stale / unavailable
→ Confidence 下降
→ 主服务继续

数据不足
→ Preview 可允许
→ Publish Gate 拒绝

LLM cluster 失败
→ 隔离 cluster
→ 记录 warning
→ 评分主链可继续
```
