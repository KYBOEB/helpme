"""POST /api/chat — единственный эндпоинт диалога. ВЛАДЕЛЕЦ: A."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from common.models import ChatRequest, ChatResponse
from core import dialog
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
