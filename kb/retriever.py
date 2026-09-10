"""
Гибридный поиск по базе знаний.
Сейчас — примитивный поиск по вхождению слов. C заменит на BM25 + эмбеддинги с RRF.
Сигнатуру менять нельзя.
"""
from __future__ import annotations

from common.models import Article


class HybridRetriever:
    def __init__(self, articles: list[Article], embed_fn=None) -> None:
        self.articles = articles
        self.embed_fn = embed_fn

    def search(self, query: str, top_k: int = 5) -> list[tuple[Article, float]]:
        """Вернуть top_k пар (статья, оценка 0..1), отсортированных по убыванию."""
        # TODO(C): BM25 + эмбеддинги + RRF
        q = set(query.lower().replace("ё", "е").split())
        scored: list[tuple[Article, float]] = []
        for a in self.articles:
            hay = " ".join([a.title, a.category, *a.symptoms]).lower().replace("ё", "е")
            words = set(hay.split())
            hits = len(q & words)
            if hits:
                scored.append((a, hits / max(len(q), 1)))
        scored.sort(key=lambda p: p[1], reverse=True)
        return scored[:top_k]
