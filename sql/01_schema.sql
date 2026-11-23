-- =====================================================================
--  Proyecto Integrador — Unidad 3: Arquitectura híbrida PostgreSQL + MongoDB
--  HCE Básica — Red de Hospitales Públicos de la Gobernación de Antioquia
--  Subsistema: HOSPITALIZACIÓN  |  Capa ACID (PostgreSQL 16)
--
--  Institución Universitaria Pascual Bravo — Bases de Datos II
--  Equipo: Juan Esteban Castaño Montoya, Jhonjader Correa Ruiz
--
--  Archivo 01 de 05 — DDL: esquema, dominios, tablas, restricciones.
--  Ejecutar:  psql -U postgres -d hce_antioquia -f sql/01_schema.sql
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. Base de datos y esquema
-- ---------------------------------------------------------------------
-- CREATE DATABASE hce_antioquia ENCODING 'UTF8';
-- \c hce_antioquia

CREATE SCHEMA IF NOT EXISTS hosp;
SET search_path TO hosp, public;

-- pgcrypto: gen_random_uuid() para la llave puente hacia MongoDB.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------
-- 1. Dominios y tipos — las reglas de negocio se declaran en el motor,
--    no en la aplicación. Esta es la diferencia práctica de la capa ACID.
-- ---------------------------------------------------------------------
DROP DOMAIN IF EXISTS dom_documento CASCADE;
CREATE DOMAIN dom_documento AS VARCHAR(20)
    CHECK (VALUE ~ '^[0-9A-Z]{5,20}$');

DROP DOMAIN IF EXISTS dom_email CASCADE;
CREATE DOMAIN dom_email AS VARCHAR(120)
    CHECK (VALUE IS NULL OR VALUE ~ '^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$');

DROP TYPE IF EXISTS tipo_documento CASCADE;
CREATE TYPE tipo_documento AS ENUM ('CC','TI','CE','RC','PA','NUIP');

DROP TYPE IF EXISTS sexo_biologico CASCADE;
CREATE TYPE sexo_biologico AS ENUM ('M','F','I');

DROP TYPE IF EXISTS estado_admision CASCADE;
CREATE TYPE estado_admision AS ENUM ('ABIERTA','EN_TRASLADO','CERRADA','ANULADA');

DROP TYPE IF EXISTS estado_cama CASCADE;
CREATE TYPE estado_cama AS ENUM ('DISPONIBLE','OCUPADA','LIMPIEZA','MANTENIMIENTO');

DROP TYPE IF EXISTS tipo_egreso CASCADE;
CREATE TYPE tipo_egreso AS ENUM ('MEJORIA','REMISION','VOLUNTARIO','FALLECIMIENTO','FUGA');

