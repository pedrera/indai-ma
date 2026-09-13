# v0.8.2 — RiskAgent

RiskAgent analiza exposición y escenarios de demanda como modo independiente.
No recupera contratos ni utiliza RAG, no llama a otros agentes y no incorpora
Supervisor ni frameworks externos. ProcurementAgent y CommercialAgent conservan
sus implementaciones y pruebas.

## Flujo y reutilización

1. `parse_scenario_input` extrae demanda, suministro y spot. Se reutiliza
   `extract_demand_scenarios(..., use_defaults=False, strict=True)` para tomar
   únicamente los porcentajes explícitos. El parser compartido se amplía con
   «aumentase», «disminuyese» y listas como `escenarios +10 % y +20 %`.
2. `RiskInputs` valida valores finitos, no negativos y porcentajes desde -100 %.
   El límite de veinte escenarios es operativo, no una política de riesgo.
   Un dato ausente o ambiguo produce `needs_input`; un porcentaje inválido produce
   `failed`, sin calcular escenarios con valores supuestos.
3. La base usa `calculate_supply_position` y, si hay spot,
   `calculate_spot_exposure`. Cada estrés añade `calculate_demand_scenario`
   antes de las otras dos herramientas. Todas pasan por `LocalToolRegistry`.
4. `risk_calculations.py` deriva unidades, diferencias respecto a la base y
   textos factuales. No vuelve a implementar las fórmulas de demanda, posición
   ni coste spot. Sin spot, el volumen descubierto se deriva de la posición
   negativa; los importes y sus deltas permanecen en `None`.
5. Por defecto termina sin LLM. La opción LLM realiza **una sola generación**
   después de todos los escenarios. El modelo devuelve una prioridad de IDs de
   hallazgos mediante `RiskInterpretation`. Se rechazan IDs desconocidos,
   duplicados y campos adicionales. Los hallazgos no seleccionados se conservan.
   No se acepta texto libre del modelo que pueda introducir cifras, políticas
   de cobertura, límites, apetito de riesgo o razonamiento interno.
6. `RiskAgentResult` conserva base, estrés, deltas, hallazgos, advertencias,
   herramientas y modo de interpretación. `AgentJob` admite proveedor opcional
   para transportar el resultado estructurado también en ejecución determinista.

La dirección de los hallazgos compara la cobertura con la base; no clasifica
una exposición como aceptable/inaceptable ni define umbrales de materialidad.
Se mantienen suministro y spot constantes entre escenarios. No se calcula margen,
VaR, Monte Carlo ni optimización, y no se ejecutan operaciones de mercado.

Los resultados visibles redondean las cantidades a doce decimales y los importes
a ocho para eliminar residuos binarios de las herramientas compartidas. Las
trazas conservan sus resultados originales. Un fallo de síntesis conserva el
resultado determinista con advertencia. Una cancelación de sesión usa el contrato
existente de AgentJob.

## Prueba manual exacta

Selecciona **RiskAgent**, deja desmarcado **Priorizar hallazgos con LLM** y pulsa
**Analizar riesgo** con este texto:

```text
El Hospital Costa Sur prevé consumir 4,8 GWh el próximo mes.
Tenemos 4,3 GWh aprovisionados y el precio spot es de 42 EUR/MWh.

Analiza nuestra exposición actual y qué ocurriría si la demanda
aumentase un 10 %.
```

| Magnitud | Base | Demanda +10 % | Cambio |
|---|---:|---:|---:|
| Demanda (GWh) | 4,8 | 5,28 | +0,48 |
| Suministro (GWh) | 4,3 | 4,3 | 0 |
| Posición (GWh) | -0,5 SHORT | -0,98 SHORT | -0,48 |
| Volumen descubierto (GWh) | 0,5 | 0,98 | +0,48 |
| Volumen descubierto (MWh) | 500 | 980 | +480 |
| Exposición spot (EUR) | 21.000 | 41.160 | +20.160 |

Prueba también `escenarios +10 % y +20 %`, `escenario -10 %`, una consulta sin
escenarios, y el texto anterior sin precio spot. Sin spot se muestran posiciones
y volúmenes, y los importes aparecen como no disponibles. Para comprobar la
opción de interpretación, activa la casilla con un proveedor/modelo configurado:
el número máximo de generaciones sigue siendo uno, aunque haya varios escenarios.

## Pipeline Inspector y portapapeles

Para el caso de referencia, el orden observable es:

```text
RiskAgent / agent_start
input_parsing
Base
  calculate_supply_position
  calculate_spot_exposure
Demanda +10 %
  calculate_demand_scenario
  calculate_supply_position
  calculate_spot_exposure
risk_delta
risk_interpretation (omitida por defecto; una llamada si se activa)
risk_result
agent_final
```

La vista muestra escenario, argumentos, resultado y duración por herramienta,
delta y estado. El resultado Pydantic completo está en un desplegable.
**Copy diagnostics** añade `Risk Execution` con una base, un estrés, cinco
llamadas de herramientas, cero RAG, cero LLM por defecto, cifras y deltas.
Incluye tiempo total, tiempo de cálculo determinista, tiempo de herramientas y
tiempo total de solicitudes LLM. El tiempo del bloque de cálculos incluye
validación, generación de hallazgos y trazas; no debe sumarse al tiempo de sus
herramientas anidadas. **Copy response**, **Copy all** y la descarga JSON usan el
resultado ya disponible, sin nuevas llamadas.

## Archivos

- Nuevos: `risk_models.py`, `risk_calculations.py`, `risk_agent.py`, `risk_ui.py`,
  `tests/test_risk_agent.py` y esta documentación.
- Integración: `app.py`, `generation.py`, `diagnostics.py`, `pipeline_inspector.py`,
  `clipboard_text.py`, `gas_analysis.py` y `README.md`.

Las pruebas cubren el caso exacto, transiciones LONG/SHORT/BALANCED, reducción,
escenarios múltiples y arbitrarios, datos ausentes e inválidos, ausencia de RAG,
reutilización de herramientas, límite de una llamada, fallback sin políticas
inventadas, métricas, portapapeles, AgentJob e interfaz/inspector con AppTest.
