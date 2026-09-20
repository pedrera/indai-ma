from pydantic import BaseModel, Field


class AnalysisRequestDTO(BaseModel):
    text: str = Field(min_length=1)


class MetricDTO(BaseModel):
    label: str
    value: str
    origin: str


class ExplanationDTO(BaseModel):
    title: str
    text: str


class EvidenceDTO(BaseModel):
    document: str
    section: str
    page: str
    excerpt: str = ""


class ProvenanceDTO(BaseModel):
    category: str
    label: str
    value: str
    origin: str


class RoutingDTO(BaseModel):
    selected_agents: list[str]
    skipped_agents: list[str]
    method: str
    reasons: list[str]


class SpecialistStatusDTO(BaseModel):
    agent_name: str
    status: str
    llm_calls: int
    rag_calls: int
    tool_calls: int
    warnings: list[str] = []


class DiagnosticsDTO(BaseModel):
    llm_calls: int
    rag_calls: int
    tool_calls: int


class BusinessActionDTO(BaseModel):
    category: str
    action: str
    rationale: str | None = None
    supporting_metrics: list[tuple[str, str]] = []


class BusinessRecommendationDTO(BaseModel):
    action: str
    is_complete: bool
    rationale: list[str] = []
    contractual_implication: str | None = None
    operational_implication: str | None = None
    risk_implication: str | None = None
    supporting_metrics: list[tuple[str, str]] = []
    warnings: list[str] = []
    actions: list[BusinessActionDTO] = []


class AnalysisResponseDTO(BaseModel):
    operation_id: str
    status: str
    summary: str
    metrics: list[MetricDTO]
    explanations: list[ExplanationDTO]
    evidence: list[EvidenceDTO]
    provenance: list[ProvenanceDTO]
    warnings: list[str]
    routing: RoutingDTO
    specialists: list[SpecialistStatusDTO]
    diagnostics: DiagnosticsDTO
    recommendation: BusinessRecommendationDTO | None = None


class HealthDTO(BaseModel):
    status: str


class ErrorDTO(BaseModel):
    code: str
    message: str
    operation_id: str | None = None
