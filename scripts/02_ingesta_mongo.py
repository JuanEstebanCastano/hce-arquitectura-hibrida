#!/usr/bin/env python3
"""
02_ingesta_mongo.py — Ingesta masiva en la capa BASE (MongoDB).

Simula el flujo real de una red hospitalaria:
  * telemetria        → lecturas de monitores/wearables cada 30 s (volumen masivo)
  * eventos_clinicos  → bitácora semiestructurada de esquema variable
  * teleconsultas     → documentos anidados con transcripción y adjuntos

Aplica además los mismos validadores e índices de nosql/01_init_colecciones.js
(equivalente en pymongo, para entornos sin mongosh).

Uso:  python3 scripts/02_ingesta_mongo.py [--lecturas-por-admision 1800] [--lote 5000]
"""
import argparse
import random
import sys
import time
from datetime import datetime, timedelta

import psycopg2
from pymongo import MongoClient, ASCENDING, DESCENDING, TEXT

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from comun import (PG_DSN, MONGO_URI, MONGO_DB, SEMILLA, TZ,  # noqa: E402
                   generar_telemetria, lotes)

ESQUEMA_TELEMETRIA = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["uuid_admision", "medido_en"],
        "properties": {
            "uuid_admision": {"bsonType": "string"},
            "id_dispositivo": {"bsonType": "int"},
            "tipo_sensor": {"bsonType": "string"},
            "medido_en": {"bsonType": "date"},
            "alarma": {"bsonType": "bool"},
            "signos": {
                "bsonType": "object",
                "properties": {
                    "fc": {"bsonType": "int", "minimum": 20, "maximum": 250},
                    "fr": {"bsonType": "int", "minimum": 4, "maximum": 60},
                    "spo2": {"bsonType": "int", "minimum": 50, "maximum": 100},
                    "temp_c": {"bsonType": "double", "minimum": 30.0, "maximum": 45.0},
                    "pas": {"bsonType": "int", "minimum": 40, "maximum": 260},
                    "pad": {"bsonType": "int", "minimum": 20, "maximum": 160},
                },
            },
        },
    }
}


def init_colecciones(db):
    """Equivalente pymongo de nosql/01_init_colecciones.js"""
    for c in ("telemetria", "eventos_clinicos", "teleconsultas"):
        db.drop_collection(c)
    db.create_collection("telemetria", validator=ESQUEMA_TELEMETRIA,
                         validationLevel="moderate", validationAction="warn")
    db.create_collection("eventos_clinicos")
    db.create_collection("teleconsultas")

    db.telemetria.create_index([("uuid_admision", ASCENDING), ("medido_en", DESCENDING)],
                               name="ix_adm_tiempo")
    db.telemetria.create_index([("medido_en", DESCENDING)], name="ix_alarmas",
                               partialFilterExpression={"alarma": True})
    db.eventos_clinicos.create_index([("uuid_admision", ASCENDING), ("ocurrido_en", DESCENDING)],
                                     name="ix_adm_evento")
    db.eventos_clinicos.create_index([("tipo", ASCENDING), ("ocurrido_en", DESCENDING)],
                                     name="ix_tipo_evento")
    db.eventos_clinicos.create_index([("severidad", ASCENDING)], name="ix_criticos",
                                     partialFilterExpression={"severidad": "CRITICA"})
    db.teleconsultas.create_index([("uuid_paciente", ASCENDING), ("iniciada_en", DESCENDING)],
                                  name="ix_pac_teleconsulta")
    db.teleconsultas.create_index([("transcripcion.texto", TEXT)], name="ix_texto_transcripcion")


def contexto_desde_postgres():
    """La capa BASE lee de la capa ACID las llaves puente. Nunca al revés."""
    cn = psycopg2.connect(PG_DSN)
    cur = cn.cursor()
    cur.execute("SET search_path TO hosp, public")
    cur.execute("""
        SELECT a.uuid_admision::text, p.uuid_paciente::text, ad.id_dispositivo,
               s.nombre, pb.servicio
          FROM admision a
          JOIN paciente p  ON p.id_paciente = a.id_paciente
          JOIN sede s      ON s.id_sede = a.id_sede
          JOIN asignacion_dispositivo ad ON ad.id_admision = a.id_admision
          LEFT JOIN cama c ON c.id_cama = a.id_cama
          LEFT JOIN pabellon pb ON pb.id_pabellon = c.id_pabellon
         WHERE a.estado = 'ABIERTA'
         ORDER BY a.id_admision""")
    filas = cur.fetchall()
    cur.execute("SELECT uuid_paciente::text FROM paciente ORDER BY id_paciente LIMIT 5000")
    pacientes = [r[0] for r in cur.fetchall()]
    cn.close()
    return filas, pacientes


