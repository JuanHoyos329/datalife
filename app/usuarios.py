"""Administración: usuarios y roles, ETL y bitácora (Fundador y Coordinador)."""
import os
import tempfile
from pathlib import Path

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from core.db import get_engine

from .auth import ROL_LABEL, permiso
from .db_helpers import bitacora, ex, q, q1, val

bp = Blueprint("admin", __name__, url_prefix="/admin")


def guardar_usuario(usuario, nombre, rol, clave, albergue_id=None):
    with get_engine().begin() as c:
        from sqlalchemy import text
        c.execute(text("""INSERT INTO usuarios (usuario, nombre, password_hash, rol, albergue_id)
                          VALUES (:u, :n, :p, :r, :a)
                          ON CONFLICT (usuario) DO UPDATE SET nombre = EXCLUDED.nombre, rol = EXCLUDED.rol,
                          password_hash = EXCLUDED.password_hash, activo = TRUE"""),
                  {"u": usuario.lower(), "n": nombre, "p": generate_password_hash(clave), "r": rol, "a": albergue_id})


def roles_asignables():
    # Un coordinador no puede crear fundadores; un fundador puede asignar cualquier rol
    return [r for r in ROL_LABEL if g.user["rol"] == "fundador" or r != "fundador"]


@bp.route("/usuarios")
@permiso("puede_admin")
def usuarios():
    filas = q("""SELECT u.*, r.nombre AS rol_nombre, a.nombre AS albergue FROM usuarios u
                 JOIN roles r ON r.codigo = u.rol LEFT JOIN albergues a ON a.id = u.albergue_id
                 WHERE NOT u.pendiente ORDER BY r.nivel, u.usuario""")
    solicitudes = q("""SELECT u.*, a.nombre AS albergue FROM usuarios u LEFT JOIN albergues a ON a.id = u.albergue_id
                       WHERE u.pendiente ORDER BY u.creado_en""")
    roles = q("SELECT * FROM roles ORDER BY nivel, codigo")
    albergues = q("SELECT id, nombre FROM albergues WHERE activo ORDER BY id")
    return render_template("admin/usuarios.html", seccion="admin", filas=filas, roles=roles, albergues=albergues,
                           solicitudes=solicitudes,
                           asignables=roles_asignables(), ROL_LABEL=ROL_LABEL)


@bp.route("/usuarios", methods=["POST"])
@permiso("puede_admin")
def crear_usuario():
    f = request.form
    usuario, nombre, rol, clave = f.get("usuario", "").strip().lower(), f.get("nombre", "").strip(), f.get("rol"), f.get("clave", "")
    if not usuario.isidentifier() or not nombre or rol not in roles_asignables() or len(clave) < 8:
        flash("Revisa los datos: usuario sin espacios, nombre, un rol permitido y clave de mínimo 8 caracteres.", "error")
    elif val("SELECT 1 FROM usuarios WHERE usuario = :u", u=usuario):
        flash(f"El usuario {usuario} ya existe.", "error")
    else:
        ex("""INSERT INTO usuarios (usuario, nombre, password_hash, rol, albergue_id) VALUES (:u, :n, :p, :r, :a)""",
           u=usuario, n=nombre, p=generate_password_hash(clave), r=rol, a=int(f.get("albergue") or 0) or None)
        bitacora("usuarios", f"Usuario creado: {usuario} ({rol})")
        flash(f"Usuario {usuario} creado con rol {ROL_LABEL[rol]}.")
    return redirect(url_for(".usuarios"))


@bp.route("/usuarios/<int:uid>/rol", methods=["POST"])
@permiso("puede_admin")
def cambiar_rol(uid):
    rol = request.form.get("rol")
    actual = val("SELECT rol FROM usuarios WHERE id = :i", i=uid)
    if uid == g.user["id"]:
        flash("No puedes cambiar tu propio rol.", "error")
    elif rol not in roles_asignables() or (actual == "fundador" and g.user["rol"] != "fundador"):
        flash("No tienes permiso para asignar ese rol.", "error")
    else:
        ex("UPDATE usuarios SET rol = :r WHERE id = :i", r=rol, i=uid)
        bitacora("usuarios", f"Cambio de rol del usuario {uid} a {rol}")
        flash("Rol actualizado.")
    return redirect(url_for(".usuarios"))