-- ---------------------------------------------------------------------
-- 2. Datos maestros (baja volatilidad, alta criticidad)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS sede CASCADE;
CREATE TABLE sede (
    id_sede         SMALLSERIAL PRIMARY KEY,
    codigo_habilita VARCHAR(12)  NOT NULL UNIQUE,   -- código REPS habilitación
    nombre          VARCHAR(120) NOT NULL,
    municipio       VARCHAR(80)  NOT NULL,
    subregion       VARCHAR(40)  NOT NULL,          -- subregiones de Antioquia
    nivel_atencion  SMALLINT     NOT NULL CHECK (nivel_atencion BETWEEN 1 AND 3),
    activo          BOOLEAN      NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE sede IS 'Hospitales de la red pública departamental. Dato maestro: ACID.';

DROP TABLE IF EXISTS eps CASCADE;
CREATE TABLE eps (
    id_eps     SMALLSERIAL PRIMARY KEY,
    nit        VARCHAR(15)  NOT NULL UNIQUE,
    nombre     VARCHAR(120) NOT NULL,
    regimen    VARCHAR(20)  NOT NULL CHECK (regimen IN ('CONTRIBUTIVO','SUBSIDIADO','ESPECIAL'))
);

DROP TABLE IF EXISTS diagnostico_cie10 CASCADE;
CREATE TABLE diagnostico_cie10 (
    codigo      VARCHAR(6) PRIMARY KEY,
    descripcion VARCHAR(200) NOT NULL,
    capitulo    VARCHAR(80)  NOT NULL
);

-- ---------------------------------------------------------------------
-- 3. Identidad del paciente — el núcleo que JAMÁS puede ser eventual.
--    Un documento duplicado o un UUID inconsistente rompe la trazabilidad
--    clínica completa; por eso vive aquí y no en la capa documental.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS paciente CASCADE;
CREATE TABLE paciente (
    id_paciente     BIGSERIAL PRIMARY KEY,
    uuid_paciente   UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE, -- llave puente -> MongoDB
    tipo_doc        tipo_documento NOT NULL,
    num_doc         dom_documento  NOT NULL,
    primer_nombre   VARCHAR(50) NOT NULL,
    segundo_nombre  VARCHAR(50),
    primer_apellido VARCHAR(50) NOT NULL,
    segundo_apellido VARCHAR(50),
    -- Límite inferior inmutable: un CHECK no puede usar CURRENT_DATE
    -- (no es IMMUTABLE y rompería un pg_dump/restore con otra fecha).
    fecha_nacimiento DATE NOT NULL CHECK (fecha_nacimiento >= DATE '1900-01-01'),
    sexo            sexo_biologico NOT NULL,
    id_eps          SMALLINT REFERENCES eps(id_eps),
    telefono        VARCHAR(20),
    email           dom_email,
    municipio_res   VARCHAR(80),
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Regla de identidad: la pareja (tipo, número) es única en toda la red.
    CONSTRAINT uq_paciente_documento UNIQUE (tipo_doc, num_doc)
);
COMMENT ON COLUMN paciente.uuid_paciente IS
  'Identificador estable que se replica en MongoDB. MongoDB nunca lo genera: solo lo referencia.';

-- ---------------------------------------------------------------------
-- 4. Talento humano en salud
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS profesional CASCADE;
CREATE TABLE profesional (
    id_profesional  SERIAL PRIMARY KEY,
    registro_rethus VARCHAR(20) NOT NULL UNIQUE,
    tipo_doc        tipo_documento NOT NULL,
    num_doc         dom_documento  NOT NULL,
    nombre_completo VARCHAR(120) NOT NULL,
    especialidad    VARCHAR(80)  NOT NULL,
    id_sede         SMALLINT NOT NULL REFERENCES sede(id_sede),
    activo          BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_profesional_doc UNIQUE (tipo_doc, num_doc)
);

-- ---------------------------------------------------------------------
-- 5. Capacidad instalada: pabellón y cama
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS pabellon CASCADE;
CREATE TABLE pabellon (
    id_pabellon SERIAL PRIMARY KEY,
    id_sede     SMALLINT NOT NULL REFERENCES sede(id_sede),
    nombre      VARCHAR(60) NOT NULL,
    servicio    VARCHAR(40) NOT NULL
                CHECK (servicio IN ('MEDICINA_INTERNA','CIRUGIA','UCI','UCI_NEONATAL',
                                    'PEDIATRIA','GINECOBSTETRICIA','URGENCIAS_OBS')),
    CONSTRAINT uq_pabellon UNIQUE (id_sede, nombre)
);

DROP TABLE IF EXISTS cama CASCADE;
CREATE TABLE cama (
    id_cama     SERIAL PRIMARY KEY,
    id_pabellon INTEGER NOT NULL REFERENCES pabellon(id_pabellon),
    codigo      VARCHAR(12) NOT NULL,
    estado      estado_cama NOT NULL DEFAULT 'DISPONIBLE',
    CONSTRAINT uq_cama UNIQUE (id_pabellon, codigo)
);

-- ---------------------------------------------------------------------
-- 6. Admisión (estancia hospitalaria) — entidad transaccional central
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS admision CASCADE;
CREATE TABLE admision (
    id_admision     BIGSERIAL PRIMARY KEY,
    uuid_admision   UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE, -- llave puente -> MongoDB
    id_paciente     BIGINT   NOT NULL REFERENCES paciente(id_paciente),
    id_sede         SMALLINT NOT NULL REFERENCES sede(id_sede),
    id_cama         INTEGER  REFERENCES cama(id_cama),
    id_medico_tratante INTEGER NOT NULL REFERENCES profesional(id_profesional),
    fecha_ingreso   TIMESTAMPTZ NOT NULL DEFAULT now(),
    fecha_egreso    TIMESTAMPTZ,
    estado          estado_admision NOT NULL DEFAULT 'ABIERTA',
    tipo_egreso     tipo_egreso,
    cod_dx_ingreso  VARCHAR(6) NOT NULL REFERENCES diagnostico_cie10(codigo),
    -- Integridad temporal: no se puede egresar antes de ingresar.
    CONSTRAINT ck_admision_fechas CHECK (fecha_egreso IS NULL OR fecha_egreso >= fecha_ingreso),
    -- Integridad de estado: una admisión cerrada exige egreso y tipo de egreso.
    CONSTRAINT ck_admision_cierre CHECK (
        (estado = 'CERRADA' AND fecha_egreso IS NOT NULL AND tipo_egreso IS NOT NULL)
        OR (estado <> 'CERRADA')
    )
);
COMMENT ON TABLE admision IS
  'Estancia hospitalaria. Es el "ancla" transaccional: la telemetría de MongoDB se asocia a uuid_admision.';

-- Regla de ocupación: una cama no puede tener dos admisiones abiertas.
-- Índice único parcial = restricción de negocio imposible de garantizar en BASE.
CREATE UNIQUE INDEX uq_cama_ocupada_una_vez
    ON admision (id_cama) WHERE estado IN ('ABIERTA','EN_TRASLADO');

-- Un paciente no puede estar hospitalizado dos veces a la vez en la red.
CREATE UNIQUE INDEX uq_paciente_una_admision_abierta
    ON admision (id_paciente) WHERE estado IN ('ABIERTA','EN_TRASLADO');

DROP TABLE IF EXISTS admision_diagnostico CASCADE;
CREATE TABLE admision_diagnostico (
    id_admision BIGINT NOT NULL REFERENCES admision(id_admision) ON DELETE CASCADE,
    codigo_cie10 VARCHAR(6) NOT NULL REFERENCES diagnostico_cie10(codigo),
    tipo        VARCHAR(12) NOT NULL CHECK (tipo IN ('PRINCIPAL','RELACIONADO','COMPLICACION')),
    registrado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id_admision, codigo_cie10)
);

