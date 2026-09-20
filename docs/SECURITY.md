# Security Guide

Store credentials in `.env` or a managed secret store, never in source, screenshots or documentation. Restrict dashboard/operator API access behind authenticated network controls. Configure recovery operators explicitly; default configuration permits no emergency reactivation.

Use separate MT5 accounts and database credentials for research, paper, shadow and live environments. Retain append-only audit logs and limit production promoter authority.
