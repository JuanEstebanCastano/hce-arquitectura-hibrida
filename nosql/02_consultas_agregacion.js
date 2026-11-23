// =====================================================================
//  Archivo NoSQL 02 — Operaciones de consulta sobre la capa BASE
//  Ejecutar: mongosh "mongodb://127.0.0.1:27017/hce_antioquia" nosql/02_consultas_agregacion.js
//
//  La numeración OP-n coincide con bench/benchmark.py y con el informe.
// =====================================================================
const db = db.getSiblingDB("hce_antioquia");
const uuidDemo = db.telemetria.findOne({}, { uuid_admision: 1 }).uuid_admision;
print("Admisión de demostración: " + uuidDemo);
print("Documentos en telemetria: " + db.telemetria.estimatedDocumentCount());

// =====================================================================
// OP-2 · Lectura puntual: últimas 50 lecturas de una admisión
//        Resuelta íntegramente por ix_adm_tiempo.
// =====================================================================
printjson(
  db.telemetria.find(
    { uuid_admision: uuidDemo },
    { _id: 0, medido_en: 1, "signos.fc": 1, "signos.spo2": 1, "signos.pas": 1, "signos.pad": 1 }
  ).sort({ medido_en: -1 }).limit(5).toArray()
);

// Plan de ejecución real
printjson(
  db.telemetria.find({ uuid_admision: uuidDemo })
    .sort({ medido_en: -1 }).limit(50)
    .explain("executionStats").executionStats
);

// =====================================================================
// OP-3 · Ventana temporal: alarmas de las últimas 6 horas
// =====================================================================
const tMax = db.telemetria.find({}, { medido_en: 1 }).sort({ medido_en: -1 }).limit(1).toArray()[0].medido_en;
const desde = new Date(tMax.getTime() - 6 * 3600 * 1000);

printjson(
  db.telemetria.aggregate([
    { $match: { alarma: true, medido_en: { $gte: desde } } },
    { $group: { _id: null, lecturas_en_alarma: { $sum: 1 }, adm: { $addToSet: "$uuid_admision" } } },
    { $project: { _id: 0, lecturas_en_alarma: 1, pacientes_afectados: { $size: "$adm" } } }
  ]).toArray()
);

// =====================================================================
// OP-4 · Tendencia horaria de una admisión ($dateTrunc)
// =====================================================================
printjson(
  db.telemetria.aggregate([
    { $match: { uuid_admision: uuidDemo } },
    { $group: {
        _id: { $dateTrunc: { date: "$medido_en", unit: "hour" } },
        fc_promedio:  { $avg: "$signos.fc" },
        fc_maxima:    { $max: "$signos.fc" },
        spo2_minima:  { $min: "$signos.spo2" },
        spo2_promedio:{ $avg: "$signos.spo2" },
        alarmas:      { $sum: { $cond: ["$alarma", 1, 0] } }
    } },
    { $sort: { _id: 1 } },
    { $limit: 5 }
  ]).toArray()
);

// =====================================================================
// OP-5 · Agregación global: top-10 admisiones por número de alarmas
// =====================================================================
printjson(
  db.telemetria.aggregate([
    { $match: { alarma: true } },
    { $group: { _id: "$uuid_admision", alarmas: { $sum: 1 } } },
    { $sort: { alarmas: -1 } },
    { $limit: 10 }
  ]).toArray()
);

// =====================================================================
// OP-6 · Lado documental de la consulta híbrida
//        PostgreSQL entrega la lista de uuid_admision de las camas de UCI;
//        aquí se resuelve el último signo vital de cada una.
//        Variante optimizada: una búsqueda por admisión, servida por índice,
//        en lugar de agrupar decenas de miles de documentos.
// =====================================================================
// NOTA: en el sistema real estas claves vienen de PostgreSQL
//   SELECT uuid_admision FROM hosp.v_admisiones_activas WHERE servicio='UCI' LIMIT 20
// (ver bench/benchmark.py, OP-6). Aquí se toman de MongoDB solo para que este
// archivo sea ejecutable de forma aislada en mongosh, sin conexión al relacional.
const claves = db.telemetria.distinct("uuid_admision").slice(0, 20);
printjson(
  claves.slice(0, 3).map(function (k) {
    return db.telemetria.findOne(
      { uuid_admision: k },
      { _id: 0, uuid_admision: 1, medido_en: 1, signos: 1, alarma: 1 },
      { sort: { medido_en: -1 } }
    );
  })
);

