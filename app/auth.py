"""Inicio de sesión, roles y protección CSRF."""
import functools
import os
import hmac
import secrets

from flask import (Blueprint, abort, flash, g, redirect, render_template, request, session, url_for)
import re

from werkzeug.security import check_password_hash, generate_password_hash

from .db_helpers import bitacora, ex, q, q1, val

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
        u = g.get("user")
        pend = val("SELECT count(*) FROM usuarios WHERE pendiente") if u and u["puede_admin"] else 0
        return {"csrf_token": session["csrf"], "user": u, "solicitudes_pendientes": pend,
                "modo_demo": os.environ.get("SEED_DEMO") == "1",
                "tema": "light" if request.cookies.get("tema") == "light" else "dark"}

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
        u = q1("SELECT id, password_hash, activo, pendiente FROM usuarios WHERE usuario = :u", u=usuario)
        clave_ok = bool(u) and check_password_hash(u["password_hash"], clave)
        if clave_ok and u["pendiente"]:
            return render_template("login.html", pendiente=True, usuario=usuario)
        if clave_ok and not u["activo"]:
            return render_template("login.html", usuario=usuario,
                                   error="Tu cuenta está desactivada. Pídele a un coordinador que la active.")
        if clave_ok:
            session.clear()
            session["uid"] = u["id"]
            session["csrf"] = secrets.token_urlsafe(32)
            ex("UPDATE usuarios SET ultimo_ingreso = now() WHERE id = :id", id=u["id"])
            g.user = {"id": u["id"]}
            bitacora("sesion", f"Ingreso de {usuario}")
            destino = request.args.get("next", "")
            return redirect(destino if destino.startswith("/") and not destino.startswith("//") else url_for("inicio"))
        error = "Usuario o clave incorrectos."
    return render_template("login.html", error=error, usuario=request.form.get("usuario", ""))


ROLES_REGISTRO = {"voluntario": "Voluntario / Operario", "brigadista": "Brigadista"}
USUARIO_RE = re.compile(r"^[a-z][a-z0-9_.]{2,29}$")


@bp.route("/registro", methods=["GET", "POST"])
def registro():
    """Crear cuenta: queda pendiente hasta que un Fundador o Coordinador la apruebe.

    Desde aquí solo se pueden pedir roles operativos (Voluntario o Brigadista):
    nadie puede darse a sí mismo acceso a Simulacros ni al censo.
    """
    if g.user:
        return redirect(url_for("inicio"))
    albergues = q("SELECT id, nombre FROM albergues WHERE activo ORDER BY id")
    datos, errores = {}, {}
    if request.method == "POST":
        f = request.form
        datos = {"nombre": " ".join(f.get("nombre", "").split()), "usuario": f.get("usuario", "").strip().lower(),
                 "rol": f.get("rol", "voluntario"), "albergue": f.get("albergue", "")}
        clave, clave2 = f.get("clave", ""), f.get("clave2", "")
        if len(datos["nombre"]) < 5 or " " not in datos["nombre"]:
            errores["nombre"] = "Escribe tu nombre y apellido."
        if not USUARIO_RE.match(datos["usuario"]):
            errores["usuario"] = "De 3 a 30 caracteres: letras minúsculas, números, punto o guion bajo. Sin espacios."
        elif val("SELECT 1 FROM usuarios WHERE usuario = :u", u=datos["usuario"]):
            errores["usuario"] = "Ese usuario ya existe. Prueba con otro."
        if len(clave) < 8:
            errores["clave"] = "La clave debe tener mínimo 8 caracteres."
        elif clave != clave2:
            errores["clave2"] = "Las dos claves no coinciden."
        if datos["rol"] not in ROLES_REGISTRO:
            errores["rol"] = "Elige Voluntario o Brigadista."
        alb = int(datos["albergue"]) if datos["albergue"].isdigit() else None
        if alb and alb not in {a["id"] for a in albergues}:
            errores["albergue"] = "Elige un albergue de la lista."
        if not errores:
            ex("""INSERT INTO usuarios (usuario, nombre, password_hash, rol, albergue_id, activo, pendiente)
                  VALUES (:u, :n, :p, :r, :a, FALSE, TRUE)""",
               u=datos["usuario"], n=datos["nombre"], p=generate_password_hash(clave), r=datos["rol"], a=alb)
            bitacora("usuarios", f"Solicitud de cuenta: {datos['usuario']} ({datos['rol']})")
            return render_template("registro.html", creado=True, datos=datos)
    return render_template("registro.html", datos=datos, errores=errores, albergues=albergues, roles=ROLES_REGISTRO)


@bp.route("/salir", methods=["POST"])
def logout():
    if g.user:
        bitacora("sesion", f"Salida de {g.user['usuario']}")
    session.clear()
    flash("Sesión cerrada.")
    return redirect(url_for("auth.login"))
