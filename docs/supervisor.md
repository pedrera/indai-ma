# v0.8.3 — Supervisor y routing de agentes

El modo **Multi-Agent Supervisor** selecciona los especialistas necesarios,
los ejecuta secuencialmente y compone una respuesta con resultados verificables.
No introduce cálculos de negocio, agentes adicionales ni un framework externo.
Los modos independientes siguen disponibles.

## Arquitectura y contratos

```text
Consulta → Supervisor → clasificación determinista
                         └─ ambigua: router LLM (máximo una llamada)
                     → CommercialAgent, si se solicita contrato
                     → ProcurementAgent, si se solicita posición actual
                     → RiskAgent, si se solicitan escenarios
                     → recopilación → composición → resultado
```

El orden es siempre Commercial → Procurement → Risk, omitiendo los no seleccionados.
Los especialistas no se importan ni se llaman entre sí. El Supervisor recibe los
modelos existentes: `CommercialAgentResult`, `AgentRunResult` de Procurement y
`RiskAgentResult`. `SpecialistExecutionResult` los envuelve con nombre, estado,
duración, error controlado, advertencias y contadores. Una validación comprueba
que el tipo del resultado corresponda al especialista.

Procurement usa `ProcurementDeterministicWorkflow` con el renderizador canónico
existente como adaptador de síntesis, sin LLM. No se cambia el comportamiento de
sus modos independientes ReAct, Planner o Deterministic. Supervisor configura Commercial explícitamente con `interpretation_mode="deterministic"`: mantiene
su recuperación RAG, hechos, citas y cálculos, pero no crea un proveedor de generación.
El modo independiente de Commercial conserva `interpretation_mode="llm"` como opción. Risk mantiene cero RAG y
cero generaciones dentro del Supervisor.

Se reutilizan `classify_gas_query`, sus señales documentales,
`extract_procurement_context` y `extract_demand_scenarios`. El clasificador previo
documentary/quantitative no basta por sí solo para distinguir Procurement de Risk.
El routing añade intención explícita de posición actual y de estrés.

La corrección compartida del parser reconoce `4,8 GWh de demanda` sin confundir
el suministro que aparece después con la demanda. Está cubierta por regresión.
No se infieren unidades monetarias: escribe `42 EUR/MWh`, no solo `spot 42`.

## Routing

| Consulta | Selección |
|---|---|
| Flexibilidad contractual de Hospital Costa Sur | Commercial |
| Demanda y suministro; cuál es nuestra posición | Procurement |
| Demanda y suministro; qué ocurre si la demanda sube un 10 % | Risk |
| Contrato y posición | Commercial, Procurement |
| Posición y escenario | Procurement, Risk |
| Contrato y escenario | Commercial, Risk |
| Contrato, posición y escenario | Los tres |

Los datos numéricos necesarios para Risk no activan Procurement automáticamente.
`Suministro contratado` es un dato de suministro, no una petición contractual.
La evidencia del routing son categorías observables; no se solicita ni muestra
razonamiento interno. Una consulta de dominio sin intención clara puede usar un
router LLM. Su salida solo admite nombres conocidos y únicos. Una respuesta vacía,
inválida o un router no disponible lleva a una aclaración sin invocaciones aleatorias.
Una consulta ajena al dominio no llama al router.

Las reglas cubren los patrones documentados; no constituyen un intérprete general
de cualquier construcción lingüística. El panel permite revisar qué se seleccionó.

## Reutilización y límites

Cada operación tiene una caché privada de herramientas, indexada por nombre y
argumentos validados. Nunca persiste entre consultas. Las copias de resultados
evitan que un especialista modifique lo recibido por otro. Esto es reutilización
del servicio de herramientas, no comunicación entre agentes.

Cuando Procurement y Risk están seleccionados, el Supervisor adapta la petición
de Procurement a demanda, suministro y spot inequívocos. Procurement vuelve a
validar esa petición y Risk conserva la consulta original y sus escenarios.
Los cálculos de base que Risk solicita pueden reutilizar los de Procurement.
Si los datos no son inequívocos, se conserva la consulta original para que el
especialista responda con su validación habitual.

La composición conserva las secciones contractual, aprovisionamiento, estrés e
implicaciones conjuntas. No suma ni recalcula las magnitudes. En particular,
**exceso contractual y déficit de aprovisionamiento son conceptos diferentes**.

Una opción de UI permite una llamada adicional de síntesis para priorizar las
secciones relevantes. Su JSON solo selecciona claves existentes; la respuesta
conserva todo el contenido canónico, cifras y fuentes. No se admite prosa numérica
libre del modelo. Si falla, se conserva la composición determinista.

| Caso claro | Router LLM | LLM especialistas | Síntesis Supervisor por defecto |
|---|---:|---:|---:|
| Commercial solo | 0 | 0 | 0 |
| Procurement solo | 0 | 0 | 0 |
| Risk solo | 0 | 0 | 0 |
| Combinado completo | 0 | 0 | 0 |

Una consulta ambigua añade como máximo una generación de routing. Activar la
priorización añade como máximo una generación de síntesis. Embeddings se cuentan
en la recuperación, no como generaciones LLM. No hay paralelismo ni bucles.

## Fallos y cancelación

Un fallo de especialista se registra y no elimina los resultados anteriores ni
impide ejecutar otros especialistas dentro del tiempo disponible. El estado final
es `partial` si queda información útil, `failed` si todos fallan o `needs_input`
si falta información para responder. Los errores públicos son controlados.
No se añaden implicaciones de un especialista fallido como si hubiera respondido.

