# DataLife · despliegue real

De prototipo HTML a una aplicación web de verdad:

```
Google Form ──► Google Sheets ──► ETL en Python ──► PostgreSQL ──► App web (Flask) ──► URL pública
 (registro)      (respuestas)     (limpia y valida)   (db/schema.sql)  (roles)         (Docker + Render)
```

| Parte | Archivo |
|---|---|
| Formulario de Google (crea el Form y lo conecta a la hoja) | `google/formulario_datalife.gs` |
| Script de la base de datos (DDL PostgreSQL) | `db/schema.sql` |
| ETL Sheets → SQL | `etl/etl_personas.py` (+ `etl/ejemplo_respuestas.csv` para probar) |
| App web que lee la base | `app/` (Flask, con el mismo diseño del HTML) |
| Contenedores | `Dockerfile`, `docker-compose.yml` |
| Despliegue desde GitHub | `.github/workflows/ci.yml`, `.github/workflows/etl.yml`, `render.yaml` |
| Pruebas | `tests/` (15 pruebas: roles, ETL, cupos, entregas, simulacro completo, privacidad) |

---

## 1. Roles

| | Albergues (cupos, recursos, alertas) | Censo de personas | Simulacros | Usuarios y ETL |
|---|---|---|---|---|
| **Fundador** | ✓ | ✓ | ✓ | ✓ (puede crear cualquier rol) |
| **Coordinador** | ✓ | ✓ | ✓ | ✓ (no puede crear ni tocar fundadores) |
| **Voluntario / Operario** | ✓ (registra) | — | — | — |
| **Brigadista** | ✓ (registra) | — | — | — |

Los permisos viven en la tabla `roles` (`puede_simulacros`, `puede_operar`, `puede_admin`), no regados por el código:
para cambiar qué ve un rol basta un `UPDATE roles …`. Si un voluntario escribe `/simulacros` en la barra de direcciones
recibe **403 Acceso restringido** y el intento queda en la bitácora.

**Albergues** tiene tres páginas:
- **Disponibilidad de cupos**: cupos libres, % de ocupación, familias en espera (incluidas las que llegan del formulario),
  asignar cupo (se bloquea si no caben), registrar salida, sugerencia de remisión al albergue con más cupos y tablero de la ciudad.
- **Recursos**: inventario con días restantes al consumo actual, cobertura, necesidades por prioridad, entregas (descuentan
  inventario y cubren la necesidad) e ingresos de donaciones.
- **Alertas**: sobreocupación (≥ 90 % / 100 %), familias que no caben, escasez (< 4 días / < 2 días) y necesidades altas sin
  atender, por albergue o de todos. Son la vista SQL `v_alertas`, así que se recalculan solas.

Usuarios de demostración (clave `datalife2026`, se cambia con `DEMO_PASSWORD`): `fundador`, `coordinador`, `voluntario`, `brigada`.

---

## 2. Google Form + Google Sheets (una sola vez, unos 5 minutos)

1. Abre la hoja **«DataLife – Registro de personas»** que ya está en tu Google Drive:
   <https://docs.google.com/spreadsheets/d/14VCjl8W0LKFl_RvQExjv55aru74mtR81U1FOwQUqIFw/edit>
   (su ID, para `GOOGLE_SHEET_ID`, es `14VCjl8W0LKFl_RvQExjv55aru74mtR81U1FOwQUqIFw`).
2. Menú **Extensiones → Apps Script**. Borra lo que haya, pega todo `google/formulario_datalife.gs` y guarda.
3. Arriba elige la función **`crearFormularioDataLife`** y pulsa **Ejecutar**. Google pide permisos: *Revisar permisos → tu cuenta →
   Configuración avanzada → Ir a (no seguro) → Permitir*. Es normal: el script es tuyo.
4. En **Registro de ejecución** salen el enlace para compartir el formulario y el de edición. En la hoja aparece la pestaña
   **«Respuestas de formulario 1»**: cada respuesta llega ahí como una fila.

El formulario tiene 4 secciones: identificación; ubicación y atención prioritaria; albergue; contacto de emergencia y
**autorización Ley 1581** (obligatoria; sin ella el ETL descarta la fila). Si cambias el título de una pregunta, cámbialo también
en `COLUMNAS` del ETL.

---

## 3. Conectar tu Google Drive con el ETL (automatización)

El ETL lee la hoja con una **cuenta de servicio** de Google (un «usuario robot» con permiso solo de lectura):

1. Entra a <https://console.cloud.google.com/> y crea un proyecto (p. ej. `datalife`).
2. **APIs y servicios → Biblioteca**: habilita **Google Sheets API** y **Google Drive API**.
3. **IAM y administración → Cuentas de servicio → Crear**: nombre `datalife-etl`, sin roles. Entra a la cuenta →
   **Claves → Agregar clave → JSON**. Se descarga un `.json`: guárdalo bien y **nunca lo subas a GitHub**.
4. Copia el correo de la cuenta (`datalife-etl@….iam.gserviceaccount.com`), abre tu hoja → **Compartir** → pégalo como **Lector**.
5. El **ID de la hoja** es lo que va entre `/d/` y `/edit` en su URL.

Con eso hay tres formas de que los datos lleguen a la base (se pueden usar las tres a la vez):