-- ---------------------------------------------------------------------
-- 7. Dispositivos de monitoreo — la frontera ACID/BASE
--    El registro del dispositivo (quién lo porta, desde cuándo) es ACID.
--    Lo que el dispositivo EMITE (millones de lecturas) es BASE.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS dispositivo CASCADE;
CREATE TABLE dispositivo (
    id_dispositivo  SERIAL PRIMARY KEY,
    serial_fabrica  VARCHAR(40) NOT NULL UNIQUE,
    tipo            VARCHAR(30) NOT NULL
                    CHECK (tipo IN ('MONITOR_MULTIPARAMETROS','WEARABLE_PULSERA',
                                    'BOMBA_INFUSION','VENTILADOR','OXIMETRO')),
    fabricante      VARCHAR(60),
    id_sede         SMALLINT NOT NULL REFERENCES sede(id_sede),
    activo          BOOLEAN NOT NULL DEFAULT TRUE
);

DROP TABLE IF EXISTS asignacion_dispositivo CASCADE;
CREATE TABLE asignacion_dispositivo (
    id_asignacion  BIGSERIAL PRIMARY KEY,
    id_dispositivo INTEGER NOT NULL REFERENCES dispositivo(id_dispositivo),
    id_admision    BIGINT  NOT NULL REFERENCES admision(id_admision),
    asignado_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
    retirado_en    TIMESTAMPTZ,
    CONSTRAINT ck_asignacion_fechas CHECK (retirado_en IS NULL OR retirado_en >= asignado_en)
);
-- Un dispositivo solo puede estar asignado a un paciente a la vez.
CREATE UNIQUE INDEX uq_dispositivo_asignado
    ON asignacion_dispositivo (id_dispositivo) WHERE retirado_en IS NULL;

-- ---------------------------------------------------------------------
-- 8. Tabla espejo de telemetría — SOLO para el benchmark comparativo.
--    Modela "qué pasaría si guardáramos los eventos masivos en la capa ACID".
--    En la arquitectura propuesta esta tabla NO existe en producción.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS telemetria_signos CASCADE;
CREATE TABLE telemetria_signos (
    id_lectura     BIGSERIAL PRIMARY KEY,
    uuid_admision  UUID        NOT NULL,
    id_dispositivo INTEGER     NOT NULL,
    medido_en      TIMESTAMPTZ NOT NULL,
    fc             SMALLINT,      -- frecuencia cardiaca (lpm)
    fr             SMALLINT,      -- frecuencia respiratoria (rpm)
    spo2           SMALLINT,      -- saturación de oxígeno (%)
    temp_c         NUMERIC(4,1),  -- temperatura (°C)
    pas            SMALLINT,      -- presión arterial sistólica
    pad            SMALLINT,      -- presión arterial diastólica
    alarma         BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT ck_tel_spo2 CHECK (spo2 IS NULL OR spo2 BETWEEN 0 AND 100)
);
COMMENT ON TABLE telemetria_signos IS
  'Espejo relacional de la colección MongoDB hce.telemetria. Existe para medir ACID vs BASE sobre la MISMA operación.';

