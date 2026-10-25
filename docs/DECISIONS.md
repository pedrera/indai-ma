# Retrospective architectural decisions

These entries document decisions visible in the implementation; they are not
claims that formal ADRs existed at the time.

## ADR-001 — Deterministic-first execution

**Status:** implemented. **Context:** complete contractual and numeric paths do not
need generation. **Decision:** prefer deterministic extraction and tools, with LLM
interpretation optional. **Consequences:** predictable cost and latency; wording
coverage remains bounded by deterministic rules.

## ADR-002 — RAG retrieves, tools calculate, LLM interprets

**Status:** implemented. **Context:** evidence and computation have different
failure modes. **Decision:** keep those responsibilities separate. **Consequences:**
results can be grounded and audited without an LLM call.

## ADR-003 — Provider abstraction

**Status:** implemented. **Decision:** provider clients are behind `llm_client.py`
and specialist adapters. **Consequence:** local providers can be replaced without
changing business tools.

## ADR-004/005 — Specialist boundaries and Supervisor ownership

**Status:** implemented. Specialists do not call specialists; `Supervisor` owns
routing, collection and synthesis.

## ADR-006 — Multi-Agent does not require Multi-LLM

**Status:** implemented. Deterministic Supervisor and specialist paths can complete
with zero generation calls.

## ADR-007 — Capability-dependent validation

**Status:** implemented. Guardrails and result status distinguish blocked input,
missing required capability, and expected optional interpretation skips.

## ADR-008/009 — Response and provenance contracts

**Status:** implemented. Documentary comparisons are structured separately from
quantitative calculations; retrieved facts, calculations, and friendly sources are
represented distinctly.

## ADR-010/011 — Independent contracts and semantic chunks

**Status:** implemented. Multi-contract extraction groups by document before
extraction, and structural chunking preserves related contractual statements.

## ADR-012/013 — Offline evaluation and test distinction

**Status:** implemented. FAST evaluation is deterministic/offline. Unit/integration
tests check coded behavior; evaluations check business outcomes and grounding.

## ADR-014 — Optional enrichment does not invalidate deterministic results

**Status:** implemented. An expected skipped interpretation stage is diagnostic
information, not a failure of an otherwise complete result.

## ADR-015 — Deterministic Executive Result projection

**Status:** implemented. Business presentation is derived deterministically from
structured specialist results; domain results remain the source of truth and
technical diagnostics remain separate. The projection must preserve provenance,
specialist boundaries and zero-LLM completeness without recalculating business
values.

## ADR-016 — Application Service as the transport-independent analysis boundary

**Status:** implemented in v1.1 Increment A. The main analysis use case is
exposed through `AnalysisService`, independent of Streamlit. UI and future
transport adapters construct an `AnalysisRequest`, invoke the service and
present or store its `AnalysisResult`.

The service composes the existing Supervisor, specialists, RAG/tools,
guardrails, deterministic Executive Result projection and observability. This
keeps application orchestration reusable from tests and future transports while
preserving the current specialist boundaries and behavior. Streamlit remains
responsible for jobs, threading, cancellation, reruns, session state and
rendering. FastAPI is intentionally not introduced in this increment.

## ADR-017 — FastAPI as an HTTP adapter over AnalysisService

**Status:** implemented in v1.1 Increment B. FastAPI exposes only health, readiness and the synchronous analysis endpoint. HTTP DTOs are representations of `AnalysisResult`, `SupervisorResult` and `ExecutiveResultProjection`; they are not sources of business truth and do not recalculate values.

The API owns transport validation and server-controlled runtime configuration, then constructs `AnalysisRequest` and invokes `AnalysisService`. It does not call Supervisor, specialists, RAG or tools directly. No operation persistence, polling, authentication or enterprise infrastructure is introduced in this increment.

## ADR-018 — Streamlit Business UI consumes the backend through the HTTP API

**Status:** implemented in v1.1 Increment C. Business execution crosses the
`AnalysisApiClient` boundary and reaches `AnalysisService` only through FastAPI.
The client sends the public `{text}` contract, and server configuration controls
providers, models, limits, LLM policy and RAG paths. There is no automatic direct
fallback when the API is unavailable. AgentJob remains responsible for frontend
threading, reruns, timeout display and local wait cancellation; backend execution
is not distributed-cancellable in this increment.

