# DataLife

**DataLife** es una plataforma web para gestionar albergues en emergencias: quién está albergado, cuántos cupos quedan,
qué recursos hay y qué falta, y cómo responde la gente en un simulacro de evacuación.

## Por qué lo hacemos

Cuando ocurre una emergencia (inundación, incendio, sismo), los albergues suelen manejarse con listas en papel u hojas
sueltas. Así es difícil saber a tiempo si un albergue está lleno, si se va a acabar la comida o el agua, o a dónde remitir
a una familia que llega. DataLife reúne esa información en un solo lugar para que coordinadores, voluntarios y brigadistas
tomen decisiones con datos y no a ciegas.

## Cómo funciona

```
Google Form ──► Google Sheets ──► ETL en Python ──► PostgreSQL (Supabase) ──► App web (Flask) ──► Render
 (registro)      (respuestas)     (limpia y valida)                             (por roles)
```

1. Las personas se registran en un **formulario de Google** (con consentimiento de datos, Ley 1581).
2. Un **ETL** limpia y valida las respuestas y las guarda en la base de datos.
3. La **aplicación web** muestra y opera esos datos según el rol de quien ingresa:
   - **Cupos**: ocupación de cada albergue, familias en espera, asignar cupo, registrar salidas y remitir a otro albergue.
   - **Recursos**: inventario, días que alcanza cada recurso, necesidades y entregas.
   - **Alertas**: sobreocupación, escasez y necesidades sin atender, calculadas automáticamente.
   - **Censo** de personas y **simulacros** con registro por QR e informe final.
   - **Usuarios**: fundador, coordinador, voluntario y brigadista, cada uno con sus permisos.

## Base de datos

PostgreSQL alojado en **Supabase**. Todo el modelo está en [`db/schema.sql`](db/schema.sql): tablas de `albergues`,
`personas`, `familias`, `recursos`, `inventario`, `necesidades`, `entregas`, `simulacros`, `checkins`, `usuarios` y
`roles`, más la bitácora y el historial del ETL. Las vistas `v_ocupacion`, `v_inventario_dias` y `v_alertas` calculan la
ocupación, los días de inventario y las alertas directamente en SQL, así que siempre están al día.

## API

El backend está hecho en **Python con Flask** ([`app/`](app/)). Además de las páginas, expone dos puntos técnicos
([`app/api.py`](app/api.py)):

- `GET /salud`: confirma que el servicio y la base de datos responden.
- `POST /api/etl/ejecutar`: lo llama el formulario de Google al recibir una respuesta y carga los datos nuevos
  (protegido con el token `X-DataLife-Token`).

## Frontend

Plantillas **Jinja** renderizadas por Flask ([`app/templates/`](app/templates/)) con un CSS propio
([`app/static/datalife.css`](app/static/datalife.css)). Tema oscuro por defecto con opción clara, tipografía Manrope,
menú lateral en computador y navegación inferior en celular.

## Página en vivo

**https://datalife.onrender.com**
