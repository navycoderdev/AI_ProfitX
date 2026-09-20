# MT5 Adaptive AI Trading Platform

Institutional-style research, paper, shadow and controlled-live trading platform. It separates market data, features, regimes, AI proposals, independent risk, execution, trade memory, candidate training, validation and model deployment.

LIVE is disabled by default. An AI model only proposes; it cannot send an MT5 order, override risk, change production weights or promote itself.

## Quick start

1. Create `.env` from `.env.example` and keep `TRADING_MODE=BACKTEST`.
2. Create the virtual environment and install `requirements.txt`.
3. Run `python main.py`.
4. Visit `http://127.0.0.1:8000/api/health` and `/api/control/overview`.

Run verification with `python -m pytest -q`.

See [architecture](docs/ARCHITECTURE.md), [installation](docs/INSTALLATION.md), [MT5 setup](docs/MT5_SETUP.md), and the mode guides under `docs/`.
