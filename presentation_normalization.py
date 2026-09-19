"""Small pure conversions used by presentation dispatch."""
from application_models import AnalysisResult
from supervisor_models import SupervisorResult


def normalize_supervisor_result(domain_result):
    """Return the SupervisorResult expected by the Supervisor presentation."""
    if isinstance(domain_result, AnalysisResult):
        return domain_result.supervisor_result
    if isinstance(domain_result, SupervisorResult):
        return domain_result
    return domain_result
