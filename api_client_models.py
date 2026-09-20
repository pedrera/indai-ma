"""Frontend models for the public analysis HTTP contract."""
from pydantic import BaseModel


class ApiMetric(BaseModel):
    label: str
    value: str
    origin: str


class ApiExplanation(BaseModel):
    title: str
    text: str


class ApiEvidence(BaseModel):
    document: str
    section: str
    page: str
    excerpt: str = ""


class ApiProvenance(BaseModel):
    category: str
    label: str
    value: str
    origin: str


class ApiRouting(BaseModel):
    selected_agents: list[str]
    skipped_agents: list[str]
    method: str
    reasons: list[str]


class ApiSpecialist(BaseModel):
    agent_name: str
    status: str
    llm_calls: int
    rag_calls: int
    tool_calls: int
    warnings: list[str] = []


class ApiDiagnostics(BaseModel):
    llm_calls: int
    rag_calls: int
    tool_calls: int


class ApiBusinessAction(BaseModel):
    id: str | None = None
    category: str
    action: str
    rationale: str | None = None
    supporting_metrics: list[tuple[str, str]] = []


class ApiDecisionAlternative(BaseModel):
    id: str
    label: str
    description: str
    source_step_id: str


class ApiDecisionStep(BaseModel):
    id: str
    category: str
    action: str
    rationale: str | None = None
    supporting_metrics: list[tuple[str, str]] = []
    horizon: str | None = None
    decision_state: str = "review_required"
    depends_on: list[str] = []
    missing_information: list[str] = []
    source_action_id: str | None = None
    source_agent: str | None = None
    readiness: str | None = None
    alternatives: list[ApiDecisionAlternative] = []


class ApiDecisionPlan(BaseModel):
    steps: list[ApiDecisionStep] = []
    is_complete: bool
    warnings: list[str] = []
    readiness: str | None = None


class ApiBusinessRecommendation(BaseModel):
    action: str
    is_complete: bool
    rationale: list[str] = []
    contractual_implication: str | None = None
    operational_implication: str | None = None
    risk_implication: str | None = None
    supporting_metrics: list[tuple[str, str]] = []
    warnings: list[str] = []
    actions: list[ApiBusinessAction] = []
    decision_plan: ApiDecisionPlan | None = None


class ApiAnalysisResult(BaseModel):
    operation_id: str
    status: str
    summary: str
    metrics: list[ApiMetric] = []
    explanations: list[ApiExplanation] = []
    evidence: list[ApiEvidence] = []
    provenance: list[ApiProvenance] = []
    warnings: list[str] = []
    routing: ApiRouting
    specialists: list[ApiSpecialist] = []
    diagnostics: ApiDiagnostics
    recommendation: ApiBusinessRecommendation | None = None

    @property
    def content(self) -> str:
        return self.summary

    @property
    def tool_executions(self) -> list[dict]:
        return []

    def model_dump(self, mode: str = "python") -> dict:
        return super().model_dump(mode=mode)
