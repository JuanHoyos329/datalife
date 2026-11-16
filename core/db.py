"""Conexión a la base de datos compartida por la app web y el ETL."""
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

SCHEMA_SQL = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
_engine = None


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "postgresql://datalife:datalife@localhost:5432/datalife")
    # Render/Heroku/Neon entregan "postgres://" o "postgresql://": usamos el driver psycopg 3
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(database_url(), pool_pre_ping=True, future=True)
    return _engine


def reset_engine():
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def schema_exists(engine: Engine) -> bool:
    with engine.connect() as c:
        return c.execute(text("SELECT to_regclass('public.personas') IS NOT NULL")).scalar()


def apply_schema(engine: Engine):
    """Ejecuta db/schema.sql (idempotente: usa IF NOT EXISTS / ON CONFLICT)."""
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    raw = engine.raw_connection()
    try:
        raw.driver_connection.autocommit = True  # el archivo trae su propio BEGIN/COMMIT
        cur = raw.cursor()
        cur.execute(sql)
        raw.commit()
    finally:
        raw.close()
