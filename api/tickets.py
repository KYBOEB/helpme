"""История обращений, оценка, эскалация, метрики."""
from __future__ import annotations

import json
import os
from collections import Counter

from fastapi import APIRouter, Body, Depends, HTTPException

from core import security
from core.auth import require_operator
from db import repo
from db.models import iso_utc

router = APIRouter(prefix="/api", tags=["tickets"])


def _brief(t) -> dict:
    return {
        "ticket_id": t.id,
        "public_no": t.public_no,
        "created_at": iso_utc(t.created_at),
        "updated_at": iso_utc(t.updated_at),
        "state": t.state,
        "category": t.category,
        "article_id": t.article_id,
        "confidence": round(t.confidence, 2),
        "problem_summary": t.problem_summary,
        "resolved_by_bot": t.resolved_by_bot,
        "assist_used": t.assist_used,
        "operator_taken": t.operator_taken,
        "needs_specialist": t.needs_specialist,
        "out_of_scope": bool(t.out_of_scope),
        "closed_by_user": bool(t.closed_by_user),
        "user_actions_count": t.user_actions_count,
        "rating": t.rating,
        # Повторное обращение: специалисту важно видеть, что человек приходит
        # с этим второй раз, и что старое обращение закрывали зря.
        "parent_ticket_id": t.parent_ticket_id,
    }


@router.get("/tickets", dependencies=[Depends(require_operator)])
def list_tickets(state: str | None = None, queue: bool = False,
                 limit: int = 200) -> list[dict]:
    """Список обращений. queue=1 — только те, что ждут специалиста."""
    db = repo.get_session()
    try:
        limit = max(1, min(limit, 1000))
        return [_brief(t) for t in repo.list_tickets(db, limit=limit,
                                                     state=state, queue=queue)]
    finally:
        db.close()


