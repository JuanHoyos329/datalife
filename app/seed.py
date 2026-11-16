"""Datos iniciales (albergues e inventario) y datos de demostración."""
import os
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from werkzeug.security import generate_password_hash

TZ = ZoneInfo("America/Bogota")

ALBERGUES = [
    # codigo, nombre, direccion, capacidad, existencias (agua, alimentos, medicamentos, cobijas, higiene)
    ("piloto01", "Albergue Piloto 01", "Cali · Comuna 20", 120, (540, 1050, 85, 60, 140)),
    ("coliseo", "Coliseo El Pueblo", "Cali · Cra. 50 con Calle 9", 200, (1265, 2450, 110, 70, 165)),
    ("santalibrada", "Colegio Santa Librada", "Cali · Calle 7 # 14-00", 80, (328, 702, 40, 30, 60)),
]
RECURSOS = ["agua", "alimentos", "medicamentos", "cobijas", "higiene"]


def datos_iniciales(engine):
    """Albergues e inventario base. Idempotente."""
    with engine.begin() as c:
        for cod, nom, dir_, cap, stock in ALBERGUES:
            aid = c.execute(text("""
                INSERT INTO albergues (codigo, nombre, direccion, capacidad) VALUES (:c, :n, :d, :cap)
                ON CONFLICT (codigo) DO UPDATE SET nombre = EXCLUDED.nombre RETURNING id"""),
                {"c": cod, "n": nom, "d": dir_, "cap": cap}).scalar()
            for r, s in zip(RECURSOS, stock):
                c.execute(text("""INSERT INTO inventario (albergue_id, recurso, existencias) VALUES (:a, :r, :s)
                                  ON CONFLICT DO NOTHING"""), {"a": aid, "r": r, "s": s})


USUARIOS_DEMO = [
    ("fundador", "Juan Hoyos (demo)", "fundador", "piloto01"),
    ("coordinador", "Jorge Iván Restrepo", "coordinador", "piloto01"),
    ("voluntario", "Valentina Cárdenas", "voluntario", "piloto01"),
    ("brigada", "Carlos Andrés Ríos", "brigadista", "coliseo"),
]


