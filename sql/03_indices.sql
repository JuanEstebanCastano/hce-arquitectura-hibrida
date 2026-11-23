-- =====================================================================
--  Archivo 03 de 05 — Índices de la capa ACID
--
--  Criterio: cada índice responde a un patrón de acceso REAL del
--  subsistema de hospitalización, no a "por si acaso". Un índice de más
--  en la capa transaccional es costo de escritura en cada INSERT.
-- =====================================================================
SET search_path TO hosp, public;

-- --- Identidad del paciente -------------------------------------------
-- Búsqueda por documento: el caso de uso #1 en admisiones.
-- (La restricción UNIQUE ya crea el índice; se documenta por claridad.)
-- uq_paciente_documento  → (tipo_doc, num_doc)

-- Búsqueda por apellido para el módulo de consulta asistencial.
CREATE INDEX IF NOT EXISTS ix_paciente_apellidos
    ON paciente (primer_apellido, primer_nombre);

-- Llave puente hacia MongoDB: resolución uuid → identidad.
-- (UNIQUE sobre uuid_paciente ya existe por DDL.)

-- --- Admisión ----------------------------------------------------------
-- Censo diario por sede: "¿quién está hospitalizado hoy en Rionegro?"
CREATE INDEX IF NOT EXISTS ix_admision_sede_estado
    ON admision (id_sede, estado, fecha_ingreso DESC);

-- Historia de un paciente ordenada en el tiempo.
CREATE INDEX IF NOT EXISTS ix_admision_paciente
    ON admision (id_paciente, fecha_ingreso DESC);

-- Índice parcial: solo las admisiones vivas. Es una fracción mínima de la
-- tabla y es la que consulta el tablero operativo cada pocos segundos.
CREATE INDEX IF NOT EXISTS ix_admision_abiertas
    ON admision (id_sede, fecha_ingreso) WHERE estado = 'ABIERTA';

-- Estadística epidemiológica por diagnóstico de ingreso.
CREATE INDEX IF NOT EXISTS ix_admision_dx
    ON admision (cod_dx_ingreso, fecha_ingreso);

-- --- Diagnósticos ------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_admdx_codigo
    ON admision_diagnostico (codigo_cie10);

-- --- Dispositivos ------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_asignacion_admision
    ON asignacion_dispositivo (id_admision);

-- --- Tabla espejo de telemetría (solo para el benchmark) --------------
-- Equivalentes exactos de los índices de MongoDB, para que la comparación
-- de la ingesta y de las consultas se haga en igualdad de condiciones:
--   MongoDB ix_adm_tiempo  ≡  PostgreSQL ix_tel_adm_tiempo
--   MongoDB ix_alarmas     ≡  PostgreSQL ix_tel_alarmas
CREATE INDEX IF NOT EXISTS ix_tel_adm_tiempo
    ON telemetria_signos (uuid_admision, medido_en DESC);

CREATE INDEX IF NOT EXISTS ix_tel_alarmas
    ON telemetria_signos (medido_en DESC) WHERE alarma;

-- Índice de cobertura evaluado en OP-9 del benchmark.
-- RESULTADO MEDIDO: el planeador SÍ lo elige (Parallel Index Only Scan, ver
-- bench/resultados/planes.txt) y aun así la consulta NO mejora: con ~33 % de
-- las filas cumpliendo "alarma" hay que recorrer unas 330.000 claves, y eso
-- cuesta lo mismo que el escaneo secuencial paralelo. Se conserva comentado:
-- un índice que no reduce el tiempo sigue costando espacio y penaliza cada
-- escritura. La lección es que una sugerencia de índice se valida con EXPLAIN
-- *y con el cronómetro*; que el planeador lo use no significa que convenga.
-- CREATE INDEX ix_tel_cobertura_alarmas
--     ON telemetria_signos (medido_en DESC, uuid_admision) WHERE alarma;

-- --- Verificación ------------------------------------------------------
SELECT relname AS tabla,
       indexrelname AS indice,
       pg_size_pretty(pg_relation_size(indexrelid)) AS tamano
  FROM pg_stat_user_indexes
 WHERE schemaname = 'hosp'
 ORDER BY pg_relation_size(indexrelid) DESC;
