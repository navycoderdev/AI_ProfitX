from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from config.settings import Settings
from database.base import Base
from database import models  # noqa: F401 - registers metadata


def create_database_engine(settings: Settings):
    if settings.database_url.startswith("sqlite:///"):
        Path("data").mkdir(exist_ok=True)
        return create_engine(settings.database_url, connect_args={"check_same_thread": False}, future=True)
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


def create_session_factory(settings: Settings) -> sessionmaker[Session]:
    return sessionmaker(bind=create_database_engine(settings), autoflush=False, autocommit=False)


def initialize_database(settings: Settings) -> sessionmaker[Session]:
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def check_database(session_factory: sessionmaker[Session]) -> bool:
    with session_factory() as session:
        session.execute(text("SELECT 1"))
    return True


def session_dependency(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    with factory() as session:
        yield session