El presupuesto temporal se comparte secuencialmente. La cancelación detiene la
generación activa y se comprueba entre especialistas. Las operaciones síncronas
de recuperación ya existentes deben retornar antes de que el hilo pueda terminar;
no se inicia el siguiente especialista después de una cancelación.

## Prueba manual exacta

Arranca Streamlit desde el directorio del proyecto, con el entorno existente:

```powershell
Set-Location C:\Users\pedre\IdeaProjects\indai-ma
.\venv\Scripts\python.exe -m streamlit run app.py
```

En **Documentación RAG**, indexa el contrato de Hospital Costa Sur usado en la
prueba comercial: referencia mensual 4 GWh, flexibilidad ±15 %, exceso Spot + 4
EUR/MWh. No se requiere un modelo de generación para Commercial desde Supervisor; el modelo
de embeddings sí debe estar disponible. Mantén desmarcada
la priorización LLM del Supervisor. Pega en **Multi-Agent Supervisor**:

```text
El Hospital Costa Sur prevé consumir 4,8 GWh el próximo mes.
Tenemos 4,3 GWh aprovisionados y el precio spot actual es de 42 EUR/MWh.

Analiza:
- las condiciones relevantes de su contrato;
- nuestra posición de aprovisionamiento;
- el coste de cubrir cualquier déficit;
- y qué ocurriría si la demanda aumentase un 10 %.
```

Routing esperado: determinista, CommercialAgent → ProcurementAgent → RiskAgent;
ningún especialista omitido.

| Área | Resultado |
|---|---|
| Contrato | Referencia 4 GWh; ±15 %; rango 3,4–4,6 GWh |
| Exceso contractual | 0,2 GWh; precio Spot + 4 EUR/MWh; fuentes legibles |
| Aprovisionamiento | Demanda 4,8; suministro 4,3; posición −0,5 GWh, SHORT |
| Cobertura spot | 0,5 GWh / 500 MWh; 21.000 EUR |
| Estrés +10 % | Demanda 5,28; posición −0,98 GWh, SHORT; 41.160 EUR |
| Delta | +0,48 GWh descubiertos; +20.160 EUR de exposición |

## Pipeline Inspector y diagnósticos

```text
supervisor_start
input_parsing
routing (método, evidencia, seleccionados y omitidos)
specialist_execution: CommercialAgent
  RAG → cálculo contractual → commercial_interpretation (skipped) → resultado
specialist_execution: ProcurementAgent
  posición → exposición → respuesta canónica validada
specialist_execution: RiskAgent
  base (2 resultados reutilizados)
  escenario (+10 %): demanda → posición → exposición
  delta → resultado
result_collection
supervisor_synthesis (determinista; 0 LLM)
supervisor_result
supervisor_final
```

Las etapas de cada especialista conservan propietario, argumentos, resultados,
duraciones y estados. `Copy diagnostics` incluye **Supervisor Execution**, además
de las métricas comunes; `Copy response`, `Copy all` y JSON no generan llamadas.

Contadores del caso completo, con contrato y modelo disponibles:

| Propietario | Generaciones LLM | Recuperaciones RAG | Herramientas reales | Reutilizaciones |
|---|---:|---:|---:|---:|
| Commercial | 0 | 1 | 1 cálculo contractual | 0 |
| Procurement | 0 | 0 | 2 | 0 |
| Risk | 0 | 0 | 3 | 2 |
| Supervisor | 0 | 0 | 0 | 0 |
| Total | 0 | 1 | 6 | 2 |

Los resultados de especialistas conservan ocho observaciones de herramienta;
solo seis corresponden a ejecuciones reales. Las dos restantes tienen duración
de herramienta cero y `reused_result` en la traza. Los fallos de ejecución también
se cuentan como intentos de herramienta; los saltos no cuentan.

El tiempo total se mide con reloj independiente, tanto en el resultado como en
el recorder de la operación. No se obtiene sumando duraciones de agentes y
herramientas: están anidadas. El snapshot de UI incluye además la finalización
y puede ser ligeramente mayor que el tiempo del resultado del runner.

## Archivos y validación

- Nuevos: `supervisor.py`, `supervisor_models.py`, `supervisor_routing.py`,
  `supervisor_ui.py`, `tests/test_supervisor.py`, `docs/supervisor.md`.
- Integración: `app.py`, `pipeline_inspector.py`, `clipboard_text.py`, README.
- Corrección compartida: `gas_analysis.py` para demanda expresada tras el valor.
- No se modifican los módulos de los especialistas.

Las pruebas incluyen las siete combinaciones de routing, ambigüedad y consultas
no soportadas, agentes omitidos, fallo parcial, cancelación, cache por operación,
llamadas opcionales, serialización de modelos, cifras exactas, métricas, fuentes
y arranque del modo Streamlit sin modelo. El caso completo usa especialistas
reales y RAG local temporal con embeddings y generación simulados; no sustituye
la validación manual del modelo local y el contrato realmente indexado.

La ejecución comercial determinista conserva todos los fragmentos del contrato seleccionado
como evidencia literal con fuentes legibles. `CommercialAgentResult.interpretation_mode`
y los diagnósticos identifican el modo. La etapa `commercial_interpretation` queda
`skipped`; no se registra ninguna generación LLM comercial.
