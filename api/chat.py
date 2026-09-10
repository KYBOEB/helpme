from __future__ import annotations

from fastapi import APIRouter

from common.models import ChatRequest, ChatResponse, Reply

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    # TODO(A): передать в core.dialog
    return ChatResponse(
        ticket_id="t_demo",
        token="s_demo",
        state="NEW",
        reply=Reply(type="error", text="Ядро диалога ещё не подключено"),
    )
