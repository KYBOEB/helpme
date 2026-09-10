"""
КОНЕЧНЫЙ ФОРМАТ ДИАЛОГА 

Правила:
  * состояние вычисляется ТОЛЬКО здесь, клиент его не присылает;
  * не более MAX_CLARIFYING_QUESTIONS уточняющих вопросов за диалог;
  * нет подходящей статьи → эскалация, а не выдуманные шаги.
"""
from __future__ import annotations

import os

CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.75"))
MAX_CLARIFYING_QUESTIONS = int(os.getenv("MAX_CLARIFYING_QUESTIONS", "2"))