from config.settings import get_settings
from database.session import initialize_database
from research.dataset_v1 import ResearchDatasetV1Builder

if __name__ == "__main__":
    print(ResearchDatasetV1Builder(initialize_database(get_settings())).build_and_freeze())
