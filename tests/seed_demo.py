"""
Наполнение базы демонстрационными обращениями.

Обращения создаются НЕ вставкой в базу, а через настоящий /api/chat: система
обрабатывает их так же, как живого пользователя. Поэтому метрики на вкладке
«Аналитика» получаются настоящие, а не нарисованные, и заодно прогревается кэш
модели — то есть скрипт заменяет ручную подготовку к демонстрации.

Запуск:
    python -m tests.seed_demo                              # локально
    python -m tests.seed_demo https://helpme-tpu.duckdns.org
    python -m tests.seed_demo <адрес> --delay 3            # медленнее

Перед прогоном имеет смысл очистить базу, чтобы номера обращений пошли с 1001:
    docker compose exec app rm -f data/helpme.db && docker compose restart app
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

QR_SOLVED = "Получилось"
QR_FAILED = "Не получилось"

# Сценарии специально разные: все семь категорий каталога, решённые и нерешённые,
# нецелевые и брошенные. Так дашборд показывает осмысленное распределение,
# а не двадцать одинаковых обращений про VPN.
SCENARIOS: list[dict] = [
    # --- VPN ---
    {"text": "не подключается корпоративный vpn", "answers": ["Windows"],
     "outcome": "solved", "rating": 1},
    {"text": "впн отваливается через пару минут после подключения",
     "answers": ["macOS"], "outcome": "failed", "rating": 0},
    {"text": "vpn выдаёт ошибку сертификата", "answers": ["Windows"],
     "outcome": "solved", "rating": 1},

    # --- Wi-Fi ---
    {"text": "не могу подключиться к корпоративному wi-fi",
     "outcome": "solved", "rating": 1},
    {"text": "ноутбук не видит сеть wifi в переговорной",
     "outcome": "solved", "rating": None},

    # --- доступы ---
    {"text": "забыл пароль от учётной записи", "outcome": "solved", "rating": 1},
    {"text": "нет доступа к сетевой папке отдела", "outcome": "solved", "rating": 1},
    {"text": "не приходит код двухфакторной аутентификации",
     "outcome": "failed", "rating": 0},

    # --- корпоративная почта ---
    {"text": "не приходят письма на рабочую почту", "outcome": "solved", "rating": 1},
    {"text": "почта требует ввести пароль заново каждый час",
     "outcome": "solved", "rating": None},

    # --- рабочее место ---
    {"text": "компьютер не включается", "outcome": "solved", "rating": 1},
    {"text": "сильно тормозит компьютер, всё открывается по минуте",
     "outcome": "solved", "rating": 1},
    {"text": "синий экран при загрузке windows", "outcome": "failed", "rating": 0},

    # --- оборудование ---
    {"text": "не печатает принтер", "outcome": "solved", "rating": 1},
    {"text": "сканер не видит документ в лотке", "outcome": "solved", "rating": None},
    {"text": "перестала работать мышь", "outcome": "solved", "rating": 1},

    # --- программное обеспечение ---
    {"text": "не запускается офис, пишет что слетела лицензия",
     "outcome": "solved", "rating": 1},
    {"text": "ошибка при обновлении программы, код 0x80072ee7",
     "outcome": "failed", "rating": 0},

    # --- брошенное: пользователь ушёл, не дойдя до конца ---
    {"text": "не работает док-станция, монитор не видит ноутбук",
     "outcome": "abandon", "rating": None},

    # --- нецелевые: их система закрывает сама ---
    {"text": "почему небо голубое", "outcome": "stop", "rating": None},
    {"text": "у ребёнка выпал молочный зубик, что делать",
     "outcome": "stop", "follow_up": ["а какая завтра погода"], "rating": None},
    {"text": "))))))", "outcome": "stop", "follow_up": ["!!!!!!"], "rating": None},

    # --- просьба о специалисте без описания проблемы ---
    {"text": "вызовите специалиста", "outcome": "stop",
     "follow_up": ["Всё равно позвать специалиста"], "rating": None},
]


def post(base: str, path: str, payload: dict, retries: int = 2) -> dict:
    """Отправить запрос. На временной ошибке сервера или сети пробует ещё раз."""
    last: dict = {}
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(3 * attempt)
        req = urllib.request.Request(
            base.rstrip("/") + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:200]
            last = {"__http_error__": exc.code, "__body__": body}
            if exc.code not in (429, 500, 502, 503, 504):
                return last
        except Exception as exc:                               # noqa: BLE001
            last = {"__error__": str(exc)}
    return last


def send(base: str, ticket_id, token, message=None, quick_reply=None) -> dict:
    return post(base, "/api/chat", {
        "ticket_id": ticket_id, "token": token,
        "request_id": str(uuid.uuid4()),
        "message": message, "quick_reply": quick_reply,
    })


def run_scenario(base: str, sc: dict, delay: float) -> str:
    """Провести один диалог до конца. Возвращает короткий итог для отчёта."""
    data = send(base, None, None, message=sc["text"])
    if "__error__" in data or "__http_error__" in data:
        return f"ОШИБКА: {data}"

    ticket_id, token = data["ticket_id"], data["token"]
    answers = list(sc.get("answers", []))
    follow_up = list(sc.get("follow_up", []))

    for _ in range(10):
        reply = data.get("reply") or {}
        kind = reply.get("type")

        if kind in ("summary", "escalation", "closed", "error"):
            break

        if sc["outcome"] == "stop" and kind not in ("steps",):
            if not follow_up:
                break
            time.sleep(delay)
            data = send(base, ticket_id, token, quick_reply=follow_up.pop(0))
            continue

        time.sleep(delay)

        if kind == "steps":
            if sc["outcome"] == "abandon":
                post(base, f"/api/tickets/{ticket_id}/close", {"token": token})
                return f"{data.get('state')} · брошено пользователем"
            qr = QR_SOLVED if sc["outcome"] == "solved" else QR_FAILED
            data = send(base, ticket_id, token, quick_reply=qr)
        elif kind in ("question", "choice"):
            if answers:
                data = send(base, ticket_id, token, quick_reply=answers.pop(0))
            elif reply.get("quick_replies"):
                data = send(base, ticket_id, token,
                            quick_reply=reply["quick_replies"][0])
            else:
                data = send(base, ticket_id, token, message="не знаю")
        else:
            break

        if "__error__" in data or "__http_error__" in data:
            return f"ОШИБКА: {data}"

    if sc.get("rating") is not None:
        time.sleep(delay / 2)
        post(base, f"/api/tickets/{ticket_id}/rate",
             {"rating": sc["rating"], "token": token})

    card = data.get("ticket_card") or {}
    no = card.get("public_no") or ""
    return f"{data.get('state', '?')} · {data.get('category') or 'не определено'}" \
           + (f" · №{no}" if no else "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Наполнить базу демо-обращениями")
    ap.add_argument("base", nargs="?", default="http://127.0.0.1:8000",
                    help="адрес приложения")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="пауза между запросами, секунды (защита от лимита частоты)")
    ap.add_argument("--only", type=int, default=0,
                    help="прогнать только первые N сценариев")
    args = ap.parse_args()

    scenarios = SCENARIOS[:args.only] if args.only else SCENARIOS
    print(f"Наполняем {args.base}: {len(scenarios)} обращений, "
          f"пауза {args.delay} с\n")

    failures = 0
    for i, sc in enumerate(scenarios, 1):
        result = run_scenario(args.base, sc, args.delay)
        if result.startswith("ОШИБКА"):
            failures += 1
        print(f"{i:2}. {sc['text'][:46]:<48} → {result}")
        time.sleep(args.delay)

    print(f"\nГотово. Обращений: {len(scenarios)}, ошибок: {failures}.")
    print("Загляните на вкладку «Аналитика» — метрики уже пересчитаны.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
