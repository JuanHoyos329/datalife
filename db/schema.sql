-- =====================================================================
--  DataLife · Esquema de base de datos (PostgreSQL 14+)
--  Ejecutar una sola vez sobre una base vacía:
--     psql "$DATABASE_URL" -f db/schema.sql
--  (docker-compose lo ejecuta solo al crear el volumen de Postgres, y la
--   app lo ejecuta con `flask --app app init-db` si las tablas no existen)
--  Todos los nombres están en español para que coincidan con la app.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. Roles y usuarios
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS roles (
    codigo              VARCHAR(20) PRIMARY KEY,           -- fundador, coordinador, voluntario, brigadista
    nombre              VARCHAR(60)  NOT NULL,
    nivel               SMALLINT     NOT NULL,             -- 1 = más alto
    puede_simulacros    BOOLEAN      NOT NULL DEFAULT FALSE, -- módulo Simulacros + censo de personas
    puede_operar        BOOLEAN      NOT NULL DEFAULT FALSE, -- registrar cupos, necesidades y entregas
    puede_admin         BOOLEAN      NOT NULL DEFAULT FALSE  -- gestionar usuarios y correr el ETL
);

INSERT INTO roles (codigo, nombre, nivel, puede_simulacros, puede_operar, puede_admin) VALUES
    ('fundador',    'Fundador',              1, TRUE,  TRUE,  TRUE),
    ('coordinador', 'Coordinador',           2, TRUE,  TRUE,  TRUE),
    ('voluntario',  'Voluntario / Operario', 3, FALSE, TRUE,  FALSE),
    ('brigadista',  'Brigadista',            3, FALSE, TRUE,  FALSE)
ON CONFLICT (codigo) DO NOTHING;

