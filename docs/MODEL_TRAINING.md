# Model Training Guide

Experiences create time-safe datasets only after outcomes resolve. Candidate training creates a new immutable `Brain-vN` artifact and records lineage, features, metrics and cost assumptions. It does not change the current production model.

Validate candidates chronologically with out-of-sample, walk-forward, stress, Monte Carlo, regime and robustness checks. Promotion is controlled through PAPER and SHADOW; accuracy and historical profit do not guarantee future returns.

## Brain-v1 Milestone 2 OOS evaluation

Run `python -m scripts.simulate_brain_v1_oos` against the local frozen Dataset v1 database. The command verifies the registry artifact SHA-256, frozen dataset hash, feature list, label policy and the locked `confidence-policy-v1` threshold of 0.40. It never imports an MT5 or execution gateway. The immutable artifact is `models/artifacts/Brain-v1.json`; the published summary is `reports/brain_v1_oos.json` and the same result is persisted in `backtest_runs` with `trade_scope=OFFLINE_OOS_SIMULATION_TRADES`.

The simulation predicts only on labeled OOS rows, enters at the next M5 bar's observed open, exits at the label horizon's observed close, and allows one open simulated position per symbol. Position size is 1,000 units. The locked label specification supplies one point of spread and two points of slippage on each side, with point size 0.00001; USDJPY quote P&L is converted to USD at exit close. Profit factor uses net winning and losing trades. Max drawdown uses closed-trade equity from a 10,000 USD starting balance, so intratrade drawdown is not represented. These are explicit research assumptions, not broker fills.

The frozen OOS split contains USDJPY rows only. EURUSD and GBPUSD show `NO_OOS_ROWS`; their zero counts mean no evaluation coverage, not zero trading risk. The model remains `CANDIDATE`; its OOS SHORT F1 of 0.0000 remains visible. Offline OOS simulation trades are separate from runtime PAPER trades and do not authorize deployment.
