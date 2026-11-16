"""
ETL de DataLife: Google Forms → Google Sheets → PostgreSQL
===========================================================

Extrae las respuestas del formulario "DataLife · Registro de personas",
las limpia y valida, y las carga (upsert) en la tabla `personas`.

  * Extract   – lee la hoja de Google con una cuenta de servicio (gspread)
                o un archivo local .csv / .xlsx (útil para pruebas).
  * Transform – normaliza textos, documentos, celulares y fechas; traduce las
                opciones del formulario a los códigos de la base; rechaza filas
                sin autorización de datos (Ley 1581) o sin campos obligatorios.
  * Load      – INSERT … ON CONFLICT (clave_origen) DO UPDATE: si la persona
                ya existe (mismo documento) se actualiza, si no, se crea con un
                código P-0001 nuevo. Si es "Persona albergada", se crea además
                su familia en espera de cupo en el albergue elegido.

Cada ejecución queda registrada en `etl_ejecuciones` y las filas rechazadas
en `etl_rechazos`, para revisar qué falló.

Uso
---
    # Desde Google Sheets (variables GOOGLE_SHEET_ID y GOOGLE_SERVICE_ACCOUNT_JSON)
    python -m etl.etl_personas

    # Desde un archivo exportado de la hoja
    python -m etl.etl_personas --fuente etl/ejemplo_respuestas.csv

    # Ver qué haría sin escribir en la base
    python -m etl.etl_personas --fuente etl/ejemplo_respuestas.csv --simular
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text

# Permite ejecutar el archivo directamente (python etl/etl_personas.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import apply_schema, get_engine, schema_exists  # noqa: E402

log = logging.getLogger("datalife.etl")
TZ = ZoneInfo("America/Bogota")

# ---------------------------------------------------------------------------
# Títulos de las preguntas del formulario → columna de la tabla `personas`.
# Deben coincidir con google/formulario_datalife.gs
# ---------------------------------------------------------------------------
COLUMNAS = {
    "Marca temporal": "respondido_en",
    "Timestamp": "respondido_en",                      # cuenta de Google en inglés
    "Nombre completo": "nombre",
    "Tipo de documento": "tipo_documento",
    "Número de documento": "numero_documento",
    "Fecha de nacimiento": "fecha_nacimiento",
    "Tipo de persona": "tipo",
    "Grupo de atención prioritaria": "grupo_prioritario",
    "Torre o área": "torre_area",
    "Apartamento o cargo": "apto_cargo",
    "Punto de encuentro asignado": "punto_encuentro",
    "Albergue": "albergue",
    "Integrantes del grupo familiar": "integrantes_familia",
    "Condiciones médicas o necesidades especiales": "condiciones_medicas",
    "Contacto de emergencia (nombre)": "contacto_nombre",
    "Celular del contacto de emergencia": "contacto_celular",
    "Autorización de tratamiento de datos (Ley 1581 de 2012)": "autoriza_datos",
}

TIPO_DOC = {
    "cedula de ciudadania": "CC", "tarjeta de identidad": "TI", "registro civil": "RC",
    "cedula de extranjeria": "CE", "ppt": "PPT", "pasaporte": "PA", "sin documento": "SD",
}
TIPO_PERSONA = {
    "residente": "residente", "trabajador": "trabajador",
    "visitante frecuente": "visitante", "persona albergada": "albergado",
}
GRUPO = {
    "ninguno": "ninguno", "movilidad reducida": "movilidad", "adulto mayor": "mayor",
    "menor de edad": "menor", "gestante": "gestante",
}
FORMATOS_FECHA = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S",
                  "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y")


def sin_tildes(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def limpiar(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def nombre_propio(s: str) -> str:
    menores = {"de", "del", "la", "las", "los", "y"}
    palabras = limpiar(s).lower().split(" ")
    return " ".join(p if (i and p in menores) else p[:1].upper() + p[1:] for i, p in enumerate(palabras) if p)


def parse_fecha(v) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = limpiar(v)
    if not s:
        return None
    for f in FORMATOS_FECHA:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    raise ValueError(f"fecha no reconocida «{s}»")


# ---------------------------------------------------------------------------
# EXTRACT
# ---------------------------------------------------------------------------
def extraer_archivo(ruta: str) -> list[dict]:
    p = Path(ruta)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        filas = list(ws.iter_rows(values_only=True))
        enc = [limpiar(h) for h in filas[0]]
        return [dict(zip(enc, f)) for f in filas[1:] if any(c not in (None, "") for c in f)]
    with p.open(encoding="utf-8-sig", newline="") as fh:
        muestra = fh.read(4096)
        fh.seek(0)
        dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t")
        return [dict(r) for r in csv.DictReader(fh, dialect=dialecto)]


def extraer_google_sheet(sheet_id: str, credenciales: str) -> list[dict]:
    """Lee la pestaña de respuestas con una cuenta de servicio de Google.

    `credenciales` es el JSON de la cuenta de servicio (texto) o la ruta a ese
    archivo. La hoja debe estar compartida con el correo de la cuenta de servicio
    como Lector.
    """
    import gspread

    info = json.loads(Path(credenciales).read_text()) if Path(credenciales).is_file() else json.loads(credenciales)
    gc = gspread.service_account_from_dict(info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    libro = gc.open_by_key(sheet_id)
    nombre_hoja = os.environ.get("GOOGLE_SHEET_TAB")
    if nombre_hoja:
        ws = libro.worksheet(nombre_hoja)
    else:  # primera pestaña que tenga la columna "Nombre completo"
        ws = next((w for w in libro.worksheets() if "Nombre completo" in w.row_values(1)), libro.sheet1)
    return ws.get_all_records(default_blank="")


# ---------------------------------------------------------------------------
# TRANSFORM
# ---------------------------------------------------------------------------
@dataclass
class Resultado:
    validas: list[dict] = field(default_factory=list)
    rechazos: list[tuple[int, str]] = field(default_factory=list)


def transformar(filas: list[dict], albergues: dict[str, int]) -> Resultado:
    """Convierte las filas de la hoja en registros listos para `personas`.

    `albergues` mapea el nombre del albergue (sin tildes, minúsculas) a su id.
    La fila 2 de la hoja es la primera respuesta (la 1 son los títulos).
    """
    res = Resultado()
    vistos: dict[str, int] = {}
    for n, fila in enumerate(filas, start=2):
        r = {COLUMNAS[k.strip()]: v for k, v in fila.items() if k and k.strip() in COLUMNAS}
        try:
            reg = _transformar_fila(r, albergues)
        except ValueError as e:
            res.rechazos.append((n, str(e)))
            continue
        # Si la misma persona respondió dos veces, gana la respuesta más reciente
        if reg["clave_origen"] in vistos:
            res.validas[vistos[reg["clave_origen"]]] = reg
        else:
            vistos[reg["clave_origen"]] = len(res.validas)
            res.validas.append(reg)
    return res


def _transformar_fila(r: dict, albergues: dict[str, int]) -> dict:
    nombre = nombre_propio(r.get("nombre", ""))
    if len(nombre) < 3:
        raise ValueError("falta el nombre completo")

    aut = sin_tildes(limpiar(r.get("autoriza_datos")))
    if not (aut.startswith("si") or aut in ("true", "1", "x")):
        raise ValueError("no autorizó el tratamiento de datos (Ley 1581): la fila no se guarda")

    tipo_doc = TIPO_DOC.get(sin_tildes(limpiar(r.get("tipo_documento"))), None)
    numero = re.sub(r"\D", "", limpiar(r.get("numero_documento")))
    if numero and not 5 <= len(numero) <= 15:
        raise ValueError(f"número de documento inválido «{numero}»")
    if tipo_doc == "SD":
        numero = ""

    tipo = TIPO_PERSONA.get(sin_tildes(limpiar(r.get("tipo"))))
    if not tipo:
        raise ValueError(f"tipo de persona desconocido «{limpiar(r.get('tipo'))}»")
    grupo = GRUPO.get(sin_tildes(limpiar(r.get("grupo_prioritario"))) or "ninguno", "ninguno")

    nac = parse_fecha(r.get("fecha_nacimiento"))
    nac = nac.date() if nac else None
    if nac and not (date(1900, 1, 1) <= nac <= date.today()):
        raise ValueError(f"fecha de nacimiento fuera de rango «{nac:%d/%m/%Y}»")
    # Regla de negocio: un menor de 18 años queda marcado como prioritario
    if nac and grupo == "ninguno":
        edad = (date.today() - nac).days // 365
        grupo = "menor" if edad < 18 else ("mayor" if edad >= 60 else grupo)

    cel = re.sub(r"\D", "", limpiar(r.get("contacto_celular")))
    if cel.startswith("57") and len(cel) == 12:
        cel = cel[2:]
    if cel and not re.fullmatch(r"3\d{9}", cel):
        raise ValueError(f"celular del contacto inválido «{cel}»")

    torre = limpiar(r.get("torre_area"))
    torre = "" if sin_tildes(torre) == "no aplica" else torre

    alb_txt = sin_tildes(limpiar(r.get("albergue")))
    albergue_id = None
    if alb_txt and alb_txt != "no aplica":
        albergue_id = albergues.get(alb_txt)
        if albergue_id is None:
            raise ValueError(f"albergue desconocido «{limpiar(r.get('albergue'))}»")
    if tipo == "albergado" and albergue_id is None:
        raise ValueError("es persona albergada pero no eligió albergue")

    integ_txt = limpiar(r.get("integrantes_familia"))
    integrantes = int(float(integ_txt)) if integ_txt else None
    if integrantes is not None and not 1 <= integrantes <= 20:
        raise ValueError(f"integrantes fuera de rango «{integrantes}»")

    resp = parse_fecha(r.get("respondido_en"))
    resp = resp.replace(tzinfo=TZ) if resp and resp.tzinfo is None else resp

    if numero:
        clave = f"{tipo_doc or 'DOC'}:{numero}"
    else:  # sin documento: huella con nombre + ubicación + nacimiento
        base = "|".join([sin_tildes(nombre), sin_tildes(torre), sin_tildes(limpiar(r.get("apto_cargo"))), str(nac or "")])
        clave = "H:" + hashlib.sha1(base.encode()).hexdigest()[:20]

    return {
        "clave_origen": clave,
        "nombre": nombre,
        "tipo_documento": tipo_doc,
        "numero_documento": numero or None,
        "fecha_nacimiento": nac,
        "tipo": tipo,
        "grupo_prioritario": grupo,
        "torre_area": torre or None,
        "apto_cargo": limpiar(r.get("apto_cargo")) or None,
        "punto_encuentro": limpiar(r.get("punto_encuentro")) or None,
        "albergue_id": albergue_id,
        "integrantes_familia": integrantes,
        "condiciones_medicas": limpiar(r.get("condiciones_medicas")) or None,
        "contacto_nombre": nombre_propio(r.get("contacto_nombre", "")) or None,
        "contacto_celular": cel or None,
        "autoriza_datos": True,
        "respondido_en": resp,
    }


# ---------------------------------------------------------------------------
# LOAD
# ---------------------------------------------------------------------------
CAMPOS = ["nombre", "tipo_documento", "numero_documento", "fecha_nacimiento", "tipo", "grupo_prioritario",
          "torre_area", "apto_cargo", "punto_encuentro", "albergue_id", "integrantes_familia",
          "condiciones_medicas", "contacto_nombre", "contacto_celular", "autoriza_datos", "respondido_en"]

UPSERT = text(f"""
    INSERT INTO personas (clave_origen, {", ".join(CAMPOS)}, fuente)
    VALUES (:clave_origen, {", ".join(":" + c for c in CAMPOS)}, 'google_forms')
    ON CONFLICT (clave_origen) DO UPDATE SET
        {", ".join(f"{c} = EXCLUDED.{c}" for c in CAMPOS)},
        actualizado_en = now()
    WHERE ({", ".join("personas." + c for c in CAMPOS)})
          IS DISTINCT FROM ({", ".join("EXCLUDED." + c for c in CAMPOS)})
      AND (personas.respondido_en IS NULL OR EXCLUDED.respondido_en IS NULL
           OR EXCLUDED.respondido_en >= personas.respondido_en)
    RETURNING id, (xmax = 0) AS insertada
