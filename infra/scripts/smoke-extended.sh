#!/usr/bin/env bash
# Voltari extended smoke tests — 20 cases covering Stage-4 features.
# Coverage:
#   ST-01..ST-10  — base smoke (см. smoke.sh)
#   ST-11..ST-15  — каждый из 5 новых провайдеров (Anthropic, Google, DeepSeek, Yandex, Sber)
#   ST-16         — smart router 4 strategies (auto:cheap/smart/fast/ru-legal)
#   ST-17         — failover при 5xx primary
#   ST-18         — ЮKassa webhook (signature + idempotency)
#   ST-19         — SDK Python из локальной wheel
#   ST-20         — SDK JS из локального tarball
#
# Usage:
#   bash infra/scripts/smoke-extended.sh                # все 20
#   bash infra/scripts/smoke-extended.sh --quick        # только ST-01..ST-10
#   bash infra/scripts/smoke-extended.sh --providers-only   # только ST-11..ST-15
#   bash infra/scripts/smoke-extended.sh --skip 17,18   # пропустить ST-17 и ST-18
#
# Exit code: 0 — все запланированные PASS; N — номер первого упавшего теста (1..20).
# Бюджет времени: <120 сек на полный прогон.
set -uo pipefail

MODE="full"
SKIP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quick) MODE="quick"; shift ;;
    --providers-only) MODE="providers"; shift ;;
    --skip) SKIP="$2"; shift 2 ;;
    --verbose) VERBOSE=1; shift ;;
    *) echo "unknown arg: $1"; exit 99 ;;
  esac
done
VERBOSE="${VERBOSE:-0}"

# --- Required env ---
: "${BASE_URL:?BASE_URL required}"
: "${SMOKE_API_KEY:?SMOKE_API_KEY required}"
: "${SMOKE_ADMIN_TOKEN:?SMOKE_ADMIN_TOKEN required}"
: "${SMOKE_ACCOUNT_ID:?SMOKE_ACCOUNT_ID required}"
: "${SMOKE_DASHBOARD_URL:?SMOKE_DASHBOARD_URL required}"
: "${SMOKE_DB_DSN:?SMOKE_DB_DSN required}"
# Optional (нужны только для определённых тестов):
YOOKASSA_WEBHOOK_SECRET="${YOOKASSA_WEBHOOK_SECRET:-}"
VOLTARI_REPO_ROOT="${VOLTARI_REPO_ROOT:-$(pwd)}"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
START_TS=$(date +%s)
RESULTS=()
FIRST_FAIL=0

is_skipped() {
  local n="$1"
  [[ ",$SKIP," == *",$n,"* ]]
}

run_test() {
  local num="$1"; local name="$2"; local cmd="$3"
  if is_skipped "$num"; then
    echo -e "${YELLOW}-${NC} ST-$(printf "%02d" $num) $name (skipped)"
    return 0
  fi
  local t0=$(date +%s%N)
  local out
  out=$(eval "$cmd" 2>&1)
  local rc=$?
  local t1=$(date +%s%N)
  local ms=$(( (t1 - t0) / 1000000 ))

  if [[ $rc -eq 0 ]]; then
    echo -e "${GREEN}OK${NC} ST-$(printf "%02d" $num) $name (${ms}ms)"
    RESULTS+=("PASS|$num|$name|$ms")
  else
    echo -e "${RED}FAIL ST-$(printf "%02d" $num) $name (${ms}ms)${NC}"
    echo "$out" | sed 's/^/    /'
    RESULTS+=("FAIL|$num|$name|$ms")
    [[ $FIRST_FAIL -eq 0 ]] && FIRST_FAIL=$num
  fi
  [[ $VERBOSE -eq 1 ]] && echo "$out" | sed 's/^/    /'
}

echo "=== Voltari Extended Smoke (mode=$MODE) ==="
echo "BASE_URL=$BASE_URL  started=$(date -Iseconds)"
echo ""

# ---- Base smoke (ST-01..ST-10) — делегируем в smoke.sh при наличии ----
if [[ "$MODE" == "quick" || "$MODE" == "full" ]]; then
  if [[ -x "$(dirname "$0")/smoke.sh" ]]; then
    echo "--- Base smoke (delegating to smoke.sh) ---"
    bash "$(dirname "$0")/smoke.sh" || FIRST_FAIL=$?
    if [[ $FIRST_FAIL -ne 0 ]]; then
      echo -e "${RED}Base smoke failed at ST-$FIRST_FAIL — aborting extended.${NC}"
      exit $FIRST_FAIL
    fi
  else
    echo -e "${YELLOW}smoke.sh not found — skipping ST-01..ST-10${NC}"
  fi
