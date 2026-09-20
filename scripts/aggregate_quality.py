"""Create a real aggregate data-quality report from stored historical candles."""
import json
from pathlib import Path
from config.settings import get_settings
from data.operations import aggregate_quality
from data.storage import RawMarketDataRepository
from database.session import initialize_database

if __name__ == "__main__":
    result = aggregate_quality(RawMarketDataRepository(initialize_database(get_settings())), Path("reports/data_quality"))
    print(json.dumps({"run_id": result["run_id"], "overall_status": result["overall_status"], "rows": len(result["coverage"])}, indent=2))
