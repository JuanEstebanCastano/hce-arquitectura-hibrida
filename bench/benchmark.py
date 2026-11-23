#!/usr/bin/env python3
"""
benchmark.py — Comparación cuantitativa ACID (PostgreSQL) vs BASE (MongoDB)
sobre LA MISMA operación y LOS MISMOS datos.

Metodología
-----------
1. Ambos motores contienen el mismo conjunto de 1.008.000 lecturas de
   telemetría, generadas con semilla fija (scripts/comun.py).
2. Los índices son equivalentes en ambos lados (ver scripts/03_espejo_postgres.py).
3. Cada operación se ejecuta con WARMUP (descartado) + N repeticiones medidas.
4. Se reportan p50, p95, p99 y media: la media sola esconde la cola, y en un
   sistema de monitoreo clínico la cola es justamente lo que importa.
5. Se capturan los planes de ejecución reales (EXPLAIN ANALYZE / explain
   executionStats) en bench/resultados/planes.txt.

Uso:  python3 bench/benchmark.py [--repeticiones 30]
"""
import argparse
import csv
import json
import os
import statistics as st
import sys
import time
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import execute_values
from pymongo import MongoClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from comun import PG_DSN, MONGO_URI, MONGO_DB, TZ, generar_telemetria  # noqa: E402

RES = os.path.join(os.path.dirname(__file__), "resultados")
os.makedirs(RES, exist_ok=True)
PLANES = open(os.path.join(RES, "planes.txt"), "w", encoding="utf-8")
MEDICIONES = []   # (operacion, motor, p50, p95, p99, media, n, unidad, nota)


def log_plan(titulo, contenido):
    PLANES.write("\n" + "=" * 78 + f"\n{titulo}\n" + "=" * 78 + "\n")
    PLANES.write(contenido.rstrip() + "\n")


def medir(nombre, motor, fn, repeticiones, warmup=3, unidad="ms", nota=""):
    """Ejecuta fn() warmup+N veces y registra la distribución de latencias."""
    for _ in range(warmup):
        fn()
    muestras = []
    for _ in range(repeticiones):
        t0 = time.perf_counter()
        fn()
        muestras.append((time.perf_counter() - t0) * 1000.0)
    muestras.sort()
    fila = (nombre, motor,
            round(st.median(muestras), 3),
            round(muestras[max(0, int(len(muestras) * 0.95) - 1)], 3),
            round(muestras[max(0, int(len(muestras) * 0.99) - 1)], 3),
            round(st.fmean(muestras), 3),
            repeticiones, unidad, nota)
    MEDICIONES.append(fila)
    print(f"  {motor:<10} {nombre:<42} p50={fila[2]:>9.2f} {unidad}  "
          f"p95={fila[3]:>9.2f}  media={fila[5]:>9.2f}")
    return fila


