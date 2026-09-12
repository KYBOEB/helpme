"""POST /api/chat — единственный эндпоинт диалога."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Body, Request

from common.models import ChatRequest, ChatResponse
from core import dialog, security
from db import repo

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


def _client_key(req: ChatRequest, request: Request) -> str:
    """Ключ для ограничения частоты.

    За обратным прокси request.client.host — это адрес самого Caddy, один на всех.
    Если оставить так, во время демонстрации несколько человек с разных устройств
    делили бы общий лимит и ловили 429 друг из-за друга. Поэтому сначала берём
    номер обращения, затем первый адрес из X-Forwarded-For.
    """
    if req.ticket_id:
        return req.ticket_id
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "anon"


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
    client_key = _client_key(req, request)
    db = repo.get_session()
    try:
        return dialog.handle(db, req, client_key)
    finally:
        db.close()


# Опрос делаем POST, а не GET: токен обращения — секрет, и в строке запроса он
# осел бы в журналах прокси и в истории браузера. В теле запроса он не осядет.
def _message_out(m: dict) -> dict:
    """Одно сообщение для страницы чата: текст и, для реплик бота, шаги."""
    text, steps = (dialog.split_steps(m["content"])
                   if m["role"] == "assistant" else (m["content"], []))
    return {"id": m["id"], "role": m["role"], "text": text,
            "steps": steps, "created_at": m["created_at"]}


@router.post("/chat/updates")
def chat_updates(payload: dict = Body(...)) -> dict:
    """Новые реплики специалиста по обращению.

    Страница чата вызывает это, только пока обращение передано человеку.
    Конечный автомат здесь не работает: эндпоинт ничего не меняет, только читает.
    """
    ticket_id = str(payload.get("ticket_id") or "")
    after = payload.get("after", 0)
    try:
        after = max(0, int(after))
    except (TypeError, ValueError):
        after = 0
    # full=true — вся переписка целиком: нужна, когда страница восстанавливает
    # незавершённое обращение после перезагрузки. Обычный опрос берёт только
    # реплики специалиста, чтобы не гонять историю каждые шесть секунд.
    roles = (("user", "assistant", "operator") if payload.get("full")
             else ("operator",))

    db = repo.get_session()
    try:
        ticket = repo.get_ticket(db, ticket_id)
        security.check_owner(ticket, payload.get("token"))
        # Отдельный, более щедрый лимит: опрос раз в 5 секунд — это 12 запросов
        # в минуту, и он не должен съедать бюджет обычных сообщений.
        security.rate_limit(f"poll:{ticket_id}", max_requests=90)

        messages = repo.messages_after(db, ticket_id, after, roles=roles, limit=200)
        # Текущий набор шагов обращения — для живой карточки с кнопками,
        # которую страница рисует при восстановлении незавершённого диалога.
        # Шаги ПРОШЛЫХ реплик приходят вместе с самими сообщениями
        # (см. _message_out и core/dialog._stored_text).
        steps: list = []
        if payload.get("full"):
            try:
                steps = json.loads(ticket.steps_json or "[]")
            except ValueError:
                steps = []
        out = [_message_out(m) for m in messages]
        # Обращения, созданные ДО правки с записью шагов в переписку, шагов
        # в сообщениях не имеют. Чтобы старая демо-база не выглядела битой,
        # приклеиваем известный набор к последней реплике бота: именно после
        # неё шаги и показывались.
        if payload.get("full") and steps and not any(m["steps"] for m in out):
            for m in reversed(out):
                if m["role"] == "assistant":
                    m["steps"] = steps
                    break

        return {
            "ticket_id": ticket_id,
            # Короткий номер: в интерфейсе показывается он. До этой правки
            # страница печатала «Обращение №t_c18c55b3b7bd» — внутренний
            # идентификатор, который человеку не продиктовать.
            "public_no": ticket.public_no,
            "parent_public_no": repo.public_no_of(db, ticket.parent_ticket_id),
            "category": ticket.category,
            "confidence": round(ticket.confidence or 0.0, 2),
            "resolution": ticket.resolution,
            "rating": ticket.rating,
            "state": ticket.state,
            "operator_taken": bool(ticket.operator_taken),
            "steps": steps,
            "assist_used": bool(ticket.assist_used),
            # Шаги отделяются от текста реплики: в переписке они хранятся
            # одной записью (см. core/dialog._stored_text), а странице удобнее
            # получить их списком — она рисует их отдельным блоком.
            "messages": out,
            "last_id": messages[-1]["id"] if messages
                       else repo.last_message_id(db, ticket_id),
        }
    finally:
        db.close()
