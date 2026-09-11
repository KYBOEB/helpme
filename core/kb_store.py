"""
База знаний: карточки из репозитория плюс карточки, добавленные оператором.

Файлы kb/articles правятся через git. Карточки, созданные оператором из панели,
кладутся в data/kb_custom — это примонтированный том, поэтому они переживают
пересборку контейнера.

Так замыкается цикл: система показывает пробелы в базе знаний, оператор тут же
пишет недостающую статью, и следующий пользователь уже получает готовое решение.
"""
from __future__ import annotations

import glob
import logging
import os
import re

import yaml

from common.models import Article, CATEGORIES
from kb.loader import load_articles

log = logging.getLogger(__name__)

CUSTOM_DIR = os.getenv("KB_CUSTOM_DIR", "data/kb_custom")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,40}$")


def _load_custom() -> list[Article]:
    articles: list[Article] = []
    for fp in sorted(glob.glob(os.path.join(CUSTOM_DIR, "*.yaml"))):
        try:
            with open(fp, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            articles.append(Article(**raw))
        except Exception as exc:  # noqa: BLE001
            log.warning("карточка оператора %s повреждена: %s", fp, exc)
    return articles


def all_articles() -> list[Article]:
    """Карточки репозитория плюс операторские. При совпадении id побеждает операторская."""
    merged: dict[str, Article] = {a.id: a for a in load_articles()}
    for a in _load_custom():
        merged[a.id] = a
    return list(merged.values())


def _next_id(existing: set[str]) -> str:
    n = 1
    while f"KB-CUSTOM-{n:03d}" in existing:
        n += 1
    return f"KB-CUSTOM-{n:03d}"


def add_article(data: dict) -> Article:
    """Проверить и сохранить карточку, созданную оператором.

    Валидация строгая: кривая карточка не должна попасть в базу и сломать поиск.
    """
    existing = {a.id for a in all_articles()}

    article_id = str(data.get("id") or "").strip() or _next_id(existing)
    if not _ID_RE.match(article_id):
        raise ValueError("Идентификатор: латиница, цифры, дефис, от 3 до 40 символов")

    category = str(data.get("category") or "").strip()
    if category not in CATEGORIES:
        raise ValueError(f"Категория должна быть одной из: {', '.join(CATEGORIES)}")

    title = str(data.get("title") or "").strip()
    if not (3 <= len(title) <= 200):
        raise ValueError("Заголовок: от 3 до 200 символов")

    steps = [str(s).strip() for s in (data.get("steps") or []) if str(s).strip()]
    symptoms = [str(s).strip().lower() for s in (data.get("symptoms") or []) if str(s).strip()]
    escalate_if = str(data.get("escalate_if") or "").strip() or None

    if not steps and not escalate_if:
        raise ValueError("Либо шаги решения, либо условие передачи специалисту")
    if len(steps) > 12:
        raise ValueError("Не больше 12 шагов: длинные инструкции пользователь не дочитывает")
    if not symptoms:
        raise ValueError("Нужна хотя бы одна формулировка в symptoms — по ним работает поиск")

    article = Article(
        id=article_id, category=category, title=title,
        symptoms=symptoms, steps=steps, escalate_if=escalate_if,
        required_slots=[],   # слоты через панель не заводим, это редкий случай
    )

    os.makedirs(CUSTOM_DIR, exist_ok=True)
    path = os.path.join(CUSTOM_DIR, f"{article_id}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(article.model_dump(exclude={"required_slots"}),
                       f, allow_unicode=True, sort_keys=False)

    log.info("оператор добавил карточку %s (%s)", article_id, category)
    return article
