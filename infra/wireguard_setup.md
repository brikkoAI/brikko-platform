# WireGuard tunnel: Selectel ↔ Hetzner

**Назначение:** шифрованный туннель для трафика от РФ-сервера к зарубежному proxy. Все исходящие запросы к OpenAI/Anthropic/Google идут через эту трубу.

**Сеть:** `10.10.0.0/24` (RFC 1918, никому не маршрутизируется в интернет).

---

## 1. Топология

```
       Hetzner CX22 (Helsinki)             Selectel CS S2 (Москва)
       Public: <HEL_PUBLIC_IP>             Public: <RU_PUBLIC_IP>
       WG IP:  10.10.0.1                   WG IP:  10.10.0.2
       Listen: 51820/UDP                   no listen (client-mode)
            ▲                                   │
            └───── encrypted UDP ───────────────┘
                  ChaCha20-Poly1305
                  Curve25519 keys
                  PersistentKeepalive=25s
```

| Хост | Роль | Tunnel IP | Listen | Конфиг |
|---|---|---|---|---|
| Hetzner | Server | 10.10.0.1 | 51820/UDP | `/etc/wireguard/wg0.conf` |
| Selectel | Client | 10.10.0.2 | — | `/etc/wireguard/wg0.conf` |

> **Почему Hetzner = server, Selectel = client:** Hetzner имеет статичный, неблокируемый IP в Финляндии. Selectel может быть за NAT при некоторых конфигах. PersistentKeepalive на client'е держит сессию через NAT/firewall.

---

## 2. Генерация ключей

**На Hetzner (как root или через sudo):**
```bash
sudo -i
cd /etc/wireguard
umask 077

wg genkey | tee server-private.key | wg pubkey > server-public.key
wg genkey | tee client-private.key | wg pubkey > client-public.key
# Ради безопасности можно сгенерить все 4 ключа здесь, потом перенести client'ам.
# Но лучше — каждый peer генерит свои ключи у себя:

# Альтернативный flow (рекомендуется):
# 1. Hetzner генерит свой private + public, отдаёт public Selectel'ю
# 2. Selectel генерит свой private + public, отдаёт public Hetzner'у
# 3. Private никогда не покидает свой хост.
```

**Вариант с раздельной генерацией (правильно):**

На Hetzner:
```bash
sudo -i
cd /etc/wireguard && umask 077
wg genkey | tee /etc/wireguard/privatekey | wg pubkey > /etc/wireguard/publickey
chmod 600 privatekey
echo "HETZNER PUBLIC KEY:"
cat publickey
# Запиши вывод — отдашь Selectel-серверу.
```

На Selectel (через SSH):
```bash
sudo -i
apt install -y wireguard
cd /etc/wireguard && umask 077
wg genkey | tee /etc/wireguard/privatekey | wg pubkey > /etc/wireguard/publickey
chmod 600 privatekey
echo "SELECTEL PUBLIC KEY:"
cat publickey
# Запиши вывод — отдашь Hetzner-серверу.
```

> **Никогда не передавай privatekey** в чате/email/git. Только publickey.

---

## 3. Конфиг на Hetzner (`/etc/wireguard/wg0.conf`)

```ini
[Interface]
# WG IP сервера в туннеле
Address = 10.10.0.1/24
ListenPort = 51820
PrivateKey = <HETZNER_PRIVATE_KEY>   # cat /etc/wireguard/privatekey
# Включаем IP forwarding и NAT для исходящего из туннеля в интернет
# (нужно когда Caddy на Hetzner делает outbound к OpenAI)
PostUp = sysctl -w net.ipv4.ip_forward=1
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT
PostUp = iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
SaveConfig = false

# Peer = Selectel client
[Peer]
PublicKey = <SELECTEL_PUBLIC_KEY>
AllowedIPs = 10.10.0.2/32
# Нет endpoint — мы server, ждём подключений
PersistentKeepalive = 25
```

**Поднять интерфейс:**
```bash
sudo systemctl enable --now wg-quick@wg0
sudo systemctl status wg-quick@wg0
sudo wg show
# Должно показать peer Selectel
sudo ip addr show wg0
# 10.10.0.1/24 на интерфейсе wg0
```

**Включить IP-forwarding надолго:**
```bash
sudo bash -c 'echo "net.ipv4.ip_forward=1" >> /etc/sysctl.d/99-wireguard.conf'
sudo sysctl -p /etc/sysctl.d/99-wireguard.conf
```

