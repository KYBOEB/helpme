"""
Наполнение базы знаний и переписывание шагов под живого человека.

    python3 -m tools.kb_gen --rewrite      # переписать шаги существующих карточек
    python3 -m tools.kb_gen --new          # создать новые карточки из tools/kb_topics.py
    python3 -m tools.kb_gen --new --limit 10   # первые десять, для пробы

Зачем. Комиссия отборочного этапа написала две вещи:

  «Информация в шагах недостаточно детализирована, предполагает от пользователя
   высокой квалификации, с которой тот и не будет обращаться в поддержку
   (на примере типового кейса про VPN)»

  «Не понятна в целом предметная область, в которой чат помогает
   (она сильно ограничена)»

Первое лечится переписыванием шагов, второе — объёмом и охватом базы.

Что генерируется, а что нет. Заголовки, симптомы и уточняющие вопросы написаны
руками в `tools/kb_topics.py`. Модель пишет ТОЛЬКО шаги. Так она не придумает
ни новую категорию, ни несуществующий внутренний портал, ни телефон отдела,
которого нет, — а именно на таком враньё ловится мгновенно.

Всё написанное моделью проходит проверку перед записью на диск: формат шага,
длина, количество, запрещённые советы, выдуманные адреса и телефоны.
Что не прошло — не записывается, а печатается в конце списком.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.models import Article           # noqa: E402
from kb.loader import load_articles         # noqa: E402
from tools.kb_topics import CATEGORY_DIRS, TOPICS   # noqa: E402

ARTICLES_ROOT = Path("kb/articles")

# Карточек за один запрос. Четыре оказалось много: ответ длинный, модель
# не укладывалась в таймаут, и целые пачки терялись с «модель не вернула шаги».
BATCH = int(os.getenv("KB_GEN_BATCH", "3"))

# Пакетному инструменту ждать не жалко: перед экраном никто не сидит.
TIMEOUT = float(os.getenv("KB_GEN_TIMEOUT", "120"))

SYSTEM = """Ты пишешь инструкции для службы технической поддержки университета.
Читатель — обычный сотрудник или студент. Если бы он умел решать проблему сам,
он бы не писал в поддержку.

КАЖДЫЙ ШАГ — ОДНО ЖИВОЕ ПРЕДЛОЖЕНИЕ, в котором есть три вещи:
что сделать, где именно это находится и что человек увидит, когда сделает.

НИКАКИХ стрелок, схем и конструкций вида «действие — место — результат».
Пишите так, как объяснили бы коллеге голосом.

Плохо (слишком коротко, человек не найдёт):
  «Проверьте срок действия сертификата: Настройки → Профиль»

Плохо (схема вместо речи, символ стрелки запрещён):
  «Откройте настройки → в разделе профиль → появится дата»

Хорошо:
  «Откройте программу VPN, нажмите «Настройки» в левом нижнем углу и выберите
   «Профиль» — у строки «Сертификат» будет указана дата окончания, и если она
   уже прошла, сертификат нужно обновить»

ПРАВИЛА:
1. От трёх до шести шагов. Каждый шаг — одно законченное действие.
2. Начинай с самого простого и безопасного, заканчивай тем, что помогает реже.
3. Только обратимые действия. ЗАПРЕЩЕНО: удаление файлов, форматирование,
   отключение антивируса, правка реестра, сброс к заводским настройкам,
   переустановка системы.
4. НЕ ВЫДУМЫВАЙ: названий внутренних порталов и систем, адресов сайтов,
   телефонов, номеров кабинетов, имён отделов и сотрудников. Ты не знаешь
   устройства этого университета. Пиши «обратитесь в техподдержку», а не
   «позвоните в отдел ИТ по номеру 123».
5. Названия кнопок и пунктов меню операционных систем и обычных программ
   писать можно и нужно — это как раз то, чего не хватает читателю.
