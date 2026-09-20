# Quant research Q-20260920

Vietnamese report: `docs/QUANT_RESEARCH_20260920.md` and
`output/pdf/quant-research-20260920.pdf` from the repository root.
This research is isolated from the paper deployment and never submits signals.

## Reproduction

Use Python 3.12+ and `pip install -r quant/research_20260920/requirements.txt`
in a separate virtual environment. From the repository root:

```text
python -B quant/research_20260920/data_audit.py
python -B quant/research_20260920/replay.py
python -B quant/research_20260920/existing_evidence.py
python -B -m unittest discover -s quant/research_20260920 -p "test_*.py"
python -B -m unittest discover -s quant -p test_backtest.py
python -B quant/research_20260920/verify_results.py
python -B quant/research_20260920/build_report.py
```

Data audit makes public Binance GET requests and preserves existing source caches.
The other steps use local evidence. PDF font defaults are Windows Arial;
on another platform set `QUANT_FONT_DIR` to a directory containing licensed
`arial.ttf`, `arialbd.ttf`, and `ariali.ttf`. No font files are redistributed.

`existing_evidence.py` requires the two saved September 17 results files,
their original BTC/ETH raw caches, and `quant/backtest.py`. The reproducibility
archive includes these inputs, the canonical five-coin caches, and forward evidence.
No credentials are included. Dependencies themselves are not bundled.

## Research design and versions

- `PROTOCOL.initial.md`: original hypotheses and rules before inspecting the
  new 540-run output; SHA-256 matches the original output manifest.
- `PROTOCOL.md`: timing wording clarified to say peaks include prior closes,
  but triggers are checked at decision opens. No threshold or strategy changed.
- `replay-before-diagnostic-fixes.json`: retained initial output in the data folder.
- Final replay fixes queue bookkeeping across dust/re-entry, counts peak
  entry-fee blocks, and rejects malformed/non-finite input. All 540 trade lists
  and returns match the original run exactly; only diagnostics changed.
- `verification.json`: hashes, accounting assertions and before/after identity.

Historical data and coin selection were already observed. This is development
research, not externally preregistered evidence or untouched out-of-sample testing.
Do not tune on these results and describe the same dates as fresh validation.

## Interpretation

10 windows = continuous 24 months + 8 non-overlapping three-month startup
scenarios + recent 19 days. Startup windows begin flat with a fresh $50; they
must not be spliced into one compounded policy. 3 universe/cap settings x
6 policies/benchmarks x 3 cost cases x 10 windows = 540 runs, with overlapping
observations and repeated benchmark paths. These are not 540 independent trials.

`blockedHours` counts hourly loss-related entry restrictions, including fees
near the peak floor, whether or not an entry signal is present. It excludes
capacity blocks and absence of a trend. `firstLossHalt` is the first such
restriction and can be temporary. `peakBlockedHours` counts the permanently
latched peak guard. Exit backlog can include tiny precision-boundary dust.

The percentile intervals on saved proxy daily returns are conditional diagnostics;
resampling does not rerun risk gates and is not a predictive or multiple-testing
adjusted inference. No AI historical performance is inferred from rule backtests.