---

## 4. Конфиг на Selectel (`/etc/wireguard/wg0.conf`)

```ini
[Interface]
Address = 10.10.0.2/24
PrivateKey = <SELECTEL_PRIVATE_KEY>   # cat /etc/wireguard/privatekey
# НЕТ ListenPort — мы client
SaveConfig = false

[Peer]
PublicKey = <HETZNER_PUBLIC_KEY>
Endpoint = <HEL_PUBLIC_IP>:51820
# Маршрутим только подсеть 10.10.0.0/24 в туннель (НЕ default route)
# Это критично! Иначе ВЕСЬ исходящий трафик Selectel пойдёт через Hetzner = baaad.
AllowedIPs = 10.10.0.0/24
PersistentKeepalive = 25
```

**Поднять:**
```bash
sudo apt install -y wireguard
sudo systemctl enable --now wg-quick@wg0
sudo systemctl status wg-quick@wg0
sudo wg show
sudo ip addr show wg0
```

---

## 5. Проверка туннеля

**С Selectel:**
```bash
# Пинг должен идти
ping -c 3 10.10.0.1
# 64 bytes from 10.10.0.1: icmp_seq=1 ttl=64 time=20-40 ms

# Curl на Caddy proxy через туннель
curl -v http://10.10.0.1:8080/healthz
# 200 OK, body "ok"

# wg show — handshake должен быть свежим (<2 min ago)
sudo wg show
# latest handshake: X seconds ago
# transfer: X B received, Y B sent
```

**С Hetzner:**
```bash
# Пинг до Selectel-WG-IP
ping -c 3 10.10.0.2
# Должен идти

# Если из Hetzner делаем curl к OpenAI — без proxy (он сам себе exit)
curl -v https://api.openai.com/v1/models
# Должно работать (Hetzner имеет direct access к OpenAI)
```

---

## 6. Firewall — финальная конфигурация

**На Hetzner:**
```bash
# UFW
sudo ufw status
# Должно быть:
# 22/tcp ALLOW IN
# 51820/udp ALLOW IN          ← WG handshake
# 9090/tcp ALLOW IN           ← HealthCheck (если используем UptimeRobot HTTP)
# Anywhere DENY IN

# Hetzner Cloud Firewall (в их Cloud Console — отдельный layer):
# Inbound rules:
#   TCP 22 from <твой IP>/32
#   UDP 51820 from <RU_PUBLIC_IP>/32
#   TCP 9090 from 0.0.0.0/0 (или uptimerobot IPs)
# Outbound: allow all
```

**На Selectel:**
```bash
sudo ufw status
# 22/tcp ALLOW IN (или с твоего IP only)
# 80/tcp ALLOW IN
# 443/tcp ALLOW IN
# 443/udp ALLOW IN              ← HTTP/3 (Caddy)
# Anywhere DENY IN
# (51820/udp НЕ нужен — мы клиент, не слушаем)
```

---

## 7. Troubleshooting

### 7.1 Туннель не поднимается

```bash
sudo journalctl -u wg-quick@wg0 -n 50 --no-pager
sudo wg show
```

**Проверки:**
- `ListenPort` на сервере открыт в UFW и Hetzner Cloud Firewall.
- `Endpoint` на клиенте указывает на правильный публичный IP.
- Public keys поменяны крест-накрест (Hetzner pubkey → в Selectel-config, наоборот).
- `AllowedIPs` корректные. Подмаска /32 для одиночных peer'ов, /24 для подсети.

### 7.2 Туннель up, но `ping 10.10.0.1` не идёт

```bash
# На Hetzner — IP forwarding
sysctl net.ipv4.ip_forward
# Должно быть 1, не 0

# Iptables FORWARD policy
sudo iptables -L FORWARD -v -n
# Должна быть правило ACCEPT для wg0

# tcpdump на интерфейсе wg0 со стороны Hetzner
sudo tcpdump -i wg0 -n
# Должны лететь пакеты от 10.10.0.2
```

### 7.3 Handshake не происходит

```bash
sudo wg show
# Если "latest handshake: never" — проблема с UDP-доступом
sudo tcpdump -i eth0 -n udp port 51820
# С Selectel должны прилетать UDP пакеты на этот порт
```

