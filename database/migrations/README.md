# Database migrations

Phase 1 initializes the baseline schema from SQLAlchemy metadata so a fresh
installation starts deterministically. All schema changes after this baseline
must be captured as Alembic revisions before deployment; production databases
must not rely on automatic schema creation for upgrades.
