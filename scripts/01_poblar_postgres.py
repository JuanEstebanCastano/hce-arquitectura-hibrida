#!/usr/bin/env python3
"""
01_poblar_postgres.py — Poblamiento de la capa ACID.

Crea pacientes, admisiones (abiertas y cerradas) y asignaciones de
dispositivos respetando TODAS las restricciones declaradas en el DDL:
  * documento único en la red,
  * una sola admisión abierta por paciente,
  * una sola admisión abierta por cama.

Uso:  python3 scripts/01_poblar_postgres.py [--pacientes 20000] [--admisiones 8000]
"""
import argparse
import random
import sys
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from comun import PG_DSN, SEMILLA, TZ  # noqa: E402

NOMBRES_M = ["Juan", "Carlos", "Andrés", "Santiago", "Jhon", "Luis", "Mauricio",
             "Esteban", "Felipe", "Óscar", "Jorge", "Diego", "Camilo", "Héctor"]
NOMBRES_F = ["María", "Luz", "Daniela", "Marcela", "Paula", "Catalina", "Laura",
             "Sandra", "Verónica", "Isabel", "Gloria", "Natalia", "Ángela", "Rosa"]
APELLIDOS = ["Restrepo", "Zapata", "Correa", "Castaño", "Gómez", "Arango", "Betancur",
             "Vélez", "Monsalve", "Ospina", "Jaramillo", "Echeverri", "Quintero",
             "Mesa", "Montoya", "Acevedo", "Cardona", "Henao", "Gallego", "Úsuga"]
MUNICIPIOS = ["Medellín", "Bello", "Itagüí", "Envigado", "Rionegro", "Yarumal",
              "Apartadó", "Caucasia", "Andes", "Turbo", "La Ceja", "Sabaneta"]


