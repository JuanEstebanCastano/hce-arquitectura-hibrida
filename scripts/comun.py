"""
comun.py — utilidades compartidas por los scripts de carga y benchmark.

Clave del diseño: el generador de telemetría es DETERMINISTA (semilla fija).
Así, PostgreSQL y MongoDB reciben exactamente el mismo conjunto de lecturas
y la comparación de latencias es justa: misma operación, mismos datos.
"""
import os
import random
from datetime import datetime, timedelta, timezone

PG_DSN = os.environ.get(
    "PG_DSN", "host=/tmp port=5432 dbname=hce_antioquia user=postgres"
)
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DB = os.environ.get("MONGO_DB", "hce_antioquia")

SEMILLA = 20263  # Unidad 3 - 2026
TZ = timezone(timedelta(hours=-5))  # America/Bogota

# Perfiles clínicos: cada admisión monitorizada sigue un patrón fisiológico
# distinto, para que las agregaciones no devuelvan valores planos.
PERFILES = {
    "ESTABLE":      dict(fc=(68, 6),  fr=(16, 2), spo2=(97, 1), temp=(36.6, 0.3), pas=(118, 8),  pad=(76, 5)),
    "SEPTICO":      dict(fc=(112, 12), fr=(26, 4), spo2=(92, 3), temp=(38.9, 0.6), pas=(92, 12), pad=(55, 8)),
    "RESPIRATORIO": dict(fc=(96, 9),  fr=(28, 5), spo2=(88, 4), temp=(37.4, 0.5), pas=(128, 10), pad=(80, 7)),
    "CARDIACO":     dict(fc=(58, 14), fr=(18, 3), spo2=(94, 2), temp=(36.4, 0.3), pas=(148, 16), pad=(92, 9)),
    "POSTQX":       dict(fc=(88, 8),  fr=(18, 3), spo2=(95, 2), temp=(37.1, 0.4), pas=(110, 9),  pad=(70, 6)),
}
NOMBRES_PERFIL = list(PERFILES)

TIPOS_SENSOR = ["MONITOR_MULTIPARAMETROS", "WEARABLE_PULSERA", "OXIMETRO"]


def _val(rng, media, desv, minimo, maximo, entero=True):
    v = rng.gauss(media, desv)
    v = max(minimo, min(maximo, v))
    return int(round(v)) if entero else round(v, 1)


def generar_telemetria(admisiones, lecturas_por_admision, intervalo_seg=30,
                       inicio=None, semilla=SEMILLA):
    """
    Genera lecturas de signos vitales de forma determinista.

    admisiones: lista de tuplas (uuid_admision:str, id_dispositivo:int)
    Devuelve un generador de dicts con claves neutras, que cada motor
    adapta a su modelo (fila relacional o documento BSON).
    """
    rng = random.Random(semilla)
    if inicio is None:
        inicio = datetime(2026, 11, 20, 6, 0, 0, tzinfo=TZ)

    for idx, (uuid_adm, id_disp) in enumerate(admisiones):
        perfil_nombre = NOMBRES_PERFIL[idx % len(NOMBRES_PERFIL)]
        p = PERFILES[perfil_nombre]
        t = inicio + timedelta(seconds=rng.randint(0, 600))
        # deriva lenta: el paciente mejora o se deteriora durante la estancia
        deriva = rng.choice([-1, 0, 1])
        for k in range(lecturas_por_admision):
            avance = (k / max(1, lecturas_por_admision)) * deriva
            fc = _val(rng, p["fc"][0] + 10 * avance, p["fc"][1], 35, 190)
            fr = _val(rng, p["fr"][0] + 3 * avance, p["fr"][1], 6, 50)
            spo2 = _val(rng, p["spo2"][0] - 4 * avance, p["spo2"][1], 60, 100)
            temp = _val(rng, p["temp"][0] + 0.6 * avance, p["temp"][1], 33.0, 42.0, entero=False)
            pas = _val(rng, p["pas"][0] - 10 * avance, p["pas"][1], 60, 220)
            pad = _val(rng, p["pad"][0] - 6 * avance, p["pad"][1], 30, 140)
            alarma = spo2 < 90 or fc > 130 or fc < 45 or pas < 90 or temp >= 39.0
            yield dict(
                uuid_admision=uuid_adm,
                id_dispositivo=id_disp,
                tipo_sensor=TIPOS_SENSOR[id_disp % 3],
                perfil=perfil_nombre,
                medido_en=t + timedelta(seconds=k * intervalo_seg),
                fc=fc, fr=fr, spo2=spo2, temp_c=temp, pas=pas, pad=pad,
                alarma=alarma,
            )


def lotes(iterable, n):
    """Agrupa un iterable en lotes de tamaño n (para inserción por bloques)."""
    buf = []
    for item in iterable:
        buf.append(item)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf
