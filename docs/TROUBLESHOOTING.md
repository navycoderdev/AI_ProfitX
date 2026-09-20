# Troubleshooting

Check `/api/health` and `/api/control/system-health` first. Review `/api/control/audit-log` and operational alerts for MT5 disconnection, stale feed, abnormal spread, execution failure, model inference failure, database failure or state mismatch.

For an execution timeout, do not resend manually; use reconciliation. For stale data or database failure, keep new orders blocked until health is restored and the cause is documented.