@bp.route("/usuarios/<int:uid>/activo", methods=["POST"])
@permiso("puede_admin")
def alternar_activo(uid):
    actual = val("SELECT rol FROM usuarios WHERE id = :i", i=uid)
    if uid == g.user["id"] or (actual == "fundador" and g.user["rol"] != "fundador"):
        flash("No puedes desactivar este usuario.", "error")
    else:
        ex("UPDATE usuarios SET activo = NOT activo WHERE id = :i", i=uid)
        bitacora("usuarios", f"Usuario {uid} activado/desactivado")
    return redirect(url_for(".usuarios"))


@bp.route("/usuarios/<int:uid>/solicitud", methods=["POST"])
@permiso("puede_admin")
def resolver_solicitud(uid):
    """Aprobar o rechazar una cuenta creada desde "Crear cuenta"."""
    u = q1("SELECT usuario FROM usuarios WHERE id = :i AND pendiente", i=uid)
    if not u:
        flash("Esa solicitud ya fue resuelta.", "error")
    elif request.form.get("accion") == "aprobar":
        rol = request.form.get("rol", "voluntario")
        if rol not in roles_asignables():
            flash("No tienes permiso para asignar ese rol.", "error")
            return redirect(url_for(".usuarios"))
        ex("UPDATE usuarios SET pendiente = FALSE, activo = TRUE, rol = :r WHERE id = :i", r=rol, i=uid)
        bitacora("usuarios", f"Cuenta aprobada: {u['usuario']} ({rol})")
        flash(f"Cuenta de {u['usuario']} aprobada como {ROL_LABEL[rol]}. Ya puede ingresar.")
    else:
        ex("DELETE FROM usuarios WHERE id = :i AND pendiente", i=uid)
        bitacora("usuarios", f"Solicitud rechazada: {u['usuario']}")
        flash(f"Solicitud de {u['usuario']} rechazada.")
    return redirect(url_for(".usuarios"))


@bp.route("/etl")
@permiso("puede_admin")
def etl():
    corridas = q("SELECT * FROM etl_ejecuciones ORDER BY id DESC LIMIT 15")
    rechazos = q("""SELECT r.* FROM etl_rechazos r WHERE r.ejecucion_id = (SELECT max(id) FROM etl_ejecuciones)
                    ORDER BY r.fila""")
    sheets = bool(os.environ.get("GOOGLE_SHEET_ID") and os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))
    bita = q("""SELECT b.*, u.usuario FROM bitacora b LEFT JOIN usuarios u ON u.id = b.usuario_id
                ORDER BY b.id DESC LIMIT 40""")
    return render_template("admin/etl.html", seccion="admin", corridas=corridas, rechazos=rechazos,
                           sheets=sheets, bita=bita, sheet_id=os.environ.get("GOOGLE_SHEET_ID", ""))


@bp.route("/etl", methods=["POST"])
@permiso("puede_admin")
def correr_etl():
    from etl.etl_personas import ejecutar
    archivo = request.files.get("archivo")
    try:
        if archivo and archivo.filename:
            ext = Path(archivo.filename).suffix.lower()
            if ext not in (".csv", ".xlsx"):
                raise ValueError("Sube un archivo .csv o .xlsx exportado de la hoja de respuestas.")
            with tempfile.TemporaryDirectory() as tmp:
                ruta = Path(tmp) / f"respuestas{ext}"
                archivo.save(ruta)
                r = ejecutar(str(ruta))
        else:
            r = ejecutar()
        flash(f"ETL terminado: {r['insertadas']} personas nuevas, {r['actualizadas']} actualizadas, "
              f"{r['rechazadas']} filas rechazadas.")
    except SystemExit as e:
        flash(str(e), "error")
    except Exception as e:  # noqa: BLE001 – se muestra al administrador
        flash(f"El ETL falló: {e}", "error")
    return redirect(url_for(".etl"))
