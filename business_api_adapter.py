"""Frontend adapter for the Streamlit Business-over-HTTP execution path."""
from api_client import AnalysisApiClient
from generation import AgentJob


class ApiJobAdapter:
    """Adapt the synchronous API client to the existing AgentJob contract."""

    def __init__(self, client: AnalysisApiClient):
        self.client = client

    def run(self, text: str, timeout_seconds: float):
        return self.client.analyze(text)


def create_business_api_job(request: str, client: AnalysisApiClient | None = None) -> AgentJob:
    client = client or AnalysisApiClient()
    return AgentJob(ApiJobAdapter(client), request, client.config.analysis_timeout, None)
