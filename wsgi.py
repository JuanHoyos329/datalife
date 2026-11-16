"""Punto de entrada para gunicorn: gunicorn wsgi:app"""
import os

from app import create_app, inicializar

if os.environ.get("AUTO_INIT_DB", "1") == "1":
    # Crea las tablas si la base está vacía (útil en Render/Railway, donde no hay initdb)
    inicializar(demo=os.environ.get("SEED_DEMO", "0") == "1")

app = create_app()
