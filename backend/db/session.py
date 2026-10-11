"""Engine, sessions, and migration bootstrap."""

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend import config

_connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
if config.DATABASE_URL.startswith("sqlite:///"):
    Path(config.DATABASE_URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _record) -> None:
        # SQLite ignores foreign keys (and so ON DELETE SET NULL / CASCADE) unless
        # asked on every connection. Postgres always enforces them.
        dbapi_connection.execute("PRAGMA foreign_keys=ON")


@contextmanager
def session_scope():
    """Commit on success, roll back on error, always close."""
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    """FastAPI dependency."""
    with session_scope() as session:
        yield session


def run_migrations() -> None:
    """Bring the schema to head. Safe to call on every startup."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(config.ROOT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
    command.upgrade(cfg, "head")
