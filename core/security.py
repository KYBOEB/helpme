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
    if ticket is None or not token:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    # Сравниваем байты: compare_digest не работает с не-ASCII строками и
    # на подделанном токене с кириллицей падал бы с 500 вместо честного 404.
    if not secrets.compare_digest(ticket.token.encode("utf-8"),
                                  str(token).encode("utf-8")):
        raise HTTPException(status_code=404, detail="Обращение не найдено")


# ------------------------------------------------------- допустимые переходы

# какие состояния готовы принять очередное сообщение пользователя
ACCEPTS_INPUT = {"NEW", "CLASSIFYING", "CLARIFYING", "SOLVING", "VERIFYING"}


def assert_can_accept(ticket) -> None:
    # ESCALATED — обращение у живого специалиста. Молчать в ответ на сообщение
    # пользователя здесь было бы хамством: реплику принимаем и кладём в переписку,
    # которую специалист видит в панели. Автомат при этом не запускается.
    if ticket.state == "ESCALATED":
        return
    if ticket.state not in ACCEPTS_INPUT:
        raise HTTPException(
            status_code=409,
            detail="Обращение уже закрыто. Начните новое.",
        )


# ------------------------------------------------------------ частота запросов

_WINDOW_SECONDS = 60
_MAX_REQUESTS = 30
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, max_requests: int = _MAX_REQUESTS) -> None:
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > _WINDOW_SECONDS:
        q.popleft()
    if len(q) >= max_requests:
        raise HTTPException(status_code=429,
                            detail="Слишком много сообщений подряд. Подождите немного.")
    q.append(now)