def doc_telemetria(lec):
    """Adapta la lectura neutra al modelo documental (signos anidados)."""
    return {
        "uuid_admision": lec["uuid_admision"],
        "id_dispositivo": int(lec["id_dispositivo"]),
        "tipo_sensor": lec["tipo_sensor"],
        "medido_en": lec["medido_en"],
        "signos": {
            "fc": int(lec["fc"]), "fr": int(lec["fr"]), "spo2": int(lec["spo2"]),
            "temp_c": float(lec["temp_c"]), "pas": int(lec["pas"]), "pad": int(lec["pad"]),
        },
        "alarma": bool(lec["alarma"]),
        "perfil": lec["perfil"],
    }


# ---------------------------------------------------------------------
# Eventos clínicos: cinco formas de documento distintas en una colección.
# ---------------------------------------------------------------------
def generar_eventos(contexto, n, rng):
    medicamentos = [("Cefepime", "2 g", "IV"), ("Vancomicina", "1 g", "IV"),
                    ("Furosemida", "40 mg", "IV"), ("Enoxaparina", "40 mg", "SC"),
                    ("Acetaminofén", "1 g", "VO"), ("Noradrenalina", "0.1 mcg/kg/min", "IV")]
    notas = ["Paciente refiere disnea leve en reposo.",
             "Se realiza curación de herida quirúrgica, sin signos de infección.",
             "Tolera dieta blanda, diuresis conservada.",
             "Se explica a familiar el plan de manejo; firma consentimiento.",
             "Somnoliento, responde a estímulo verbal."]
    for _ in range(n):
        uuid_adm, uuid_pac, id_disp, sede, servicio = rng.choice(contexto)
        t = datetime.now(TZ) - timedelta(minutes=rng.randint(0, 60 * 72))
        tipo = rng.choices(
            ["ALARMA_MONITOR", "ADMIN_MEDICAMENTO", "NOTA_ENFERMERIA",
             "CAMBIO_DISPOSITIVO", "ERROR_SINCRONIZACION"],
            weights=[0.34, 0.26, 0.24, 0.08, 0.08])[0]

        base = {"uuid_admision": uuid_adm, "uuid_paciente": uuid_pac,
                "tipo": tipo, "ocurrido_en": t, "sede": sede, "servicio": servicio}

        if tipo == "ALARMA_MONITOR":
            param = rng.choice(["SPO2_BAJA", "TAQUICARDIA", "BRADICARDIA",
                                "HIPOTENSION", "FIEBRE"])
            base.update(parametro=param,
                        valor=round(rng.uniform(30, 190), 1),
                        umbral=round(rng.uniform(40, 180), 1),
                        severidad=rng.choices(["BAJA", "MEDIA", "CRITICA"],
                                              weights=[0.5, 0.35, 0.15])[0],
                        atendida_en_seg=rng.randint(5, 600),
                        id_dispositivo=id_disp)
        elif tipo == "ADMIN_MEDICAMENTO":
            m, dosis, via = rng.choice(medicamentos)
            base.update(medicamento=m, dosis=dosis, via=via,
                        administrado_por="ENF-" + str(rng.randint(100, 999)),
                        verificacion_doble=rng.random() < 0.7)
        elif tipo == "NOTA_ENFERMERIA":
            base.update(texto=rng.choice(notas),
                        turno=rng.choice(["MAÑANA", "TARDE", "NOCHE"]),
                        etiquetas=rng.sample(["dolor", "movilidad", "dieta",
                                              "familia", "piel", "sueño"],
                                             k=rng.randint(1, 3)))
        elif tipo == "CAMBIO_DISPOSITIVO":
            base.update(id_dispositivo_anterior=id_disp,
                        id_dispositivo_nuevo=id_disp + rng.randint(1, 50),
                        motivo=rng.choice(["BATERIA", "FALLA_SENSOR", "DESINFECCION"]),
                        firmware={"anterior": "3.2.1", "nuevo": "3.4.0"})
        else:  # ERROR_SINCRONIZACION — campo libre, estructura impredecible
            base.update(severidad=rng.choice(["BAJA", "MEDIA", "CRITICA"]),
                        codigo_error="E" + str(rng.randint(100, 999)),
                        reintentos=rng.randint(1, 8),
                        payload_crudo={"bytes": rng.randint(120, 4096),
                                       "protocolo": rng.choice(["HL7v2", "FHIR", "MQTT"]),
                                       "checksum_ok": rng.random() < 0.6})
        yield base


