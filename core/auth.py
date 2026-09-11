"""
Авторизация оператора.

Панель оператора показывает переписку пользователей — открывать её всему
интернету нельзя. Вход по паролю, сессия хранится в подписанной куке:
подделать её без SECRET_KEY невозможно, а сервер не хранит состояние сессий.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException, Request

COOKIE_NAME = "op_session"
TTL_SECONDS = 8 * 60 * 60

_SECRET = os.getenv("SECRET_KEY") or secrets.token_hex(32)
_PASSWORD = os.getenv("OPERATOR_PASSWORD", "")


def is_configured() -> bool:
    return bool(_PASSWORD)


def check_password(password: str) -> bool:
    """Сравнение постоянного времени: по длительности ответа пароль не подобрать."""
    if not _PASSWORD:
        return False
    # сравниваем байты: compare_digest не работает с не-ASCII строками
    return hmac.compare_digest((password or "").encode("utf-8"),
                               _PASSWORD.encode("utf-8"))


def _sign(payload: str) -> str:
    mac = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def make_token() -> str:
    exp = str(int(time.time()) + TTL_SECONDS)
    return f"{exp}.{_sign(exp)}"


def verify_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp, sig = token.split(".", 1)
    if not hmac.compare_digest(sig, _sign(exp)):
        return False
    try:
        return int(exp) > time.time()
    except ValueError:
        return False


def require_operator(request: Request) -> None:
    """Зависимость FastAPI для эндпоинтов панели."""
    if not verify_token(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="Требуется вход оператора")
