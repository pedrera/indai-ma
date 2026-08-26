from diagnostics import PerformanceRecorder
from gas_analysis import PreparedGasAnalysis, prepare_gas_analysis
from rag_service import RAGService


def prepare_gas_analysis_with_rag(
    portfolio_description: str,
    recorder: PerformanceRecorder,
    rag_service: RAGService,
) -> PreparedGasAnalysis:
    """Resolve document-backed domain context before deterministic analysis."""
    retrieval = rag_service.retrieve(portfolio_description)
    return prepare_gas_analysis(
        portfolio_description,
        recorder,
        gas_resolution=retrieval.gas_type_resolution,
    )