| Cómo | Cuándo corre | Qué configurar |
|---|---|---|
| **GitHub Actions** (`.github/workflows/etl.yml`) | cada 15 minutos | Secretos del repo: `DATABASE_URL`, `GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON` (el JSON completo) |
| **Al instante**, desde el formulario | cada vez que alguien envía | Apps Script → Configuración del proyecto → Propiedades del script: `DATALIFE_WEBHOOK_URL` = `https://TU-APP.onrender.com/api/etl/ejecutar` y `DATALIFE_WEBHOOK_TOKEN` = el `ETL_WEBHOOK_TOKEN` de Render |
| **A mano** | cuando quieras | En la app: *Datos y usuarios → Correr ETL* (lee Sheets, o sube un .csv/.xlsx descargado de la hoja) |

### Qué hace el ETL (`etl/etl_personas.py`)
- **Extract**: lee la pestaña que tenga la columna «Nombre completo» (o un .csv/.xlsx).
- **Transform**: nombres en formato propio; documento solo dígitos; celular colombiano de 10 dígitos (quita el +57); fechas
  `dd/mm/aaaa`; opciones del formulario → códigos de la base; menor de 18 o mayor de 60 por fecha de nacimiento → atención
  prioritaria; si la misma persona respondió dos veces, gana la respuesta más reciente.
  **Rechaza**, y guarda el motivo en `etl_rechazos`: sin autorización Ley 1581, sin nombre, celular o documento inválido, albergue desconocido.
- **Load**: `INSERT … ON CONFLICT (clave_origen) DO UPDATE`, así que es idempotente (correrlo 100 veces no duplica). Cada persona
  nueva recibe su código `P-0001`. Si es «Persona albergada», se crea su familia **en espera de cupo** en el albergue elegido y
  aparece en *Cupos*.
- Cada corrida queda en `etl_ejecuciones` y en la bitácora.

```bash
python -m etl.etl_personas --fuente etl/ejemplo_respuestas.csv --simular   # ver qué haría, sin escribir
python -m etl.etl_personas                                                  # desde Google Sheets
```

---

## 4. Correr todo en tu computador con Docker

Requisito: Docker Desktop.

```bash
cp .env.example .env          # opcional: tus datos de Google
docker compose up --build     # PostgreSQL + app
```

Abre <http://localhost:8000> y entra como `coordinador` / `datalife2026`.
ETL periódico local: `docker compose --profile etl up`.

Sin Docker: `pip install -r requirements.txt`, un PostgreSQL y
`DATABASE_URL=postgresql://… flask --app app init-db --demo && flask --app app run`.

Pruebas: `TEST_DATABASE_URL=postgresql://postgres@localhost:5432/datalife_test pytest -q`

---

## 5. Publicar con URL desde GitHub (recomendado: Render)

**Por qué Render**: despliega el `Dockerfile` directo desde GitHub, da HTTPS y una URL `https://datalife-xxxx.onrender.com`,
crea el PostgreSQL desde el mismo `render.yaml` y tiene plan gratuito. Railway o Fly.io sirven igual con el mismo Dockerfile.

1. Crea un repositorio en GitHub (p. ej. `datalife`) y sube esta carpeta:
   ```bash
   git init && git add . && git commit -m "DataLife: despliegue con Docker"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/datalife.git
   git push -u origin main
   ```
2. En <https://render.com> entra con GitHub → **New → Blueprint** → elige el repo → **Apply**. Render crea `datalife-db` y el servicio `datalife`.
3. En el servicio → **Environment**, llena `DEMO_PASSWORD`, `GOOGLE_SHEET_ID` y `GOOGLE_SERVICE_ACCOUNT_JSON`.
4. En unos minutos tienes la URL pública. Cada `git push` a `main` vuelve a desplegar.
5. En GitHub → **Settings → Secrets and variables → Actions** agrega `DATABASE_URL` (la *External Database URL* de Render),
   `GOOGLE_SHEET_ID` y `GOOGLE_SERVICE_ACCOUNT_JSON` para el ETL cada 15 min. Opcional: `RENDER_DEPLOY_HOOK_URL`.

`ci.yml` corre en cada push: **pruebas** contra un PostgreSQL de GitHub → **imagen Docker** publicada en
`ghcr.io/TU_USUARIO/datalife` → aviso de **despliegue** a Render.

**Plan gratuito de Render**: el servicio se duerme tras 15 min sin visitas (la primera carga tarda cerca de un minuto) y la base
gratuita vence a los 30 días. Para la presentación sirve; para operar de verdad usa un plan pagado o una base en **Neon**
(neon.tech, gratis y no vence): pon su URL en `DATABASE_URL`.

### Antes de cargar datos reales
- `SEED_DEMO=0`; crea tus usuarios con `flask --app app crear-usuario juan fundador` (o desde *Datos y usuarios*) y desactiva los de demo.
- `SECRET_KEY` larga y aleatoria (Render la genera sola).
- Son datos personales sensibles (salud, menores): la hoja compartida solo con quien deba y el JSON de la cuenta de servicio solo
  en secretos, nunca en el repositorio.

---

## Estructura

```
app/            Flask: auth.py (login, roles, CSRF), albergues.py, simulacros.py, personas.py, usuarios.py, api.py
  templates/    Plantillas con el diseño del prototipo (static/datalife.css es el mismo CSS)
core/db.py      Conexión compartida por la app y el ETL
db/schema.sql   Tablas, roles, recursos y vistas de indicadores y alertas
etl/            ETL y archivo de ejemplo
google/         Apps Script del formulario
tests/          Pruebas con PostgreSQL real
```

Endpoints útiles: `/salud` (estado, lo usa Render) y `POST /api/etl/ejecutar` (webhook del formulario, pide `X-DataLife-Token`).
