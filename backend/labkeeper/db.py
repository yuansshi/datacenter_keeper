"""Database engine and session helpers (SQLite by default, any SQLAlchemy URL works)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from sqlalchemy import Engine, MetaData, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

T = TypeVar("T")

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def init_engine(url: str, echo: bool = False) -> Engine:
    global _engine, _session_factory
    kwargs: dict = {"echo": echo, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    _engine = engine
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine


def engine() -> Engine:
    if _engine is None:
        raise RuntimeError("database not initialised; call init_engine() first")
    return _engine


def new_session() -> Session:
    if _session_factory is None:
        raise RuntimeError("database not initialised; call init_engine() first")
    return _session_factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()


async def run_in_session(fn: Callable[[Session], T]) -> T:
    """Run blocking DB work in a worker thread with its own committed session."""

    def _work() -> T:
        with session_scope() as s:
            return fn(s)

    return await asyncio.to_thread(_work)


def ensure_columns(engine_: Engine, metadata: MetaData) -> list[str]:
    """Tiny forward-only migration: add columns that exist in the models but not in the database.

    Covers the common upgrade case (new nullable columns) without Alembic. Returns what was added.
    """
    added: list[str] = []
    insp = inspect(engine_)
    existing_tables = set(insp.get_table_names())
    with engine_.begin() as conn:
        for table in metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl_type = col.type.compile(dialect=engine_.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}'))
                added.append(f"{table.name}.{col.name}")
    return added
