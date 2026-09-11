"""
Калибровка route() на tests/cases.yaml. Инструмент отладки, не часть контракта.
Запуск: python -m llm.calibrate
"""
from __future__ import annotations

from pathlib import Path

import yaml

from llm import router

CASES_PATH = Path(__file__).resolve().parent.parent / "tests" / "cases.yaml"


def load_cases() -> list[dict]:
    with open(CASES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def check(actual: str, expected) -> bool:
    if isinstance(expected, list):
        return actual in expected
    return actual == expected


def main() -> None:
    cases = load_cases()
    total = len(cases)
    correct = 0
    by_group: dict[str, list[int]] = {}

    for case in cases:
        result = router.route(case["text"], [], [], {})
        ok = check(result.category, case["expect_category"])
        correct += ok

        group = case.get("group", "?")
        by_group.setdefault(group, [0, 0])
        by_group[group][1] += 1
        if ok:
            by_group[group][0] += 1

        mark = "OK  " if ok else "FAIL"
        print(f"{mark} {case['id']:6} ожидали={str(case['expect_category']):35} получили={result.category}")

    print()
    print("=== По группам ===")
    for group, (ok_count, total_count) in by_group.items():
        print(f"{group:20} {ok_count}/{total_count}")

    accuracy = correct / total * 100
    print()
    print(f"ИТОГО: {correct}/{total} = {accuracy:.1f}%")


if __name__ == "__main__":
    main()