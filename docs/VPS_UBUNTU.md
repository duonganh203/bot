# Ubuntu 24.04 VPS deployment with Git

The working deployment uses Ubuntu 24.04 x86_64, Node.js 24 at `/usr/bin/node`,
pnpm 11, and Python 3.12 (`python3`). Approximately 894 MiB RAM plus 1 GiB swap
was sufficient for backend installation and the Codex HOLD connectivity test.
The full scheduled analysis still needs its own VPS dry run.

The backend is a single systemd process. Source is cloned into
`/opt/ai-paper-trader`; SQLite lives separately at
`/var/lib/ai-paper-trader/paper.sqlite`. There is no domain requirement.
With `API_TOKEN` empty, any client that can reach the API can submit paper signals.

## Existing server: next step

The backend and Codex login are already installed on the current VPS. Do not
repeat first-install commands. Follow [CODEX_RUNNER.md](CODEX_RUNNER.md) to pull
the runner code, perform a dry run, and then enable the timer.

## First installation on another prepared VPS

Install Node.js 24, pnpm 11, Git, Python 3, and the native build toolchain first.
Verify `node --version`, `pnpm --version`, `python3 --version`, and available disk
and swap. The service template assumes Node is `/usr/bin/node` and executable by
a system user, rather than installed under root's home.

Run as root on the VPS:

```bash
(
  set -e
  cd /opt
  git clone https://github.com/duonganh203/bot.git ai-paper-trader
  cd ai-paper-trader
  pnpm install --frozen-lockfile
  pnpm build
  if ! id paper-trader >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/ai-paper-trader \
      --shell /usr/sbin/nologin paper-trader
  fi
  install -d -o paper-trader -g paper-trader -m 0750 /var/lib/ai-paper-trader
  runuser -u paper-trader -- /usr/bin/node --version
  test ! -e /etc/ai-paper-trader.env
  test ! -e /etc/systemd/system/ai-paper-trader.service
  install -m 0600 deploy/ubuntu/ai-paper-trader.env.example /etc/ai-paper-trader.env
  install -m 0644 deploy/ubuntu/ai-paper-trader.service /etc/systemd/system/
  systemd-analyze verify /etc/systemd/system/ai-paper-trader.service
  systemctl daemon-reload
  systemctl enable --now ai-paper-trader
)
```

The environment sets production mode, `HOST=0.0.0.0`, `PORT=3000`, the persistent
database path above, `LOG_LEVEL=info`, and empty `API_TOKEN`. Startup applies
migrations and preserves existing balances. Never use `db:reset` as an upgrade.

## Verify connectivity

On the VPS:

```bash
systemctl status ai-paper-trader --no-pager
curl --fail-with-body http://127.0.0.1:3000/health
curl --fail-with-body http://127.0.0.1:3000/api/context
```

For public access, inspect `ufw status` and the provider's firewall. If UFW is
already active, allow the intended source to port 3000. Do not reset the firewall
or enable it blindly during an SSH session. [Ubuntu firewall documentation](https://ubuntu.com/server/docs/how-to/security/firewalls/)

On Windows, replace the placeholder with the VPS IP:

```powershell
Invoke-RestMethod 'http://YOUR_VPS_IP:3000/health'
Invoke-RestMethod 'http://YOUR_VPS_IP:3000/api/context'
```

The `/` path is not a dashboard. Use `/health` for polling; `/api/context` stores
a new audit snapshot on each request. The agent on the same VPS uses localhost.

## Operations and upgrades

```bash
systemctl status ai-paper-trader --no-pager
journalctl -u ai-paper-trader -n 100 --no-pager
du -sh /var/lib/ai-paper-trader /home/trade-agent/paper-runner
free -h
df -h /
```

Before upgrading a scheduled deployment, stop its timer and let the active agent
finish. Resolve any pending submission before changing code or restoring a
database. Back up SQLite using a SQLite-aware backup that includes committed WAL
data; do not copy only a live `.sqlite` file. See [DEPLOYMENT.md](DEPLOYMENT.md#updates-and-data-operations).

Then pull and build in the existing checkout:

```bash
(
  set -e
  cd /opt/ai-paper-trader
  git pull --ff-only
  pnpm install --frozen-lockfile
  pnpm build
  systemctl restart ai-paper-trader
)
```

Check health, portfolio, and history after restart. Re-enable the agent timer only
after its checks pass. Keep one runner and one backend writer for this portfolio.
