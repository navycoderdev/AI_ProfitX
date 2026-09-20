# MT5 Setup

Install and log into the MetaTrader 5 terminal with the intended demo/live account. Set `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`, and optionally `MT5_TERMINAL_PATH` in `.env`; never commit them.

Confirm terminal health and symbols before any paper/shadow workflow. A terminal connection alone does not activate LIVE orders. MT5 order submission still requires LIVE configuration, an approved risk decision, no emergency stop, and the orchestrated lifecycle path.
