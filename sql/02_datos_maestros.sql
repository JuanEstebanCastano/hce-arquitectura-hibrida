-- =====================================================================
--  Archivo 02 de 05 — Datos maestros de la capa ACID
--  Nota: los códigos de habilitación y NIT son FICTICIOS, construidos
--  para el ejercicio académico. Los nombres de municipios y subregiones
--  sí corresponden a la división político-administrativa de Antioquia.
-- =====================================================================
SET search_path TO hosp, public;

-- ---------------------------------------------------------------------
-- Sedes de la red (9 hospitales, 6 subregiones, 3 niveles de atención)
-- ---------------------------------------------------------------------
INSERT INTO sede (codigo_habilita, nombre, municipio, subregion, nivel_atencion) VALUES
 ('ANT050001','E.S.E. Hospital General de Medellín','Medellín','Valle de Aburrá',3),
 ('ANT050002','E.S.E. Hospital La María','Medellín','Valle de Aburrá',3),
 ('ANT050088','E.S.E. Hospital Marco Fidel Suárez','Bello','Valle de Aburrá',2),
 ('ANT050360','E.S.E. Hospital del Sur','Itagüí','Valle de Aburrá',2),
 ('ANT056150','E.S.E. Hospital San Juan de Dios','Rionegro','Oriente',2),
 ('ANT053400','E.S.E. Hospital San Rafael','Yarumal','Norte',2),
 ('ANT058370','E.S.E. Hospital La Anunciación','Apartadó','Urabá',2),
 ('ANT054250','E.S.E. Hospital San Vicente de Paúl','Caucasia','Bajo Cauca',2),
 ('ANT057600','E.S.E. Hospital San Antonio','Andes','Suroeste',1);

-- ---------------------------------------------------------------------
-- Aseguradoras
-- ---------------------------------------------------------------------
INSERT INTO eps (nit, nombre, regimen) VALUES
 ('900123456-1','Savia Salud EPS','SUBSIDIADO'),
 ('890900841-9','SURA EPS','CONTRIBUTIVO'),
 ('800251440-6','Nueva EPS','CONTRIBUTIVO'),
 ('811016192-7','Coosalud EPS','SUBSIDIADO'),
 ('830074184-3','Salud Total EPS','CONTRIBUTIVO'),
 ('900156264-2','Magisterio (Fiduprevisora)','ESPECIAL');

-- ---------------------------------------------------------------------
-- Catálogo CIE-10 reducido (diagnósticos frecuentes en hospitalización)
-- ---------------------------------------------------------------------
INSERT INTO diagnostico_cie10 (codigo, descripcion, capitulo) VALUES
 ('J189','Neumonía, no especificada','Enfermedades del sistema respiratorio'),
 ('J441','Enfermedad pulmonar obstructiva crónica con exacerbación','Enfermedades del sistema respiratorio'),
 ('J960','Insuficiencia respiratoria aguda','Enfermedades del sistema respiratorio'),
 ('I500','Insuficiencia cardiaca congestiva','Enfermedades del sistema circulatorio'),
 ('I219','Infarto agudo del miocardio, sin otra especificación','Enfermedades del sistema circulatorio'),
 ('I10X','Hipertensión esencial (primaria)','Enfermedades del sistema circulatorio'),
 ('I64X','Accidente vascular encefálico agudo','Enfermedades del sistema circulatorio'),
 ('E119','Diabetes mellitus tipo 2 sin complicaciones','Enfermedades endocrinas y metabólicas'),
 ('E141','Diabetes mellitus no especificada con cetoacidosis','Enfermedades endocrinas y metabólicas'),
 ('A419','Sepsis, no especificada','Ciertas enfermedades infecciosas y parasitarias'),
 ('A090','Gastroenteritis y colitis de origen infeccioso','Ciertas enfermedades infecciosas y parasitarias'),
 ('N390','Infección de vías urinarias, sitio no especificado','Enfermedades del sistema genitourinario'),
 ('N179','Insuficiencia renal aguda, no especificada','Enfermedades del sistema genitourinario'),
 ('K219','Enfermedad del reflujo gastroesofágico sin esofagitis','Enfermedades del sistema digestivo'),
 ('K359','Apendicitis aguda, no especificada','Enfermedades del sistema digestivo'),
 ('S069','Traumatismo intracraneal, no especificado','Traumatismos y envenenamientos'),
 ('S720','Fractura del cuello de fémur','Traumatismos y envenenamientos'),
 ('O149','Preeclampsia, no especificada','Embarazo, parto y puerperio'),
 ('P073','Otros recién nacidos pretérmino','Afecciones originadas en el periodo perinatal'),
 ('C349','Tumor maligno de los bronquios o del pulmón','Neoplasias');