Technical modes may remain direct during the migration. The remote Business
Inspector exposes only safe aggregate diagnostics and never raw event streams,
prompts or credentials.

## ADR-019 — Explicit Industrial Gases criteria over retained scenario facts

**Status:** implemented locally for the v1.18.0 release candidate. **Context:**
v1.17 retains an ordered baseline and explicit alternatives, but factual
comparison alone does not represent business targets or mandatory conditions.

**Decision:** keep objectives and hard constraints bounded to Industrial Gases.
Use immutable typed predicates over the seven existing ScenarioSetComparison
metrics, finite Decimal thresholds, strict Boolean expectations and exact units.
Evaluate verified retained facts without calling SupplyAssuranceService or any
generation, embedding or RAG provider. Objectives have independent outcomes;
hard constraints aggregate with violation precedence, then unknown, then
satisfied, and NOT_APPLICABLE for an empty constraint set. Satisfaction is not a
claim of overall safety or a decision recommendation.

Bind definitions and evaluations to source references/revisions and the five-field
position identity. Invalid global bindings abort; unavailable individual facts
produce UNKNOWN. The evaluator rejects partial scenario comparisons; Workspace
rebuilds a full factual comparison from retained results without changing the
displayed A/B comparison or recalculating projections. Source provenance records
remain unchanged, with result/projection/provenance references in the evaluation.

The Workspace accepts only complete sentences in a small explicit grammar.
Fully parsed criteria-only sentences reuse the retained focused position instead
of entering general comparison scope resolution; “al menos” must not turn a
threshold into a request to compare portfolio positions. New scenario requests
retain their existing resolver. Identical definitions reuse evaluation; set/focus
changes and reset invalidate unrelated criteria. UI and Copy format the same
recorded results, without interpreting predicates again.

**Consequences:** deterministic, inspectable criterion satisfaction with no
automatic generation, ranking, scoring or optimization. The typed API is broader
than the Workspace grammar. References and content fingerprints are session-scoped
integrity checks, not persistent audit records or cryptographic authentication.
The caller remains responsible for authoritative set revisions. A transversal
cross-domain criteria abstraction is deferred until another domain demonstrates
a concrete need. Energy/Business API, RAG and citation behavior remain unchanged.

## ADR-020 — Explicit unique-compliance selection over verified retained criteria

**Status:** implemented locally for the v1.19.0 release candidate. **Context:**
v1.17 retains scenario facts and v1.18 evaluates explicit criteria, but neither
authorizes selection. A bounded decision must distinguish unique compliance from
business preference and from incomplete evidence.

**Decision:** keep the engine in Industrial Gases. The only supported policy is
UNIQUE_HARD_CONSTRAINT_COMPLIANT, created by Workspace only after a recognized
explicit ES/EN command. Default EXPLICIT_ALTERNATIVES excludes baseline;
ALL_SCENARIOS requires express inclusion. Candidate order is preserved, not used
to choose. Objectives do not affect eligibility. A hard violation dominates
UNKNOWN while preserving both references; otherwise UNKNOWN leaves eligibility
unresolved. No hard constraints retains NOT_APPLICABLE and eligibility None.

Verify full identity, set revision, manifests, criterion coverage, versions and
source references; rebuild the full factual comparison and rerun v1.18 logical
evaluation against original retained results. Require equality of the canonical
snapshot before classifying candidates. Source integrity errors remain typed
errors, never business outcomes. The caller supplies the authoritative current
revision; the engine cannot discover unseen later revisions.

Exactly one eligible candidate and no unresolved rivals yields DECIDED. Multiple
eligible candidates, none, or absent hard constraints yield UNDECIDABLE with
specific reasons. At most one eligible candidate with unresolved rivals yields
INSUFFICIENT_EVIDENCE. No implicit policy, preferences or tie-breakers are applied.

Workspace retains the explicit policy and scope in the decision snapshot and
reuses only matching source objects/content, identity, revision, criteria, policy,
scope and evaluator/engine versions. Changes invalidate reuse; new sets and reset
do not inherit authorization. Partial factual displays never determine scope.
UI and Copy format recorded data through one presentation path.

