"""
Выгрузка данных и публичные ссылки. ВЛАДЕЛЕЦ: A.

Организаторы отдельно называли ценностью выгрузку данных, передачу их в другие
системы и управление доступом по ссылке — здесь всё это.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import secrets

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from core.auth import require_operator
from db import repo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["export"])
public_router = APIRouter(tags=["public"])

CSV_COLUMNS = [
    "ticket_id", "created_at", "state", "category", "article_id", "confidence",
    "problem_summary", "resolved_by_bot", "needs_specialist", "assist_used",
    "user_actions_count", "rating",
]


@router.get("/tickets/export.csv", dependencies=[Depends(require_operator)])
def export_csv() -> StreamingResponse:
    """Выгрузка всех обращений. UTF-8 с BOM и точка с запятой — чтобы Excel открыл как надо."""
    db = repo.get_session()
    try:
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(CSV_COLUMNS)
        for t in repo.list_tickets(db, limit=10000):
            writer.writerow([
                t.id, t.created_at.isoformat(), t.state, t.category or "",
                t.article_id or "", round(t.confidence, 2), t.problem_summary,
                int(t.resolved_by_bot), int(t.needs_specialist), int(t.assist_used),
                t.user_actions_count, t.rating if t.rating is not None else "",
            ])
        data = "\ufeff" + buf.getvalue()
    finally:
        db.close()

    return StreamingResponse(
        io.BytesIO(data.encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="tickets.csv"'},
    )


def _card_dict(db, t) -> dict:
    return {
        "ticket_id": t.id,
        "created_at": t.created_at.isoformat(),
        "category": t.category,
        "article_id": t.article_id,
        "confidence": round(t.confidence, 2),
        "problem_summary": t.problem_summary,
        "slots": repo.slots_dict(db, t.id),
        "steps_done": json.loads(t.steps_json or "[]"),
        "resolved_by_bot": t.resolved_by_bot,
        "needs_specialist": t.needs_specialist,
        "assist_used": t.assist_used,
        "resolution": t.resolution,
        "messages": repo.history(db, t.id, limit=200),
    }


@router.get("/tickets/{ticket_id}/export.json", dependencies=[Depends(require_operator)])
def export_json(ticket_id: str) -> JSONResponse:
    """Карточка обращения в формате, который откроет любая внешняя система."""
    db = repo.get_session()
    try:
        t = repo.get_ticket(db, ticket_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Обращение не найдено")
        return JSONResponse(
            _card_dict(db, t),
            headers={"Content-Disposition": f'attachment; filename="{ticket_id}.json"'},
        )
    finally:
        db.close()


@router.post("/tickets/{ticket_id}/share", dependencies=[Depends(require_operator)])
def share(ticket_id: str, payload: dict = Body(default={})) -> dict:
    """Включить или выключить публичную ссылку на обращение."""
    enabled = bool(payload.get("enabled"))
    db = repo.get_session()
    try:
        t = repo.get_ticket(db, ticket_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Обращение не найдено")

        if enabled:
            t.share_token = t.share_token or secrets.token_urlsafe(16)
            url = f"/s/{t.share_token}"
        else:
            t.share_token = None
            url = None

        repo.log_event(db, ticket_id, "share_toggled", {"enabled": enabled})
        db.commit()
        return {"ok": True, "enabled": enabled, "url": url}
    finally:
        db.close()


@public_router.get("/s/{share_token}", response_class=HTMLResponse)
def public_view(share_token: str) -> HTMLResponse:
    """Просмотр обращения по ссылке. Доступ выключили — ссылка перестаёт работать."""
    from db.models import Ticket

    db = repo.get_session()
    try:
        t = db.query(Ticket).filter(Ticket.share_token == share_token).first()
        if t is None:
            raise HTTPException(status_code=404,
                                detail="Ссылка недействительна или доступ закрыт")

        def esc(x: object) -> str:
            return (str(x).replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace('"', "&quot;"))

        steps = json.loads(t.steps_json or "[]")
        rows = "".join(
            f'<div class="m m-{esc(m["role"])}">{esc(m["content"])}</div>'
            for m in repo.history(db, t.id, limit=200)
        )
        html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Обращение {esc(t.id)}</title>
<link rel="stylesheet" href="/static/style.css">
<style>
 .wrap{{max-width:44rem;margin:0 auto;padding:2rem 1.25rem}}
 .box{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
      padding:1.5rem;box-shadow:var(--shadow);margin-bottom:1rem}}
 .m{{padding:.625rem .875rem;border-radius:10px;margin-bottom:.5rem;max-width:85%}}
 .m-user{{background:var(--accent);color:#fff;margin-left:auto}}
 .m-assistant{{background:var(--bg);border:1px solid var(--border)}}
 dt{{font-size:.75rem;text-transform:uppercase;color:var(--text-muted);font-weight:700}}
 dd{{margin:0 0 .75rem}}
</style></head><body><div class="wrap">
<div class="box"><h1 style="margin:0 0 1rem;font-size:1.25rem">Обращение {esc(t.id)}</h1>
<dl><dt>Категория</dt><dd>{esc(t.category or "—")}</dd>
<dt>Проблема</dt><dd>{esc(t.problem_summary or "—")}</dd>
<dt>Результат</dt><dd>{esc(t.resolution or "в работе")}</dd>
<dt>Выполненные шаги</dt><dd>{esc("; ".join(steps)) or "—"}</dd></dl></div>
<div class="box"><h2 style="margin:0 0 1rem;font-size:1rem">Переписка</h2>{rows}</div>
<p style="text-align:center;color:var(--text-muted);font-size:.8125rem">
Ссылка выдана оператором поддержки и может быть отозвана</p>
</div></body></html>"""
        return HTMLResponse(html)
    finally:
        db.close()


def send_webhook(card) -> None:
    """Отправить карточку во внешнюю систему. Ошибки наружу не пробрасываются."""
    url = os.getenv("WEBHOOK_URL", "").strip()
    if not url:
        return
    import requests

    payload = card.model_dump() if hasattr(card, "model_dump") else dict(card)
    requests.post(url, json=payload, timeout=5)
    log.info("карточка %s отправлена на вебхук", payload.get("ticket_id"))