-- ---------------------------------------------------------------------
-- Pabellones: cada sede ofrece un subconjunto de servicios
-- ---------------------------------------------------------------------
INSERT INTO pabellon (id_sede, nombre, servicio)
SELECT s.id_sede,
       'PAB-' || sv.servicio || '-' || s.id_sede,
       sv.servicio
  FROM sede s
  CROSS JOIN (VALUES ('MEDICINA_INTERNA'),('CIRUGIA'),('UCI'),
                     ('PEDIATRIA'),('GINECOBSTETRICIA'),('URGENCIAS_OBS')) AS sv(servicio)
 WHERE s.nivel_atencion >= 2
    OR sv.servicio IN ('MEDICINA_INTERNA','URGENCIAS_OBS');  -- nivel 1: servicios básicos

-- UCI neonatal solo en los hospitales de tercer nivel
INSERT INTO pabellon (id_sede, nombre, servicio)
SELECT id_sede, 'PAB-UCIN-' || id_sede, 'UCI_NEONATAL'
  FROM sede WHERE nivel_atencion = 3;

-- ---------------------------------------------------------------------
-- Camas: 12 por pabellón (UCI 8)
-- ---------------------------------------------------------------------
INSERT INTO cama (id_pabellon, codigo)
SELECT p.id_pabellon,
       'C' || lpad(p.id_pabellon::text, 3, '0') || '-' || lpad(g.n::text, 2, '0')
  FROM pabellon p
  CROSS JOIN LATERAL generate_series(1,
        CASE WHEN p.servicio IN ('UCI','UCI_NEONATAL') THEN 8 ELSE 12 END) AS g(n);

-- ---------------------------------------------------------------------
-- Profesionales: 14 por sede, especialidad rotada
-- ---------------------------------------------------------------------
INSERT INTO profesional (registro_rethus, tipo_doc, num_doc, nombre_completo, especialidad, id_sede)
SELECT 'RT' || lpad(((s.id_sede*100)+g.n)::text, 8, '0'),
       'CC',
       (70000000 + (s.id_sede*1000) + g.n)::text,
       (ARRAY['Camilo','Daniela','Esteban','Marcela','Andrés','Luisa','Jhon','Paula',
              'Mauricio','Verónica','Santiago','Catalina','Felipe','Isabel'])[g.n]
         || ' ' ||
       (ARRAY['Restrepo','Zapata','Correa','Castaño','Gómez','Arango','Betancur','Vélez',
              'Monsalve','Ospina','Jaramillo','Echeverri','Quintero','Mesa'])[((s.id_sede+g.n) % 14) + 1],
       (ARRAY['Medicina Interna','Cirugía General','Cuidado Intensivo','Pediatría',
              'Ginecobstetricia','Medicina de Urgencias','Cardiología',
              'Neumología','Nefrología','Infectología','Anestesiología',
              'Medicina General','Neurología','Ortopedia'])[g.n],
       s.id_sede
  FROM sede s CROSS JOIN generate_series(1,14) AS g(n);

-- ---------------------------------------------------------------------
-- Dispositivos de monitoreo: 1 por cama de UCI + wearables por sede
-- ---------------------------------------------------------------------
INSERT INTO dispositivo (serial_fabrica, tipo, fabricante, id_sede)
SELECT 'DEV-' || lpad(c.id_cama::text, 6, '0'),
       CASE WHEN p.servicio IN ('UCI','UCI_NEONATAL') THEN 'MONITOR_MULTIPARAMETROS'
            WHEN p.servicio = 'CIRUGIA' THEN 'OXIMETRO'
            ELSE 'WEARABLE_PULSERA' END,
       (ARRAY['Philips','Mindray','GE Healthcare','Nihon Kohden'])[(c.id_cama % 4) + 1],
       p.id_sede
  FROM cama c JOIN pabellon p ON p.id_pabellon = c.id_pabellon;

-- ---------------------------------------------------------------------
-- Verificación
-- ---------------------------------------------------------------------
SELECT 'sede' t, count(*) FROM sede
UNION ALL SELECT 'eps', count(*) FROM eps
UNION ALL SELECT 'cie10', count(*) FROM diagnostico_cie10
UNION ALL SELECT 'pabellon', count(*) FROM pabellon
UNION ALL SELECT 'cama', count(*) FROM cama
UNION ALL SELECT 'profesional', count(*) FROM profesional
UNION ALL SELECT 'dispositivo', count(*) FROM dispositivo;
