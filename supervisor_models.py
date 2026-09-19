"""Supervisor envelopes preserve each specialist's own result contract."""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from agent_models import AgentRunResult
from commercial_models import CommercialAgentResult
from risk_models import RiskAgentResult
from guardrails import GuardrailResult
from business_recommendation import BusinessRecommendation

AgentName = Literal["CommercialAgent", "ProcurementAgent", "RiskAgent"]
AGENT_ORDER = ("CommercialAgent", "ProcurementAgent", "RiskAgent")


class SupervisorStatus(str, Enum):
    REJECTED_INPUT = "rejected_input"
    VALIDATION_FAILED = "validation_failed"
    COMPLETED = "completed"
    PARTIAL = "partial"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class RoutingSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_agents: list[AgentName] = Field(max_length=3)


class SupervisorRoutingDecision(RoutingSelection):
    routing_method: Literal["deterministic", "llm", "hybrid"] = "deterministic"
    routing_reasons: list[str] = Field(default_factory=list)
    ambiguity_detected: bool = False
    warnings: list[str] = Field(default_factory=list)

    @property
    def skipped_agents(self):
        return [name for name in AGENT_ORDER if name not in self.selected_agents]


class SpecialistExecutionResult(BaseModel):
    agent_name: AgentName
    status: str
    duration_seconds: float = 0
    result: CommercialAgentResult | AgentRunResult | RiskAgentResult | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    llm_calls: int = 0
    rag_calls: int = 0
    tool_calls: int = 0
    reused_tool_results: int = 0

    @model_validator(mode="after")
    def preserve_specialist_contract(self):
        expected = {"CommercialAgent": CommercialAgentResult, "ProcurementAgent": AgentRunResult, "RiskAgent": RiskAgentResult}
        if self.result is not None and not isinstance(self.result, expected[self.agent_name]):
            raise ValueError("El resultado no corresponde al especialista indicado.")
        return self


class SupervisorResult(BaseModel):
    input_guardrails: GuardrailResult = Field(default_factory=GuardrailResult)
    output_guardrails: GuardrailResult = Field(default_factory=GuardrailResult)
    status: SupervisorStatus
    routing: SupervisorRoutingDecision
    specialist_results: list[SpecialistExecutionResult] = Field(default_factory=list)
    combined_findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str = ""
    synthesis_status: str = "deterministic"
    total_operation_wall_time: float = 0
    total_llm_calls: int = 0
    total_rag_calls: int = 0
    total_tool_calls: int = 0
    router_llm_calls: int = 0
    synthesis_llm_calls: int = 0
    recommendation: BusinessRecommendation | None = None

    @property
    def tool_executions(self):
        return [dict(tool, supervisor_agent=item.agent_name)
                for item in self.specialist_results if item.result is not None
                for tool in item.result.tool_executions]

    @property
    def content(self):
        if self.status in {SupervisorStatus.REJECTED_INPUT, SupervisorStatus.VALIDATION_FAILED}:
            return "\n".join([self.summary, *[v.message for g in (self.input_guardrails, self.output_guardrails) for v in g.violations]])
        labels = {"CommercialAgent": "Posición contractual", "ProcurementAgent": "Posición de aprovisionamiento",
                  "RiskAgent": "Escenarios de estrés"}
        lines = [self.summary]
        for item in self.specialist_results:
            lines.append(f"### {labels[item.agent_name]}")
            lines.append(item.result.content if item.result is not None else item.error or "Sin resultado disponible.")
        if self.combined_findings:
            lines += ["### Implicaciones conjuntas", *self.combined_findings]
        lines += [f"Aviso: {warning}" for warning in self.warnings]
        return "\n\n".join(lines)
