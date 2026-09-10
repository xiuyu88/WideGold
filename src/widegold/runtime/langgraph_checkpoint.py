from __future__ import annotations

import atexit
from functools import lru_cache

from widegold.observability.logging import get_logger
from widegold.settings.app import get_settings

logger = get_logger("widegold.langgraph")
_conn = None


@lru_cache(maxsize=1)
def get_checkpointer():
    """Return a process-wide PostgreSQL LangGraph checkpointer when enabled.

    Table setup is performed by the Docker one-shot `langgraph-migrate` service, not by every
    API/worker process at startup.
    """
    global _conn
    settings = get_settings()
    if settings.langgraph_checkpoint_mode != "postgres":
        return None
    try:
        import psycopg
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:  # pragma: no cover - production dependency path
        raise RuntimeError("langgraph-checkpoint-postgres is required in postgres mode") from exc
    _conn = psycopg.connect(
        settings.langgraph_database_url,
        autocommit=True,
        row_factory=dict_row,
    )
    return PostgresSaver(_conn)


def _close() -> None:
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            logger.warning("Failed to close LangGraph checkpoint connection", exc_info=True)
        _conn = None


atexit.register(_close)
