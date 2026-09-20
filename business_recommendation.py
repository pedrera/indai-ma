"""Deterministic composition of a Business recommendation."""
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor_models import SupervisorResult


@dataclass(frozen=True)
class BusinessRecommendation:
    action: str
    is_complete: bool
    rationale: tuple[str, ...] = ()
    contractual_implication: str | None = None
    operational_implication: str | None = None
    risk_implication: str | None = None
    supporting_metrics: tuple[tuple[str, str], ...] = ()
    warnings: tuple[str, ...] = ()


def _execution(result, name):
    return next((item for item in getattr(result, "tool_executions", ())
                 if item.get("name") == name), None)


def _commercial_calculation(item):
    result = getattr(item.result, "calculations", ()) if item and item.result else ()
    return result[0].result if result else {}


def _commercial_surcharge(item):
    """Read the contractual surcharge from existing structured inputs/facts."""
    if not item or not item.result:
        return None
    calculations = getattr(item.result, "calculations", ())
    if calculations:
        surcharge = getattr(calculations[0], "inputs", {}).get("excess_surcharge_eur_mwh")
        if surcharge is not None:
            return surcharge
    return next((fact.value for fact in getattr(item.result, "contract_facts", ())
                 if fact.name == "excess_surcharge_eur_mwh"), None)


def _risk_number(value):
    if value is None:
        return None
    return f"{value:,.0f}" if isinstance(value, (int, float)) and float(value).is_integer() else f"{value:g}"


