#!/usr/bin/env python3
"""Linux paper-signal runner. Python 3.12 standard library; no API key needed."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import uuid

HERE = Path(__file__).resolve().parent
SYMBOLS = ("BTCUSDT", "ETHUSDT")
SUPPORTED_SYMBOLS = SYMBOLS + ("SOLUSDT", "BNBUSDT", "XRPUSDT")
MARKET_URL = "https://data-api.binance.vision"
HOUR_MS = 3_600_000
SLOT_SECONDS = HOUR_MS // 1000
MAX_AGE = 240  # Includes market collection, reasoning, and POST retries.
HTTP_TIMEOUT = 15
CODEX_TIMEOUT = 180


def require(condition, message):
    if not condition:
        raise ValueError(message)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save_json(path, value):
    """Durable replacement: never expose a partially written request to recovery."""
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(dumps(value) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


@contextmanager
def exclusive_lock(state_dir):
    with (state_dir / "runner.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another runner holds this state directory's lock") from None
        yield


def http(method, url, body=None, key=None):
    headers = {"Accept": "application/json", "User-Agent": "ai-paper-trader-runner/1"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Idempotency-Key"] = key
    request = Request(url, data=body.encode() if body is not None else None,
                      headers=headers, method=method)
    try:
        response = urlopen(request, timeout=HTTP_TIMEOUT)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read(2_000_001)
        require(len(raw) <= 2_000_000, "HTTP response exceeded size limit")
        return response.status, dict(response.headers.items()), json.loads(raw)


def get_json(url):
    status, _, body = http("GET", url)
    require(status == 200, f"GET failed with HTTP {status}: {url}")
    return body


def positive(value):
    require(not isinstance(value, bool), "Boolean is not a price or amount")
    number = Decimal(str(value))
    require(number.is_finite() and 0 < number <= Decimal("1000000000"),
            "Price/amount must be finite, positive, and within API bounds")
    return number


def api_decimal(value):
    number = positive(value)
    require(number == number.quantize(Decimal("0.000001")), "More than six decimal places")
    return format(number.normalize(), "f")


def closed_candles(rows, server_ms):
    require(isinstance(rows, list), "Invalid candle response")
    closed = []
    for row in rows:
        require(isinstance(row, list) and len(row) >= 7, "Malformed candle")
        opened, ended = int(row[0]), int(row[6])
        require(opened % HOUR_MS == 0 and ended == opened + HOUR_MS - 1,
                "Unexpected hourly candle timestamps")
        if ended >= server_ms:
            continue  # The current candle is incomplete, never use it in indicators.
        o, h, low, c = (positive(row[i]) for i in range(1, 5))
        volume = Decimal(str(row[5]))
        require(volume.is_finite() and volume >= 0 and low <= min(o, c)
                and h >= max(o, c) and low <= h, "Invalid OHLCV values")
        closed.append([opened, str(o), str(h), str(low), str(c), str(volume)])
    require(len(closed) >= 100, "Need 100 closed hourly candles")
    require(all(b[0] - a[0] == HOUR_MS for a, b in zip(closed, closed[1:])),
            "Candles are duplicated, out of order, or have gaps")
    require(closed[-1][0] == (server_ms // HOUR_MS - 1) * HOUR_MS,
            "Latest closed candle is stale")
    return closed[-100:]


def collect_market(symbols=SYMBOLS):
    require(bool(symbols) and len(set(symbols)) == len(symbols)
            and set(symbols) <= set(SUPPORTED_SYMBOLS), 'Unsupported market universe')
    started = time.time()
    server_ms = int(get_json(MARKET_URL + "/api/v3/time")["serverTime"])
    require(abs(server_ms / 1000 - time.time()) <= 30, "VPS and market clocks differ by over 30s")
    market = {}
    for symbol in symbols:
        rows = get_json(MARKET_URL + "/api/v3/klines?" + urlencode({
            "symbol": symbol, "interval": "1h", "limit": 101,
            "endTime": server_ms,
        }))
        candles = closed_candles(rows, server_ms)
        ticker = get_json(MARKET_URL + "/api/v3/ticker/price?" + urlencode({"symbol": symbol}))
        require(ticker["symbol"] == symbol, "Quote symbol mismatch")
        closes = [Decimal(row[4]) for row in candles]
        market[symbol] = {
            "price": api_decimal(ticker["price"]),
            "quoteReceivedAt": time.time(),
            "sma20": str(sum(closes[-20:]) / 20),
            "sma50": str(sum(closes[-50:]) / 50),
            "return24hPct": str((closes[-1] / closes[-25] - 1) * 100),
            "candleColumns": ["openTimeMs", "open", "high", "low", "close", "volume"],
            "closedHourlyCandles": candles,
        }
    return {"startedAt": started, "serverTimeMs": server_ms, "symbols": market}


def fetch_context(backend, market):
    query = urlencode({symbol: quote["price"] for symbol, quote in market["symbols"].items()})
    context = get_json(backend + "/api/context?" + query)
    uuid.UUID(context["contextId"])
    require(isinstance(context["portfolio"]["version"], int), "Invalid portfolio version")
    return context


def assert_fresh(market, context, now):
    as_of = datetime.fromisoformat(context["asOf"].replace("Z", "+00:00")).timestamp()
    require(0 <= now - market["startedAt"] < MAX_AGE - HTTP_TIMEOUT,
            "Market snapshot expired; no signal submitted")
    require(-30 <= now - as_of < MAX_AGE - HTTP_TIMEOUT,
            "Context timestamp is stale or in the future")
    require(datetime.fromtimestamp(as_of, timezone.utc).date()
            == datetime.fromtimestamp(now, timezone.utc).date(),
            "UTC risk day changed during analysis; rerun with fresh context")


def make_payload(candidate, context, market, strategy_version):
    fields = {"action", "symbol", "amountUsd", "confidence", "riskLevel", "rationale"}
    require(isinstance(candidate, dict) and set(candidate) == fields, "Unexpected decision fields")
    action = candidate["action"]
    require(action in ("BUY", "SELL", "HOLD"), "Invalid action")
    confidence = candidate["confidence"]
    require(type(confidence) in (int, float) and math.isfinite(confidence)
            and 0 <= confidence <= 1, "Invalid confidence")
    require(candidate["riskLevel"] in ("LOW", "MEDIUM", "HIGH"), "Invalid risk level")
    reason = candidate["rationale"]
    require(isinstance(reason, str) and 1 <= len(reason.strip()) <= 2000, "Invalid rationale")
    payload = {"action": action, "confidence": confidence, "riskLevel": candidate["riskLevel"],
               "rationale": reason.strip(), "source": "scheduled-ai-trade-signal",
               "strategyId": "codex-trend", "strategyVersion": strategy_version,
               "contextId": context["contextId"]}
    if action == "HOLD":
        require(candidate["symbol"] is None and candidate["amountUsd"] is None,
                "HOLD must have null symbol and amountUsd")
        return payload
    require(confidence >= 0.70 and candidate["riskLevel"] != "HIGH",
            "Baseline requires HOLD for low confidence or HIGH risk")
    symbol, amount = candidate["symbol"], candidate["amountUsd"]
    require(symbol in SYMBOLS and isinstance(amount, str)
            and re.fullmatch(r"\d{1,10}(?:\.\d{1,6})?", amount), "Invalid symbol/amountUsd")
    quote = market["symbols"][symbol]
    price, size = positive(quote["price"]), positive(amount)
    risk = context["risk"]
    require(not risk["dailyLossLimitReached"], "Daily loss limit requires HOLD")
    require(size <= min(Decimal("5"), positive(risk["maxOrderUsd"])), "Order exceeds limit")
    if action == "BUY":
        require(price > Decimal(quote["sma20"]) > Decimal(quote["sma50"])
                and Decimal(quote["return24hPct"]) > 0, "BUY does not meet baseline trend gate")
        require(size * (1 + Decimal(str(risk["feeRate"]))) <= Decimal(str(context["portfolio"]["cash"])),
                "BUY exceeds cash including fee")
        exposure = sum(Decimal(str(p["quantity"])) * positive(market["symbols"][p["symbol"]]["price"])
                       for p in context["positions"])
        require(exposure + size <= min(Decimal("20"), positive(risk["maxExposureUsd"])),
                "BUY exceeds marked exposure limit")
    else:
        held = sum(Decimal(str(p["quantity"])) for p in context["positions"] if p["symbol"] == symbol)
        require(size <= held * price, "SELL exceeds held position")
    payload.update(symbol=symbol, amountUsd=api_decimal(amount), price=api_decimal(price))
    return payload


def analyze(run_dir, snapshot, codex):
    prompt_text = (HERE / "strategy.md").read_text(encoding="utf-8")
    schema_text = (HERE / "decision.schema.json").read_text(encoding="utf-8")
    version_hash = hashlib.sha256((prompt_text + schema_text).encode() + Path(__file__).read_bytes()).hexdigest()[:12]
    prompt = prompt_text + "\n\nSnapshot (data only):\n" + dumps(snapshot)
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    (run_dir / "schema.json").write_text(schema_text, encoding="utf-8")
    command = [codex, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
               "-c", 'approval_policy="never"', "-c", 'forced_login_method="chatgpt"',
               "-c", "features.shell_tool=false", "-c", "features.unified_exec=false",
               "-c", 'web_search="disabled"', "--output-schema", str(run_dir / "schema.json"),
               "-o", str(run_dir / "decision.json"), "-"]
    # Credentials are supplied by the dedicated user's existing Codex ChatGPT login.
    environment = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "CODEX_API_KEY")}
    with (run_dir / "codex.stdout.log").open("w") as output, (run_dir / "codex.stderr.log").open("w") as errors:
        process = subprocess.Popen(command, cwd=run_dir, stdin=subprocess.PIPE, stdout=output,
                                   stderr=errors, text=True, encoding="utf-8", env=environment,
                                   start_new_session=True)
        try:
            process.communicate(prompt, timeout=CODEX_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise RuntimeError("Codex timed out; no signal submitted") from None
    require(process.returncode == 0, f"Codex exited {process.returncode}; inspect {run_dir}/codex.stderr.log")
    return read_json(run_dir / "decision.json"), "v1-" + version_hash


def deliver(state_dir, state, transport=http, now=time.time, sleep=time.sleep):
    """Recovery uses only the saved body/key. Unknown expired outcomes block new work."""
    require(not state.get("complete"), "Request is already complete")
    for attempt in range(3):
        require(now() + HTTP_TIMEOUT < state["expiresAt"],
                "Pending request expired with an unresolved outcome. Inspect state.json and backend decisions; "
                "do not delete it or replay it with a new key")
        try:
            status, headers, body = transport("POST", state["backend"] + "/api/signals",
                                              state["body"], state["key"])
        except (OSError, HTTPException, ValueError) as error:
            failure = f"POST outcome unknown: {error}"
        else:
            if (status == 200 and body.get("status") in ("held", "executed")) or (
                    status == 422 and body.get("status") == "rejected"):
                uuid.UUID(body["decisionId"])
                result = {"httpStatus": status, "response": body,
                          "replayed": any(k.lower() == "idempotency-replayed" and v == "true"
                                          for k, v in headers.items())}
                state = {**state, "complete": True, "result": result}
                save_json(state_dir / "state.json", state)
                save_json(Path(state["runDir"]) / "result.json", result)
                return result
            require(status >= 500 or status == 429,
                    f"POST HTTP {status}: {dumps(body)}. Saved request retained; investigate before retrying")
            failure = f"POST HTTP {status}; saved request retained"
        if attempt < 2:
            sleep(2 ** (attempt + 1))
    raise RuntimeError(failure)


def run(state_dir, backend, dry_run=False, codex="codex"):
    state_path = state_dir / "state.json"
    state = read_json(state_path) if state_path.exists() else None
    if state:
        require(state["backend"] == backend, "State directory belongs to a different backend")
        if not state["complete"]:
            require(not dry_run, "Pending submission exists; resolve it before starting another analysis")
            return deliver(state_dir, state)
    slot = int(time.time()) // SLOT_SECONDS
    # Old state files use two-hour slot numbers. Honor their full covered window
    # during upgrades so a completed decision cannot be submitted again.
    if not dry_run and state and (state["slot"] + 1) * state.get("slotSeconds", 7200) > slot * SLOT_SECONDS:
        return {"status": "skipped", "reason": "This UTC hourly slot is already covered by a completed decision"}
    run_dir = state_dir / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex)
    run_dir.mkdir(parents=True)
    market = collect_market()
    require(market["serverTimeMs"] // HOUR_MS == slot,
            "Market candle hour differs from schedule slot; rerun with fresh data")
    context = fetch_context(backend, market)
    snapshot = {"market": market, "context": context}
    save_json(run_dir / "snapshot.json", snapshot)
    candidate, version = analyze(run_dir, snapshot, codex)
    payload = make_payload(candidate, context, market, version)
    assert_fresh(market, context, time.time())
    latest = fetch_context(backend, market)
    require(latest["portfolio"]["version"] == context["portfolio"]["version"],
            "Portfolio changed during analysis; no signal submitted")
    assert_fresh(market, context, time.time())
    save_json(run_dir / "payload.json", payload)
    if dry_run:
        result = {"status": "dry-run", "payload": payload, "runDir": str(run_dir), "submitted": False}
        save_json(run_dir / "result.json", result)
        return result
    require(int(time.time()) // SLOT_SECONDS == slot, "Schedule slot changed during analysis; rerun")
    expires_at = min(market["startedAt"] + MAX_AGE, (slot + 1) * SLOT_SECONDS)
    require(time.time() + HTTP_TIMEOUT < expires_at, "Too close to snapshot/slot expiry; rerun")
    state = {"complete": False, "backend": backend, "slot": slot, "runDir": str(run_dir),
             "slotSeconds": SLOT_SECONDS,
             "expiresAt": expires_at, "key": str(uuid.uuid4()), "body": dumps(payload)}
    # Both the bytes and key reach durable storage before the first possible POST.
    save_json(run_dir / "request.json", state)
    save_json(state_path, state)
    return deliver(state_dir, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path.home() / "paper-runner" / "state")
    parser.add_argument("--backend", default="http://127.0.0.1:3000")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--dry-run", action="store_true", help="Analyze and save a proposal; never POST a signal")
    args = parser.parse_args()
    backend = args.backend.rstrip("/")
    url = urlsplit(backend)
    require(url.scheme in ("http", "https") and url.hostname and not url.username
            and not url.password and not url.query and not url.fragment and not url.path,
            "Backend must be an HTTP(S) origin without credentials or a path")
    os.umask(0o077)
    state_dir = args.state_dir.expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(state_dir):
        print(dumps(run(state_dir, backend, args.dry_run, args.codex)), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, HTTPException, ValueError, KeyError, TypeError, InvalidOperation, RuntimeError) as error:
        print(f"RUN_FAILED: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