// =====================================================================
// OP-7 · Flexibilidad de esquema: un sensor nuevo empieza a escribir
//        campos que ningún documento anterior tenía. Cero migraciones.
// =====================================================================
db.telemetria.insertOne({
  uuid_admision: uuidDemo,
  id_dispositivo: 90001,
  tipo_sensor: "GLUCOMETRO_CONTINUO",
  medido_en: new Date(),
  signos: { glucosa_mgdl: 126, etco2: 38 },
  alarma: false,
  calibracion: { lote: "GLX-2026-11", vencimiento: "2027-03-01" }
});
print("Documentos con el campo nuevo signos.glucosa_mgdl: " +
      db.telemetria.countDocuments({ "signos.glucosa_mgdl": { $exists: true } }));

// =====================================================================
// Consultas sobre la bitácora semiestructurada (esquema variable)
// =====================================================================

// Tiempo de respuesta a alarmas críticas por severidad
printjson(
  db.eventos_clinicos.aggregate([
    { $match: { tipo: "ALARMA_MONITOR" } },
    { $group: { _id: "$severidad",
                n: { $sum: 1 },
                respuesta_prom_seg: { $avg: "$atendida_en_seg" },
                respuesta_p_max: { $max: "$atendida_en_seg" } } },
    { $sort: { respuesta_prom_seg: -1 } }
  ]).toArray()
);

// Medicamentos más administrados y tasa de doble verificación
printjson(
  db.eventos_clinicos.aggregate([
    { $match: { tipo: "ADMIN_MEDICAMENTO" } },
    { $group: { _id: { medicamento: "$medicamento", via: "$via" },
                administraciones: { $sum: 1 },
                doble_verificacion: { $avg: { $cond: ["$verificacion_doble", 1, 0] } } } },
    { $sort: { administraciones: -1 } },
    { $limit: 6 }
  ]).toArray()
);

// Errores de sincronización por protocolo: campos que NO existen en los
// demás tipos de evento. En un modelo relacional esto exigiría otra tabla.
printjson(
  db.eventos_clinicos.aggregate([
    { $match: { tipo: "ERROR_SINCRONIZACION" } },
    { $group: { _id: "$payload_crudo.protocolo",
                errores: { $sum: 1 },
                reintentos_prom: { $avg: "$reintentos" },
                checksum_ok_pct: { $avg: { $cond: ["$payload_crudo.checksum_ok", 100, 0] } } } },
    { $sort: { errores: -1 } }
  ]).toArray()
);

// Teleconsultas: documentos anidados + calidad de red
printjson(
  db.teleconsultas.aggregate([
    { $group: { _id: "$modalidad",
                consultas: { $sum: 1 },
                duracion_prom_min: { $avg: "$duracion_min" },
                jitter_prom_ms: { $avg: "$calidad_red.jitter_ms" },
                turnos_transcritos_prom: { $avg: { $size: "$transcripcion" } },
                con_cuidador: { $sum: { $cond: [{ $gt: [{ $size: "$participantes" }, 2] }, 1, 0] } } } },
    { $sort: { consultas: -1 } }
  ]).toArray()
);

// Búsqueda de texto completo sobre la transcripción (índice de texto)
printjson(
  db.teleconsultas.find(
    { $text: { $search: "adherencia tratamiento" } },
    { score: { $meta: "textScore" }, motivo: 1, iniciada_en: 1, _id: 0 }
  ).sort({ score: { $meta: "textScore" } }).limit(3).toArray()
);
