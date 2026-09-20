# MT5 Adaptive AI Trading Platform — Phase 1 Foundation

## Operating boundary

The default mode is BACKTEST. PAPER and SHADOW are isolated operating modes.
LIVE is rejected unless the runtime is production and ALLOW_LIVE_TRADING is
true. No Phase 1 interface submits an order.

## Dependency flow

MT5 Gateway → Market Data → Features → Regime → Strategy or AI Decision → Risk
→ Execution → Position or Portfolio → Trade Memory → Performance and Training
→ Validation → Model Registry.

Each package exposes a typed contract. Future implementations attach to those
contracts without allowing research, paper, or production to share mutable
runtime state.

## Modules

- config: Pydantic settings and operating-mode safety controls.
- core: context, exceptions, structured logging, retry policy, event bus.
- database: SQLAlchemy metadata, sessions, audit log, model-version record,
  and migration policy.
- mt5, data, features, regimes, strategies, ai, risk, execution, portfolio,
  memory, backtesting, training, validation, models: explicit contracts only.
- monitoring: database and MT5 gateway health report.
- api: read-only health and runtime endpoints.
- tests: settings, database, event, context, and health checks.

## Evolution rule

Closed trades create immutable experience records in a later phase. Candidate
models are trained and validated separately. Only a promoted, versioned model
can deploy; no live model may modify itself after an individual trade.
# Phase 2 — MetaTrader 5 integration

The MT5 boundary is deliberately isolated in `mt5/`. `MT5ConnectionManager` owns terminal startup,
authentication, disconnects, heartbeat checks and reconnects.  It is the only component permitted to
create or hold the MetaTrader5 client.

`MT5AccountService`, `MT5SymbolService`, `MT5MarketDataService`, and `MT5PositionService` expose
normalized account, terminal, instrument, tick, OHLCV, positions, pending-order and history reads.
All timestamps passed to the rates service must be UTC-aware.

`MT5OrderService` performs pre-flight checks for terminal availability, tradability, symbol selection,
volume increment/bounds, spread, stop/freeze levels and available margin.  It only permits an order
when both the supplied `TradingContext` and process settings are explicitly `LIVE`. It creates audit
records for submissions, changes, cancellations, closes and reconciliation.

Order outcomes are normalized into `ExecutionResult`, which preserves the requested/fill price and
volume, spread, slippage, tickets, IDs, retcode and UTC execution time. A response that is not a
confirmed MT5 success is treated as uncertain and reconciled against MT5; it is never retried by
submitting another order.

`MT5HealthMonitor` is an operations-facing terminal/account liveness probe. Its reconnect action is
safe because it reconnects the terminal only, never resends an execution request.

# Phase 3 — Market data and features

`RawMarketDataRepository` stores MT5 OHLCV/spread observations and ticks in separate append-only tables.
It detects an already-seen provider observation and keeps the first raw version unchanged. `HistoricalDataCollector`
imports MT5 rates, while `LiveTickCollector` can be scheduled with a stop event for continuous polling.
`CandleBuilder` creates provisional tick-derived bars separately from historical provider bars.

`DataQualityEngine` reports duplicate observations, missing OHLC values, timestamp gaps and anomalous ranges.
`FeatureEngine` is pure and deterministic: every calculation filters data to `timestamp <= as_of`, including
higher-timeframe context. This enforces the no-look-ahead boundary in research, backtests and decisions.
`MultiTimeframeSynchronizer` applies that same single UTC decision boundary to every requested timeframe.

`FeatureRegistry` versions immutable feature definitions. `FeatureSnapshotService` persists the exact values,
definition version, decision timestamp and raw-data cutoff that an AI decision consumed, making a later trade
decision reproducible.

# Phase 4 — Deterministic backtesting

`HistoricalReplayEngine` reveals a strategy only bars closed at the decision time. A signal is queued and
`SimulatedBroker` fills it on the next bar's open, with configurable bid/ask spread, commission, slippage,
margin, partial-fill ratio and position limits. Protective exits do not evaluate on the entry bar; if a later
bar reaches both SL and TP, the conservative stop outcome is used.

`PortfolioSimulator` records marked equity and margin. `PerformanceAnalyzer` produces P&L, win/loss,
drawdown, Sharpe-like/Sortino-like, exposure, MFE/MAE, holding-time, cost and slippage metrics. Reports retain
strategy/data/feature versions, symbol, timeframe, range, complete configuration and cost assumptions.
The MA trend, breakout and mean-reversion strategies in `strategies/baselines.py` are engineering benchmarks
only; they make no profitability claim and provide a stable comparator for later AI models.

# Phase 5 — Independent risk boundary

`DeterministicRiskEngine` is the mandatory signal-to-execution boundary. It validates an immutable
`RiskProfile` for allowed mode, spread/slippage, hard stop, risk/reward, sizing, exposure, margin, loss,
drawdown, trade frequency and cooldown before returning `APPROVED`, `REDUCED_SIZE` or `REJECTED` with exact
reason codes. `MT5OrderService.submit_market` requires that approved decision and uses its approved size;
rejection leaves no executable order for the MT5 layer.

