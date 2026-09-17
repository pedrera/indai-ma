# v0.8.4 — Frontend y experiencia de ejecución

Esta entrega conserva las seis pantallas y la arquitectura de v0.8.3. No incluye
Evaluation, guardrails, nuevos agentes ni historial persistente genérico.

## Identidad de ejecución

`ExecutionView` envuelve el resultado de dominio, sin sustituirlo, con identificador,
modo, estado, configuración efectiva, texto de negocio, herramientas y snapshot.
Al finalizar el job se capturan copias del resultado, configuración y traza.
`GenerationResult.domain_result` permite conservar también `AgentRunResult`.

`execution_views` y `execution_mode_ids` viven solo en la sesión Streamlit.
Cada pantalla resuelve su ejecución. Chat usa la operación de la última respuesta
del asistente. Durante una nueva petición de Chat esa respuesta conserva su
diagnóstico hasta que llegue la siguiente. No se mezclan ambas operaciones.

El recorder global continúa sirviendo a la ejecución activa. Nunca es un fallback
para copiar un resultado cuyo snapshot no existe. Las operaciones de indexación
tienen su propio diagnóstico en Documentación RAG y no sustituyen el snapshot
de los resultados existentes.

## Controles

- Commercial: interpretación **Determinista** por defecto; conserva RAG y cálculos
  sin generar texto con un LLM. La opción **LLM** mantiene el comportamiento anterior.
- Procurement: conserva Deterministic, Planner Agent y ReAct Agent. Una indicación
  aclara que el workflow determinista independiente incluye síntesis LLM.
- Risk: interpretación determinista por defecto y checkbox de priorización LLM.
- Supervisor: routing determinista primero; clasificación LLM solo si hay ambigüedad
  y priorización opcional. Commercial bajo Supervisor sigue siendo determinista.

Proveedor/modelo/runtime continúan siendo globales. Las indicaciones aclaran
cuándo el modelo de generación no afecta a la ejecución. Embeddings y generación
siguen siendo servicios distintos. El interruptor documental se refiere a Chat.

## Presentación y portapapeles

Las cabeceras de especialistas muestran estado, duración y estrategia o
interpretación efectiva. Supervisor añade routing, agentes y síntesis.
El identificador visible permite comprobar la asociación con el inspector.

`business_output.py` contiene formateadores puros. Commercial muestra resumen,
valores con etiquetas de negocio y fuentes legibles. Los textos completos,
similitudes y estructuras permanecen en el expander de evidencia o diagnóstico.
Supervisor reutiliza los renderizadores Commercial/Risk y el texto de Procurement.
No concatena los diccionarios internos de los especialistas.

Las tres acciones de copia se generan desde el mismo `ExecutionView`:

- Copiar respuesta: representación de negocio.
- Copiar diagnóstico: snapshot de esa operación.
- Copiar todo: respuesta y detalles técnicos de esa misma operación.

También funcionan en estados `partial` y `needs_input`. Se reutiliza la redacción
de secretos del portapapeles existente. Las descargas JSON permanecen técnicas.

Las herramientas se muestran bajo un expander; el inspector también comienza
plegado. Supervisor comparte ahora el contenedor de métricas avanzadas existente.
Gas y Chat conservan sus recorridos y modelos de respuesta.

## Benchmark y límites

El benchmark sigue comparando exclusivamente estrategias de Procurement.
Se conservan CLI, SQLite, métricas e historial. No hay botones de benchmark
genérico ni persistencia de `ExecutionView` tras cerrar la sesión.

Los registros de ejecución se conservan en memoria durante la sesión. No se
introduce una pantalla de historial. El diagnóstico del resultado visible es
prioritario; no se sustituye automáticamente por una ejecución ajena más reciente.
Las operaciones síncronas de preparación/indexación mantienen sus limitaciones
previas de cancelación.

## Comprobación manual

1. Ejecutar Risk con demanda 4,8 GWh, suministro 4,3 GWh, spot 42 EUR/MWh y +10 %.
2. Anotar la operación y ejecutar Supervisor con otra consulta.
3. Volver a Risk: cabecera, inspector y portapapeles deben conservar la operación.
4. Indexar un documento: el resultado Risk debe mantener su diagnóstico.
5. Ejecutar Commercial sin modelo de generación, con contrato y embeddings disponibles.
   Debe completar en modo determinista sin llamadas de generación.
6. Verificar las tres estrategias y el historial del benchmark de Procurement.
7. Ejecutar el caso Costa Sur combinado: exceso contractual 0,2 GWh, déficit de
   aprovisionamiento 0,5 GWh/21.000 EUR, estrés 5,28 GWh/41.160 EUR y delta 20.160 EUR.
   El recorrido determinista conserva 0 LLM, 1 RAG y 6 herramientas reales.


## Cierre v0.8.4: Commercial

La omisión intencional de interpretación LLM se registra como `skipped` con
`skip_kind=expected_optional`. No degrada el estado. Sin spot, la condición
relativa del exceso sigue siendo válida; el aviso es informativo salvo que se
solicite un precio absoluto o importe. Los fallos de capacidades requeridas
mantienen `partial`/`needs_input`. El modo LLM conserva su comportamiento.

Las comparaciones explícitas entre contratos identificables usan una recuperación
RAG y extracción determinista independiente por `document_id`. El campo opcional
`CommercialAgentResult.comparison` conserva hechos, evidencia y fuentes por
contrato sin reutilizar cálculos de un contrato para otro. La tabla y el texto
copiable comparten esos datos. La comparación documental no llama al LLM,
incluso si se seleccionó interpretación LLM; el análisis individual conserva esa opción.

La extracción cubre referencia mensual, flexibilidad, límites mensuales, volumen
anual, take-or-pay, mínimo anual y recargo spot en las formas documentales
soportadas. Los límites se extraen de la evidencia; si faltan, pueden derivarse de entradas del mismo contrato. Los hechos derivados llevan origen deterministic_calculation, entradas y fuentes; la tabla los marca como calculados. Los valores explícitos contradictorios no se sustituyen por derivaciones.
Una celda ausente produce un aviso y `partial`; menos de dos contratos identificados
requiere aclaración. No se modifica Top-K: evidencia fuera de los resultados
recuperados no puede presentarse como conocida. No es un motor general de
comparación ni admite todas las formulaciones contractuales.

Validación reproducible: `python -m unittest tests.test_commercial_comparison -q`.
La consulta de diferencias entre Hospital Costa Sur e Industrias Mediterráneo
verifica los ocho hechos por contrato, las fuentes, 0 LLM y una recuperación.

La extracción admite rangos con dos puntos y saltos de línea, límites explícitos, volumen anual de referencia y obligación mínima anual de take-or-pay. Los saltos expected_optional permanecen en el pipeline pero no se presentan como errores. Los avisos de evidencia incompleta sí se muestran.
