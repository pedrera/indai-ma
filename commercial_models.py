from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class CommercialStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class CommercialSource(BaseModel):
    document_name: str
    section: str | None = None
    page_start: int
    page_end: int
    score: float

    @property
    def label(self) -> str:
        pages = str(self.page_start) if self.page_start == self.page_end else f"{self.page_start}–{self.page_end}"
        return f"{self.document_name} · {self.section or 'Sin sección'} · pág. {pages}"


class ContractFact(BaseModel):
    name: str
    value: float | str
    unit: str | None = None
    evidence: str
    source: CommercialSource


class CommercialFinding(BaseModel):
    evidence: str
    source: CommercialSource


class CommercialCalculation(BaseModel):
    name: str
    origin: Literal["deterministic"] = "deterministic"
    inputs: dict[str, float]
    result: dict[str, float | None]
    input_evidence: dict[str, str] = Field(default_factory=dict)


class CommercialAgentResult(BaseModel):
    interpretation_mode: Literal["deterministic", "llm"] = "llm"
    status: CommercialStatus
    customer: str | None = None
    contract_facts: list[ContractFact] = Field(default_factory=list)
    commercial_findings: list[CommercialFinding] = Field(default_factory=list)
    calculations: list[CommercialCalculation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sources: list[CommercialSource] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    summary: str
    tool_executions: list[dict] = Field(default_factory=list)

    @property
    def content(self) -> str:
        lines = [self.summary]
        for fact in self.contract_facts:
            lines.append(f"{fact.name}: {fact.value} {fact.unit or ''} [{fact.source.label}]")
        for finding in self.commercial_findings:
            lines.append(f"{finding.evidence} [{finding.source.label}]")
        for calculation in self.calculations:
            lines.append(f"Cálculo determinista ({calculation.name}): {calculation.result}")
        lines.extend(f"Aviso: {warning}" for warning in self.warnings)
        return "\n\n".join(lines)


class EvidenceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_number: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)


class CommercialInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[EvidenceSelection] = Field(default_factory=list, max_length=8)


class CommercialEvidencePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_numbers: list[Annotated[int, Field(strict=True, ge=1)]] = Field(max_length=8)
