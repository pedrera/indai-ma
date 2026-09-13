# CommercialAgent — v0.8

Segundo agente especializado, independiente de ProcurementAgent. Disponible
en el selector de modos de Streamlit. No incorpora Supervisor, RiskAgent,
llamadas entre agentes ni frameworks nuevos.

## Arquitectura y decisiones

1. Recupera hasta doce fragmentos usando `RAGService.retrieve`, embeddings e
   índice existentes. Amplía la consulta con términos comerciales, sin insertar
   valores ni condiciones de un cliente concreto.
2. Delimita un contrato por cliente o nombre de documento. Si la evidencia
   recuperada no permite identificar uno inequívocamente, pide información.
3. Extrae referencia mensual, flexibilidad y recargo con
   `extract_contractual_volume_terms`. Añade extracción acotada de volumen anual,
   take-or-pay y precio base cuando constan literalmente en los fragmentos.
4. Reutiliza `calculate_contractual_volume_impact` para el exceso y calcula el
   límite inferior con la misma referencia y porcentaje. Las entradas conservan
   procedencia; los resultados se etiquetan como cálculos deterministas.
5. Decide calcular margen solo ante una petición de margen, rentabilidad o
   beneficio y con los tres parámetros disponibles. Usa
   `LocalToolRegistry(("calculate_margin",))`. El coste debe estar explícito en
   la consulta o en evidencia contractual inequívoca. No equipara recargo comercial y margen ni extiende el precio base
   del contrato al exceso. Puede usar un precio de venta explícito del usuario.
6. Realiza como máximo una llamada de generación. El modelo selecciona números
   de fuente con `CommercialEvidencePlan`; se valida su existencia y la aplicación
   construye `CommercialInterpretation` usando exclusivamente el texto original.
   Se presenta el fragmento completo para conservar sus
   condiciones. El modelo no aporta aritmética ni cifras al resultado calculado.

Cuando hay exceso, una política de completitud añade siempre las cláusulas de
flexibilidad y recargo que respaldan el cálculo, aunque el modelo no las seleccione.

Una búsqueda de embeddings no se contabiliza como llamada LLM de generación.
La selección de evidencia es la decisión acotada del modelo; recuperación y
elegibilidad de cálculos se controlan por políticas de la aplicación.

## Resultado y fuentes

`CommercialAgentResult` incluye estado, cliente, hechos, hallazgos, cálculos,
advertencias, fuentes, herramientas y resumen. `AgentJob` conserva su estructura
en `GenerationResult.structured_result`. Los agentes anteriores siguen usando
su contrato habitual.

Cada hecho conserva evidencia y documento/sección/páginas/similitud. Las citas
son etiquetas amigables; los IDs de fragmento no forman parte del resultado.
El resumen de cifras es determinista. La pantalla y la descarga JSON distinguen
hechos documentales y resultados calculados. Copy response y Copy all reutilizan
la infraestructura de portapapeles y operación existente.

Pipeline Inspector muestra inicio, recuperación, decisiones operativas,
herramientas, interpretación, resultado estructurado, advertencias y estado
final. Los timings LLM provienen del proveedor compartido, sin chain-of-thought.

## Prueba Hospital Costa Sur

Con el contrato indexado, seleccionar CommercialAgent e introducir:

> El Hospital Costa Sur prevé consumir 4,8 GWh el próximo mes.
> Analiza las implicaciones comerciales según su contrato.

Comprobar referencia de 4 GWh, flexibilidad del 15 %, rango calculado 3,4–4,6 GWh,
exceso de 0,2 GWh y condición Spot + 4 EUR/MWh con fuente. La falta de un spot
vigente se comunica; no impide presentar el exceso ni la condición contractual.
Los valores se extraen del contrato, no de constantes del agente.

Las pruebas usan una copia de los fragmentos del contrato local como fixture;
incluyen también ingesta y búsqueda con el RAG real y embeddings de prueba.

La comprobación local del 13 de septiembre de 2026 con Qwen3 8B (thinking
desactivado, máximo 128 tokens) recuperó el contrato y produjo el rango y exceso
esperados con una llamada de generación, en aproximadamente 158 segundos.
La selección del modelo fue incompleta; la política de completitud de evidencia
se añadió después y se verificó con una prueba de regresión. La latencia es una
medición exploratoria, no una garantía de rendimiento.

## Límites de esta entrega

- La extracción numérica reconoce patrones acotados en español. Datos ausentes
  o ambiguos no se completan por el modelo.
- Se analiza un contrato por operación; no se resuelven versiones contractuales
  ni periodos de vigencia entre documentos diferentes.
- La interpretación consiste en seleccionar cláusulas respaldadas por evidencia,
  no en generar asesoramiento contractual libre. Las condiciones de revisión,
  disponibilidad y take-or-pay se presentan como evidencia literal.
- El alcance de la recuperación puede omitir cláusulas; el usuario puede revisar
  las fuentes consultadas. No se garantiza exhaustividad de todo el contrato.
- No se calcula un margen por tramos con precios distintos. El volumen anual no
  se compara con el límite mensual de flexibilidad.
- Una respuesta LLM inválida conserva los hechos y cálculos y marca estado parcial.
  Sin contrato no se llama al LLM; un fallo RAG genera un resultado fallido.
- La cancelación reutiliza AgentJob y el proveedor; una petición de embeddings
  que ya esté en curso termina según su propio timeout antes de continuar.
