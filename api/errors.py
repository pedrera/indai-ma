from fastapi import Request
from fastapi.responses import JSONResponse

from api.models import ErrorDTO
from application_service import AnalysisServiceError


async def analysis_service_error(request: Request, exc: AnalysisServiceError):
    return JSONResponse(status_code=503, content=ErrorDTO(code="service_unavailable", message=str(exc)).model_dump())


async def unexpected_error(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content=ErrorDTO(code="internal_error", message="No se pudo completar el análisis.").model_dump())
