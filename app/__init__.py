"""DataLife · aplicación web (Flask)."""
import os
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

import click
from flask import Flask, g, redirect, url_for

from core.db import apply_schema, get_engine, schema_exists

TZ = ZoneInfo("America/Bogota")


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production" or os.environ.get("COOKIE_SECURE") == "1",
        ETL_WEBHOOK_TOKEN=os.environ.get("ETL_WEBHOOK_TOKEN", ""),
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    )
    if config:
        app.config.update(config)

    from . import albergues, api, auth, personas, simulacros, usuarios
    app.register_blueprint(auth.bp)
    app.register_blueprint(albergues.bp)
    app.register_blueprint(simulacros.bp)
    app.register_blueprint(personas.bp)
    app.register_blueprint(usuarios.bp)
    app.register_blueprint(api.bp)
    auth.init_app(app)

    @app.route("/")
    def inicio():
        if not g.get("user"):
            return redirect(url_for("auth.login"))
        return redirect(url_for("albergues.cupos"))

    # ---- filtros de plantilla ----
    @app.template_filter("fecha")
    def fecha(v, formato="%d/%m/%Y %H:%M"):
        if not v:
            return "—"
        if isinstance(v, datetime) and v.tzinfo:
            v = v.astimezone(TZ)
        return v.strftime(formato)

    @app.template_filter("n")
    def numero(v, dec=0):
        if v is None:
            return "—"
        s = f"{float(v):,.{dec}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")

    # ---- comandos de consola ----
    @app.cli.command("init-db")
    @click.option("--demo/--sin-demo", default=False, help="Carga usuarios y datos de demostración")
    def init_db(demo):
        """Crea las tablas (db/schema.sql) y los datos iniciales."""
        inicializar(demo)
        click.echo("Base de datos lista.")

    @app.cli.command("crear-usuario")
    @click.argument("usuario")
    @click.argument("rol", type=click.Choice(["fundador", "coordinador", "voluntario", "brigadista"]))
    @click.option("--nombre", prompt=True)
    @click.password_option()
    def crear_usuario(usuario, rol, nombre, password):
        """Crea (o actualiza la clave de) un usuario."""
        from .usuarios import guardar_usuario
        guardar_usuario(usuario, nombre, rol, password)
        click.echo(f"Usuario {usuario} ({rol}) listo.")

    return app


def inicializar(demo: bool = False):
    from sqlalchemy import text

    from .seed import datos_iniciales, datos_demo
    engine = get_engine()
    # Candado: varios procesos de gunicorn arrancan a la vez y solo uno debe crear las tablas
    with engine.connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(20261116)"))
        try:
            if not schema_exists(engine):
                apply_schema(engine)
            datos_iniciales(engine)
            if demo:
                datos_demo(engine)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(20261116)"))
            lock.commit()
