import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Agency

# The stock SQLite driver waits 5s for a contended write, so a "slow provider"
# must exceed that to reproduce what ingestion actually did.
SLOW_PROVIDER_SECONDS = 6.0


def engine_for(path: Path, *, concurrency_pragmas: bool):
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    if concurrency_pragmas:

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

    Base.metadata.create_all(engine)
    return engine


def write_while_held(engine, hold_seconds: float) -> None:
    """A background write stays open while a second connection writes.

    This is the ingestion-versus-request shape: a background run holds its
    transaction while waiting on a parser or model response, and a request
    needs to write in the meantime.
    """
    started = threading.Event()

    def holder() -> None:
        with Session(engine) as session:
            session.add(Agency(key="holder", name="Holder", details={}, is_default=False))
            session.flush()
            started.set()
            time.sleep(hold_seconds)
            session.commit()

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert started.wait(timeout=5)
        with Session(engine) as session:
            session.add(Agency(key="other", name="Other", details={}, is_default=False))
            session.commit()
    finally:
        thread.join(timeout=30)


def test_default_sqlite_settings_reproduce_the_lock_failure(tmp_path):
    engine = engine_for(tmp_path / "plain.db", concurrency_pragmas=False)
    with pytest.raises(Exception, match="database is locked"):
        write_while_held(engine, SLOW_PROVIDER_SECONDS)


def test_wal_and_busy_timeout_let_the_concurrent_write_through(tmp_path):
    engine = engine_for(tmp_path / "wal.db", concurrency_pragmas=True)
    write_while_held(engine, 1.0)
    with Session(engine) as session:
        assert session.scalar(select(Agency).where(Agency.key == "other")) is not None


def test_sqlite_engines_are_configured_for_wal(tmp_path):
    engine = engine_for(tmp_path / "app.db", concurrency_pragmas=True)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 15000
