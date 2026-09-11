"""Вход оператора в панель."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse

from core import auth

router = APIRouter(tags=["operator"])


@router.post("/api/operator/login")
def login(response: Response, payload: dict = Body(...)) -> dict:
    if not auth.is_configured():
        raise HTTPException(status_code=503,
                            detail="Пароль оператора не задан на сервере")
    if not auth.check_password(str(payload.get("password", ""))):
        raise HTTPException(status_code=401, detail="Неверный пароль")

    response.set_cookie(
        auth.COOKIE_NAME, auth.make_token(),
        max_age=auth.TTL_SECONDS, httponly=True, samesite="lax", path="/",
    )
    return {"ok": True}


@router.post("/api/operator/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/api/operator/me")
def me(request: Request) -> dict:
    return {"authorized": auth.verify_token(request.cookies.get(auth.COOKIE_NAME))}


@router.get("/operator")
def panel(request: Request):
    """Единственный вход в панель. Без сессии — форма входа."""
    if auth.verify_token(request.cookies.get(auth.COOKIE_NAME)):
        return RedirectResponse("/static/admin.html", status_code=302)
    return FileResponse("static/login.html")
