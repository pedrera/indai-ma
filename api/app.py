from fastapi import FastAPI

from api.composition import build_service
from api.errors import analysis_service_error, unexpected_error
from api.routes import router
from application_service import AnalysisServiceError


def create_app(service=None, runtime_config=None, use_llm_synthesis=False) -> FastAPI:
    app = FastAPI(title="indAI MA API", version="1.1")
    if service is None:
        service, runtime_config = build_service()
    app.state.analysis_service = service
    app.state.runtime_config = runtime_config
    app.state.use_llm_synthesis = use_llm_synthesis
    app.include_router(router)
    app.add_exception_handler(AnalysisServiceError, analysis_service_error)
    app.add_exception_handler(Exception, unexpected_error)
    return app


app = create_app()
