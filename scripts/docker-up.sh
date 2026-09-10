#!/usr/bin/env sh
set -eu

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed or not on PATH" >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Configure secrets before production use."
fi

auth_mode=$(grep -E '^WIDEGOLD_AUTH_MODE=' .env | tail -n1 | cut -d= -f2- || true)
admin_password=$(grep -E '^WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD=' .env | tail -n1 | cut -d= -f2- || true)
postgres_password=$(grep -E '^POSTGRES_PASSWORD=' .env | tail -n1 | cut -d= -f2- || true)
if [ "${auth_mode:-session}" = "session" ]; then
  if [ -z "${admin_password}" ]; then
    echo "ERROR: set WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD in .env before production start." >&2
    exit 2
  fi
  if [ "${#admin_password}" -lt 8 ]; then
    echo "ERROR: WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD must be at least 8 characters." >&2
    exit 2
  fi
fi
if [ -z "${postgres_password}" ] || [ "${postgres_password}" = "change-me" ] || [ "${postgres_password}" = "widegold" ]; then
  echo "ERROR: replace POSTGRES_PASSWORD in .env with a strong unique password." >&2
  exit 2
fi

docker compose config >/dev/null
docker compose up -d --build

http_port=$(grep -E '^HTTP_PORT=' .env | tail -n1 | cut -d= -f2- || true)
prefect_port=$(grep -E '^PREFECT_PORT=' .env | tail -n1 | cut -d= -f2- || true)
http_port=${http_port:-80}
prefect_port=${prefect_port:-4200}
if [ "$http_port" = "80" ]; then dashboard_url="http://localhost"; else dashboard_url="http://localhost:$http_port"; fi

echo "WideGold containers started."
echo "Dashboard: $dashboard_url"
echo "Prefect:   http://localhost:$prefect_port"
echo "Use: docker compose ps"
echo "Use: docker compose logs -f --tail=200 prefect-worker"
