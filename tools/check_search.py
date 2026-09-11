"""
Честная проверка поиска: находит ли система статью, если пользователь написал
НЕ теми словами, что записаны в карточке.

    python3 -m tools.check_search

Зачем отдельный скрипт. Метрика «полнота поиска 40 из 40» из отборочного этапа
считалась на вопросах, составленных по тем же карточкам, тем же лексическим
индексом. Такая проверка ничего не доказывает: она измеряет совпадение слов
с самими собой. Здесь запросы намеренно написаны иначе — синонимы, разговорные
формулировки, опечатки, описание симптома вместо названия проблемы.

Скрипт сравнивает два режима на одних и тех же запросах:
  * BM25 — как работало в отборочном этапе;
  * BM25 + эмбеддинги через RRF — как работает после подключения индекса.

Ожидаемые id можно и нужно править под свою базу знаний.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.kb_store import all_articles         # noqa: E402
from kb import embeddings                      # noqa: E402
from kb.retriever import HybridRetriever       # noqa: E402

TOP_K = 5

# (запрос пользователя, id статьи, которая обязана оказаться в топ-5)
CASES: list[tuple[str, str]] = [
    ("не пускает во впн",                          "KB-VPN-001"),
    ("вpн не рабоатет",                            "KB-VPN-001"),
    ("не могу зайти в рабочую сеть из дома",       "KB-VPN-001"),
    ("соединение с офисом рвётся каждые пять минут", "KB-VPN-002"),
    ("ругается на сертификат когда жму подключить", "KB-VPN-003"),
    ("с телефона в рабочую сеть не заходит",        "KB-VPN-005"),
    ("ноут не видит корпоративную сеть",            "KB-WIFI-004"),
    ("подключился к воздуху а страницы не грузятся", "KB-WIFI-002"),
    ("письма висят в исходящих",                    "KB-EMAIL-001"),
    ("ящик забился, пишет нет места",               "KB-EMAIL-003"),
    ("не помню пароль от учётки",                   "KB-ACCESS-001"),
    ("смс с кодом не приходит при входе",           "KB-ACCESS-005"),
    ("бумага есть, а печати нет",                   "KB-HW-001"),
    ("наушники молчат, в динамиках тишина",         "KB-HW-003"),
    ("комп гудит и всё открывается вечность",       "KB-WORKPLACE-003"),
    ("машина включена, а на мониторе пусто",        "KB-WORKPLACE-002"),
    ("приложение вылетает через минуту работы",     "KB-SOFT-005"),
    ("после апдейта винды софт перестал открываться", "KB-SOFT-007"),
]


def run(retriever: HybridRetriever, label: str) -> int:
    print(f"\n=== {label} ===")
    hits = 0
    for query, expected in CASES:
        found = [a.id for a, _ in retriever.search(query, top_k=TOP_K)]
        ok = expected in found
        hits += ok
        mark = "+" if ok else "-"
        where = f"место {found.index(expected) + 1}" if ok else f"нет в топ-{TOP_K}"
        print(f"  {mark} {query!r:48} → {where}")
    print(f"  Итого: {hits} из {len(CASES)}")
    return hits


def main() -> None:
    articles = all_articles()
    known = {a.id for a in articles}
    missing = {exp for _, exp in CASES if exp not in known}
    if missing:
        print(f"ВНИМАНИЕ: в базе знаний нет статей {sorted(missing)} — "
              f"поправьте CASES под свою базу.\n")

    lexical = run(HybridRetriever(articles), "BM25 — как было")

    embed_fn = embeddings.make_embed_fn()
    if embed_fn is None:
        print("\nИндекса эмбеддингов нет. Соберите его: python3 -m tools.build_emb_index")
        raise SystemExit(1)

    hybrid = run(HybridRetriever(articles, embed_fn=embed_fn),
                 "BM25 + эмбеддинги (RRF) — как стало")

    print(f"\nПрирост: {hybrid - lexical} запросов из {len(CASES)}")
    if hybrid < lexical:
        print("Стало хуже — не выкатывайте, разбирайтесь.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
