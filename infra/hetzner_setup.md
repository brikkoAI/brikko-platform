# Hetzner — зарубежный proxy для OpenAI/Anthropic/Google

**Цель:** минимальный VPS в Финляндии, который проксирует исходящие LLM-запросы. Без хранения данных, без Docker, без логов.

**Стоимость:** Hetzner CX22 = €4.49/мес (~430 ₽ при курсе 95 ₽/€).

---

## 1. ⚠️ Что нужно ДО заказа Hetzner — action item для CEO

**Hetzner НЕ принимает карты РФ-банков** (МИР, Visa/Mastercard выпущенные в РФ).

**Варианты оплаты:**

| Способ | Плюсы | Минусы | Что нужно |
|---|---|---|---|
| **Зарубежная карта** (Казахстан/Армения/Грузия/Сербия/UAE) | Привычный flow, рекуррент | Открыть счёт в зарубежном банке (поездка / удалённо через посредника) | Сама карта |
| **PayPal** | Быстро привязать | Hetzner принимает PayPal, но он сам в РФ заблокирован | Зарубежный PayPal-аккаунт |
| **Криптовалюта (BTC/ETH/USDT)** | Не требует банка | Не reccurring — пополнять депозит руками каждый месяц; пополнения через биржу (P2P) | Кошелёк + биржа (Bybit/MEXC P2P) |
| **Через посредника-ИП** в EU | Кто-то платит за нас, мы платим ему рублями | Доверие, риск посредника | Знакомый в EU + договор |

**Рекомендация (сначала с быстрым flow):**
1. **На запуск** — оплатить 6-12 мес вперёд через USDT TRC20 (Hetzner принимает крипту через сторонний шлюз: Coinbase Commerce — НЕТ; нужно через aaPanel или вручную через biller). Или одолжить чью-то зарубежную карту на 1 раз пополнения.
2. **На M3-M6** — получить Wise/Revolut/казахстанский Halyk-bank — тогда стабильно.

> **Действие CEO:** до получения ИНН — определиться **как платить Hetzner**. Если нет варианта — сначала развернуться на одиночном сервере (компромисс), потом мигрировать.

---

## 2. Регистрация в Hetzner

1. Открыть https://accounts.hetzner.com/signUp.
2. Email + пароль + 2FA (Google Authenticator — обязательно).
3. **Verify identity** — Hetzner просит документы (копию паспорта). Это нормально, GDPR-комплаянс.
4. Добавить payment method.
5. Создать **Cloud Project** «Brikko Proxy».

---

## 3. Заказ сервера

Меню: **Cloud Console → Servers → Add Server**.

| Поле | Значение |
|---|---|
| Location | **Helsinki (HEL1)** — Финляндия. *Альтернатива: Falkenstein/Nuremberg DE если в HEL нет capacity.* |
| Image | **Ubuntu 24.04** |
| Type | **Shared vCPU → CX22** (2 vCPU AMD, 4 GB RAM, 40 GB NVMe) |
| Networking | IPv4: ✅, IPv6: ✅ (бесплатно). Public network: ✅ |
| SSH Keys | **Загрузить публичный ключ заранее** (см. §4) |
| Volumes | пропустить (40 GB встроенного хватит с запасом) |
| Firewalls | пропустить (настроим UFW на сервере вручную) |
| Backups | **off** на старте (бэкапить нечего — конфиг Caddy и WG в git). +20% к цене, не нужно. |
| Placement groups | пропустить |
| Labels | `env=prod`, `role=outbound-proxy` |
| Cloud config / User data | вставить cloud-init скрипт ниже (см. §5) |
| Name | `brikko-proxy-hel1` |

Создать. Через ~30 сек сервер готов, видишь публичный IP.

**Запиши IP:** `<HEL_PUBLIC_IP>` — пригодится в WireGuard и DNS.

---

## 4. SSH-ключи

**На локальной машине (один раз):**
```bash
# В Windows — через WSL или Git Bash
ssh-keygen -t ed25519 -f ~/.ssh/brikko_hetzner -C "brikko-hetzner-deploy"
# Запросит passphrase — задать (потерявший ключ = взлом сервера)
cat ~/.ssh/brikko_hetzner.pub
# Скопировать → загрузить в Hetzner Cloud Console при создании сервера
```

**Подключение к серверу:**
```bash
ssh -i ~/.ssh/brikko_hetzner root@<HEL_PUBLIC_IP>
```

Если хочешь короче — `~/.ssh/config`:
```
Host brikko-hetzner
    HostName <HEL_PUBLIC_IP>
    User root
    IdentityFile ~/.ssh/brikko_hetzner
    Port 22
```

---

## 5. Cloud-init (стартовая настройка)

При создании сервера в поле **Cloud config** вставить:

