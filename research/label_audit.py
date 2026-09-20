"""Pure fixed-policy label audit before a research dataset is frozen."""
from collections import Counter, defaultdict
from datetime import datetime

from ai.brain_v1 import LABEL_SPEC_V1, _numeric


SPLITS = ("TRAIN", "VALIDATION", "OOS")
CLASSES = ("LONG", "SHORT", "NO_TRADE")


def audit_fixed_labels(candidates: list[dict], close: dict[tuple[str, datetime], float]) -> dict:
    grouped = defaultdict(list)
    for row in candidates:
        _numeric(row["values"])
        grouped[row["symbol"]].append(row)
    classes = {symbol: {split: Counter() for split in SPLITS} for symbol in grouped}
    exclusions = Counter()
    horizon = LABEL_SPEC_V1["horizon_bars"]
    for symbol, rows in grouped.items():
        rows.sort(key=lambda item: item["decision_time"])
        for index, row in enumerate(rows):
            split = row["split"]
            if index + horizon >= len(rows):
                exclusions[("LABEL_HORIZON_UNAVAILABLE", symbol, split)] += 1
                continue
            future = rows[index + horizon]
            if future["split"] != split:
                exclusions[("LABEL_HORIZON_CROSSES_SPLIT", symbol, split)] += 1
                continue
            start = close.get((symbol, datetime.fromisoformat(row["raw_data_cutoff"])))
            end = close.get((symbol, datetime.fromisoformat(future["raw_data_cutoff"])))
            if start is None or end is None or start <= 0:
                exclusions[("LABEL_HORIZON_UNAVAILABLE", symbol, split)] += 1
                continue
            move = (end - start) / start
            label = "LONG" if move >= LABEL_SPEC_V1["long_threshold"] else "SHORT" if move <= LABEL_SPEC_V1["short_threshold"] else "NO_TRADE"
            classes[symbol][split][label] += 1
    distribution = {symbol: {split: {label: classes[symbol][split][label] for label in CLASSES}
                             for split in SPLITS} for symbol in sorted(grouped)}
    # A class with <1% in any partition is too sparse for meaningful four-symbol evaluation.
    pathological = []
    for symbol, partitions in distribution.items():
        for split, counts in partitions.items():
            total = sum(counts.values())
            for label, count in counts.items():
                if total == 0 or count / total < .01:
                    pathological.append({"symbol": symbol, "split": split, "class": label,
                                         "count": count, "total": total})
    return {"label_policy": LABEL_SPEC_V1, "class_distribution": distribution,
            "exclusions": {"|".join(key): value for key, value in sorted(exclusions.items())},
            "included_cross_split_labels": 0, "pathological_classes": pathological,
            "usable_for_training": not pathological}
