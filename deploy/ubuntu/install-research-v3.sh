#!/usr/bin/env bash
# First installation only. Existing experiment histories are never reset.
set -euo pipefail
test "$(id -u)" = 0
test "$(pwd -P)" = /opt/ai-paper-research-v3
test -f dist/app/server.js
test -d node_modules
id paper-trader >/dev/null
id trade-agent >/dev/null
test ! -e /home/trade-agent/paper-research-v3/experiment.json
for mode in baseline candidate; do
  test ! -e "/var/lib/ai-paper-research-$mode/paper.sqlite"
  test ! -e "/etc/ai-paper-research-$mode.env"
done
for unit in deploy/ubuntu/ai-paper-research-*.service deploy/ubuntu/ai-paper-research-*.timer; do
  test ! -e "/etc/systemd/system/$(basename "$unit")"
done

install -d -o trade-agent -g trade-agent -m 0700 /home/trade-agent/paper-research-v3
for part in baseline candidate market reports; do
  install -d -o trade-agent -g trade-agent -m 0700 "/home/trade-agent/paper-research-v3/$part"
done
for mode in baseline candidate; do
  install -d -o paper-trader -g paper-trader -m 0700 "/var/lib/ai-paper-research-$mode"
  install -m 0600 "deploy/ubuntu/ai-paper-research-$mode.env.example" "/etc/ai-paper-research-$mode.env"
done
install -m 0644 deploy/ubuntu/ai-paper-research-*.service deploy/ubuntu/ai-paper-research-*.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/ai-paper-research-*.service /etc/systemd/system/ai-paper-research-*.timer
systemctl daemon-reload
systemctl enable --now ai-paper-research-backend@baseline.service ai-paper-research-backend@candidate.service
python3 - <<'PY'
import time
from urllib.request import urlopen
for port in (3004, 3005):
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
runuser -u trade-agent -- python3 -B runner/research_runner.py --root /home/trade-agent/paper-research-v3 --initialize
systemctl start ai-paper-research-market.service
systemctl start ai-paper-research-agent@baseline.service ai-paper-research-agent@candidate.service
systemctl start ai-paper-research-report.service
systemctl enable --now ai-paper-research-market.timer ai-paper-research-agent@baseline.timer ai-paper-research-agent@candidate.timer ai-paper-research-report.timer
systemctl is-active ai-paper-research-backend@baseline.service ai-paper-research-backend@candidate.service ai-paper-research-market.timer ai-paper-research-agent@baseline.timer ai-paper-research-agent@candidate.timer ai-paper-research-report.timer
