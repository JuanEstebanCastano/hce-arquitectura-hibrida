// =====================================================================
//  Archivo NoSQL 01 — Inicialización de la capa BASE (MongoDB 8.0)
//  HCE Básica — Red de Hospitales Públicos de la Gobernación de Antioquia
//
//  Ejecutar:  mongosh "mongodb://127.0.0.1:27017/hce_antioquia" nosql/01_init_colecciones.js
//
//  Nota de diseño: MongoDB NO genera identidad de paciente. Solo referencia
//  los UUID que PostgreSQL emitió (uuid_paciente, uuid_admision). La capa
//  documental es dueña del EVENTO, nunca del SUJETO.
// =====================================================================
const db = db.getSiblingDB("hce_antioquia");

// ---------------------------------------------------------------------
// 1. telemetria — lecturas de monitores y wearables (alto volumen)
//    Validador "moderate": exige la llave puente y la marca de tiempo,
//    pero deja libre el resto del documento. Esa es la flexibilidad BASE:
//    un sensor nuevo puede empezar a escribir campos sin migrar nada.
// ---------------------------------------------------------------------
db.telemetria.drop();
db.createCollection("telemetria", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["uuid_admision", "medido_en"],
      properties: {
        uuid_admision: { bsonType: "string", description: "UUID emitido por PostgreSQL" },
        id_dispositivo: { bsonType: "int" },
        tipo_sensor: { bsonType: "string" },
        medido_en: { bsonType: "date" },
        alarma: { bsonType: "bool" },
        signos: {
          bsonType: "object",
          properties: {
            fc:   { bsonType: "int", minimum: 20,  maximum: 250 },
            fr:   { bsonType: "int", minimum: 4,   maximum: 60 },
            spo2: { bsonType: "int", minimum: 50,  maximum: 100 },
            temp_c: { bsonType: "double", minimum: 30.0, maximum: 45.0 },
            pas:  { bsonType: "int", minimum: 40,  maximum: 260 },
            pad:  { bsonType: "int", minimum: 20,  maximum: 160 }
          }
        }
      }
    }
  },
  validationLevel: "moderate",
  validationAction: "warn"   // BASE: no se rechaza el dato, se marca. Perder
                             // una lectura es peor que recibirla imperfecta.
});

// Índice compuesto: patrón de acceso dominante = "últimas lecturas de ESTA admisión"
db.telemetria.createIndex({ uuid_admision: 1, medido_en: -1 }, { name: "ix_adm_tiempo" });
// Índice parcial: solo las alarmas. Ocupa una fracción y sirve al tablero clínico.
db.telemetria.createIndex({ medido_en: -1 }, {
  name: "ix_alarmas", partialFilterExpression: { alarma: true }
});

// ---------------------------------------------------------------------
// 2. eventos_clinicos — bitácora semiestructurada (esquema variable)
//    Cada tipo de evento trae campos distintos. En un modelo relacional
//    esto exigiría o una tabla por tipo, o una tabla con decenas de
//    columnas nulas, o un EAV. Aquí es natural.
// ---------------------------------------------------------------------
db.eventos_clinicos.drop();
db.createCollection("eventos_clinicos");
db.eventos_clinicos.createIndex({ uuid_admision: 1, ocurrido_en: -1 }, { name: "ix_adm_evento" });
db.eventos_clinicos.createIndex({ tipo: 1, ocurrido_en: -1 }, { name: "ix_tipo_evento" });
db.eventos_clinicos.createIndex({ severidad: 1 }, {
  name: "ix_criticos", partialFilterExpression: { severidad: "CRITICA" }
});

// ---------------------------------------------------------------------
// 3. teleconsultas — documentos anidados (participantes, transcripción,
//    adjuntos, métricas de red). Un documento = una consulta completa.
// ---------------------------------------------------------------------
db.teleconsultas.drop();
db.createCollection("teleconsultas");
db.teleconsultas.createIndex({ uuid_paciente: 1, iniciada_en: -1 }, { name: "ix_pac_teleconsulta" });
db.teleconsultas.createIndex({ "transcripcion.texto": "text" }, { name: "ix_texto_transcripcion" });

// ---------------------------------------------------------------------
// 4. Verificación
// ---------------------------------------------------------------------
["telemetria", "eventos_clinicos", "teleconsultas"].forEach(function (c) {
  print(c + " → índices: " + db.getCollection(c).getIndexes().map(i => i.name).join(", "));
});