**Consequences:** unique compliance is not overall safety, optimality, a business
recommendation or execution authorization. Selection/presentation use no LLM,
embeddings, RAG, network or physical recalculation; logical reevaluation is allowed.
Provenance and reuse keys are session-local, not persistent audit storage or
authentication. New policies, optimization, execution, persistent lifecycle and
cross-domain abstractions remain deferred. No new dependencies are introduced.

## ADR-021 — Transport-independent security contracts (C1.1)

**Status:** implemented, contracts only. **Context:** the modular monolith needs
explicit identity and access contracts before OIDC, authorization enforcement or
multi-tenant storage can be introduced.

**Decision:** define frozen value objects and provider-independent Protocol ports
in `security/`. PrincipalId uses the exact issuer and subject pair, because a
subject is scoped to its issuer and email is not a stable identity key. TenantId
represents a security ownership boundary, independently of customer_id and the
five-field industrial position identity. ResourceRef records the resource type,
identifier and owning tenant; constructing it does not establish ownership.

SecurityContext holds a principal, previously validated active tenant, explicit
typed effective permissions and correlation identifier. Its nested identifiers
are frozen and permission collections are copied into a frozenset, preventing
accidental mutation across operations. No credentials or tokens are retained.
Permission strings require explicit enum conversion; unknown values are rejected.
There are no roles, default grants or physical-execution permissions.

Only a future trusted authentication/application boundary may construct the
context after credential validation, membership resolution and permission
resolution. Prompts, LLM outputs and business identifiers cannot establish these
facts or grant permissions. Object construction validates structure, not trust.
Authenticator, MembershipResolver and Authorizer separate identity validation,
tenant membership and action/resource authorization. Exceptions remain independent
of HTTP; future adapters will translate failures without swallowing denials.

Future API and authenticated Streamlit adapters will construct these contexts;
application services, Supervisor, agents, tool boundaries and document storage
will consume them. A protected Industrial Gases application boundary will check
access to retained evidence before calling the Decision Engine. The engine and
pure calculations remain free of authentication and transport concerns, preserving
determinism and existing source-integrity checks.

**Consequences:** no runtime integration, provider, dependency or existing flow is
changed by this increment. These values are not cryptographic proof, and the ports
have no production implementation yet.

**C1.1 solo define contratos. No introduce autenticación, autorización efectiva
ni aislamiento multi-tenant en las entradas actuales.**

## ADR-022 — Tenant-local RBAC and authoritative membership policy (C1.2)

**Status:** implemented, not integrated. **Context:** C1.1 values validate
structure but do not prove identity, membership, resource ownership or grants.

**Decision:** retain the C1.1 contracts unchanged. Add MembershipDirectory as an
extension of MembershipResolver with one authoritative resolve(principal, tenant)
lookup returning a frozen TenantMembership or None. This avoids a caller-supplied
membership Boolean and binds roles to the complete issuer/subject and tenant pair.
ConfiguredMembershipDirectory is an immutable, explicitly supplied snapshot for
tests and controlled development, not an enterprise directory or user database.
No built-in users, tenant defaults or universal grants exist. Duplicate pair
configuration is rejected. Replacing configuration applies membership changes.

The explicit role map has no inheritance or wildcard:

| Role | Permissions |
| --- | --- |
| Viewer | portfolio:read, documents:read |
| Analyst | portfolio:read, documents:read, analysis:run, scenarios:evaluate, criteria:evaluate |
| Approver | portfolio:read, documents:read, decisions:evaluate |
| Tenant Admin | documents:read, documents:ingest, documents:delete, diagnostics:read |

Multiple explicitly assigned roles union their permissions only within that tenant.
Tenant Admin does not inherit business permissions. Approver is a reserved business
role name: decisions:evaluate permits evaluation, not approval or physical action.
A future approval operation requires a separately designed explicit permission;
this increment retains exactly the nine C1.1 permissions.

