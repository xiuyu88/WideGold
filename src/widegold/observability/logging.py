from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "service",
            "request_id",
            "analysis_run_id",
            "prefect_flow_run_id",
            "graph_thread_id",
            "event_id",
            "asset_id",
            "provider",
            "model_alias",
            "stage",
            "error_code",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = str(value)
        extra_payload = getattr(record, "details", None)
        if isinstance(extra_payload, dict) and extra_payload:
            payload["details"] = extra_payload
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def get_logger(name: str = "widegold") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
