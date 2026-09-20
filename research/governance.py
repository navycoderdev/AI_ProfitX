"""Dataset-v1 governance contracts; no feature generation or training occurs here."""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
import json


class QualityReasonCode(StrEnum):
    EXPECTED_MARKET_CLOSURE="EXPECTED_MARKET_CLOSURE"; POSSIBLE_DATA_GAP="POSSIBLE_DATA_GAP"; DUPLICATE_TIMESTAMP="DUPLICATE_TIMESTAMP"; OUT_OF_ORDER_TIMESTAMP="OUT_OF_ORDER_TIMESTAMP"; INVALID_OHLC="INVALID_OHLC"; RANGE_OUTLIER="RANGE_OUTLIER"; SPREAD_OUTLIER="SPREAD_OUTLIER"; MISSING_VOLUME="MISSING_VOLUME"; INSUFFICIENT_HISTORY="INSUFFICIENT_HISTORY"; BROKER_HISTORY_UNAVAILABLE="BROKER_HISTORY_UNAVAILABLE"; QUARANTINED_INTERVAL="QUARANTINED_INTERVAL"; DEPENDENCY_CONTAMINATION="DEPENDENCY_CONTAMINATION"; ALIGNMENT_CONTEXT_UNAVAILABLE="ALIGNMENT_CONTEXT_UNAVAILABLE"; FUTURE_CONTEXT_LEAKAGE="FUTURE_CONTEXT_LEAKAGE"; DEPENDENCY_METADATA_INCOMPLETE="DEPENDENCY_METADATA_INCOMPLETE"


FATAL_REASONS = {QualityReasonCode.DUPLICATE_TIMESTAMP, QualityReasonCode.OUT_OF_ORDER_TIMESTAMP, QualityReasonCode.INVALID_OHLC, QualityReasonCode.FUTURE_CONTEXT_LEAKAGE}


def quality_status(codes: set[QualityReasonCode]) -> str:
    return "FAIL" if codes & FATAL_REASONS else "WARNING" if codes - {QualityReasonCode.EXPECTED_MARKET_CLOSURE} else "PASS"


@dataclass(frozen=True)
class DatasetSpec:
    version: str = "research-dataset-v1"; execution_timeframe: str = "M5"; context_timeframes: tuple[str,...] = ("M15","M30","H1","H4")
    decision_semantics: str = "M5_CLOSE"; alignment_policy: str = "LATEST_FULLY_CLOSED_AS_OF"; feature_set_version: str = "feature-set-v1"
    quality_policy_version: str = "quality-policy-v1"; quarantine_policy_version: str = "quarantine-policy-v1"; split_policy_version: str = "chronological-v1"

    def canonical(self) -> dict: return asdict(self)


def content_hash(rows: list[dict], spec: DatasetSpec) -> str:
    canonical = {"spec": spec.canonical(), "rows": sorted(rows, key=lambda row: (row.get("symbol",""), row.get("decision_time","")))}
    return sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


FREEZE_GATES = {"ALIGNMENT_PASS","QUALITY_POLICY_PASS","QUARANTINE_APPLIED","FEATURE_VALIDATION_PASS","LEAKAGE_PASS","SPLIT_INTEGRITY_PASS","REPRODUCIBILITY_PASS","MANIFEST_PASS","CONTENT_HASH_PASS"}
def can_freeze(gates: set[str], fatal_quality: bool) -> bool: return not fatal_quality and FREEZE_GATES <= gates


def reason_codes_from_quality(report: dict) -> list[str]:
    """Single adapter from DataQualityEngine output to the governance contract."""
    codes: set[QualityReasonCode] = set()
    for gap in report.get("gaps", ()):
        codes.add(QualityReasonCode(gap.get("classification", "POSSIBLE_DATA_GAP")))
    if report.get("outliers"): codes.add(QualityReasonCode.RANGE_OUTLIER)
    for error in report.get("data_errors", ()):
        reason = error.get("reason") if isinstance(error, dict) else str(error)
        mapping = {"duplicate_timestamp": QualityReasonCode.DUPLICATE_TIMESTAMP,
                   "out_of_order_timestamp": QualityReasonCode.OUT_OF_ORDER_TIMESTAMP,
                   "invalid_ohlc": QualityReasonCode.INVALID_OHLC}
        if reason in mapping: codes.add(mapping[reason])
    if report.get("invalid_ohlc"): codes.add(QualityReasonCode.INVALID_OHLC)
    if report.get("missing_values"): codes.add(QualityReasonCode.MISSING_VOLUME)
    return sorted(code.value for code in codes)


def dependency_audit(definitions: list[object]) -> dict:
    """Audit only registered metadata; never infer a feature's dependencies."""
    complete, incomplete = [], []
    for definition in definitions:
        spec = getattr(definition, "specification", definition)
        name = getattr(definition, "name", spec.get("name", "unnamed"))
        version = getattr(definition, "version", spec.get("version"))
        metadata = spec.get("dependency", spec)
        if not all(key in metadata for key in ("source_timeframe", "lookback", "warmup")):
            incomplete.append({"name": name, "version": version, "reason_code": QualityReasonCode.DEPENDENCY_METADATA_INCOMPLETE.value})
            continue
        if not isinstance(metadata["lookback"], int) or metadata["lookback"] < 0 or not isinstance(metadata["warmup"], int) or metadata["warmup"] < 0:
            incomplete.append({"name": name, "version": version, "reason_code": QualityReasonCode.DEPENDENCY_METADATA_INCOMPLETE.value})
        else:
            complete.append({"name": name, "version": version, **{key: metadata[key] for key in ("source_timeframe", "lookback", "warmup")}})
    return {"complete": complete, "incomplete": incomplete, "freeze_blocked": bool(incomplete)}


TIMEFRAME_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}
def contaminated_decision_times(mask_start: datetime, mask_end: datetime, source_timeframe: str, lookback: int,
                                warmup: int, decision_times: list[datetime], execution_timeframe: str = "M5") -> list[datetime]:
    """Return decision times whose historical dependency window intersects [start, end)."""
    if source_timeframe not in TIMEFRAME_SECONDS or execution_timeframe not in TIMEFRAME_SECONDS:
        raise ValueError("Unsupported timeframe for dependency calculation.")
    if mask_start.tzinfo is None or mask_end.tzinfo is None or mask_start >= mask_end:
        raise ValueError("Mask interval must be ordered timezone-aware timestamps.")
    horizon = timedelta(seconds=TIMEFRAME_SECONDS[source_timeframe] * (lookback + warmup + 1))
    return [at for at in decision_times if at.tzinfo and at - horizon < mask_end and at >= mask_start]
