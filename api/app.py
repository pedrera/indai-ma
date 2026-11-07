from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.exceptions import RequestValidationError

from api.composition import build_service, build_security
from api.errors import analysis_service_error, unexpected_error, security_error, validation_error
from api.routes import router
from application_service import AnalysisServiceError
from security.errors import SecurityError, IdentityInfrastructureError
from security_audit import AuditMiddleware, configured_audit


def create_app(service=None, runtime_config=None, use_llm_synthesis=False, *, security=None) -> FastAPI:
    owns_security = security is None
    if security is None:
        try:
            security = build_security()
        except IdentityInfrastructureError:
            security = None

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if owns_security and security is not None:
                security.authenticator.close()

    app = FastAPI(title="indAI MA API", version="1.1", lifespan=lifespan)
    if service is None and security is not None:
        service, runtime_config = build_service(security.authorizer)
    app.state.security = security
    app.state.analysis_service = service
    app.state.runtime_config = runtime_config
    app.state.use_llm_synthesis = use_llm_synthesis
    app.add_middleware(AuditMiddleware, sink=configured_audit())
    app.include_router(router)
    app.add_exception_handler(AnalysisServiceError, analysis_service_error)
    app.add_exception_handler(SecurityError, security_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(Exception, unexpected_error)
    return app


app = create_app()