fi

[[ "$MODE" == "quick" ]] && { echo "Quick mode done."; exit 0; }

# ---- ST-11..ST-15: новые провайдеры ----
if [[ "$MODE" == "full" || "$MODE" == "providers" ]]; then

run_test 11 "anthropic claude-haiku-4.5" '
  RESP=$(curl -sS -m 30 -w "\n%{http_code}" \
    -H "Authorization: Bearer $SMOKE_API_KEY" \
    -H "Content-Type: application/json" \
    -X POST "$BASE_URL/v1/chat/completions" \
    -d "{\"model\":\"claude-haiku-4.5\",\"messages\":[{\"role\":\"user\",\"content\":\"say ok\"}],\"max_tokens\":5}")
  CODE=$(echo "$RESP" | tail -1)
  BODY=$(echo "$RESP" | head -n -1)
  [[ "$CODE" == "200" ]] || { echo "code=$CODE body=$BODY"; exit 1; }
  echo "$BODY" | jq -e ".choices[0].message.content | length > 0" >/dev/null \
    || { echo "empty content: $BODY"; exit 1; }
'

run_test 12 "google gemini-3-flash" '
  RESP=$(curl -sS -m 30 -w "\n%{http_code}" \
    -H "Authorization: Bearer $SMOKE_API_KEY" \
    -X POST "$BASE_URL/v1/chat/completions" \
    -d "{\"model\":\"gemini-3-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"max_tokens\":5}")
  CODE=$(echo "$RESP" | tail -1)
  [[ "$CODE" == "200" ]] || { echo "code=$CODE"; exit 1; }
'

run_test 13 "deepseek-v3.2-chat" '
  RESP=$(curl -sS -m 30 -w "\n%{http_code}" \
    -H "Authorization: Bearer $SMOKE_API_KEY" \
    -X POST "$BASE_URL/v1/chat/completions" \
    -d "{\"model\":\"deepseek-v3.2-chat\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"max_tokens\":5}")
  CODE=$(echo "$RESP" | tail -1)
  [[ "$CODE" == "200" ]] || { echo "code=$CODE"; exit 1; }
'

run_test 14 "yandex yandexgpt-5-lite (ru-legal)" '
  RESP=$(curl -sS -m 30 -w "\n%{http_code}" \
    -H "Authorization: Bearer $SMOKE_API_KEY" \
    -X POST "$BASE_URL/v1/chat/completions" \
    -d "{\"model\":\"yandexgpt-5-lite\",\"messages\":[{\"role\":\"user\",\"content\":\"ок\"}],\"max_tokens\":5}")
  CODE=$(echo "$RESP" | tail -1)
  BODY=$(echo "$RESP" | head -n -1)
  if [[ "$CODE" == "401" ]]; then
    echo "Yandex IAM token expired — runbook: 06_Operations/04_runbook.md §IAM-rotation"
    exit 1
  fi
  [[ "$CODE" == "200" ]] || { echo "code=$CODE body=$BODY"; exit 1; }
'

run_test 15 "sber gigachat-2-lite (ru-legal)" '
  RESP=$(curl -sS -m 30 -w "\n%{http_code}" \
    -H "Authorization: Bearer $SMOKE_API_KEY" \
    -X POST "$BASE_URL/v1/chat/completions" \
    -d "{\"model\":\"gigachat-2-lite\",\"messages\":[{\"role\":\"user\",\"content\":\"ок\"}],\"max_tokens\":5}")
  CODE=$(echo "$RESP" | tail -1)
  [[ "$CODE" == "200" ]] || { echo "code=$CODE"; exit 1; }
'

fi  # providers section

[[ "$MODE" == "providers" ]] && { echo "Providers mode done."; exit ${FIRST_FAIL:-0}; }