```yaml
#cloud-config
package_update: true
package_upgrade: true
packages:
  - ufw
  - fail2ban
  - wireguard
  - caddy
  - htop
  - curl
  - vim
  - unattended-upgrades

# Создаём deploy-юзера
users:
  - name: deploy
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: sudo
    shell: /bin/bash
    ssh_authorized_keys:
      - ssh-ed25519 AAAA... brikko-hetzner-deploy   # ← подставить твой реальный pubkey

# Запретить root SSH и password auth
write_files:
  - path: /etc/ssh/sshd_config.d/hardening.conf
    content: |
      PermitRootLogin no
      PasswordAuthentication no
      ChallengeResponseAuthentication no
      KbdInteractiveAuthentication no
      UsePAM yes
      X11Forwarding no
      AllowAgentForwarding no
      AllowTcpForwarding no

  # Auto-обновления безопасности
  - path: /etc/apt/apt.conf.d/20auto-upgrades
    content: |
      APT::Periodic::Update-Package-Lists "1";
      APT::Periodic::Unattended-Upgrade "1";
      APT::Periodic::AutocleanInterval "7";

runcmd:
  # UFW: только SSH + WireGuard
  - ufw default deny incoming
  - ufw default allow outgoing
  - ufw allow 22/tcp comment 'SSH'
  - ufw allow 51820/udp comment 'WireGuard'
  - ufw --force enable
  - systemctl enable --now ufw
  - systemctl enable --now fail2ban
  - systemctl restart sshd
  # Caddy будет настроен и запущен после WireGuard (см. §7)
  - systemctl stop caddy
  - systemctl disable caddy

# Задаём hostname
hostname: brikko-proxy-hel1
fqdn: brikko-proxy-hel1.local
```

> **Caddy disabled на старте** — мы запустим его руками после того как WireGuard поднимется и Caddy будет bind-иться на 10.10.0.1.

---

## 6. Базовая security-проверка (сразу после первого SSH)

```bash
ssh -i ~/.ssh/brikko_hetzner deploy@<HEL_PUBLIC_IP>

# 1. SSH-only-by-key
sudo grep -E "^(PasswordAuthentication|PermitRootLogin)" /etc/ssh/sshd_config.d/hardening.conf
# PasswordAuthentication no
# PermitRootLogin no

# 2. UFW открыт только для SSH и WG
sudo ufw status verbose
# Должно быть: 22/tcp ALLOW, 51820/udp ALLOW, всё остальное DENY

# 3. fail2ban работает
sudo systemctl status fail2ban
sudo fail2ban-client status
sudo fail2ban-client status sshd

# 4. unattended-upgrades крутится
sudo systemctl status unattended-upgrades
sudo cat /var/log/unattended-upgrades/unattended-upgrades.log | tail -20

# 5. Никаких лишних сервисов
sudo ss -tlnp
# Должны быть только: sshd на 22 (опционально systemd-resolved на 53/loopback)

# 6. Открытый трафик наружу — да
curl -fsS https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY" | head
# Если 401 — значит до OpenAI достучались, ключ просто не настоящий — это OK
```

---

## 7. Установка Caddy (после WireGuard)

WireGuard ставим **раньше** Caddy (см. `infra/wireguard_setup.md`). После того как `wg0` интерфейс up и `ip a show wg0` показывает `10.10.0.1/24`:

**Caddyfile** (`/etc/caddy/Caddyfile`):

```caddy
# Brikko outbound-proxy.
# Слушает ТОЛЬКО на туннеле — снаружи 8080 закрыт UFW + bind на 10.10.0.1.

{
    admin off
    auto_https off                # внутренний proxy, нам HTTPS на этом hop не нужен
    persist_config off
    log {
        output stderr
        level WARN                # только warn/error, никаких access-логов
    }
}

# Bind на туннельный IP — НЕ на 0.0.0.0
http://10.10.0.1:8080 {
    # Проксим к OpenAI
    handle_path /openai/* {
        reverse_proxy https://api.openai.com {
            header_up Host api.openai.com
            transport http {
                tls
                response_header_timeout 300s
                read_timeout 300s
                write_timeout 300s
                dial_timeout 10s
            }
        }
    }

    # Проксим к Anthropic
    handle_path /anthropic/* {
        reverse_proxy https://api.anthropic.com {
            header_up Host api.anthropic.com
            transport http {
                tls
                response_header_timeout 300s
                read_timeout 300s
                write_timeout 300s
                dial_timeout 10s
            }
        }
    }

    # Проксим к Google AI
    handle_path /google/* {
        reverse_proxy https://generativelanguage.googleapis.com {
            header_up Host generativelanguage.googleapis.com
            transport http {
                tls
                response_header_timeout 300s
                read_timeout 300s
                write_timeout 300s
                dial_timeout 10s
            }
        }
    }

    # Health check для UptimeRobot (через WG → проверяем туннель + caddy)
    handle /healthz {
        respond "ok" 200 {
            close
        }
    }

    # Всё остальное — 404
    handle {
        respond "Not Found" 404
    }
}
```

**Запустить Caddy:**
```bash
sudo nano /etc/caddy/Caddyfile        # вставить конфиг выше
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
sudo systemctl status caddy
sudo journalctl -u caddy -n 50 --no-pager
```

---

## 8. Проверка работы proxy

