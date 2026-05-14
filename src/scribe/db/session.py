"""SQLAlchemy engine + session."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scribe.config import settings
from scribe.db.models import Base

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

__all__ = ["engine", "SessionLocal", "Base"]