# ---- ST-16: smart router 4 strategies ----
run_test 16 "router 4 strategies" '
  declare -A EXPECT=(
    [cheap]="deepseek\|gemini\|gigachat"
    [smart]="claude\|gpt\|gemini"
    [fast]="haiku\|gemini-3-flash\|gigachat-2-lite\|yandexgpt-5-lite"
    [ru-legal]="yandexgpt\|gigachat"
  )
  for STRATEGY in cheap smart fast ru-legal; do
    TMP=$(mktemp)
    curl -sS -m 30 -D "$TMP" -o /dev/null \
      -H "Authorization: Bearer $SMOKE_API_KEY" \
      -X POST "$BASE_URL/v1/chat/completions" \
      -d "{\"model\":\"auto:$STRATEGY\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"max_tokens\":3}"
    DECISION=$(grep -i "^x-router-decision:" "$TMP" | cut -d" " -f2- | tr -d "\r\n")
    rm "$TMP"
    [[ -n "$DECISION" ]] || { echo "no header for $STRATEGY"; exit 1; }
    echo "$DECISION" | grep -qE "${EXPECT[$STRATEGY]}" \
      || { echo "$STRATEGY: got \"$DECISION\", expected /${EXPECT[$STRATEGY]}/"; exit 1; }
  done
'

