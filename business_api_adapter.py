"""Frontend adapter for the Streamlit Business-over-HTTP execution path."""
from dataclasses import dataclass

from api_client import AnalysisApiClient
from generation import AgentJob


@dataclass(frozen=True)
class BusinessApiRequest:
    text: str
    alternative_evaluation: object | None = None


class ApiJobAdapter:
    """Adapt the synchronous API client to the existing AgentJob contract."""

    def __init__(self, client: AnalysisApiClient):
        self.client = client

    def run(self, text: str, timeout_seconds: float):
        if isinstance(text, BusinessApiRequest):
            return self.client.analyze(text.text, text.alternative_evaluation)
        return self.client.analyze(text)


def create_business_api_job(request, client: AnalysisApiClient | None = None) -> AgentJob:
    client = client or AnalysisApiClient()
    return AgentJob(ApiJobAdapter(client), request, client.config.analysis_timeout, None)
