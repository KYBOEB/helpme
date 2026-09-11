"""
ЕДИНЫЕ МОДЕЛИ ДАННЫХ ПРОЕКТА.

Владелец файла — A (тимлид). Менять только по согласованию, письменно, с объявлением команде.
Все остальные модули импортируют отсюда и НЕ создают своих копий этих классов.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# Каталог услуг из технического задания кейса
CATEGORIES = [
    "рабочее место",
    "доступы",
    "VPN",
    "корпоративная почта",
    "Wi-Fi",
    "программное обеспечение",
    "оборудование",
    "не определено",
]

STATES = [
    "NEW",
    "CLASSIFYING",
    "CLARIFYING",
    "SOLVING",
    "VERIFYING",
    "RESOLVED",
    "ESCALATED",
]


# ---------------------------------------------------------------- база знаний

class Slot(BaseModel):
    """Сведение, без которого решение неприменимо."""

    key: str
    question: str
    options: list[str] = Field(default_factory=list)
    optional: bool = False


class Article(BaseModel):
    """Карточка базы знаний."""

    id: str
    category: str
    title: str
    symptoms: list[str] = Field(default_factory=list)
    required_slots: list[Slot] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    escalate_if: Optional[str] = None


# ---------------------------------------------------------------- слой LLM

class RouteResult(BaseModel):
    """Результат классификации обращения."""

    category: str = "не определено"
    article_id: Optional[str] = None
    confidence: float = 0.0
    problem_summary: str = ""
    filled_slots: dict[str, str] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    # Обращение не относится к технической поддержке: бытовой/медицинский вопрос,
    # оскорбления, набор символов. Такое обращение закрывается, а не уходит человеку.
    is_out_of_scope: bool = False
    # "off_topic" | "abuse" | "nonsense" | "" — нормализуется в llm/router.py
    off_topic_kind: str = ""


class AnswerResult(BaseModel):
    """Готовый ответ пользователю."""

    text: str = ""
    steps: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- HTTP API

class ChatRequest(BaseModel):
    ticket_id: Optional[str] = None       # None = новое обращение
    token: Optional[str] = None           # секрет обращения, выдан при создании
    request_id: str                       # UUID, для идемпотентности
    # Optional, а не str: при нажатии кнопки клиент присылает message: null,
    # и отвергать такой запрос было бы придиркой к форме, а не к содержанию.
    message: Optional[str] = Field(default="", max_length=2000)
    quick_reply: Optional[str] = None     # если пользователь нажал кнопку


class Reply(BaseModel):
    # "closed" — обращение закрыто системой как нецелевое: специалиста не зовём.
    type: Literal["question", "steps", "summary", "escalation", "choice",
                  "error", "closed", "operator"]
    text: str
    quick_replies: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    # "kb"      — шаги взяты из карточки базы знаний (по умолчанию)
    # "general" — общая рекомендация модели, В БАЗЕ ЗНАНИЙ РЕШЕНИЯ НЕТ.
    #             Фронтенд обязан пометить такой ответ отдельной плашкой.
    source: Literal["kb", "general"] = "kb"


class TicketCard(BaseModel):
    ticket_id: str
    # Короткий номер для пользователя: «Обращение №1042».
    # Внутренний ticket_id в интерфейсе не показывается.
    public_no: int = 0
    category: str
    problem_summary: str
    slots: dict[str, str] = Field(default_factory=dict)
    steps_done: list[str] = Field(default_factory=list)
    resolved_by_bot: bool = False
    needs_specialist: bool = False
    out_of_scope: bool = False
    article_id: Optional[str] = None
    created_at: str = ""


class ChatResponse(BaseModel):
    ticket_id: str
    token: str
    state: Literal["NEW", "CLASSIFYING", "CLARIFYING", "SOLVING",
                   "VERIFYING", "RESOLVED", "ESCALATED"]
    category: Optional[str] = None
    # Уверенность в ПОДОБРАННОЙ СТАТЬЕ базы знаний, а не «в ответе вообще».
    # Если статья не найдена, значение равно 0.0 и интерфейс не показывает его:
    # показывать «90 %» рядом с ответом, которого нет в базе, было бы враньём.
    confidence: float = 0.0
    article: Optional[dict] = None        # {"id": "...", "title": "..."}
    reply: Reply
    ticket_card: Optional[TicketCard] = None
    user_actions_count: int = 0
