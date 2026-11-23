# Arquitectura híbrida PostgreSQL + MongoDB — Subsistema de hospitalización (HCE)

**Unidad 3 · Bases de Datos II · Institución Universitaria Pascual Bravo**
Equipo: Laura ⟨apellidos⟩ · Jhon Jader Correa · Juan Esteban Castaño Montoya

Arquitectura de persistencia híbrida para el subsistema de hospitalización de la
HCE Básica de la Red de Hospitales Públicos de la Gobernación de Antioquia. La
identidad del paciente y la trazabilidad del acto asistencial viven en
**PostgreSQL 16** bajo garantías ACID; la telemetría de monitores y wearables,
la bitácora de eventos clínicos y las teleconsultas viven en **MongoDB 8.0** bajo
un modelo BASE.

Todo lo que el informe afirma está respaldado por una ejecución reproducible:
`bash scripts/ejecutar_todo.sh` reconstruye el escenario completo y regenera las
métricas y las figuras desde cero.

---

## Resultados en una tabla

Mismo conjunto de **1.008.000 lecturas de signos vitales** cargado en ambos
motores, con los mismos tres índices por motor y el mismo tipo de estructura de
destino (tabla permanente contra colección normal: una tabla temporal de
PostgreSQL no escribe WAL y falsearía la comparación). 30 repeticiones por
operación, 10 en las agregaciones globales. Latencia mediana p50 en milisegundos.

| Operación | PostgreSQL | MongoDB | Ganador |
|---|---:|---:|---|
| Ingesta de un lote de 5.000 lecturas | 75,46 | **37,59** | MongoDB 2,0× |
| Ingesta con `COPY` (vía nativa) | **13,81** | — | PostgreSQL 2,7× sobre MongoDB |
| Últimas 50 lecturas de una admisión | **0,12** | 0,35 | PostgreSQL 3,1× |
| Alarmas de las últimas 6 h | **27,08** | 185,72 | PostgreSQL 6,9× |
| Tendencia horaria de una admisión | **0,55** | 7,99 | PostgreSQL 14,6× |
| Top-10 admisiones por alarmas | **42,64** | 789,17 | PostgreSQL 18,5× |
| Egreso transaccional (5 tablas) | **0,30** | rechazado | Solo PostgreSQL |
| Disco ocupado (datos + índices) | 192,5 MB | **72,3 MB** | MongoDB 2,7× |

Tablero de UCI (consulta híbrida sobre las dos capas): 170,92 ms con la
orquestación ingenua, **7,12 ms** tras rediseñarla — la mejora más grande del
trabajo, y no vino de tocar ningún motor.

Métricas crudas: [`bench/resultados/metricas.csv`](bench/resultados/metricas.csv) ·
[`metricas.json`](bench/resultados/metricas.json) ·
planes de ejecución: [`planes.txt`](bench/resultados/planes.txt)

---

## Estructura del repositorio

```
hce-hibrido/
├── sql/                         Capa ACID — PostgreSQL 16
│   ├── 01_schema.sql            DDL: 12 tablas, dominios, enums, procedimiento de egreso
│   ├── 02_datos_maestros.sql    Sedes, EPS, CIE-10, pabellones, camas, profesionales
│   ├── 03_indices.sql           Índices por patrón de acceso (incluye el que el planeador descarta)
│   └── 04_consultas.sql         Operaciones OP-2 a OP-8 del análisis comparativo
├── nosql/                       Capa BASE — MongoDB 8.0
│   ├── 01_init_colecciones.js   Colecciones, validadores $jsonSchema, índices
│   └── 02_consultas_agregacion.js  Pipelines equivalentes a las consultas SQL
├── scripts/
│   ├── comun.py                 Generador determinista de telemetría (semilla fija)
│   ├── 01_poblar_postgres.py    20.000 pacientes · 8.000 admisiones
│   ├── 02_ingesta_mongo.py      Ingesta masiva: 1.008.000 + 150.000 + 5.000 docs
│   ├── 03_espejo_postgres.py    Misma telemetría en PostgreSQL (para comparar)
│   └── ejecutar_todo.sh         Reproduce el escenario completo de punta a punta
├── bench/
│   ├── benchmark.py             9 operaciones instrumentadas, p50/p95/p99
│   ├── graficas.py              Figuras del informe
│   └── resultados/              metricas.csv · metricas.json · planes.txt
├── docs/
│   ├── informe-tecnico.pdf      Informe técnico (entregable principal)
│   ├── informe-tecnico.md       Fuente del informe
│   ├── diagrama-arquitectura.mmd  Diagrama (Mermaid)
│   ├── prompts-ia.md            Bitácora de uso de IA, con lo aceptado y lo rechazado
│   ├── guion-video.md           Guion del video con reparto por integrante
│   └── figuras/                 fig0 … fig4 en PNG
└── evidencias/                  Salida de consola de cada paso de la ejecución
```

---

## Cómo reproducirlo

### Requisitos

- PostgreSQL 16 en marcha, accesible con el usuario `postgres`
- MongoDB 8.0 en marcha en `127.0.0.1:27017`
- Python 3.11+ con `psycopg2-binary`, `pymongo`, `matplotlib`
- Opcional: `mongosh` (para los scripts `.js`), `pandoc` + `xelatex` (para regenerar el PDF)

