"""SQLAlchemy engine + session. TODO(task#2): wire up."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scribe.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
