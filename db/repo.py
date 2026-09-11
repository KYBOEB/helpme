"""Доступ к данным."""
from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from db.models import (Base, Event, Idempotency, Message, SlotValue, Ticket,
                       iso_utc)

DB_PATH = os.getenv("DB_PATH", "data/helpme.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

# timeout: сколько ждать освобождения базы. По умолчанию в SQLite это 5 секунд —
# меньше, чем длится запрос к модели, поэтому при одновременных обращениях
# второе падало бы с «database is locked».
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    """Журнал упреждающей записи: читающие не блокируют пишущих и наоборот.

    Иначе открытая панель оператора, которая опрашивает список обращений,
    мешает завершить запись чата.
    """
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


# Колонки, добавленные после первого релиза. SQLite умеет ALTER TABLE ADD COLUMN,
# поэтому обходимся без миграций: база на сервере переживает обновление.
_NEW_COLUMNS = {
    "tickets": {
        "assist_used": "BOOLEAN DEFAULT 0",
        "operator_taken": "BOOLEAN DEFAULT 0",
        "share_token": "VARCHAR(64)",
        "out_of_scope": "BOOLEAN DEFAULT 0",
        "offtopic_count": "INTEGER DEFAULT 0",
        "public_no": "INTEGER",
        "closed_by_user": "BOOLEAN DEFAULT 0",
        "specialist_asked": "BOOLEAN DEFAULT 0",
        "client_hash": "VARCHAR(64)",
        "parent_ticket_id": "VARCHAR(32)",
    },
}

# Индексы для колонок, по которым идёт поиск. Без индекса выборка обращений
# посетителя — полный перебор таблицы, и с ростом базы она начнёт тормозить
# ровно там, где пользователь ждёт список.
_INDEXES = {
    "ix_tickets_client_hash": "tickets(client_hash)",
    "ix_tickets_parent": "tickets(parent_ticket_id)",
}


def init_db() -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table, columns in _NEW_COLUMNS.items():
            existing = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        for name, target in _INDEXES.items():
            conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")


def get_session() -> Session:
    return SessionLocal()


# ---------------------------------------------------------------- обращения

def get_ticket(db: Session, ticket_id: str) -> Ticket | None:
    return db.get(Ticket, ticket_id)


def list_tickets(db: Session, limit: int = 200,
                 state: str | None = None, queue: bool = False) -> list[Ticket]:
    stmt = select(Ticket)
    if queue:
        # очередь специалиста: передано человеку и ещё не взято в работу
        stmt = stmt.where(Ticket.needs_specialist.is_(True),
                          Ticket.operator_taken.is_(False))
        stmt = stmt.order_by(Ticket.created_at.asc())   # первым — самое старое
    else:
        if state:
            stmt = stmt.where(Ticket.state == state)
        stmt = stmt.order_by(Ticket.created_at.desc())
    return list(db.scalars(stmt.limit(limit)))


def tickets_for_client(db: Session, client_hash: str, limit: int = 20) -> list[Ticket]:
    """Обращения одного анонимного посетителя, свежие первыми.

    Пустой хэш не должен возвращать «все обращения без владельца» — это была бы
    выдача чужой переписки любому, кто пришёл без идентификатора.
    """
    if not client_hash:
        return []
    stmt = (select(Ticket)
            .where(Ticket.client_hash == client_hash)
            .order_by(Ticket.created_at.desc())
            .limit(max(1, min(limit, 50))))
    return list(db.scalars(stmt))


def public_no_of(db: Session, ticket_id: str | None) -> int | None:
    """Короткий номер обращения по внутреннему идентификатору."""
    if not ticket_id:
        return None
    return db.scalar(select(Ticket.public_no).where(Ticket.id == ticket_id))


def add_message(db: Session, ticket_id: str, role: str, content: str) -> None:
    db.add(Message(ticket_id=ticket_id, role=role, content=content))


def history(db: Session, ticket_id: str, limit: int = 10) -> list[dict]:
    stmt = (select(Message).where(Message.ticket_id == ticket_id)
            .order_by(Message.created_at.desc()).limit(limit))
    rows = list(db.scalars(stmt))[::-1]
    return [{"id": m.id, "role": m.role, "content": m.content,
             "created_at": iso_utc(m.created_at)} for m in rows]


def next_public_no(db: Session) -> int:
    """Следующий короткий номер обращения. Начинаем с 1001, чтобы номер
    сразу выглядел как настоящий, а не как «обращение №1» на демонстрации."""
    current = db.scalar(select(func.max(Ticket.public_no)))
    return max(int(current or 0), 1000) + 1


def messages_after(db: Session, ticket_id: str, after_id: int,
                   roles: tuple[str, ...] = ("operator",),
                   limit: int = 50) -> list[dict]:
    """Сообщения обращения с id больше указанного.

    Нужна странице чата: пока обращение у живого специалиста, браузер
    подтягивает его реплики, не трогая конечный автомат диалога.
    """
    stmt = (select(Message)
            .where(Message.ticket_id == ticket_id,
                   Message.id > after_id,
                   Message.role.in_(roles))
            .order_by(Message.id.asc())
            .limit(max(1, min(limit, 200))))
    return [{"id": m.id, "role": m.role, "content": m.content,
             "created_at": iso_utc(m.created_at)} for m in db.scalars(stmt)]


def last_message_id(db: Session, ticket_id: str) -> int:
    stmt = (select(Message.id).where(Message.ticket_id == ticket_id)
            .order_by(Message.id.desc()).limit(1))
    return db.scalars(stmt).first() or 0


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
