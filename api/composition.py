import os
import json
import ssl
from pathlib import Path

import certifi
import httpx

from application_service import AnalysisService
from embeddings import LMStudioEmbeddingProvider, get_embedding_model_name
from llm_client import get_llm_provider
from rag_service import RAGService
from runtime_config import LLMRuntimeConfig
from vector_store import LocalVectorStore
from security.config import OIDCConfig
from security.errors import IdentityInfrastructureError
from security.memberships import ConfiguredMembershipDirectory, TenantMembership
from security.models import PrincipalId, TenantId
from security.oidc import OIDCAuthenticator
from security.roles import Role
from api.security import APISecurity


def build_security() -> APISecurity:
    """Admin-owned configuration only; no built-in users, tenants or grants."""
    try:
        config = OIDCConfig(
            issuer=os.environ["INDAI_OIDC_ISSUER"], audience=os.environ["INDAI_OIDC_AUDIENCE"],
            jwks_url=os.environ["INDAI_OIDC_JWKS_URL"],
        )
        records = json.loads(Path(os.environ["INDAI_MEMBERSHIPS_FILE"]).read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("Membership configuration must be a list")
        memberships = []
        for record in records:
            if (not isinstance(record, dict)
                    or set(record) != {"issuer", "subject", "tenant_id", "roles"}
                    or record["issuer"] != config.issuer or not isinstance(record["roles"], list)):
                raise ValueError("Invalid configured membership")
            memberships.append(TenantMembership(
                PrincipalId(record["issuer"], record["subject"]), TenantId(record["tenant_id"]),
                frozenset(Role(role) for role in record["roles"]),
            ))
        directory = ConfiguredMembershipDirectory(memberships)
        transport = None
        ca_file = os.environ.get("INDAI_OIDC_CA_FILE")
        if ca_file is not None:
            ca_path = Path(ca_file)
            if not ca_path.is_absolute() or not ca_path.is_file():
                raise ValueError("Invalid explicit CA path")
            # Process-local trust configuration. Preserve the default public roots;
            # neither the venv bundle nor Windows stores are modified.
            tls = ssl.create_default_context(cafile=certifi.where())
            tls.load_verify_locations(cafile=str(ca_path))
            transport = httpx.HTTPTransport(verify=tls, trust_env=False)
    except (OSError, ValueError, TypeError, KeyError):
        raise IdentityInfrastructureError("Identity configuration is unavailable") from None
    return APISecurity(OIDCAuthenticator(config, transport=transport), directory)


def build_service(authorizer) -> tuple[AnalysisService, LLMRuntimeConfig]:
    runtime = LLMRuntimeConfig.from_environment()
    def rag_factory(recorder, config):
        # Defer document/index access until after AnalysisService authorization.
        store = LocalVectorStore.from_environment(get_embedding_model_name())
        if not store.chunk_count:
            raise ValueError("El índice RAG contractual no está disponible.")
        return RAGService(LMStudioEmbeddingProvider(), store, recorder)

    def provider_factory(recorder, config):
        provider_name = os.getenv("LLM_PROVIDER", "lmstudio")
        model_name = os.getenv("LMSTUDIO_MODEL") or os.getenv("OPENAI_MODEL")
        return get_llm_provider(provider_name, model_name, recorder=recorder, runtime_config=config)

    return AnalysisService(authorizer=authorizer, provider_factory=provider_factory, rag_factory=rag_factory), runtime
