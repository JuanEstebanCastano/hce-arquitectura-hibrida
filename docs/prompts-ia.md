# Bitácora de uso de inteligencia artificial

**Proyecto:** Arquitectura híbrida PostgreSQL + MongoDB — Unidad 3, Bases de Datos II
**Herramienta:** Claude (modelo Opus 5), sesión de trabajo del 22 de noviembre de 2026
**Equipo:** Laura ⟨apellidos⟩, Jhon Jader Correa, Juan Esteban Castaño Montoya

Este documento registra qué se le pidió a la IA, qué devolvió, **qué se aceptó y
qué se rechazó**. El criterio del equipo fue tratar cada salida como una
hipótesis que hay que verificar, no como una respuesta. Los casos en que la IA se
equivocó están incluidos deliberadamente: son la parte más instructiva.

---

## Fase 1 — Diseño de la arquitectura

### P1.1 · Reparto de entidades entre las dos capas

> «Para el subsistema de hospitalización de una HCE (red de hospitales públicos
> de Antioquia), define qué entidades deben vivir en PostgreSQL bajo garantías
> ACID y cuáles en MongoDB bajo BASE. Justifica cada decisión con el teorema CAP,
> el volumen esperado y el nivel de consistencia requerido. No me des una regla
> genérica SQL vs NoSQL: dame un criterio que yo pueda aplicar entidad por
> entidad.»

**Qué aportó.** La «regla de las tres preguntas» que se usa en la sección 2.3 del
informe (¿puede dañar a un paciente? ¿participa en una invariante multi-entidad?
¿alguien lo va a actualizar?). Resultó más útil que una lista de entidades,
porque es un criterio reutilizable cuando aparezcan entidades nuevas.

**Qué se rechazó.** La propuesta inicial llevaba `admision_diagnostico` a
MongoDB, argumentando que los diagnósticos son «semiestructurados». El equipo lo
descartó: los diagnósticos de egreso alimentan los RIPS y los indicadores
obligatorios de la red, y necesitan integridad referencial contra el catálogo
CIE-10. Se quedaron en la capa ACID.

**Decisión del equipo.** Criterio adoptado; una de sus conclusiones, rechazada.

---

### P1.2 · La llave puente

> «¿Cómo evito que la identidad del paciente se duplique o se desincronice entre
> PostgreSQL y MongoDB? Quiero una regla que sea difícil de violar por accidente
> desde el código de la aplicación.»

**Qué aportó.** La propuesta de **unidireccionalidad estricta**: PostgreSQL emite
los UUID y MongoDB solo los referencia, reforzada en tres niveles —`DEFAULT
gen_random_uuid()` con restricción `UNIQUE` en la capa ACID, validador
`$jsonSchema` que exige `uuid_admision` en la capa BASE, y resolución de la
identidad en la API antes de aceptar el evento.

**Decisión del equipo.** Adoptada íntegramente. Es la decisión de diseño que más
se repite en el informe.

---

## Fase 2 — Implementación

### P2.1 · Invariantes que una base documental no puede garantizar

> «Necesito garantizar en el motor que una cama no pueda tener dos admisiones
> abiertas al mismo tiempo, y que un paciente no pueda estar hospitalizado dos
> veces simultáneamente en la red. Una restricción UNIQUE normal no sirve porque
> la cama sí se repite entre admisiones cerradas.»

**Qué aportó.** Los índices únicos parciales:

```sql
CREATE UNIQUE INDEX uq_cama_ocupada_una_vez
    ON admision (id_cama) WHERE estado IN ('ABIERTA','EN_TRASLADO');
```

**Por qué importó.** Es el ejemplo más claro del informe sobre qué significa
«ACID» en la práctica: la regla vive en el motor y ninguna ruta de escritura
puede saltársela. El equipo lo convirtió en argumento central de la sección 2.3.

**Decisión del equipo.** Adoptado.

---

### P2.2 · Nivel de validación en MongoDB

> «Dame el validador $jsonSchema para la colección de telemetría de signos
> vitales.»

**Qué devolvió.** Un validador con `validationLevel: "strict"` y
`validationAction: "error"`, con rangos fisiológicos obligatorios para todos los
signos.

**Qué se rechazó y por qué.** El equipo lo cambió a `moderate` / `warn`. El
razonamiento es clínico, no técnico: con `error`, una lectura de SpO₂ fuera de
rango se **descarta**, y una saturación anómala puede ser tanto un sensor mal
puesto como un paciente desaturando. En un flujo de telemetría, perder el dato es
peor que recibirlo imperfecto. Se conservaron como obligatorios únicamente los
campos que hacen al documento trazable (`uuid_admision`, `medido_en`).

**Decisión del equipo.** Modificado. Este cambio aparece justificado en la
sección 3.2 del informe.

---

### P2.3 · Generación de datos realistas

> «Genera telemetría sintética para 560 admisiones monitorizadas que no sea ruido
> uniforme: quiero perfiles fisiológicos distintos y que el estado del paciente
> derive durante la estancia. Tiene que ser determinista para poder cargar
> exactamente lo mismo en los dos motores.»

