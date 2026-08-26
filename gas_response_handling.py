from dataclasses import dataclass
from enum import Enum

from diagnostics import PerformanceRecorder
from gas_analysis import (
    GasAnalysisError,
    ScenarioAnalysis,
    ScenarioResult,
    parse_scenario_analysis,
)
from rag_service import sanitize_rag_citations


class GasResponseContract(str, Enum):
    DOCUMENT_TEXT = "document_text"
    QUANTITATIVE_TEXT = "quantitative_text"
    SCENARIO_JSON = "scenario_json"


@dataclass(frozen=True)
class HandledGasResponse:
    contract: GasResponseContract
    documentary_content: str | None = None
    scenario_analysis: ScenarioAnalysis | None = None


def handle_gas_response(
    contract: GasResponseContract,
    content: str,
    recorder: PerformanceRecorder,
    scenarios: list[ScenarioResult],
    sources: list[dict],
) -> HandledGasResponse:
    if contract in {
        GasResponseContract.DOCUMENT_TEXT,
        GasResponseContract.QUANTITATIVE_TEXT,
    }:
        handled = _handle_documentary_response(content, recorder, sources)
        return HandledGasResponse(
            contract=contract,
            documentary_content=handled.documentary_content,
        )
    return _handle_scenario_response(content, recorder, scenarios)


def _handle_documentary_response(
    content: str,
    recorder: PerformanceRecorder,
    sources: list[dict],
) -> HandledGasResponse:
    final_event = recorder.start_stage(
        "final_response", response_chars=len(content)
    )
    if not content.strip():
        recorder.fail_stage(final_event, error_type="empty_document_response")
        raise GasAnalysisError(
            "El LLM no devolvió contenido para la respuesta documental."
        )
    sanitized = sanitize_rag_citations(content, sources)
    recorder.complete_stage(
        final_event, response_chars=len(sanitized), response_format="text"
    )
    return HandledGasResponse(
        contract=GasResponseContract.DOCUMENT_TEXT,
        documentary_content=sanitized,
    )


def _handle_scenario_response(
    content: str,
    recorder: PerformanceRecorder,
    scenarios: list[ScenarioResult],
) -> HandledGasResponse:
    try:
        analysis = parse_scenario_analysis(content, scenarios, recorder)
    except GasAnalysisError:
        final_event = recorder.start_stage("final_response")
        recorder.fail_stage(
            final_event, error_type="parse_or_validation_error"
        )
        raise
    final_event = recorder.start_stage(
        "final_response", response_chars=len(content)
    )
    recorder.complete_stage(
        final_event, response_format="scenario_json"
    )
    return HandledGasResponse(
        contract=GasResponseContract.SCENARIO_JSON,
        scenario_analysis=analysis,
    )
