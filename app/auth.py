"""Inicio de sesión, roles y protección CSRF."""
import functools
import hmac
import secrets

from flask import (Blueprint, abort, flash, g, redirect, render_template, request, session, url_for)
from werkzeug.security import check_password_hash

from .db_helpers import bitacora, ex, q1

bp = Blueprint("auth", __name__)

ROL_LABEL = {"fundador": "Fundador", "coordinador": "Coordinador",
             "voluntario": "Voluntario / Operario", "brigadista": "Brigadista"}


def init_app(app):
    @app.before_request
    def cargar_usuario():
        g.user = None
        uid = session.get("uid")
        if uid:
            g.user = q1("""SELECT u.id, u.usuario, u.nombre, u.rol, u.albergue_id, r.nombre AS rol_nombre,
                                  r.puede_simulacros, r.puede_operar, r.puede_admin
                           FROM usuarios u JOIN roles r ON r.codigo = u.rol
                           WHERE u.id = :id AND u.activo""", id=uid)
            if g.user is None:
                session.clear()
        if request.method == "POST" and not request.path.startswith("/api/"):
            enviado = request.form.get("csrf") or request.headers.get("X-CSRF", "")
            if not hmac.compare_digest(enviado, session.get("csrf", "")):
                abort(400, "El formulario expiró. Recarga la página e inténtalo de nuevo.")

    @app.teardown_request
    def cerrar_db(exc):
        db = g.pop("db", None)
        tx = g.pop("db_tx", None)
        if db is not None:
            if exc is None and tx is not None and tx.is_active:
                tx.commit()
            elif tx is not None and tx.is_active:
                tx.rollback()
            db.close()

    @app.context_processor
    def contexto():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return {"csrf_token": session["csrf"], "user": g.get("user")}

    @app.errorhandler(403)
    def prohibido(e):
        return render_template("error.html", codigo=403, titulo="Acceso restringido",
                               mensaje="Esta sección es solo para los roles Fundador y Coordinador. "
                                       "Con tu rol puedes usar Albergues: cupos, recursos y alertas."), 403

    @app.errorhandler(404)
    def no_encontrado(e):
        return render_template("error.html", codigo=404, titulo="No encontrado",
                               mensaje="La página o el registro que buscas no existe."), 404

    @app.errorhandler(400)
    def mala(e):
        return render_template("error.html", codigo=400, titulo="Solicitud no válida",
                               mensaje=getattr(e, "description", "")), 400


def login_required(view):
    @functools.wraps(view)
    def envoltura(*a, **kw):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*a, **kw)
    return envoltura


def permiso(flag):
    """@permiso('puede_simulacros') → solo roles con ese permiso (fundador, coordinador)."""
    def deco(view):
        @functools.wraps(view)
        @login_required
        def envoltura(*a, **kw):
            if not g.user[flag]:
                bitacora("acceso", f"Acceso denegado a {request.path} ({g.user['rol']})")
                abort(403)
            return view(*a, **kw)
        return envoltura
    return deco


@bp.route("/ingresar", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip().lower()
        clave = request.form.get("clave", "")
        u = q1("SELECT id, password_hash, activo FROM usuarios WHERE usuario = :u", u=usuario)
        if u and u["activo"] and check_password_hash(u["password_hash"], clave):
            session.clear()
            session["uid"] = u["id"]
            session["csrf"] = secrets.token_urlsafe(32)
            ex("UPDATE usuarios SET ultimo_ingreso = now() WHERE id = :id", id=u["id"])
            g.user = {"id": u["id"]}
            bitacora("sesion", f"Ingreso de {usuario}")
            destino = request.args.get("next", "")
            return redirect(destino if destino.startswith("/") and not destino.startswith("//") else url_for("inicio"))
        error = "Usuario o clave incorrectos."
    return render_template("login.html", error=error)


@bp.route("/salir", methods=["POST"])
def logout():
    if g.user:
        bitacora("sesion", f"Salida de {g.user['usuario']}")
    session.clear()
    flash("Sesión cerrada.")
    return redirect(url_for("auth.login"))
