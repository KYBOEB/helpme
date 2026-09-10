"""
api/export.py — выгрузка данных и интеграции.

Отвечает за:
- CSV всех обращений (utf-8-sig, разделитель ;) — чтобы Excel в русской локали
  открывал без кракозябр и в правильных колонках;
- JSON карточки одного обращения с Content-Disposition: attachment;
- публичную ссылку /s/{share_token} — включается и выключается;
- вебхук во внешний Service Desk (не роняет основной сценарий).

Роутеры:
- router         (prefix="/api")  — export.csv, export.json, share
- public_router  (без префикса)   — GET /s/{share_token}

Модель карточки берём из common/models.py — TicketCard. Свою копию не заводим.
Доступ к БД — через db.repo.get_session() и готовые функции:
    repo.list_tickets, repo.get_ticket, repo.slots_dict, repo.history, repo.log_event.
"""

from __future__ import annotations

import csv
import html
import io
import json
import os
import secrets
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from common.models import TicketCard
from db import repo
from security import check_owner  # проверка владения токеном (404 при провале)


# ---------------------------------------------------------------- роутеры

router = APIRouter(prefix="/api", tags=["export"])
public_router = APIRouter(tags=["public"])


# ---------------------------------------------------------------- схемы

class ShareIn(BaseModel):
    """Тело POST /api/tickets/{id}/share."""
    enabled: bool
    # token приходит от клиента — тот же, что в ChatResponse.
    # Проверяется через security.check_owner.
    token: str | None = None


# ---------------------------------------------------------------- утилиты

CSV_COLUMNS = [
    "ticket_id",
    "created_at",
    "category",
    "article_id",
    "confidence",
    "problem_summary",
    "resolved_by_bot",
    "needs_specialist",
    "user_actions_count",
    "rating",
]