**Qué aportó.** Los cinco perfiles (estable, séptico, respiratorio, cardíaco,
posquirúrgico) con distribución normal por parámetro y una deriva lineal de
mejoría/deterioro, todo bajo semilla fija.

**Por qué importó.** Sin determinismo, la comparación de la sección 5 no tendría
validez: no se puede atribuir una diferencia de latencia al motor si los datos no
son idénticos.

**Decisión del equipo.** Adoptado.

---

## Fase 3 — Depuración

### P3.1 · `CREATE TEMP TABLE (LIKE ...)` pierde el `DEFAULT`

**Error observado:**

```
psycopg2.errors.NotNullViolation: null value in column "id_lectura"
of relation "_ingesta_tmp" violates not-null constraint
```

> «Creé la tabla temporal con `CREATE TEMP TABLE _ingesta_tmp (LIKE
> hosp.telemetria_signos)` y los INSERT fallan porque id_lectura llega nulo,
> aunque en la tabla original es BIGSERIAL.»

**Diagnóstico de la IA.** `LIKE` copia la estructura pero **no** los valores por
defecto salvo que se indique `INCLUDING DEFAULTS`; el `BIGSERIAL` de la tabla
original es azúcar sintáctico sobre un `DEFAULT nextval(...)` que no se heredó.

**Decisión del equipo.** Se declaró la tabla temporal explícitamente con su
propio `BIGSERIAL`, para no compartir la secuencia con la tabla real durante el
benchmark.

---

### P3.2 · El `%` de SQL contra el marcador de psycopg2

**Error observado:** `IndexError: tuple index out of range` en un `UPDATE` con
operador módulo.

> «Este UPDATE falla con *IndexError: tuple index out of range* y no entiendo por
> qué, si solo le paso un parámetro:
> `cur.execute("UPDATE telemetria_signos SET glucosa_mgdl = 90 + (fc % 60) WHERE uuid_admision = %s", (uuid,))`»

**Diagnóstico de la IA.** psycopg2 interpreta `%` como inicio de un marcador de
parámetro; dentro de una sentencia parametrizada el operador módulo debe
escribirse `%%`.

**Decisión del equipo.** Corregido.

---

## Fase 4 — Optimización (el caso más instructivo)

### P4.1 · Índices de cobertura

> «Estas dos operaciones son las más lentas en MongoDB: el conteo de alarmas en
> una ventana de 6 horas (186 ms) y el top-10 de admisiones por alarmas sobre
> 1.008.000 documentos (789 ms). Sugiere índices que las cubran y explica por qué
> funcionarían.»

**Qué propuso.** Un único índice compuesto `{alarma: 1, medido_en: -1,
uuid_admision: 1}`, argumentando que cubriría ambas consultas porque contiene los
tres campos implicados y permitiría un plan `PROJECTION_COVERED` sin `FETCH` de
documentos.

**Qué pasó al medirlo.**

| Consulta | Antes | Con el índice propuesto | Resultado |
|---|---:|---:|---|
| Alarmas 6 h | 185,72 ms | 41,25 ms | **4,5× mejor** |
| Top-10 por alarmas | 789,17 ms | 1.136,67 ms | **1,44× PEOR** |

El argumento era plausible y resultó medio equivocado. El índice ordena por
`medido_en`, lo cual sirve a la consulta que filtra por rango de tiempo y
perjudica a la que agrupa por admisión: el motor recorre un índice ordenado por
tiempo para construir grupos por otra clave, y eso es más caro que leer la
colección de corrido.

**Segunda iteración.** Con el diagnóstico en la mano se probó
`{alarma: 1, uuid_admision: 1}` —ordenado por la clave de agrupación—:
**563,60 ms**, 1,40× mejor que el punto de partida, con plan
`IXSCAN` + `PROJECTION_COVERED` confirmado por `explain()`.

**Decisión del equipo.** Se conservan los dos índices, cada uno para su consulta.
La lección se incorporó al informe: *una sugerencia de índice es una hipótesis
hasta que el plan de ejecución la confirma.*

---

### P4.2 · El índice que PostgreSQL sí usó y que aun así no sirvió

> «Agrega el índice equivalente en PostgreSQL y verifica el plan.»

**Qué pasó.** El índice parcial `(medido_en DESC, uuid_admision) WHERE alarma` se
creó sin error y el planeador **lo adoptó**: el `EXPLAIN ANALYZE` guardado en
`bench/resultados/planes.txt` muestra
`Parallel Index Only Scan using ix_tel_cobertura_alarmas` con 38 `Heap Fetches`,
es decir, una consulta cubierta por el índice, que es exactamente lo que la IA
había previsto.

**Y aun así no sirvió.** El tiempo pasó de 42,64 ms a 44,75 ms: ninguna ganancia.
La causa es la selectividad: el 33 % de las filas cumple `alarma`, de modo que
recorrer ~330.000 claves del índice cuesta lo mismo que el `Parallel Seq Scan`
sobre una tabla de 94 MB que el planeador venía usando.

