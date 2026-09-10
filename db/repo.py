"""Доступ к данным. ВЛАДЕЛЕЦ: A."""
from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base, Event, Idempotency, Message, SlotValue, Ticket

DB_PATH = os.getenv("DB_PATH", "data/helpme.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()


# ---------------------------------------------------------------- обращения

def get_ticket(db: Session, ticket_id: str) -> Ticket | None:
    return db.get(Ticket, ticket_id)


def list_tickets(db: Session, limit: int = 200) -> list[Ticket]:
    stmt = select(Ticket).order_by(Ticket.created_at.desc()).limit(limit)
    return list(db.scalars(stmt))


def add_message(db: Session, ticket_id: str, role: str, content: str) -> None:
    db.add(Message(ticket_id=ticket_id, role=role, content=content))


def history(db: Session, ticket_id: str, limit: int = 10) -> list[dict]:
    stmt = (select(Message).where(Message.ticket_id == ticket_id)
            .order_by(Message.created_at.desc()).limit(limit))
    rows = list(db.scalars(stmt))[::-1]
    return [{"role": m.role, "content": m.content} for m in rows]


def slots_dict(db: Session, ticket_id: str) -> dict[str, str]:
    stmt = select(SlotValue).where(SlotValue.ticket_id == ticket_id)
    return {s.key: s.value for s in db.scalars(stmt)}


def set_slot(db: Session, ticket_id: str, key: str, value: str) -> None:
    stmt = select(SlotValue).where(SlotValue.ticket_id == ticket_id,
                                   SlotValue.key == key)
    existing = db.scalars(stmt).first()
    if existing:
        existing.value = value
    else:
        db.add(SlotValue(ticket_id=ticket_id, key=key, value=value))


def log_event(db: Session, ticket_id: str | None, type_: str, payload: dict | None = None) -> None:
    db.add(Event(ticket_id=ticket_id, type=type_,
                 payload=json.dumps(payload or {}, ensure_ascii=False)))


# ---------------------------------------------------------------- идемпотентность

def get_idempotent(db: Session, request_id: str) -> str | None:
    row = db.get(Idempotency, request_id)
    return row.response_json if row else None


def save_idempotent(db: Session, request_id: str, ticket_id: str, response_json: str) -> None:
    if db.get(Idempotency, request_id) is None:
        db.add(Idempotency(request_id=request_id, ticket_id=ticket_id,
                           response_json=response_json))