**Возможные причины:**
- Hetzner Cloud Firewall не пропускает UDP 51820.
- Selectel блокирует исходящий UDP к зарубежным IP (никогда не видел такого, но дефолт UFW на Selectel — allow outbound).
- `<HEL_PUBLIC_IP>` неправильный.

### 7.4 Туннель up, всё работает, но иногда «отваливается»

```bash
# В обоих конфигах должен быть PersistentKeepalive
grep PersistentKeepalive /etc/wireguard/wg0.conf
# = 25 — стандарт для NAT-traversal

# Если NAT-stateful timeout короче — поставить 15
```

### 7.5 Хочу сменить ключи (компрометация / ротация раз в год)

```bash
# На обоих хостах:
sudo systemctl stop wg-quick@wg0
sudo wg genkey | sudo tee /etc/wireguard/privatekey-new
sudo cat /etc/wireguard/privatekey-new | wg pubkey | sudo tee /etc/wireguard/publickey-new

# Обмениваешься новыми publickey между хостами
# Обновляешь wg0.conf на обоих сторонах одновременно
# sudo systemctl start wg-quick@wg0

# Старые ключи удалить только после успешного запуска новых.
```

---

## 8. Backup-стратегия конфига

Конфиг WG (без приватных ключей) — в git. Приватные ключи — в 1Password.

```bash
# В git: infra/wireguard/wg0.example.conf
# Production: /etc/wireguard/wg0.conf (chmod 600, owner root)
```

**При пересоздании сервера:**
1. Достаём приватный ключ из 1Password vault «Brikko Production / Hetzner WG private key».
2. Кладём в `/etc/wireguard/privatekey`.
3. Берём шаблон из git и подставляем ключ + endpoint.
4. `systemctl start wg-quick@wg0`.

---

## 9. Мониторинг туннеля

**На Selectel — простой скрипт `/usr/local/bin/wg-monitor.sh`:**

```bash
#!/bin/bash
# Проверяет туннель и алертит в Telegram при пропадании.

set -e

PING_HOST="10.10.0.1"
TG_TOKEN="${TG_BOT_TOKEN:-}"
TG_CHAT="${TG_CHAT_ID:-}"

if ping -c 2 -W 3 "$PING_HOST" >/dev/null 2>&1; then
    # OK — туннель жив
    exit 0
fi

# Туннель упал
HOSTNAME=$(hostname)
MSG="🚨 WireGuard tunnel DOWN on ${HOSTNAME}. Cannot ping ${PING_HOST}. Trying restart..."

if [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ]; then
    curl -s "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d "chat_id=${TG_CHAT}" -d "text=${MSG}" >/dev/null
fi

# Пробуем рестарт
systemctl restart wg-quick@wg0
sleep 5

if ping -c 2 -W 3 "$PING_HOST" >/dev/null 2>&1; then
    MSG="✅ WireGuard tunnel restored after restart on ${HOSTNAME}"
else
    MSG="❌ WireGuard tunnel STILL DOWN on ${HOSTNAME} after restart. Manual intervention required."
fi

if [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ]; then
    curl -s "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d "chat_id=${TG_CHAT}" -d "text=${MSG}" >/dev/null
fi
```

**Cron** (`/etc/cron.d/wg-monitor`):
```cron
# Раз в 2 минуты
*/2 * * * * deploy /usr/local/bin/wg-monitor.sh
```

`chmod +x /usr/local/bin/wg-monitor.sh && chown deploy:deploy /usr/local/bin/wg-monitor.sh`.

В скрипт подставлять `TG_BOT_TOKEN` / `TG_CHAT_ID` из `/opt/voltari/.env` или отдельный `/etc/wg-monitor.env`.

---

## 10. Шаблоны для git (без секретов)

Положить в `infra/wireguard/`:

**`hetzner-wg0.example.conf`** — шаблон для Hetzner.
**`selectel-wg0.example.conf`** — шаблон для Selectel.

Создаются с placeholder'ами `<HETZNER_PRIVATE_KEY>`, `<SELECTEL_PUBLIC_KEY>`, `<HEL_PUBLIC_IP>`.

> Эти файлы я НЕ создаю как часть этого ТЗ — содержимое уже выше в §3-§4. Скопируешь в момент развёртывания.

---

## 11. Связь с другими документами

- `infra/two_server_setup.md` — общая архитектура.
- `infra/hetzner_setup.md` — пошагово как поднять Hetzner (включая WG установку).
- `infra/launch_day_runbook.md` — последовательность дня запуска.
