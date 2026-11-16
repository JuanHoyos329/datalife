"""Las pruebas usan una base PostgreSQL real (TEST_DATABASE_URL), que se recrea en cada sesión."""
import os
import re
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@localhost:5432/datalife_test")


@pytest.fixture(scope="session")
def app():
    base, nombre = URL.rsplit("/", 1)
    admin = create_engine(base.replace("postgresql://", "postgresql+psycopg://") + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{nombre}"'))
    admin.dispose()
    os.environ["DATABASE_URL"] = URL
    os.environ["DEMO_PASSWORD"] = "clave-prueba"
    from core.db import reset_engine
    reset_engine()
    from app import create_app, inicializar
    inicializar(demo=True)
    return create_app({"TESTING": True, "ETL_WEBHOOK_TOKEN": "tok-123"})


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client, path="/ingresar"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(client, usuario):
    tok = csrf(client)
    r = client.post("/ingresar", data={"usuario": usuario, "clave": "clave-prueba", "csrf": tok})
    assert r.status_code == 302, r.get_data(as_text=True)
    return csrf(client, "/albergues/cupos")
