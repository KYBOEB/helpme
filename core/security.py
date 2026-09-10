"""
Защита от некорректных запросов. ВЛАДЕЛЕЦ: A.

Требование организаторов: приложение нельзя сломать запросами в обход браузера.
Базовый принцип — клиент ничего не решает. Он присылает текст и нажатия,
всё остальное определяет сервер.
"""
from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException

# ------------------------------------------------------------------ токены

def new_ticket_id() -> str:
    return "t_" + secrets.token_hex(6)


def new_token() -> str:
    return "s_" + secrets.token_urlsafe(24)


def check_owner(ticket, token: str | None) -> None:
    """Чужой или отсутствующий токен = обращения не существует.

    Отдаём именно 404, а не 403: так снаружи не отличить «нет доступа»
    от «нет такого обращения», и перебором чужие id не нащупать.
    """
    if ticket is None or not token or not secrets.compare_digest(ticket.token, token):
        raise HTTPException(status_code=404, detail="Обращение не найдено")


# ------------------------------------------------------- допустимые переходы

# какие состояния готовы принять очередное сообщение пользователя
ACCEPTS_INPUT = {"NEW", "CLASSIFYING", "CLARIFYING", "SOLVING", "VERIFYING"}


def assert_can_accept(ticket) -> None:
    if ticket.state not in ACCEPTS_INPUT:
        raise HTTPException(
            status_code=409,
            detail="Обращение уже закрыто. Начните новое.",
        )


# ------------------------------------------------------------ частота запросов

_WINDOW_SECONDS = 60
_MAX_REQUESTS = 20
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str) -> None:
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > _WINDOW_SECONDS:
        q.popleft()
    if len(q) >= _MAX_REQUESTS:
        raise HTTPException(status_code=429,
                            detail="Слишком много сообщений подряд. Подождите немного.")
    q.append(now)
