# Benchmark repetible de Procurement

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
