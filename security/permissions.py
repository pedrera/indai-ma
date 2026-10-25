"""Explicit application permissions; no roles or automatic grants."""
from enum import Enum


class Permission(str, Enum):
    ANALYSIS_RUN = "analysis:run"
    PORTFOLIO_READ = "portfolio:read"
    DOCUMENTS_READ = "documents:read"
    DOCUMENTS_INGEST = "documents:ingest"
    DOCUMENTS_DELETE = "documents:delete"
    SCENARIOS_EVALUATE = "scenarios:evaluate"
    CRITERIA_EVALUATE = "criteria:evaluate"
    DECISIONS_EVALUATE = "decisions:evaluate"
    DIAGNOSTICS_READ = "diagnostics:read"
