from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from widegold.auth.passwords import verify_password
from widegold.repositories.factory import repository
from widegold.runtime.redis_client import redis_client
from widegold.settings.app import get_settings


@dataclass(frozen=True)
class Principal:
    user_id: UUID | None
    username: str
    roles: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return "ADMIN" in self.roles


class AuthError(RuntimeError):
    pass


class AuthService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def login(self, username: str, password: str) -> tuple[str, Principal]:
        if self.settings.auth_mode == "disabled":
            principal = Principal(None, username or "dev-admin", frozenset({"ADMIN", "USER"}))
            return "dev-session", principal
        row = repository().get_user_auth(username)
        if not row or not row["is_active"] or not verify_password(password, row["password_hash"]):
            raise AuthError("invalid username or password")
        principal = Principal(UUID(str(row["user_id"])), row["username"], frozenset(row["roles"]))
        token = secrets.token_urlsafe(32)
        payload = json.dumps({
            "user_id": str(principal.user_id),
            "username": principal.username,
            "roles": sorted(principal.roles),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        try:
            redis_client().setex(
                f"widegold:session:{token}", self.settings.session_ttl_seconds, payload
            )
        except Exception as exc:
            # Authentication is security-sensitive: Redis failure is fail-closed.
            raise AuthError("session backend unavailable") from exc
        return token, principal

    def authenticate(self, token: str | None) -> Principal:
        if self.settings.auth_mode == "disabled":
            return Principal(None, "dev-admin", frozenset({"ADMIN", "USER"}))
        if not token:
            raise AuthError("missing session")
        try:
            raw = redis_client().get(f"widegold:session:{token}")
        except Exception as exc:
            raise AuthError("session backend unavailable") from exc
        if not raw:
            raise AuthError("session expired or invalid")
        data = json.loads(raw)
        return Principal(UUID(data["user_id"]), data["username"], frozenset(data["roles"]))

    def logout(self, token: str | None) -> None:
        if self.settings.auth_mode == "disabled" or not token:
            return
        try:
            redis_client().delete(f"widegold:session:{token}")
        except Exception:
            # Logout remains safe if deletion fails because session TTL is bounded.
            pass