@router.post("/tickets/{ticket_id}/take", dependencies=[Depends(require_operator)])
def take(ticket_id: str) -> dict:
    """Оператор взял обращение в работу — оно уходит из очереди."""
    db = repo.get_session()
    try:
        t = repo.get_ticket(db, ticket_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Обращение не найдено")
        t.operator_taken = True
        repo.log_event(db, ticket_id, "operator_took")
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.post("/tickets/{ticket_id}/reply", dependencies=[Depends(require_operator)])
def operator_reply(ticket_id: str, payload: dict = Body(...)) -> dict:
    """Ответ специалиста пользователю прямо из панели.

    С этого момента диалог ведёт человек: обращение помечается как взятое,
    а бот в переписку больше не вмешивается — он только доставляет реплики.
    """
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Текст ответа пуст")
    if len(text) > 2000:
        raise HTTPException(status_code=422, detail="Ответ длиннее 2000 символов")

    db = repo.get_session()
    try:
        t = repo.get_ticket(db, ticket_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Обращение не найдено")
        if t.out_of_scope:
            raise HTTPException(status_code=409,
                                detail="Обращение закрыто как нецелевое")

        repo.add_message(db, ticket_id, "operator", text)
        t.state = "ESCALATED"
        t.needs_specialist = True
        t.operator_taken = True
        t.resolved_by_bot = False
        repo.log_event(db, ticket_id, "operator_reply", {"length": len(text)})
        db.commit()
        return {"ok": True, "ticket_id": ticket_id, "state": t.state}
    finally:
        db.close()


@router.post("/tickets/{ticket_id}/resolve", dependencies=[Depends(require_operator)])
def resolve_by_operator(ticket_id: str) -> dict:
    """Специалист завершил работу по обращению."""
    db = repo.get_session()
    try:
        t = repo.get_ticket(db, ticket_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Обращение не найдено")
        t.state = "RESOLVED"
        t.needs_specialist = False
        t.operator_taken = True
        t.resolved_by_bot = False       # решил человек, а не бот
        t.resolution = t.resolution or "закрыто специалистом"
        repo.log_event(db, ticket_id, "resolved_by_operator")
        db.commit()
        return {"ok": True, "state": t.state}
    finally:
        db.close()


@router.get("/tickets/{ticket_id}", dependencies=[Depends(require_operator)])
def get_ticket(ticket_id: str) -> dict:
    db = repo.get_session()
    try:
        t = repo.get_ticket(db, ticket_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Обращение не найдено")
        return {
            **_brief(t),
            "slots": repo.slots_dict(db, ticket_id),
            "steps": json.loads(t.steps_json or "[]"),
            "messages": repo.history(db, ticket_id, limit=100),
        }
    finally:
        db.close()


@router.post("/tickets/{ticket_id}/rate")
def rate(ticket_id: str, payload: dict = Body(...)) -> dict:
    # Оценка бинарная: 1 — ответ помог, 0 — не помог. Из неё считается
    # доля полезных ответов в процентах. Средний балл по пятибалльной шкале
    # на таком объёме ничего не означал бы.
    rating = payload.get("rating")
    if rating not in (0, 1) or isinstance(rating, bool):
        raise HTTPException(status_code=422,
                            detail="rating должен быть 1 (помогло) или 0 (не помогло)")
    db = repo.get_session()
    try:
        t = repo.get_ticket(db, ticket_id)
        security.check_owner(t, payload.get("token"))
        t.rating = rating
        repo.log_event(db, ticket_id, "rated", {"rating": rating})
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.post("/tickets/{ticket_id}/escalate")
def escalate(ticket_id: str, payload: dict = Body(default={})) -> dict:
    db = repo.get_session()
    try:
        t = repo.get_ticket(db, ticket_id)
        security.check_owner(t, payload.get("token"))
        t.state = "ESCALATED"
        t.needs_specialist = True
        t.resolved_by_bot = False
        t.resolution = "пользователь запросил специалиста"
        repo.log_event(db, ticket_id, "escalated", {"reason": "по просьбе пользователя"})
        db.commit()
        return {"ok": True, "ticket_id": ticket_id}
    finally:
        db.close()


@router.post("/tickets/{ticket_id}/close")
def close_by_user(ticket_id: str, payload: dict = Body(default={})) -> dict:
    """Пользователь вышел из диалога — обращение закрывается.

    Без этого брошенные обращения висели в панели как активные, и оператор
    не мог отличить их от тех, где человек действительно ждёт ответа.
    Обращение, которое уже взял в работу специалист, не трогаем: он ведёт
    переписку и закроет её сам.
    """
    db = repo.get_session()
    try:
        t = repo.get_ticket(db, ticket_id)
        security.check_owner(t, payload.get("token"))
        if t.state in ("RESOLVED",) or t.operator_taken:
            return {"ok": True, "state": t.state}
        t.state = "RESOLVED"
        t.closed_by_user = True
        t.resolved_by_bot = False
        t.needs_specialist = False
        t.resolution = "закрыто пользователем: вышел из диалога"
        repo.log_event(db, ticket_id, "closed_by_user")
        db.commit()
        return {"ok": True, "state": t.state}
    finally:
        db.close()


# Окна для выбора периода на вкладке «Аналитика»
_PERIODS = {"day": 1, "week": 7, "month": 30, "all": None}


@router.get("/stats", dependencies=[Depends(require_operator)])
def stats(period: str = "all") -> dict:
    """Метрики дашборда. Главная — среднее число действий пользователя."""
    import datetime as _dt

    days = _PERIODS.get(period, None)
    # created_at лежит в базе без часового пояса, поэтому и границу считаем
    # в UTC без пояса — иначе сравнение упадёт на разнице типов.
    since = (_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
             - _dt.timedelta(days=days)) if days else None

    db = repo.get_session()
    try:
        tickets = repo.list_tickets(db, limit=10000)
        if since is not None:
            tickets = [t for t in tickets if t.created_at >= since]
        rejected = [t for t in tickets if t.out_of_scope]
        # Нецелевые обращения не участвуют в доле решённых: они не были задачами
        # поддержки, и портить ими метрику так же нечестно, как ею хвастаться.
        finished = [t for t in tickets
                    if t.state in ("RESOLVED", "ESCALATED") and not t.out_of_scope]
        solved = [t for t in finished if t.resolved_by_bot]
        # Оценка бинарная: 1 — помогло, 0 — нет. Считаем долю полезных ответов.
        # Значения прижимаем к 0..1: в базе могли остаться оценки старой
        # пятибалльной шкалы, и без этого «полезность» показывала 340 %.
        rated = [min(1, max(0, int(t.rating))) for t in tickets if t.rating is not None]
        actions = [t.user_actions_count for t in solved]

        # среднее время от создания обращения до первого шага решения
        from db.models import Event
        ids = {t.id for t in tickets}
        first_step: list[float] = []
        created = {e.ticket_id: e.created_at for e in
                   db.query(Event).filter(Event.type == "created").all()}
        for e in db.query(Event).filter(Event.type.in_(("solved", "assisted"))).all():
            if e.ticket_id not in ids:
                continue                      # обращение вне выбранного периода
            start = created.get(e.ticket_id)
            if start:
                first_step.append((e.created_at - start).total_seconds() * 1000)

        accuracy_env = os.getenv("CLASSIFICATION_ACCURACY", "").strip()
        return {
            "total": len(tickets),
            "finished": len(finished),
            "resolved_by_bot": len(solved),
            "waiting_operator": len([t for t in tickets
                                     if t.needs_specialist and not t.operator_taken]),
            "assist_used": len([t for t in tickets if t.assist_used]),
            "out_of_scope": len(rejected),
            "resolved_by_bot_share": round(len(solved) / len(finished), 3) if finished else 0.0,
            "avg_user_actions": round(sum(actions) / len(actions), 2) if actions else 0.0,
            "by_category": dict(Counter(t.category for t in tickets if t.category)),
            # доля ответов, которые пользователи отметили как полезные, 0..1
            "usefulness": round(sum(rated) / len(rated), 3) if rated else None,
            "ratings_count": len(rated),
            "period": period if period in _PERIODS else "all",
            "closed_by_user": len([t for t in tickets if t.closed_by_user]),
            # дублируем под именами, которые использует панель
            "total_count": len(tickets),
            "resolved_by_bot_count": len(solved),
            "rating_count": len(rated),
            "avg_time_to_first_step_ms": round(sum(first_step) / len(first_step))
                                          if first_step else None,
            "classification_accuracy": float(accuracy_env) if accuracy_env else None,
        }
    finally:
        db.close()


# ---------------------------------------------------------------- база знаний

@router.get("/kb/search")
def kb_search(q: str = "", limit: int = 10) -> list[dict]:
    """Поиск по базе знаний. Используется вкладкой «База знаний» в панели."""
    from core import dialog  # импорт здесь, чтобы не тянуть модель при старте роутера

    if len(q) > 500:
        raise HTTPException(status_code=422, detail="Слишком длинный запрос")

    limit = max(1, min(limit, 50))

    if not q.strip():
        articles = [(a, 0.0) for a in dialog._articles[:limit]]
    else:
        articles = dialog._retriever.search(q, top_k=limit)

    return [{
        "id": a.id,
        "category": a.category,
        "title": a.title,
        "score": round(score, 3),
        "symptoms": a.symptoms,
        "steps": a.steps,
        "required_slots": [s.key for s in a.required_slots if not s.optional],
        "has_solution": bool(a.steps),
    } for a, score in articles]


@router.get("/kb/gaps", dependencies=[Depends(require_operator)])
def kb_gaps(limit: int = 100) -> list[dict]:
    """Обращения, для которых в базе знаний не нашлось статьи.

    Готовый список тем для новых карточек: база растёт на реальных обращениях.
    """
    import json as _json

    from db.models import Event

    db = repo.get_session()
    try:
        rows = (db.query(Event)
                  .filter(Event.type.in_(("kb_gap", "kb_insufficient")))
                  .order_by(Event.created_at.desc())
                  .limit(max(1, min(limit, 500))).all())
        out = []
        for e in rows:
            try:
                payload = _json.loads(e.payload or "{}")
            except ValueError:
                payload = {}
            out.append({
                "kind": "нет статьи" if e.type == "kb_gap" else "статья не помогла",
                "article_id": payload.get("article_id"),
                "ticket_id": e.ticket_id,
                "created_at": iso_utc(e.created_at),
                "query": payload.get("query", ""),
                "category": payload.get("category"),
            })
        return out
    finally:
        db.close()


@router.post("/kb/articles", dependencies=[Depends(require_operator)])
def create_article(payload: dict = Body(...)) -> dict:
    """Добавить карточку в базу знаний прямо из панели оператора.

    Карточка сразу попадает в поиск — перезапуск не нужен.
    """
    from core import dialog, kb_store

    try:
        article = kb_store.add_article(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    total = dialog.reload_kb()
    return {"ok": True, "id": article.id, "articles_total": total}
