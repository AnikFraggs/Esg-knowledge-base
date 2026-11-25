"""Engine + session factory. SQLite by default, PostgreSQL via DATABASE_URL."""
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from esg import settings
from esg.models import Base


def _prepare_url(url: str) -> str:
    # make sure the sqlite file directory exists before the engine opens it
    if url.startswith("sqlite:///"):
        path = url.replace("sqlite:///", "", 1)
        if path and path != ":memory:":
            settings.ensure_dirs()
    return url


connect_args = {"timeout": 30, "check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(_prepare_url(settings.DATABASE_URL), connect_args=connect_args, future=True)

if settings.DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _migrate_sqlite_schema():
    """Safe inline migration for SQLite when columns are added without Alembic."""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    import sqlite3
    path = settings.DATABASE_URL.replace("sqlite:///", "", 1)
    if not path or path == ":memory:":
        return
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(companies)")
        c_cols = [r[1] for r in cur.fetchall()]
        if c_cols and "isin" not in c_cols:
            cur.execute("ALTER TABLE companies ADD COLUMN isin VARCHAR(12)")

        cur.execute("PRAGMA table_info(filings)")
        f_cols = [r[1] for r in cur.fetchall()]
        if f_cols:
            if "credibility_score" not in f_cols:
                cur.execute("ALTER TABLE filings ADD COLUMN credibility_score FLOAT DEFAULT 1.0")
            if "approval_status" not in f_cols:
                cur.execute("ALTER TABLE filings ADD COLUMN approval_status VARCHAR(20) DEFAULT 'approved'")
        conn.commit()
        conn.close()
    except Exception:
        pass


def init_db():
    """Create all tables and run inline SQLite column backfills."""
    settings.ensure_dirs()
    Base.metadata.create_all(engine)
    _migrate_sqlite_schema()


@contextmanager
def get_session():
    init_db()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
