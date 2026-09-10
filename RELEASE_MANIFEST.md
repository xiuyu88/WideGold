# WideGold V1 发布清单

- 项目版本：`1.0.0`
- 整理日期：`2026-09-11`
- External Bridge Build：`2026.09.11-free-sources-v3.1`
- 逻辑因子：27
- 物理 FactorState 目标：42
- Required Indicator：33
- 目标资产：7
- Python 回归测试：128 tests，PASS
- Compileall：PASS
- Runtime Config 校验：PASS
- YAML 静态校验：PASS
- External Bridge 静态校验：PASS
- Machine Output Contract：PASS
- Alembic Offline：PASS
- Secret Scan：PASS
- 当前打包环境无 Docker CLI / 前端 node_modules，因此 Release Gate 中 frontend_build 与 docker_compose_config 标记为 SKIP；目标 Windows 10 主机已完成 Docker 实机运行验证。

本发布包为 GitHub 整理版，不包含 `.env`、真实 API Key、数据库数据 Volume 或用户本地备份。
