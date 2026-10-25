"""Frontend adapter for the Streamlit Business-over-HTTP execution path."""
from dataclasses import dataclass, field

from api_client import AnalysisApiClient, validate_operation_credentials, AnalysisApiCredentialsError
from generation import AgentJob


@dataclass(frozen=True)
class BusinessApiRequest:
    text: str
    alternative_evaluation: object | None = None
    access_token: str | None = field(default=None, repr=False)
    tenant_id: str | None = None


class ApiJobAdapter:
    """Adapt the synchronous API client to the existing AgentJob contract."""

    def __init__(self, client: AnalysisApiClient):
        self.client = client

    def run(self, text: str, timeout_seconds: float):
        if isinstance(text, BusinessApiRequest):
            validate_operation_credentials(text.access_token, text.tenant_id)
            return self.client.analyze(text.text, text.alternative_evaluation,
                                       access_token=text.access_token, tenant_id=text.tenant_id)
        raise AnalysisApiCredentialsError("Se requieren credenciales por operación.")


def create_business_api_job(request, client: AnalysisApiClient | None = None) -> AgentJob:
    if not isinstance(request, BusinessApiRequest):
        raise AnalysisApiCredentialsError("Se requieren credenciales por operación.")
    validate_operation_credentials(request.access_token, request.tenant_id)
    client = client or AnalysisApiClient()
    return AgentJob(ApiJobAdapter(client), request, client.config.analysis_timeout, None)
