# Помоги мне — виртуальная техническая поддержка

Решение кейса 2 хакатона re:action stack (ТПУ × Актион).

## Запуск локально

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload
```

Открыть http://127.0.0.1:8000 — страница, http://127.0.0.1:8000/health — проверка,
http://127.0.0.1:8000/docs — автодокументация API.

## Запуск в Docker

```bash
docker compose up -d --build
```

## TODO
- [ ] README: описание, схема архитектуры, скриншоты, список возможностей
