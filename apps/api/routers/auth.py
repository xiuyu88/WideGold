from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel

from apps.api.dependencies import COOKIE_NAME, current_user
from widegold.auth.service import AuthError, AuthService, Principal
from widegold.settings.app import get_settings

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginRequest, response: Response):
    try:
        token, principal = AuthService().login(body.username, body.password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if get_settings().auth_mode != "disabled":
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            httponly=True,
            secure=get_settings().cookie_secure,
            samesite="lax",
            max_age=get_settings().session_ttl_seconds,
            path="/",
        )
    return {"username": principal.username, "roles": sorted(principal.roles)}


@router.post("/auth/logout", status_code=204)
def logout(response: Response, widegold_session: str | None = Cookie(default=None)):
    AuthService().logout(widegold_session)
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/auth/me")
def me(user: Principal = Depends(current_user)):
    return {"user_id": str(user.user_id) if user.user_id else None, "username": user.username, "roles": sorted(user.roles)}
