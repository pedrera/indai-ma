# Benchmark repetible de Procurement

En Streamlit, abre **ProcurementAgent → Comparar benchmarks guardados** para
seleccionar un lote, filtrar casos y estrategias y consultar métricas y respuestas.
**Actualizar historial** recarga los intentos guardados, incluidos los de un lote
en curso. **Descargar informe JSON** exporta solo las ejecuciones filtradas y su
resumen. La vista consulta la base predeterminada; no inicia llamadas LLM.
El selector muestra la fecha de la primera ejecución guardada en UTC y marca
el lote con actividad guardada más reciente. **Ver lote más reciente** permite
seleccionarlo directamente. Las descargas incluyen fecha UTC e identificador
completo de lote en el nombre para distinguirlas de informes anteriores.

El detalle muestra si se aplicó fallback y los códigos de los motivos de
validación, por ejemplo `unsupported_economic_amount`. El JSON incluye
`validation_reasons` en cada ejecución: una lista vacía significa validación sin
incidencias y `null` significa que no se registraron motivos (lotes antiguos o
ejecuciones sin validación final). Los lotes antiguos no se modifican; sus motivos
no pueden reconstruirse a partir de la respuesta final guardada.

La validación distingue una posición neta con signo (`posición neta: -25 GWh`)
de un volumen de déficit, que debe ser positivo (`déficit de 25 GWh`). Las
cantidades explícitas vinculadas a posición, déficit, excedente o volumen a
cubrir se contrastan con la herramienta de posición. Una discrepancia registra
`position_quantity_mismatch`; un déficit negativo registra además
`short_deficit_presented_as_negative`. El historial muestra una explicación en
español junto al código. Esta comprobación usa patrones de redacción acotados;
no constituye una validación semántica completa de cualquier texto libre.

La comprobación de importes conserva el signo y reconoce `€`, `EUR` antes o
después de la cantidad, `euros`, y cantidades expresadas en miles o millones.
Los precios por MWh, GWh o kWh se excluyen de la comparación con la exposición
total. Un total que no coincide con un resultado de exposición ejecutado activa
`unsupported_economic_amount`, también cuando solo difiere en el signo.

El benchmark ejecuta secuencialmente los cinco casos de referencia con las
estrategias existentes. No se activa desde una consulta de Streamlit.
El comando por defecto solo muestra el número de ejecuciones previsto:

```powershell
.\venv\Scripts\python.exe procurement_benchmark.py
```

Para ejecutar tres repeticiones por caso y estrategia (45 ejecuciones):

```powershell
.\venv\Scripts\python.exe procurement_benchmark.py --execute --provider lmstudio --model qwen/qwen3-8b --repetitions 3
```

Se pueden limitar casos y estrategias con `--cases short_spot` y
`--modes deterministic planner_agent`. Cada ejecución puede hacer varias
llamadas LLM; el tiempo y, con OpenAI, el coste dependen del modelo y los límites
configurados en `.env`. El timeout existente se aplica a las llamadas de cada
estrategia, no constituye un límite global del lote. No hay reintentos del lote
ni ejecución paralela. El orden de estrategias rota entre repeticiones.

Cada intento se guarda inmediatamente en
`.indai_ma/comparisons.sqlite3`, excluido de Git. Incluye el registro
`ExecutionComparisonRecord`, fecha UTC, identificador de lote y caso, repetición,
proveedor, modelo, configuración de generación y contadores de calidad.
La base contiene las respuestas generadas; no guarda claves ni errores crudos.
Una interrupción conserva los intentos ya guardados. Los errores de almacenamiento
detienen el lote para no continuar perdiendo mediciones.

El identificador del lote se imprime antes de comenzar. Para consultar los
resultados sin realizar llamadas LLM:

```powershell
.\venv\Scripts\python.exe procurement_benchmark.py --report IDENTIFICADOR_DEL_LOTE
```

`--database RUTA` permite elegir otra base local en ambos comandos.
El informe JSON agrupa por caso, estrategia, proveedor, modelo y configuración.
Incluye completadas, no completadas, planes inválidos, fallbacks, acciones omitidas
y llamadas LLM. Mediana y p95 se calculan solo sobre ejecuciones completadas;
p95 usa rango más próximo (`ceil(0.95 * n)`). Sin completadas se informa `null`.
Los fallbacks cuentan como completadas y además se contabilizan explícitamente.
El estado de completado es el del runner, no una evaluación independiente de
calidad semántica. No se mezclan los tiempos de fallos con los de éxito.

Tres muestras sirven para comprobar el proceso, pero son insuficientes para
conclusiones estadísticas sólidas. Los resultados dependen también de carga,
calentamiento y versión del servidor. Las pruebas automatizadas usan dobles
locales y no constituyen mediciones de rendimiento de un modelo real.
