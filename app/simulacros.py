"""Simulacros de evacuación: SOLO roles Fundador y Coordinador."""
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .auth import permiso
from .db_helpers import bitacora, ex, q, q1, val

bp = Blueprint("simulacros", __name__, url_prefix="/simulacros")
TZ = ZoneInfo("America/Bogota")
TIPOS = {"sismo": "Sismo", "incendio": "Incendio", "inundacion": "Inundación", "otro": "Otro"}
GRUPOS = {"movilidad": "Movilidad reducida", "mayor": "Adulto mayor", "menor": "Menor de edad", "gestante": "Gestante"}
MINUTOS_ALERTA_PRIORITARIO = 5

# Personas que deben evacuar: censo activo, sin contar a las personas albergadas.
# Solo cuentan las que ya estaban en el censo al momento del simulacro (:corte),
# para que un informe viejo no muestre como faltantes a personas registradas después.
ESPERADOS = ("SELECT * FROM personas WHERE activo AND tipo <> 'albergado' "
             "AND creado_en <= COALESCE(CAST(:corte AS timestamptz), now())")


def _sim(sid):
    s = q1("SELECT * FROM simulacros WHERE id = :id", id=sid)
    if not s:
        abort(404)
    return s


def estado(s):
    if s["finalizado_en"]:
        return ("Finalizado", "t-neu")
    if s["iniciado_en"]:
        return ("En curso", "t-crit")
    return ("Programado", "t-nav")


def calcular(s):
    sid, corte = s["id"], s["finalizado_en"]
    esperados = val(f"SELECT count(*) FROM ({ESPERADOS}) e", corte=corte)
    presentes = val("SELECT count(*) FROM checkins WHERE simulacro_id = :s", s=sid)
    faltantes = q(f"""SELECT e.* FROM ({ESPERADOS}) e
                      WHERE NOT EXISTS (SELECT 1 FROM checkins c WHERE c.simulacro_id = :s AND c.persona_id = e.id)
                      ORDER BY (e.grupo_prioritario = 'ninguno'), e.torre_area, e.apto_cargo, e.nombre""", s=sid, corte=corte)
    puntos = q("""SELECT c.punto_encuentro AS punto, count(*) AS n, max(c.registrado_en) AS ultimo
                  FROM checkins c WHERE c.simulacro_id = :s GROUP BY 1 ORDER BY 1""", s=sid)
    recientes = q("""SELECT c.*, p.codigo, p.nombre FROM checkins c JOIN personas p ON p.id = c.persona_id
                     WHERE c.simulacro_id = :s ORDER BY c.registrado_en DESC LIMIT 8""", s=sid)
    prio_tot = val(f"SELECT count(*) FROM ({ESPERADOS}) e WHERE e.grupo_prioritario <> 'ninguno'", corte=corte)
    prio_falt = [f for f in faltantes if f["grupo_prioritario"] != "ninguno"]
    alertas = []
    if s["iniciado_en"] and not s["finalizado_en"]:
        transcurrido = datetime.now(TZ) - s["iniciado_en"]
        if transcurrido > timedelta(minutes=MINUTOS_ALERTA_PRIORITARIO):
            for f in prio_falt:
                alertas.append({"titulo": f"Falta {f['nombre']} ({GRUPOS[f['grupo_prioritario']]})",
                                "detalle": f"{f['codigo']} · {f['torre_area'] or ''} {f['apto_cargo'] or ''} · "
                                           f"lleva más de {MINUTOS_ALERTA_PRIORITARIO} minutos sin llegar a {f['punto_encuentro']}."})
    pct = presentes / esperados if esperados else 0
    return {"esperados": esperados, "presentes": presentes, "faltantes": faltantes, "puntos": puntos,
            "recientes": recientes, "pct": pct, "prio_tot": prio_tot, "prio_pres": prio_tot - len(prio_falt),
            "alertas": alertas}


@bp.route("/")
@permiso("puede_simulacros")
def lista():
    sims = q("""SELECT s.*, (SELECT count(*) FROM checkins c WHERE c.simulacro_id = s.id) AS presentes
                FROM simulacros s ORDER BY s.programado_para DESC""")
    esperados = val(f"SELECT count(*) FROM ({ESPERADOS}) e", corte=None)
    activo = next((s for s in sims if s["iniciado_en"] and not s["finalizado_en"]), None)
    return render_template("simulacros/lista.html", seccion="simulacros", sims=sims, esperados=esperados,
                           activo=activo, estado=estado, TIPOS=TIPOS, ahora=datetime.now(TZ))


