from sqlalchemy import DateTime
from sqlalchemy.orm import configure_mappers

from app.db.models import Base


def test_all_foundation_models_have_identity_and_timestamps() -> None:
    configure_mappers()
    assert len(Base.metadata.tables) == 11
    for table in Base.metadata.tables.values():
        assert {"id", "created_at", "updated_at"} <= set(table.c.keys())
        assert isinstance(table.c.created_at.type, DateTime) and table.c.created_at.type.timezone
        assert isinstance(table.c.updated_at.type, DateTime) and table.c.updated_at.type.timezone
