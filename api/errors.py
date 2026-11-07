from fastapi import Request
from fastapi.responses import JSONResponse
import json
import re

from api.models import ErrorDTO
from application_service import AnalysisServiceError
from security.errors import (AuthenticationRequired, InvalidCredentials,
                             IdentityInfrastructureError, PermissionDenied, TenantAccessDenied)


async def analysis_service_error(request: Request, exc: AnalysisServiceError):
    return JSONResponse(status_code=503, content=ErrorDTO(code="service_unavailable", message="El servicio de análisis no está disponible.").model_dump())


async def security_error(request: Request, exc):
    validation_ids = request.headers.getlist("x-validation-id")
    if (request.method == "POST" and request.url.path == "/api/v1/analysis"
            and len(validation_ids) == 1 and re.fullmatch(r"[a-f0-9]{32}", validation_ids[0])):
        kind = next((name for cls, name in (
            (TenantAccessDenied, "TenantAccessDenied"), (PermissionDenied, "PermissionDenied"),
            (AuthenticationRequired, "AuthenticationRequired"), (InvalidCredentials, "InvalidCredentials"),
            (IdentityInfrastructureError, "IdentityInfrastructureError")) if isinstance(exc, cls)), "SecurityError")
        stage = getattr(request.state, "analysis_security_stage", "NOT_REACHED")
        allowed_stages = {"BEARER", "CONTEXT_FACTORY", "OPERATION_AUTHORIZATION", "AUTHORIZED"}
        print(json.dumps({"event": "ANALYSIS_SECURITY_REJECTION", "validation_id": validation_ids[0],
                          "exception_class": kind, "security_stage": stage if stage in allowed_stages else "UNKNOWN",
                          "service_dependency_entered": bool(getattr(request.state, "analysis_service_dependency_entered", False)),
                          "analysis_handler_entered": bool(getattr(request.state, "analysis_handler_entered", False))}), flush=True)
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