def datos_demo(engine):
    """Usuarios de prueba, familias, necesidades, entregas, censo y un simulacro pasado.

    Solo se cargan si la base no tiene familias todavía (no pisa datos reales).
    """
    clave = os.environ.get("DEMO_PASSWORD", "datalife2026")
    ahora = datetime.now(TZ)
    with engine.begin() as c:
        ids = {r.codigo: r.id for r in c.execute(text("SELECT id, codigo FROM albergues"))}
        for usuario, nombre, rol, alb in USUARIOS_DEMO:
            c.execute(text("""INSERT INTO usuarios (usuario, nombre, password_hash, rol, albergue_id)
                              VALUES (:u, :n, :p, :r, :a) ON CONFLICT (usuario) DO NOTHING"""),
                      {"u": usuario, "n": nombre, "p": generate_password_hash(clave), "r": rol, "a": ids[alb]})
        if c.execute(text("SELECT count(*) FROM familias")).scalar():
            return

        def familias(aid, alojadas, pendientes, dias):
            todas = alojadas + pendientes
            fids = []
            for i, n in enumerate(todas):
                aloj = i < len(alojadas)
                t = ahora - timedelta(days=dias - (i * dias) // max(len(todas), 1), hours=i % 7)
                fids.append(c.execute(text("""
                    INSERT INTO familias (albergue_id, codigo, integrantes, estado, registrada_en, alojada_en)
                    VALUES (:a, :c, :n, :e, :t, :al) RETURNING id"""),
                    {"a": aid, "c": f"FAM-{i + 1:03d}", "n": n, "e": "alojada" if aloj else "pendiente",
                     "t": t, "al": t + timedelta(minutes=40) if aloj else None}).scalar())
            return fids

        # Piloto 01: igual al prototipo HTML (14 familias alojadas, FAM-015 en espera)
        p = familias(ids["piloto01"], [6, 8, 5, 7, 9, 4, 8, 6, 7, 5, 10, 6, 8, 7], [6], 4)
        necesidades = [(1, "medicamentos", "alta", "pendiente"), (4, "agua", "alta", "pendiente"),
                       (10, "alimentos", "alta", "pendiente"), (8, "cobijas", "media", "pendiente"),
                       (14, "medicamentos", "media", "pendiente"), (12, "higiene", "baja", "pendiente"),
                       (0, "agua", "media", "atendida"), (3, "cobijas", "baja", "atendida"),
                       (6, "alimentos", "alta", "atendida")]
        for i, (f, r, pr, e) in enumerate(necesidades):
            c.execute(text("""INSERT INTO necesidades (familia_id, recurso, prioridad, estado, registrada_en)
                              VALUES (:f, :r, :p, :e, :t)"""),
                      {"f": p[f], "r": r, "p": pr, "e": e, "t": ahora - timedelta(hours=5 + i * 3)})
        for f, r, cant, d in [(6, "alimentos", 24, 1), (3, "cobijas", 4, 1), (0, "agua", 30, 0), (11, "higiene", 2, 0)]:
            c.execute(text("""INSERT INTO entregas (albergue_id, familia_id, recurso, cantidad, fecha)
                              VALUES (:a, :f, :r, :c, :d)"""),
                      {"a": ids["piloto01"], "f": p[f], "r": r, "c": cant, "d": (ahora - timedelta(days=d)).date()})

        # Coliseo El Pueblo: 136 personas de 200
        col = [5, 4, 6, 3, 5, 7, 4, 5, 6, 4, 3, 5, 6, 4, 5, 7, 4, 3, 5, 6, 4, 5, 3, 4, 6, 5, 3]
        col.append(136 - sum(col))
        cf = familias(ids["coliseo"], col, [], 3)
        for f, r, pr in [(2, "medicamentos", "alta"), (9, "cobijas", "media"), (16, "higiene", "media"),
                         (20, "alimentos", "alta"), (24, "agua", "baja")]:
            c.execute(text("INSERT INTO necesidades (familia_id, recurso, prioridad) VALUES (:f, :r, :p)"),
                      {"f": cf[f], "r": r, "p": pr})
        # Santa Librada: 78 de 80 y una familia de 7 que no cabe → alerta crítica
        sl = [6, 5, 7, 4, 6, 5, 8, 4, 6, 5, 7, 5, 4, 6]
        sl[-1] += 78 - sum(sl)
        sf = familias(ids["santalibrada"], sl, [7], 2)
        for f, r, pr in [(1, "agua", "alta"), (5, "medicamentos", "alta"), (8, "alimentos", "alta"), (11, "cobijas", "media")]:
            c.execute(text("INSERT INTO necesidades (familia_id, recurso, prioridad) VALUES (:f, :r, :p)"),
                      {"f": sf[f], "r": r, "p": pr})

        _censo_demo(c, ahora)
        _simulacro_demo(c, ahora)


NOM = ["Andrés", "Valentina", "Santiago", "Camila", "Juan David", "Laura", "Sebastián", "Daniela", "Carlos",
       "Mariana", "Felipe", "Natalia", "Alejandro", "Paula", "Diego", "Isabella", "Jhon Fredy", "Luisa Fernanda",
       "Mateo", "Sofía", "Óscar", "Ana María", "Julián", "Catalina", "Esteban", "Manuela", "Hernán", "Gloria",
       "Luis Alberto", "Martha", "Samuel", "Salomé", "Jorge", "Carmen", "Nicolás", "Gabriela", "Fabio", "Rosa",
       "Emiliano", "Luciana", "Wilson", "Yolanda", "Tomás", "Valeria", "Ricardo", "Lucía", "Kevin", "Sara",
       "Álvaro", "Teresa", "Miguel Ángel", "Juliana", "Héctor", "Ángela"]
APE = ["Rodríguez", "Gómez", "López", "Martínez", "García", "Hernández", "Ramírez", "Torres", "Valencia",
       "Cárdenas", "Ospina", "Restrepo", "Mosquera", "Giraldo", "Quintero", "Castaño", "Rentería", "Arango",
       "Muñoz", "Zapata", "Vélez", "Caicedo", "Londoño", "Salazar", "Bermúdez", "Rivas", "Palacios", "Cifuentes"]
PUNTOS = ["Zona verde norte", "Parqueadero de visitantes"]


def _censo_demo(c, ahora):
    """62 personas ficticias (54 residentes + 8 trabajadores), como en DataLife Alpha."""
    rnd = random.Random(20261021)
    hogares = [3, 2, 4, 1, 3, 2, 2, 3, 4, 2, 1, 3, 2, 2, 3, 2, 4, 2, 1, 3, 2, 3]
    aptos = ["A101", "A104", "A203", "A302", "A402", "A404", "A501", "B102", "B201", "B204", "B303", "B304",
             "B401", "B503", "C101", "C102", "C202", "C301", "C302", "C401", "C403", "C504"]
    gente, k = [], 0
    for h, n in enumerate(hogares):
        for j in range(n):
            a = aptos[h]
            gente.append((f"{NOM[k % len(NOM)]} {APE[h % len(APE)]} {APE[(h * 7 + j * 3 + 5) % len(APE)]}",
                          "residente", "Torre " + a[0], a[1:], PUNTOS[1] if a[0] == "C" else PUNTOS[0]))
            k += 1
    for nom, area, cargo in [("Wilmar Mina Angulo", "Portería", "Vigilancia"), ("Jhon Jairo Possu", "Portería", "Vigilancia"),
                             ("Édgar Rodallega", "Portería", "Vigilancia"), ("Yesid Carabalí", "Portería", "Vigilancia"),
                             ("Luz Dary Riascos", "Zonas comunes", "Aseo"), ("Marleny Banguero", "Zonas comunes", "Aseo"),
                             ("Ferney Lucumí", "Zonas comunes", "Aseo"), ("Diana Patricia Orozco", "Administración", "Administradora")]:
        gente.append((nom, "trabajador", area, cargo, PUNTOS[1]))
    grupos = {4: "movilidad", 20: "movilidad", 41: "movilidad", 3: "mayor", 9: "mayor", 15: "mayor", 26: "mayor",
              33: "mayor", 47: "mayor", 2: "menor", 7: "menor", 8: "menor", 12: "menor", 18: "menor", 23: "menor",
              29: "menor", 38: "menor", 44: "menor", 31: "gestante"}
    sin_contacto = {6, 14, 27, 39, 57}
    for i, (nom, tipo, torre, apto, punto) in enumerate(gente):
        cel = None if i in sin_contacto else "3" + rnd.choice(["00", "01", "04", "10", "12", "15", "17", "20"]) + \
            "".join(str(rnd.randint(0, 9)) for _ in range(7))
        c.execute(text("""
            INSERT INTO personas (clave_origen, nombre, tipo_documento, numero_documento, tipo, grupo_prioritario,
                                  torre_area, apto_cargo, punto_encuentro, contacto_nombre, contacto_celular,
                                  autoriza_datos, fuente, respondido_en, creado_en)
            VALUES (:k, :n, 'CC', :d, :t, :g, :to, :a, :p, :cn, :cc, TRUE, 'demo', :r, :r)"""),
            {"k": f"DEMO:{i + 1}", "n": nom, "d": str(1100000000 + i * 7919), "t": tipo, "g": grupos.get(i, "ninguno"),
             "to": torre, "a": apto, "p": punto, "cn": None if i in sin_contacto else f"{NOM[(i * 3) % len(NOM)]} {APE[(i * 5 + 3) % len(APE)]}",
             "cc": cel, "r": ahora - timedelta(days=60)})


def _simulacro_demo(c, ahora):
    """Un simulacro finalizado hace 26 días, para comparar en el informe."""
    inicio = (ahora - timedelta(days=26)).replace(hour=10, minute=0, second=0, microsecond=0)
    sid = c.execute(text("""
        INSERT INTO simulacros (codigo, tipo, descripcion, programado_para, iniciado_en, finalizado_en, observaciones)
        VALUES ('SIM-001', 'sismo', 'Simulacro Nacional de Respuesta a Emergencias 2026', :i, :i, :f,
                'Evacuación ordenada. Un residente con movilidad reducida de la Torre B no llegó: asignar dos brigadistas de apoyo.')
        RETURNING id"""), {"i": inicio, "f": inicio + timedelta(minutes=11, seconds=40)}).scalar()
    personas = c.execute(text("SELECT id, punto_encuentro, grupo_prioritario FROM personas ORDER BY id")).all()
    rnd = random.Random(7)
    faltan = {p.id for p in personas if p.grupo_prioritario == "movilidad"}
    faltan = set(sorted(faltan)[:1]) | {personas[10].id, personas[33].id, personas[50].id}
    for p in personas:
        if p.id in faltan:
            continue
        c.execute(text("""INSERT INTO checkins (simulacro_id, persona_id, punto_encuentro, registrado_en)
                          VALUES (:s, :p, :pt, :t)"""),
                  {"s": sid, "p": p.id, "pt": p.punto_encuentro, "t": inicio + timedelta(seconds=rnd.randint(70, 640))})
