from fastapi import Cookie, HTTPException, status

from widegold.auth.service import AuthError, AuthService, Principal

COOKIE_NAME = "widegold_session"


def current_user(widegold_session: str | None = Cookie(default=None)) -> Principal:
    try:
        return AuthService().authenticate(widegold_session)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


def require_admin(user: Principal = None):
    # FastAPI injects current_user through the wrapper dependency declared below.
    return user


def admin_user(widegold_session: str | None = Cookie(default=None)) -> Principal:
    try:
        principal = AuthService().authenticate(widegold_session)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if not principal.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ADMIN role required")
    return principal
