"""Albergues: disponibilidad de cupos, recursos y alertas (todos los roles)."""
from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from .auth import login_required, permiso
from .db_helpers import bitacora, ex, q, q1, val

bp = Blueprint("albergues", __name__, url_prefix="/albergues")

PRIO = {"alta": ("Alta", "t-crit"), "media": ("Media", "t-warn"), "baja": ("Baja", "t-ok")}


def nivel_ocupacion(occ):
    return "crit" if occ >= 1 else ("warn" if occ >= 0.9 else "ok")


def nivel_dias(d):
    if d is None:
        return "ok"
    return "crit" if d < 2 else ("warn" if d < 4 else "ok")


def albergue_actual():
    """Albergue elegido en el selector (?a=ID), recordado en la sesión."""
    if request.args.get("a", "").isdigit():
        session["albergue_id"] = int(request.args["a"])
    aid = session.get("albergue_id") or (g.user and g.user["albergue_id"])
    alb = q1("SELECT * FROM v_ocupacion WHERE albergue_id = :a", a=aid) if aid else None
    if alb is None:
        alb = q1("SELECT * FROM v_ocupacion ORDER BY albergue_id LIMIT 1")
    if alb:
        session["albergue_id"] = alb["albergue_id"]
    return alb


def contexto_comun(alb):
    lista = q("SELECT albergue_id, nombre FROM v_ocupacion ORDER BY albergue_id")
    n_alertas = val("SELECT count(*) FROM v_alertas WHERE albergue_id = :a", a=alb["albergue_id"]) if alb else 0
    return {"alb": alb, "albergues": lista, "n_alertas": n_alertas}


# ---------------------------------------------------------------------------
# Disponibilidad de cupos
# ---------------------------------------------------------------------------
@bp.route("/cupos")
@login_required
def cupos():
    alb = albergue_actual()
    if alb is None:
        return render_template("albergues/vacio.html")
    a = alb["albergue_id"]
    pendientes = q("""SELECT f.*, p.nombre AS responsable FROM familias f LEFT JOIN personas p ON p.id = f.persona_id
                      WHERE f.albergue_id = :a AND f.estado = 'pendiente' ORDER BY f.registrada_en""", a=a)
    alojadas = q("""SELECT f.*, p.nombre AS responsable FROM familias f LEFT JOIN personas p ON p.id = f.persona_id
                    WHERE f.albergue_id = :a AND f.estado = 'alojada' ORDER BY f.codigo""", a=a)
    flujo = q1("""SELECT COALESCE(SUM(integrantes) FILTER (WHERE alojada_en::date = CURRENT_DATE), 0) AS entradas,
                         COALESCE(SUM(integrantes) FILTER (WHERE salida_en::date = CURRENT_DATE), 0) AS salidas
                  FROM familias WHERE albergue_id = :a""", a=a)
    ciudad = q("""SELECT o.*, (SELECT count(*) FROM v_alertas v WHERE v.albergue_id = o.albergue_id AND v.nivel = 'critica') AS criticas
                  FROM v_ocupacion o ORDER BY o.ocupacion DESC""")
    # Sugerencia de remisión: familias que no caben → albergue con más cupos libres
    remisiones = []
    for f in pendientes:
        if f["integrantes"] > alb["disponibles"]:
            mejor = max((c for c in ciudad if c["albergue_id"] != a), key=lambda c: c["disponibles"], default=None)
            if mejor and mejor["disponibles"] >= f["integrantes"]:
                remisiones.append({"familia": f, "destino": mejor})
    siguiente = (val("""SELECT COALESCE(MAX(substring(codigo from '[0-9]+$')::int), 0) + 1
                        FROM familias WHERE albergue_id = :a""", a=a))
    return render_template("albergues/cupos.html", seccion="cupos", pendientes=pendientes, alojadas=alojadas,
                           flujo=flujo, ciudad=ciudad, remisiones=remisiones, nivel=nivel_ocupacion(alb["ocupacion"]),
                           siguiente=f"FAM-{siguiente:03d}", nivel_ocupacion=nivel_ocupacion, **contexto_comun(alb))


@bp.route("/cupos/familia", methods=["POST"])
@permiso("puede_operar")
def registrar_familia():
    alb = albergue_actual()
    codigo = request.form.get("codigo", "").strip().upper()
    try:
        n = int(request.form.get("integrantes", "0"))
    except ValueError:
        n = 0
    if not codigo or not 1 <= n <= 20:
        flash("Escribe un identificador y entre 1 y 20 integrantes.", "error")
    elif val("SELECT 1 FROM familias WHERE albergue_id = :a AND codigo = :c", a=alb["albergue_id"], c=codigo):
        flash(f"El identificador {codigo} ya existe en este albergue.", "error")
    else:
        ex("INSERT INTO familias (albergue_id, codigo, integrantes) VALUES (:a, :c, :n)",
           a=alb["albergue_id"], c=codigo, n=n)
        bitacora("albergue", f"Registro de {codigo} ({n} integrantes) en {alb['nombre']}")
        flash(f"{codigo} registrada. Queda en espera de cupo.")
    return redirect(url_for(".cupos"))


