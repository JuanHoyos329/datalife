from pathlib import Path

import pytest
from sqlalchemy import text

from conftest import csrf, login
from core.db import get_engine

EJEMPLO = Path(__file__).resolve().parent.parent / "etl" / "ejemplo_respuestas.csv"


def db(sql, **p):
    with get_engine().begin() as c:
        return c.execute(text(sql), p).all()


# ---------------- Roles ----------------
@pytest.mark.parametrize("usuario", ["voluntario", "brigada"])
def test_roles_basicos_solo_ven_albergues(client, usuario):
    login(client, usuario)
    for p in ["/albergues/cupos", "/albergues/recursos", "/albergues/alertas"]:
        assert client.get(p).status_code == 200, p
    for p in ["/simulacros/", "/personas/", "/admin/etl", "/admin/usuarios"]:
        assert client.get(p).status_code == 403, p
    html = client.get("/albergues/cupos").get_data(as_text=True)
    assert "Simulacros" not in html and "Censo de personas" not in html


@pytest.mark.parametrize("usuario", ["fundador", "coordinador"])
def test_roles_altos_acceden_a_simulacros(client, usuario):
    login(client, usuario)
    for p in ["/simulacros/", "/personas/", "/admin/etl", "/admin/usuarios", "/albergues/alertas"]:
        assert client.get(p).status_code == 200, p


def test_sin_sesion_redirige_al_login(client):
    r = client.get("/simulacros/")
    assert r.status_code == 302 and "/ingresar" in r.headers["Location"]


def test_clave_incorrecta(client):
    tok = csrf(client)
    r = client.post("/ingresar", data={"usuario": "fundador", "clave": "mal", "csrf": tok})
    assert "Usuario o clave incorrectos" in r.get_data(as_text=True)


def test_post_sin_csrf_se_rechaza(client):
    login(client, "voluntario")
    assert client.post("/albergues/cupos/familia", data={"codigo": "X", "integrantes": 2}).status_code == 400


def test_coordinador_no_puede_crear_fundador(client):
    tok = login(client, "coordinador")
    client.post("/admin/usuarios", data={"usuario": "otro", "nombre": "Otro", "rol": "fundador",
                                         "clave": "12345678", "csrf": tok})
    assert not db("SELECT 1 FROM usuarios WHERE usuario = 'otro'")


# ---------------- Albergues ----------------
def test_asignacion_bloqueada_sin_cupos(client):
    tok = login(client, "voluntario")
    # Santa Librada tiene 2 cupos libres y FAM-015 necesita 7
    fid = db("""SELECT f.id FROM familias f JOIN albergues a ON a.id = f.albergue_id
                WHERE a.codigo = 'santalibrada' AND f.codigo = 'FAM-015'""")[0][0]
    client.post(f"/albergues/cupos/{fid}/asignar", data={"csrf": tok})
    assert db("SELECT estado FROM familias WHERE id = :f", f=fid)[0][0] == "pendiente"
    alertas = db("SELECT titulo FROM v_alertas WHERE titulo LIKE 'FAM-015 no cabe%'")
    assert alertas


def test_entrega_descuenta_inventario_y_cubre_necesidad(client):
    tok = login(client, "voluntario")
    client.get("/albergues/cupos?a=1")
    fam = db("""SELECT n.familia_id FROM necesidades n JOIN familias f ON f.id = n.familia_id
                WHERE f.albergue_id = 1 AND n.recurso = 'agua' AND n.estado = 'pendiente' LIMIT 1""")[0][0]
    antes = db("SELECT existencias FROM inventario WHERE albergue_id = 1 AND recurso = 'agua'")[0][0]
    client.post("/albergues/recursos/entrega", data={"recurso": "agua", "cantidad": "20", "familia": fam, "csrf": tok})
    assert db("SELECT existencias FROM inventario WHERE albergue_id = 1 AND recurso = 'agua'")[0][0] == antes - 20
    assert not db("SELECT 1 FROM necesidades WHERE familia_id = :f AND recurso = 'agua' AND estado = 'pendiente'", f=fam)


# ---------------- ETL ----------------
def test_etl_carga_valida_rechaza_y_es_idempotente():
    from etl.etl_personas import ejecutar
    r = ejecutar(str(EJEMPLO))
    assert (r["leidas"], r["insertadas"], r["rechazadas"]) == (12, 9, 2)
    motivos = " ".join(x["motivo"] for x in r["rechazos"])
    assert "Ley 1581" in motivos and "celular" in motivos
    # La respuesta repetida (mismo documento) queda con los datos más recientes
    assert db("SELECT nombre FROM personas WHERE numero_documento = '16789432'")[0][0] == "Jhon Edinson Mina Possu"
    # Menor por fecha de nacimiento → grupo prioritario
    assert db("SELECT grupo_prioritario FROM personas WHERE numero_documento = '1107654321'")[0][0] == "menor"
    # Persona albergada → familia en espera en su albergue
    assert db("""SELECT f.estado FROM familias f JOIN personas p ON p.id = f.persona_id
                 WHERE p.numero_documento = '94512876'""")[0][0] == "pendiente"
    r2 = ejecutar(str(EJEMPLO))
    assert (r2["insertadas"], r2["actualizadas"]) == (0, 0)


