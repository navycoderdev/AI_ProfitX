# LIVE Deployment Checklist

- `APP_ENV=production`, `TRADING_MODE=LIVE`, and `ALLOW_LIVE_TRADING=true` are explicitly configured.
- MT5 demo/paper/shadow validation is complete and terminal health is stable.
- Candidate passed multi-criterion Promotion Gate, PAPER and SHADOW stages.
- Authorized operator has recorded gate evidence and promotion permission.
- Risk profile, emergency policy, recovery operators, monitoring and alert routing are reviewed.
- Database backup, audit retention and startup reconciliation are verified.

Never use LIVE as a default mode or bypass the promotion gate.
