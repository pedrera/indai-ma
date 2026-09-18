from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from supervisor_models import AgentName


class ValueExpectation(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    field: str
    expected: Any
    absolute_tolerance: float = Field(default=1e-6, ge=0)
    relative_tolerance: float = Field(default=1e-9, ge=0)


class SourceExpectation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_name: str
    section_contains: str = ''
    evidence_contains: str = ''


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_version: Literal['0.9'] = '0.9'
    case_id: str = Field(pattern=r'^[a-z0-9_]+$')
    name: str
    category: Literal['procurement', 'commercial', 'risk', 'supervisor', 'guardrails']
    query: str
    expected_agents: list[AgentName] | None = None
    forbidden_agents: list[AgentName] = Field(default_factory=list)
    expected_values: list[ValueExpectation] = Field(default_factory=list)
    expected_sources: list[SourceExpectation] = Field(default_factory=list)
    expected_status: str | None = None
    expected_max_llm_calls: int = Field(default=0, ge=0)
    expected_rag_calls: int | None = Field(default=None, ge=0)
    expected_tool_names: list[str] | None = None
    expected_warning_codes: list[str] = Field(default_factory=list)
    expected_input_allowed: bool = True
    forbidden_content: list[str] = Field(default_factory=list)
    fixture: Literal['contract', 'injection', 'multi_contract'] = 'contract'
    tags: list[str] = Field(default_factory=list)


class ValueCheck(ValueExpectation):
    actual: Any = None
    passed: bool


class EvaluationResult(BaseModel):
    case_id: str
    category: str
    operation_id: str
    status: str = 'evaluated'
    passed: bool = False
    execution_status: str = 'error'
    actual_agents: list[str] = Field(default_factory=list)
    routing_correct: bool | None = None
    false_agent_invocations: int = 0
    missing_agent_invocations: int = 0
    value_checks: list[ValueCheck] = Field(default_factory=list)
    source_checks: list[bool] = Field(default_factory=list)
    tool_checks: bool | None = None
    llm_call_check: bool = False
    rag_call_check: bool | None = None
    guardrail_checks: bool = False
    unsupported_answer_failure: bool = False
    input_allowed: bool = True
    latency_ms: float = 0
    llm_calls: int = 0
    rag_calls: int = 0
    tool_calls: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    execution: dict = Field(default_factory=dict)
    trace: dict = Field(default_factory=dict)


class EvaluationSummary(BaseModel):
    total_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float | None
    routing_accuracy: float | None
    false_agent_invocations: int
    missing_agent_invocations: int
    numeric_assertions_total: int
    numeric_assertions_passed: int
    calculation_accuracy: float | None
    source_assertions_total: int
    source_assertions_passed: int
    unsupported_answer_failures: int
    average_latency_ms: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    max_latency_ms: float | None
    total_llm_calls: int
    average_llm_calls_per_case: float | None
    total_rag_calls: int
    total_tool_calls: int
    failed_operations: int
    blocked_inputs: int
    allowed_inputs: int
    guardrail_failures: int