**С локальной машины через Hetzner SSH:**
```bash
ssh deploy@<HEL_PUBLIC_IP>
# Локально на Hetzner
curl -v http://10.10.0.1:8080/healthz
# Ожидаем: 200 OK, body "ok"

curl -v http://10.10.0.1:8080/openai/v1/models -H "Authorization: Bearer test"
# Ожидаем: 401 от OpenAI (= достучались, ключ невалидный)
```

**С Selectel-сервера (после поднятия WG-tunnel):**
```bash
ssh deploy@<RU_PUBLIC_IP>
curl -v http://10.10.0.1:8080/healthz
# Ожидаем: 200 OK
```

---

## 9. UptimeRobot мониторинг

Меню в UptimeRobot: **Add New Monitor**.

| Поле | Значение |
|---|---|
| Type | HTTP(S) |
| URL | **НЕ через WG** (UptimeRobot снаружи) — `http://<HEL_PUBLIC_IP>:8080/healthz` |
| **НО:** UFW блокирует 8080 снаружи | значит создаём отдельный health-endpoint, доступный извне |

**Решение:** на Hetzner откроем **только `/healthz` на 0.0.0.0:9090** через отдельный simple-server, а `/openai/*` etc. — только через туннель.

В Caddyfile добавляем второй блок:
```caddy
:9090 {
    handle /healthz {
        respond "ok" 200
    }
    handle {
        respond "Not Found" 404
    }
}
```

И в UFW:
```bash
sudo ufw allow 9090/tcp comment 'Health check (UptimeRobot)'
```

UptimeRobot:
- URL: `http://<HEL_PUBLIC_IP>:9090/healthz`
- Interval: 5 min
- Alert: email + Telegram (через UptimeRobot integration)

> **Альтернатива покруче:** UptimeRobot сам имеет диапазон IP, можно открыть 9090 только им: https://uptimerobot.com/inc/files/ips/IPv4andIPv6.txt — добавить в UFW по списку. На старте упрощаем до open-9090.

---

## 10. Cost summary

| Item | Цена | Где платим |
|---|---|---|
| Hetzner CX22 Helsinki | €4.49/мес ≈ **430 ₽/мес** | Зарубежная карта / USDT |
| Hetzner backup option | НЕ берём (+20%) | — |
| IPv4 (включён) | 0 | — |
| Трафик (20 TB/мес incl) | 0 | — |
| **Итого** | **~430 ₽/мес** | |

**Прогноз:** при росте до 500+ клиентов и >20 TB трафика рассмотреть CX32 (€8.99/мес ≈ 850 ₽/мес) или второй CX22 в другом регионе для failover.

---

## 11. Чек-лист безопасности

- [ ] SSH key only (ssh-ed25519, passphrase задан)
- [ ] root login disabled
- [ ] Password auth disabled
- [ ] UFW: deny incoming default, allow только 22/51820/9090
- [ ] fail2ban работает (`fail2ban-client status sshd`)
- [ ] unattended-upgrades включены
- [ ] 2FA в Hetzner Cloud Console — включён
- [ ] Caddy bind на туннельный IP, не на 0.0.0.0 (для proxy)
- [ ] Caddy НЕ логирует запросы (только WARN+)
- [ ] Hetzner Cloud Firewall — пропускает в Hetzner UI: deny inbound по умолчанию (двойная защита поверх UFW)
- [ ] Reset/console доступ через Hetzner Cloud Console — пароль хранится в 1Password

---

## 12. Откат и инциденты

| Инцидент | Что делать |
|---|---|
| Hetzner упал / IP сменился | (1) В Hetzner Cloud Console → Reset → может вернёт; (2) Если IP сменился — обновить `endpoint` в `wg0.conf` на Selectel, `systemctl restart wg-quick@wg0`. |
| OpenAI блокирует HEL IP | Поднять второй CX22 в другом регионе (FSN1 / NBG1), переключить tunnel и `Endpoint` в WG-config. |
| Caddy упал | `sudo systemctl restart caddy`; если не помогает — `journalctl -u caddy -n 100`. |
| WG tunnel down | См. `infra/wireguard_setup.md` §7 troubleshoot. |
| Cloud-провайдер блокирует Hetzner-аккаунт | Backup-провайдер: Vultr (Frankfurt, $6/мес, принимает крипту). Передеплоить через 30 мин. |

---

## 13. Что НЕ делать на этом сервере

- Не ставить Docker (RAM-overhead, не нужен).
- Не ставить PostgreSQL / Redis / любые БД.
- Не хранить .env с секретами OpenAI/Anthropic — они на Selectel-стороне, идут с каждым запросом в headers.
- Не логировать тела запросов / response (privacy).
- Не открывать наружу 8080 (UFW + bind на туннель).
- Не использовать как jump-host для других серверов.

---

## 14. Связь с другими документами

- `infra/wireguard_setup.md` — как поднять туннель.
- `infra/two_server_setup.md` — общая архитектура.
- `infra/launch_day_runbook.md` — порядок развёртывания.
- `.github/workflows/deploy-proxy.yml` (создан Sprint 8) — auto-deploy Caddyfile на Hetzner при push в main.
