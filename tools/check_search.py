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

# (запрос пользователя, идентификаторы статей, любая из которых считается
#  верным ответом — через запятую)
#
# Вариантов несколько намеренно. База выросла со сорока карточек до сотни,
# и у некоторых запросов появился ВТОРОЙ подходящий ответ. Например
# «не могу зайти в рабочую сеть из дома» раньше должно было находить
# KB-VPN-001, а теперь есть отдельная карточка про работу из дома, и она
# подходит лучше. Требовать ровно один идентификатор — значит записывать
# в промахи улучшение.
CASES: list[tuple[str, str]] = [
    ("не пускает во впн",                          "KB-VPN-001"),
    ("вpн не рабоатет",                            "KB-VPN-001"),
    ("не могу зайти в рабочую сеть из дома",       "KB-VPN-001,KB-VPN-008"),
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
    # Два симптома в одной фразе: «гудит» — это перегрев, «открывается
    # вечность» — это тормоза. Подходящих карточек честно несколько, и в
    # продукте на таком обращении срабатывает экран уточнения. Требовать
    # ровно одну статью здесь неправильно.
    ("комп гудит и всё открывается вечность",
     "KB-WORKPLACE-003,KB-WORKPLACE-009,KB-HW-013"),
    # Тот же симптом без примеси перегрева — проверка, что тормоза находятся
    # сами по себе.
    ("всё открывается по пять минут, работать невозможно",
     "KB-WORKPLACE-003,KB-WORKPLACE-009"),
    ("машина включена, а на мониторе пусто",        "KB-WORKPLACE-002"),
    ("приложение вылетает через минуту работы",     "KB-SOFT-005"),
    ("после апдейта винды софт перестал открываться", "KB-SOFT-007"),
]


def expected_ids(spec: str) -> list[str]:
    return [i.strip() for i in spec.split(",") if i.strip()]


def run(retriever: HybridRetriever, label: str, diag: bool = False) -> int:
    print(f"\n=== {label} ===")
    hits = 0
    for query, spec in CASES:
        wanted = expected_ids(spec)
        found = [a.id for a, _ in retriever.search(query, top_k=TOP_K)]
        place = next((found.index(w) + 1 for w in wanted if w in found), None)
        ok = place is not None
        expected = wanted[0]
        hits += ok
        mark = "+" if ok else "-"
        where = f"место {place}" if ok else f"нет в топ-{TOP_K}"
        # Скор BM25 нужен, чтобы подобрать KB_SKIP_VECTOR_SCORE: порог, выше
        # которого за вектором в сеть можно не ходить вовсе.
        score = retriever.raw_top1_score(query)
        print(f"  {mark} {query!r:48} → {where:16} скор BM25 {score:5.1f}")

        # Для промахов показываем, какой именно сигнал не сработал: лексический,
        # векторный или объединение. Иначе поиск чинится гаданием.
        if diag and not ok:
            # Что выиграло вместо ожидаемого — обычно это и есть ответ на
            # вопрос «поиск сломался или в базе появилась статья получше».
            print(f"      выдача: {', '.join(found[:3]) or 'пусто'}")
            d = retriever.explain(query, expected)
            bm = d["bm25"] or "не найдена"
            em = d["embed"] or "—"
            sim = d["similarity"]
            best = d["best_similarity"]
            print(f"      BM25: {bm} | вектора: {em}"
                  + (f" (близость {sim}, лучшая в базе {best})" if sim is not None else "")
                  + (f" | ошибка: {d['error']}" if d.get("error") else ""))
    print(f"  Итого: {hits} из {len(CASES)}")
    return hits


def main() -> None:
    articles = all_articles()
    known = {a.id for a in articles}
    # Ругаемся, только если НИ ОДИН из допустимых идентификаторов не найден:
    # часть вариантов может относиться к карточкам, которых в этой базе нет,
    # и это нормально.
    missing = [spec for _, spec in CASES
               if not any(i in known for i in expected_ids(spec))]
    if missing:
        print(f"ВНИМАНИЕ: в базе знаний нет ни одной из статей {missing} — "
              f"поправьте CASES под свою базу.\n")

    lexical = run(HybridRetriever(articles), "BM25 — как было", diag=True)

    embed_fn = embeddings.make_embed_fn()
    if embed_fn is None:
        print("\nИндекса эмбеддингов нет. Соберите его: python3 -m tools.build_emb_index")
        raise SystemExit(1)

    hybrid = run(HybridRetriever(articles, embed_fn=embed_fn),
                 "BM25 + эмбеддинги (RRF) — как стало", diag=True)

    # Подсказка по порогу: ниже какого скора BM25 начинает промахиваться.
    ok_scores, miss_scores = [], []
    plain = HybridRetriever(articles)
    for query, spec in CASES:
        found = [a.id for a, _ in plain.search(query, top_k=TOP_K)]
        hit = any(w in found for w in expected_ids(spec))
        (ok_scores if hit else miss_scores).append(plain.raw_top1_score(query))
    if ok_scores and miss_scores:
        print(f"\nСкор BM25: на попаданиях от {min(ok_scores):.1f}, "
              f"на промахах до {max(miss_scores):.1f}.")
        if min(ok_scores) > max(miss_scores):
            print(f"Можно не ходить в сеть за вектором, когда BM25 и так уверен: "
                  f"KB_SKIP_VECTOR_SCORE={min(ok_scores):.0f}")

    print(f"\nПрирост: {hybrid - lexical} запросов из {len(CASES)}")
    if hybrid < lexical:
        print("Стало хуже — не выкатывайте, разбирайтесь.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