@bp.route("/cupos/<int:fid>/asignar", methods=["POST"])
@permiso("puede_operar")
def asignar(fid):
    f = q1("SELECT * FROM familias WHERE id = :f FOR UPDATE", f=fid)
    if not f or f["estado"] != "pendiente":
        flash("La familia ya no está en espera.", "error")
        return redirect(url_for(".cupos"))
    ex("SELECT pg_advisory_xact_lock(:a)", a=f["albergue_id"])
    libres = val("SELECT disponibles FROM v_ocupacion WHERE albergue_id = :a", a=f["albergue_id"])
    if f["integrantes"] > libres:
        flash(f"No hay cupos suficientes para {f['codigo']}: necesita {f['integrantes']} y quedan {libres}.", "error")
    else:
        ex("UPDATE familias SET estado = 'alojada', alojada_en = now() WHERE id = :f", f=fid)
        bitacora("albergue", f"Cupo asignado a {f['codigo']} ({f['integrantes']} personas)")
        flash(f"Cupo asignado a {f['codigo']}.")
    return redirect(url_for(".cupos"))


@bp.route("/cupos/<int:fid>/salida", methods=["POST"])
@permiso("puede_operar")
def salida(fid):
    f = q1("SELECT * FROM familias WHERE id = :f", f=fid)
    if f and f["estado"] == "alojada":
        ex("UPDATE familias SET estado = 'salida', salida_en = now() WHERE id = :f", f=fid)
        bitacora("albergue", f"Salida de {f['codigo']}")
        flash(f"Salida de {f['codigo']} registrada. Se liberaron {f['integrantes']} cupos.")
    return redirect(url_for(".cupos"))


@bp.route("/cupos/<int:fid>/remitir", methods=["POST"])
@permiso("puede_operar")
def remitir(fid):
    f = q1("SELECT * FROM familias WHERE id = :f AND estado = 'pendiente'", f=fid)
    destino = int(request.form.get("destino", "0") or 0)
    d = q1("SELECT * FROM v_ocupacion WHERE albergue_id = :a", a=destino)
    if not f or not d or d["disponibles"] < f["integrantes"]:
        flash("No se pudo remitir: el albergue destino ya no tiene cupos suficientes.", "error")
        return redirect(url_for(".cupos"))
    n = val("SELECT COALESCE(MAX(substring(codigo from '[0-9]+$')::int), 0) + 1 FROM familias WHERE albergue_id = :a", a=destino)
    nuevo = f"FAM-{n:03d}"
    ex("UPDATE familias SET albergue_id = :d, codigo = :c WHERE id = :f", d=destino, c=nuevo, f=fid)
    bitacora("albergue", f"Remisión de {f['codigo']} a {d['nombre']} como {nuevo}")
    flash(f"{f['codigo']} remitida a {d['nombre']} (queda como {nuevo}, en espera de cupo).")
    return redirect(url_for(".cupos"))


# ---------------------------------------------------------------------------
# Recursos
# ---------------------------------------------------------------------------
@bp.route("/recursos")
@login_required
def recursos():
    alb = albergue_actual()
    if alb is None:
        return render_template("albergues/vacio.html")
    a = alb["albergue_id"]
    inv = q("SELECT * FROM v_inventario_dias WHERE albergue_id = :a ORDER BY orden", a=a)
    vitales = sorted([r for r in inv if r["vital"] and r["dias_restantes"] is not None], key=lambda r: r["dias_restantes"])
    peor = vitales[0] if vitales else None
    necesidades = q("""SELECT n.*, f.codigo AS familia, r.nombre AS recurso_nombre FROM necesidades n
                       JOIN familias f ON f.id = n.familia_id JOIN recursos r ON r.codigo = n.recurso
                       WHERE f.albergue_id = :a AND n.estado = 'pendiente' AND f.estado <> 'salida'
                       ORDER BY CASE n.prioridad WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, n.registrada_en""", a=a)
    entregas = q("""SELECT e.*, f.codigo AS familia, r.nombre AS recurso_nombre, r.unidad FROM entregas e
                    JOIN familias f ON f.id = e.familia_id JOIN recursos r ON r.codigo = e.recurso
                    WHERE e.albergue_id = :a ORDER BY e.registrada_en DESC LIMIT 12""", a=a)
    familias = q("""SELECT id, codigo, integrantes, estado FROM familias WHERE albergue_id = :a AND estado <> 'salida'
                    ORDER BY codigo""", a=a)
    activas = val("SELECT count(*) FROM familias WHERE albergue_id = :a AND estado <> 'salida'", a=a)
    con_pend = len({n["familia_id"] for n in necesidades})
    cobertura = (activas - con_pend) / activas if activas else 1
    lista_rec = q("SELECT * FROM recursos ORDER BY orden")
    return render_template("albergues/recursos.html", seccion="recursos", inv=inv, peor=peor,
                           necesidades=necesidades, entregas=entregas, familias=familias, recursos=lista_rec,
                           cobertura=cobertura, activas=activas, con_pend=con_pend, PRIO=PRIO,
                           nivel_dias=nivel_dias, **contexto_comun(alb))


