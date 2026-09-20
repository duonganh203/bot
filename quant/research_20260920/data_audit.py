#!/usr/bin/env python3
"""Build immutable-range, audited Binance hourly inputs with public GETs only.

Existing source caches are read-only. Canonical files are deliberately simple and
retain all raw Binance columns. Reruns validate existing canonical files and
repeat independent anchor requests rather than silently trusting the cache.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "quant"))
import backtest as bt

START = 1724788800000
END = 1789862400000
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")
OUT = ROOT / "data" / "quant" / "research-20260920"
CANDLES = OUT / "candles"
ENDPOINT = bt.BASE + "/api/v3/klines"
LOCK = threading.Lock()
REQUESTS = []
ORIGINAL_GET_JSON = bt.get_json


def now():
    return bt.iso(int(time.time() * 1000))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_sha(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def audited_get_json(path, params=None):
    record = {"requestedAt": now(), "url": bt.BASE + path + "?" + urlencode(params or {})}
    try:
        result = ORIGINAL_GET_JSON(path, params)
        record.update({"completedAt": now(), "rowCount": len(result),
                       "decodedResponseSha256": object_sha(result), "ok": True})
        return result
    except Exception as exc:
        record.update({"completedAt": now(), "ok": False,
                       "error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        with LOCK:
            REQUESTS.append(record)


bt.get_json = audited_get_json


def source_cache(path, expected_symbol):
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved["symbol"] != expected_symbol:
        raise ValueError(f"Unexpected source symbol: {path}")
    # Reuse the production research reader and validator in strict offline mode.
    bt.history(expected_symbol, saved["start"], saved["endExclusive"], path.parent, offline=True)
    return saved, {"path": str(path.relative_to(ROOT)), "sha256": sha(path),
                   "start": saved["start"], "endExclusive": saved["endExclusive"],
                   "rows": len(saved["rows"]), "fetchedAt": saved.get("fetchedAt"),
                   "source": saved.get("source")}


def build_symbol(symbol):
    canonical = CANDLES / f"{symbol}-1h-{START}-{END}.json"
    provenance = []
    overlap_count = 0
    reuse = canonical.exists()
    if reuse:
        saved = json.loads(canonical.read_text(encoding="utf-8"))
        if set(saved) != {"symbol", "start", "endExclusive", "rows", "source", "fetchedAt"}:
            raise ValueError(f"Unexpected canonical schema: {canonical}")
        rows = saved["rows"]
        bt.history(symbol, START, END, CANDLES, offline=True)
        # Preserve original provenance on a rerun of the same source file.
        prior_manifest = OUT / "data-manifest.json"
        if prior_manifest.exists():
            prior = json.loads(prior_manifest.read_text(encoding="utf-8"))
            previous = next((x for x in prior.get("symbols", []) if x["symbol"] == symbol), {})
            provenance = previous.get("sourceParts", [])
            overlap_count = previous.get("overlappingCacheRowsIdentical", 0)
    else:
        by_time = {}
        if symbol in ("BTCUSDT", "ETHUSDT"):
            paths = sorted((ROOT / "data" / "quant" / "candles").glob(f"{symbol}-1h-*.json"))
            for path in paths:
                saved, source = source_cache(path, symbol)
                provenance.append(source)
                for row in saved["rows"]:
                    stamp = int(row[0])
                    if not START <= stamp < END:
                        continue
                    if stamp in by_time:
                        if by_time[stamp] != row:
                            raise ValueError(f"Conflicting overlapping source row {symbol} {bt.iso(stamp)}")
                        overlap_count += 1
                    by_time[stamp] = row
        cursor = START
        while cursor in by_time:
            cursor += bt.HOUR
        if any(stamp >= cursor for stamp in by_time):
            raise ValueError(f"Non-contiguous reusable cache for {symbol}")
        if cursor < END:
            downloads = CANDLES / "source-parts"
            bt.history(symbol, cursor, END, downloads)
            downloaded_path = downloads / f"{symbol}-1h-{cursor}-{END}.json"
            downloaded, source = source_cache(downloaded_path, symbol)
            provenance.append(source)
            for row in downloaded["rows"]:
                if int(row[0]) in by_time:
                    raise ValueError("Unexpected duplicate in downloaded tail")
                by_time[int(row[0])] = row
        rows = [by_time[stamp] for stamp in sorted(by_time)]
        bt.validate_rows(rows, START, END)
        bt.write_json(canonical, {"symbol": symbol, "start": START, "endExclusive": END,
                                  "rows": rows, "source": ENDPOINT, "fetchedAt": now()})
        print(f"CANONICAL_READY {symbol} {canonical} {len(rows)} rows", flush=True)
    bt.validate_rows(rows, START, END)
    if int(rows[-1][6]) >= int(time.time() * 1000):
        raise ValueError(f"Unclosed candle for {symbol}")
    # Three independently requested non-overlapping 24h anchors, fixed by range.
    midpoint = START + (((END - START) // bt.HOUR // 2) // 24) * 24 * bt.HOUR
    anchors = []
    index = {int(row[0]): row for row in rows}
    for label, anchor_start in (("start", START), ("middle", midpoint), ("end", END - 24 * bt.HOUR)):
        anchor_end = anchor_start + 24 * bt.HOUR
        fresh = bt.get_json("/api/v3/klines", {"symbol": symbol, "interval": "1h", "limit": 24,
                                              "startTime": anchor_start, "endTime": anchor_end - 1})
        bt.validate_rows(fresh, anchor_start, anchor_end)
        cached = [index[anchor_start + i * bt.HOUR] for i in range(24)]
        # Timestamp plus raw string OHLCV are compared, not rounded float values.
        cached_ohlcv, fresh_ohlcv = [r[:6] for r in cached], [r[:6] for r in fresh]
        matches = cached_ohlcv == fresh_ohlcv
        anchors.append({"label": label, "start": anchor_start, "endExclusive": anchor_end,
                        "rows": 24, "rawTimestampOhlcvEqual": matches,
                        "allRawColumnsEqual": cached == fresh,
                        "cachedOhlcvSha256": object_sha(cached_ohlcv),
                        "independentOhlcvSha256": object_sha(fresh_ohlcv),
                        "independentFetchedAt": now()})
        if not matches:
            raise ValueError(f"Independent raw OHLCV anchor mismatch {symbol} {label}")
    result = {"symbol": symbol, "path": str(canonical.relative_to(ROOT)), "sha256": sha(canonical),
              "rawRowsSha256": object_sha(rows), "rowCount": len(rows),
              "start": START, "endExclusive": END, "firstOpen": bt.iso(rows[0][0]),
              "lastOpen": bt.iso(rows[-1][0]), "lastClose": bt.iso(rows[-1][6]),
              "allCandlesClosed": True, "gaps": 0, "duplicates": 0, "invalidOhlcv": 0,
              "overlappingCacheRowsIdentical": overlap_count,
              "sourceParts": provenance, "canonicalCacheReused": reuse, "anchorChecks": anchors}
    print(f"AUDIT_PASSED {symbol}", flush=True)
    return result


def main():
    started = now()
    current_ms = int(time.time() * 1000)
    if END > current_ms // bt.HOUR * bt.HOUR:
        raise ValueError("Research end includes a future or currently open candle")
    CANDLES.mkdir(parents=True, exist_ok=True)
    symbols, errors = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {pool.submit(build_symbol, symbol): symbol for symbol in SYMBOLS}
        for future in as_completed(pending):
            symbol = pending[future]
            try:
                symbols.append(future.result())
            except Exception as exc:
                errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
                print(f"AUDIT_FAILED {symbol}: {exc}", flush=True)
    manifest = {"schemaVersion": 1, "auditStartedAt": started, "auditCompletedAt": now(),
                "source": ENDPOINT, "interval": "1h", "start": START, "endExclusive": END,
                "startIso": bt.iso(START), "endExclusiveIso": bt.iso(END),
                "expectedRowsPerSymbol": (END - START) // bt.HOUR,
                "status": "passed" if len(symbols) == len(SYMBOLS) and not errors else "failed",
                "symbols": sorted(symbols, key=lambda x: SYMBOLS.index(x["symbol"])),
                "requests": sorted(REQUESTS, key=lambda x: (x["requestedAt"], x["url"])),
                "errors": errors,
                "limitations": [
                    "Anchor refetch is independent HTTP retrieval from the same Binance source, not cross-venue validation.",
                    "Saved raw rows are not immutable exchange-signed observations; source revisions outside sampled anchors remain possible.",
                    "2026-09-17 through 2026-09-20 has already been observed in forward monitoring and is not untouched out-of-sample data.",
                    "Five-symbol universe was selected at the research date; this is not a point-in-time universe selection backtest.",
                    "Historical hourly bars cannot identify intrabar execution order or historical spreads and order-book depth.",
                ]}
    bt.write_json(OUT / "data-manifest.json", manifest)
    print(json.dumps({"status": manifest["status"], "symbols": len(symbols), "errors": errors,
                      "manifest": str(OUT / "data-manifest.json")}), flush=True)
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
