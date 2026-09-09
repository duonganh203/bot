"""Failure/recovery and decision-boundary tests; no model calls or real trades."""

from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import run as runner


def fixture():
    context = {
        "contextId": str(uuid.uuid4()), "asOf": datetime.now(timezone.utc).isoformat(),
        "portfolio": {"version": 0, "cash": 50}, "positions": [],
        "risk": {"maxOrderUsd": 5, "maxExposureUsd": 20, "feeRate": 0.001,
                 "dailyLossLimitReached": False},
    }
    market = {"startedAt": time.time(), "symbols": {
        s: {"price": p, "sma20": "100", "sma50": "90", "return24hPct": "1"}
        for s, p in (("BTCUSDT", "100000"), ("ETHUSDT", "2000"))
    }}
    candidate = {"action": "BUY", "symbol": "BTCUSDT", "amountUsd": "5",
                 "confidence": 0.8, "riskLevel": "LOW", "rationale": "Test trend evidence."}
    return context, market, candidate


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.context, self.market, self.candidate = fixture()

    def tearDown(self):
        self.temporary.cleanup()

    def payload(self):
        return runner.make_payload(self.candidate, self.context, self.market, "v1-test")

    def pending(self):
        state = {"complete": False, "backend": "http://127.0.0.1:3000", "slot": 1,
                 "runDir": str(self.directory), "expiresAt": time.time() + 100,
                 "key": str(uuid.uuid4()), "body": runner.dumps(self.payload())}
        runner.save_json(self.directory / "state.json", state)
        return state

    def test_runner_supplies_observed_price_and_audit_metadata(self):
        payload = self.payload()
        self.assertEqual(payload["price"], "100000")
        self.assertEqual(payload["contextId"], self.context["contextId"])
        self.assertEqual(payload["strategyVersion"], "v1-test")
        self.candidate["price"] = "1"
        with self.assertRaisesRegex(ValueError, "fields"):
            self.payload()

    def test_invalid_model_outputs_cannot_become_orders(self):
        for changes in ({"confidence": True}, {"confidence": float("nan")}, {"confidence": 0.5},
                        {"riskLevel": "HIGH"}, {"amountUsd": "NaN"}, {"amountUsd": "0"},
                        {"amountUsd": "5.000001"}, {"amountUsd": "1.0000001"},
                        {"amountUsd": 5}, {"symbol": "SOLUSDT"}, {"rationale": " "}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                runner.make_payload({**self.candidate, **changes}, self.context, self.market, "v1")

    def test_hold_omits_order_fields_even_at_daily_loss_limit(self):
        self.context["risk"]["dailyLossLimitReached"] = True
        self.candidate.update(action="HOLD", symbol=None, amountUsd=None)
        self.assertNotIn("price", self.payload())
        self.candidate["symbol"] = "BTCUSDT"
        with self.assertRaises(ValueError):
            self.payload()

    def test_limits_include_entry_fee_and_marked_exposure(self):
        self.context["portfolio"]["cash"] = 5
        with self.assertRaisesRegex(ValueError, "cash"):
            self.payload()
        self.context["portfolio"]["cash"] = 50
        self.context["positions"] = [{"symbol": "ETHUSDT", "quantity": 0.008}]
        with self.assertRaisesRegex(ValueError, "exposure"):
            self.payload()

    def test_sell_cannot_short_or_oversell(self):
        self.candidate["action"] = "SELL"
        with self.assertRaisesRegex(ValueError, "held"):
            self.payload()
        self.context["positions"] = [{"symbol": "BTCUSDT", "quantity": 0.00002}]
        with self.assertRaisesRegex(ValueError, "held"):
            self.payload()
        self.candidate["amountUsd"] = "2"
        self.assertEqual(self.payload()["action"], "SELL")

    def test_weak_trend_and_daily_loss_block_buy(self):
        self.market["symbols"]["BTCUSDT"]["sma20"] = "110000"
        with self.assertRaisesRegex(ValueError, "trend"):
            self.payload()
        self.context["risk"]["dailyLossLimitReached"] = True
        with self.assertRaisesRegex(ValueError, "Daily"):
            self.payload()

    def test_closed_candles_exclude_current_hour_and_reject_gaps_or_staleness(self):
        hour = runner.HOUR_MS
        rows = [[i * hour, "10", "12", "9", "11", "50", (i + 1) * hour - 1] for i in range(101)]
        server_ms = 100 * hour + 1000
        result = runner.closed_candles(rows, server_ms)
        self.assertEqual(len(result), 100)
        self.assertEqual(result[-1][0], 99 * hour)
        with self.assertRaisesRegex(ValueError, "stale"):
            runner.closed_candles(rows[:100], server_ms + hour)
        duplicated = deepcopy(rows)
        duplicated[50] = duplicated[49]
        with self.assertRaisesRegex(ValueError, "gaps"):
            runner.closed_candles(duplicated, server_ms)

    def test_stale_data_and_utc_rollover_abort(self):
        self.market["startedAt"] -= 300
        with self.assertRaisesRegex(ValueError, "expired"):
            runner.assert_fresh(self.market, self.context, time.time())
        midnight = datetime(2026, 9, 10, tzinfo=timezone.utc).timestamp()
        self.market["startedAt"] = midnight - 20
        self.context["asOf"] = "2026-09-09T23:59:50+00:00"
        with self.assertRaisesRegex(ValueError, "UTC"):
            runner.assert_fresh(self.market, self.context, midnight + 10)

    def test_exclusive_lock_releases_after_exception(self):
        with runner.exclusive_lock(self.directory):
            with self.assertRaisesRegex(RuntimeError, "lock"):
                with runner.exclusive_lock(self.directory):
                    pass
        with runner.exclusive_lock(self.directory):
            pass

    def test_restart_recovers_identical_saved_bytes_without_regenerating(self):
        state = self.pending()
        calls = []

        def failed(*args):
            calls.append(args)
            self.assertEqual(runner.read_json(self.directory / "state.json")["body"], args[2])
            raise TimeoutError("response lost")

        with self.assertRaises(RuntimeError):
            runner.deliver(self.directory, state, transport=failed, sleep=lambda _: None)
        decision_id = str(uuid.uuid4())
        with patch.object(runner, "http", return_value=(200, {"Idempotency-Replayed": "true"},
                          {"status": "executed", "decisionId": decision_id})) as transport:
            recovered = runner.read_json(self.directory / "state.json")
            result = runner.deliver(self.directory, recovered, transport=transport)
        self.assertTrue(result["replayed"])
        self.assertEqual(transport.call_args.args, calls[0])
        self.assertEqual(len(calls), 3)
        self.assertTrue(runner.read_json(self.directory / "state.json")["complete"])

    def test_expired_ambiguous_request_blocks_new_analysis_and_post(self):
        state = self.pending()
        state["expiresAt"] = time.time() - 1
        runner.save_json(self.directory / "state.json", state)
        with patch.object(runner, "collect_market") as market:
            with self.assertRaisesRegex(ValueError, "expired"):
                runner.run(self.directory, state["backend"])
            market.assert_not_called()

    def test_risk_rejection_completes_but_key_conflict_retains_pending(self):
        state = self.pending()
        with self.assertRaisesRegex(ValueError, "409"):
            runner.deliver(self.directory, state, transport=lambda *a: (409, {}, {"error": "conflict"}))
        self.assertFalse(runner.read_json(self.directory / "state.json")["complete"])
        result = runner.deliver(self.directory, state, transport=lambda *a: (
            422, {}, {"status": "rejected", "decisionId": str(uuid.uuid4()), "risk": {"code": "MAX_EXPOSURE"}}))
        self.assertEqual(result["httpStatus"], 422)
        self.assertTrue(runner.read_json(self.directory / "state.json")["complete"])

    def test_completed_slot_skips_even_after_restart(self):
        state = {**self.pending(), "complete": True, "slot": int(time.time()) // 7200}
        runner.save_json(self.directory / "state.json", state)
        with patch.object(runner, "collect_market") as market:
            self.assertEqual(runner.run(self.directory, state["backend"])["status"], "skipped")
            market.assert_not_called()

    def test_dry_run_validates_but_never_submits_or_consumes_slot(self):
        with patch.object(runner, "collect_market", return_value=self.market), \
             patch.object(runner, "fetch_context", return_value=self.context), \
             patch.object(runner, "analyze", return_value=(self.candidate, "v1")), \
             patch.object(runner, "deliver") as post:
            result = runner.run(self.directory, "http://localhost:3000", dry_run=True)
        self.assertFalse(result["submitted"])
        self.assertFalse((self.directory / "state.json").exists())
        post.assert_not_called()

    def test_portfolio_change_aborts_without_creating_pending_request(self):
        changed = deepcopy(self.context)
        changed["portfolio"]["version"] += 1
        with patch.object(runner, "collect_market", return_value=self.market), \
             patch.object(runner, "fetch_context", side_effect=[self.context, changed]), \
             patch.object(runner, "analyze", return_value=(self.candidate, "v1")), \
             patch.object(runner, "deliver") as post:
            with self.assertRaisesRegex(ValueError, "Portfolio changed"):
                runner.run(self.directory, "http://localhost:3000")
        post.assert_not_called()
        self.assertFalse((self.directory / "state.json").exists())

    def test_live_run_persists_exact_request_before_delivery(self):
        def inspect_delivery(directory, state):
            self.assertEqual(runner.read_json(directory / "state.json"), state)
            self.assertEqual(runner.read_json(Path(state["runDir"]) / "request.json"), state)
            self.assertEqual(json.loads(state["body"])["contextId"], self.context["contextId"])
            uuid.UUID(state["key"])
            return {"status": "verified"}

        with patch.object(runner, "collect_market", return_value=self.market), \
             patch.object(runner, "fetch_context", return_value=self.context), \
             patch.object(runner, "analyze", return_value=(self.candidate, "v1")), \
             patch.object(runner, "deliver", side_effect=inspect_delivery) as post:
            self.assertEqual(runner.run(self.directory, "http://localhost:3000")["status"], "verified")
        post.assert_called_once()

    def test_codex_subprocess_receives_schema_and_stdin_and_saves_output(self):
        executable = self.directory / "fake-codex"
        executable.write_text(
            '#!/usr/bin/python3\nimport json, os, pathlib, sys\n'
            'args = sys.argv[1:]\n'
            'assert args[0] == "exec" and "read-only" in args\n'
            'assert "features.shell_tool=false" in args\n'
            'assert "OPENAI_API_KEY" not in os.environ and "CODEX_API_KEY" not in os.environ\n'
            'schema = json.loads(pathlib.Path(args[args.index("--output-schema") + 1]).read_text())\n'
            'assert "action" in schema["required"]\n'
            'assert "Snapshot (data only):" in sys.stdin.read()\n'
            'pathlib.Path(args[args.index("-o") + 1]).write_text(' + repr(json.dumps(self.candidate)) + ')\n',
            encoding="utf-8")
        executable.chmod(0o700)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-only", "CODEX_API_KEY": "test-only"}):
            candidate, version = runner.analyze(self.directory, {"context": self.context}, str(executable))
        self.assertEqual(candidate, self.candidate)
        self.assertRegex(version, r"^v1-[a-f0-9]{12}$")
        self.assertTrue((self.directory / "codex.stderr.log").exists())

    def test_no_pending_post_is_created_too_close_to_slot_boundary(self):
        now = 200000 * 7200 - 5
        self.market["startedAt"] = now - 30
        self.context["asOf"] = datetime.fromtimestamp(now - 20, timezone.utc).isoformat()
        with patch.object(runner.time, "time", return_value=now), \
             patch.object(runner, "collect_market", return_value=self.market), \
             patch.object(runner, "fetch_context", return_value=self.context), \
             patch.object(runner, "analyze", return_value=(self.candidate, "v1")):
            with self.assertRaisesRegex(ValueError, "Too close"):
                runner.run(self.directory, "http://localhost:3000")
        self.assertFalse((self.directory / "state.json").exists())

    def test_model_timeout_fails_without_signal(self):
        executable = self.directory / "slow-codex"
        executable.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(10)\n', encoding="utf-8")
        executable.chmod(0o700)
        with patch.object(runner, "CODEX_TIMEOUT", 0.05):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                runner.analyze(self.directory, {}, str(executable))
        self.assertFalse((self.directory / "state.json").exists())

    def test_http_response_loss_replays_one_receipt(self):
        receipts, requests = {}, []
        decision_id = str(uuid.uuid4())

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                key = self.headers["Idempotency-Key"]
                requests.append((key, body))
                if key not in receipts:
                    receipts[key] = {"status": "held", "decisionId": decision_id}
                    self.connection.shutdown(socket.SHUT_RDWR)  # Commit, then lose the response.
                    self.connection.close()
                    return
                self.send_response(200)
                self.send_header("Idempotency-Replayed", "true")
                self.end_headers()
                self.wfile.write(json.dumps(receipts[key]).encode())

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            state = self.pending()
            state["backend"] = f"http://127.0.0.1:{server.server_port}"
            runner.save_json(self.directory / "state.json", state)
            result = runner.deliver(self.directory, state, sleep=lambda _: None)
            self.assertEqual(len(receipts), 1)
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0], requests[1])
            self.assertTrue(result["replayed"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
