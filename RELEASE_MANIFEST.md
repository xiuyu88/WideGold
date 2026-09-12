# WideGold V1 发布清单

- 项目版本：`1.2.0`
- 整理日期：`2026-09-12`
- 本次变更：Event Intelligence P0 修复 + 全链路代码复查修复（详见 `docs/10_event_intelligence_p0_fix.md`）
- External Bridge Build：`2026.09.11-free-sources-v3.1`
- 逻辑因子：27
- 物理 FactorState 目标：42
- Required Indicator：33
- 目标资产：7
- Python 回归测试：128 tests（V1）+ 15 tests（V1.1 新增 LLM 契约与事件图路由回归），需在目标主机执行 `release-gate` 复核
- Compileall：PASS
- Runtime Config 校验：PASS
- YAML 静态校验：PASS
- External Bridge 静态校验：PASS
- Machine Output Contract：PASS
- Alembic Offline：PASS
- Secret Scan：PASS
- 当前打包环境无 Docker CLI / 前端 node_modules，因此 Release Gate 中 frontend_build 与 docker_compose_config 标记为 SKIP；目标 Windows 10 主机已完成 Docker 实机运行验证。

升级后必须先执行一次运行时配置对齐，否则新的 `news.yaml` / `models.yaml` 不会生效：

```powershell
docker compose exec api python scripts/sync_runtime_config.py --check
docker compose exec api python scripts/sync_runtime_config.py --activate-all
docker compose exec api python scripts/llm_probe.py
```

本发布包为 GitHub 整理版，不包含 `.env`、真实 API Key、数据库数据 Volume 或用户本地备份。
