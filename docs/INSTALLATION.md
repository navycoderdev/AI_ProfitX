# Installation

Use Python 3.11+ on Windows for the MetaTrader5 Python package. Create `.venv`, install `requirements.txt`, copy `.env.example` to `.env`, then run `python main.py`.

Keep secrets only in `.env`; it is ignored by git. Run `python -m pytest -q` before changing modes. SQLite is suitable for local development; configure PostgreSQL for durable multi-process deployments.