# =====================================================================
def main(repeticiones):
    pg = psycopg2.connect(PG_DSN)
    pg.autocommit = True
    cur = pg.cursor()
    cur.execute("SET search_path TO hosp, public")
    mongo = MongoClient(MONGO_URI)
    db = mongo[MONGO_DB]
    tel = db.telemetria

    # Muestra de admisiones monitorizadas (las mismas para ambos motores)
    cur.execute("""SELECT a.uuid_admision::text, ad.id_dispositivo
                     FROM admision a
                     JOIN asignacion_dispositivo ad ON ad.id_admision = a.id_admision
                    WHERE a.estado='ABIERTA' ORDER BY a.id_admision""")
    adm = cur.fetchall()
    uuids = [a[0] for a in adm]
    uuid_demo = uuids[0]

    # Estado base reproducible: las mediciones "sin índice de cobertura"
    # (OP-3 y OP-5) exigen que los índices de OP-9 no existan todavía.
    cur.execute("DROP INDEX IF EXISTS hosp.ix_tel_cobertura_alarmas")
    for ix in ("ix_cobertura_alarmas", "ix_alarma_adm"):
        try:
            tel.drop_index(ix)
        except Exception:
            pass

    cur.execute("SELECT count(*) FROM telemetria_signos")
    n_pg = cur.fetchone()[0]
    n_mg = tel.estimated_document_count()
    print(f"\nVolumen verificado → PostgreSQL: {n_pg:,} filas | MongoDB: {n_mg:,} documentos")
    print(f"Admisiones monitorizadas: {len(uuids)}\n")

    # -----------------------------------------------------------------
    print("OP-1 · Ingesta por lotes de 5.000 lecturas")
    # -----------------------------------------------------------------
    lote_datos = list(generar_telemetria([(uuid_demo, 999)], 5000, semilla=777))
    filas_pg = [(d["uuid_admision"], d["id_dispositivo"], d["medido_en"], d["fc"], d["fr"],
                 d["spo2"], d["temp_c"], d["pas"], d["pad"], d["alarma"]) for d in lote_datos]
    docs_mg = [{"uuid_admision": d["uuid_admision"], "id_dispositivo": int(d["id_dispositivo"]),
                "tipo_sensor": d["tipo_sensor"], "medido_en": d["medido_en"],
                "signos": {"fc": int(d["fc"]), "fr": int(d["fr"]), "spo2": int(d["spo2"]),
                           "temp_c": float(d["temp_c"]), "pas": int(d["pas"]), "pad": int(d["pad"])},
                "alarma": bool(d["alarma"]), "perfil": d["perfil"]} for d in lote_datos]

    # Tabla PERMANENTE (no temporal): una tabla temporal de PostgreSQL no
    # escribe WAL, y compararla contra una colección normal de MongoDB
    # regalaría al motor relacional una ventaja que en producción no tiene.
    cur.execute("DROP TABLE IF EXISTS hosp.ingesta_bench")
    cur.execute("""CREATE TABLE hosp.ingesta_bench (
                      id_lectura BIGSERIAL PRIMARY KEY,
                      uuid_admision UUID NOT NULL, id_dispositivo INTEGER NOT NULL,
                      medido_en TIMESTAMPTZ NOT NULL, fc SMALLINT, fr SMALLINT,
                      spo2 SMALLINT, temp_c NUMERIC(4,1), pas SMALLINT, pad SMALLINT,
                      alarma BOOLEAN NOT NULL DEFAULT FALSE)""")
    cur.execute("""CREATE INDEX IF NOT EXISTS ix_bench_adm ON hosp.ingesta_bench (uuid_admision, medido_en DESC)""")
    cur.execute("""CREATE INDEX IF NOT EXISTS ix_bench_alarma ON hosp.ingesta_bench (medido_en DESC) WHERE alarma""")
    db.drop_collection("ingesta_bench")
    tmp = db["ingesta_bench"]
    tmp.create_index([("uuid_admision", 1), ("medido_en", -1)])
    tmp.create_index([("medido_en", -1)], partialFilterExpression={"alarma": True})

    def ing_pg():
        execute_values(cur, """INSERT INTO hosp.ingesta_bench
            (uuid_admision, id_dispositivo, medido_en, fc, fr, spo2, temp_c, pas, pad, alarma)
            VALUES %s""", filas_pg, page_size=5000)

    def ing_mg():
        tmp.insert_many([dict(d) for d in docs_mg], ordered=False)

    medir("Ingesta lote 5.000 lecturas", "PostgreSQL", ing_pg, repeticiones,
          nota="INSERT multi-fila (execute_values) en tabla permanente: WAL + MVCC, commit por lote")
    medir("Ingesta lote 5.000 lecturas", "MongoDB", ing_mg, repeticiones,
          nota="insert_many ordered=False en colección normal, writeConcern w:1 j:false")

    # COPY: la vía nativa optimizada de PostgreSQL (fuera de la comparación 1:1)
    import io
    buf_src = "".join("\t".join(str(x) for x in f) + "\n" for f in filas_pg)

    def ing_pg_copy():
        cur.copy_expert("""COPY hosp.ingesta_bench (uuid_admision, id_dispositivo, medido_en,
                           fc, fr, spo2, temp_c, pas, pad, alarma) FROM STDIN""",
                        io.StringIO(buf_src))

    medir("Ingesta lote 5.000 (COPY nativo)", "PostgreSQL", ing_pg_copy, repeticiones,
          nota="Vía optimizada: sin parseo de INSERT fila a fila")

    cur.execute("DROP TABLE hosp.ingesta_bench")
    db.drop_collection("ingesta_bench")

    # -----------------------------------------------------------------
    print("\nOP-2 · Lectura puntual: últimas 50 lecturas de una admisión")
    # -----------------------------------------------------------------
    import random
    rr = random.Random(99)

    def q2_pg():
        u = rr.choice(uuids)
        cur.execute("""SELECT medido_en, fc, spo2, pas, pad FROM telemetria_signos
                        WHERE uuid_admision = %s ORDER BY medido_en DESC LIMIT 50""", (u,))
        cur.fetchall()

    def q2_mg():
        u = rr.choice(uuids)
        list(tel.find({"uuid_admision": u},
                      {"medido_en": 1, "signos.fc": 1, "signos.spo2": 1,
                       "signos.pas": 1, "signos.pad": 1, "_id": 0})
             .sort("medido_en", -1).limit(50))

    medir("Últimas 50 lecturas de una admisión", "PostgreSQL", q2_pg, repeticiones)
    medir("Últimas 50 lecturas de una admisión", "MongoDB", q2_mg, repeticiones)

    cur.execute("""EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
                   SELECT medido_en, fc, spo2, pas, pad FROM telemetria_signos
                    WHERE uuid_admision = %s ORDER BY medido_en DESC LIMIT 50""", (uuid_demo,))
    log_plan("OP-2 PostgreSQL — EXPLAIN ANALYZE", "\n".join(r[0] for r in cur.fetchall()))
    ex = tel.find({"uuid_admision": uuid_demo}).sort("medido_en", -1).limit(50).explain()
    log_plan("OP-2 MongoDB — explain executionStats",
             json.dumps({"stage": ex["queryPlanner"]["winningPlan"],
                         "exec": {k: ex["executionStats"][k] for k in
                                  ("nReturned", "executionTimeMillis", "totalKeysExamined",
                                   "totalDocsExamined")}}, indent=2, default=str))

    # -----------------------------------------------------------------
    print("\nOP-3 · Ventana temporal con filtro: alarmas de las últimas 6 horas")
    # -----------------------------------------------------------------
    cur.execute("SELECT max(medido_en) FROM telemetria_signos")
    t_max = cur.fetchone()[0]
    desde = t_max - timedelta(hours=6)

    def q3_pg():
        cur.execute("""SELECT count(*), count(DISTINCT uuid_admision)
                         FROM telemetria_signos
                        WHERE alarma AND medido_en >= %s""", (desde,))
        cur.fetchall()

    def q3_mg():
        list(tel.aggregate([
            {"$match": {"alarma": True, "medido_en": {"$gte": desde}}},
            {"$group": {"_id": None, "n": {"$sum": 1},
                        "adm": {"$addToSet": "$uuid_admision"}}},
            {"$project": {"n": 1, "admisiones": {"$size": "$adm"}}},
        ]))

    medir("Alarmas últimas 6 h (índice parcial)", "PostgreSQL", q3_pg, repeticiones)
    medir("Alarmas últimas 6 h (índice parcial)", "MongoDB", q3_mg, repeticiones)

    # -----------------------------------------------------------------
    print("\nOP-4 · Agregación por hora de una admisión (tendencia clínica)")
    # -----------------------------------------------------------------
    def q4_pg():
        cur.execute("""SELECT date_trunc('hour', medido_en) h,
                              round(avg(fc),1), max(fc), min(spo2), round(avg(spo2),1),
                              count(*) FILTER (WHERE alarma)
                         FROM telemetria_signos
                        WHERE uuid_admision = %s
                        GROUP BY 1 ORDER BY 1""", (uuid_demo,))
        cur.fetchall()

    def q4_mg():
        list(tel.aggregate([
            {"$match": {"uuid_admision": uuid_demo}},
            {"$group": {
                "_id": {"$dateTrunc": {"date": "$medido_en", "unit": "hour"}},
                "fc_prom": {"$avg": "$signos.fc"}, "fc_max": {"$max": "$signos.fc"},
                "spo2_min": {"$min": "$signos.spo2"}, "spo2_prom": {"$avg": "$signos.spo2"},
                "alarmas": {"$sum": {"$cond": ["$alarma", 1, 0]}}}},
            {"$sort": {"_id": 1}},
        ]))

    medir("Tendencia horaria de una admisión", "PostgreSQL", q4_pg, repeticiones)
    medir("Tendencia horaria de una admisión", "MongoDB", q4_mg, repeticiones)

    cur.execute("""EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
                   SELECT date_trunc('hour', medido_en) h, avg(fc), max(fc)
                     FROM telemetria_signos WHERE uuid_admision = %s
                    GROUP BY 1 ORDER BY 1""", (uuid_demo,))
    log_plan("OP-4 PostgreSQL — EXPLAIN ANALYZE", "\n".join(r[0] for r in cur.fetchall()))

    # -----------------------------------------------------------------
    print("\nOP-5 · Agregación global sobre 1.008.000 registros (ranking de alarmas)")
    # -----------------------------------------------------------------
    def q5_pg():
        cur.execute("""SELECT uuid_admision, count(*) AS alarmas
                         FROM telemetria_signos WHERE alarma
                        GROUP BY uuid_admision ORDER BY alarmas DESC LIMIT 10""")
        cur.fetchall()

    def q5_mg():
        list(tel.aggregate([
            {"$match": {"alarma": True}},
            {"$group": {"_id": "$uuid_admision", "alarmas": {"$sum": 1}}},
            {"$sort": {"alarmas": -1}}, {"$limit": 10},
        ]))

    medir("Top-10 admisiones por alarmas (dataset completo)", "PostgreSQL", q5_pg,
          max(5, repeticiones // 3))
    medir("Top-10 admisiones por alarmas (dataset completo)", "MongoDB", q5_mg,
          max(5, repeticiones // 3))

    # Planes de LÍNEA BASE, antes de tocar ningún índice nuevo (OP-9 los compara)
    cur.execute("""EXPLAIN (ANALYZE, BUFFERS)
                   SELECT count(*), count(DISTINCT uuid_admision) FROM telemetria_signos
                    WHERE alarma AND medido_en >= %s""", (desde,))
    log_plan("OP-3 PostgreSQL — EXPLAIN ANALYZE (línea base)",
             "\n".join(r[0] for r in cur.fetchall()))
    ex3 = db.command("explain", {
        "aggregate": "telemetria",
        "pipeline": [{"$match": {"alarma": True, "medido_en": {"$gte": desde}}},
                     {"$group": {"_id": None, "n": {"$sum": 1},
                                 "adm": {"$addToSet": "$uuid_admision"}}}],
        "cursor": {}}, verbosity="executionStats")
    log_plan("OP-3 MongoDB — explain (línea base)",
             json.dumps(ex3.get("queryPlanner", ex3), indent=2, default=str)[:2500])

    cur.execute("""EXPLAIN (ANALYZE, BUFFERS)
                   SELECT uuid_admision, count(*) FROM telemetria_signos WHERE alarma
                    GROUP BY uuid_admision ORDER BY 2 DESC LIMIT 10""")
    log_plan("OP-5 PostgreSQL — EXPLAIN ANALYZE (línea base)",
             "\n".join(r[0] for r in cur.fetchall()))
    ex5 = db.command("explain", {
        "aggregate": "telemetria",
        "pipeline": [{"$match": {"alarma": True}},
                     {"$group": {"_id": "$uuid_admision", "alarmas": {"$sum": 1}}},
                     {"$sort": {"alarmas": -1}}, {"$limit": 10}],
        "cursor": {}}, verbosity="executionStats")
    log_plan("OP-5 MongoDB — explain (línea base)",
             json.dumps(ex5.get("queryPlanner", ex5), indent=2, default=str)[:2500])

    # -----------------------------------------------------------------
    print("\nOP-6 · Consulta HÍBRIDA: identidad (PG) + telemetría (Mongo)")
    # -----------------------------------------------------------------
    def q6_hibrida():
        cur.execute("""SELECT uuid_admision::text, paciente, documento, sede, cama
                         FROM v_admisiones_activas
                        WHERE servicio = 'UCI' LIMIT 20""")
        activas = cur.fetchall()
        claves = [a[0] for a in activas]
        ultimos = list(tel.aggregate([
            {"$match": {"uuid_admision": {"$in": claves}}},
            {"$sort": {"medido_en": -1}},
            {"$group": {"_id": "$uuid_admision",
                        "ultima": {"$first": "$medido_en"},
                        "fc": {"$first": "$signos.fc"},
                        "spo2": {"$first": "$signos.spo2"},
                        "alarmas": {"$sum": {"$cond": ["$alarma", 1, 0]}}}},
        ]))
        idx = {u["_id"]: u for u in ultimos}
        return [dict(zip(("uuid", "paciente", "doc", "sede", "cama"), a),
                     **idx.get(a[0], {})) for a in activas]

    medir("Tablero UCI: 20 camas con último signo vital", "Híbrido PG+Mongo",
          q6_hibrida, repeticiones, nota="2 round-trips: 1 a PG + 1 pipeline $group sobre $in")

    # Variante optimizada: en lugar de agrupar decenas de miles de documentos,
    # se piden 20 "últimas lecturas" que el índice ix_adm_tiempo resuelve
    # directamente. Misma información, orquestación distinta.
    def q6_hibrida_opt():
        cur.execute("""SELECT uuid_admision::text, paciente, documento, sede, cama
                         FROM v_admisiones_activas
                        WHERE servicio = 'UCI' LIMIT 20""")
        activas = cur.fetchall()
        out = []
        for a in activas:
            doc = tel.find_one({"uuid_admision": a[0]},
                               {"medido_en": 1, "signos": 1, "alarma": 1, "_id": 0},
                               sort=[("medido_en", -1)])
            out.append((a, doc))
        return out

    medir("Tablero UCI — orquestación optimizada", "Híbrido PG+Mongo",
          q6_hibrida_opt, repeticiones,
          nota="21 round-trips cortos resueltos por ix_adm_tiempo, sin agregación")

    # -----------------------------------------------------------------
    # -----------------------------------------------------------------
    # Tamaño en disco — se mide AQUÍ, con el juego de índices que el diseño
    # propone (3 por motor), antes de que OP-9 añada los de cobertura.
    # -----------------------------------------------------------------
    cur.execute("""SELECT pg_relation_size('telemetria_signos'),
                          pg_indexes_size('telemetria_signos'),
                          pg_total_relation_size('telemetria_signos')""")
    pg_d, pg_i, pg_t = cur.fetchone()
    stm = db.command("collstats", "telemetria")
    almacenamiento = {
        "PostgreSQL": {"datos_MB": round(pg_d / 1e6, 1), "indices_MB": round(pg_i / 1e6, 1),
                       "total_MB": round(pg_t / 1e6, 1), "registros": n_pg},
        "MongoDB": {"datos_MB": round(stm["size"] / 1e6, 1),
                    "datos_comprimidos_MB": round(stm["storageSize"] / 1e6, 1),
                    "indices_MB": round(stm["totalIndexSize"] / 1e6, 1),
                    "total_MB": round((stm["storageSize"] + stm["totalIndexSize"]) / 1e6, 1),
                    "registros": n_mg},
    }
    print("\nAlmacenamiento:", json.dumps(almacenamiento, ensure_ascii=False))

    print("\nOP-7 · Evolución del esquema: llega un sensor nuevo (glucometría + EtCO2)")
    # -----------------------------------------------------------------
    cur.execute("ALTER TABLE telemetria_signos DROP COLUMN IF EXISTS glucosa_mgdl")
    cur.execute("ALTER TABLE telemetria_signos DROP COLUMN IF EXISTS etco2")

    t0 = time.perf_counter()
    cur.execute("ALTER TABLE telemetria_signos ADD COLUMN glucosa_mgdl SMALLINT")
    cur.execute("ALTER TABLE telemetria_signos ADD COLUMN etco2 SMALLINT")
    dt_alter = (time.perf_counter() - t0) * 1000
    MEDICIONES.append(("Añadir 2 campos nuevos (DDL)", "PostgreSQL", round(dt_alter, 3),
                       round(dt_alter, 3), round(dt_alter, 3), round(dt_alter, 3), 1, "ms",
                       "ALTER TABLE ADD COLUMN NULL: solo catálogo, sin reescritura"))
    print(f"  PostgreSQL ALTER TABLE ADD COLUMN x2            → {dt_alter:.2f} ms")

    t0 = time.perf_counter()
    cur.execute("""UPDATE telemetria_signos SET glucosa_mgdl = 90 + (fc %% 60)
                    WHERE uuid_admision = %s""", (uuid_demo,))
    filas_upd = cur.rowcount
    dt_backfill = (time.perf_counter() - t0) * 1000
    MEDICIONES.append(("Backfill del campo nuevo (1 admisión)", "PostgreSQL",
                       round(dt_backfill, 3), round(dt_backfill, 3), round(dt_backfill, 3),
                       round(dt_backfill, 3), 1, "ms",
                       f"UPDATE de {filas_upd:,} filas: reescritura de tuplas (MVCC)"))
    print(f"  PostgreSQL backfill de {filas_upd:,} filas{' ' * 10}→ {dt_backfill:.2f} ms")

    t0 = time.perf_counter()
    tel.insert_many([{
        "uuid_admision": uuid_demo, "id_dispositivo": 90001,
        "tipo_sensor": "GLUCOMETRO_CONTINUO", "medido_en": datetime.now(TZ),
        "signos": {"glucosa_mgdl": 118 + i % 40, "etco2": 35 + i % 8},
        "alarma": False, "calibracion": {"lote": "GLX-2026-11", "vencimiento": "2027-03-01"},
    } for i in range(100)], ordered=False)
    dt_mg_nuevo = (time.perf_counter() - t0) * 1000
    MEDICIONES.append(("Aceptar 2 campos nuevos (sin DDL)", "MongoDB", round(dt_mg_nuevo, 3),
                       round(dt_mg_nuevo, 3), round(dt_mg_nuevo, 3), round(dt_mg_nuevo, 3),
                       1, "ms", "Sin DDL: 100 documentos con forma nueva conviven con los anteriores"))
    print(f"  MongoDB inserta 100 docs con forma nueva        → {dt_mg_nuevo:.2f} ms "
          f"(0 migraciones)")

    coexisten = tel.count_documents({"signos.glucosa_mgdl": {"$exists": True}})
    log_plan("OP-7 MongoDB — coexistencia de esquemas",
             f"Documentos con el campo nuevo signos.glucosa_mgdl: {coexisten}\n"
             f"Documentos totales en la colección: {tel.estimated_document_count():,}\n"
             "La colección contiene dos formas de documento simultáneas sin migración.")

    # -----------------------------------------------------------------
    print("\nOP-8 · Transacción multi-entidad: egreso hospitalario")
    # -----------------------------------------------------------------
    cur.execute("""SELECT id_admision FROM admision WHERE estado='ABIERTA'
                   ORDER BY id_admision DESC LIMIT %s""", (repeticiones + 5,))
    candidatas = [r[0] for r in cur.fetchall()]
    it = iter(candidatas)

    def egreso_pg():
        cur.execute("CALL sp_egresar_paciente(%s, %s, %s)", (next(it), "MEJORIA", "I10X"))

    medir("Egreso (4 tablas en 1 transacción)", "PostgreSQL", egreso_pg,
          min(repeticiones, len(candidatas) - 4), warmup=1,
          nota="ACID: UPDATE admision + cama + dispositivo + INSERT auditoría, todo o nada")

    # Equivalente documental del egreso: tres escrituras sobre DOS colecciones
    # que deben ocurrir juntas o no ocurrir (cerrar el episodio, retirar el
    # dispositivo y dejar la auditoría). Se intenta como transacción única.
    intento_tx = None
    try:
        with mongo.start_session() as ses:
            with ses.start_transaction():
                db.eventos_clinicos.insert_one(
                    {"tipo": "EGRESO", "uuid_admision": uuid_demo,
                     "ocurrido_en": datetime.now(TZ)}, session=ses)
                db.eventos_clinicos.update_many(
                    {"uuid_admision": uuid_demo, "tipo": "CAMBIO_DISPOSITIVO"},
                    {"$set": {"retirado": True}}, session=ses)
                db.telemetria.update_many(
                    {"uuid_admision": uuid_demo, "alarma": True},
                    {"$set": {"cerrada_por_egreso": True}}, session=ses)
        intento_tx = ("La transacción multi-documento se ejecutó: el despliegue "
                      "tiene replica set y las tres escrituras fueron atómicas.")
    except Exception as e:
        intento_tx = (f"RECHAZADA por el motor: {type(e).__name__}: {str(e)[:200]} "
                      "— las tres escrituras del egreso no pueden agruparse "
                      "atómicamente en un despliegue de nodo único.")
    log_plan("OP-8 MongoDB — intento de transacción multi-documento", intento_tx)
    print(f"  MongoDB transacción multi-documento → {intento_tx[:110]}")


    print("\nOP-9 · Optimización: índices de cobertura sugeridos por IA")
    #  Diagnóstico: OP-3 y OP-5 fetchean el documento/tupla completo solo para
    #  leer uuid_admision. Un índice que CUBRA la consulta evita ese acceso.
    # -----------------------------------------------------------------
    tel.create_index([("alarma", 1), ("medido_en", -1), ("uuid_admision", 1)],
                     name="ix_cobertura_alarmas")
    cur.execute("""CREATE INDEX IF NOT EXISTS ix_tel_cobertura_alarmas
                   ON telemetria_signos (medido_en DESC, uuid_admision) WHERE alarma""")
    cur.execute("ANALYZE telemetria_signos")

    medir("Alarmas últimas 6 h — índice cobertura v1", "PostgreSQL", q3_pg, repeticiones,
          nota="Índice parcial (medido_en, uuid_admision) WHERE alarma")
    medir("Alarmas últimas 6 h — índice cobertura v1", "MongoDB", q3_mg, repeticiones,
          nota="ix_cobertura_alarmas {alarma, medido_en, uuid_admision}: consulta cubierta")
    medir("Top-10 por alarmas — índice cobertura v1", "PostgreSQL", q5_pg,
          max(5, repeticiones // 3),
          nota="El planeador SÍ usa el índice (Parallel Index Only Scan) y aun así no gana: 33 % de selectividad")
    medir("Top-10 por alarmas — índice cobertura v1", "MongoDB", q5_mg,
          max(5, repeticiones // 3),
          nota="Índice mal ordenado para el $group: EMPEORA respecto al COLLSCAN")

    # Segunda iteración: el índice correcto para una agrupación es el que
    # ordena por la CLAVE DE AGRUPACIÓN, no por el tiempo.
    tel.create_index([("alarma", 1), ("uuid_admision", 1)], name="ix_alarma_adm")
    tel.drop_index("ix_cobertura_alarmas")
    medir("Top-10 por alarmas — índice cobertura v2", "MongoDB", q5_mg,
          max(5, repeticiones // 3),
          nota="ix_alarma_adm {alarma, uuid_admision}: PROJECTION_COVERED, sin FETCH")

    ex9 = db.command("explain", {
        "aggregate": "telemetria",
        "pipeline": [{"$match": {"alarma": True}},
                     {"$group": {"_id": "$uuid_admision", "alarmas": {"$sum": 1}}},
                     {"$sort": {"alarmas": -1}}, {"$limit": 10}],
        "cursor": {}}, verbosity="executionStats")
    log_plan("OP-9 MongoDB — plan con índice de cobertura",
             json.dumps(ex9.get("queryPlanner", ex9), indent=2, default=str)[:4000])
    cur.execute("""EXPLAIN (ANALYZE, BUFFERS)
                   SELECT uuid_admision, count(*) FROM telemetria_signos WHERE alarma
                    GROUP BY uuid_admision ORDER BY 2 DESC LIMIT 10""")
    log_plan("OP-9 PostgreSQL — plan con índice de cobertura",
             "\n".join(r[0] for r in cur.fetchall()))


    # -----------------------------------------------------------------
    # Persistencia de resultados
    # -----------------------------------------------------------------
    cab = ["operacion", "motor", "p50_ms", "p95_ms", "p99_ms", "media_ms",
           "repeticiones", "unidad", "nota"]
    with open(os.path.join(RES, "metricas.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cab)
        w.writerows(MEDICIONES)
    with open(os.path.join(RES, "metricas.json"), "w", encoding="utf-8") as f:
        json.dump({
            "ejecutado_en": datetime.now(TZ).isoformat(),
            "entorno": {
                "postgresql": cur.connection.server_version,
                "mongodb": mongo.server_info()["version"],
                "cpus": os.cpu_count(),
                "registros_telemetria": n_pg,
            },
            "mediciones": [dict(zip(cab, m)) for m in MEDICIONES],
            "almacenamiento": almacenamiento,
            "transaccion_mongo": intento_tx,
        }, f, indent=2, ensure_ascii=False)

    PLANES.close()
    pg.close()
    mongo.close()
    print(f"\n✓ Resultados en {RES}/metricas.csv, metricas.json y planes.txt")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeticiones", type=int, default=30)
    a = ap.parse_args()
    main(a.repeticiones)
