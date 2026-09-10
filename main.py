"""
Точка входа приложения «Помоги мне».

Запуск локально:   uvicorn main:app --reload
Запуск в Docker:   docker compose up -d --build
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from api import chat as chat_api      # noqa: E402
from api import tickets as tickets_api  # noqa: E402

app = FastAPI(title="Помоги мне — виртуальная техподдержка", version="0.1.0")

app.include_router(chat_api.router)
app.include_router(tickets_api.router)


@app.get("/health")
def health() -> dict:
    """Проверка живости для мониторинга и для деплоя."""
    return {"status": "ok", "demo_mode": os.getenv("DEMO_MODE", "0") == "1"}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse("static/index.html")
