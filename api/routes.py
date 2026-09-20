from fastapi import APIRouter, Depends, Request

from api.models import (AnalysisRequestDTO, AnalysisResponseDTO, DiagnosticsDTO,
                        EvidenceDTO, ExplanationDTO, HealthDTO, MetricDTO,
                        ProvenanceDTO, RoutingDTO, SpecialistStatusDTO,
                        BusinessRecommendationDTO, BusinessActionDTO,
                        BusinessDecisionPlanDTO, BusinessDecisionStepDTO)
from application_models import AnalysisRequest
from application_service import AnalysisService

router = APIRouter()


def service_dependency(request: Request) -> AnalysisService:
    return request.app.state.analysis_service


@router.get("/health", response_model=HealthDTO)
def health():
    return HealthDTO(status="ok")


@router.get("/ready", response_model=HealthDTO)
def ready(request: Request):
    if getattr(request.app.state, "analysis_service", None) is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="La aplicación no está preparada.")
    return HealthDTO(status="ready")


@router.post("/api/v1/analysis", response_model=AnalysisResponseDTO)
def analyze(payload: AnalysisRequestDTO, request: Request, service: AnalysisService = Depends(service_dependency)):
    result = service.analyze(AnalysisRequest(
        text=payload.text,
        runtime=request.app.state.runtime_config,
        use_llm_synthesis=request.app.state.use_llm_synthesis,
    ))
    projection = result.executive
    routing = result.supervisor_result.routing
    return AnalysisResponseDTO(
        operation_id=result.operation_id,
        status=result.supervisor_result.status.value,
        summary=projection.summary,
        metrics=[MetricDTO(**metric.__dict__) for metric in projection.metrics],
        explanations=[ExplanationDTO(title=title, text=text) for title, text in projection.explanations],
        evidence=[EvidenceDTO(**item.__dict__) for item in projection.evidence],
        provenance=[ProvenanceDTO(**item.__dict__) for item in projection.provenance],
        warnings=list(projection.warnings),
        routing=RoutingDTO(selected_agents=routing.selected_agents, skipped_agents=routing.skipped_agents,
                           method=routing.routing_method, reasons=routing.routing_reasons),
        specialists=[SpecialistStatusDTO(agent_name=item.agent_name, status=item.status, llm_calls=item.llm_calls,
                                         rag_calls=item.rag_calls, tool_calls=item.tool_calls, warnings=item.warnings)
                     for item in result.supervisor_result.specialist_results],
        diagnostics=DiagnosticsDTO(llm_calls=result.supervisor_result.total_llm_calls,
                                  rag_calls=result.supervisor_result.total_rag_calls,
                                  tool_calls=result.supervisor_result.total_tool_calls),
        recommendation=(BusinessRecommendationDTO(
            action=projection.recommendation.action,
            is_complete=projection.recommendation.is_complete,
            rationale=list(projection.recommendation.rationale),
            contractual_implication=projection.recommendation.contractual_implication,
            operational_implication=projection.recommendation.operational_implication,
            risk_implication=projection.recommendation.risk_implication,
            supporting_metrics=list(projection.recommendation.supporting_metrics),
            warnings=list(projection.recommendation.warnings),
            actions=[BusinessActionDTO(
                id=item.id,
                category=item.category,
                action=item.action,
                rationale=item.rationale,
                supporting_metrics=list(item.supporting_metrics),
            ) for item in projection.recommendation.actions],
            decision_plan=(BusinessDecisionPlanDTO(
                steps=[BusinessDecisionStepDTO(
                    id=step.id,
                    category=step.category,
                    action=step.action,
                    rationale=step.rationale,
                    supporting_metrics=list(step.supporting_metrics),
                    horizon=step.horizon,
                    decision_state=step.decision_state,
                    depends_on=list(step.depends_on),
                    missing_information=list(step.missing_information),
                    source_action_id=step.source_action_id,
                    source_agent=step.source_agent,
                    readiness=step.readiness,
                ) for step in projection.recommendation.decision_plan.steps],
                is_complete=projection.recommendation.decision_plan.is_complete,
                warnings=list(projection.recommendation.decision_plan.warnings),
                readiness=projection.recommendation.decision_plan.readiness,
            ) if projection.recommendation.decision_plan is not None else None),
        ) if projection.recommendation is not None else None),
    )
