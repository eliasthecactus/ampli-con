"""Web sign-in for the one user."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth, config, db

router = APIRouter(prefix="/api/auth")


class Creds(BaseModel):
    username: str
    password: str


class Change(BaseModel):
    current: str
    username: str | None = None
    new: str | None = None


def _set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", secure=request.url.scheme == "https",
                        max_age=config.SESSION_DAYS * 86400, path="/")


@router.get("/state")
def state(request: Request):
    return {"user": auth.session_user(request.cookies.get(auth.COOKIE)), "version": config.VERSION,
            "env_user": config.env_user_set()}


@router.post("/login")
def login(body: Creds, request: Request, response: Response):
    u = auth.check_login(body.username, body.password, auth.client_ip(request))
    _set_cookie(request, response, auth.create_session(u["id"]))
    return {"ok": True, "user": {"id": u["id"], "username": u["username"]}}


@router.post("/logout")
def logout(request: Request, response: Response):
    if tok := request.cookies.get(auth.COOKIE):
        db.execute("DELETE FROM sessions WHERE token = ?", [tok])
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.post("/change")
def change(body: Change, request: Request, response: Response, user: dict = Depends(auth.require_user)):
    u = auth.the_user()
    if not auth.verify_password(u["password_hash"], body.current):
        raise HTTPException(400, "The current password is wrong")
    try:
        auth.set_credentials(body.username or u["username"], body.new or body.current, sign_out=bool(body.new))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if body.new:
        _set_cookie(request, response, auth.create_session(u["id"]))
    return {"ok": True, "env_user": config.env_user_set()}
