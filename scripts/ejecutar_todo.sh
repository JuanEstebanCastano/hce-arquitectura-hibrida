#!/usr/bin/env bash
# =====================================================================
#  ejecutar_todo.sh — Reproduce el escenario completo de la Unidad 3
#  de punta a punta: esquema, datos, ingesta masiva, benchmark y figuras.
#
#  Requisitos previos:
#    · PostgreSQL 16 en marcha y accesible con el usuario postgres
#    · MongoDB 8.0 en marcha en 127.0.0.1:27017
#    · Python 3 con: psycopg2-binary, pymongo, matplotlib
#
#  Variables de entorno opcionales:
#    PGHOST (por defecto /tmp)   PG_DSN   MONGO_URI   MONGO_DB
#
#  Uso:  bash scripts/ejecutar_todo.sh
# =====================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

PGHOST="${PGHOST:-/tmp}"
PSQL="psql -h $PGHOST -U postgres -v ON_ERROR_STOP=1"
EV="evidencias"
mkdir -p "$EV"

echo "──────────────────────────────────────────────────────────"
echo " 1/9  Base de datos y esquema relacional (capa ACID)"
echo "──────────────────────────────────────────────────────────"
$PSQL -d postgres -c "DROP DATABASE IF EXISTS hce_antioquia;"
$PSQL -d postgres -c "CREATE DATABASE hce_antioquia ENCODING 'UTF8' TEMPLATE template0;"
$PSQL -d hce_antioquia -f sql/01_schema.sql        | tee "$EV/01_schema.log"       | tail -3

echo
echo " 2/9  Datos maestros"
$PSQL -d hce_antioquia -f sql/02_datos_maestros.sql | tee "$EV/02_maestros.log"    | tail -12

echo
echo " 3/9  Índices"
$PSQL -d hce_antioquia -f sql/03_indices.sql        | tee "$EV/03_indices.log"     | tail -8

echo
echo " 4/9  Poblamiento transaccional (pacientes y admisiones)"
python3 scripts/01_poblar_postgres.py               | tee "$EV/04_poblar_pg.log"

echo
echo " 5/9  Colecciones, validadores e índices de MongoDB"
if command -v mongosh >/dev/null; then
  mongosh --quiet "${MONGO_URI:-mongodb://127.0.0.1:27017}/${MONGO_DB:-hce_antioquia}" \
      nosql/01_init_colecciones.js                  | tee "$EV/05_init_mongo.log"
else
  echo "(mongosh no disponible: 02_ingesta_mongo.py aplica las mismas definiciones)"
fi

echo
echo " 6/9  Ingesta masiva en MongoDB (capa BASE)"
python3 scripts/02_ingesta_mongo.py                 | tee "$EV/06_ingesta_mongo.log"

echo
echo " 7/9  Carga espejo en PostgreSQL (para comparar la MISMA operación)"
python3 scripts/03_espejo_postgres.py               | tee "$EV/07_espejo_pg.log"

echo
echo " 8/9  Benchmark comparativo y figuras"
python3 bench/benchmark.py --repeticiones 30        | tee "$EV/09_benchmark.log"
python3 bench/graficas.py                           | tee "$EV/10_graficas.log"

echo
echo " 9/9  Operaciones de consulta documentadas en ambos motores"
if command -v mongosh >/dev/null; then
  mongosh --quiet "${MONGO_URI:-mongodb://127.0.0.1:27017}/${MONGO_DB:-hce_antioquia}" \
      nosql/02_consultas_agregacion.js              > "$EV/11_consultas_mongo.log" 2>&1 \
      && echo "   nosql/02_consultas_agregacion.js  → evidencias/11_consultas_mongo.log"
fi
$PSQL -d hce_antioquia -f sql/04_consultas.sql      > "$EV/11_consultas_sql.log" 2>&1 \
  && echo "   sql/04_consultas.sql               → evidencias/11_consultas_sql.log"


echo
echo "──────────────────────────────────────────────────────────"
echo " Listo. Resultados:"
echo "   bench/resultados/metricas.csv   — tabla de latencias"
echo "   bench/resultados/metricas.json  — métricas + almacenamiento"
echo "   bench/resultados/planes.txt     — EXPLAIN de ambos motores"
echo "   docs/figuras/*.png              — figuras del informe"
echo "   evidencias/*.log                — salida de consola de cada paso"
echo "──────────────────────────────────────────────────────────"

# Opcional: regenerar el PDF del informe (requiere pandoc + xelatex)
if command -v pandoc >/dev/null && command -v xelatex >/dev/null; then
  echo "Regenerando docs/informe-tecnico.pdf…"
  (cd docs && pandoc informe-tecnico.md -o informe-tecnico.pdf \
      --pdf-engine=xelatex --toc)
  echo "✓ docs/informe-tecnico.pdf"
else
  echo "(pandoc/xelatex no disponibles: se omite la regeneración del PDF)"
fi
