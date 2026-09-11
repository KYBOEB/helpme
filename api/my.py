"""
История обращений посетителя.

Два пункта из списка дополнительных возможностей кейса — «история обращений»
и «возможность вернуться к предыдущему обращению» — закрываются этим модулем
и кнопкой «Проблема вернулась» в чате.

Автомат диалога здесь не работает: эндпоинт только читает. Просмотр самой
переписки отдельного эндпоинта не требует — его уже делает
`POST /api/chat/updates` с `full=true`, и на закрытых обращениях он работает
так же, как на активных.
"""
from __future__ import annotations

from fastapi import APIRouter, Body

from core import security
from db import repo
from db.models import iso_utc

router = APIRouter(prefix="/api", tags=["my"])

_LIMIT = 20


def _row(ticket, parent_no: int | None) -> dict:
    return {
        "ticket_id": ticket.id,
        # Токен нужен браузеру, чтобы открыть переписку через /api/chat/updates
        # и создать повторное обращение. Он и так лежит в браузере у активного
        # обращения — здесь возвращаются токены остальных обращений того же
        # посетителя, и ничьих больше.
        "token": ticket.token,
        "public_no": ticket.public_no,
        "parent_public_no": parent_no,
        "category": ticket.category,
        "problem_summary": ticket.problem_summary,
        "state": ticket.state,
        "resolved_by_bot": bool(ticket.resolved_by_bot),
        "needs_specialist": bool(ticket.needs_specialist),
        "operator_taken": bool(ticket.operator_taken),
        "out_of_scope": bool(ticket.out_of_scope),
        "closed_by_user": bool(ticket.closed_by_user),
        "assist_used": bool(ticket.assist_used),
        "rating": ticket.rating,
        "created_at": iso_utc(ticket.created_at),
        "updated_at": iso_utc(ticket.updated_at),
    }


@router.post("/my/tickets")
def my_tickets(payload: dict = Body(default={})) -> dict:
    """Последние обращения этого посетителя.

    POST, а не GET, по той же причине, что и у `/api/chat/updates`:
    идентификатор посетителя — секрет, и в строке запроса он осел бы
    в журналах обратного прокси и в истории браузера.

    Неизвестный идентификатор получает пустой список и код 200, а не 404.
    Иначе эндпоинт превращается в оракул: перебором можно было бы отличать
    живые идентификаторы от выдуманных.
    """
    client_id = str(payload.get("client_id") or "")
    hashed = security.client_hash(client_id)
    if not hashed:
        return {"tickets": []}

    # Свой ключ ограничения частоты: список запрашивается редко, и он не должен
    # ни съедать бюджет сообщений, ни давать перебирать идентификаторы.
    security.rate_limit(f"my:{hashed[:16]}", max_requests=60)

    db = repo.get_session()
    try:
        tickets = repo.tickets_for_client(db, hashed, limit=_LIMIT)
        # Номера родительских обращений одним проходом, без запроса на строку.
        parents = {t.parent_ticket_id for t in tickets if t.parent_ticket_id}
        parent_no = {pid: repo.public_no_of(db, pid) for pid in parents}
        return {"tickets": [_row(t, parent_no.get(t.parent_ticket_id))
                            for t in tickets]}
    finally:
        db.close()
