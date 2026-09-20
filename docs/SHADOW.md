# Brain-v2 forward Shadow observation

The Milestone 3 observer is a separate, read-only MT5 process. It loads only the locked Brain-v2 artifact and verifies its SHA-256, Dataset v3 lineage, 22 ordered features, and confidence policy before reading market data. `ALLOW_LIVE_TRADING` must remain false. Brain-v2 remains `CANDIDATE`.

Run a bounded observation window from the repository root:

```powershell
$env:TRADING_MODE = 'SHADOW'
& .venv/Scripts/python.exe -m scripts.run_shadow --duration-seconds 3600 --poll-seconds 15
```

The process reads EURUSD, GBPUSD, USDJPY, and XAUUSD through `MT5RuntimeGateway.candles` and `account_snapshot`. It has no order service or execution orchestrator. It ignores forming M5 and higher timeframe bars. The first service start time is persisted; old historical candles cannot be converted into forward Shadow decisions to satisfy a runtime gate. A restart keeps the original start time and the database uniqueness constraint prevents duplicate model/symbol/decision-time records.

Decisions and their feature snapshots are persisted in `shadow_decisions` and `feature_snapshots`. Outcomes are stored separately in `shadow_outcomes` only after 12 subsequent valid closed M5 bars are available. No `TradeMemory` or PAPER execution is created. Hypothetical P&L stays null without a genuine executable entry and approved research exit policy.

The current strategy architecture does not supply a hard SL/TP for Brain-v2. The independent Risk Engine therefore blocks actionable proposals with `INVALID_STOP`; this is recorded instead of inventing stop prices. A passing observational risk result would still never submit an order.

The Control Center reads `/api/control/live-shadow` and shows real persisted decisions, counts, risk reasons, outcome status, and order status. A closed broker session may leave all decision counts at zero. That means the real forward runtime gate is still pending; do not backfill historical decisions or claim Milestone 3 complete.
