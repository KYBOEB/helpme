"""
КОНЕЧНЫЙ АВТОМАТ ДИАЛОГА — сердце системы. ВЛАДЕЛЕЦ: A (тимлид).

    NEW → CLASSIFYING → CLARIFYING → SOLVING → VERIFYING → RESOLVED
              │             │                      │
              └─────────────┴──────────────────────┴──→ ESCALATED

Правила, которые здесь обеспечиваются:
  * состояние вычисляется только сервером, клиент его не присылает;
  * уточняющих вопросов не больше MAX_CLARIFYING_QUESTIONS за диалог;
  * вопрос задаётся только про слот, который реально нужен выбранной статье;
  * нет подходящей статьи → эскалация, а не выдуманные шаги.
"""
from __future__ import annotations

import json
import os

from sqlalchemy.orm import Session

from common.models import (Article, ChatRequest, ChatResponse, Reply, Slot,
                           TicketCard)
from core import security
from core.redact import redact
from db import repo
from db.models import Ticket
from core import kb_store
from kb.retriever import HybridRetriever
from core import assist, demo_cache
from llm import answerer, router

CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.75"))
MAX_CLARIFYING_QUESTIONS = int(os.getenv("MAX_CLARIFYING_QUESTIONS", "2"))

_articles: list[Article] = []
_retriever: HybridRetriever | None = None
_by_id: dict[str, Article] = {}


def reload_kb() -> int:
    """Перечитать базу знаний. Вызывается на старте и после правок из панели."""
    global _articles, _retriever, _by_id
    _articles = kb_store.all_articles()
    _retriever = HybridRetriever(_articles)
    _by_id = {a.id: a for a in _articles}
    return len(_articles)


reload_kb()


# ---------------------------------------------------------------- вспомогательное

def _article(ticket: Ticket) -> Article | None:
    return _by_id.get(ticket.article_id) if ticket.article_id else None


def _slot_by_key(article: Article, key: str) -> Slot | None:
    for s in article.required_slots:
        if s.key == key:
            return s
    return None


def _card(db: Session, ticket: Ticket) -> TicketCard:
    art = _article(ticket)
    return TicketCard(
        ticket_id=ticket.id,
        category=ticket.category or "не определено",
        problem_summary=ticket.problem_summary,
        slots=repo.slots_dict(db, ticket.id),
        steps_done=json.loads(ticket.steps_json or "[]"),
        resolved_by_bot=ticket.resolved_by_bot,
        needs_specialist=ticket.needs_specialist,
        article_id=art.id if art else None,
        created_at=ticket.created_at.isoformat(),
    )


def _respond(db: Session, ticket: Ticket, reply: Reply,
             with_card: bool = False) -> ChatResponse:
    art = _article(ticket)
    repo.add_message(db, ticket.id, "assistant", reply.text)
    return ChatResponse(
        ticket_id=ticket.id,
        token=ticket.token,
        state=ticket.state,
        category=ticket.category,
        confidence=ticket.confidence,
        article={"id": art.id, "title": art.title} if art else None,
        reply=reply,
        ticket_card=_card(db, ticket) if with_card else None,
        user_actions_count=ticket.user_actions_count,
    )


# ---------------------------------------------------------------- шаги автомата

def _notify_external(db: Session, ticket: Ticket) -> None:
    """Отправить карточку во внешнюю систему. Интеграция не имеет права
    уронить основной сценарий, поэтому любая ошибка только логируется."""
    try:
        from api.export import send_webhook
    except ImportError:
        return
    try:
        send_webhook(_card(db, ticket))
        repo.log_event(db, ticket.id, "webhook_sent")
    except Exception as exc:  # noqa: BLE001
        repo.log_event(db, ticket.id, "webhook_failed", {"error": str(exc)[:200]})


def _escalate(db: Session, ticket: Ticket, reason: str) -> ChatResponse:
    ticket.state = "ESCALATED"
    ticket.needs_specialist = True
    ticket.resolved_by_bot = False
    ticket.resolution = reason
    repo.log_event(db, ticket.id, "escalated", {"reason": reason})

    intro = {
        "нет статьи с готовым решением":
            "Готового решения для такой проблемы в базе знаний нет.",
        "подходящая статья не найдена":
            "Я не нашёл в базе знаний подходящей инструкции.",
        "шаги из базы знаний не помогли":
            "Стандартные шаги не помогли — значит случай нетиповой.",
        "рекомендации не помогли":
            "Ни инструкция из базы, ни общие рекомендации не помогли.",
        "обращение вне тематики технической поддержки":
            "Этот вопрос выходит за рамки технической поддержки.",
        "пользователь запросил специалиста": "Конечно.",
    }.get(reason, "Передаю обращение специалисту.")

    _notify_external(db, ticket)

    text = (f"{intro} Я передал обращение специалисту поддержки, "
            f"номер — {ticket.id}. Категорию, ваши ответы и выполненные шаги "
            f"он уже видит, объяснять заново ничего не нужно.")
    return _respond(db, ticket, Reply(type="escalation", text=text), with_card=True)


