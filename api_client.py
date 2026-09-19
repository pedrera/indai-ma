"""Small HTTP boundary used by the Streamlit Business adapter."""
import os
from dataclasses import dataclass

import httpx

from api_client_models import ApiAnalysisResult


class AnalysisApiClientError(RuntimeError):
    pass


class AnalysisApiConnectionError(AnalysisApiClientError):
    pass


class AnalysisApiTimeoutError(AnalysisApiClientError):
    pass


class AnalysisApiResponseError(AnalysisApiClientError):
    pass


@dataclass(frozen=True)
class ApiClientConfig:
    base_url: str = "http://127.0.0.1:8000"
    connect_timeout: float = 5
    analysis_timeout: float = 300

    @classmethod
    def from_environment(cls):
        return cls(
            os.getenv("INDAI_API_BASE_URL", cls.base_url).rstrip("/"),
            float(os.getenv("INDAI_API_CONNECT_TIMEOUT_SECONDS", cls.connect_timeout)),
            float(os.getenv("INDAI_API_ANALYSIS_TIMEOUT_SECONDS", cls.analysis_timeout)),
        )


class AnalysisApiClient:
    def __init__(self, base_url=None, connect_timeout=5, analysis_timeout=300,
                 transport=None, client=None):
        self.config = ApiClientConfig(base_url or ApiClientConfig.from_environment().base_url,
                                      connect_timeout, analysis_timeout)
        self._client = client or httpx.Client(
            base_url=self.config.base_url,
            transport=transport,
            timeout=httpx.Timeout(self.config.analysis_timeout, connect=self.config.connect_timeout),
        )

    def analyze(self, text: str) -> ApiAnalysisResult:
        try:
            response = self._client.post("/api/v1/analysis", json={"text": text})
        except httpx.TimeoutException as error:
            raise AnalysisApiTimeoutError("El análisis ha superado el tiempo permitido.") from error
        except httpx.HTTPError as error:
            raise AnalysisApiConnectionError("El servicio de análisis no está disponible.") from error
        if response.status_code >= 400:
            if response.status_code in (422, 500, 503, 504):
                raise AnalysisApiResponseError("El servicio de análisis no pudo procesar la solicitud.")
            raise AnalysisApiResponseError("Respuesta HTTP no válida del servicio de análisis.")
        try:
            return ApiAnalysisResult.model_validate(response.json())
        except (ValueError, TypeError) as error:
            raise AnalysisApiResponseError("Respuesta del backend no válida.") from error

