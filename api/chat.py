"""POST /api/chat — единственный эндпоинт диалога. ВЛАДЕЛЕЦ: A."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from common.models import ChatRequest, ChatResponse
from core import dialog
from db import repo

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
    client_key = req.ticket_id or (request.client.host if request.client else "anon")
    db = repo.get_session()
    try:
        return dialog.handle(db, req, client_key)
    finally:
        db.close()