def _assist(db: Session, ticket: Ticket, user_text: str,
            reason: str = "gap") -> ChatResponse:
    """Второй уровень каскада: в базе знаний решения нет.

    Умная модель даёт общую рекомендацию, но она честно помечается как НЕ из
    базы знаний, обращение попадает в очередь к специалисту, а запрос
    записывается как пробел в базе — по нему потом напишут статью.
    """
    safe_text, _ = redact(user_text)
    # gap — статьи нет вовсе; insufficient — статья нашлась, но шаги не помогли.
    # И то и другое стоит показать оператору: первое просит новую карточку,
    # второе — доработать существующую.
    repo.log_event(db, ticket.id,
                   "kb_gap" if reason == "gap" else "kb_insufficient",
                   {"query": safe_text[:300], "category": ticket.category,
                    "article_id": ticket.article_id})

    answer = assist.general_help(safe_text, ticket.category or "не определено")
    if answer is None:
        return _escalate(db, ticket, "подходящая статья не найдена")

    ticket.assist_used = True
    ticket.needs_specialist = True          # уже в очереди, не дожидаясь исхода
    ticket.state = "VERIFYING"
    ticket.steps_json = json.dumps(answer.steps, ensure_ascii=False)
    repo.log_event(db, ticket.id, "assisted", {"steps": len(answer.steps)})

    if reason == "gap":
        intro = answer.text or "Готовой инструкции для этого случая в базе знаний нет."
    else:
        intro = "Шаги из базы знаний не помогли — попробуем общие рекомендации."
    text = (f"{intro} Обращение уже передано специалисту — он подключится. "
            f"А пока можно попробовать общие шаги, они безопасны.")

    return _respond(db, ticket, Reply(
        type="steps", text=text, steps=answer.steps, source="general",
        quick_replies=["Получилось", "Не получилось", "Позвать специалиста"]))


def _solve(db: Session, ticket: Ticket) -> ChatResponse:
    """Выдать шаги решения из статьи."""
    art = _article(ticket)
    if art is None or not art.steps:
        return _escalate(db, ticket, "нет статьи с готовым решением")

    slots = repo.slots_dict(db, ticket.id)
    last_user = ""
    hist = repo.history(db, ticket.id, limit=4)
    for m in reversed(hist):
        if m["role"] == "user":
            last_user = m["content"]
            break

    safe_slots = {k: redact(v)[0] for k, v in slots.items()}
    safe_last, found = redact(last_user)
    if found:
        repo.log_event(db, ticket.id, "redacted", found)

    answer = demo_cache.cached_answer(art, safe_slots, safe_last)
    steps = answer.steps or art.steps
    # Страховка: модель не имеет права добавлять шаги, которых нет в статье
    if len(steps) > len(art.steps):
        steps = art.steps

    ticket.steps_json = json.dumps(steps, ensure_ascii=False)
    ticket.state = "VERIFYING"
    repo.log_event(db, ticket.id, "solved", {"article_id": art.id, "steps": len(steps)})

    intro = answer.text or f"Похоже на «{art.title}». Давайте по шагам."
    return _respond(db, ticket, Reply(type="steps", text=intro, steps=steps,
                                      quick_replies=["Получилось", "Не получилось"]))


def _ask_slot(db: Session, ticket: Ticket, key: str) -> ChatResponse:
    art = _article(ticket)
    slot = _slot_by_key(art, key) if art else None
    if slot is None:
        return _solve(db, ticket)

    ticket.state = "CLARIFYING"
    ticket.pending_slot = key
    ticket.clarify_count += 1
    repo.log_event(db, ticket.id, "clarify", {"slot": key})

    question = answerer.make_clarifying_question(slot, ticket.problem_summary)
    return _respond(db, ticket, Reply(type="question", text=question,
                                      quick_replies=list(slot.options)))


def _classify(db: Session, ticket: Ticket, user_text: str) -> ChatResponse:
    """Найти кандидатов, спросить модель, решить что делать дальше."""
    hits = _retriever.search(user_text, top_k=5)
    candidates = [a for a, _ in hits]
    known = repo.slots_dict(db, ticket.id)

    # Поиск идёт по оригиналу — он никуда не уходит и точнее ищет.
    # Во внешнюю модель отправляем уже очищенный текст.
    safe_text, found = redact(user_text)
    if found:
        repo.log_event(db, ticket.id, "redacted", found)
    safe_hist = [{"role": m["role"], "content": redact(m["content"])[0]}
                 for m in repo.history(db, ticket.id)]
    safe_known = {k: redact(v)[0] for k, v in known.items()}

    result = demo_cache.cached_route(safe_text, safe_hist, candidates, safe_known)

    # Постобработка: модель не может назвать статью, которой не было среди кандидатов
    allowed = {a.id for a in candidates}
    article_id = result.article_id if result.article_id in allowed else None

    ticket.category = result.category
    ticket.confidence = max(0.0, min(1.0, result.confidence))
    ticket.article_id = article_id
    if result.problem_summary:
        ticket.problem_summary = result.problem_summary
    for k, v in (result.filled_slots or {}).items():
        repo.set_slot(db, ticket.id, k, str(v))

    repo.log_event(db, ticket.id, "classified",
                   {"category": ticket.category, "confidence": ticket.confidence,
                    "article_id": article_id})

    if result.is_out_of_scope:
        return _escalate(db, ticket, "обращение вне тематики технической поддержки")

    if article_id is None:
        return _assist(db, ticket, user_text)

    # Уверенности не хватает — не гадаем, а предлагаем выбрать
    if ticket.confidence < CONFIDENCE_THRESHOLD:
        ticket.state = "CLASSIFYING"
        options, seen = [], set()
        for a in candidates:
            if a.category not in seen:
                seen.add(a.category)
                options.append(a.category)
            if len(options) == 3:
                break
        return _respond(db, ticket, Reply(
            type="choice",
            text="Уточните, пожалуйста, к чему относится проблема:",
            quick_replies=options))

    # Чего не хватает для решения
    art = _by_id[article_id]
    slots_now = repo.slots_dict(db, ticket.id)
    missing = [s.key for s in art.required_slots
               if not s.optional and s.key not in slots_now]

    if missing and ticket.clarify_count < MAX_CLARIFYING_QUESTIONS:
        return _ask_slot(db, ticket, missing[0])

    return _solve(db, ticket)


