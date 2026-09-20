from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from database.models import FeatureDefinition


class FeatureRegistry:
    """Feature contracts are immutable by `(name, version)` once registered."""
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def register(self, name: str, version: str, specification: dict) -> FeatureDefinition:
        with self.sessions() as session:
            existing = session.scalar(select(FeatureDefinition).where(FeatureDefinition.name == name,
                FeatureDefinition.version == version))
            if existing:
                if existing.specification != specification:
                    raise ValueError("Feature definition version already exists with different specification.")
                return existing
            definition = FeatureDefinition(name=name, version=version, specification=specification, active=True)
            session.add(definition)
            session.commit()
            session.refresh(definition)
            return definition

    def definitions(self, version: str | None = None) -> list[FeatureDefinition]:
        with self.sessions() as session:
            statement = select(FeatureDefinition).order_by(FeatureDefinition.name, FeatureDefinition.version)
            if version:
                statement = statement.where(FeatureDefinition.version == version)
            return list(session.scalars(statement))
