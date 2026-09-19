"""Canonical business questions used by the Commercial Shell demo library."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DemoDefinition:
    key: str
    title: str
    description: str
    request: str


DEMO_SCENARIOS = (
    DemoDefinition(
        key="hospital_integrated",
        title="Hospital Costa Sur",
        description="Análisis integrado contractual, de aprovisionamiento y de riesgo.",
        request=(
            "Hospital Costa Sur prevé consumir 4,8 GWh el próximo mes.\n"
            "Tenemos 4,3 GWh aprovisionados y el precio spot es 42 EUR/MWh.\n"
            "Analiza la situación contractual, de aprovisionamiento y de riesgo."
        ),
    ),
    DemoDefinition(
        key="contract_comparison",
        title="Comparar contratos",
        description="Compara las condiciones de Hospital Costa Sur e Industrias Mediterráneo.",
        request=(
            "Compara las condiciones de flexibilidad, take-or-pay y consumo excedentario\n"
            "del Hospital Costa Sur y de Industrias Mediterráneo."
        ),
    ),
    DemoDefinition(
        key="procurement_shortfall",
        title="Aprovisionamiento",
        description="Analiza una posición de suministro con déficit y precio spot.",
        request=(
            "La demanda prevista es de 120 GWh, tenemos 95 GWh aprovisionados y el precio\n"
            "spot es de 42 EUR/MWh. Analiza nuestra posición de aprovisionamiento."
        ),
    ),
    DemoDefinition(
        key="risk_stress",
        title="Escenario de riesgo",
        description="Evalúa el impacto de un aumento explícito de demanda del 10 %.",
        request=(
            "La demanda prevista es de 4,8 GWh, tenemos 4,3 GWh aprovisionados y el precio\n"
            "spot es de 42 EUR/MWh. Analiza qué ocurre si la demanda aumenta un 10 %."
        ),
    ),
)


def get_demo(key: str) -> DemoDefinition:
    return next(demo for demo in DEMO_SCENARIOS if demo.key == key)
