#!/usr/bin/env bash
# tests/attack_api.sh
# Проверка «защиты от дурака»: нельзя ли сломать приложение через API,
# минуя браузер. Результат сохраняется в docs/SECURITY.md.
#
# Запуск:
#   BASE_URL=http://localhost:8000 ./tests/attack_api.sh
#
# Переменные окружения:
#   BASE_URL   — адрес приложения (по умолчанию http://localhost:8000)
#   OUT        — куда писать отчёт (по умолчанию docs/SECURITY.md)
#   CLOSED_ID  — id заранее закрытого тикета для проверки 8 (по умолчанию T-CLOSED)
#   RATE_CODE  — код при отсутствии токена, 401 или 403 (по умолчанию 401)

set -u

BASE_URL="${BASE_URL:-http://localhost:8000}"
OUT="${OUT:-docs/SECURITY.md}"
CLOSED_ID="${CLOSED_ID:-T-CLOSED}"
RATE_CODE="${RATE_CODE:-401}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0

# ---------- утилиты ----------

# req <метод> <путь> <тело|-> <ожидаемый_код> <описание>
req() {
  local method="$1" path="$2" body="$3" expect="$4" desc="$5"
  local args=(-s -o "$TMP/body" -w "%{http_code}" -X "$method" "$BASE_URL$path")
  if [ "$body" != "-" ]; then
    args+=(-H "Content-Type: application/json" --data "$body")
  fi
  local code
  code="$(curl "${args[@]}")"
  report "$desc" "$expect" "$code"
}

# report <описание> <ожидали> <получили>
report() {
  local desc="$1" expect="$2" got="$3"
  if [ "$got" = "$expect" ]; then
    PASS=$((PASS+1))
    printf '| %s | %s | %s | OK |\n' "$desc" "$expect" "$got"
  else
    FAIL=$((FAIL+1))
    printf '| %s | %s | %s | FAIL |\n' "$desc" "$expect" "$got"
  fi
}

# report_pair <описание> <ожидали> <получили_описание> <ok?>
report_pair() {
  local desc="$1" expect="$2" got_desc="$3" ok="$4"
  if [ "$ok" = "1" ]; then
    PASS=$((PASS+1))
    printf '| %s | %s | %s | OK |\n' "$desc" "$expect" "$got_desc"
  else
    FAIL=$((FAIL+1))
    printf '| %s | %s | %s | FAIL |\n' "$desc" "$expect" "$got_desc"
  fi
}

# ---------- шапка отчёта ----------

mkdir -p "$(dirname "$OUT")" 2>/dev/null || true

{
  echo "# SECURITY.md — проверка защиты API"
  echo
  echo "Прогон: $(date -u +%Y-%m-%dT%H:%M:%SZ)  "
  echo "BASE_URL: \`$BASE_URL\`"
  echo
  echo "| Проверка | Ожидали | Получили | Результат |"
  echo "|---|---|---|---|"
} > "$OUT"

echo "Проверка защиты API: $BASE_URL"
echo

# ---------- 1. несуществующий ticket_id → 404 ----------
req GET "/api/tickets/NOPE-999" - 404 "GET несуществующего обращения"

# ---------- 2. чужой/битый token в /rate → 401 ----------
req POST "/api/tickets/T-1001/rate" \
  '{"token":"deadbeef","rating":5}' \
  "$RATE_CODE" "POST /rate с чужим токеном"

# ---------- 3. лишние поля state, confidence → игнорируются ----------
# Лишние поля не должны менять ответ по сравнению с запросом без них.
# Оба запроса идут с битым токеном, оба должны вернуть RATE_CODE.
CODE_A="$(curl -s -o "$TMP/b3a" -w "%{http_code}" -X POST "$BASE_URL/api/tickets/T-1001/rate" \
  -H 'Content-Type: application/json' \
  --data '{"token":"x","rating":5}')"
CODE_B="$(curl -s -o "$TMP/b3b" -w "%{http_code}" -X POST "$BASE_URL/api/tickets/T-1001/rate" \
  -H 'Content-Type: application/json' \
  --data '{"token":"x","rating":5,"state":"closed","confidence":0.99}')"