""")


def _crear_familia(conn, persona_id: int, albergue_id: int, integrantes: int | None):
    ya = conn.execute(text("SELECT 1 FROM familias WHERE persona_id = :p"), {"p": persona_id}).first()
    if ya:
        return
    conn.execute(text("SELECT pg_advisory_xact_lock(:a)"), {"a": albergue_id})
    n = conn.execute(text("""
        SELECT COALESCE(MAX(substring(codigo from '[0-9]+$')::int), 0) + 1
        FROM familias WHERE albergue_id = :a"""), {"a": albergue_id}).scalar()
    conn.execute(text("""
        INSERT INTO familias (albergue_id, codigo, integrantes, estado, persona_id)
        VALUES (:a, :c, :n, 'pendiente', :p)"""),
        {"a": albergue_id, "c": f"FAM-{n:03d}", "n": integrantes or 1, "p": persona_id})


def cargar(conn, registros: list[dict]) -> tuple[int, int]:
    ins = act = 0
    for reg in registros:
        fila = conn.execute(UPSERT, reg).first()
        if fila is None:
            continue  # sin cambios
        if fila.insertada:
            ins += 1
            if reg["tipo"] == "albergado" and reg["albergue_id"]:
                _crear_familia(conn, fila.id, reg["albergue_id"], reg["integrantes_familia"])
        else:
            act += 1
    return ins, act


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------
def filas_desde_tabla(valores: list[list]) -> list[dict]:
    """Convierte una tabla [[encabezados], [fila], ...] (como la envía Apps Script) en registros."""
    if not valores:
        return []
    enc = [limpiar(h) for h in valores[0]]
    return [dict(zip(enc, f)) for f in valores[1:] if any(limpiar(c) for c in f)]


def ejecutar(fuente: str | None = None, simular: bool = False, engine=None,
             filas: list[dict] | None = None, etiqueta: str = "apps_script") -> dict:
    """Corre el ETL. Origen, en este orden: `filas` ya extraídas (webhook de Apps Script),
    un archivo `fuente` (.csv/.xlsx) o Google Sheets con cuenta de servicio."""
    engine = engine or get_engine()
    if not schema_exists(engine):
        log.info("La base está vacía: aplicando db/schema.sql")
        apply_schema(engine)

    if filas is not None:
        pass
    elif fuente:
        etiqueta, filas = f"archivo:{Path(fuente).name}", extraer_archivo(fuente)
    else:
        sheet_id = os.environ.get("GOOGLE_SHEET_ID")
        cred = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not sheet_id or not cred:
            raise SystemExit("Sin origen de datos: usa el botón \"Enviar a DataLife\" de la hoja (Apps Script), "
                             "sube el .csv/.xlsx aquí, o configura GOOGLE_SHEET_ID y GOOGLE_SERVICE_ACCOUNT_JSON.")
        etiqueta, filas = f"gsheet:{sheet_id}", extraer_google_sheet(sheet_id, cred)
    log.info("Extraídas %d filas de %s", len(filas), etiqueta)

    with engine.connect() as c:
        albergues = {sin_tildes(n): i for i, n in c.execute(text("SELECT id, nombre FROM albergues"))}
    res = transformar(filas, albergues)
    log.info("Transformadas: %d válidas, %d rechazadas", len(res.validas), len(res.rechazos))
    for fila, motivo in res.rechazos:
        log.warning("Fila %d rechazada: %s", fila, motivo)

    resumen = {"fuente": etiqueta, "leidas": len(filas), "validas": len(res.validas),
               "rechazadas": len(res.rechazos), "insertadas": 0, "actualizadas": 0,
               "rechazos": [{"fila": f, "motivo": m} for f, m in res.rechazos]}
    if simular:
        return resumen

    with engine.begin() as c:
        ej = c.execute(text("INSERT INTO etl_ejecuciones (fuente, filas_leidas) VALUES (:f, :n) RETURNING id"),
                       {"f": etiqueta, "n": len(filas)}).scalar()
    try:
        with engine.begin() as c:
            ins, act = cargar(c, res.validas)
            for fila, motivo in res.rechazos:
                c.execute(text("INSERT INTO etl_rechazos (ejecucion_id, fila, motivo) VALUES (:e, :f, :m)"),
                          {"e": ej, "f": fila, "m": motivo})
            c.execute(text("""UPDATE etl_ejecuciones SET fin = now(), estado = 'ok', insertadas = :i,
                              actualizadas = :a, rechazadas = :r WHERE id = :e"""),
                      {"i": ins, "a": act, "r": len(res.rechazos), "e": ej})
            c.execute(text("INSERT INTO bitacora (tipo, detalle) VALUES ('etl', :d)"),
                      {"d": f"ETL {etiqueta}: {ins} nuevas, {act} actualizadas, {len(res.rechazos)} rechazadas"})
    except Exception as e:
        with engine.begin() as c:
            c.execute(text("UPDATE etl_ejecuciones SET fin = now(), estado = 'error', mensaje = :m WHERE id = :e"),
                      {"m": str(e)[:2000], "e": ej})
        raise
    resumen.update(insertadas=ins, actualizadas=act, ejecucion_id=ej)
    log.info("Cargadas: %d nuevas, %d actualizadas", ins, act)
    return resumen


def main(argv=None):
    ap = argparse.ArgumentParser(description="ETL de DataLife: Google Sheets → PostgreSQL")
    ap.add_argument("--fuente", help="archivo .csv o .xlsx exportado de la hoja (si no, lee Google Sheets)")
    ap.add_argument("--simular", action="store_true", help="no escribe en la base, solo muestra el resultado")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    resumen = ejecutar(args.fuente, args.simular)
    print(json.dumps(resumen, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