SecurityContextFactory defines the trusted construction boundary: an injected
Authenticator validates credentials, then the selected tenant is resolved from
the injected directory, and permissions come exclusively from that membership.
There is no roles/permissions input from the caller. A tenant change requires a
new context with fresh resolution. No production authenticator is supplied here.

TenantAuthorizer implements the C1.1 Authorizer port and resolves membership on
every call. It denies missing membership, untyped permissions, absent effective
permissions, context grants exceeding current authoritative grants, and resources
owned by another tenant. Context permissions may narrow grants, never widen them.
Directory/security failures propagate, never become partial results or fallback
access. A permission can authorize a general operation without a resource; actual
resource access must also pass the resource check. Cross-tenant/shared resources
have no exception: no sharing policy is implemented. customer_id, prompt text and
global labels never establish membership.

The composition root must provide trustworthy authentication and directory ports;
resource ownership must be loaded from trusted storage. A constructed PrincipalId,
SecurityContext or ResourceRef is not cryptographic proof. This policy does not
authenticate arbitrary Python callers or detect forged ownership supplied by a
compromised caller. Future adapters must not deserialize these trusted values
directly from client business payloads, LLM output or prompts.

**Consequences:** no HTTP, OIDC provider, database, dependencies or existing business
flow changes. Authentication adapters, safe resource loaders, application-service
enforcement, session/tenant transitions, document isolation and audit integration
remain future work. The Decision Engine remains pure and outside authentication.
**C1.2 does not yet protect current application entry points or provide effective
multi-tenant isolation for existing business flows.**

## ADR-023 — Explicit RFC 9068 access-token validation (C1.3)

**Status:** implemented, not integrated. **Context:** authentication needs real
signature validation without coupling identity to HTTP, UI or authorization.
The user approved PyJWT[crypto]; requirements now declare >=2.15.1,<3. RSA and
JWK operations use PyJWT/cryptography, never handwritten cryptography.

**Decision:** OIDCConfig is immutable and explicit: one exact HTTPS issuer, API
audience and preconfigured HTTPS JWKS URL, with no discovery or environment
defaults. URLs cannot contain credentials, query or fragment. The composition
root must approve that endpoint; the token cannot select a trust configuration.
OIDCAuthenticator implements Authenticator and returns only PrincipalId(iss, sub).

The supported profile is signed RFC 9068 JWT access tokens with RS256 and a
protected typ of at+jwt or application/at+jwt. Require iss, sub, aud, exp, iat,
client_id and jti; validate signature, exact issuer, audience, expiration, issued
time and nbf when present. Subjects/client IDs/token IDs must be nonblank strings;
timestamps must be finite numbers, not strings or booleans, with exp after iat.
Default clock skew is 15 seconds (configurable 0–60). An ID token with typ JWT
is rejected even if signed by the same issuer. Providers using another access
token format, opaque tokens, JWE or other signing algorithms are unsupported.

Keycloak remains the intended development provider, but this increment neither
installs it nor assumes its default tokens match RFC 9068. A future integration
must configure/verify that emission profile, audience and HTTPS or separately
design and test an explicit provider-specific profile. No permissive typ fallback.

JWKS is fetched only from the configured URL using TLS verification, with proxy
environment settings and redirects disabled. Token jku, x5u, embedded jwk/x5c,
critical extensions and b64 overrides are rejected. kid is merely a bounded
selector within the trusted set, not an authentication assertion or URL. Only
eligible RSA signing keys of at least 2048 bits are used; duplicate eligible kids,
private key material and incompatible key operations are rejected.

Each authenticator owns its issuer-scoped cache protected by a lock. Default TTL
is 300 seconds; unknown kids refresh at most once per 10 seconds, including after
failures. A provider rotation must publish a new kid. Successful refresh replaces
the set, removing withdrawn keys. A cached known key can remain valid until TTL;
there is no immediate revocation/introspection. Expired keys never provide fallback
when refresh fails. A newly rotated unknown key may be denied during cooldown.
Timeout defaults to 5 seconds, bounded to 30, with streamed size (256 KiB), key
count (64), token size (16 KiB), and elapsed-response checks. HTTPX timeouts bound
individual I/O; this synchronous adapter does not provide hard cancellation of
DNS resolution or an already blocking transport operation.

