# Database Guide

Database tables preserve raw market observations, feature snapshots, decisions, trade memory, regimes, inference logs, model versions, alerts, audit events and control state. Run Alembic migrations in a deployment workflow; local startup creates metadata for development.

Back up PostgreSQL regularly. Do not manually edit raw observations, production model records, audit rows or emergency control state.