def _fmt_dt(value) -> str:
    """Приводим created_at к ISO-строке. Поле может быть str или datetime."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


def _card_to_dict(card: TicketCard) -> dict:
    """
    Карточка в формате TicketCard, без чувствительных полей.
    Токен владения и share_token в JSON-выгрузку НЕ попадают.
    """
    data = card.model_dump() if hasattr(card, "model_dump") else dict(card)
    # На всякий случай вырезаем чувствительные поля, даже если они появятся
    # в модели в будущем.
    for secret in ("token", "share_token"):
        data.pop(secret, None)
    return data


def _build_card(db, ticket) -> TicketCard:
    """Собираем TicketCard из объекта Ticket и связанных данных."""
    slots = repo.slots_dict(db, ticket.ticket_id) or {}
    history = repo.history(db, ticket.ticket_id, limit=1000) or []

    # Пробуем разные способы получить поля. У TicketCard часть полей может
    # отсутствовать в БД и вычисляться — тогда просто отдаём то, что есть.
    return TicketCard(
        ticket_id=ticket.ticket_id,
        category=getattr(ticket, "category", "") or "",
        problem_summary=getattr(ticket, "problem_summary", "") or "",
        slots=slots,
        steps_done=list(getattr(ticket, "steps_done", []) or []),
        resolved_by_bot=bool(getattr(ticket, "resolved_by_bot", False)),
        needs_specialist=bool(getattr(ticket, "needs_specialist", False)),
        article_id=getattr(ticket, "article_id", None),
        created_at=_fmt_dt(getattr(ticket, "created_at", None)),
    )


# ---------------------------------------------------------------- CSV

@router.get("/tickets/export.csv")
def export_csv():
    """
    Все обращения одной таблицей.

    - Кодировка utf-8-sig (BOM) — иначе Excel в русской локали покажет
      кракозябры вместо кириллицы.
    - Разделитель ';' — иначе Excel сложит всё в одну колонку.
    """
    db = repo.get_session()
    try:
        tickets = repo.list_tickets(db, limit=10000)

        buf = io.StringIO()
        # csv.writer с dialect 'excel' + delimiter=';'
        writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL,
                            lineterminator="\r\n")

        writer.writerow(CSV_COLUMNS)

        for t in tickets:
            writer.writerow([
                getattr(t, "ticket_id", ""),
                _fmt_dt(getattr(t, "created_at", None)),
                getattr(t, "category", "") or "",
                getattr(t, "article_id", "") or "",
                getattr(t, "confidence", "") or "",
                getattr(t, "problem_summary", "") or "",
                int(bool(getattr(t, "resolved_by_bot", False))),
                int(bool(getattr(t, "needs_specialist", False))),
                getattr(t, "user_actions_count", "") or "",
                getattr(t, "rating", "") or "",
            ])

        payload = buf.getvalue().encode("utf-8-sig")

        filename = f"tickets_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "text/csv; charset=utf-8",
        }
        return StreamingResponse(iter([payload]), headers=headers, media_type="text/csv")
    finally:
        db.close()


# ---------------------------------------------------------------- JSON

@router.get("/tickets/{ticket_id}/export.json")
def export_json(ticket_id: str):
    """
    Карточка одного обращения в формате TicketCard.
    Отдаётся с Content-Disposition: attachment — браузер скачает файл.
    """
    db = repo.get_session()
    try:
        ticket = repo.get_ticket(db, ticket_id)
        if ticket is None:
            # Человеческий 404, а не трейсбек.
            raise HTTPException(status_code=404, detail="Обращение не найдено")

        card = _build_card(db, ticket)
        data = _card_to_dict(card)
        body = json.dumps(data, ensure_ascii=False, indent=2)

        filename = f"ticket_{ticket_id}.json"
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "application/json; charset=utf-8",
        }
        return JSONResponse(content=json.loads(body), headers=headers)
    finally:
        db.close()


# ---------------------------------------------------------------- share

@router.post("/tickets/{ticket_id}/share")
def share_ticket(ticket_id: str, payload: ShareIn, request: Request):
    """
    Включить или выключить публичный доступ к обращению.

    enabled=true  — генерируем случайный токен, пишем в share_token, возвращаем URL.
    enabled=false — обнуляем share_token, ссылка перестаёт работать.

    Владение проверяем через security.check_owner — при провале он поднимет 404.
    """
    db = repo.get_session()
    try:
        ticket = repo.get_ticket(db, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Обращение не найдено")

        # check_owner сам бросит нужное исключение (404),
        # если токен чужой или отсутствует.
        check_owner(ticket, payload.token)

        if payload.enabled:
            token = secrets.token_urlsafe(16)
            ticket.share_token = token
            repo.log_event(db, ticket_id, "share_enabled", {"token_hint": token[:6]})
            url = str(request.base_url).rstrip("/") + f"/s/{token}"
            return {"enabled": True, "url": url, "token": token}

        # выключение
        ticket.share_token = None
        repo.log_event(db, ticket_id, "share_disabled", {})
        return {"enabled": False, "url": None, "token": None}
    finally:
        db.close()


# ---------------------------------------------------------------- публичная страница

def _render_public_card(card: TicketCard) -> str:
    """Простая HTML-страница карточки. Без шаблонизатора, без CDN."""
    steps_html = "".join(
        f"<li>{html.escape(str(s))}</li>" for s in (card.steps_done or [])
    )
    slots_html = "".join(
        f"<li><b>{html.escape(str(k))}:</b> {html.escape(str(v))}</li>"
        for k, v in (card.slots or {}).items()
    )

    status = "решено ботом" if card.resolved_by_bot else (
        "передано специалисту" if card.needs_specialist else "в работе"
    )

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Обращение {html.escape(card.ticket_id)}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
           background:#FAFAFA; color:#1A1A1A; margin:0; padding:24px; }}
    .card {{ background:#fff; border-radius:12px; padding:24px; max-width:720px;
            margin:0 auto; box-shadow:0 2px 12px rgba(0,0,0,.06); }}
    h1 {{ margin:0 0 4px; font-size:20px; }}
    .muted {{ color:#5A5A5A; font-size:.9rem; }}
    dl {{ display:grid; grid-template-columns:auto 1fr; gap:4px 16px; margin:16px 0; }}
    dt {{ color:#5A5A5A; }}
    dd {{ margin:0; }}
    h2 {{ font-size:16px; margin:20px 0 8px; }}
    ol, ul {{ margin:0; padding-left:20px; }}
    li {{ margin:4px 0; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Обращение {html.escape(card.ticket_id)}</h1>
    <div class="muted">Статус: {html.escape(status)}</div>

    <dl>
      <dt>Категория</dt><dd>{html.escape(card.category or "—")}</dd>
      <dt>Статья</dt><dd>{html.escape(card.article_id or "—")}</dd>
      <dt>Создано</dt><dd>{html.escape(card.created_at or "—")}</dd>
    </dl>

    <h2>Описание</h2>
    <p>{html.escape(card.problem_summary or "—")}</p>

    {f'<h2>Уточнения</h2><ul>{slots_html}</ul>' if slots_html else ''}
    {f'<h2>Выполненные шаги</h2><ol>{steps_html}</ol>' if steps_html else ''}

    <div class="muted" style="margin-top:24px">
      Публичный просмотр. Доступ можно отозвать в панели оператора.
    </div>
  </div>
</body>
</html>"""


