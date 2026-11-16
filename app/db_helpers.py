"""Atajos para consultar la base desde las vistas."""
from flask import g
from sqlalchemy import text

from core.db import get_engine


def conn():
    """Una conexión por petición; se confirma al terminar (ver auth.init_app)."""
    if "db" not in g:
        g.db = get_engine().connect()
        g.db_tx = g.db.begin()
    return g.db


def q(sql, **params):
    return conn().execute(text(sql), params).mappings().all()


def q1(sql, **params):
    return conn().execute(text(sql), params).mappings().first()


def val(sql, **params):
    return conn().execute(text(sql), params).scalar()


def ex(sql, **params):
    return conn().execute(text(sql), params)


def bitacora(tipo, detalle):
    uid = g.user["id"] if g.get("user") else None
    ex("INSERT INTO bitacora (usuario_id, tipo, detalle) VALUES (:u, :t, :d)", u=uid, t=tipo, d=detalle)
