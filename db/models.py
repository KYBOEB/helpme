"""Таблицы SQLAlchemy. ВЛАДЕЛЕЦ: A."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    state: Mapped[str] = mapped_column(String(16), default="NEW")
    category: Mapped[str | None] = mapped_column(String(64), default=None)
    article_id: Mapped[str | None] = mapped_column(String(32), default=None)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    problem_summary: Mapped[str] = mapped_column(Text, default="")

    # счётчики и служебное
    user_actions_count: Mapped[int] = mapped_column(Integer, default=0)
    clarify_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_slot: Mapped[str | None] = mapped_column(String(64), default=None)
    steps_json: Mapped[str] = mapped_column(Text, default="[]")

    # итог
    resolved_by_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_specialist: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str] = mapped_column(Text, default="")
    rating: Mapped[int | None] = mapped_column(Integer, default=None)
    assist_used: Mapped[bool] = mapped_column(Boolean, default=False)
    operator_taken: Mapped[bool] = mapped_column(Boolean, default=False)
    share_token: Mapped[str | None] = mapped_column(String(64), default=None)
    # закрыто как нецелевое (не ИТ, оскорбления, бессмыслица) — специалиста не звали
    out_of_scope: Mapped[bool] = mapped_column(Boolean, default=False)
    # сколько раз подряд пришло нецелевое сообщение: первое — предупреждение,
    # второе — закрытие. Один промах пользователя не должен стоить ему обращения.
    offtopic_count: Mapped[int] = mapped_column(Integer, default=0)

    messages: Mapped[list["Message"]] = relationship(back_populates="ticket",
                                                     cascade="all, delete-orphan")
    slots: Mapped[list["SlotValue"]] = relationship(back_populates="ticket",
                                                    cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"))
    role: Mapped[str] = mapped_column(String(16))          # user | assistant | operator
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="messages")


class SlotValue(Base):
    __tablename__ = "slot_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"))
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)

    ticket: Mapped[Ticket] = relationship(back_populates="slots")


class Event(Base):
    """Журнал для дашборда и отладки. Содержимое сообщений сюда не пишем."""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str | None] = mapped_column(String(32), default=None)
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Idempotency(Base):
    """Один request_id — один результат. Защита от двойных кликов и ретраев."""
    __tablename__ = "idempotency"

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(32))
    response_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