Drawdown, daily/weekly loss and consecutive-loss breaches are account-level circuit breakers: they trigger
`EmergencyKillSwitch`, append an audit incident, publish a `risk.incident` monitoring event and require the
configured recovery procedure plus a named human approver. AI has no control over these limits, the switch,
exposure limits, environment mode or model-promotion permissions.

# Phase 6 — Trade lifecycle and execution

`TradeOrchestrator` is the single entry point from approved signal to MT5 execution. It records deterministic
`OrderStateMachine` transitions from signal and risk validation through submission, acknowledgement, fill,
management and closure. Its idempotency key maps repeated delivery of a signal to the same `TradeRecord` and
never submits the order again.

Each trade owns a position through strategy ID, model ID, trade ID and MT5 magic number. `PositionManager`
uses a single-writer lease to prevent exit modules from modifying a position concurrently. `ExecutionReconciler`
checks MT5-owned positions before and after execution; a timeout can recover to `POSITION_OPEN` only after MT5
state confirms it. `ExitManager` supports break-even, trailing stop, partial exits and time exits, with all
execution telemetry and lifecycle events audit logged.

# Phase 7 — Experience memory

`ExperienceBuilder` persists every LONG, SHORT and `NO_TRADE` decision with a linked immutable market snapshot,
feature snapshot ID, strategy/model/feature versions, environment, proposal and risk outcome. Executed trades add
requests, fills, slippage, commission, tickets and lifecycle events; closed trades add exit, duration, P&L, costs,
R multiple and MFE/MAE. `reconstruct(decision_id)` returns the complete linked decision, snapshot, feature and
trade record.

`OutcomeLabeler` records context-dependent observed outcomes such as favorable, adverse, neutral or unresolved.
It deliberately leaves `decision_quality` as `UNDETERMINED`: a trade's realized P&L cannot prove that the earlier
decision was correct or incorrect, especially after regime/spread/news-context changes.

# Phase 8 — Market regime intelligence

`DeterministicRegimeDetector` uses only decision-time features to identify trending direction/strength, range,
breakout, volatility, spread/liquidity and session context. Missing or weak evidence returns `UNCERTAIN`, never a
forced market label. `RegimeStabilityManager` requires repeated candidate observations before switching its active
label and records confirmed transitions.

`RegimeResearchPipeline` is a deterministic seeded clustering workflow for offline research only; it cannot replace
the production detector. `RegimeRepository` persists regime history with measurements, confidence, feature/model
versions and decision links. `HistoricalRegimeReport` groups stored decisions and trade outcomes by the regime
captured in each pre-trade market snapshot.

# Phase 9 — AI decision engine

`DatasetBuilder` only accepts feature snapshots whose raw-data cutoff is at or before the decision timestamp and
whose supervised target occurs strictly later. It creates chronological train, validation and untouched
out-of-sample partitions; random time-series splitting is not implemented. `ModelTrainer` fits a deterministic,
inspectable multinomial logistic baseline using train-only normalization, while validation selects a probability
temperature and OOS remains evaluation-only.

`InferenceEngine` consumes a feature snapshot, regime and portfolio context and produces a `TradeProposal` only.
`DecisionPolicy` maps low confidence and the explicit NO_TRADE class to `NO_TRADE`; inference input/output is
persisted in `ModelInferenceLog`. The engine has no MT5 or execution dependency, so every proposed trade must still
pass the independent Phase 5 risk boundary. Model/baseline comparison and classification metrics are explicitly
historical research evidence, never a profitability guarantee.

# Phase 10 — Controlled adaptive learning

`ExperienceDatasetBuilder` derives time-safe supervised rows only from resolved historical experiences. A losing
LONG/SHORT is conservatively labeled `NO_TRADE`; it is never treated as evidence for the unobserved opposite-side
counterfactual. `RetrainingPolicyEngine` triggers only on minimum new data, elapsed schedule, or explicit
performance/data-drift investigations—not a single loss.

`CandidateTrainingService` reserves a new immutable `Brain-vN`, trains a candidate artifact, records full dataset,
period, feature/algorithm/hyperparameter/metric/cost/lineage metadata, then stops at `CANDIDATE`. `ModelRegistry`
enforces the controlled TRAINING → CANDIDATE → VALIDATING → PAPER → SHADOW → APPROVED → PRODUCTION lifecycle,
requires configured promoter authority for production or rollback, and never overwrites a previous production
artifact.

# Phase 11 — Model validation and promotion

`WalkForwardValidator`, `OutOfSampleValidator`, `CostStressTester`, `MonteCarloAnalyzer`,
`RegimePerformanceAnalyzer` and `RobustnessAnalyzer` produce independent evidence on chronological periods,
cost assumptions, bootstrap uncertainty, regime coverage and parameter sensitivity. `ChampionChallengerEngine`
requires a challenger to improve out-of-sample, drawdown, walk-forward and stress evidence—not just total profit.

`PromotionGate` creates an explicit multi-criterion rejection report. Passing offline validation can move a
candidate only to PAPER, then explicitly to SHADOW. `ShadowEvaluationEngine` emits and records hypothetical
inferences from current feature snapshots but has no execution dependency. LIVE promotion requires enough shadow
observations, a configured allow flag, the full validation gate and an authorized registry actor.