Malformed credentials, validation errors and JWKS failures deny access with typed
exceptions and sanitized messages, without logging credentials or private claims.
No roles, scopes, tenants or permissions are granted from token claims.
MembershipDirectory and TenantAuthorizer remain responsible for tenant membership
and authorization after identity validation. The trusted factory can use this
authenticator, but no application entry point constructs or invokes it yet.

**Consequences:** offline tests generate ephemeral RSA keys and simulate JWKS,
including key rotation, failures, cross-issuer isolation and concurrent cache
access. No interactive login, PKCE, HTTP integration, provider deployment,
persistent audit or business-flow changes. Callers own authenticator lifecycle
and close its HTTP client. Deployment egress rules should further constrain
configured endpoints; token content can never authorize additional destinations.
**Authentication is not yet connected to current product entry points.**

References: [RFC 9068](https://www.rfc-editor.org/rfc/rfc9068.html),
[PyJWT validation API](https://pyjwt.readthedocs.io/en/stable/api.html).

## ADR-024 — Mandatory analysis authorization and authenticated HTTP (C1.4)

**Status:** implemented for AnalysisService and POST /api/v1/analysis. **Context:**
HTTP-only checks would leave the direct Streamlit Supervisor entry unsecured.
The revised increment explicitly authorizes disabling that entry until an
authenticated UI exists. No identity, permissions or tenant defaults are added.

**Decision:** AnalysisService requires a TenantAuthorizer at construction and
an explicit SecurityContext for every analyze(request, context) call. It checks
analysis:run against current authoritative membership before validating business
inputs, constructing a recorder, Supervisor, RAG or generation provider. Context
permissions cannot exceed current grants. Missing/invalid context denies access;
there is no optional-context path, privileged workflow or test/development mode.
SecurityError exceptions propagate instead of becoming partial/service results.
Authentication of the principal remains the trusted caller's responsibility;
constructing a Python context alone is not cryptographic identity proof.

The HTTP dependency accepts one Bearer credential exclusively in Authorization
and one active tenant in X-Tenant-ID. Tenant syntax is 1–128 ASCII letters/digits
and . _ : -, beginning with a letter/digit. No path, whitespace or duplicate
selection is accepted. SecurityContextFactory authenticates using OIDCAuthenticator
and resolves membership, then TenantAuthorizer checks analysis:run. The service
checks again, including revocation between HTTP resolution and execution. Context
and correlation are per request and passed explicitly, never stored in mutable
global request/session state. Body, cookies, query parameters and token role/tenant
claims cannot establish identity, membership or grants. Business DTOs still forbid
extra fields. Validation errors do not echo rejected input values.

HTTP failures use sanitized messages: missing/invalid/expired credentials are
401 with WWW-Authenticate: Bearer; missing permission/membership is 403; missing,
duplicate or malformed tenant is 400; unavailable/misconfigured identity/JWKS or
membership infrastructure is 503. IdentityInfrastructureError distinguishes source
failures from InvalidCredentials without string matching. Failed JWKS refresh
retains the infrastructure classification during cooldown; an unknown kid in a
successfully loaded key set remains an invalid credential. No stale expired-key
fallback exists. These rules supersede the undifferentiated C1.3 JWKS exception.

APISecurity composes an authenticator, MembershipDirectory, context factory and
authorizer per app. Composition reads INDAI_OIDC_ISSUER, INDAI_OIDC_AUDIENCE,
INDAI_OIDC_JWKS_URL and INDAI_MEMBERSHIPS_FILE. The membership file is an admin-owned
JSON list of exact records with issuer, subject, tenant_id and a roles list;
issuer must match configured OIDC issuer. Empty configuration grants no access;
unknown roles, duplicate memberships and malformed configuration fail closed.
The configuration must never be written from client input or committed with real
identities. Existing configured-directory limitations remain: it is a snapshot,
not a production directory; replacing/reloading it is needed for grant changes.

Missing configuration leaves analysis unavailable (503 for supplied credentials,
401 when absent); health stays public and readiness reports 503. No index is read
during service composition: RAG index access is deferred until authorized execution.
The app owns/closes the authenticator it creates through its lifespan; injected
dependencies are owned by the trusted caller. Test fixtures inject explicit
identities/memberships or real JWTs with offline JWKS; production does not import
tests or offer a bypass flag. There is no new dependency.

AnalysisApiClient requires access_token and tenant_id as keyword-only operation
arguments and sets headers on that request only. BusinessApiRequest carries them
per job, excludes the token from repr and rejects missing credentials before job
creation/execution. Tokens are never client-global defaults or environment fallbacks.
The public business JSON remains unchanged. Interactive login is not implemented.

The Streamlit Supervisor renderer displays a disabled-mode message and returns;
its direct start function raises AuthenticationRequired before any work. Its old
implementation is retained unreachable for a future authenticated adapter.
Streamlit Business has no authenticated credential source yet: its analysis
button is disabled and direct start rejects before constructing a job. The
authenticated HTTP client is usable by callers supplying genuine per-operation
credentials, but the UI must not advertise Business as executable yet.

**Consequences and limits:** authentication/operation authorization do not enforce
document ownership. The contractual RAG index, document ingestion/listing/deletion,
retrieval, source presentation and cross-tenant document/resource authorization
still need protection. An authorized analysis can access the existing configured
contract index; do not treat C1.4 as enterprise document multi-tenancy. Workspace,
Industrial Gases, specialist technical views, standalone Supervisor and evaluation
tools remain outside this protected boundary. Their deterministic behavior is
unchanged and they must not be advertised as protected enterprise entry points.
No changes to formulas, specialists, RAG logic or Decision Engine. No Keycloak,
database, login or broader Streamlit security is introduced. Enterprise deployment
remains blocked on those resource and entry-point controls; C1.5 is not automatic.

## ADR-025 — Native Streamlit OIDC client for Business (C1.5)

**Status:** implemented, live-provider acceptance pending. **Context:** the user
approved Streamlit as the OIDC client, Authlib and Streamlit 1.62.0 minimum, with
no BFF or relaxed backend validation. Installed Streamlit 1.62.0 exposes public
st.login/st.logout/st.user.tokens.access through expose_tokens=["access"]. Authlib
1.9.1 and its joserfc dependency were installed; no dependency is used to invent
an alternate callback/cookie flow.

**Decision:** use the native Authorization Code client, configured with external
secrets and PKCE S256. Add authenticated GET /api/v1/session for validated identity,
expiration and per-principal tenant discovery. Extend membership listing without
changing C1.1 resolve semantics. OIDCAuthenticator exposes validated metadata while
its Authenticator port continues returning only PrincipalId. ID tokens do not
authenticate either API endpoint. List responses never contain credentials.

Business verifies native issuer/subject against the backend and requires explicit
selection of a tenant with analysis:run. Tokens are read per operation from the
public native API; session metadata contains no tokens. Native login timestamps,
principal, selected tenant and a random epoch bind jobs to a session. Permission
checks run again at start and polling; revoked/expired/unverifiable sessions fail
closed. User/tenant changes and logout remove Business state and Inspector data,
detach/cancel old jobs locally and reject late results. Workspace state is not
treated as protected Business state. Renewal requires new login rather than
unsupported refresh-token extraction. Native persistent cookies do not extend
the accepted access-token lifetime. Other browser sessions are not globally revoked.

Keycloak's at+jwt option exists from 26.2 but is disabled by default. The proposed
client config includes exact redirects, RS256, API audience and required client_id
claim; real issued-token compatibility and callback/logout/certificates still need
provider acceptance. No Keycloak/Docker/server or production identities were added.
Secrets are configured outside the repository. See [Streamlit OIDC setup](STREAMLIT_OIDC.md).

**Consequences:** Business is conditionally enabled for verified authorized native
sessions. Supervisor technical mode remains disabled. Backend authorization stays
mandatory, with no anonymous/static-token fallback. This is not a BFF: native
framework cookie handling, dependency/proxy logging policy and browser-session
lifecycle remain deployment concerns. Resource/document isolation and other
direct views remain pending and block enterprise multi-tenant deployment. Offline
tests exercise real JWTs and explicit public-API doubles, not live Keycloak login.