def _verify(db: Session, ticket: Ticket, user_text: str) -> ChatResponse:
    """Пользователь сообщил, помогло или нет."""
    low = user_text.lower()
    if "специалист" in low or "оператор" in low:
        return _escalate(db, ticket, "пользователь запросил специалиста")

    ok = any(w in low for w in ("получилось", "помогло", "решено", "да", "спасибо"))
    failed = any(w in low for w in ("не получилось", "не помогло", "не работает", "нет"))

    if failed and not low.startswith("получилось"):
        # Второй уровень каскада: шаги из базы не помогли — пробуем общую
        # рекомендацию. Человека зовём, только если и она не сработала.
        if ticket.assist_used:
            return _escalate(db, ticket, "рекомендации не помогли")
        return _assist(db, ticket,
                       ticket.problem_summary or "шаги из базы знаний не помогли",
                       reason="insufficient")

    if ok:
        ticket.state = "RESOLVED"
        # Общая рекомендация не засчитывается как «решено ботом»: решения
        # в базе знаний не было, и метрику этим завышать нечестно.
        ticket.resolved_by_bot = not ticket.assist_used
        ticket.needs_specialist = ticket.assist_used
        ticket.resolution = ("решено общей рекомендацией, статья в базе нужна"
                             if ticket.assist_used else "решено без специалиста")
        repo.log_event(db, ticket.id, "resolved",
                       {"actions": ticket.user_actions_count})
        text = answerer.make_summary(_card(db, ticket))
        return _respond(db, ticket, Reply(type="summary", text=text), with_card=True)

    # Ответ непонятен — переспрашиваем один раз, не меняя состояния
    return _respond(db, ticket, Reply(
        type="question",
        text="Подскажите, шаги помогли решить проблему?",
        quick_replies=["Получилось", "Не получилось"]))


# ---------------------------------------------------------------- точка входа

def handle(db: Session, req: ChatRequest, client_key: str) -> ChatResponse:
    security.rate_limit(client_key)

    # 1. Идемпотентность: повтор того же request_id возвращает тот же ответ
    cached = repo.get_idempotent(db, req.request_id)
    if cached:
        return ChatResponse(**json.loads(cached))

    text = (req.quick_reply or req.message or "").strip()

    # 2. Новое обращение или продолжение существующего
    if req.ticket_id is None:
        ticket = Ticket(id=security.new_ticket_id(), token=security.new_token())
        db.add(ticket)
        db.flush()
        repo.log_event(db, ticket.id, "created")
    else:
        ticket = repo.get_ticket(db, req.ticket_id)
        security.check_owner(ticket, req.token)
        security.assert_can_accept(ticket)

    if not text:
        return _respond(db, ticket, Reply(
            type="error", text="Напишите, пожалуйста, что случилось."))

    repo.add_message(db, ticket.id, "user", text)
    ticket.user_actions_count += 1

    # 3. Ветвление по состоянию — единственное место, где оно меняется
    if ticket.state == "CLARIFYING" and ticket.pending_slot:
        repo.set_slot(db, ticket.id, ticket.pending_slot, text)
        ticket.pending_slot = None
        art = _article(ticket)
        slots_now = repo.slots_dict(db, ticket.id)
        missing = [s.key for s in art.required_slots
                   if not s.optional and s.key not in slots_now] if art else []
        if missing and ticket.clarify_count < MAX_CLARIFYING_QUESTIONS:
            response = _ask_slot(db, ticket, missing[0])
        else:
            response = _solve(db, ticket)
    elif ticket.state == "VERIFYING":
        response = _verify(db, ticket, text)
    else:
        response = _classify(db, ticket, text)

    # 4. Фиксируем результат под этим request_id
    repo.save_idempotent(db, req.request_id, ticket.id,
                         response.model_dump_json())
    db.commit()
    return response
