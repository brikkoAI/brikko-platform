#!/usr/bin/env bash
# Brikko Bridge — end-to-end smoke test from Aeza.
#
# Run on the Aeza server (where the bot container lives) to verify the full
# stack is wired together: SSH reverse tunnel up, daemon reachable, claude
# binary present, sessions visible, send/cancel endpoints answer.
#
#   ssh root@aeza
#   bash /opt/brikko/apps/bridge/deploy/smoke_test.sh
#
# This DOES NOT exercise Telegram — that's manual (open the deep-link the
# supervisor printed on the CEO's PC, send a /status message, observe).

set -euo pipefail

DAEMON_URL="${BRIDGE_BOT_DAEMON_URL:-http://127.0.0.1:8090}"
TIMEOUT_SEC=10

green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*"; }
yel()   { printf "\033[33m%s\033[0m\n" "$*"; }

ok=0
fail=0
check() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then
        green "  ✓ $name"
        ok=$((ok + 1))
    else
        red "  ✗ $name"
        fail=$((fail + 1))
    fi
}

echo "=== Brikko Bridge smoke test ==="
echo "Daemon URL: $DAEMON_URL"
echo

# 1. Tunnel reachable
echo "[1/6] SSH reverse tunnel"
check "tcp port 8090 listening on loopback" \
    ss -lnt 'sport = :8090' \| grep -q 127.0.0.1

# 2. Daemon /health
echo "[2/6] Daemon /health"
HEALTH=$(curl -fsS --max-time "$TIMEOUT_SEC" "$DAEMON_URL/health" || echo "")
if echo "$HEALTH" | grep -q '"status":"ok"'; then
    green "  ✓ /health returns ok"
    ok=$((ok + 1))
else
    red "  ✗ /health failed: $HEALTH"
    fail=$((fail + 1))
fi

# 3. claude binary version
if echo "$HEALTH" | grep -q '"claude_version":"unknown"'; then
    yel "  ⚠ claude_version=unknown — check claude binary on PC"
    fail=$((fail + 1))
else
    cv=$(echo "$HEALTH" | grep -oE '"claude_version":"[^"]*"' | head -1)
    green "  ✓ $cv"
    ok=$((ok + 1))
fi

# 4. /sessions
echo "[3/6] Daemon /sessions"
SESSIONS=$(curl -fsS --max-time "$TIMEOUT_SEC" "$DAEMON_URL/sessions" || echo "")
n=$(echo "$SESSIONS" | grep -oE '"session_id"' | wc -l)
if [ "$n" -gt 0 ]; then
    green "  ✓ found $n session(s)"
    ok=$((ok + 1))
else
    yel "  ⚠ no sessions found — fine for fresh PC, otherwise check ~/.claude/projects/"
    fail=$((fail + 1))
fi

# 5. /sessions/active
echo "[4/6] Daemon /sessions/active"
ACTIVE=$(curl -fsS --max-time "$TIMEOUT_SEC" "$DAEMON_URL/sessions/active" || echo "")
if echo "$ACTIVE" | grep -q '"active"'; then
    green "  ✓ /sessions/active responds"
    ok=$((ok + 1))
else
    red "  ✗ /sessions/active failed"
    fail=$((fail + 1))
fi

# 6. Cancel of a fake session is idempotent
echo "[5/6] Daemon /sessions/{fake}/cancel idempotency"
CANCEL=$(curl -fsS --max-time "$TIMEOUT_SEC" -X POST "$DAEMON_URL/sessions/fake-uuid/cancel" || echo "")
if echo "$CANCEL" | grep -q '"ok":true'; then
    green "  ✓ cancel of idle session returns ok=true"
    ok=$((ok + 1))
else
    red "  ✗ cancel returned: $CANCEL"
    fail=$((fail + 1))
fi

# 7. Bot container running
echo "[6/6] Bot container status"
if docker ps --format '{{.Names}}' | grep -q '^brikko-bridge-bot$'; then
    green "  ✓ container brikko-bridge-bot is running"
    ok=$((ok + 1))
    # Tail recent log
    echo
    yel "Recent bot logs (last 20 lines):"
    docker logs --tail 20 brikko-bridge-bot 2>&1 | sed 's/^/    /'
else
    red "  ✗ container brikko-bridge-bot not running"
    fail=$((fail + 1))
fi

echo
echo "=== Result: $ok passed, $fail failed ==="
exit $((fail > 0 ? 1 : 0))