6. Русский язык, обращение на «вы», без канцелярита.
7. Не ссылайся на «инструкции университета», «внутренний портал» и прочие
   источники, которых ты не видел. Если точного адреса не знаешь — пиши
   «на странице входа в корпоративную учётную запись», а не «по ссылке
   из инструкций».
8. Не пиши в шаге слово «шаг» и не нумеруй шаги: нумерацию добавит интерфейс.

ФОРМАТ ОТВЕТА — строго такой, без пояснений до и после:
### <идентификатор карточки>
ШАГ: <текст>
ШАГ: <текст>
ЭСКАЛАЦИЯ: <в каком случае нужен живой специалист, одна фраза>
"""

# --------------------------------------------------------------- проверки

# Выдуманные координаты: адреса, телефоны, номера кабинетов
FORBIDDEN = [
    (re.compile(r"https?://|www\.|\.ru\b|\.com\b", re.I), "выдуманный адрес сайта"),
    (re.compile(r"\+?\d[\d\-\s()]{6,}\d"), "выдуманный телефон или номер"),
    (re.compile(r"каб(инет)?\.?\s*№?\s*\d+", re.I), "выдуманный номер кабинета"),
    # Опасно не слово «удалить», а ЧТО удаляют.
    #
    # Первая версия ловила «удалённый доступ». Вторая — «Забыть сеть — сеть
    # удалится из сохранённых», «без удаления личных документов», «не удаляя
    # старый способ подтверждения». Всё это правильные советы, и половина
    # карточек по Wi-Fi без них не пишется вовсе: забыть сеть и подключиться
    # заново — стандартный шаг.
    #
    # Поэтому смотрим на объект: файлы, папки, документы, диск, раздел,
    # систему, учётную запись. Профиль сети, сохранённое подключение и кэш
    # удалять можно. Обороты «без удаления» и «не удаляя» — это обещание
    # НЕ удалять, они безопасны по смыслу.
    (re.compile(r"(?i)(?<!без )(?<!не )удал(?!ённ|енн)\w*\s+"
                r"(?:\w+\s+){0,2}?(файл|папк|документ|данны|диск|раздел"
                r"|систем|учётн|учетн|профиль пользовател)"),
     "удаление пользовательских данных"),
    (re.compile(r"(?i)(снес(и|ти)|формати(ру|рова)\w*"
                r"|отключ\w*\s+(антивирус|защит|брандмауэр|firewall)"
                r"|реестр|regedit|сброс\w*\s+(до\s+)?заводск"
                r"|переустанов\w*\s+(систем|windows))"), "опасное действие"),
    # Стрелка означает, что модель написала схему вместо предложения.
    (re.compile(r"[→>]{1}\s"), "схема вместо живого предложения"),
    (re.compile(r"(?i)инструкц\w*\s+университета|внутренн\w*\s+портал"),
     "ссылка на источник, которого модель не видела"),
]

MIN_STEP_LEN = 45       # короче — это снова «Проверьте настройки»
MAX_STEP_LEN = 400
MIN_STEPS = 3
MAX_STEPS = 6


def validate_steps(steps: list[str]) -> list[str]:
    """Вернуть список проблем. Пустой список — карточка годится."""
    problems = []
    if not (MIN_STEPS <= len(steps) <= MAX_STEPS):
        problems.append(f"шагов {len(steps)}, нужно от {MIN_STEPS} до {MAX_STEPS}")
    for step in steps:
        if len(step) < MIN_STEP_LEN:
            problems.append(f"слишком коротко ({len(step)} символов): {step[:60]}")
        if len(step) > MAX_STEP_LEN:
            problems.append(f"слишком длинно ({len(step)} символов): {step[:60]}")
        for pattern, label in FORBIDDEN:
            found = pattern.search(step)
            if found:
                # Показываем ИМЕННО то, что сработало, с контекстом. Иначе
                # непонятно, настоящая это проблема или промах фильтра —
                # на «удалённом доступе» мы уже один раз так ошиблись.
                a, b = found.span()
                context = step[max(0, a - 25):min(len(step), b + 25)]
                problems.append(f"{label} — «{found.group().strip()}» "
                                f"в «…{context}…»")
    return problems


# --------------------------------------------------------------- разбор

def _clean(step: str) -> str:
    """Убрать разметку, которую модель приносит по привычке.

    Обратные кавычки и звёздочки в шаге — это markdown, а интерфейс чата
    показывает текст как есть. Пользователь увидит «путь в формате
    `\\\\server\\share`» вместе с кавычками и решит, что их надо набирать.
    """
    step = re.sub(r"[`*]+", "", step)
    step = re.sub(r"^\s*\d+[.)]\s*", "", step)     # нумерация от модели
    return re.sub(r"\s{2,}", " ", step).strip()


def parse_answer(raw: str) -> dict[str, dict]:
    """Разобрать ответ модели: идентификатор → {steps, escalate_if}."""
    out: dict[str, dict] = {}
    current = None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("###"):
            current = line.lstrip("#").strip()
            out[current] = {"steps": [], "escalate_if": ""}
        elif current and line.upper().startswith("ШАГ:"):
            out[current]["steps"].append(_clean(line.split(":", 1)[1]))
        elif current and line.upper().startswith("ЭСКАЛАЦИЯ:"):
            out[current]["escalate_if"] = _clean(line.split(":", 1)[1])
    return out


def ask(items: list[dict]) -> dict[str, dict]:
    """items: [{key, category, title, symptoms}] → разобранный ответ модели."""
    from llm.client import chat

    blocks = []
    for it in items:
        blocks.append(
            f"### {it['key']}\n"
            f"Категория: {it['category']}\n"
            f"Проблема: {it['title']}\n"
            f"Как её описывают люди: {'; '.join(it['symptoms'])}"
            + (f"\nУже известно от пользователя: "
               f"{', '.join(s['key'] for s in it.get('slots', []))}"
               if it.get("slots") else "")
        )
    user = ("Напиши шаги для каждой карточки. Идентификаторы в ответе "
            "повторяй точно.\n\n" + "\n\n".join(blocks))

    raw = chat([{"role": "system", "content": SYSTEM},
                {"role": "user", "content": user}],
               model=os.getenv("LLM_MODEL_SMART"), temperature=0.3,
               max_tokens=2200, timeout=TIMEOUT)
    return parse_answer(raw)


# --------------------------------------------------------------- запись

def next_id(prefix: str, taken: set[str]) -> str:
    n = 1
    while f"{prefix}-{n:03d}" in taken:
        n += 1
    return f"{prefix}-{n:03d}"


def _q(text: str) -> str:
    """Строка в двойных кавычках, как в карточках, написанных руками."""
    return '"' + str(text).replace("\\", "\\\\").replace('"', "'").strip() + '"'


def dump_card(card: dict) -> str:
    """Сериализация в том же стиле, в каком карточки писала команда.

    `yaml.safe_dump` формально верен, но переносит длинные строки, снимает
    кавычки и меняет отступы списков — из-за этого правка двух шагов давала
    diff на весь файл, и вычитывать его невозможно.
    """
    # Заголовок закавычиваем, если в нём есть что-то, ломающее YAML.
    # На этом уже споткнулись: «VPN не работает в чужой сети: кафе, отель»
    # превращалось в `title: VPN ...: кафе` и падало с «mapping values are
    # not allowed here».
    title = card["title"]
    needs_quotes = any(ch in title for ch in ':#"\'{}[]&*!|>%@`') \
        or title.strip() != title or title[:1] == "-"

    lines = [f"id: {card['id']}",
             f"category: {card['category']}",
             f"title: {_q(title) if needs_quotes else title}"]

    lines.append("symptoms:")
    for s in card.get("symptoms", []):
        lines.append(f"  - {_q(s)}")

    slots = card.get("required_slots") or []
    if slots:
        lines.append("required_slots:")
        for slot in slots:
            lines.append(f"  - key: {slot['key']}")
            lines.append(f"    question: {_q(slot['question'])}")
            if slot.get("options"):
                opts = ", ".join(_q(o) for o in slot["options"])
                lines.append(f"    options: [{opts}]")
            if slot.get("optional"):
                lines.append("    optional: true")

    lines.append("steps:")
    for s in card.get("steps", []):
        lines.append(f"  - {_q(s)}")

    if card.get("escalate_if"):
        lines.append(f"escalate_if: {_q(card['escalate_if'])}")

    return "\n".join(lines) + "\n"


def write_card(card: dict, path: Path) -> None:
    """Записать карточку, предварительно прогнав её через ту же валидацию,
    что и загрузчик приложения. Битую карточку на диск не кладём."""
    Article(**card)                      # бросит ValidationError, если что-то не так
    text = dump_card(card)
    # Контрольная сборка: то, что записываем, должно читаться обратно
    # и оставаться валидной карточкой.
    Article(**yaml.safe_load(text))
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": иначе на Windows файлы получают CRLF, и git ругается
    # на каждую карточку при коммите.
    path.write_text(text, encoding="utf-8", newline="\n")


# --------------------------------------------------------------- режимы

def _title_words(title: str) -> set[str]:
    return {w for w in re.findall(r"[а-яёa-z0-9]+", title.lower()) if len(w) > 3}


def mode_new(limit: int | None) -> None:
    articles = list(load_articles())
    existing = {a.id for a in articles}
    topics = TOPICS[:limit] if limit else TOPICS

    # Карточка, слишком похожая на уже существующую, вредна дважды: путает
    # пользователя и размывает поиск — оба заголовка тянут на себя один запрос.
    # Сравниваем по Жаккару и только внутри одной категории. Доля от более
    # короткого заголовка не годится: «Принтер не печатает» целиком входит
    # в «Принтер печатает пустые страницы», хотя это разные проблемы.
    existing_titles = [(_title_words(a.title), a.category, a.id, a.title)
                       for a in articles]
    filtered = []
    for t in topics:
        words = _title_words(t["title"])
        clash = None
        for ew, cat, aid, atitle in existing_titles:
            if cat != t["category"] or not ew or not words:
                continue
            if len(ew & words) / len(ew | words) >= 0.7:
                clash = (aid, atitle)
                break
        if clash:
            print(f"  пропуск «{t['title']}» — почти то же, что {clash[0]} «{clash[1]}»")
            continue
        filtered.append(t)
    topics = filtered

    # Заранее раздаём идентификаторы, чтобы модель отвечала по ним
    prepared = []
    taken = set(existing)
    for t in topics:
        folder, prefix = CATEGORY_DIRS[t["category"]]
        key = next_id(prefix, taken)
        taken.add(key)
        prepared.append({**t, "key": key, "folder": folder})

    print(f"Карточек к созданию: {len(prepared)}")
    written, rejected = 0, []

    for start in range(0, len(prepared), BATCH):
        chunk = prepared[start:start + BATCH]
        try:
            answers = ask(chunk)
        except Exception as exc:  # noqa: BLE001
            rejected.append((", ".join(c["key"] for c in chunk), f"модель: {exc}"))
            continue

        for item in chunk:
            got = answers.get(item["key"])
            if not got or not got["steps"]:
                rejected.append((item["key"], "модель не вернула шаги"))
                continue
            problems = validate_steps(got["steps"])
            if problems:
                rejected.append((item["key"], "; ".join(problems[:3])))
                continue

            card = {
                "id": item["key"],
                "category": item["category"],
                "title": item["title"],
                "symptoms": item["symptoms"],
                "required_slots": item.get("slots", []),
                "steps": got["steps"],
                "escalate_if": got["escalate_if"] or
                               "шаги не помогли, проблема сохраняется",
            }
            try:
                write_card(card, ARTICLES_ROOT / item["folder"] / f"{item['key']}.yaml")
                written += 1
            except Exception as exc:  # noqa: BLE001
                rejected.append((item["key"], f"не прошла валидацию: {exc}"))

        print(f"  обработано {min(start + BATCH, len(prepared))} из {len(prepared)}")

    report(written, rejected)


def mode_rewrite(limit: int | None, only: set[str] | None = None) -> None:
    """Переписать шаги существующих карточек под неподготовленного читателя."""
    articles = list(load_articles())
    if only:
        # Точечный прогон: после сетевых обрывов проще добрать отклонённые,
        # чем переписывать заново всю базу и платить за это временем и деньгами.
        articles = [a for a in articles if a.id in only]
        missing = only - {a.id for a in articles}
        if missing:
            print(f"  нет таких карточек: {', '.join(sorted(missing))}")
    elif limit:
        articles = articles[:limit]
    by_id = {a.id: a for a in articles}

    files = {}
    for path in ARTICLES_ROOT.rglob("*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw and raw.get("id") in by_id:
            files[raw["id"]] = (path, raw)

    print(f"Карточек к переписыванию: {len(files)}")
    written, rejected = 0, []
    ids = list(files.keys())

    for start in range(0, len(ids), BATCH):
        chunk_ids = ids[start:start + BATCH]
        chunk = []
        for aid in chunk_ids:
            art = by_id[aid]
            chunk.append({"key": aid, "category": art.category, "title": art.title,
                          "symptoms": art.symptoms + ["ТЕКУЩИЕ ШАГИ (расписать "
                                                      "подробнее, смысл сохранить): "
                                                      + " | ".join(art.steps)],
                          "slots": [{"key": s.key} for s in art.required_slots]})
        try:
            answers = ask(chunk)
        except Exception as exc:  # noqa: BLE001
            rejected.append((", ".join(chunk_ids), f"модель: {exc}"))
            continue

        for aid in chunk_ids:
            got = answers.get(aid)
            if not got or not got["steps"]:
                rejected.append((aid, "модель не вернула шаги"))
                continue
            problems = validate_steps(got["steps"])
            if problems:
                rejected.append((aid, "; ".join(problems[:3])))
                continue

            path, raw = files[aid]
            raw["steps"] = got["steps"]
            if got["escalate_if"]:
                raw["escalate_if"] = got["escalate_if"]
            try:
                write_card(raw, path)
                written += 1
            except Exception as exc:  # noqa: BLE001
                rejected.append((aid, f"не прошла валидацию: {exc}"))

        print(f"  обработано {min(start + BATCH, len(ids))} из {len(ids)}")

    report(written, rejected)


def report(written: int, rejected: list[tuple[str, str]]) -> None:
    print(f"\nЗаписано карточек: {written}")
    if rejected:
        print(f"Отклонено: {len(rejected)}")
        for key, why in rejected:
            print(f"  - {key}: {why}")
        print("\nОтклонённые можно прогнать повторно тем же режимом: "
              "уже записанные карточки пропускаются.")
    print("\nДальше обязательно:")
    print("  1. ВЫЧИТАТЬ написанное — модель убедительна и когда ошибается")
    print("  2. python3 -m tools.build_emb_index    # индекс устарел")
    print("  3. python3 -m tools.warm_queries")
    print("  4. python3 -m tools.check_search")


def main() -> None:
    ap = argparse.ArgumentParser(description="Наполнение базы знаний")
    ap.add_argument("--new", action="store_true", help="создать новые карточки")
    ap.add_argument("--rewrite", action="store_true",
                    help="переписать шаги существующих карточек")
    ap.add_argument("--limit", type=int, default=None, help="сколько штук")
    ap.add_argument("--ids", default=None,
                    help="только эти карточки, через запятую: "
                         "KB-ACCESS-001,KB-EMAIL-003")
    args = ap.parse_args()

    only = {i.strip().upper() for i in args.ids.split(",")} if args.ids else None

    if not (args.new or args.rewrite):
        ap.error("укажите --new или --rewrite")
    if args.rewrite:
        mode_rewrite(args.limit, only)
    if args.new:
        mode_new(args.limit)


if __name__ == "__main__":
    main()
