# Database Guide

Database tables preserve raw market observations, feature snapshots, decisions, trade memory, regimes, inference logs, model versions, alerts, audit events and control state. Run Alembic migrations in a deployment workflow; local startup creates metadata for development.

Back up PostgreSQL regularly. Do not manually edit raw observations, production model records, audit rows or emergency control state.

# Corrected Research Dataset v2

`python -m scripts.build_research_dataset_v2` freezes `research-dataset-v2` with `chronological-time-v2` membership. Unique UTC decision timestamps define the 70% / 15% / 15% boundaries; every symbol at the same timestamp receives the same split. The manifest counts are reconciled against all persisted `DatasetRow` records before freeze. The command writes the audited counts, boundaries, label exclusions and unchanged v1 hash to `reports/dataset_v2_freeze.json`.

The original `research-dataset-v1` manifest, Brain-v1 artifact and Brain-v1 OOS report are retained for lineage. Brain-v1 was trained on the old defective split and remains a candidate; the v2 freeze does not train, retune or evaluate a model for deployment. Twelve-valid-bar labels crossing TRAIN/VALIDATION or VALIDATION/OOS are excluded from label rows.
