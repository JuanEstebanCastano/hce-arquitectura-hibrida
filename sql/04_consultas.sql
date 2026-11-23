-- =====================================================================
--  Archivo 04 de 05 — Operaciones de consulta sobre la capa ACID
--
--  Cada consulta está numerada igual que en el benchmark (OP-n) y en el
--  informe técnico, para que la evidencia sea rastreable de punta a punta.
-- =====================================================================
SET search_path TO hosp, public;
\timing on

-- =====================================================================
-- OP-2 · Lectura puntual: últimas 50 lecturas de una admisión
--        Patrón: "abrir la gráfica del paciente de la cama 7".
-- =====================================================================
SELECT medido_en, fc, spo2, pas, pad
  FROM telemetria_signos
 WHERE uuid_admision = (SELECT uuid_admision FROM admision
                         WHERE estado = 'ABIERTA' ORDER BY id_admision LIMIT 1)
 ORDER BY medido_en DESC
 LIMIT 50;

-- =====================================================================
-- OP-3 · Ventana temporal con filtro: alarmas de las últimas 6 horas
--        Patrón: tablero de seguridad del paciente.
-- =====================================================================
SELECT count(*) AS lecturas_en_alarma,
       count(DISTINCT uuid_admision) AS pacientes_afectados
  FROM telemetria_signos
 WHERE alarma
   AND medido_en >= (SELECT max(medido_en) - INTERVAL '6 hours' FROM telemetria_signos);

-- =====================================================================
-- OP-4 · Agregación por hora: tendencia clínica de una admisión
-- =====================================================================
SELECT date_trunc('hour', medido_en)                AS hora,
       round(avg(fc), 1)                            AS fc_promedio,
       max(fc)                                      AS fc_maxima,
       min(spo2)                                    AS spo2_minima,
       round(avg(spo2), 1)                          AS spo2_promedio,
       count(*) FILTER (WHERE alarma)               AS alarmas
  FROM telemetria_signos
 WHERE uuid_admision = (SELECT uuid_admision FROM admision
                         WHERE estado = 'ABIERTA' ORDER BY id_admision LIMIT 1)
 GROUP BY 1
 ORDER BY 1;

-- =====================================================================
-- OP-5 · Agregación global sobre 1.008.000 registros
-- =====================================================================
SELECT uuid_admision, count(*) AS alarmas
  FROM telemetria_signos
 WHERE alarma
 GROUP BY uuid_admision
 ORDER BY alarmas DESC
 LIMIT 10;

-- =====================================================================
-- OP-6 · Lado relacional de la consulta híbrida
--        Devuelve la IDENTIDAD; la telemetría la aporta MongoDB.
-- =====================================================================
SELECT uuid_admision, paciente, documento, sede, cama, medico_tratante
  FROM v_admisiones_activas
 WHERE servicio = 'UCI'
 LIMIT 20;

-- =====================================================================
-- OP-8 · Transacción multi-entidad: egreso hospitalario
--        Cuatro tablas, una sola unidad atómica.
-- =====================================================================
-- psql no admite subconsultas como argumento de CALL: se resuelve antes con \gset
SELECT id_admision AS adm, id_cama AS cam
  FROM admision WHERE estado = 'ABIERTA' ORDER BY id_admision LIMIT 1 \gset

BEGIN;
    CALL sp_egresar_paciente(:adm, 'MEJORIA', 'I10X');

    -- Comprobación dentro de la misma transacción: la admisión quedó cerrada,
    -- la cama pasó a LIMPIEZA y la auditoría quedó escrita. Todo o nada.
    SELECT a.id_admision, a.estado, a.tipo_egreso, c.estado AS estado_cama
      FROM admision a LEFT JOIN cama c ON c.id_cama = a.id_cama
     WHERE a.id_admision = :adm;

    SELECT id_admision, id_cama_liberada, detalle
      FROM auditoria_egreso WHERE id_admision = :adm;
COMMIT;

-- Demostración de ATOMICIDAD: un segundo egreso sobre la misma admisión debe
-- ser rechazado por el procedimiento y no dejar nada a medias.
BEGIN;
    DO $$
    DECLARE v_id BIGINT;
    BEGIN
        SELECT id_admision INTO v_id FROM auditoria_egreso
         ORDER BY id_auditoria DESC LIMIT 1;
        BEGIN
            CALL sp_egresar_paciente(v_id, 'MEJORIA', NULL);
            RAISE NOTICE 'No debería llegar aquí: el egreso duplicado fue aceptado';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Rechazado como se esperaba: %', SQLERRM;
        END;
    END $$;
ROLLBACK;

-- =====================================================================
-- Consultas de interés clínico-administrativo (evidencia adicional)
-- =====================================================================

-- Ocupación por sede y servicio
SELECT s.nombre AS sede, pb.servicio,
       count(*) FILTER (WHERE c.estado = 'OCUPADA') AS ocupadas,
       count(*)                                     AS camas,
       round(100.0 * count(*) FILTER (WHERE c.estado = 'OCUPADA') / count(*), 1) AS pct_ocupacion
  FROM cama c
  JOIN pabellon pb ON pb.id_pabellon = c.id_pabellon
  JOIN sede s      ON s.id_sede = pb.id_sede
 GROUP BY 1, 2
 ORDER BY pct_ocupacion DESC, 1, 2;

-- Estancia promedio por diagnóstico de ingreso (admisiones cerradas)
SELECT d.codigo, d.descripcion,
       count(*) AS egresos,
       round(avg(EXTRACT(EPOCH FROM (a.fecha_egreso - a.fecha_ingreso)) / 86400.0), 2) AS dias_estancia_prom
  FROM admision a
  JOIN diagnostico_cie10 d ON d.codigo = a.cod_dx_ingreso
 WHERE a.estado = 'CERRADA'
 GROUP BY 1, 2
HAVING count(*) > 50
 ORDER BY dias_estancia_prom DESC
 LIMIT 10;

-- Mortalidad hospitalaria por subregión
SELECT s.subregion,
       count(*) AS egresos,
       count(*) FILTER (WHERE a.tipo_egreso = 'FALLECIMIENTO') AS fallecimientos,
       round(100.0 * count(*) FILTER (WHERE a.tipo_egreso = 'FALLECIMIENTO') / count(*), 2) AS tasa_pct
  FROM admision a
  JOIN sede s ON s.id_sede = a.id_sede
 WHERE a.estado = 'CERRADA'
 GROUP BY 1
 ORDER BY tasa_pct DESC;

\timing off