@public_router.get("/s/{share_token}", response_class=HTMLResponse)
def public_ticket(share_token: str):
    """
    Публичный просмотр обращения по токену. Без авторизации.
    404 с человеческим текстом, если токен не найден или отключён.
    """
    db = repo.get_session()
    try:
        # Ищем тикет по share_token. Если repo не умеет — перебираем через list.
        ticket = None
        finder = getattr(repo, "get_by_share_token", None)
        if callable(finder):
            ticket = finder(db, share_token)
        else:
            for t in repo.list_tickets(db, limit=10000):
                if getattr(t, "share_token", None) == share_token:
                    ticket = t
                    break

        if ticket is None:
            return HTMLResponse(
                status_code=404,
                content=_not_found_page("Ссылка не найдена или была отключена."),
            )

        card = _build_card(db, ticket)
        return HTMLResponse(content=_render_public_card(card))
    finally:
        db.close()


def _not_found_page(message: str) -> str:
    """Человеческая 404-страница вместо трейсбека."""
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>404 — не найдено</title>
  <style>
    body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
           background:#FAFAFA; display:grid; place-items:center; min-height:100vh;
           margin:0; padding:24px; color:#1A1A1A; }}
    .card {{ background:#fff; border-radius:12px; padding:32px; max-width:480px;
            box-shadow:0 2px 12px rgba(0,0,0,.06); text-align:center; }}
    h1 {{ margin:0 0 8px; font-size:20px; }}
    p {{ color:#5A5A5A; line-height:1.5; margin:0; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Ссылка недоступна</h1>
    <p>{html.escape(message)}</p>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------- вебхук

def send_webhook(card: TicketCard) -> bool:
    """
    POST карточки на URL из переменной WEBHOOK_URL.

    - таймаут 5 секунд;
    - ошибки НЕ пробрасываются наружу — интеграция не должна ронять
      основной сценарий;
    - возвращает True при успехе, False при любой ошибке.

    Тимлид вызывает эту функцию при эскалации обращения.
    """
    url = os.environ.get("WEBHOOK_URL")
    if not url:
        # Переменная не задана — просто ничего не делаем.
        return False

    try:
        payload = _card_to_dict(card)
        with httpx.Client(timeout=5.0) as client:
            r = client.post(url, json=payload,
                            headers={"Content-Type": "application/json"})
            return 200 <= r.status_code < 300
    except Exception:
        # Никаких raise — вебхук не должен ломать сценарий.
        return False