# ---- ST-17: failover ----
run_test 17 "failover on 5xx primary" '
  curl -sS -m 5 -X POST "$BASE_URL/admin/debug/force-error" \
    -H "Authorization: Bearer $SMOKE_ADMIN_TOKEN" \
    -d "{\"provider\":\"openai\",\"status\":503,\"ttl_seconds\":30}" >/dev/null

  TMP=$(mktemp)
  CODE=$(curl -sS -m 30 -D "$TMP" -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $SMOKE_API_KEY" \
    -X POST "$BASE_URL/v1/chat/completions" \
    -d "{\"model\":\"gpt-5.4-mini\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"max_tokens\":5}")
  FAILOVER=$(grep -i "^x-failover-used:" "$TMP" | cut -d" " -f2- | tr -d "\r\n")
  DECISION=$(grep -i "^x-router-decision:" "$TMP" | cut -d" " -f2- | tr -d "\r\n")
  rm "$TMP"

  curl -sS -m 5 -X DELETE "$BASE_URL/admin/debug/force-error?provider=openai" \
    -H "Authorization: Bearer $SMOKE_ADMIN_TOKEN" >/dev/null

  [[ "$CODE" == "200" ]] || { echo "code=$CODE — failover not engaged"; exit 1; }
  [[ "$FAILOVER" == "true" ]] || { echo "X-Failover-Used=$FAILOVER"; exit 1; }
  echo "$DECISION" | grep -qv "openai" || { echo "still on openai: $DECISION"; exit 1; }
'

# ---- ST-18: ЮKassa webhook ----
if [[ -z "$YOOKASSA_WEBHOOK_SECRET" ]]; then
  echo -e "${YELLOW}-${NC} ST-18 yookassa webhook (skipped: YOOKASSA_WEBHOOK_SECRET not set)"
else
  run_test 18 "yookassa webhook signature + idempotency" '
    PAYMENT_ID="smoke-$(date +%s)-$$"
    PAYLOAD="{\"type\":\"notification\",\"event\":\"payment.succeeded\",\"object\":{\"id\":\"$PAYMENT_ID\",\"status\":\"succeeded\",\"amount\":{\"value\":\"1.00\",\"currency\":\"RUB\"},\"metadata\":{\"account_id\":\"$SMOKE_ACCOUNT_ID\",\"kind\":\"smoke\"},\"paid\":true}}"
    SIG=$(printf "%s" "$PAYLOAD" | openssl dgst -sha256 -hmac "$YOOKASSA_WEBHOOK_SECRET" -hex | awk "{print \$NF}")

    BAL_BEFORE=$(psql "$SMOKE_DB_DSN" -tAc "SELECT balance_kopecks FROM accounts WHERE id=$SMOKE_ACCOUNT_ID")

    CODE1=$(curl -sS -m 5 -o /dev/null -w "%{http_code}" \
      -H "Content-Type: application/json" \
      -H "X-Yookassa-Signature: $SIG" \
      -X POST "$BASE_URL/v1/billing/yookassa/webhook" -d "$PAYLOAD")

    CODE2=$(curl -sS -m 5 -o /dev/null -w "%{http_code}" \
      -H "Content-Type: application/json" \
      -H "X-Yookassa-Signature: $SIG" \
      -X POST "$BASE_URL/v1/billing/yookassa/webhook" -d "$PAYLOAD")

    sleep 2
    BAL_AFTER=$(psql "$SMOKE_DB_DSN" -tAc "SELECT balance_kopecks FROM accounts WHERE id=$SMOKE_ACCOUNT_ID")
    DELTA=$((BAL_AFTER - BAL_BEFORE))

    # Откат — списать 100 коп обратно
    psql "$SMOKE_DB_DSN" -c "UPDATE accounts SET balance_kopecks = balance_kopecks - 100 WHERE id=$SMOKE_ACCOUNT_ID" >/dev/null

    [[ "$CODE1" == "200" ]] || { echo "1st webhook code=$CODE1"; exit 1; }
    [[ "$CODE2" == "200" ]] || { echo "2nd webhook code=$CODE2 (must be 200 idempotently)"; exit 1; }
    [[ "$DELTA" == "100" ]] || { echo "idempotency broken, delta=$DELTA коп"; exit 1; }
  '
fi

# ---- ST-19: SDK Python ----
run_test 19 "SDK Python wheel install + first call" '
  TMPVENV=$(mktemp -d)
  python3 -m venv "$TMPVENV" >/dev/null
  source "$TMPVENV/bin/activate"

  WHEEL=$(ls -t "$VOLTARI_REPO_ROOT"/sdk/python/dist/voltari_sdk-*.whl 2>/dev/null | head -1)
  [[ -n "$WHEEL" ]] || { echo "no wheel in sdk/python/dist/"; deactivate; rm -rf "$TMPVENV"; exit 1; }

  pip install --quiet "$WHEEL" || { echo "pip install failed"; deactivate; rm -rf "$TMPVENV"; exit 1; }

  python3 -c "
from voltari import Voltari
import os
client = Voltari(api_key=os.environ[\"SMOKE_API_KEY\"], base_url=os.environ[\"BASE_URL\"])
r = client.chat.completions.create(model=\"auto:cheap\", messages=[{\"role\":\"user\",\"content\":\"ok\"}], max_tokens=5)
assert r.choices[0].message.content
print(\"OK\", r.model)
" || { echo "python SDK call failed"; deactivate; rm -rf "$TMPVENV"; exit 1; }

  deactivate
  rm -rf "$TMPVENV"
'

# ---- ST-20: SDK JS ----
run_test 20 "SDK JS tarball install + first call" '
  TMPDIR=$(mktemp -d)
  pushd "$TMPDIR" >/dev/null

  cat > package.json <<EOF
{"name":"voltari-smoke","version":"1.0.0","type":"module"}
EOF

  TARBALL=$(ls -t "$VOLTARI_REPO_ROOT"/sdk/js/voltari-sdk-*.tgz 2>/dev/null | head -1)
  [[ -n "$TARBALL" ]] || { echo "no tarball"; popd >/dev/null; rm -rf "$TMPDIR"; exit 1; }

  npm install --silent "$TARBALL" >/dev/null || { echo "npm install failed"; popd >/dev/null; rm -rf "$TMPDIR"; exit 1; }

  cat > smoke.mjs <<EOF
import { Voltari } from "voltari-sdk";
const c = new Voltari({ apiKey: process.env.SMOKE_API_KEY, baseURL: process.env.BASE_URL });
const r = await c.chat.completions.create({ model:"auto:cheap", messages:[{role:"user",content:"ok"}], max_tokens:5 });
if (!r.choices[0].message.content) { console.error("empty"); process.exit(1); }
console.log("OK", r.model);
EOF

  node smoke.mjs || { echo "node SDK call failed"; popd >/dev/null; rm -rf "$TMPDIR"; exit 1; }

  popd >/dev/null
  rm -rf "$TMPDIR"
'

# ---- Summary ----
END_TS=$(date +%s)
TOTAL=$((END_TS - START_TS))
PASSED=$(printf "%s\n" "${RESULTS[@]}" | grep -c "^PASS" || true)
FAILED=$(printf "%s\n" "${RESULTS[@]}" | grep -c "^FAIL" || true)

echo ""
echo "=== Summary ==="
echo "Passed: $PASSED"
echo "Failed: $FAILED"
echo "Time:   ${TOTAL}s"

if [[ $FIRST_FAIL -gt 0 ]]; then
  echo -e "${RED}EXTENDED SMOKE FAILED — first failure: ST-$(printf "%02d" $FIRST_FAIL)${NC}"
  exit $FIRST_FAIL
fi
echo -e "${GREEN}ALL EXTENDED SMOKE TESTS PASSED${NC}"
exit 0
