"""Регресс-прогон тест-кейсов по базе знаний.

Запуск:
    python -m tests.run_cases --retrieval-only   # только поиск, без модуля B
    python -m tests.run_cases                    # с роутером, когда он готов
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from kb import HybridRetriever, load_articles
from kb.normalize import informative_tokens

CASES_PATH = Path(__file__).parent / "cases.yaml"

# Порог уверенности для «неполных» кейсов, если в запросе всё-таки
# есть значимые токены, но BM25 не находит ничего осмысленного.
# Сырой BM25-скор топ-1 — вспомогательный сигнал.
RAW_SCORE_THRESHOLD = 0.5

NO_CATEGORY = "не определено"


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def predict_with_router(text: str) -> tuple[str, float]:
    """Полный режим — через роутер участника B."""
    try:
        from llm.router import route  # type: ignore
    except ImportError as e:
        raise SystemExit(
            "Модуль llm.router не найден. Запусти с --retrieval-only "
            "или дождись участника B."
        ) from e
    result = route(text)
    # Договорись с B о формате: ожидаем объект с .category и .confidence
    return result.category, float(getattr(result, "confidence", 0.0))


def check_in_top_k(retriever: HybridRetriever, text: str,
                   expect: str, top_k: int = 5) -> bool:
    """Проверка для кейсов с конкретной ожидаемой категорией."""
    results = retriever.search(text, top_k=top_k)
    return any(a.category == expect for a, _ in results)


def run(retrieval_only: bool) -> int:
    articles = load_articles()
    retriever = HybridRetriever(articles)
    cases = load_cases()

    rows: list[tuple[str, str, str, str, float, bool]] = []
    per_group: dict[str, list[bool]] = {}

    for case in cases:
        cid = case["id"]
        group = case["group"]
        text = case["text"]
        expect = case["expect_category"]

        # приводим ожидание к списку допустимых категорий
        expect_list = expect if isinstance(expect, list) else [expect]
        expect_display = " | ".join(expect_list)

        if retrieval_only:
            if NO_CATEGORY in expect_list:
                # «не определено»: сигнал — информативность запроса.
                # Если значимых токенов нет — система обязана уточнять.
                info = informative_tokens(text)
                results = retriever.search(text, top_k=1)
                got_cat = results[0][0].category if results else "—"
                raw = retriever.raw_top1_score(text)

                no_info = len(info) == 0
                empty = not results or results[0][1] == 0.0
                weak = raw < RAW_SCORE_THRESHOLD

                ok = no_info or empty or weak
                conf = raw
                if ok:
                    got_cat = NO_CATEGORY
            else:
                # правильная категория (любая из списка) должна быть в топ-5
                ok = any(
                    check_in_top_k(retriever, text, e, top_k=5)
                    for e in expect_list
                )
                results = retriever.search(text, top_k=1)
                got_cat = results[0][0].category if results else "—"
                conf = results[0][1] if results else 0.0
        else:
            got_cat, conf = predict_with_router(text)
            ok = got_cat in expect_list

        rows.append((cid, group, expect_display, got_cat, conf, ok))
        per_group.setdefault(group, []).append(ok)

    # ---- таблица ----
    header = (
        f"{'ID':<6} {'ГРУППА':<16} {'ОЖИДАЛИ':<32} "
        f"{'ПОЛУЧИЛИ':<24} {'CONF':<6} РЕЗУЛЬТАТ"
    )
    print(header)
    print("-" * len(header))
    for cid, group, expect, got, conf, ok in rows:
        mark = "OK" if ok else "FAIL"
        print(f"{cid:<6} {group:<16} {expect:<32} {got:<24} {conf:<6.2f} {mark}")

    total = len(rows)
    passed = sum(1 for r in rows if r[5])
    print()
    print(f"ИТОГО: {passed}/{total} ({passed * 100 // total}%)")
    groups_str = ", ".join(
        f"{g} {sum(v)}/{len(v)}" for g, v in sorted(per_group.items())
    )
    print(f"по группам: {groups_str}")

    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Проверять только поиск, без роутера участника B",
    )
    args = parser.parse_args()
    return run(retrieval_only=args.retrieval_only)


if __name__ == "__main__":
    sys.exit(main())