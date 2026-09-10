# WideGold Docker 运行日志与 Trace

## 1. 三层观测模型

WideGold 不把所有日志都推到管理员网页，而是分三层：

1. **Docker 技术日志**：stdout/stderr、Python exception、Nginx、PostgreSQL、Redis、Uvicorn、Prefect Worker。
2. **Prefect Workflow Trace**：Flow/Task 状态、retry、schedule、task failure，通过 Prefect UI 查看。
3. **WideGold 业务 Trace**：只记录一次分析的关键阶段、进度、降级和 Quality Gate，保存到 `ops.run_events`，提供给管理员前端。

完整 Prompt、模型逐 token 输出、Provider 原始响应默认不进入业务 Trace。

## 2. Docker 常用日志命令

```bash
# 所有服务最近日志
docker compose logs --tail=200

# 实时看 API
docker compose logs -f --tail=200 api

# 实时看正式分析 Worker（最重要）
docker compose logs -f --tail=300 prefect-worker

# Prefect Server
docker compose logs -f --tail=200 prefect-server

# PostgreSQL
docker compose logs -f --tail=100 postgres

# Redis
docker compose logs -f --tail=100 redis

# Nginx
docker compose logs -f --tail=100 nginx

# 数据库迁移/Seed
docker compose logs db-migrate db-seed
```

查看容器：

```bash
docker compose ps
```

查看资源：

```bash
docker stats
```

## 3. 日志轮转

Compose 已统一使用 Docker `local` logging driver：

```yaml
logging:
  driver: local
  options:
    max-size: 10m
    max-file: 5
```

因此不会让 Docker JSON 日志无限增长占满磁盘。

## 4. Prefect UI

默认端口：

```text
http://localhost:4200
```

Prefect 用于查看：

- Deployment 是否按 18:10 触发；
- Flow Run 当前状态；
- retry；
- 异常 stack trace；
- Worker 是否在线。

业务 Dashboard 不复制 Prefect 的完整内部 Trace。

## 5. Admin 业务 Trace API

分页增量读取：

```http
GET /api/v1/admin/runs/{run_id}/trace?after_seq=0&limit=100
```

SSE：

```http
GET /api/v1/admin/runs/{run_id}/trace/stream
```

事件示例：

```json
{
  "trace_seq": 12,
  "stage": "QUALITY_GATE",
  "status": "DEGRADED",
  "level": "WARNING",
  "message": "质量门检查完成",
  "progress": 0.79,
  "details": {
    "passed_for_publish": false,
    "overall_weighted_coverage": 0.68
  }
}
```

## 6. 为什么业务 Trace 对性能影响很小

当前设计每次完整分析通常只产生约 10～20 条业务事件。每条事件是几百字节到几 KB 的 JSON，且只在阶段变化时写入，而不是每个 token、每条行情、每个内部函数都写。

SSE 使用 cursor，只向浏览器发送新事件；前端最多保留最近约 120 条。相比一次 LLM 请求和金融数据抓取，这部分开销通常可以忽略。

真正应该避免的是：

- 每个 LLM token 都写 PostgreSQL 并实时发前端；
- 把完整 prompt / response 每秒重复发送；
- 每个行情点都作为 Trace 事件；
- 前端每秒重新请求完整历史 Trace；
- 把 Docker stdout 全量经 FastAPI 转发。

## 7. 故障策略

```text
业务 Trace DB 写失败
→ 记录 stdout warning
→ 分析继续

SSE 断线
→ 分析继续
→ 前端状态轮询仍可工作

Docker logging driver 异常
→ 不参与投资评分逻辑

Prefect Server UI 不可访问但 Worker 已拿到任务
→ 具体行为以 Prefect 状态为准，业务 Snapshot 仍由 PostgreSQL 保存
```

观测能力不能成为评分系统的单点故障。
