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
_LOGIN = os.getenv("OPERATOR_LOGIN", "operator")


def is_configured() -> bool:
    return bool(_PASSWORD)


# ------------------------------------------------- источник учётных записей
#
# Единственное место, где система узнаёт, существует ли такой сотрудник
# и подходит ли пароль. Сейчас здесь одна демонстрационная запись
# из переменных окружения.
#
# Это точка подключения каталога сотрудников организации. Чтобы система
# начала пускать по рабочим учётным записям, достаточно заменить тело
# ОДНОЙ функции — обращением к LDAP, к таблице сотрудников или к провайдеру
# единого входа. Всё остальное — сессия, защита методов панели, права —
# останется как есть, потому что оно про эту функцию ничего не знает.
#
# Пароль сравнивается по хэшу, а не по строке: если задать
# OPERATOR_PASSWORD_SHA256, открытого пароля на сервере не будет вовсе.

_PASSWORD_SHA256 = os.getenv("OPERATOR_PASSWORD_SHA256", "").strip().lower()


def _password_matches(password: str) -> bool:
    """Сравнение постоянного времени: по длительности ответа пароль не подобрать."""
    password = password or ""
    if _PASSWORD_SHA256:
        digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, _PASSWORD_SHA256)
    if not _PASSWORD:
        return False
    # сравниваем байты: compare_digest не работает с не-ASCII строками
    return hmac.compare_digest(password.encode("utf-8"),
                               _PASSWORD.encode("utf-8"))


def find_operator(login: str, password: str) -> str | None:
    """Проверить учётные данные. Вернуть имя вошедшего или None.

    Логин необязателен: если его не прислали, работает прежний вход
    по одному паролю. Это сделано нарочно — чтобы правка не сломала
    уже открытые вкладки панели.
    """
    login = (login or "").strip()
    if login and not hmac.compare_digest(login.lower(), _LOGIN.lower()):
        return None
    if not _password_matches(password):
        return None
    return login or _LOGIN


def check_password(password: str) -> bool:
    """Оставлено для совместимости с прежним кодом."""
    return _password_matches(password)


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
