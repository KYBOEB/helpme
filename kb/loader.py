"""
Сейчас — минимальная рабочая версия: читает все *.yaml из kb/articles и валидирует.
C добавит понятные сообщения об ошибках и проверки уникальности id.
"""
from __future__ import annotations

import glob
import os

import yaml

from common.models import Article

_cache: list[Article] | None = None


def load_articles(path: str = "kb/articles") -> list[Article]:
    """Прочитать и провалидировать все карточки базы знаний."""
    global _cache
    if _cache is not None:
        return _cache

    articles: list[Article] = []
    for fp in sorted(glob.glob(os.path.join(path, "*.yaml"))):
        with open(fp, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if not raw:
            continue
        try:
            articles.append(Article(**raw))
        except Exception as exc:  # TODO(C): человекочитаемое сообщение
            raise ValueError(f"Битая карточка {fp}: {exc}") from exc

    _cache = articles
    return articles
