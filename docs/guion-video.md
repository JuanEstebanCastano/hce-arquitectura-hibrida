# Guion del video demostrativo (8–10 minutos)

**Entrega:** Unidad 3 — Bases de Datos II
**Integrantes y reparto:** Laura ⟨apellidos⟩ · Jhon Jader Correa · Juan Esteban
Castaño Montoya

La rúbrica exige **participación equilibrada**: cada integrante habla en pantalla
y además *ejecuta* algo en vivo. Tiempo total objetivo: **9:00**, repartido en
tres partes prácticamente iguales:

| Integrante | Bloques | Tiempo |
|---|---|---:|
| Laura | 1 (arquitectura) + parte de 4 (optimizaciones) | ~2:50 |
| Jhon Jader | 2 (PostgreSQL en vivo) + parte de 4 + mitad del cierre | ~3:10 |
| Juan Esteban | 3 (MongoDB en vivo) + parte de 4 + mitad del cierre | ~3:00 |

---

## Antes de grabar — lista de verificación

- [ ] PostgreSQL y MongoDB levantados, datos ya cargados (`bash scripts/ejecutar_todo.sh`).
- [ ] Tres terminales abiertas y con fuente grande (16–18 pt): `psql`, `mongosh`, y una para el benchmark.
- [ ] `docs/figuras/fig0_arquitectura.png` abierto a pantalla completa.
- [ ] `bench/resultados/metricas.csv` abierto.
- [ ] Repositorio abierto en el navegador.
- [ ] Grabar a 1080p; probar audio de los tres antes de empezar.
- [ ] Ensayar una vez con cronómetro: la parte 4 siempre se alarga.

---

## Bloque 1 · Problema y arquitectura — **Laura** (0:00 – 2:15)

**En pantalla:** diagrama `fig0_arquitectura.png`.

Qué decir, en este orden:

1. **El problema en una cifra** (0:00–0:30). El subsistema de hospitalización de
   la red genera 1.612.800 lecturas de signos vitales al día con 560 camas
   monitorizadas. Esa carga no se parece en nada a la de registrar una admisión.
2. **El teorema CAP como decisión por clase de dato** (0:30–1:15). La partición
   de red no se elige, ocurre. Lo que se elige es qué se sacrifica. Y la
   respuesta es distinta para cada dato: si se cae la red mientras llega una
   saturación de oxígeno, hay que aceptarla (AP); si se cae mientras dos sedes
   asignan la misma cama, hay que rechazar la operación (CP).
3. **El reparto** (1:15–1:50). Señalar en el diagrama: identidad, admisión, cama,
   egreso → PostgreSQL. Telemetría, eventos, teleconsultas → MongoDB.
   Enunciar la regla de las tres preguntas.
4. **La llave puente** (1:50–2:15). PostgreSQL es el único emisor de identidad;
   MongoDB solo referencia `uuid_admision` y `uuid_paciente`. Señalar las flechas
   punteadas. Decir por qué: sin esa asimetría, la arquitectura híbrida se
   convierte en dos bases de datos que hablan de pacientes distintos.

> **No hacer:** leer el diagrama entero. Señalar tres cosas y explicar la cuarta.

---

## Bloque 2 · Implementación relacional en vivo — **Jhon Jader** (2:15 – 4:30)

**En pantalla:** terminal con `psql`.

```sql
-- 1. El esquema existe y está poblado
\dt hosp.*
SELECT count(*) FROM hosp.paciente;
SELECT count(*) FROM hosp.admision WHERE estado = 'ABIERTA';
```

```sql
-- 2. La invariante que define la capa ACID: intentar ocupar una cama ocupada
SELECT id_cama FROM hosp.admision WHERE estado='ABIERTA' LIMIT 1;   -- anotar el id

INSERT INTO hosp.admision (id_paciente, id_sede, id_cama, id_medico_tratante, cod_dx_ingreso)
VALUES (99, 1, ⟨ese id_cama⟩, 1, 'J189');
-- ERROR: duplicate key value violates unique constraint "uq_cama_ocupada_una_vez"
```

> **Decir mientras aparece el error:** «Esto es lo que significa ACID en la
> práctica. La regla no está en la aplicación: está en el motor, y ninguna ruta
> de escritura puede saltársela.»

```sql
-- 3. Transacción multi-entidad: el egreso toca 4 tablas, todo o nada
CALL hosp.sp_egresar_paciente(
   (SELECT id_admision FROM hosp.admision WHERE estado='ABIERTA' ORDER BY 1 LIMIT 1),
   'MEJORIA', 'I10X');

SELECT estado, tipo_egreso, fecha_egreso FROM hosp.admision WHERE id_admision = ⟨id⟩;
SELECT estado FROM hosp.cama WHERE id_cama = ⟨id_cama⟩;      -- quedó en LIMPIEZA
SELECT * FROM hosp.auditoria_egreso ORDER BY id_auditoria DESC LIMIT 1;
```

Cerrar el bloque mostrando `sql/01_schema.sql` en el repositorio: dominios,
tipos enumerados, `CHECK` de coherencia de estado.

---

## Bloque 3 · Ingesta masiva y capa documental — **Juan Esteban** (4:30 – 6:45)

**En pantalla:** terminal con `mongosh` y, en paralelo, la ingesta corriendo.

```bash
# 1. Ingesta masiva en vivo (dura ~15 s, se ve el contador avanzar)
python3 scripts/02_ingesta_mongo.py
```

> Mientras corre: «1.008.000 lecturas en lotes de 5.000. Fíjense en el
> rendimiento que va reportando: alrededor de 75.000 documentos por segundo,
> incluyendo el tiempo de generar los datos.»

