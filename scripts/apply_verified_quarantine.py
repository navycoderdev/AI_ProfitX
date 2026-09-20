"""Persist only a specifically evidenced non-session data gap as a Dataset-v1 mask."""
from datetime import datetime, timedelta
import json
from pathlib import Path

from config.settings import get_settings
from database.session import initialize_database
from research.governance import DatasetSpec, QualityReasonCode, TIMEFRAME_SECONDS
from research.repository import DatasetGovernanceRepository


def main() -> None:
    reports = sorted(Path("reports/data_quality").glob("aggregate_*.json"), key=lambda p: p.stat().st_mtime)
    if not reports: raise SystemExit("NOT VERIFIED: no persisted aggregate quality report.")
    evidence = json.loads(reports[-1].read_text(encoding="utf-8"))
    rows = [row for row in evidence.get("coverage", []) if row["symbol"] == "USDJPY" and row["timeframe"] == "M1"]
    gaps = [gap for row in rows for gap in row.get("gaps", []) if gap.get("classification") == "POSSIBLE_DATA_GAP"]
    if len(gaps) != 1: raise SystemExit("NOT VERIFIED: expected exactly one evidenced USDJPY/M1 possible gap.")
    gap = gaps[0]; after = datetime.fromisoformat(gap["after"]); before = datetime.fromisoformat(gap["before"])
    start = after + timedelta(seconds=TIMEFRAME_SECONDS["M1"])
    if start >= before: raise SystemExit("NOT VERIFIED: evidence does not prove a missing M1 interval.")
    repo = DatasetGovernanceRepository(initialize_database(get_settings()))
    repo.register_not_built(DatasetSpec())
    mask = repo.quarantine(policy_version=DatasetSpec().quarantine_policy_version, symbol="USDJPY", timeframe="M1",
        start_time=start, end_time=before, reason_codes=[QualityReasonCode.POSSIBLE_DATA_GAP.value],
        source_quality_run_id=evidence["run_id"])
    print(json.dumps({"mask_id": mask.mask_id, "symbol": mask.symbol, "timeframe": mask.timeframe,
        "start_time": mask.start_time.isoformat(), "end_time": mask.end_time.isoformat(), "state": mask.state,
        "reason_codes": mask.reason_codes, "source_quality_run_id": mask.source_quality_run_id}, default=str))

if __name__ == "__main__": main()
