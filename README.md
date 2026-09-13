# indAI MA

## v0.8.3: Multi-Agent Supervisor

Orquestación secuencial de CommercialAgent, ProcurementAgent y RiskAgent con
routing determinista y una clasificación LLM acotada solo para consultas
ambiguas. Conserva resultados parciales, fuentes y modelos de cada especialista.
La composición es determinista por defecto y las métricas distinguen herramientas
ejecutadas de resultados reutilizados. Los modos independientes siguen disponibles.
Véase [Supervisor: arquitectura, métricas y prueba manual](docs/supervisor.md).

## v0.8.2: RiskAgent

Modo independiente para comparar exposición base y escenarios de demanda,
con cálculos deterministas, deltas y trazas por herramienta. Funciona sin LLM
por defecto y permite priorizar los hallazgos con una única generación opcional.
No utiliza RAG ni otros agentes. Consulta la
[arquitectura y prueba manual de RiskAgent](docs/risk-agent.md).

## v0.8: CommercialAgent

Disponible como segundo agente especializado para análisis comercial y
contractual, reutilizando el RAG local, las herramientas deterministas y
Pipeline Inspector. Presenta hechos con evidencia, exceso contractual, margen
cuando procede y resultados estructurados. Véase
[CommercialAgent](docs/commercial-agent.md) para arquitectura, prueba Costa Sur
y límites de esta entrega.

La base v0.7.0 fase 1 es un asistente web B2B multigás para los sectores
sanitario e industrial. Mantiene el análisis determinista, Chat y RAG de
v0.6.0, y añade las bases explícitas para comparar orquestación determinista,
Planner y ReAct sin incorporar todavía un framework de agentes.

## v0.7.0 fase 1: ProcurementAgent

El área **ProcurementAgent** ofrece tres estrategias sobre las mismas tools:

- **Deterministic**: la aplicación selecciona y ejecuta el workflow conocido;
  el LLM sólo realiza la síntesis final.
- **Planner Agent**: el LLM genera primero un plan Pydantic completo; el plan se
  valida antes de ejecutar sus acciones secuencialmente.
- **ReAct Agent**: el LLM decide la siguiente acción después de cada observación
  hasta finalizar o alcanzar sus límites.

Las tres estrategias reutilizan `calculate_supply_position`,
`calculate_spot_exposure` y `calculate_demand_scenario` mediante un registro
acotado. Incluyen validación de schemas y procedencia, prevención de duplicados,
presupuestos de decisiones/tools, comprobación de evidencia antes de finalizar
y fallback determinista cuando la redacción contradice resultados observables.

Pipeline Inspector registra decisiones operativas, planes, acciones,
observaciones, tools omitidas, llamadas LLM y validación final sin exponer
chain-of-thought. Los botones **Copy response**, **Copy diagnostics** y
**Copy all** trabajan exclusivamente con el estado disponible de la operación.

El workflow **Gas B2B Portfolio Analysis** continúa siendo independiente y no
ha sido sustituido. Consulta los resultados exploratorios en
[`docs/v0.7.0-phase-1-benchmark.md`](docs/v0.7.0-phase-1-benchmark.md) y el
alcance de la entrega en
[`RELEASE_NOTES_v0.7.0.md`](RELEASE_NOTES_v0.7.0.md).

El [benchmark repetible](docs/procurement-benchmark.md) permite guardar
comparaciones en SQLite y consultar medianas, p95, fallos y fallbacks por lote.
Se ejecuta explícitamente por CLI; el comando por defecto no realiza llamadas LLM.

## Requisitos

- Python 3.10 o superior
- Una API key de OpenAI o un servidor local de LM Studio

## Instalación

1. Crea y activa un entorno virtual:

   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

2. Instala las dependencias:

   ```powershell
   pip install -r requirements.txt
   ```

3. Copia `.env.example` como `.env` y configura el proveedor.

   Para OpenAI:

   ```env
   LLM_PROVIDER=openai
   OPENAI_API_KEY=your_openai_api_key
   OPENAI_MODEL=gpt-4o-mini
   ```

   Para LM Studio, inicia su servidor local con el modelo cargado y utiliza:

   ```env
   LLM_PROVIDER=lmstudio
   LMSTUDIO_BASE_URL=http://localhost:1234/v1
   LMSTUDIO_MODEL=deepseek/deepseek-r1-0528-qwen3-8b
   RAG_EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5
   RAG_TOP_K=4
   RAG_INDEX_PATH=.indai_ma/rag_index
   ```