def _num(nombre):
    try:
        return float(request.form.get(nombre, "").replace(",", "."))
    except ValueError:
        return 0


@bp.route("/recursos/entrega", methods=["POST"])
@permiso("puede_operar")
def entrega():
    alb = albergue_actual()
    a, rec, cant = alb["albergue_id"], request.form.get("recurso"), _num("cantidad")
    fam = q1("SELECT * FROM familias WHERE id = :f AND albergue_id = :a AND estado <> 'salida'",
             f=int(request.form.get("familia") or 0), a=a)
    stock = val("SELECT existencias FROM inventario WHERE albergue_id = :a AND recurso = :r FOR UPDATE", a=a, r=rec)
    if not fam or stock is None:
        flash("Elige la familia y el recurso.", "error")
    elif cant <= 0 or cant > float(stock):
        flash(f"Cantidad no válida: hay {stock} disponibles.", "error")
    else:
        ex("UPDATE inventario SET existencias = existencias - :c, actualizado_en = now() WHERE albergue_id = :a AND recurso = :r",
           c=cant, a=a, r=rec)
        cod = val("""INSERT INTO entregas (albergue_id, familia_id, recurso, cantidad, registrada_por)
                     VALUES (:a, :f, :r, :c, :u) RETURNING codigo""", a=a, f=fam["id"], r=rec, c=cant, u=g.user["id"])
        cubiertas = ex("""UPDATE necesidades SET estado = 'atendida', atendida_en = now()
                          WHERE familia_id = :f AND recurso = :r AND estado = 'pendiente'""", f=fam["id"], r=rec).rowcount
        bitacora("entregas", f"{cod}: {cant:g} de {rec} a {fam['codigo']}")
        flash(f"{cod} registrada." + (" Necesidad marcada como cubierta." if cubiertas else ""))
    return redirect(url_for(".recursos"))


@bp.route("/recursos/necesidad", methods=["POST"])
@permiso("puede_operar")
def necesidad():
    alb = albergue_actual()
    fam = q1("SELECT * FROM familias WHERE id = :f AND albergue_id = :a", f=int(request.form.get("familia") or 0),
             a=alb["albergue_id"])
    rec, prio = request.form.get("recurso"), request.form.get("prioridad")
    if not fam or prio not in PRIO or not val("SELECT 1 FROM recursos WHERE codigo = :r", r=rec):
        flash("Elige familia, tipo y prioridad.", "error")
    else:
        ex("INSERT INTO necesidades (familia_id, recurso, prioridad) VALUES (:f, :r, :p)", f=fam["id"], r=rec, p=prio)
        bitacora("albergue", f"Necesidad {rec} ({prio}) para {fam['codigo']}")
        flash(f"Necesidad registrada para {fam['codigo']}.")
    return redirect(url_for(".recursos"))


@bp.route("/recursos/ingreso", methods=["POST"])
@permiso("puede_operar")
def ingreso():
    alb = albergue_actual()
    rec, cant = request.form.get("recurso"), _num("cantidad")
    if cant <= 0 or not val("SELECT 1 FROM recursos WHERE codigo = :r", r=rec):
        flash("Escribe una cantidad mayor que cero.", "error")
    else:
        ex("""INSERT INTO inventario (albergue_id, recurso, existencias) VALUES (:a, :r, :c)
              ON CONFLICT (albergue_id, recurso) DO UPDATE SET existencias = inventario.existencias + EXCLUDED.existencias,
              actualizado_en = now()""", a=alb["albergue_id"], r=rec, c=cant)
        bitacora("albergue", f"Ingreso de {cant:g} de {rec} en {alb['nombre']}")
        flash("Ingreso de inventario registrado.")
    return redirect(url_for(".recursos"))


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------
@bp.route("/alertas")
@login_required
def alertas():
    alb = albergue_actual()
    if alb is None:
        return render_template("albergues/vacio.html")
    todas = request.args.get("ver") == "todas"
    filas = q(f"""SELECT v.*, o.nombre AS albergue FROM v_alertas v JOIN v_ocupacion o ON o.albergue_id = v.albergue_id
                  {'' if todas else 'WHERE v.albergue_id = :a'}
                  ORDER BY CASE v.nivel WHEN 'critica' THEN 0 ELSE 1 END, o.nombre, v.categoria""", a=alb["albergue_id"])
    resumen = q("""SELECT o.albergue_id, o.nombre,
                          count(v.*) FILTER (WHERE v.nivel = 'critica') AS criticas,
                          count(v.*) FILTER (WHERE v.nivel = 'atencion') AS atencion
                   FROM v_ocupacion o LEFT JOIN v_alertas v ON v.albergue_id = o.albergue_id
                   GROUP BY o.albergue_id, o.nombre ORDER BY o.albergue_id""")
    return render_template("albergues/alertas.html", seccion="alertas", filas=filas, todas=todas,
                           resumen=resumen, **contexto_comun(alb))
