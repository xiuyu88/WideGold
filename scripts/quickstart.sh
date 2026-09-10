#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 Docker，请先安装并启动 Docker Engine。" >&2
  exit 2
fi

if [[ ! -f .env ]]; then
  cp .env.final.example .env
  echo "已创建 .env。请填写 POSTGRES_PASSWORD、WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD、FRED_API_KEY、DEEPSEEK_API_KEY 后再次运行。"
  exit 2
fi

value() { grep -E "^$1=" .env | head -n1 | cut -d= -f2- || true; }
pg="$(value POSTGRES_PASSWORD)"
admin="$(value WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD)"
if [[ -z "$pg" || "$pg" == "change-me" || "$pg" == "widegold" ]]; then
  echo "请先设置安全的 POSTGRES_PASSWORD。" >&2; exit 2
fi
if [[ ${#admin} -lt 8 ]]; then
  echo "WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD 至少 8 位。" >&2; exit 2
fi

./scripts/docker-up.sh
port="$(value HTTP_PORT)"; port="${port:-8080}"
base="http://localhost:${port}"
for _ in $(seq 1 24); do
  if curl -fsS "$base/health" >/dev/null 2>&1; then
    echo "WideGold: $base"
    prefect_port="$(value PREFECT_PORT)"; prefect_port="${prefect_port:-4200}"
    echo "Prefect : http://localhost:${prefect_port}"
    echo "启动完成。下一步可运行 ./scripts/quicktest.sh"
    exit 0
  fi
  sleep 5
done

docker compose ps
printf '%s\n' "API 在 120 秒内未就绪，请查看日志。" >&2
exit 2
