#!/usr/bin/env python3
"""
03_espejo_postgres.py — Carga el MISMO conjunto de telemetría en la capa ACID.

Esta tabla espejo no pertenece a la arquitectura propuesta: existe únicamente
para responder la pregunta del reto, "¿cómo se comporta la MISMA operación en
cada motor?". Para que la medición sea honesta se igualan las condiciones:

  * mismos datos (generador determinista, semilla fija),
  * mismo tamaño de lote que insert_many en MongoDB,
  * mismo juego de índices activos DURANTE la carga
    (MongoDB: _id + ix_adm_tiempo + ix_alarmas
     PostgreSQL: PK + ix_tel_adm_tiempo + ix_tel_alarmas).

Se mide además COPY, la vía optimizada de PostgreSQL, para no comparar
al motor relacional con una mano atada a la espalda.

Uso:  python3 scripts/03_espejo_postgres.py [--lecturas-por-admision 1800] [--modo lotes|copy]
"""
import argparse
import io
import sys
import time

import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from comun import PG_DSN, generar_telemetria, lotes  # noqa: E402

DDL_INDICES = """
CREATE INDEX IF NOT EXISTS ix_tel_adm_tiempo
    ON hosp.telemetria_signos (uuid_admision, medido_en DESC);
CREATE INDEX IF NOT EXISTS ix_tel_alarmas
    ON hosp.telemetria_signos (medido_en DESC) WHERE alarma;
"""


def admisiones_monitorizadas(cur):
    cur.execute("""
        SELECT a.uuid_admision::text, ad.id_dispositivo
          FROM hosp.admision a
          JOIN hosp.asignacion_dispositivo ad ON ad.id_admision = a.id_admision
         WHERE a.estado = 'ABIERTA'
         ORDER BY a.id_admision""")
    return cur.fetchall()


def carga_por_lotes(cur, filas_iter, lote):
    sql = """INSERT INTO hosp.telemetria_signos
             (uuid_admision, id_dispositivo, medido_en, fc, fr, spo2, temp_c, pas, pad, alarma)
             VALUES %s"""
    n = 0
    t0 = time.perf_counter()
    for bloque in lotes(filas_iter, lote):
        execute_values(cur, sql, bloque, page_size=lote)
        n += len(bloque)
        if n % (lote * 20) == 0:
            print(f"   {n:,} ({n / (time.perf_counter() - t0):,.0f} filas/s)")
    return n, time.perf_counter() - t0


def carga_copy(cur, filas_iter):
    buf = io.StringIO()
    n = 0
    for f in filas_iter:
        buf.write("\t".join(str(x) for x in f) + "\n")
        n += 1
    buf.seek(0)
    t0 = time.perf_counter()
    cur.copy_expert("""COPY hosp.telemetria_signos
                       (uuid_admision, id_dispositivo, medido_en, fc, fr, spo2,
                        temp_c, pas, pad, alarma) FROM STDIN""", buf)
    return n, time.perf_counter() - t0


def main(lecturas_por_admision, lote, modo):
    cn = psycopg2.connect(PG_DSN)
    cn.autocommit = False
    cur = cn.cursor()

    print("→ Preparando tabla espejo e índices equivalentes a los de MongoDB…")
    cur.execute("TRUNCATE hosp.telemetria_signos RESTART IDENTITY")
    cur.execute(DDL_INDICES)
    cn.commit()

    adm = admisiones_monitorizadas(cur)
    total = len(adm) * lecturas_por_admision
    print(f"→ {len(adm)} admisiones monitorizadas | {total:,} lecturas a insertar (modo={modo})")

    filas = (
        (l["uuid_admision"], l["id_dispositivo"], l["medido_en"], l["fc"], l["fr"],
         l["spo2"], l["temp_c"], l["pas"], l["pad"], l["alarma"])
        for l in generar_telemetria(adm, lecturas_por_admision)
    )

    if modo == "copy":
        n, dt = carga_copy(cur, filas)
    else:
        n, dt = carga_por_lotes(cur, filas, lote)
    cn.commit()
    print(f"✓ telemetria_signos: {n:,} filas en {dt:.2f} s → {n / dt:,.0f} filas/s")

    cur.execute("ANALYZE hosp.telemetria_signos")
    cn.commit()
    cur.execute("""SELECT pg_size_pretty(pg_relation_size('hosp.telemetria_signos')),
                          pg_size_pretty(pg_indexes_size('hosp.telemetria_signos')),
                          pg_size_pretty(pg_total_relation_size('hosp.telemetria_signos'))""")
    datos, idx, tot = cur.fetchone()
    print(f"Resumen telemetria_signos: datos {datos} | índices {idx} | total {tot}")
    cn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lecturas-por-admision", type=int, default=1800)
    ap.add_argument("--lote", type=int, default=5000)
    ap.add_argument("--modo", choices=["lotes", "copy"], default="lotes")
    a = ap.parse_args()
    main(a.lecturas_por_admision, a.lote, a.modo)
