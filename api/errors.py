from fastapi import Request
from fastapi.responses import JSONResponse

from api.models import ErrorDTO
from application_service import AnalysisServiceError
from security.errors import (AuthenticationRequired, InvalidCredentials,
                             IdentityInfrastructureError)


async def analysis_service_error(request: Request, exc: AnalysisServiceError):
    return JSONResponse(status_code=503, content=ErrorDTO(code="service_unavailable", message="El servicio de análisis no está disponible.").model_dump())


async def security_error(request: Request, exc):
    if isinstance(exc, (AuthenticationRequired, InvalidCredentials)):
        return JSONResponse(status_code=401, content=ErrorDTO(code="unauthorized", message="Se requieren credenciales válidas.").model_dump(), headers={"WWW-Authenticate": "Bearer"})
    if isinstance(exc, IdentityInfrastructureError):
        return JSONResponse(status_code=503, content=ErrorDTO(code="identity_unavailable", message="No se puede verificar el acceso en este momento.").model_dump())
    return JSONResponse(status_code=403, content=ErrorDTO(code="forbidden", message="Acceso no autorizado.").model_dump())


async def unexpected_error(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content=ErrorDTO(code="internal_error", message="No se pudo completar el análisis.").model_dump())


async def validation_error(request: Request, exc):
    # Do not echo rejected body values (which may contain credentials or claims).
    return JSONResponse(status_code=422, content=ErrorDTO(code="invalid_request", message="Solicitud de análisis no válida.").model_dump())