@bp.route("/nuevo", methods=["POST"])
@permiso("puede_simulacros")
def nuevo():
    tipo = request.form.get("tipo", "sismo")
    desc = request.form.get("descripcion", "").strip()
    try:
        cuando = datetime.fromisoformat(request.form.get("programado_para", "")).replace(tzinfo=TZ)
        meta = int(request.form.get("meta_pct", 92))
    except ValueError:
        cuando, meta = None, 0
    if tipo not in TIPOS or not desc or not cuando or not 1 <= meta <= 100:
        flash("Completa tipo, descripción, fecha y una meta entre 1 y 100 %.", "error")
        return redirect(url_for(".lista"))
    n = val("SELECT COALESCE(MAX(substring(codigo from '[0-9]+$')::int), 0) + 1 FROM simulacros")
    sid = val("""INSERT INTO simulacros (codigo, tipo, descripcion, programado_para, meta_pct, creado_por)
                 VALUES (:c, :t, :d, :p, :m, :u) RETURNING id""",
              c=f"SIM-{n:03d}", t=tipo, d=desc, p=cuando, m=meta, u=g.user["id"])
    bitacora("simulacro", f"Programado SIM-{n:03d}: {desc}")
    flash(f"Simulacro SIM-{n:03d} programado.")
    return redirect(url_for(".ver", sid=sid))


@bp.route("/<int:sid>")
@permiso("puede_simulacros")
def ver(sid):
    s = _sim(sid)
    if s["finalizado_en"]:
        return redirect(url_for(".informe", sid=sid))
    return render_template("simulacros/ver.html", seccion="simulacros", s=s, d=calcular(s), estado=estado(s),
                           TIPOS=TIPOS, GRUPOS=GRUPOS)


@bp.route("/<int:sid>/estado.json")
@permiso("puede_simulacros")
def estado_json(sid):
    s = _sim(sid)
    d = calcular(s)
    return jsonify(presentes=d["presentes"], esperados=d["esperados"], faltantes=len(d["faltantes"]),
                   alertas=len(d["alertas"]))


@bp.route("/<int:sid>/iniciar", methods=["POST"])
@permiso("puede_simulacros")
def iniciar(sid):
    s = _sim(sid)
    otro = val("SELECT codigo FROM simulacros WHERE iniciado_en IS NOT NULL AND finalizado_en IS NULL AND id <> :s", s=sid)
    if otro:
        flash(f"Ya hay un simulacro en curso ({otro}). Finalízalo primero.", "error")
    elif not s["iniciado_en"]:
        ex("UPDATE simulacros SET iniciado_en = now() WHERE id = :s", s=sid)
        bitacora("simulacro", f"Inicio de {s['codigo']}")
        flash("Simulacro iniciado. El cronómetro está corriendo.")
    return redirect(url_for(".ver", sid=sid))


def _persona_por_codigo(texto):
    t = texto.strip().upper()
    if not t:
        return None
    if t.isdigit() and len(t) <= 4:
        t = f"P-{int(t):04d}"
    return q1(f"""SELECT * FROM ({ESPERADOS}) e WHERE upper(e.codigo) = :t OR e.numero_documento = :t""", t=t, corte=None)


@bp.route("/<int:sid>/checkin", methods=["POST"])
@permiso("puede_simulacros")
def checkin(sid):
    s = _sim(sid)
    if not s["iniciado_en"] or s["finalizado_en"]:
        flash("El simulacro no está en curso.", "error")
        return redirect(url_for(".ver", sid=sid))
    p = _persona_por_codigo(request.form.get("codigo", ""))
    if p is None:
        flash(f"No se encontró «{request.form.get('codigo', '')}» en el censo.", "error")
    else:
        r = ex("""INSERT INTO checkins (simulacro_id, persona_id, punto_encuentro, registrado_por)
                  VALUES (:s, :p, :pt, :u) ON CONFLICT DO NOTHING""",
               s=sid, p=p["id"], pt=request.form.get("punto") or p["punto_encuentro"], u=g.user["id"])
        if r.rowcount:
            bitacora("checkin", f"{s['codigo']}: {p['codigo']} en {request.form.get('punto') or p['punto_encuentro']}")
            flash(f"✓ {p['codigo']} · {p['nombre']} registrado.")
        else:
            flash(f"{p['codigo']} · {p['nombre']} ya estaba registrado.", "error")
    return redirect(url_for(".ver", sid=sid, punto=request.form.get("punto", "")))