def generar_teleconsultas(pacientes, n, rng):
    motivos = ["Control post-egreso", "Ajuste de anticoagulación",
               "Resultado de laboratorio", "Seguimiento de herida quirúrgica",
               "Control de hipertensión"]
    frases = ["Refiere mejoría del dolor desde el egreso.",
              "Se ajusta dosis y se solicita control en 15 días.",
              "Signos de alarma explicados al paciente y su cuidadora.",
              "Reporta adherencia parcial al tratamiento.",
              "Se solicita hemograma y creatinina de control."]
    for _ in range(n):
        pac = rng.choice(pacientes)
        ini = datetime.now(TZ) - timedelta(days=rng.randint(0, 120),
                                           minutes=rng.randint(0, 1440))
        dur = rng.randint(6, 35)
        yield {
            "uuid_paciente": pac,
            "motivo": rng.choice(motivos),
            "iniciada_en": ini,
            "finalizada_en": ini + timedelta(minutes=dur),
            "duracion_min": dur,
            "modalidad": rng.choice(["VIDEO", "AUDIO", "CHAT"]),
            "participantes": [
                {"rol": "MEDICO", "registro": "RT" + str(rng.randint(10000000, 99999999))},
                {"rol": "PACIENTE", "uuid": pac},
            ] + ([{"rol": "CUIDADOR", "parentesco": rng.choice(["hijo", "cónyuge", "hermana"])}]
                 if rng.random() < 0.4 else []),
            "transcripcion": [
                {"t": i * 45, "hablante": rng.choice(["MEDICO", "PACIENTE"]),
                 "texto": rng.choice(frases)}
                for i in range(rng.randint(3, 9))
            ],
            "adjuntos": [{"tipo": rng.choice(["PDF", "JPG", "DICOM"]),
                          "kb": rng.randint(40, 9000)}
                         for _ in range(rng.randint(0, 3))],
            "calidad_red": {"jitter_ms": rng.randint(5, 180),
                            "perdida_pct": round(rng.uniform(0, 7), 2),
                            "cortes": rng.randint(0, 4)},
        }


def main(lecturas_por_admision, lote, eventos, teleconsultas):
    cli = MongoClient(MONGO_URI)
    db = cli[MONGO_DB]
    rng = random.Random(SEMILLA)

    print("→ Creando colecciones, validadores e índices…")
    init_colecciones(db)

    contexto, pacientes = contexto_desde_postgres()
    print(f"→ {len(contexto)} admisiones monitorizadas leídas desde PostgreSQL")
    admisiones = [(c[0], c[2]) for c in contexto]

    total = len(admisiones) * lecturas_por_admision
    print(f"→ Ingesta de {total:,} lecturas de telemetría (lotes de {lote:,})…")
    t0 = time.perf_counter()
    n = 0
    for bloque in lotes(generar_telemetria(admisiones, lecturas_por_admision), lote):
        db.telemetria.insert_many([doc_telemetria(x) for x in bloque], ordered=False)
        n += len(bloque)
        if n % (lote * 20) == 0:
            print(f"   {n:,}/{total:,} ({n / (time.perf_counter() - t0):,.0f} docs/s)")
    dt = time.perf_counter() - t0
    print(f"✓ telemetria: {n:,} docs en {dt:.2f} s → {n / dt:,.0f} docs/s")

    print(f"→ Ingesta de {eventos:,} eventos clínicos (esquema variable)…")
    t0 = time.perf_counter()
    for bloque in lotes(generar_eventos(contexto, eventos, rng), lote):
        db.eventos_clinicos.insert_many(bloque, ordered=False)
    print(f"✓ eventos_clinicos: {eventos:,} docs en {time.perf_counter() - t0:.2f} s")

    print(f"→ Ingesta de {teleconsultas:,} teleconsultas (documentos anidados)…")
    t0 = time.perf_counter()
    for bloque in lotes(generar_teleconsultas(pacientes, teleconsultas, rng), 1000):
        db.teleconsultas.insert_many(bloque, ordered=False)
    print(f"✓ teleconsultas: {teleconsultas:,} docs en {time.perf_counter() - t0:.2f} s")

    st = db.command("collstats", "telemetria")
    print(f"\nResumen telemetria: {st['count']:,} docs | "
          f"datos {st['size'] / 1e6:.1f} MB | índices {st['totalIndexSize'] / 1e6:.1f} MB")
    cli.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lecturas-por-admision", type=int, default=1800)
    ap.add_argument("--lote", type=int, default=5000)
    ap.add_argument("--eventos", type=int, default=150000)
    ap.add_argument("--teleconsultas", type=int, default=5000)
    a = ap.parse_args()
    main(a.lecturas_por_admision, a.lote, a.eventos, a.teleconsultas)
