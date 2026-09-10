# 贡献指南

感谢对 WideGold 的关注。项目的核心目标是保持“可解释、可回放、可验证”，而不是追求 Agent 数量或模型复杂度。

## 开发环境

```bash
python -m pip install -e ".[dev]"
pytest -q
```

前端：

```bash
cd apps/web
npm install
npm run build
```

## 修改原则

提交涉及 Factor/Rule/Score 的代码时，应：

- 增加或更新测试；
- 不绕过 Point-in-Time；
- 不把 missing data 当作 0；
- 不让 LLM 直接生成最终 Score；
- 不把 API Key/密码写进代码；
- 如果修改 Runtime Config schema，同时更新 bootstrap YAML 与 config validation；
- 如果改变数据库结构，必须增加 Alembic migration。

## Commit 建议

推荐：

```text
feat: ...
fix: ...
test: ...
docs: ...
refactor: ...
chore: ...
```

## Pull Request 自检

```text
pytest
compileall
frontend build
docker compose config
secret scan
```

真实 LLM/公网数据测试不要默认放进 CI，应显式 opt-in。