@bp.route("/<int:sid>/deshacer", methods=["POST"])
@permiso("puede_simulacros")
def deshacer(sid):
    s = _sim(sid)
    ult = q1("""SELECT c.persona_id, p.codigo FROM checkins c JOIN personas p ON p.id = c.persona_id
                WHERE c.simulacro_id = :s ORDER BY c.registrado_en DESC LIMIT 1""", s=sid)
    if ult and not s["finalizado_en"]:
        ex("DELETE FROM checkins WHERE simulacro_id = :s AND persona_id = :p", s=sid, p=ult["persona_id"])
        bitacora("checkin", f"{s['codigo']}: se deshizo el check-in de {ult['codigo']}")
        flash(f"Se deshizo el check-in de {ult['codigo']}.")
    return redirect(url_for(".ver", sid=sid))


@bp.route("/<int:sid>/demo", methods=["POST"])
@permiso("puede_simulacros")
def demo(sid):
    """Modo demo: simula llegadas hasta la meta y deja 1 o 2 prioritarios como faltantes."""
    s = _sim(sid)
    if not s["iniciado_en"] or s["finalizado_en"]:
        return redirect(url_for(".ver", sid=sid))
    d = calcular(s)
    objetivo = round(d["esperados"] * s["meta_pct"] / 100)
    faltan = d["faltantes"]
    rnd = random.Random(sid)
    prio = [f for f in faltan if f["grupo_prioritario"] != "ninguno"]
    reservar = {f["id"] for f in prio[:rnd.choice([1, 2])]}
    candidatos = [f for f in faltan if f["id"] not in reservar]
    rnd.shuffle(candidatos)
    base = s["iniciado_en"]
    for i, f in enumerate(candidatos[:max(0, objetivo - d["presentes"])]):
        ex("""INSERT INTO checkins (simulacro_id, persona_id, punto_encuentro, registrado_en, registrado_por)
              VALUES (:s, :p, :pt, :t, :u) ON CONFLICT DO NOTHING""",
           s=sid, p=f["id"], pt=f["punto_encuentro"], u=g.user["id"],
           t=min(datetime.now(TZ), base + timedelta(seconds=40 + i * 6)))
    bitacora("simulacro", f"{s['codigo']}: modo demo hasta {s['meta_pct']} %")
    flash(f"Modo demo: llegadas simuladas hasta el {s['meta_pct']} %.")
    return redirect(url_for(".ver", sid=sid))


@bp.route("/<int:sid>/finalizar", methods=["POST"])
@permiso("puede_simulacros")
def finalizar(sid):
    s = _sim(sid)
    if s["iniciado_en"] and not s["finalizado_en"]:
        ex("UPDATE simulacros SET finalizado_en = now(), observaciones = :o WHERE id = :s",
           s=sid, o=request.form.get("observaciones", "").strip() or None)
        bitacora("simulacro", f"Fin de {s['codigo']}")
    return redirect(url_for(".informe", sid=sid))


@bp.route("/<int:sid>/informe")
@permiso("puede_simulacros")
def informe(sid):
    s = _sim(sid)
    if not s["finalizado_en"]:
        return redirect(url_for(".ver", sid=sid))
    d = calcular(s)
    dur = s["finalizado_en"] - s["iniciado_en"]
    puntos = q("""SELECT c.punto_encuentro AS punto, count(*) AS n, max(c.registrado_en) - :i AS tiempo
                  FROM checkins c WHERE c.simulacro_id = :s GROUP BY 1 ORDER BY 1""", s=sid, i=s["iniciado_en"])
    previo = q1("""SELECT s.*, (SELECT count(*) FROM checkins c WHERE c.simulacro_id = s.id) AS presentes
                   FROM simulacros s WHERE s.finalizado_en IS NOT NULL AND s.finalizado_en < :f
                   ORDER BY s.finalizado_en DESC LIMIT 1""", f=s["finalizado_en"])
    return render_template("simulacros/informe.html", seccion="simulacros", s=s, d=d, dur=dur, puntos=puntos,
                           previo=previo, TIPOS=TIPOS, GRUPOS=GRUPOS)
