from contextlib import contextmanager
from typing import Generator

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine

from app.core.config import settings

engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def tenant_session(tenant_id: str) -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        # Make tenant identity available to PostgreSQL RLS policies.
        session.execute(text("SELECT set_config('app.current_tenant', :tenant_id, true)"), {"tenant_id": tenant_id})
        yield session
    finally:
        session.close()
