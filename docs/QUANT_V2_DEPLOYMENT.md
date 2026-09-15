# V2 deployment record — 2026-09-15

V2 was initialized at **10:44:49 UTC / 17:44:49 UTC+7** with version
`v2-5cb4bb2b37123eda`. Its first eligible market hour is slot `497075`
(11:00 UTC); collection is scheduled at **11:02 UTC / 18:02 UTC+7** and both
consumers at 11:02:15 UTC. The preflight market event belongs to the previous
hour and was not traded.

Both new accounts opened with $50 cash, zero positions, zero fills and zero
fees. Both backends and all three timers were active at verification. V1 timers
and backends were disabled; the public AI endpoint remains port 3000 and the
control endpoint remains loopback port 3001, now serving the new V2 databases.

## Preserved V1 state

Final common-quote mark at 10:44:39 UTC:

| Portfolio | Cash | Equity | Fees paid | Fills / closed round trips |
| --- | ---: | ---: | ---: | ---: |
| AI V1 | $49.930520539 | $49.930520959 | $0.009940461 | 2 / 1 |
| Control V1 | $49.852780347 | $49.852781391 | $0.019872653 | 4 / 2 |

Tiny residual quantities remain in the old ledgers and their valuations.
These figures are historical V1 outcomes; they are not included in V2 performance.

The original database and runner-state paths remain intact. A separate root-only
archive at `/opt/ai-paper-v1-archive-20260915T1046Z` includes:

- Consistent SQLite backups with integrity checks and full ledger reconciliation.
- The V1 source release, runner states, original systemd units and environment.
- The final V1 report, V2 opening manifest, and V2 preflight report.

No pending V1 request existed at the switch. The archive's label is an identifier;
the recorded context/manifest timestamps above are the actual capture times.

## Verification evidence

- 81 TypeScript tests passed, including V2 marked-equity loss, reducing sells
  after daily loss, stale/incomplete context, quote/version mismatch and replay.
- 49 Python runner tests passed, including independent control execution during
  an AI outage, common sizing/exits, immutable/stale events and durable recovery.
- Typecheck, lint, build and whitespace checks passed.
- Disposable real HTTP/SQLite smoke passed on the VPS, including a synthetic
  AI outage, $5 control BUY, restart/replay, isolation, reporting and reconciliation.
  Evidence: `/opt/ai-paper-v2-stage-20260915/evidence/paired-smoke-dyukgzfe/verification.json`.
- The actual dedicated-user Codex entry-filter invocation returned valid output
  with `submitted=false`. Its synthetic proposal was never posted to a backend.
  Evidence: `/home/trade-agent/paper-v2/diagnostics/entry-ca175f9156004a0e9b52c8456f7dd2b6/verification.json`.
- Both live V2 ledgers reconciled at $50 and zero trades. A manually started
  preflight of each systemd consumer correctly waited for the next eligible hour.

This record verifies deployment and execution controls, not profitability. The
first scheduled market decision was still pending when this record was written.
For current results, regenerate the [paired report and reconciliation](QUANT_V2.md#run-and-inspect).
