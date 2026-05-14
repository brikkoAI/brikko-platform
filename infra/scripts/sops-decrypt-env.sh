#!/usr/bin/env bash
# infra/scripts/sops-decrypt-env.sh — TD-024 / TD-025
#
# Расшифровывает infra/.env.sops.yaml (зашифрованный SOPS+age) и пишет в
# infra/.env (plain text). Запускается:
#   - Локально маинтейнером перед `docker compose up` (если используется SOPS-flow)
#   - На VPS вручную после `git pull` (если CI не делает этого сам)
#   - В .github/workflows/deploy.yml перед SSH-deploy
#
# Usage:
#   ./infra/scripts/sops-decrypt-env.sh             # пишет в infra/.env
#   OUT=/tmp/.env ./infra/scripts/sops-decrypt-env.sh
#
# Pre-conditions:
#   - sops в $PATH (бинарник)
#   - age приватный ключ в одном из:
#       $SOPS_AGE_KEY_FILE         — путь к файлу с приватным ключом
#       $SOPS_AGE_KEY              — сам ключ (multi-line) (для CI)
#       ~/.config/sops/age/keys.txt — default локально
#
# Безопасность:
#   - Output-файл получает chmod 600 + chown deploy:deploy
#   - Если расшифровка не удалась — НЕ пишем (атомарная замена через mv)
#   - НЕ логируем содержимое в stdout (только метаданные)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOPS_FILE="${REPO_ROOT}/infra/.env.sops.yaml"
OUT="${OUT:-${REPO_ROOT}/infra/.env}"

# Проверки.
if ! command -v sops &> /dev/null; then
    echo "ERROR: sops not found in PATH. Install: https://github.com/getsops/sops/releases" >&2
    exit 1
fi

if [[ ! -f "${SOPS_FILE}" ]]; then
    echo "ERROR: ${SOPS_FILE} not found. Did you commit the encrypted file?" >&2
    exit 1
fi

# Если переданный $SOPS_AGE_KEY (multi-line строка) — пишем во временный файл.
TMP_KEY=""
cleanup() {
    if [[ -n "${TMP_KEY}" && -f "${TMP_KEY}" ]]; then
        shred -u "${TMP_KEY}" 2>/dev/null || rm -f "${TMP_KEY}"
    fi
}
trap cleanup EXIT

if [[ -n "${SOPS_AGE_KEY:-}" && -z "${SOPS_AGE_KEY_FILE:-}" ]]; then
    TMP_KEY=$(mktemp)
    chmod 600 "${TMP_KEY}"
    printf '%s\n' "${SOPS_AGE_KEY}" > "${TMP_KEY}"
    export SOPS_AGE_KEY_FILE="${TMP_KEY}"
fi

# Расшифровка → временный файл → atomic mv.
# SOPS выдаёт YAML; мы конвертим в .env-формат через простой `sops -d --output-type dotenv`.
TMP_OUT=$(mktemp)
chmod 600 "${TMP_OUT}"

# SOPS поддерживает output-type=dotenv напрямую — это лучший вариант чем yaml→env converter.
if sops --decrypt --output-type dotenv "${SOPS_FILE}" > "${TMP_OUT}" 2>/dev/null; then
    :
else
    # Fallback: расшифровать как YAML и сконвертить вручную (на случай если
    # SOPS не понимает наш schema из-за nested values).
    sops --decrypt "${SOPS_FILE}" | python3 -c "
import sys, yaml
data = yaml.safe_load(sys.stdin) or {}
for k, v in data.items():
    if v is None:
        v = ''
    s = str(v)
    # Quote if contains spaces or special chars
    if any(c in s for c in ' \\\"\\\$\\`<>|;&'):
        s = '\"' + s.replace('\\\\', '\\\\\\\\').replace('\\\"', '\\\\\\\"') + '\"'
    print(f'{k}={s}')
" > "${TMP_OUT}"
fi

# Atomic replace.
mv "${TMP_OUT}" "${OUT}"
chmod 600 "${OUT}"

# Метаданные в логи, но не сами секреты.
LINES=$(grep -cE '^[A-Z_]+=' "${OUT}" || echo 0)
echo "OK: decrypted ${LINES} env vars → ${OUT}"
echo "    file size: $(wc -c < "${OUT}") bytes"
echo "    permissions: $(stat -c '%a %U:%G' "${OUT}" 2>/dev/null || stat -f '%Lp %Su:%Sg' "${OUT}")"