CREATE TABLE IF NOT EXISTS albergues (
    id              SERIAL PRIMARY KEY,
    codigo          VARCHAR(30)  NOT NULL UNIQUE,
    nombre          VARCHAR(120) NOT NULL UNIQUE,
    direccion       VARCHAR(200),
    capacidad       INTEGER      NOT NULL CHECK (capacidad > 0),
    abierto_en      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    activo          BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS usuarios (
    id              SERIAL PRIMARY KEY,
    usuario         VARCHAR(40)  NOT NULL UNIQUE,
    nombre          VARCHAR(120) NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,                 -- hash scrypt/pbkdf2 (nunca la clave en texto)
    rol             VARCHAR(20)  NOT NULL REFERENCES roles(codigo),
    albergue_id     INTEGER      REFERENCES albergues(id), -- albergue por defecto (opcional)
    activo          BOOLEAN      NOT NULL DEFAULT TRUE,
    pendiente       BOOLEAN      NOT NULL DEFAULT FALSE,   -- cuenta creada desde "Crear cuenta", espera aprobación
    creado_en       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    ultimo_ingreso  TIMESTAMPTZ
);

-- ---------------------------------------------------------------------
-- 2. Censo de personas (lo que llega desde Google Forms → Sheets → ETL)
-- ---------------------------------------------------------------------
CREATE SEQUENCE IF NOT EXISTS personas_codigo_seq START 1;

CREATE TABLE IF NOT EXISTS personas (
    id                   SERIAL PRIMARY KEY,
    codigo               VARCHAR(12)  NOT NULL UNIQUE
                         DEFAULT ('P-' || lpad(nextval('personas_codigo_seq')::text, 4, '0')),
    clave_origen         VARCHAR(80)  NOT NULL UNIQUE,     -- documento o hash: evita duplicados del ETL
    nombre               VARCHAR(150) NOT NULL,
    tipo_documento       VARCHAR(20),
    numero_documento     VARCHAR(30),
    fecha_nacimiento     DATE,
    tipo                 VARCHAR(20)  NOT NULL
                         CHECK (tipo IN ('residente','trabajador','visitante','albergado')),
    grupo_prioritario    VARCHAR(20)  NOT NULL DEFAULT 'ninguno'
                         CHECK (grupo_prioritario IN ('ninguno','movilidad','mayor','menor','gestante')),
    torre_area           VARCHAR(60),
    apto_cargo           VARCHAR(60),
    punto_encuentro      VARCHAR(80),
    albergue_id          INTEGER      REFERENCES albergues(id),
    integrantes_familia  SMALLINT     CHECK (integrantes_familia BETWEEN 1 AND 20),
    condiciones_medicas  TEXT,
    contacto_nombre      VARCHAR(150),
    contacto_celular     VARCHAR(20),
    autoriza_datos       BOOLEAN      NOT NULL CHECK (autoriza_datos),   -- Ley 1581: sin autorización no se guarda
    activo               BOOLEAN      NOT NULL DEFAULT TRUE,
    fecha_baja           TIMESTAMPTZ,
    fuente               VARCHAR(20)  NOT NULL DEFAULT 'google_forms',
    respondido_en        TIMESTAMPTZ,                      -- "Marca temporal" del formulario
    creado_en            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    actualizado_en       TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_personas_nombre ON personas (lower(nombre));
CREATE INDEX IF NOT EXISTS ix_personas_activo ON personas (activo);

-- ---------------------------------------------------------------------
-- 3. Albergues: familias, cupos, recursos, necesidades y entregas
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS familias (
    id                  SERIAL PRIMARY KEY,
    albergue_id         INTEGER     NOT NULL REFERENCES albergues(id),
    codigo              VARCHAR(20) NOT NULL,              -- FAM-001 (único dentro del albergue)
    integrantes         SMALLINT    NOT NULL CHECK (integrantes BETWEEN 1 AND 20),
    estado              VARCHAR(12) NOT NULL DEFAULT 'pendiente'
                        CHECK (estado IN ('pendiente','alojada','salida')),
    persona_id          INTEGER     REFERENCES personas(id), -- responsable (si vino del formulario)
    registrada_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    alojada_en          TIMESTAMPTZ,
    salida_en           TIMESTAMPTZ,
    UNIQUE (albergue_id, codigo)
);

CREATE TABLE IF NOT EXISTS recursos (
    codigo              VARCHAR(20) PRIMARY KEY,           -- agua, alimentos, medicamentos, cobijas, higiene
    nombre              VARCHAR(80) NOT NULL,
    tipo                VARCHAR(30) NOT NULL,
    unidad              VARCHAR(20) NOT NULL,
    consumo_pp_dia      NUMERIC(8,3) NOT NULL,             -- consumo por persona por día
    vital               BOOLEAN     NOT NULL DEFAULT FALSE,
    orden               SMALLINT    NOT NULL DEFAULT 0
);

INSERT INTO recursos (codigo, nombre, tipo, unidad, consumo_pp_dia, vital, orden) VALUES
    ('agua',         'Agua potable',         'Vital',          'L',        3,    TRUE,  1),
    ('alimentos',    'Raciones de alimento', 'Vital',          'raciones', 3,    TRUE,  2),
    ('medicamentos', 'Kits de medicamentos', 'Vital',          'kits',     0.1,  TRUE,  3),
    ('cobijas',      'Cobijas',              'Abrigo',         'unid.',    0.05, FALSE, 4),
    ('higiene',      'Kits de higiene',      'Complementario', 'kits',     0.2,  FALSE, 5)
ON CONFLICT (codigo) DO NOTHING;

CREATE TABLE IF NOT EXISTS inventario (
    albergue_id         INTEGER     NOT NULL REFERENCES albergues(id),
    recurso             VARCHAR(20) NOT NULL REFERENCES recursos(codigo),
    existencias         NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (existencias >= 0),
    actualizado_en      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (albergue_id, recurso)
);

CREATE TABLE IF NOT EXISTS necesidades (
    id                  SERIAL PRIMARY KEY,
    familia_id          INTEGER     NOT NULL REFERENCES familias(id),
    recurso             VARCHAR(20) NOT NULL REFERENCES recursos(codigo),
    prioridad           VARCHAR(5)  NOT NULL CHECK (prioridad IN ('alta','media','baja')),
    estado              VARCHAR(10) NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente','atendida')),
    registrada_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    atendida_en         TIMESTAMPTZ
);

CREATE SEQUENCE IF NOT EXISTS entregas_codigo_seq START 1;

CREATE TABLE IF NOT EXISTS entregas (
    id                  SERIAL PRIMARY KEY,
    codigo              VARCHAR(20) NOT NULL UNIQUE DEFAULT ('ENT-' || lpad(nextval('entregas_codigo_seq')::text, 4, '0')),
    albergue_id         INTEGER     NOT NULL REFERENCES albergues(id),
    familia_id          INTEGER     NOT NULL REFERENCES familias(id),
    recurso             VARCHAR(20) NOT NULL REFERENCES recursos(codigo),
    cantidad            NUMERIC(10,2) NOT NULL CHECK (cantidad > 0),
    fecha               DATE        NOT NULL DEFAULT CURRENT_DATE,
    registrada_por      INTEGER     REFERENCES usuarios(id),
    registrada_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 4. Simulacros (solo fundador y coordinador)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS simulacros (
    id                  SERIAL PRIMARY KEY,
    codigo              VARCHAR(12) NOT NULL UNIQUE,       -- SIM-001
    tipo                VARCHAR(12) NOT NULL CHECK (tipo IN ('sismo','incendio','inundacion','otro')),
    descripcion         VARCHAR(200) NOT NULL,
    programado_para     TIMESTAMPTZ NOT NULL,
    meta_pct            SMALLINT    NOT NULL DEFAULT 92 CHECK (meta_pct BETWEEN 1 AND 100),
    iniciado_en         TIMESTAMPTZ,
    finalizado_en       TIMESTAMPTZ,
    observaciones       TEXT,
    creado_por          INTEGER     REFERENCES usuarios(id),
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS checkins (
    simulacro_id        INTEGER     NOT NULL REFERENCES simulacros(id) ON DELETE CASCADE,
    persona_id          INTEGER     NOT NULL REFERENCES personas(id),
    punto_encuentro     VARCHAR(80),
    registrado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrado_por      INTEGER     REFERENCES usuarios(id),
    PRIMARY KEY (simulacro_id, persona_id)                 -- no permite check-in duplicado
);

-- ---------------------------------------------------------------------
-- 5. Trazabilidad: bitácora y ejecuciones del ETL
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bitacora (
    id                  BIGSERIAL PRIMARY KEY,
    en                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    usuario_id          INTEGER     REFERENCES usuarios(id),
    tipo                VARCHAR(20) NOT NULL,              -- sesion, censo, privacidad, albergue, simulacro, etl…
    detalle             TEXT        NOT NULL
);

CREATE TABLE IF NOT EXISTS etl_ejecuciones (
    id                  SERIAL PRIMARY KEY,
    inicio              TIMESTAMPTZ NOT NULL DEFAULT now(),
    fin                 TIMESTAMPTZ,
    fuente              VARCHAR(200),
    filas_leidas        INTEGER     NOT NULL DEFAULT 0,
    insertadas          INTEGER     NOT NULL DEFAULT 0,
    actualizadas        INTEGER     NOT NULL DEFAULT 0,
    rechazadas          INTEGER     NOT NULL DEFAULT 0,
    estado              VARCHAR(10) NOT NULL DEFAULT 'corriendo' CHECK (estado IN ('corriendo','ok','error')),
    mensaje             TEXT
);

CREATE TABLE IF NOT EXISTS etl_rechazos (
    id                  SERIAL PRIMARY KEY,
    ejecucion_id        INTEGER     NOT NULL REFERENCES etl_ejecuciones(id) ON DELETE CASCADE,
    fila                INTEGER     NOT NULL,              -- número de fila en la hoja
    motivo              TEXT        NOT NULL
);

-- ---------------------------------------------------------------------
-- 6. Vistas de indicadores (las usan las páginas de Cupos, Recursos y Alertas)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ocupacion AS
SELECT a.id                                         AS albergue_id,
       a.nombre,
       a.capacidad,
       COALESCE(SUM(f.integrantes) FILTER (WHERE f.estado = 'alojada'), 0)::int   AS ocupados,
       GREATEST(a.capacidad - COALESCE(SUM(f.integrantes) FILTER (WHERE f.estado = 'alojada'), 0), 0)::int AS disponibles,
       COUNT(*) FILTER (WHERE f.estado = 'pendiente')::int                       AS familias_en_espera,
       COALESCE(SUM(f.integrantes) FILTER (WHERE f.estado = 'pendiente'), 0)::int AS personas_en_espera,
       ROUND(COALESCE(SUM(f.integrantes) FILTER (WHERE f.estado = 'alojada'), 0)::numeric / a.capacidad, 4) AS ocupacion
FROM albergues a
LEFT JOIN familias f ON f.albergue_id = a.id
WHERE a.activo
GROUP BY a.id, a.nombre, a.capacidad;

CREATE OR REPLACE VIEW v_inventario_dias AS
SELECT i.albergue_id,
       r.codigo   AS recurso,
       r.nombre,
       r.tipo,
       r.unidad,
       r.vital,
       r.orden,
       r.consumo_pp_dia,
       i.existencias,
       o.ocupados * r.consumo_pp_dia AS consumo_dia,
       CASE WHEN o.ocupados * r.consumo_pp_dia > 0
            THEN ROUND(i.existencias / (o.ocupados * r.consumo_pp_dia), 1) END AS dias_restantes
FROM inventario i
JOIN recursos r   ON r.codigo = i.recurso
JOIN v_ocupacion o ON o.albergue_id = i.albergue_id;

-- Alertas: mismas reglas del prototipo HTML
--   ocupación >= 100 % crítica, >= 90 % atención
--   familia en espera que no cabe: crítica
--   recurso con < 2 días: crítica, < 4 días: atención
--   necesidad de prioridad alta sin atender: atención
CREATE OR REPLACE VIEW v_alertas AS
SELECT o.albergue_id, 'critica'::text AS nivel, 'ocupacion'::text AS categoria,
       'Sobreocupación: albergue lleno' AS titulo,
       'Se usan los ' || o.capacidad || ' cupos. El sistema bloquea nuevas asignaciones; coordinar remisión a otro albergue.' AS detalle
FROM v_ocupacion o WHERE o.ocupacion >= 1
UNION ALL
SELECT o.albergue_id, 'atencion', 'ocupacion',
       'Ocupación alta (' || ROUND(o.ocupacion * 100) || ' %)',
       'Quedan ' || o.disponibles || ' cupos. Preparar plan de contingencia para nuevos ingresos.'
FROM v_ocupacion o WHERE o.ocupacion >= 0.9 AND o.ocupacion < 1
UNION ALL
SELECT f.albergue_id, 'critica', 'cupos',
       f.codigo || ' no cabe en el albergue',
       'Necesita ' || f.integrantes || ' cupos y quedan ' || o.disponibles || '. Requiere remisión.'
FROM familias f JOIN v_ocupacion o ON o.albergue_id = f.albergue_id
WHERE f.estado = 'pendiente' AND f.integrantes > o.disponibles
UNION ALL
SELECT d.albergue_id,
       CASE WHEN d.dias_restantes < 2 THEN 'critica' ELSE 'atencion' END,
       'inventario',
       CASE WHEN d.dias_restantes < 2 THEN 'Escasez de ' ELSE 'Inventario bajo: ' END || lower(d.nombre),
       'Quedan ' || d.existencias || ' ' || d.unidad || ', alcanza para ' || d.dias_restantes || ' días al consumo actual.'
FROM v_inventario_dias d WHERE d.dias_restantes < 4
UNION ALL
SELECT f.albergue_id, 'atencion', 'necesidades',
       'Necesidad alta sin atender: ' || f.codigo,
       r.nombre || ' · registrada el ' || to_char(n.registrada_en AT TIME ZONE 'America/Bogota', 'DD/MM/YYYY HH24:MI')
FROM necesidades n
JOIN familias f ON f.id = n.familia_id
JOIN recursos r ON r.codigo = n.recurso
WHERE n.estado = 'pendiente' AND n.prioridad = 'alta' AND f.estado <> 'salida';

-- ---------------------------------------------------------------------
-- 7. Seguridad para Supabase: activa RLS en todas las tablas.
--    Supabase publica el esquema "public" por su API REST; con RLS activo y sin
--    políticas, nadie puede leer los datos con la clave anónima. La app y el ETL
--    se conectan como dueños de las tablas, así que no les afecta.
--    En un PostgreSQL normal (Docker, Render, Neon) no cambia nada.
-- ---------------------------------------------------------------------
DO $$
DECLARE t record;
BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
    END LOOP;
END $$;
ALTER VIEW v_ocupacion       SET (security_invoker = true);
ALTER VIEW v_inventario_dias SET (security_invoker = true);
ALTER VIEW v_alertas         SET (security_invoker = true);

COMMIT;