def test_webhook_etl_exige_token(client):
    assert client.post("/api/etl/ejecutar").status_code == 401


# ---------------- Simulacro completo ----------------
def test_flujo_de_simulacro(client):
    tok = login(client, "coordinador")
    r = client.post("/simulacros/nuevo", data={"tipo": "sismo", "descripcion": "Prueba", "meta_pct": "90",
                                               "programado_para": "2026-11-16T10:00", "csrf": tok})
    sid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    client.post(f"/simulacros/{sid}/iniciar", data={"csrf": tok})
    client.post(f"/simulacros/{sid}/checkin", data={"codigo": "P-0001", "csrf": tok})
    r = client.post(f"/simulacros/{sid}/checkin", data={"codigo": "1", "csrf": tok}, follow_redirects=True)
    assert "ya estaba registrado" in r.get_data(as_text=True)
    client.post(f"/simulacros/{sid}/demo", data={"csrf": tok})
    presentes = db("SELECT count(*) FROM checkins WHERE simulacro_id = :s", s=sid)[0][0]
    esperados = db("SELECT count(*) FROM personas WHERE activo AND tipo <> 'albergado'")[0][0]
    assert presentes == round(esperados * 0.9)
    r = client.post(f"/simulacros/{sid}/finalizar", data={"observaciones": "Todo bien", "csrf": tok}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Informe del simulacro" in html and "Frente al anterior" in html and "Todo bien" in html


def test_ficha_oculta_celular_hasta_mostrar(client):
    login(client, "fundador")
    html = client.get("/personas/P-0001").get_data(as_text=True)
    cel = db("SELECT contacto_celular FROM personas WHERE codigo = 'P-0001'")[0][0]
    assert cel not in html and "Mostrar contacto" in html
    assert cel in client.get("/personas/P-0001?contacto=1").get_data(as_text=True)
    assert db("SELECT 1 FROM bitacora WHERE tipo = 'privacidad' AND detalle LIKE '%P-0001%'")


def test_salud(client):
    assert client.get("/salud").get_json()["estado"] == "ok"


# ---------------- Crear cuenta y aprobación ----------------
def test_crear_cuenta_queda_pendiente_y_coordinador_aprueba(app):
    nuevo = app.test_client()
    tok = csrf(nuevo, "/registro")
    datos = {"nombre": "Laura Gómez Prueba", "usuario": "laura.g", "rol": "brigadista", "albergue": "",
             "clave": "segura123", "clave2": "segura123", "csrf": tok}
    r = nuevo.post("/registro", data=datos)
    assert "Solicitud enviada" in r.get_data(as_text=True)
    # Usuario repetido → error en el campo
    r = nuevo.post("/registro", data=datos)
    assert "ya existe" in r.get_data(as_text=True)
    # Pendiente: la clave es correcta pero aún no puede entrar
    tok = csrf(nuevo)
    r = nuevo.post("/ingresar", data={"usuario": "laura.g", "clave": "segura123", "csrf": tok})
    assert r.status_code == 200 and "en revisión" in r.get_data(as_text=True)

    coord = app.test_client()
    tok_c = login(coord, "coordinador")
    html = coord.get("/admin/usuarios").get_data(as_text=True)
    assert "Solicitudes de cuenta" in html and "laura.g" in html
    uid = db("SELECT id FROM usuarios WHERE usuario = 'laura.g'")[0][0]
    # Un coordinador no puede aprobarla como fundador
    coord.post(f"/admin/usuarios/{uid}/solicitud", data={"accion": "aprobar", "rol": "fundador", "csrf": tok_c})
    assert db("SELECT pendiente FROM usuarios WHERE id = :i", i=uid)[0][0] is True
    coord.post(f"/admin/usuarios/{uid}/solicitud", data={"accion": "aprobar", "rol": "voluntario", "csrf": tok_c})
    assert db("SELECT activo, pendiente, rol FROM usuarios WHERE id = :i", i=uid)[0] == (True, False, "voluntario")

    tok = csrf(nuevo)
    r = nuevo.post("/ingresar", data={"usuario": "laura.g", "clave": "segura123", "csrf": tok})
    assert r.status_code == 302
    assert nuevo.get("/albergues/cupos").status_code == 200
    assert nuevo.get("/simulacros/").status_code == 403


def test_registro_no_permite_roles_altos_ni_datos_malos(app):
    c = app.test_client()
    tok = csrf(c, "/registro")
    r = c.post("/registro", data={"nombre": "X", "usuario": "Con Espacio", "rol": "fundador",
                                  "clave": "corta", "clave2": "otra", "csrf": tok})
    html = r.get_data(as_text=True)
    assert "Revisa 4 campos" in html
    assert not db("SELECT 1 FROM usuarios WHERE rol = 'fundador' AND usuario <> 'fundador'")


def test_webhook_recibe_la_hoja_de_apps_script(client):
    import csv
    with EJEMPLO.open(encoding="utf-8-sig") as fh:
        valores = list(csv.reader(fh))
    r = client.post("/api/etl/ejecutar", json={"origen": "google_forms", "hoja": "Respuestas", "valores": valores},
                    headers={"X-DataLife-Token": "tok-123"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["estado"] == "ok"
    assert db("SELECT fuente FROM etl_ejecuciones ORDER BY id DESC LIMIT 1")[0][0] == "apps_script:Respuestas"
