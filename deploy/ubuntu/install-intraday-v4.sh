#!/usr/bin/env bash
# First installation only: no existing paper experiment is reset or replaced.
set -euo pipefail
test "$(id -u)" = 0
test "$(pwd -P)" = /opt/ai-paper-intraday-v4
test -f dist/app/server.js
test -d node_modules
id paper-trader >/dev/null
id trade-agent >/dev/null
test ! -e /home/trade-agent/paper-intraday-v4
for mode in slow pullback; do
  test ! -e "/var/lib/ai-paper-intraday-$mode"
  test ! -e "/etc/ai-paper-intraday-$mode.env"
done
for unit in deploy/ubuntu/ai-paper-intraday-*.service deploy/ubuntu/ai-paper-intraday-*.timer; do
  test ! -e "/etc/systemd/system/$(basename "$unit")"
done
python3 - <<'PY'
import socket
for port in (3006, 3007):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', port))
PY

install -d -o trade-agent -g trade-agent -m 0700 /home/trade-agent/paper-intraday-v4
for part in slow pullback market reports; do
  install -d -o trade-agent -g trade-agent -m 0700 "/home/trade-agent/paper-intraday-v4/$part"
done
for mode in slow pullback; do
  install -d -o paper-trader -g paper-trader -m 0700 "/var/lib/ai-paper-intraday-$mode"
  install -m 0600 "deploy/ubuntu/ai-paper-intraday-$mode.env.example" "/etc/ai-paper-intraday-$mode.env"
done
install -m 0644 deploy/ubuntu/ai-paper-intraday-*.service deploy/ubuntu/ai-paper-intraday-*.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/ai-paper-intraday-*.service /etc/systemd/system/ai-paper-intraday-*.timer
systemctl daemon-reload
systemctl enable --now ai-paper-intraday-backend@slow.service ai-paper-intraday-backend@pullback.service
python3 - <<'PY'
import time
from urllib.request import urlopen
for port in (3006, 3007):
    for attempt in range(50):
        try:
            with urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as response:
                assert response.status == 200
            break
        except OSError:
            time.sleep(.2)
    else:
        raise SystemExit(f'Backend {port} failed health check; timers remain disabled')
PY
runuser -u trade-agent -- python3 -B runner/intraday_runner.py --root /home/trade-agent/paper-intraday-v4 --initialize --slow-backend http://127.0.0.1:3006 --pullback-backend http://127.0.0.1:3007
systemctl start ai-paper-intraday-market.service
systemctl start ai-paper-intraday-agent@slow.service ai-paper-intraday-agent@pullback.service
systemctl start ai-paper-intraday-report.service
systemctl enable --now ai-paper-intraday-market.timer ai-paper-intraday-agent@slow.timer ai-paper-intraday-agent@pullback.timer ai-paper-intraday-report.timer
systemctl is-active ai-paper-intraday-backend@slow.service ai-paper-intraday-backend@pullback.service ai-paper-intraday-market.timer ai-paper-intraday-agent@slow.timer ai-paper-intraday-agent@pullback.timer ai-paper-intraday-report.timer
