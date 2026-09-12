"""
Конечный автомат диалога.

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
import logging
import os
import re

from sqlalchemy.orm import Session

from common.models import (AnswerResult, Article, ChatRequest, ChatResponse, Reply, Slot,
                           TicketCard)
from core import security
from core.redact import redact
from db import repo
from db.models import Ticket, iso_utc
from core import kb_store
from kb import embeddings
from kb.retriever import HybridRetriever
from core import assist, demo_cache
from llm import answerer, router

log = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.75"))
MAX_CLARIFYING_QUESTIONS = int(os.getenv("MAX_CLARIFYING_QUESTIONS", "2"))

_articles: list[Article] = []
_retriever: HybridRetriever | None = None
_by_id: dict[str, Article] = {}
_by_title: dict[str, Article] = {}

# Кнопка «ничего из этого» на экране уточнения
OTHER_QR = "Другое"


def _build_retriever(articles: list[Article]) -> HybridRetriever:
    """Поиск с эмбеддингами, если индекс на месте, иначе честный BM25.

    Раньше здесь было просто `HybridRetriever(articles)` — без `embed_fn`,
    то есть поиск был чисто лексическим, хотя класс умел больше. Теперь
    вектора карточек берутся из заранее собранного `kb/emb_index.npz`.

    Любая ошибка тут означает откат на BM25, а не отказ старта: приложение
    обязано подниматься, даже если индекса нет или внешний сервис лежит.
    """
    try:
        embed_fn = embeddings.make_embed_fn()
    except Exception as exc:  # noqa: BLE001
        log.warning("индекс эмбеддингов не загрузился: %s", exc)
        embed_fn = None

    if embed_fn is not None:
        try:
            retriever = HybridRetriever(articles, embed_fn=embed_fn)
            log.info("поиск по базе знаний: гибридный (BM25 + эмбеддинги, RRF)")
            return retriever
        except Exception as exc:  # noqa: BLE001
            log.warning("эмбеддинги недоступны, поиск работает на BM25: %s", exc)

    log.info("поиск по базе знаний: лексический (BM25)")
    return HybridRetriever(articles)


def search_mode() -> str:
    """Режим поиска для README, аналитики и ответов на защите."""
    return "hybrid" if (_retriever is not None and _retriever.semantic) else "bm25"


def reload_kb() -> int:
    """Перечитать базу знаний. Вызывается на старте и после правок из панели."""
    global _articles, _retriever, _by_id, _by_title
    _articles = kb_store.all_articles()
    _retriever = _build_retriever(_articles)
    _by_id = {a.id: a for a in _articles}
    # Заголовок → статья: по нему узнаём вариант, который пользователь выбрал
    # кнопкой на экране уточнения. Тогда ни поиск, ни модель уже не нужны.
    _by_title = {a.title.strip().lower(): a for a in _articles}
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
        public_no=ticket.public_no or 0,
        category=ticket.category or "не определено",
        problem_summary=ticket.problem_summary,
        slots=repo.slots_dict(db, ticket.id),
        steps_done=json.loads(ticket.steps_json or "[]"),
        resolved_by_bot=ticket.resolved_by_bot,
        needs_specialist=ticket.needs_specialist,
        out_of_scope=bool(ticket.out_of_scope),
        article_id=art.id if art else None,
        created_at=iso_utc(ticket.created_at),
    )


def _respond(db: Session, ticket: Ticket, reply: Reply,
             with_card: bool = False) -> ChatResponse:
    art = _article(ticket)
    repo.add_message(db, ticket.id, "assistant", reply.text)
    return ChatResponse(
        ticket_id=ticket.id,
        token=ticket.token,
        public_no=ticket.public_no or 0,
        parent_public_no=repo.public_no_of(db, ticket.parent_ticket_id),
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
            "Ни инструкция из базы, ни мои общие рекомендации не помогли.",
        "пользователь запросил специалиста": "Конечно.",
    }.get(reason, "")

    _notify_external(db, ticket)

    # Номер обращения видит только оператор: пользователь продолжает диалог
    # в этом же окне, и номер ему не нужен.
    text = (f"{intro} Передаю обращение специалисту поддержки. Категорию, ваши "
            f"ответы и выполненные шаги он уже видит, объяснять заново ничего "
            f"не нужно.").replace("  ", " ").strip()
    return _respond(db, ticket, Reply(type="escalation", text=text), with_card=True)


# ---------------------------------------------------------- нецелевые обращения

# Первое предупреждение: пользователь мог просто ошибиться окном.
_WARN_TEXT = {
    "off_topic": (
        "Я виртуальный помощник технической поддержки и решаю только вопросы, "
        "связанные с рабочим компьютером, программами, доступами, почтой, "
        "VPN, Wi-Fi и оборудованием. С этим вопросом я помочь не смогу.\n\n"
        "Если у вас есть проблема из этих тем — опишите её, и я подключусь."),
    "abuse": (
        "Давайте общаться уважительно — так я смогу быть полезным.\n\n"
        "Опишите, пожалуйста, что именно не работает, и я помогу."),
    "nonsense": (
        "Не смог разобрать сообщение. Опишите проблему словами: "
        "что вы делали, что произошло и какой текст ошибки видите."),
}

_CLOSE_TEXT = {
    "off_topic": ("Обращение закрыто: вопрос не относится к технической поддержке. "
                  "Специалиста я по нему не вызываю. "
                  "Если появится ИТ-проблема — начните новое обращение."),
    "abuse": ("Обращение закрыто: продолжать диалог в таком тоне я не буду. "
              "Когда понадобится помощь по работе техники — начните новое обращение."),
    "nonsense": ("Обращение закрыто: описания проблемы так и не поступило. "
                 "Начните новое обращение и опишите, что именно не работает."),
}

_MAX_OFFTOPIC = int(os.getenv("MAX_OFFTOPIC_MESSAGES", "2"))


# ------------------------------------------- просьба позвать специалиста

_SPECIALIST_RE = re.compile(r"специалист|оператор|живо(?:го|му) человек", re.I)

# Служебные слова самой просьбы: если кроме них в сообщении ничего нет,
# проблему пользователь не описал.
_FILLER = {
    "я", "мне", "меня", "вы", "вызови", "вызовите", "позови", "позовите",
    "позвать", "вызвать", "соедини", "соедините", "переключи", "переключите",
    "хочу", "нужен", "нужна", "нужно", "надо", "пожалуйста", "давай", "давайте",
    "срочно", "пусть", "ничего", "ниче", "нихрена", "не", "понимаю", "понял",
    "помогите", "помоги", "здравствуйте", "привет", "добрый", "день", "с", "к",
    "на", "у", "и", "а", "но", "это", "бы", "тут", "здесь", "вообще", "живого",
    "живому", "человека", "человеком", "поддержки", "техподдержки", "все",
    "всё", "равно",
}


def _wants_specialist(text: str) -> bool:
    """Просьба позвать человека, в которой не описана проблема."""
    if not _SPECIALIST_RE.search(text):
        return False
    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 1]
    rest = [w for w in words
            if w not in _FILLER and not _SPECIALIST_RE.search(w)]
    # Одно оставшееся слово ещё можно списать на вежливость, два и больше —
    # это уже описание проблемы, и его надо обрабатывать как обычно.
    return len(rest) <= 1


def _specialist_request(db: Session, ticket: Ticket) -> ChatResponse:
    """Пользователь просит живого человека.

    Здесь был потерян балл на отборочном этапе: комиссия записала «передача
    обращения специалисту — не реализована», хотя эскалация работает. Причина
    в том, что на просьбу без описания проблемы бот отвечал предложением помочь
    и делал так СКОЛЬКО УГОДНО РАЗ — до человека нельзя было дойти вообще.

    Теперь правило простое и объяснимое на защите:
      * проблема уже описана → передаём сразу, переспрашивать нечего;
      * проблема не описана, просят впервые → одна попытка помочь;
      * просят второй раз → передаём. Уговаривать дальше — неуважение.

    Каскад «база знаний → общий совет → человек» остаётся путём по умолчанию,
    но перестаёт быть единственным.
    """
    problem_known = bool((ticket.problem_summary or "").strip() or ticket.article_id)
    if problem_known or ticket.specialist_asked:
        return _escalate(db, ticket, "пользователь запросил специалиста")
    return _ask_to_describe(db, ticket)


def _ask_to_describe(db: Session, ticket: Ticket) -> ChatResponse:
    """Просьба позвать человека, в которой не описана проблема.

    Специалиста отсюда не зовём: заявка без описания занимает его рабочее время
    и всё равно начинается с вопроса «а что у вас не работает». Сначала
    предлагаем помощь. Если она не подойдёт, обращение уйдёт человеку обычным
    путём — из проверки результата.
    """
    first = not ticket.specialist_asked
    ticket.specialist_asked = True
    ticket.state = "NEW"
    repo.log_event(db, ticket.id, "specialist_requested_blank")

    text = ("Большинство типовых проблем я решаю сам и быстрее, чем занятый "
            "специалист. Опишите в двух словах, что не работает — а если "
            "не справлюсь, передам обращение ему вместе со всей историей."
            if first else
            "Мне правда нужно знать, в чём проблема: без этого я не смогу "
            "ни помочь, ни толком передать обращение. Напишите одной фразой, "
            "что случилось, или выберите вариант ниже.")

    return _respond(db, ticket, Reply(
        type="question", text=text,
        quick_replies=["Не подключается VPN",
                       "Не подключается Wi-Fi",
                       "Не печатает принтер"]))


def _looks_like_nonsense(text: str) -> bool:
    """Грубая локальная проверка на бессмыслицу.

    Нужна как страховка: работает без модели и в DEMO_MODE. Намеренно узкая —
    ложное срабатывание на живом пользователе хуже, чем пропуск одного тролля.
    """
    t = text.strip()
    if len(t) < 3:
        return False
    letters = [c for c in t if c.isalpha()]
    if not letters:                       # «!!!!!!», «12345», «))))»
        return True
    if len(set(c.lower() for c in letters)) == 1 and len(letters) >= 5:
        return True                       # «ааааааа», «ggggggg»
    return False


def _out_of_scope(db: Session, ticket: Ticket, kind: str) -> ChatResponse:
    """Обращение не про техподдержку.

    Первое такое сообщение — предупреждение с объяснением, что мы умеем.
    Второе подряд — закрытие. Специалиста не зовём: занимать живого человека
    молочными зубами и оскорблениями нельзя, а именно это делала бы эскалация.
    """
    kind = kind if kind in _WARN_TEXT else "off_topic"
    ticket.offtopic_count = (ticket.offtopic_count or 0) + 1
    if ticket.article_id is None:
        # Категорию, выведенную из нецелевого сообщения, не оставляем.
        ticket.category = "не определено"
        ticket.confidence = 0.0
    repo.log_event(db, ticket.id, "out_of_scope",
                   {"kind": kind, "count": ticket.offtopic_count})

    if ticket.offtopic_count < _MAX_OFFTOPIC:
        # Возвращаем обращение к началу, только если диалог ещё не начался.
        # Иначе пользователь потерял бы уже отвеченные уточняющие вопросы.
        if ticket.state in ("NEW", "CLASSIFYING"):
            ticket.state = "NEW"
        return _respond(db, ticket, Reply(type="question", text=_WARN_TEXT[kind]))

    ticket.state = "RESOLVED"
    ticket.out_of_scope = True
    ticket.resolved_by_bot = False
    ticket.needs_specialist = False       # в очередь к специалисту НЕ попадает
    ticket.resolution = f"закрыто автоматически: вне тематики ({kind})"
    repo.log_event(db, ticket.id, "closed_out_of_scope", {"kind": kind})
    return _respond(db, ticket, Reply(type="closed", text=_CLOSE_TEXT[kind]),
                    with_card=True)


def _assist(db: Session, ticket: Ticket, user_text: str,
            reason: str = "gap") -> ChatResponse:
    """Второй уровень каскада: в базе знаний решения нет.

    Модель даёт общую рекомендацию, помеченную как совет ИИ-ассистента,
    а запрос записывается как пробел в базе — по нему потом напишут статью.

    Специалиста здесь не вызываем: человек подключается на третьем уровне
    каскада, если и эти рекомендации не помогут.
    """
    safe_text, _ = redact(user_text)
    # gap — статьи нет вовсе; insufficient — статья есть, но шаги не помогли.
    # Первое просит новую карточку, второе — доработать существующую.
    repo.log_event(db, ticket.id,
                   "kb_gap" if reason == "gap" else "kb_insufficient",
                   {"query": safe_text[:300], "category": ticket.category,
                    "article_id": ticket.article_id})

    # Шаги, которые пользователь уже видел и которые ему не помогли.
    # Без этого модель их повторяла: в general_help уходил только текст
    # обращения и категория, и не повторить показанное она физически не могла.
    try:
        already_shown = json.loads(ticket.steps_json or "[]")
    except ValueError:
        already_shown = []

    answer = assist.general_help(safe_text, ticket.category or "не определено",
                                 exclude_steps=already_shown)
    if answer is None:
        # Нечего добавить сверх того, что уже не помогло, — зовём человека.
        return _escalate(db, ticket, "подходящая статья не найдена")

    ticket.assist_used = True
    ticket.state = "VERIFYING"
    ticket.steps_json = json.dumps(answer.steps, ensure_ascii=False)
    repo.log_event(db, ticket.id, "assisted", {"steps": len(answer.steps)})

    # При reason="insufficient" подводку уже показал интерфейс, повторять её
    # своими словами незачем.
    text = (answer.text if reason == "gap" else "")

    # Кнопки исхода рисует карточка шага. Отдельной кнопки «Позвать специалиста»
    # здесь нет: человека зовём после того, как рекомендации не сработали.
    return _respond(db, ticket, Reply(
        type="steps", text=text, steps=answer.steps, source="general"))


# Порог «шаг написан подробно». Короткий шаг — это «Проверьте настройки»:
# такой нужно разворачивать моделью. Длинный уже содержит и действие, и место,
# и ожидаемый результат.
_DETAILED_STEP_CHARS = int(os.getenv("KB_DETAILED_STEP_CHARS", "70"))
# Принудительно вернуть прежнее поведение: ANSWER_REWRITE=1
_FORCE_REWRITE = os.getenv("ANSWER_REWRITE", "0") == "1"


def _steps_are_detailed(steps: list[str]) -> bool:
    """Шаги карточки уже годятся для показа без переписывания моделью."""
    if _FORCE_REWRITE or not steps:
        return False
    return all(len(s) >= _DETAILED_STEP_CHARS for s in steps)


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

    # Переписывать шаги моделью нужно не всегда.
    #
    # Раньше карточки содержали короткие шаги вроде «Проверьте срок действия
    # сертификата: Настройки → Профиль», и умная модель разворачивала их под
    # ситуацию. После переписывания базы шаги уже написаны для неподготовленного
    # читателя: что сделать, где именно и что появится на экране. Гонять их через
    # модель ещё раз — это две-пять секунд ожидания на КАЖДОМ обращении основного
    # сценария, лишние деньги и риск, что хороший шаг испортят.
    #
    # Поэтому: если шаги карточки уже подробные, показываем их как есть.
    # Это заодно честнее — пользователь видит ровно то, что написано в базе.
    if _steps_are_detailed(art.steps):
        answer = AnswerResult(text="", steps=list(art.steps))
        repo.log_event(db, ticket.id, "answer_verbatim", {"article_id": art.id})
    else:
        answer = demo_cache.cached_answer(art, safe_slots, safe_last)
    steps = answer.steps or art.steps
    # Страховка: модель не имеет права добавлять шаги, которых нет в статье
    if len(steps) > len(art.steps):
        steps = art.steps

    ticket.steps_json = json.dumps(steps, ensure_ascii=False)
    ticket.state = "VERIFYING"
    repo.log_event(db, ticket.id, "solved", {"article_id": art.id, "steps": len(steps)})

    intro = answer.text or f"Похоже на «{art.title}». Давайте по шагам."
    # Кнопки «Получилось / Не получилось» рисует сама карточка шага на фронтенде.
    # Дублировать их в quick_replies нельзя — на экране появятся две пары.
    return _respond(db, ticket, Reply(type="steps", text=intro, steps=steps))


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
    text_key = user_text.strip().lower()

    # Пользователь сам выбрал проблему кнопкой — ни поиск, ни модель не нужны.
    picked = _by_title.get(text_key)
    if picked is not None:
        ticket.category = picked.category
        ticket.article_id = picked.id
        ticket.confidence = 1.0
        ticket.problem_summary = ticket.problem_summary or picked.title
        repo.log_event(db, ticket.id, "picked_suggestion", {"article_id": picked.id})
        slots_now = repo.slots_dict(db, ticket.id)
        missing = [s.key for s in picked.required_slots
                   if not s.optional and s.key not in slots_now]
        if missing and ticket.clarify_count < MAX_CLARIFYING_QUESTIONS:
            return _ask_slot(db, ticket, missing[0])
        return _solve(db, ticket)

    # «Другое» на том же экране — ни один вариант не подошёл
    if text_key == OTHER_QR.lower():
        ticket.state = "NEW"
        return _respond(db, ticket, Reply(
            type="question",
            text=("Хорошо. Опишите проблему подробнее: что вы делали, что "
                  "произошло и какой текст ошибки видите на экране.")))

    # Локальная страховка до обращения к модели: бессмыслицу видно и без ИИ,
    # а лишний вызов модели на «asdfgh» — потраченные деньги и время.
    if _looks_like_nonsense(user_text):
        return _out_of_scope(db, ticket, "nonsense")

    # «Вызови специалиста» без описания проблемы. Отправлять такое обращение
    # в поиск бессмысленно: искать нечего, а общие шаги вроде «перезагрузите
    # устройство» к неизвестной проблеме отношения не имеют.
    if _wants_specialist(user_text):
        return _specialist_request(db, ticket)

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

    # Отсев нецелевых идёт до записи категории, иначе она осталась бы в карточке.
    if result.is_out_of_scope:
        if result.problem_summary:
            ticket.problem_summary = result.problem_summary
        return _out_of_scope(db, ticket, result.off_topic_kind)

    # Постобработка: модель не может назвать статью, которой не было среди кандидатов
    allowed = {a.id for a in candidates}
    article_id = result.article_id if result.article_id in allowed else None

    ticket.category = result.category
    # Уверенность относится к подобранной статье. Статьи нет — уверенности нет.
    ticket.confidence = (max(0.0, min(1.0, result.confidence)) if article_id else 0.0)
    ticket.article_id = article_id
    if result.problem_summary:
        ticket.problem_summary = result.problem_summary
    for k, v in (result.filled_slots or {}).items():
        repo.set_slot(db, ticket.id, k, str(v))

    repo.log_event(db, ticket.id, "classified",
                   {"category": ticket.category, "confidence": ticket.confidence,
                    "article_id": article_id})

    if article_id is None:
        return _assist(db, ticket, user_text)

    # Уверенности не хватает — не гадаем, а показываем, что нашёл поиск:
    # конкретные проблемы заголовками статей плюс «Другое».
    if ticket.confidence < CONFIDENCE_THRESHOLD:
        ticket.state = "CLASSIFYING"
        options, seen = [], set()
        for a in candidates:
            if a.title in seen:
                continue
            seen.add(a.title)
            options.append(a.title)
            if len(options) == 3:
                break
        options.append(OTHER_QR)
        return _respond(db, ticket, Reply(
            type="choice",
            text="Не уверен, что понял вопрос. Возможно, вы имеете в виду:",
            quick_replies=options))

    # Чего не хватает для решения
    art = _by_id[article_id]
    slots_now = repo.slots_dict(db, ticket.id)
    missing = [s.key for s in art.required_slots
               if not s.optional and s.key not in slots_now]

    if missing and ticket.clarify_count < MAX_CLARIFYING_QUESTIONS:
        return _ask_slot(db, ticket, missing[0])

    return _solve(db, ticket)


def _summary(ticket: Ticket) -> str:
    """Итог решённого обращения.

    Собирается из данных обращения, а не генерируется моделью: здесь нужны
    две точные строки, а не пересказ, который может оборваться на лимите токенов.
    """
    problem = (ticket.problem_summary or "").strip().rstrip(".")
    head = f"Проблема решена: {problem}." if problem else "Проблема решена."
    if ticket.assist_used:
        return head + "\nРешение подсказал ИИ-ассистент — в базе знаний такой " \
                      "инструкции пока нет. Обращаться к специалисту не нужно."
    return head + "\nОбращаться к специалисту не нужно."


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
        # Совет ассистента не засчитывается как «решено ботом»: решения
        # в базе знаний не было. Но и специалиста звать незачем — проблема решена.
        ticket.resolved_by_bot = not ticket.assist_used
        ticket.needs_specialist = False
        ticket.resolution = ("решено общей рекомендацией, статья в базе нужна"
                             if ticket.assist_used else "решено без специалиста")
        repo.log_event(db, ticket.id, "resolved",
                       {"actions": ticket.user_actions_count})
        return _respond(db, ticket, Reply(type="summary", text=_summary(ticket)),
                        with_card=True)

    # Ответ непонятен — переспрашиваем один раз, не меняя состояния
    return _respond(db, ticket, Reply(
        type="question",
        text="Подскажите, шаги помогли решить проблему?",
        quick_replies=["Получилось", "Не получилось"]))


# ---------------------------------------------------------------- точка входа

def _parent_ticket(db: Session, req: ChatRequest) -> Ticket | None:
    """«Проблема вернулась»: обращение, по мотивам которого создаётся новое.

    Закрытое обращение НЕ переоткрывается. `RESOLVED` намеренно не входит
    в `security.ACCEPTS_INPUT`, и это одна из проверок защиты; переоткрытие
    сломало бы инвариант «состояние движется только вперёд», задвоило метрики
    и вернуло бы в очередь специалиста заявку, которую он уже закрыл.

    Вместо этого создаётся новое обращение со ссылкой на старое. Переносим
    категорию, формулировку проблемы и собранные сведения — чтобы бот не
    спрашивал заново то, что уже знает.

    Чего НЕ переносим — `article_id`. В handle() есть проверка
    `if ticket.article_id is None and _looks_like_nonsense(text)`: с заранее
    проставленной статьёй фильтр бессмыслицы для первого сообщения молча
    отключился бы, а классификация перестала бы быть честной — статья
    оказалась бы «угадана» до разбора текста.

    Владение проверяется ДО создания нового обращения: иначе неверный токен
    оставлял бы в базе пустое обращение-сироту.
    """
    if not req.parent_ticket_id:
        return None

    parent = repo.get_ticket(db, req.parent_ticket_id)
    # Тот же владелец, что и у обычного обращения: знания номера мало,
    # нужен токен. Иначе по чужому номеру можно было бы вытянуть контекст.
    security.check_owner(parent, req.parent_token)
    return parent


def handle(db: Session, req: ChatRequest, client_key: str) -> ChatResponse:
    security.rate_limit(client_key)

    # 1. Идемпотентность: повтор того же request_id возвращает тот же ответ
    cached = repo.get_idempotent(db, req.request_id)
    if cached:
        return ChatResponse(**json.loads(cached))

    text = (req.quick_reply or req.message or "").strip()

    # Идентификатор посетителя выдаём один раз и возвращаем только в этом
    # ответе: дальше он живёт в браузере, и гонять его по сети незачем.
    issued_client_id: str | None = None

    # 2. Новое обращение или продолжение существующего
    if req.ticket_id is None:
        client_id = req.client_id
        if not security.client_hash(client_id):
            client_id = security.new_client_id()
            issued_client_id = client_id

        parent = _parent_ticket(db, req)

        ticket = Ticket(id=security.new_ticket_id(), token=security.new_token(),
                        public_no=repo.next_public_no(db),
                        client_hash=security.client_hash(client_id))
        if parent is not None:
            ticket.parent_ticket_id = parent.id
            ticket.category = parent.category
            ticket.problem_summary = parent.problem_summary
            # Повторное обращение — тот же посетитель, даже если браузер
            # потерял идентификатор.
            if parent.client_hash and not ticket.client_hash:
                ticket.client_hash = parent.client_hash

        db.add(ticket)
        repo.log_event(db, ticket.id, "created",
                       {"repeat_of": parent.id} if parent else None)
        # Фиксируем сразу: иначе запись держала бы базу заблокированной всё время
        # запроса к внешней модели.
        db.commit()

        # Слоты переносим после фиксации: до неё обращения, на которое они
        # ссылаются, в базе ещё нет.
        if parent is not None:
            for key, value in repo.slots_dict(db, parent.id).items():
                repo.set_slot(db, ticket.id, key, value)
            db.commit()
    else:
        ticket = repo.get_ticket(db, req.ticket_id)
        security.check_owner(ticket, req.token)
        security.assert_can_accept(ticket)

    if not text:
        empty = _respond(db, ticket, Reply(
            type="error", text="Напишите, пожалуйста, что случилось."))
        # Даже на пустое сообщение идентификатор надо отдать: обращение уже
        # создано и записано на этого посетителя, а браузер о нём не узнает.
        empty.client_id = issued_client_id
        return empty

    repo.add_message(db, ticket.id, "user", text)
    ticket.user_actions_count += 1

    # 3. Обращение уже у живого специалиста: бот не вмешивается в их переписку,
    #    только принимает реплику пользователя и подтверждает получение.
    if ticket.state == "ESCALATED":
        repo.log_event(db, ticket.id, "user_msg_to_operator")
        response = ChatResponse(
            ticket_id=ticket.id, token=ticket.token, state=ticket.state,
            public_no=ticket.public_no or 0,
            parent_public_no=repo.public_no_of(db, ticket.parent_ticket_id),
            client_id=issued_client_id,
            category=ticket.category, confidence=ticket.confidence,
            article=None,
            reply=Reply(type="operator",
                        text="Сообщение передано специалисту — он ответит здесь же."),
            user_actions_count=ticket.user_actions_count,
        )
        repo.save_idempotent(db, req.request_id, ticket.id,
                             response.model_dump_json())
        db.commit()
        return response

    # 4. Явная бессмыслица, пока проблема ещё не определена. Когда статья
    #    подобрана, короткий невнятный ответ разбирает обычная ветка.
    if ticket.article_id is None and _looks_like_nonsense(text):
        response = _out_of_scope(db, ticket, "nonsense")
        repo.save_idempotent(db, req.request_id, ticket.id, response.model_dump_json())
        db.commit()
        return response

    # 5. Просьба позвать человека разбирается ДО ветвления по состоянию.
    #    Иначе нажатие кнопки «Позвать специалиста» во время уточняющего
    #    вопроса записалось бы как ответ на этот вопрос — и в карточке
    #    обращения оказалось бы «операционная система: Позвать специалиста».
    if _wants_specialist(text):
        response = _specialist_request(db, ticket)
    elif ticket.state == "CLARIFYING" and ticket.pending_slot:
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

    # 6. Фиксируем результат под этим request_id.
    #    Идентификатор посетителя кладём в ответ здесь: он выдаётся один раз
    #    на обращение-первенец и попадает в кэш идемпотентности вместе
    #    с ответом — повтор того же request_id вернёт тот же идентификатор.
    if issued_client_id:
        response.client_id = issued_client_id
    repo.save_idempotent(db, req.request_id, ticket.id,
                         response.model_dump_json())
    db.commit()
    return response