```javascript
// 2. Lo que la capa BASE hace bien #1: volumen
db.telemetria.estimatedDocumentCount()
db.telemetria.findOne()

// 3. Lo que la capa BASE hace bien #2: esquema variable en una sola colección
db.eventos_clinicos.findOne({ tipo: "ALARMA_MONITOR" })
db.eventos_clinicos.findOne({ tipo: "ERROR_SINCRONIZACION" })
// → dos documentos de la misma colección con campos completamente distintos

// 4. Lo que la capa BASE hace bien #3: un sensor nuevo, cero migraciones
db.telemetria.insertOne({
  uuid_admision: ⟨uuid⟩, tipo_sensor: "GLUCOMETRO_CONTINUO",
  medido_en: new Date(), signos: { glucosa_mgdl: 126, etco2: 38 },
  calibracion: { lote: "GLX-2026-11" } })
db.telemetria.countDocuments({ "signos.glucosa_mgdl": { $exists: true } })
```

> **Decir:** «Ningún DDL, ninguna ventana de despliegue. Ese es el argumento
> real de la capa BASE, y no la velocidad.»

> **Precisión que conviene decir en voz alta:** el validador está en modo `warn`.
> Documenta qué campos son obligatorios y permite auditarlos, pero no descarta el
> documento que los incumple. En telemetría esa es la decisión correcta: perder
> una lectura es peor que recibirla imperfecta.

---

## Bloque 4 · Análisis comparativo — **los tres, alternando** (6:45 – 8:30)

**En pantalla:** ejecutar el benchmark con menos repeticiones para que quepa en
el video, y luego mostrar las figuras con los resultados completos.

```bash
python3 bench/benchmark.py --repeticiones 10
```

Mientras corre o sobre las figuras ya generadas, repartir así:

**Juan Esteban — dónde gana MongoDB** (6:45–7:15)
Figura 3 del informe. Ingesta 2,0× más rápida, 2,7× menos disco. Y la frase que resume el
porqué: *MongoDB no es más rápido por ser más moderno; es más rápido porque
promete menos* (`w:1`, sin WAL transaccional). Mostrar también la tercera barra:
con `COPY`, PostgreSQL es 2,7× más rápido que MongoDB — el motor relacional no es
lento en ingesta, lo es por el camino transaccional.

**Jhon Jader — dónde gana PostgreSQL** (7:15–7:55)
Figura 2 del informe, escala logarítmica. Agregación global: 43 ms contra 789 ms, **18,5×**.
Explicar el porqué: ejecución paralela sobre tuplas de ancho fijo frente a
*pipeline* de documentos BSON en un hilo. Y mostrar el resultado de la
transacción en MongoDB:

```
RECHAZADA: Transaction numbers are only allowed on a replica set member or mongos
```

> «MongoDB sí soporta transacciones desde la 4.0, pero exige *replica set*. La
> atomicidad no es gratis: se paga con topología.»

**Laura — las optimizaciones y lo que enseñaron** (7:55–8:30)
Figura 5 del informe. Tres hallazgos, en este orden:

1. El índice que la IA propuso mejoró una consulta 4,5× y **empeoró otra 1,44×**.
   Solo se supo midiendo.
2. En PostgreSQL el planeador **sí adoptó** el índice —mostrar el
   `Parallel Index Only Scan` en `bench/resultados/planes.txt`— y aun así el
   tiempo no mejoró (42,64 → 44,75 ms): con 33 % de selectividad, recorrer
   330.000 claves cuesta lo mismo que el escaneo secuencial paralelo. Decir la
   frase completa: *que el planeador use un índice no prueba que convenga*.
3. La mejora más grande (24×, de 171 ms a 7 ms) no vino de ningún motor, sino de
   reescribir la orquestación del tablero de UCI.

---

## Bloque 5 · Cierre — **Jhon Jader y Juan Esteban** (8:30 – 9:00)

Jhon Jader enuncia los puntos 1 y 2; Juan Esteban, el 3 y el 4, y muestra el
repositorio.

1. La división ACID/BASE se sostiene con datos, no con opiniones.
2. El cuello de botella de una arquitectura híbrida está entre las capas.
3. La IA es buena generando hipótesis y mala como autoridad: una de sus
   propuestas habría degradado el sistema un 44 %. Pero cuando se le pidió
   atacar el borrador terminado, encontró tres errores propios del equipo —entre
   ellos, que la ingesta se estaba midiendo contra una tabla temporal sin WAL.
4. Una línea sobre lo ético: la seudonimización por UUID **no** es anonimización;
   15 horas de signos vitales atadas a un `uuid_admision` son reidentificables, y
   por eso la capa BASE necesita el mismo control de acceso que la ACID.
5. (Juan Esteban) Mostrar el repositorio: `sql/`, `nosql/`, `scripts/`, `bench/resultados/`,
   `docs/`. Mencionar que `scripts/ejecutar_todo.sh` reproduce todo de cero.

---

## Notas de producción

- **Nada de leer diapositivas.** Lo que la rúbrica premia es *ejecución real*:
  que se vean los errores de restricción, el contador de la ingesta y los
  números del benchmark saliendo en la terminal.
- Si una ejecución falla en vivo, no repetirla tres veces: explicar qué debía
  pasar y mostrar la evidencia en `evidencias/*.log`.
- Dejar 20–30 segundos de margen: la rúbrica pide 8–10 minutos y un video de
  10:40 se penaliza.
- Grabar los bloques por separado y unirlos; es más fácil que una toma única de
  nueve minutos con tres personas.