**Por qué este caso enseña más que el anterior.** La lección fácil habría sido
«el planeador descarta los índices malos, confíen en él». Lo que se midió dice
algo más incómodo: el planeador puede **preferir** un índice por costo estimado y
aun así no hacer ganar tiempo. Un `EXPLAIN` que muestra el índice en uso no es
prueba de que el índice convenga; hace falta el cronómetro.

**Decisión del equipo.** El índice quedó **comentado** en `sql/03_indices.sql`,
con la explicación. Un índice que no reduce el tiempo sigue costando 18 MB y
penalizando cada una de las 1,6 millones de escrituras diarias.

### P4.3 · El hallazgo que no era un índice

> «El tablero de UCI tarda 171 ms y solo muestra 20 camas. ¿Dónde se va el
> tiempo?»

**Qué aportó.** El diagnóstico de que el costo no estaba en ninguno de los dos
motores sino en **cómo se combinaban**: un `$match` + `$sort` + `$group` sobre
decenas de miles de documentos para obtener 20 valores. La alternativa —20
búsquedas `find_one` con `sort`, cada una resuelta por el índice
`{uuid_admision, medido_en}`— cambia muchos documentos por muchos viajes de red.

**Resultado medido.** 170,92 ms → **7,12 ms** (24,0× mejor).

**Decisión del equipo.** Adoptado. Es la mejora más grande del trabajo y la única
que no vino de tocar una base de datos.

---

## Fase 5 — Documentación

### P5.1 · Estructura del informe

> «Organiza el informe técnico de modo que cada criterio de la rúbrica tenga una
> sección identificable, y que el análisis cuantitativo se apoye solo en números
> medidos, nunca en estimaciones.»

**Qué aportó.** La estructura de siete secciones del informe y la decisión de
reportar p50/p95/p99 en lugar del promedio.

**Qué verificó el equipo.** Cada cifra citada en el informe se contrastó contra
`bench/resultados/metricas.json`. Las afirmaciones sobre normativa colombiana se
marcaron explícitamente como marco de referencia académico sujeto a verificación
de vigencia, por no tener el equipo competencia jurídica para afirmarlas.

---

### P5.2 · Revisión crítica del borrador (el prompt más rentable del trabajo)

> «Revisa esta entrega como un evaluador exigente. Verifica que toda cifra citada
> coincida con `metricas.json`, rehaz los cocientes, y señala afirmaciones del
> informe que NO estén respaldadas por evidencia del repositorio. No me elogies
> nada: solo los problemas, con archivo y línea.»

**Qué encontró.** Tres errores de fondo que el equipo no había visto:

1. **OP-1 se estaba midiendo contra una tabla temporal de PostgreSQL**, que no
   escribe WAL, mientras MongoDB escribía en una colección normal. La
   comparación favorecía indebidamente al motor relacional en el eje donde se
   suponía que perdía, y la explicación del informe («PostgreSQL debe escribir el
   WAL») describía algo que en esa medición no estaba ocurriendo.
2. **La afirmación «PostgreSQL descartó el índice» contradecía el plan guardado**
   en el propio repositorio, que mostraba `Parallel Index Only Scan`.
3. El informe afirmaba que el validador de MongoDB «rechaza en origen» los
   documentos sin llave puente, cuando está configurado como `warn` y solo
   advierte; y describía una cola de eventos huérfanos que no existe en el
   código.

**Decisión del equipo.** Se rehízo el benchmark de ingesta sobre una tabla
permanente (las cifras del informe son las de esa segunda medición), se reescribió
el análisis de OP-9 y se añadieron a la sección 2.4 dos párrafos que delimitan
qué está implementado y qué es diseño pendiente.

**Por qué se registra.** Es el único prompt de la bitácora cuyo valor estuvo en
*contradecir* trabajo ya hecho. El equipo lo considera el más rentable de todos.

---

## Balance del uso de IA

| | |
|---|---|
| Prompts registrados y transcritos | 12 en 5 fases |
| Sugerencias adoptadas sin cambios | 5 (P1.1 criterio, P1.2, P2.1, P2.3, P4.3) |
| Sugerencias adoptadas con modificación | 2 (P2.2 validador, P4.1 segundo índice) |
| Sugerencias rechazadas | 2 (P1.1 ubicación de `admision_diagnostico`, P4.2 índice) |
| Sugerencias contraproducentes al medirlas | 1 (P4.1, índice v1: 44 % más lento) |
| Errores de implementación diagnosticados por IA | 2 (P3.1, P3.2) |
| Errores del propio equipo detectados por IA | 3 (P5.2) |

**Conclusión del equipo.** La IA aceleró el diseño y la depuración, y produjo dos
ideas que el equipo no tenía: la regla de las tres preguntas y la reorquestación
del tablero. También produjo una recomendación que habría degradado el sistema un
44 % si se hubiera aceptado sin medir, y —en el otro sentido— encontró tres
errores en el trabajo ya terminado cuando se le pidió explícitamente que lo
atacara.

El aprendizaje transferible no es «usar IA», sino **medir todo lo que la IA
proponga antes de llevarlo a producción, y pedirle después que ataque el
resultado**. En un sistema que toca la atención de pacientes, esa verificación
debería ser obligatoria y no opcional.
