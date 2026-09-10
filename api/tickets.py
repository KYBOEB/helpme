from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["tickets"])


@router.get("/tickets")
def list_tickets():
    return []


@router.get("/stats")
def stats():
    return {
        "total": 0,
        "resolved_by_bot_share": 0.0,
        "avg_user_actions": 0.0,
        "by_category": {},
        "avg_rating": None,
    }