-- ---------------------------------------------------------------------
-- 9. Auditoría de egresos (evidencia de transacción multi-tabla)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS auditoria_egreso CASCADE;
CREATE TABLE auditoria_egreso (
    id_auditoria BIGSERIAL PRIMARY KEY,
    id_admision  BIGINT NOT NULL REFERENCES admision(id_admision),
    id_cama_liberada INTEGER REFERENCES cama(id_cama),
    usuario_bd   NAME NOT NULL DEFAULT CURRENT_USER,
    ejecutado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    detalle      JSONB
);

-- ---------------------------------------------------------------------
-- 10. Procedimiento de egreso: una sola transacción, todo o nada.
--     Demuestra la propiedad de ATOMICIDAD que la capa BASE no ofrece.
-- ---------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE sp_egresar_paciente(
    p_id_admision BIGINT,
    p_tipo_egreso tipo_egreso,
    p_cod_dx_egreso VARCHAR(6) DEFAULT NULL
)
LANGUAGE plpgsql AS $$
DECLARE
    v_id_cama INTEGER;
    v_estado  estado_admision;
BEGIN
    SELECT id_cama, estado INTO v_id_cama, v_estado
      FROM admision WHERE id_admision = p_id_admision
      FOR UPDATE;                                   -- bloqueo pesimista

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Admisión % inexistente', p_id_admision;
    END IF;
    IF v_estado = 'CERRADA' THEN
        RAISE EXCEPTION 'La admisión % ya fue egresada', p_id_admision;
    END IF;

    UPDATE admision
       SET estado = 'CERRADA', fecha_egreso = now(), tipo_egreso = p_tipo_egreso
     WHERE id_admision = p_id_admision;

    IF p_cod_dx_egreso IS NOT NULL THEN
        INSERT INTO admision_diagnostico (id_admision, codigo_cie10, tipo)
        VALUES (p_id_admision, p_cod_dx_egreso, 'PRINCIPAL')
        ON CONFLICT DO NOTHING;
    END IF;

    UPDATE cama SET estado = 'LIMPIEZA' WHERE id_cama = v_id_cama;

    UPDATE asignacion_dispositivo
       SET retirado_en = now()
     WHERE id_admision = p_id_admision AND retirado_en IS NULL;

    INSERT INTO auditoria_egreso (id_admision, id_cama_liberada, detalle)
    VALUES (p_id_admision, v_id_cama,
            jsonb_build_object('tipo_egreso', p_tipo_egreso, 'dx_egreso', p_cod_dx_egreso));
END;
$$;

-- ---------------------------------------------------------------------
-- 11. Vista de apoyo para la consulta híbrida
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_admisiones_activas AS
SELECT a.uuid_admision,
       a.id_admision,
       p.uuid_paciente,
       p.tipo_doc || '-' || p.num_doc           AS documento,
       p.primer_nombre || ' ' || p.primer_apellido AS paciente,
       date_part('year', age(p.fecha_nacimiento))::INT AS edad,
       s.nombre        AS sede,
       s.municipio,
       pb.servicio,
       c.codigo        AS cama,
       pr.nombre_completo AS medico_tratante,
       a.fecha_ingreso,
       d.descripcion   AS dx_ingreso
  FROM admision a
  JOIN paciente p  ON p.id_paciente = a.id_paciente
  JOIN sede s      ON s.id_sede = a.id_sede
  LEFT JOIN cama c ON c.id_cama = a.id_cama
  LEFT JOIN pabellon pb ON pb.id_pabellon = c.id_pabellon
  JOIN profesional pr ON pr.id_profesional = a.id_medico_tratante
  JOIN diagnostico_cie10 d ON d.codigo = a.cod_dx_ingreso
 WHERE a.estado IN ('ABIERTA','EN_TRASLADO');
