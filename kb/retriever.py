"""Гибридный поиск по базе знаний: BM25 (+ опционально эмбеддинги через RRF)."""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from rank_bm25 import BM25Okapi

from common.models import Article
from kb.normalize import tokenize


EmbedFn = Callable[[list[str]], np.ndarray]
_RRF_K = 60


def _article_text(article: Article) -> str:
    """Текст карточки для индексации: title + symptoms + category."""
    parts = [article.title, article.category]
    parts.extend(article.symptoms)
    return " \n ".join(parts)


def _rrf_fuse(rankings: list[list[int]], k: int = _RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion: список ранжирований → {doc_idx: score}."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc_idx in enumerate(ranking, start=1):
            scores[doc_idx] = scores.get(doc_idx, 0.0) + 1.0 / (k + rank)
    return scores


def _normalize_scores(scores: dict[int, float]) -> dict[int, float]:
    """Приводим RRF-скоры к 0..1 (делим на максимум)."""
    if not scores:
        return {}
    max_s = max(scores.values())
    if max_s <= 0:
        return scores
    return {i: s / max_s for i, s in scores.items()}


class HybridRetriever:
    def __init__(
        self,
        articles: list[Article],
        embed_fn: Optional[EmbedFn] = None,
    ) -> None:
        if not articles:
            raise ValueError("HybridRetriever получил пустой список статей")

        self.articles = articles
        self.embed_fn = embed_fn

        # --- BM25 ---
        self._corpus_tokens = [tokenize(_article_text(a)) for a in articles]
        # rank_bm25 падает на пустом корпусе — подстрахуемся
        self._bm25 = BM25Okapi(self._corpus_tokens) if any(self._corpus_tokens) else None

        # --- Эмбеддинги (опционально) ---
        self._emb_matrix: Optional[np.ndarray] = None
        if embed_fn is not None:
            texts = [_article_text(a) for a in articles]
            emb = np.asarray(embed_fn(texts), dtype=np.float32)
            if emb.ndim != 2 or emb.shape[0] != len(articles):
                raise ValueError(
                    f"embed_fn вернул массив формы {emb.shape}, "
                    f"ожидалось ({len(articles)}, dim)"
                )
            # L2-нормализация, чтобы косинус = скалярное произведение
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._emb_matrix = emb / norms

    # ---------- публичный API ----------

    def search(self, query: str, top_k: int = 5) -> list[tuple[Article, float]]:
        if not query or not query.strip():
            return []
        if top_k <= 0:
            return []

        rankings: list[list[int]] = []

        # 1. BM25 → ранжированный список индексов
        bm25_ranking = self._bm25_ranking(query)
        if bm25_ranking:
            rankings.append(bm25_ranking)

        # 2. Эмбеддинги → второй ранжированный список
        if self._emb_matrix is not None and self.embed_fn is not None:
            emb_ranking = self._embedding_ranking(query)
            if emb_ranking:
                rankings.append(emb_ranking)

        if not rankings:
            return []

        # 3. RRF
        fused = _rrf_fuse(rankings)
        fused_norm = _normalize_scores(fused)

        # 4. Top-k
        ordered = sorted(fused_norm.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [(self.articles[i], float(score)) for i, score in ordered]

    # ---------- внутреннее ----------
    
    def raw_top1_score(self, query: str) -> float:
        """Сырой BM25-скор лучшего документа — сигнал «уверенности» до RRF.

        Возвращает 0.0, если BM25 ничего не нашёл. Полезно вызывающей стороне,
        чтобы решить: «поиск уверен» или «надо уточнять».
        """
        if self._bm25 is None:
            return 0.0
        q_tokens = tokenize(query)
        if not q_tokens:
            return 0.0
        scores = self._bm25.get_scores(q_tokens)
        return float(scores.max()) if len(scores) else 0.0

    def _bm25_ranking(self, query: str) -> list[int]:
        if self._bm25 is None:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores = self._bm25.get_scores(q_tokens)
        # Индексы в порядке убывания; нулевые отсекаем
        order = np.argsort(-scores)
        return [int(i) for i in order if scores[i] > 0]

    def _embedding_ranking(self, query: str) -> list[int]:
        assert self._emb_matrix is not None and self.embed_fn is not None
        q = np.asarray(self.embed_fn([query]), dtype=np.float32)
        if q.ndim == 2:
            q = q[0]
        n = np.linalg.norm(q)
        if n == 0:
            return []
        q = q / n
        sims = self._emb_matrix @ q
        order = np.argsort(-sims)
        return [int(i) for i in order]