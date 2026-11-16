"""Endpoints técnicos: salud del servicio y webhook del formulario."""
import hmac
import threading

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import text

from core.db import get_engine

bp = Blueprint("api", __name__)
_lock = threading.Lock()


@bp.route("/salud")
def salud():
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        return jsonify(estado="ok", base_de_datos="ok")
    except Exception:  # noqa: BLE001
        return jsonify(estado="error", base_de_datos="sin conexión"), 503


@bp.route("/api/etl/ejecutar", methods=["POST"])
def webhook_etl():
    """Lo llama Apps Script al enviar el formulario (o GitHub Actions). Requiere X-DataLife-Token."""
    esperado = current_app.config.get("ETL_WEBHOOK_TOKEN", "")
    enviado = request.headers.get("X-DataLife-Token", "")
    if not esperado or not hmac.compare_digest(enviado, esperado):
        return jsonify(error="token inválido"), 401
    if not _lock.acquire(blocking=False):
        return jsonify(estado="ya hay un ETL en curso"), 202
    try:
        from etl.etl_personas import ejecutar, filas_desde_tabla
        datos = request.get_json(silent=True) or {}
        tabla = datos.get("valores")
        if isinstance(tabla, list) and tabla and all(isinstance(f, list) for f in tabla):
            # Apps Script envía la hoja completa: no hace falta cuenta de servicio de Google
            r = ejecutar(filas=filas_desde_tabla(tabla), etiqueta=f"apps_script:{datos.get('hoja') or 'respuestas'}")
        else:
            r = ejecutar()
        return jsonify(estado="ok", insertadas=r["insertadas"], actualizadas=r["actualizadas"], rechazadas=r["rechazadas"])
    except SystemExit as e:
        return jsonify(error=str(e)), 400
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("ETL falló")
        return jsonify(error=str(e)), 500
    finally:
        _lock.release()
