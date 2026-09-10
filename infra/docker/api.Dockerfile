FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
RUN pip install --no-cache-dir uv
COPY pyproject.toml README.md alembic.ini prefect.yaml ./
COPY src ./src
COPY apps ./apps
COPY configs ./configs
COPY fixtures ./fixtures
COPY migrations ./migrations
COPY scripts ./scripts
RUN uv pip install --system .
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
