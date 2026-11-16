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
    # Supabase exige SSL
    if "supabase." in url and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        # prepare_threshold=None: sin sentencias preparadas, compatible con el pooler de Supabase
        # (Supavisor en modo transacción, puerto 6543) y con una conexión directa normal.
        _engine = create_engine(database_url(), pool_pre_ping=True, pool_size=3, max_overflow=2,
                                pool_recycle=300, connect_args={"prepare_threshold": None})
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
