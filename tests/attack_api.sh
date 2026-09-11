#!/usr/bin/env bash
# ==========================================================================
# Проверка «защиты от дурака»: приложение нельзя сломать запросами в обход
# браузера. Требование организаторов, названное отдельным пунктом.
#
# Запуск:  bash tests/attack_api.sh https://helpme-tpu.duckdns.org
# Отчёт:   docs/SECURITY.md
# ==========================================================================
set -u
BASE="${1:-http://127.0.0.1:8000}"
REPORT="docs/SECURITY.md"
PASS=0; FAIL=0

rows=""

check() {           # check "описание" "ожидали" "получили"
  local name="$1" want="$2" got="$3" mark
  if [ "$want" = "$got" ]; then mark="OK"; PASS=$((PASS+1)); else mark="FAIL"; FAIL=$((FAIL+1)); fi
  printf "%-56s %-10s %-10s %s\n" "$name" "$want" "$got" "$mark"
  rows="${rows}| ${name} | ${want} | ${got} | ${mark} |\n"
}

code() { curl -s -o /dev/null -w "%{http_code}" "$@"; }
json() { curl -s -X POST "$BASE/api/chat" -H "Content-Type: application/json" -d "$1"; }
jcode() { curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/chat" -H "Content-Type: application/json" -d "$1"; }

echo "Проверка $BASE"
printf "%-56s %-10s %-10s %s\n" "ПРОВЕРКА" "ОЖИДАЛИ" "ПОЛУЧИЛИ" "ИТОГ"
echo "----------------------------------------------------------------------------------------"

# Живое обращение для проверок с чужим доступом
LIVE=$(json '{"request_id":"atk-live","message":"не работает vpn"}')
TID=$(printf '%s' "$LIVE" | grep -o '"ticket_id":"[^"]*"' | head -1 | cut -d'"' -f4)

check "1. Несуществующий номер обращения"        404 "$(jcode '{"request_id":"a1","ticket_id":"t_000000","token":"x","message":"привет"}')"
check "2. Чужой токен к реальному обращению"     404 "$(jcode "{\"request_id\":\"a2\",\"ticket_id\":\"$TID\",\"token\":\"s_подделка\",\"message\":\"привет\"}")"
check "3. Тело не JSON"                          422 "$(jcode 'это-не-json')"
check "4. Сообщение на 50 000 символов"          422 "$(jcode "{\"request_id\":\"a4\",\"message\":\"$(head -c 50000 /dev/zero | tr '\0' 'a')\"}")"
check "5. Отсутствует обязательный request_id"   422 "$(jcode '{"message":"привет"}')"
check "6. Панель оператора без входа"            401 "$(code "$BASE/api/tickets")"
check "7. Метрики панели без входа"              401 "$(code "$BASE/api/stats")"
check "8. Добавление статьи без входа"           401 "$(code -X POST "$BASE/api/kb/articles" -H 'Content-Type: application/json' -d '{"category":"VPN","title":"x","symptoms":["a"],"steps":["b"]}')"
check "9. Публичная ссылка с чужим токеном"      404 "$(code "$BASE/s/несуществующий-токен")"

# 10. Клиент пытается продиктовать состояние и уверенность
FORCED=$(json '{"request_id":"a10","message":"не работает vpn","state":"RESOLVED","confidence":1.0}')
FSTATE=$(printf '%s' "$FORCED" | grep -o '"state":"[^"]*"' | head -1 | cut -d'"' -f4)
if [ "$FSTATE" = "RESOLVED" ]; then GOT="принято"; else GOT="отброшено"; fi
check "10. Клиент диктует state и confidence"    "отброшено" "$GOT"

# 11. Идемпотентность: тот же request_id не создаёт второе обращение
R1=$(json '{"request_id":"atk-idem","message":"не работает vpn"}')
R2=$(json '{"request_id":"atk-idem","message":"не работает vpn"}')
T1=$(printf '%s' "$R1" | grep -o '"ticket_id":"[^"]*"' | head -1 | cut -d'"' -f4)
T2=$(printf '%s' "$R2" | grep -o '"ticket_id":"[^"]*"' | head -1 | cut -d'"' -f4)
if [ "$T1" = "$T2" ]; then GOT="дубля нет"; else GOT="создан дубль"; fi
check "11. Повторный request_id (двойной клик)"  "дубля нет" "$GOT"

# 12. Ограничение частоты
LIMITED="нет"
for i in $(seq 1 40); do
  RC=$(jcode "{\"request_id\":\"flood-$i\",\"message\":\"не работает vpn\"}")
  [ "$RC" = "429" ] && { LIMITED="да"; break; }
done
check "12. Поток из 40 запросов подряд"          "да" "$LIMITED"

# 13-15. Ответ специалиста и чтение переписки — только для своих
check "13. Ответ специалиста без входа в панель" 401 "$(code -X POST "$BASE/api/tickets/$TID/reply" -H 'Content-Type: application/json' -d '{"text":"ответ от чужого"}')"
check "14. Чтение переписки с чужим токеном"     404 "$(code -X POST "$BASE/api/chat/updates" -H 'Content-Type: application/json' -d "{\"ticket_id\":\"$TID\",\"token\":\"s_подделка\",\"after\":0}")"
check "15. Чтение переписки вообще без токена"   404 "$(code -X POST "$BASE/api/chat/updates" -H 'Content-Type: application/json' -d "{\"ticket_id\":\"$TID\",\"after\":0}")"

# ----------------------------------------------------------------------------
# 16-19. История обращений (пакет 8)
# ----------------------------------------------------------------------------

# 16. Выдуманный идентификатор посетителя: 200 и пустой список, а НЕ 404.
#     404 здесь превратил бы эндпоинт в оракул — перебором можно было бы
#     отличать живые идентификаторы от выдуманных.
FAKE=$(curl -s -X POST "$BASE/api/my/tickets" -H 'Content-Type: application/json' \
       -d '{"client_id":"c_такого_посетителя_нет_совсем_точно_нет"}')
if printf '%s' "$FAKE" | grep -q '"tickets":\[\]'; then GOT="пустой список"; else GOT="что-то отдал"; fi
check "16. История по чужому идентификатору"     "пустой список" "$GOT"

# 17. Мусор вместо идентификатора не должен ронять сервер
check "17. История по мусорному идентификатору"  200 "$(code -X POST "$BASE/api/my/tickets" -H 'Content-Type: application/json' -d '{"client_id":12345}')"

# 18. Повторное обращение по чужому обращению: знания номера мало, нужен токен
check "18. «Проблема вернулась» с чужим токеном" 404 "$(jcode "{\"request_id\":\"a18\",\"message\":\"снова не работает vpn\",\"parent_ticket_id\":\"$TID\",\"parent_token\":\"s_подделка\"}")"

# 19. Закрытое обращение по-прежнему не принимает сообщений.
#     Сторожевая проверка: пакет 8 НЕ ослабил assert_can_accept, и закрытое
#     обращение не переоткрывается — «проблема вернулась» создаёт новое.
CL=$(json '{"request_id":"atk-close","message":"не работает vpn"}')
CTID=$(printf '%s' "$CL" | grep -o '"ticket_id":"[^"]*"' | head -1 | cut -d'"' -f4)
CTOK=$(printf '%s' "$CL" | grep -o '"token":"[^"]*"' | head -1 | cut -d'"' -f4)
curl -s -o /dev/null -X POST "$BASE/api/tickets/$CTID/close" -H 'Content-Type: application/json' -d "{\"token\":\"$CTOK\"}"
check "19. Дописать в закрытое обращение"        409 "$(jcode "{\"request_id\":\"a19\",\"ticket_id\":\"$CTID\",\"token\":\"$CTOK\",\"message\":\"ещё раз\"}")"

echo "----------------------------------------------------------------------------------------"
echo "ИТОГО: пройдено $PASS, провалено $FAIL"

mkdir -p docs
{
  echo "# Проверка защиты API"
  echo
  echo "Организаторы отдельным требованием назвали «защиту от дурака»: приложение"
  echo "нельзя сломать запросами в обход браузера. Ниже — результат автоматической"
  echo "проверки, воспроизводится командой:"
  echo
  echo '```bash'
  echo "bash tests/attack_api.sh $BASE"
  echo '```'
  echo
  echo "Дата прогона: $(date '+%Y-%m-%d %H:%M')"
  echo
  echo "| Проверка | Ожидали | Получили | Итог |"
  echo "|---|---|---|---|"
  printf "$rows"
  echo
  echo "**Итого: пройдено $PASS, провалено $FAIL.**"
  echo
  echo "## Что за этим стоит"
  echo
  echo "Базовый принцип — клиент ничего не решает. Состояние обращения вычисляется"
  echo "только сервером по истории диалога; полей \`state\` и \`confidence\` нет в схеме"
  echo "запроса, поэтому продиктовать их снаружи невозможно. Доступ к чужому обращению"
  echo "даёт 404, а не 403: снаружи нельзя отличить «нет доступа» от «нет такого"
  echo "обращения», и перебором чужие номера не нащупать."
  echo
  echo "Повторный \`request_id\` возвращает ранее выданный ответ, поэтому двойной клик"
  echo "и сетевой ретрай не создают дублей. Любая необработанная ошибка превращается"
  echo "в 500 с кодом для поддержки — трейсбек наружу не уходит."
} > "$REPORT"

echo "Отчёт записан в $REPORT"
[ "$FAIL" -eq 0 ]
