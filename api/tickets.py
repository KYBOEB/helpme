"""История обращений, оценка, эскалация, метрики. ВЛАДЕЛЕЦ: A."""
from __future__ import annotations

import json
from collections import Counter

from fastapi import APIRouter, Body, HTTPException

from core import security
from db import repo

router = APIRouter(prefix="/api", tags=["tickets"])


def _brief(t) -> dict:
    return {
        "ticket_id": t.id,
        "created_at": t.created_at.isoformat(),
        "state": t.state,
        "category": t.category,
        "article_id": t.article_id,
        "confidence": round(t.confidence, 2),
        "problem_summary": t.problem_summary,
        "resolved_by_bot": t.resolved_by_bot,
        "needs_specialist": t.needs_specialist,
        "user_actions_count": t.user_actions_count,
        "rating": t.rating,
    }


@router.get("/tickets")
def list_tickets() -> list[dict]:
    db = repo.get_session()
    try:
        return [_brief(t) for t in repo.list_tickets(db)]
    finally:
        db.close()


@router.get("/tickets/{ticket_id}")
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
    rating = payload.get("rating")
    if not isinstance(rating, int) or not 1 <= rating <= 5:
        raise HTTPException(status_code=422, detail="rating должен быть числом от 1 до 5")
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


@router.get("/stats")
def stats() -> dict:
    """Метрики дашборда. Главная — среднее число действий пользователя."""
    db = repo.get_session()
    try:
        tickets = repo.list_tickets(db, limit=10000)
        finished = [t for t in tickets if t.state in ("RESOLVED", "ESCALATED")]
        solved = [t for t in finished if t.resolved_by_bot]
        rated = [t.rating for t in tickets if t.rating]
        actions = [t.user_actions_count for t in solved]
        return {
            "total": len(tickets),
            "finished": len(finished),
            "resolved_by_bot": len(solved),
            "resolved_by_bot_share": round(len(solved) / len(finished), 3) if finished else 0.0,
            "avg_user_actions": round(sum(actions) / len(actions), 2) if actions else 0.0,
            "by_category": dict(Counter(t.category for t in tickets if t.category)),
            "avg_rating": round(sum(rated) / len(rated), 2) if rated else None,
            "ratings_count": len(rated),
        }
    finally:
        db.close()