def compose_business_recommendation(supervisor_result: "SupervisorResult") -> BusinessRecommendation:
    """Compose conclusions from specialist outputs without recalculating them."""
    specialists = {item.agent_name: item for item in supervisor_result.specialist_results}
    commercial = specialists.get("CommercialAgent")
    procurement = specialists.get("ProcurementAgent")
    risk = specialists.get("RiskAgent")
    rationale: list[str] = []
    metrics: list[tuple[str, str]] = []
    warnings: list[str] = []
    contractual = None
    operational = None
    risk_text = None
    missing: list[str] = []
    incomplete_risk = False

    calculation = _commercial_calculation(commercial)
    excess = calculation.get("contractual_excess_gwh")
    forecast = calculation.get("forecast_demand_gwh")
    contractual_min = calculation.get("contractual_min_gwh")
    contractual_max = calculation.get("contractual_max_gwh")
    if excess is not None and float(excess) > 0:
        surcharge = _commercial_surcharge(commercial)
        price = calculation.get("contractual_excess_price_eur_mwh")
        contractual = f"Existe un exceso contractual independiente de {excess:g} GWh."
        if surcharge is not None:
            contractual += f" Recargo contractual: +{surcharge:g} EUR/MWh."
        if price is not None:
            contractual += f" Precio del exceso calculado: {price:g} EUR/MWh."
        metrics.append(("Exceso contractual", f"{excess:g} GWh"))
        rationale.append(contractual)
    elif (forecast is not None and contractual_min is not None and contractual_max is not None):
        if contractual_min <= forecast <= contractual_max:
            contractual = (f"El consumo previsto de {forecast:g} GWh permanece dentro del rango "
                           f"contractual de {contractual_min:g}–{contractual_max:g} GWh.")
        elif forecast < contractual_min:
            contractual = (f"El consumo previsto de {forecast:g} GWh está por debajo del rango "
                           f"contractual de {contractual_min:g}–{contractual_max:g} GWh.")
        else:
            contractual = (f"El consumo previsto de {forecast:g} GWh está por encima del rango "
                           f"contractual de {contractual_min:g}–{contractual_max:g} GWh.")
        rationale.append(contractual)

    exposure = _execution(procurement.result if procurement and procurement.result else None, "calculate_spot_exposure")
    position = _execution(procurement.result if procurement and procurement.result else None, "calculate_supply_position")
    interpretation = (position or {}).get("result", {}).get("interpretation")
    if interpretation == "SHORT":
        amount = (position or {}).get("result", {}).get("short_position_gwh")
        if amount is None:
            amount = abs((position or {}).get("result", {}).get("position_gwh", 0))
        operational = f"La posición de aprovisionamiento es SHORT por {amount:g} GWh."
        rationale.append("Se recomienda cubrir el SHORT operativo.")
        metrics.append(("SHORT operativo", f"{amount:g} GWh"))
    elif interpretation == "BALANCED":
        operational = "La posición de aprovisionamiento es BALANCED; no se recomienda cobertura adicional."
        rationale.append(operational)
    elif interpretation == "LONG":
        amount = abs((position or {}).get("result", {}).get("position_gwh", 0))
        operational = f"La posición de aprovisionamiento es LONG por {amount:g} GWh; debe revisarse el excedente."
        rationale.append(operational)
    elif procurement and procurement.result:
        missing.append("posición de aprovisionamiento")

    if exposure:
        value = exposure.get("result", {}).get("exposure_eur")
        if value is not None:
            metrics.append(("Exposición spot", f"{value:g} EUR"))
            operational = (operational + " " if operational else "") + f"La exposición spot calculada es {value:g} EUR."
    if commercial and commercial.result and excess is not None and interpretation == "SHORT":
        rationale.append("El exceso contractual y el SHORT de aprovisionamiento son magnitudes diferentes; no se suman ni se sustituyen.")

    if risk and risk.result:
        base = risk.result.base_scenario
        if base:
            risk_text = f"La posición de riesgo base es {base.interpretation} por {base.short_position_gwh:g} GWh."
            rationale.append(risk_text)
        stress = list(risk.result.stress_scenarios)
        deltas = list(getattr(risk.result, "deltas", ()))
        for scenario_index, scenario in enumerate(stress):
            scenario_type = getattr(scenario, "scenario_type", "DEMAND")
            if scenario_type == "PRICE":
                scenario_name = getattr(scenario, "name", None)
                delta = next((item for item in deltas
                              if getattr(item, "scenario_name", None) == scenario_name), None)
                if delta is None and scenario_index < len(deltas):
                    candidate = deltas[scenario_index]
                    if getattr(candidate, "scenario_type", "PRICE") == "PRICE":
                        delta = candidate
                if (base is None or base.spot_price_eur_mwh is None or
                        scenario.spot_price_eur_mwh is None or base.spot_exposure_eur is None or
                        scenario.spot_exposure_eur is None or delta is None or
                        delta.exposure_change_eur is None):
                    incomplete_risk = True
                    warnings.append(f"El escenario PRICE {scenario.stress_percent:g}% no tiene exposición monetaria completa.")
                    continue
                line = (f"Con un aumento del spot del {scenario.stress_percent:g}%, el precio pasa de "
                        f"{_risk_number(base.spot_price_eur_mwh)} a {_risk_number(scenario.spot_price_eur_mwh)} EUR/MWh "
                        f"y la exposición del SHORT pasa de {_risk_number(base.spot_exposure_eur)} a "
                        f"{_risk_number(scenario.spot_exposure_eur)} EUR ({delta.exposure_change_eur:+,.0f} EUR).")
                risk_text = (risk_text + " " if risk_text else "") + line
                rationale.append(line)
                metrics.append((f"Exposición PRICE {scenario.stress_percent:+g}%", f"{_risk_number(scenario.spot_exposure_eur)} EUR"))
            elif scenario_type == "DEMAND":
                line = f"Escenario de demanda {scenario.stress_percent:+g}%: {scenario.demand_gwh:g} GWh."
                risk_text = (risk_text + " " if risk_text else "") + line
                rationale.append(line)
        if not stress and not base:
            missing.append("posición de riesgo")

    if commercial and not calculation:
        missing.append("resultado contractual")
    if procurement and procurement.result and not position:
        missing.append("posición de aprovisionamiento")
    if not commercial and not procurement and not risk:
        missing.append("resultados de especialistas")
    if missing:
        unique = tuple(dict.fromkeys(missing))
        warnings.extend(f"Falta {item}." for item in unique)
        action = "Obtener " + ", ".join(unique) + " antes de emitir una recomendación completa."
        complete = False
    else:
        action = ("Cubrir el SHORT operativo." if interpretation == "SHORT" else
                  "Revisar las opciones de gestión del excedente operativo." if interpretation == "LONG" else
                  "Revisar las implicaciones identificadas.")
        complete = bool(rationale)
    if incomplete_risk:
        complete = False
    return BusinessRecommendation(action, complete, tuple(rationale), contractual, operational, risk_text,
                                  tuple(metrics), tuple(dict.fromkeys(warnings)))