if [ "$CODE_A" = "$CODE_B" ]; then
  report_pair "лишние поля state/confidence игнорируются" \
    "тот же код, что и без них" "$CODE_A = $CODE_B" 1
else
  report_pair "лишние поля state/confidence игнорируются" \
    "тот же код, что и без них" "$CODE_A ≠ $CODE_B" 0
fi

# ---------- 4. два одинаковых request_id → одинаковый ответ, без дубля ----------
RID="attack-$(date +%s)-$$"
BODY="{\"request_id\":\"$RID\",\"text\":\"принтер не печатает\"}"
C1="$(curl -s -o "$TMP/r1" -w "%{http_code}" -X POST "$BASE_URL/api/chat" \
      -H 'Content-Type: application/json' --data "$BODY")"
C2="$(curl -s -o "$TMP/r2" -w "%{http_code}" -X POST "$BASE_URL/api/chat" \
      -H 'Content-Type: application/json' --data "$BODY")"

if [ "$C1" = "$C2" ] && cmp -s "$TMP/r1" "$TMP/r2"; then
  report_pair "идемпотентность request_id" \
    "одинаковый ответ на оба запроса" "$C1, тела совпали" 1
else
  report_pair "идемпотентность request_id" \
    "одинаковый ответ на оба запроса" "коды $C1/$C2, тела различаются" 0
fi

# ---------- 5. сообщение 50 000 символов → 422 ----------
LONG="$(python3 -c 'print("x"*50000)')"
req POST "/api/chat" "{\"text\":\"$LONG\"}" 422 "сообщение 50 000 символов"

# ---------- 6. тело не-JSON → 422 ----------
CODE6="$(curl -s -o "$TMP/b6" -w "%{http_code}" -X POST "$BASE_URL/api/chat" \
  -H 'Content-Type: application/json' --data 'not json at all')"
report "тело не-JSON" 422 "$CODE6"

# ---------- 7. 40 запросов подряд → 429 в какой-то момент ----------
GOT429=0
for i in $(seq 1 40); do
  c="$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/api/chat" \
       -H 'Content-Type: application/json' --data '{"text":"ping"}')"
  if [ "$c" = "429" ]; then
    GOT429=1
    break
  fi
done
if [ "$GOT429" = "1" ]; then
  report "rate limit при 40 запросах" 429 429
else
  report "rate limit при 40 запросах" 429 "429 не получен"
fi

# ---------- 8. ответ в закрытом обращении → 409 ----------
req POST "/api/chat/answer" \
  "{\"ticket_id\":\"$CLOSED_ID\",\"text\":\"да\"}" \
  409 "ответ в закрытом обращении"

# ---------- 9. публичная ссылка с несуществующим токеном → 404 ----------
req GET "/s/nonexistent-token-xyz" - 404 "GET /s/<несуществующий токен>"

# ---------- 10. rate/escalate без token → 401 ----------
req POST "/api/tickets/T-1001/rate" \
  '{"rating":5}' \
  "$RATE_CODE" "POST /rate без token"

req POST "/api/tickets/T-1001/escalate" \
  '{"reason":"x"}' \
  "$RATE_CODE" "POST /escalate без token"

# ---------- итог ----------
{
  echo
  echo "**Итого: $PASS OK, $FAIL FAIL.**"
  echo
  echo "### Замечания"
  echo "- Проверка 7 (rate limit) зависит от настроек сервера. Если лимит выше 40 запросов — увеличьте счётчик в цикле."
  echo "- Проверки 3 и 8 требуют существующего тикета/токена в тестовой БД."
  echo "- Проверка 10 — ответ на уточняющий вопрос в уже закрытом обращении. Требует заранее закрытого тикета с id \`$CLOSED_ID\`."
  echo "- Код при отсутствии токена задан переменной \`RATE_CODE\` (сейчас $RATE_CODE)."
} >> "$OUT"

echo
echo "Всего: $PASS OK, $FAIL FAIL."
echo "Отчёт сохранён: $OUT"

[ "$FAIL" -eq 0 ]