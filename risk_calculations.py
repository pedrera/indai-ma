"""Derive units, deltas and factual interpretations from executed tool results."""

from risk_models import RiskDelta, RiskFinding, RiskScenarioResult


def build_risk_scenario(name, variation, demand, supply, position, spot_price, exposure):
    signed = round(position["position_gwh"], 12)
    short = round(exposure["short_position_gwh"], 12) if exposure is not None else max(-signed, 0)
    return RiskScenarioResult(
        name=name, demand_variation_percent=variation, demand_gwh=round(demand, 12),
        supply_gwh=supply, position_gwh=signed,
        interpretation=position["interpretation"], short_position_gwh=short,
        short_position_mwh=round(short * 1000, 9), spot_price_eur_mwh=spot_price,
        spot_exposure_eur=round(exposure["exposure_eur"], 8) if exposure is not None else None,
    )


def calculate_risk_delta(base: RiskScenarioResult, stress: RiskScenarioResult) -> RiskDelta:
    return RiskDelta(
        scenario_name=stress.name,
        demand_change_gwh=round(stress.demand_gwh - base.demand_gwh, 12),
        position_change_gwh=round(stress.position_gwh - base.position_gwh, 12),
        short_position_change_gwh=round(stress.short_position_gwh - base.short_position_gwh, 12),
        exposure_change_eur=(round(stress.spot_exposure_eur - base.spot_exposure_eur, 8)
                             if stress.spot_exposure_eur is not None and base.spot_exposure_eur is not None else None),
    )


def scenario_text(scenario):
    text = (f"{scenario.name}: demanda {scenario.demand_gwh:g} GWh, suministro {scenario.supply_gwh:g} GWh, "
            f"posición {scenario.position_gwh:g} GWh ({scenario.interpretation}). "
            f"Volumen descubierto: {scenario.short_position_gwh:g} GWh ({scenario.short_position_mwh:g} MWh).")
    if scenario.spot_exposure_eur is not None:
        text += f" Exposición spot: {scenario.spot_exposure_eur:,.2f} EUR."
    else:
        text += " Exposición monetaria no calculada: falta precio spot."
    return text


def build_risk_finding(base, stress=None, delta=None, finding_id="base"):
    if stress is None:
        return RiskFinding(finding_id=finding_id, scenario_name=base.name, direction="base", text=scenario_text(base))
    direction = "worsening" if delta.position_change_gwh < 0 else "improving" if delta.position_change_gwh > 0 else "unchanged"
    label = {"worsening": "Se reduce la cobertura de demanda", "improving": "Mejora la cobertura de demanda", "unchanged": "La cobertura de demanda no cambia"}[direction]
    text = (scenario_text(stress) + f" {label}; posición {base.interpretation} → {stress.interpretation}. "
            f"Cambio de volumen descubierto: {delta.short_position_change_gwh:+g} GWh.")
    if delta.exposure_change_eur is not None:
        text += f" Cambio de exposición spot: {delta.exposure_change_eur:+,.2f} EUR."
    return RiskFinding(finding_id=finding_id, scenario_name=stress.name, direction=direction, text=text)
