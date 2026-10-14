"""Test setup.

Database tests run against real PostgreSQL, because the worker relies on
Postgres-only features (FOR UPDATE SKIP LOCKED). Set TEST_DATABASE_URL to run
them; without it they are skipped and only the pure-logic tests run.

Locally:
    docker compose up -d db
    TEST_DATABASE_URL=postgresql+psycopg://tracker:tracker@localhost:5432/tracker_test pytest
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db import Base, make_engine, make_session_factory

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="session")
def migrated_engine():
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run database tests")
    cfg = alembic_config(TEST_DATABASE_URL)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    engine = make_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def engine(migrated_engine):
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with migrated_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    return migrated_engine


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        yield s
