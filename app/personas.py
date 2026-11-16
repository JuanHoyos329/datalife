"""Censo de personas (cargado por el ETL desde Google Forms). Solo Fundador y Coordinador."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .auth import permiso
from .db_helpers import bitacora, ex, q, q1, val

bp = Blueprint("personas", __name__, url_prefix="/personas")
TIPOS = {"residente": "Residente", "trabajador": "Trabajador", "visitante": "Visitante frecuente",
         "albergado": "Persona albergada"}
GRUPOS = {"movilidad": "Movilidad reducida", "mayor": "Adulto mayor", "menor": "Menor de edad", "gestante": "Gestante"}
DOCS = {"CC": "C.C.", "TI": "T.I.", "RC": "R.C.", "CE": "C.E.", "PPT": "PPT", "PA": "Pasaporte", "SD": "Sin documento"}


@bp.route("/")
@permiso("puede_simulacros")
def lista():
    buscar = request.args.get("q", "").strip()
    tipo = request.args.get("tipo", "")
    grupo = request.args.get("grupo", "")
    estado = request.args.get("estado", "activas")
    filtros, params = [], {}
    if buscar:
        filtros.append("(lower(p.nombre) LIKE :b OR lower(p.codigo) LIKE :b OR p.numero_documento LIKE :b)")
        params["b"] = f"%{buscar.lower()}%"
    if tipo in TIPOS:
        filtros.append("p.tipo = :t")
        params["t"] = tipo
    if grupo == "prioritario":
        filtros.append("p.grupo_prioritario <> 'ninguno'")
    elif grupo in GRUPOS:
        filtros.append("p.grupo_prioritario = :g")
        params["g"] = grupo
    filtros.append("p.activo" if estado == "activas" else "NOT p.activo" if estado == "inactivas" else "TRUE")
    personas = q(f"""SELECT p.*, a.nombre AS albergue FROM personas p LEFT JOIN albergues a ON a.id = p.albergue_id
                     WHERE {' AND '.join(filtros)} ORDER BY p.codigo LIMIT 500""", **params)
    kpi = q1("""SELECT count(*) FILTER (WHERE activo) AS total,
                       count(*) FILTER (WHERE activo AND grupo_prioritario <> 'ninguno') AS prioritarias,
                       count(*) FILTER (WHERE activo AND (contacto_celular IS NULL OR contacto_nombre IS NULL)) AS sin_contacto,
                       count(*) FILTER (WHERE activo AND tipo = 'albergado') AS albergadas,
                       count(*) FILTER (WHERE NOT activo) AS inactivas
                FROM personas""")
    ultima = q1("SELECT * FROM etl_ejecuciones ORDER BY id DESC LIMIT 1")
    return render_template("personas/lista.html", seccion="personas", personas=personas, kpi=kpi, ultima=ultima,
                           buscar=buscar, tipo=tipo, grupo=grupo, estado=estado, TIPOS=TIPOS, GRUPOS=GRUPOS)


@bp.route("/<codigo>")
@permiso("puede_simulacros")
def ficha(codigo):
    p = q1("""SELECT p.*, a.nombre AS albergue FROM personas p LEFT JOIN albergues a ON a.id = p.albergue_id
              WHERE p.codigo = :c""", c=codigo.upper())
    if not p:
        abort(404)
    mostrar = request.args.get("contacto") == "1"
    if mostrar:
        bitacora("privacidad", f"Se mostró el contacto de emergencia de {p['codigo']}")
    familia = q1("""SELECT f.*, a.nombre AS albergue FROM familias f JOIN albergues a ON a.id = f.albergue_id
                    WHERE f.persona_id = :p""", p=p["id"])
    checkins = q("""SELECT s.codigo, s.descripcion, c.registrado_en, c.punto_encuentro, c.registrado_en - s.iniciado_en AS tiempo
                    FROM checkins c JOIN simulacros s ON s.id = c.simulacro_id WHERE c.persona_id = :p
                    ORDER BY c.registrado_en DESC""", p=p["id"])
    return render_template("personas/ficha.html", seccion="personas", p=p, mostrar=mostrar, familia=familia,
                           checkins=checkins, TIPOS=TIPOS, GRUPOS=GRUPOS, DOCS=DOCS)


@bp.route("/<codigo>/estado", methods=["POST"])
@permiso("puede_simulacros")
def cambiar_estado(codigo):
    p = q1("SELECT * FROM personas WHERE codigo = :c", c=codigo.upper())
    if not p:
        abort(404)
    if p["activo"]:
        ex("UPDATE personas SET activo = FALSE, fecha_baja = now(), actualizado_en = now() WHERE id = :i", i=p["id"])
        bitacora("censo", f"Baja de {p['codigo']}")
        flash(f"{p['nombre']} quedó inactiva. No se borra: deja de contar en el censo y en los simulacros.")
    else:
        ex("UPDATE personas SET activo = TRUE, fecha_baja = NULL, actualizado_en = now() WHERE id = :i", i=p["id"])
        bitacora("censo", f"Reactivación de {p['codigo']}")
        flash(f"{p['nombre']} está activa de nuevo.")
    return redirect(url_for(".ficha", codigo=p["codigo"]))
