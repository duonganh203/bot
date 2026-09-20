# AI in quantitative trading: evidence notes

Research cutoff: 2026-09-20. Sources below were opened during this session. This is a source notebook, not a new backtest or a claim of live trading profitability. No trading configuration was changed.

## Findings to carry into the main report

The strongest public institutional evidence supports AI as a research and feature-production system. A strong LLM can generate hypotheses, implement research, read unstructured information and check experiments. Forecasts, portfolio construction, risk limits and execution still need measurable objectives and independent validation. Public descriptions disclose processes, not enough information to reproduce a hedge fund's proprietary alpha or attribute its live P&L to LLMs.

## Primary-source ledger

### 1. Man Group — AlphaGPT, 13 November 2025

[What AI Can (and Can't Yet) Do for Alpha](https://www.man.com/insights/what-ai-can-do-for-alpha)

Company disclosure: AlphaGPT separates hypothesis generation, Python implementation using internal research tools/data, and evaluation. Man reports signals passing its normal research thresholds, with investment-committee scrutiny and technology review. The article explicitly discusses multiple testing, hallucinations, implementation drift and human oversight. It does not publish an independently verified live return series attributable to AlphaGPT. Its most successful application at that date was systematic equity research.

Transfer: separate proposer, implementer and evaluator; maintain one experiment registry, code review and locked evaluation criteria. This is a workflow example, not a turnkey strategy or a return forecast.

### 2. Two Sigma — 2026 outlook, 12 January 2026

[AI in Investment Management: 2026 Outlook (Part I)](https://www.twosigma.com/articles/ai-in-investment-management-2026-outlook-part-i/)

Company disclosure: leaders emphasize AI's effect on research productivity, document understanding, combining text and structured data, and tools integrated with company context. Their stated bottleneck is increasingly evaluating ideas rather than finding enough ideas. The article is strategic commentary, not an experiment demonstrating profitable autonomous LLM trading.

Transfer: increase the quality and throughput of evaluation along with idea generation; otherwise a faster agent mainly creates more opportunities to overfit.

### 3. Two Sigma — feature engineering, 9 July 2026

[Anything Can Be Language Now: My Thoughts on the Future of Features Research](https://www.twosigma.com/articles/anything-can-be-language-now-my-thoughts-on-the-future-of-features-research/)

Company disclosure by Ben Wellington: edge depends on how data becomes predictive features, with many small signals combined and hypotheses discarded when unsupported. LLM outputs can themselves become datasets; they reduce the cost of investigating text, events and other modalities. The author also warns that poorly designed automation can homogenize research. This establishes a current research direction, not the standalone profitability of any particular feature.

Transfer: extract dated, attributable event variables from primary announcements; test incremental value beyond price/volume/funding baselines.

### 4. Lopez-Lira and Tang — financial-news information, initial preprint 15 April 2023; journal page available in 2026

[Can ChatGPT Forecast Stock Price Movements?](https://arxiv.org/abs/2304.07619) and [Journal of Financial Economics version](https://www.sciencedirect.com/science/article/pii/S0304405X26001066)

Academic evidence: the journal page describes U.S. stock headlines from October 2021 to May 2024, finding that language-model assessments contain information about immediate reactions and subsequent drift. Immediate-reaction accuracy is not a directly tradable return because that move may already have happened. Reported predictability differs across news topics and weakens over the sample. This is historical empirical evidence, not a current live product track record, and does not establish that the same feature works in liquid crypto.

Transfer: define an actionable event time, feature completion time and executable entry price. Study delayed response after extraction latency and full costs, rather than score whether AI can explain an already completed move.

### 5. FinBen — 20 February 2024; NeurIPS 2024 benchmark

[FinBen: A Holistic Financial Benchmark for Large Language Models](https://arxiv.org/abs/2402.12659)

Benchmark: 36 datasets and 24 financial tasks. It tests extraction, text analysis, reasoning and trading, among other tasks. Findings distinguish strong extraction/text analysis from more difficult forecasting and reasoning. Results describe the evaluated models and datasets; they do not rank today's strongest models or prove tradeable alpha.

Transfer: test the extraction layer independently on manually labelled documents. A good financial-language score is not a substitute for a prospective net-return evaluation.

### 6. StockBench — 2 October 2025, v1

[StockBench](https://arxiv.org/html/2510.02209v1)

Benchmark: a historical simulation over 20 selected DJIA stocks, 82 trading days from March 3 to June 30, 2025, with daily decisions, prices, fundamentals and recent news. The paper finds that reasoning-oriented models do not consistently beat instruction-oriented counterparts and that outcomes depend on asset universe and window. This is not documented live-capital performance. I did not find an explicit transaction-cost/slippage setup in the inspected main text; do not assume results are net of realistic implementation costs.

Source inconsistency: the abstract/introduction says most models fail to beat passive holding, but Table 2 and its discussion show most exceed the reported +0.4% baseline. Do not repeat the abstract as a firm empirical conclusion. The safe conclusion is that a short, configuration-dependent simulation cannot establish durable alpha. The paper calls its data contamination-free; this claim is model/version dependent and should not be generalized to newer models replaying the same dates.

### 7. TradingAgents — 28 December 2024; inspected v7, 3 June 2025

[TradingAgents: Multi-Agents LLM Financial Trading Framework](https://arxiv.org/html/2412.20138v7)

Research framework: analyst agents, bull/bear debate, trader and risk-review roles. Reported experiments use a three-month 2024 historical simulation; the paper explains its limited duration through heavy inference/tool cost (11 LLM calls and more than 20 tool calls per prediction). It reports strong historical results, but those are not an audited live track record. Controlling tool inputs by date does not by itself demonstrate that model weights lack later market knowledge. No claim is made here that the framework's current repository has the same performance or capabilities.

Transfer: borrow structured evidence and review separation. Debate quality and natural-language confidence do not replace statistically calibrated forecasts or code-enforced risk limits.

### 8. M2VN — 23 October 2025; ICAIF 2025

[Fusing Narrative Semantics for Financial Volatility Forecasting](https://arxiv.org/abs/2510.20699)

Academic model: combines time-series features and news embeddings from point-in-time language models to forecast volatility. This is a concrete example of text-plus-numerical modelling with temporal integrity as a design requirement. Improved volatility-forecast loss is not automatically improved net investment return.

Transfer: use language features as inputs to supervised numerical models for risk/volatility or return ranking, and test their incremental effect. Keep forecasting accuracy and economic value as separate evaluation targets.

## Proposed application to this project — research inference, not a sourced return claim

1. Offline AI research: read papers and exchange specifications, propose economically motivated hypotheses, implement them and have an independent evaluator inspect timestamps, labels, portfolio accounting and costs. Record every trial, including failures.
2. Event ingestion: store publication time, first observed time, original URL/content hash and revision time. An LLM extracts asset, event type, novelty, confidence and supporting excerpt into a strict schema. Possible crypto inputs include exchange announcements, token-supply schedules and security-incident disclosures; feasibility and data rights still need verification per source.
3. Numerical prediction: begin with simple statistical/ML baselines on price, volume and available derivatives data. Compare adding event features using time-ordered out-of-sample tests. LLM verbal confidence is not a calibrated probability.
4. Deterministic decisions: code converts forecasts and estimated costs into allowed position sizes, enforces exposure/liquidity/risk constraints and executes. An AI-generated explanation cannot override those limits.
5. Prospective measurement: run identical paper lanes with and without the event features. Freeze the model, prompt and policy during each measurement period; log inference latency and cost, invalid outputs, missing data, turnover and execution assumptions. Use fresh future data for historical periods that the current LLM may already know.

Minimum economic hurdle: incremental expected net P&L must exceed additional data, inference and infrastructure costs. With a $50 experimental account, even $1/month of added cost is 2% of capital per month. This is arithmetic, not a forecast or a quoted API price. Event-triggered extraction, caching and offline research are therefore more plausible initial uses than repeated multi-agent debate on every candle.

## Claims explicitly not established

- A general-purpose LLM's stronger reasoning necessarily yields better trading returns.
- Any of the named institutional workflows exposes a reproducible, currently profitable retail strategy.
- A benchmark leaderboard, GitHub popularity or short paper-trading result demonstrates durable live alpha after all costs.
- A news article's nominal publication date alone proves it was accessible at the historical decision time.
- A prompt telling a modern model to ignore future events removes knowledge already embedded in its weights.