```bash
pip install psycopg2-binary pymongo matplotlib
```

### Ejecución completa

```bash
bash scripts/ejecutar_todo.sh
```

Toma alrededor de 2 minutos: crea la base, carga maestros e índices, pobla
20.000 pacientes y 8.000 admisiones, ingiere 1.163.000 documentos en MongoDB,
replica la telemetría en PostgreSQL, corre el benchmark y genera las figuras.

### Ejecución por pasos

```bash
psql -U postgres -c "CREATE DATABASE hce_antioquia"
psql -U postgres -d hce_antioquia -f sql/01_schema.sql
psql -U postgres -d hce_antioquia -f sql/02_datos_maestros.sql
psql -U postgres -d hce_antioquia -f sql/03_indices.sql

python3 scripts/01_poblar_postgres.py
python3 scripts/02_ingesta_mongo.py        # o: mongosh … nosql/01_init_colecciones.js
python3 scripts/03_espejo_postgres.py

python3 bench/benchmark.py --repeticiones 30
python3 bench/graficas.py
```

### Variables de entorno

| Variable | Por defecto |
|---|---|
| `PG_DSN` | `host=/tmp port=5432 dbname=hce_antioquia user=postgres` |
| `MONGO_URI` | `mongodb://127.0.0.1:27017` |
| `MONGO_DB` | `hce_antioquia` |

---

## Decisiones de diseño que conviene conocer antes de leer el código

**La llave puente es unidireccional.** PostgreSQL es el único emisor de
identidad: `paciente.uuid_paciente` y `admision.uuid_admision` se generan con
`gen_random_uuid()` y MongoDB solo los referencia; el proceso de ingesta lee las
llaves desde PostgreSQL antes de escribir. El validador de `telemetria` declara
`uuid_admision` como campo requerido, pero está en modo `warn`: **advierte, no
rechaza**. La cola de eventos huérfanos que aparece en el diagrama es diseño
pendiente, no código de esta entrega (ver sección 2.4 del informe).

**`hosp.telemetria_signos` no es parte de la arquitectura.** Es una tabla espejo
que existe únicamente para poder comparar la misma operación en los dos motores.
En producción, la telemetría vive solo en MongoDB.

**El generador de datos es determinista.** `scripts/comun.py` usa semilla fija,
lo que permite cargar exactamente las mismas lecturas en ambos motores. Sin eso,
la comparación de latencias no significaría nada.

**El validador de MongoDB es `moderate`/`warn`, no `strict`/`error`.** Decisión
clínica, no técnica: en telemetría, perder una lectura es peor que recibirla
imperfecta. Se explica en la sección 3.2 del informe.

---

## Limitaciones conocidas

- Nodo único en ambos motores: no se evaluó replicación ni *sharding*, que es
  donde el modelo de MongoDB rinde de verdad. Por eso el egreso transaccional en
  MongoDB aparece como «rechazado»: las transacciones multi-documento exigen
  *replica set*.
- Todas las operaciones se midieron en serie; no se evaluó concurrencia.
- PostgreSQL se conecta por *socket* Unix y MongoDB por TCP sobre *loopback*: la
  diferencia de transporte favorece marginalmente a PostgreSQL en las operaciones
  de latencia submilisegundo (OP-2).
- Los datos son sintéticos. La tasa de alarmas (33 %) es más alta que la de una
  UCI real y favorece el recorrido secuencial de PostgreSQL en OP-5.
- OP-5 en PostgreSQL es la operación más ruidosa del conjunto: se observaron
  medianas entre 20 ms y 46 ms entre ejecuciones sucesivas, según el estado de la
  caché y del mapa de visibilidad. Las conclusiones se apoyan en diferencias de
  un orden de magnitud; las menores al 20 % no deberían interpretarse.
- Las cifras de esta tabla corresponden a la ejecución del 22/11/2026 registrada
  en `evidencias/09_benchmark.log`. Al reejecutar el benchmark varían unos pocos
  puntos porcentuales; las relaciones entre motores se mantienen. Si se vuelve a
  correr, conviene actualizar también las cifras citadas en el informe.

---

## Pendientes antes de entregar

- [ ] Completar los apellidos de Laura en `README.md`, `sql/01_schema.sql`, `docs/informe-tecnico.md`, `docs/prompts-ia.md` y `docs/guion-video.md` (marcados como `⟨apellidos⟩`).
- [ ] Poner la URL del repositorio al final de `docs/informe-tecnico.md` (marcada como `⟨URL…⟩`).
- [ ] Regenerar el PDF si se editó el informe: `cd docs && pandoc informe-tecnico.md -o informe-tecnico.pdf --pdf-engine=xelatex --toc`
- [ ] Inicializar el repositorio y publicarlo: `git init && git add . && git commit -m "Unidad 3: arquitectura híbrida PostgreSQL + MongoDB"`, luego subirlo a GitHub/GitLab.
- [ ] Grabar el video siguiendo `docs/guion-video.md`.
- [ ] Añadir capturas de pantalla propias a `evidencias/` (los `.log` ya están).
