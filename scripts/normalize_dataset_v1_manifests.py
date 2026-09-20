"""Retire non-canonical Dataset-v1 build attempts without deleting any evidence."""
from sqlalchemy import select

from config.settings import get_settings
from database.models import DatasetManifest
from database.session import initialize_database


def main() -> None:
    sessions = initialize_database(get_settings())
    with sessions() as session:
        manifests = list(session.scalars(select(DatasetManifest).where(DatasetManifest.dataset_version == "research-dataset-v1")
            .order_by(DatasetManifest.usable_rows.desc().nullslast(), DatasetManifest.id.desc())))
        frozen = [item for item in manifests if item.state == "FROZEN"]
        if not frozen: raise RuntimeError("No frozen Dataset v1 build exists to designate as canonical.")
        canonical = frozen[0]
        for item in manifests:
            if item.dataset_id == canonical.dataset_id: continue
            item.state = "RETIRED"
            item.reason_code_summary = {**(item.reason_code_summary or {}), "governance": "SUPERSEDED_CONCURRENT_BUILD_ATTEMPT",
                "canonical_dataset_id": canonical.dataset_id}
        session.commit()
        print({"canonical_dataset_id": canonical.dataset_id, "usable_rows": canonical.usable_rows,
               "retired_dataset_ids": [item.dataset_id for item in manifests if item.dataset_id != canonical.dataset_id]})


if __name__ == "__main__": main()
