"""Загрузка и валидация карточек базы знаний."""
from __future__ import annotations

import functools
from pathlib import Path

import yaml
from pydantic import ValidationError

from common.models import CATEGORIES, Article


class KBError(Exception):
    """Понятная ошибка загрузки базы знаний."""


def _iter_yaml_files(root: Path) -> list[Path]:
    if not root.exists():
        raise KBError(f"Папка с карточками не найдена: {root}")
    files = sorted(p for p in root.rglob("*.yaml") if p.is_file())
    if not files:
        raise KBError(f"В папке {root} нет ни одного *.yaml")
    return files


def _load_one(path: Path) -> Article:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise KBError(f"{path.name}: ошибка синтаксиса YAML — {e}") from e

    if raw is None:
        raise KBError(f"{path.name}: файл пустой")
    if not isinstance(raw, dict):
        raise KBError(f"{path.name}: ожидался словарь, получено {type(raw).__name__}")

    try:
        article = Article(**raw)
    except ValidationError as e:
        problems = []
        for err in e.errors():
            field = ".".join(str(x) for x in err["loc"]) or "<корень>"
            problems.append(f"поле '{field}': {err['msg']}")
        raise KBError(f"{path.name}: невалидная карточка — " + "; ".join(problems)) from e

    return article


def _validate_article(article: Article, path: Path) -> None:
    if article.category not in CATEGORIES:
        raise KBError(
            f"{path.name}: category='{article.category}' отсутствует в CATEGORIES. "
            f"Допустимо: {CATEGORIES}"
        )
    if not article.id.startswith("KB-"):
        raise KBError(
            f"{path.name}: id='{article.id}' не соответствует формату KB-<КАТЕГОРИЯ>-<3 цифры>."
        )
    if not article.steps and not article.escalate_if:
        raise KBError(
            f"{path.name}: у карточки пустые steps, но не заполнен escalate_if. "
            f"Карточка без решения обязана содержать escalate_if."
        )


@functools.lru_cache(maxsize=1)
def _load_cached(path_str: str) -> tuple[Article, ...]:
    root = Path(path_str)
    files = _iter_yaml_files(root)

    articles: list[Article] = []
    seen_ids: dict[str, Path] = {}

    for f in files:
        art = _load_one(f)
        _validate_article(art, f)
        if art.id in seen_ids:
            raise KBError(
                f"{f.name}: id='{art.id}' уже встречается в {seen_ids[art.id].name}"
            )
        seen_ids[art.id] = f
        articles.append(art)

    return tuple(articles)


def load_articles(path: str = "kb/articles") -> list[Article]:
    """Загружает все *.yaml из папки, валидирует, кэширует в модуле.

    Бросает KBError с понятным сообщением (имя файла + поле).

    ВАЖНО: результат кэшируется через lru_cache и НЕ сбрасывается
    автоматически при изменении файлов на диске. После правки
    YAML-карточек в том же процессе вызови clear_cache().
    """
    return list(_load_cached(str(Path(path).resolve())))


def clear_cache() -> None:
    """Сбросить кэш.

    ВАЖНО: вызывай после правки YAML-карточек, иначе load_articles()
    вернёт старую версию из кэша до перезапуска процесса.
    """
    _load_cached.cache_clear()