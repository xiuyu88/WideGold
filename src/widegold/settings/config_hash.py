from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any


def json_compatible(value: Any) -> Any:
    """Normalize YAML-native values before storing or hashing runtime config."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_compatible(item) for item in value]
    return value


def config_content_hash(content: dict) -> str:
    normalized = json_compatible(content)
    raw = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()