La sidebar permite cambiar el proveedor y el modelo durante la sesión sin modificar `.env`. Para LM Studio, los modelos de chat se consultan dinámicamente al servidor local y se excluyen los modelos de embeddings. Para OpenAI se muestra por ahora el modelo configurado en `OPENAI_MODEL`.

La interfaz de usuario obtiene la implementación configurada sin depender directamente del proveedor.

## Modos

- **Chat**: conversación con memoria durante la sesión activa.
- **Gas B2B Portfolio Analysis**: genera y valida con Pydantic un análisis estructurado de demanda, suministro, posiciones cortas y riesgos comerciales para gas natural, biometano, GNL y mezclas de hidrógeno.
- **ProcurementAgent**: permite seleccionar Deterministic, Planner Agent o
  ReAct Agent para comparar la orquestación con la misma entrada empresarial.

El modo de análisis solicita JSON explícito al modelo y valida la respuesta antes de mostrarla. Una respuesta inválida genera un mensaje controlado y no detiene la aplicación.

Las generaciones se ejecutan fuera del hilo de interfaz. Mientras el modelo trabaja, la aplicación muestra su estado y permite interrumpir realmente la petición con **Detener generación**. El timeout total se configura en `.env`:

```env
LLM_TIMEOUT_SECONDS=300
```

La configuración avanzada de la sidebar permite sobrescribir durante la sesión
los límites `LLM_MAX_OUTPUT_TOKENS` y `LLM_MAX_TOKENS`, el timeout y el modo
thinking. Estos cambios no modifican `.env` y se conservan al iniciar una nueva
conversación dentro de la misma sesión.

Una cancelación o timeout conserva el historial anterior y no añade una respuesta incompleta.

## Tools de negocio

Las tools deterministas calculan la posición de suministro (`calculate_supply_position`), margen unitario y total (`calculate_margin`), escenarios porcentuales de demanda (`calculate_demand_scenario`) y exposición económica de una posición corta al mercado spot (`calculate_spot_exposure`). En Chat, el modelo decide cuáles necesita y conserva sus protecciones actuales contra bucles. Gas B2B Portfolio Analysis genera los escenarios base, +10 % y +20 %, ejecuta primero todos los cálculos conocidos y realiza después una única llamada al LLM sin tool calling dinámico. La interfaz presenta la comparación y el Pipeline Inspector muestra cada etapa y las ejecuciones agregadas por tool.

El proveedor y el modelo seleccionados deben soportar function calling. Si LM Studio rechaza las tools, la interfaz muestra un error controlado para seleccionar un modelo compatible.

## RAG local

La versión 0.6.0 permite indexar documentos `.txt` y `.pdf` desde el bloque
**Documentación RAG** de la sidebar. La extracción, el chunking, los embeddings
y la búsqueda se ejecutan localmente. Los embeddings se solicitan a LM Studio
mediante `text-embedding-nomic-embed-text-v1.5`; OpenAI no se utiliza para esa
operación.

El índice se guarda por defecto en `.indai_ma/rag_index`, resuelto respecto a
la raíz de la aplicación, y contiene `manifest.json`, `documents.json` y
`chunks.jsonl`. Se excluye de Git y se recarga desde disco al iniciar o ejecutar
de nuevo Streamlit. Para colecciones pequeñas, la búsqueda utiliza cosine
similarity sin una base vectorial externa. Cada respuesta RAG muestra los
documentos, secciones, páginas y fragmentos recuperados.

El contenido documental se trata como datos no confiables y nunca como
instrucciones del sistema. Los cálculos de negocio continúan ejecutándose con
las tools Python. Los PDF escaneados sin capa de texto no son compatibles porque
esta versión no incluye OCR.

Cuando Scenario Analysis no encuentra un tipo de gas explícito en la consulta,
puede resolverlo de forma determinista desde los chunks ya recuperados. La
consulta del usuario tiene prioridad, pero una discrepancia entre usuario y
documentación se rechaza como conflicto en lugar de asumir un valor.

Gas B2B Portfolio Analysis distingue de forma determinista las consultas
documentales de las cuantitativas. Las preguntas sobre cláusulas, flexibilidad,
take-or-pay o condiciones contractuales pasan directamente de la recuperación
RAG a una única interpretación del LLM, sin parsing de cartera ni tools. Solo
las consultas con intención de cálculo y evidencia numérica suficiente activan
el workflow cuantitativo existente.

## Ejecución

```powershell
streamlit run app.py
```

La aplicación conserva el historial conversacional en la sesión activa de
Streamlit. El botón **Nueva conversación** limpia solo ese historial y no afecta
al índice RAG persistente. No se utiliza una base de datos externa.
