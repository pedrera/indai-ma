from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class RiskStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class RiskModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RiskInputs(RiskModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    demand_gwh: float = Field(ge=0)
    supply_gwh: float = Field(ge=0)
    spot_price_eur_mwh: float | None = Field(default=None, ge=0)
    stress_percentages: list[Annotated[float, Field(ge=-100)]] = Field(default_factory=list, max_length=20)


class RiskScenarioResult(RiskModel):
    name: str
    demand_variation_percent: float = 0
    demand_gwh: float = Field(ge=0)
    supply_gwh: float = Field(ge=0)
    position_gwh: float
    interpretation: Literal["LONG", "SHORT", "BALANCED"]
    short_position_gwh: float = Field(ge=0)
    short_position_mwh: float = Field(ge=0)
    spot_price_eur_mwh: float | None = Field(default=None, ge=0)
    spot_exposure_eur: float | None = Field(default=None, ge=0)


class RiskDelta(RiskModel):
    scenario_name: str
    demand_change_gwh: float
    position_change_gwh: float
    short_position_change_gwh: float
    exposure_change_eur: float | None = None


class RiskFinding(RiskModel):
    finding_id: str
    scenario_name: str
    direction: Literal["base", "worsening", "improving", "unchanged"]
    text: str


class RiskInterpretation(RiskModel):
    priority_finding_ids: list[str] = Field(max_length=21)


class RiskAgentResult(RiskModel):
    status: RiskStatus
    base_scenario: RiskScenarioResult | None = None
    stress_scenarios: list[RiskScenarioResult] = Field(default_factory=list)
    deltas: list[RiskDelta] = Field(default_factory=list)
    risk_findings: list[RiskFinding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    tool_executions: list[dict] = Field(default_factory=list)
    interpretation_mode: Literal["deterministic", "llm_prioritized", "deterministic_fallback"] = "deterministic"
    summary: str

    @property
    def content(self):
        return "\n\n".join([self.summary, *(f.text for f in self.risk_findings),
                             *(f"Aviso: {w}" for w in self.warnings)])