def main(n_pacientes, n_admisiones):
    rng = random.Random(SEMILLA)
    cn = psycopg2.connect(PG_DSN)
    cn.autocommit = False
    cur = cn.cursor()

    print(f"→ Limpiando tablas transaccionales…")
    cur.execute("SET search_path TO hosp, public")
    cur.execute("""TRUNCATE auditoria_egreso, asignacion_dispositivo,
                              admision_diagnostico, telemetria_signos, admision, paciente
                   RESTART IDENTITY CASCADE""")
    cur.execute("UPDATE cama SET estado = 'DISPONIBLE'")

    # ---------------- Pacientes ----------------
    print(f"→ Insertando {n_pacientes:,} pacientes…")
    cur.execute("SELECT id_eps FROM eps")
    eps_ids = [r[0] for r in cur.fetchall()]

    filas = []
    for i in range(n_pacientes):
        sexo = rng.choice(["M", "F"])
        nom = rng.choice(NOMBRES_M if sexo == "M" else NOMBRES_F)
        edad = rng.choices([rng.randint(0, 17), rng.randint(18, 59), rng.randint(60, 95)],
                           weights=[0.18, 0.47, 0.35])[0]
        nac = datetime(2026, 11, 22) - timedelta(days=edad * 365 + rng.randint(0, 364))
        tipo_doc = "TI" if edad < 18 else "CC"
        filas.append((
            tipo_doc, str(1000000000 + i * 7 + rng.randint(0, 6)),
            nom, rng.choice(NOMBRES_M + NOMBRES_F) if rng.random() < 0.4 else None,
            rng.choice(APELLIDOS), rng.choice(APELLIDOS),
            nac.date(), sexo, rng.choice(eps_ids),
            "3" + str(rng.randint(10000000, 29999999)),
            None, rng.choice(MUNICIPIOS),
        ))
    execute_values(cur, """
        INSERT INTO paciente (tipo_doc, num_doc, primer_nombre, segundo_nombre,
                              primer_apellido, segundo_apellido, fecha_nacimiento,
                              sexo, id_eps, telefono, email, municipio_res)
        VALUES %s""", filas, page_size=5000)
    cn.commit()

    # ---------------- Admisiones ----------------
    cur.execute("SELECT id_paciente FROM paciente ORDER BY id_paciente")
    pacientes = [r[0] for r in cur.fetchall()]
    cur.execute("""SELECT c.id_cama, p.id_sede FROM cama c
                     JOIN pabellon p ON p.id_pabellon = c.id_pabellon""")
    camas = cur.fetchall()
    cur.execute("SELECT id_profesional, id_sede FROM profesional")
    medicos_por_sede = {}
    for idp, ids in cur.fetchall():
        medicos_por_sede.setdefault(ids, []).append(idp)
    cur.execute("SELECT codigo FROM diagnostico_cie10")
    dxs = [r[0] for r in cur.fetchall()]

    n_abiertas = min(len(camas), int(n_admisiones * 0.07))
    print(f"→ Insertando {n_admisiones:,} admisiones "
          f"({n_abiertas} abiertas con cama ocupada, el resto cerradas)…")

    rng.shuffle(pacientes)
    pac_iter = iter(pacientes)
    camas_abiertas = rng.sample(camas, n_abiertas)

    abiertas, cerradas = [], []
    for id_cama, id_sede in camas_abiertas:
        idp = next(pac_iter)
        ingreso = datetime.now(TZ) - timedelta(hours=rng.randint(6, 240))
        abiertas.append((idp, id_sede, id_cama, rng.choice(medicos_por_sede[id_sede]),
                         ingreso, None, "ABIERTA", None, rng.choice(dxs)))

    for _ in range(n_admisiones - n_abiertas):
        idp = next(pac_iter)
        id_cama, id_sede = rng.choice(camas)
        ingreso = datetime.now(TZ) - timedelta(days=rng.randint(10, 540),
                                               hours=rng.randint(0, 23))
        egreso = ingreso + timedelta(hours=rng.randint(12, 480))
        cerradas.append((idp, id_sede, id_cama, rng.choice(medicos_por_sede[id_sede]),
                         ingreso, egreso, "CERRADA",
                         rng.choices(["MEJORIA", "REMISION", "VOLUNTARIO", "FALLECIMIENTO"],
                                     weights=[0.80, 0.11, 0.05, 0.04])[0],
                         rng.choice(dxs)))

    execute_values(cur, """
        INSERT INTO admision (id_paciente, id_sede, id_cama, id_medico_tratante,
                              fecha_ingreso, fecha_egreso, estado, tipo_egreso, cod_dx_ingreso)
        VALUES %s""", abiertas + cerradas, page_size=5000)

    cur.execute("""UPDATE cama SET estado = 'OCUPADA'
                    WHERE id_cama IN (SELECT id_cama FROM admision
                                       WHERE estado = 'ABIERTA' AND id_cama IS NOT NULL)""")

    # Diagnósticos adicionales (comorbilidades) para el 45 % de las admisiones
    cur.execute("SELECT id_admision, cod_dx_ingreso FROM admision")
    todas = cur.fetchall()
    dx_rows = []
    for id_adm, dx_ing in todas:
        dx_rows.append((id_adm, dx_ing, "PRINCIPAL"))
        if rng.random() < 0.45:
            otro = rng.choice(dxs)
            if otro != dx_ing:
                dx_rows.append((id_adm, otro, "RELACIONADO"))
    execute_values(cur, """INSERT INTO admision_diagnostico (id_admision, codigo_cie10, tipo)
                           VALUES %s ON CONFLICT DO NOTHING""", dx_rows, page_size=5000)
    cn.commit()

    # ---------------- Asignación de dispositivos a las admisiones abiertas ---
    cur.execute("""SELECT a.id_admision, d.id_dispositivo
                     FROM admision a
                     JOIN cama c ON c.id_cama = a.id_cama
                     JOIN dispositivo d ON d.serial_fabrica = 'DEV-' || lpad(c.id_cama::text, 6, '0')
                    WHERE a.estado = 'ABIERTA'""")
    pares = cur.fetchall()
    execute_values(cur, """INSERT INTO asignacion_dispositivo (id_admision, id_dispositivo)
                           VALUES %s""",
                   [(a, d) for a, d in pares], template="(%s, %s)", page_size=1000)
    cn.commit()

    # ---------------- Resumen ----------------
    cur.execute("""
        SELECT (SELECT count(*) FROM paciente),
               (SELECT count(*) FROM admision),
               (SELECT count(*) FROM admision WHERE estado='ABIERTA'),
               (SELECT count(*) FROM admision_diagnostico),
               (SELECT count(*) FROM asignacion_dispositivo)""")
    p, a, ab, dx, asg = cur.fetchone()
    print(f"✓ pacientes={p:,}  admisiones={a:,} (abiertas={ab})  "
          f"dx={dx:,}  dispositivos_asignados={asg}")
    cur.execute("ANALYZE")
    cn.commit()
    cn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pacientes", type=int, default=20000)
    ap.add_argument("--admisiones", type=int, default=8000)
    a = ap.parse_args()
    main(a.pacientes, a.admisiones)
