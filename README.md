# indAI MA

## What it is

indAI MA is a local prototype for traceable B2B energy and industrial-gas supply
assurance. Its energy/Commercial flow analyzes contracts, procurement position and
risk; its separate Industrial Gases domain projects physical inventory continuity
for individual installations and independent portfolios. It is not a replacement
for trading, procurement, CRM, contract-management or enterprise systems.

Product principle: **Simple for the business user, traceable for the technical user.**

**Current state: v1.14.0 release candidate.** The published baseline is
v1.13.0; v1.14.0 has not been released or tagged.

## Energy / Commercial capabilities

Business mode sends a natural-language question through the Business API to the
AnalysisService and Supervisor. CommercialAgent, ProcurementAgent and RiskAgent
provide separate capabilities; Supervisor routes and synthesizes their structured
results. Deterministic tools calculate supported values, while RAG retrieves
contract evidence where needed. Optional LLM interpretation or synthesis is not
required for complete deterministic paths.

The energy/Commercial Business experience includes contract analysis and comparison,
procurement SHORT/BALANCED/LONG analysis, risk demand and spot-price scenarios,
Take-or-Pay projections, structured recommendations, decision plans/readiness and
explicit alternative evaluations. Contractual excess and procurement SHORT remain
distinct values.

## Industrial Gases capabilities

Industrial Gases is a separate physical-inventory domain. Its deterministic
Supply Assurance service projects stock continuity to a planned delivery, including
days of supply, inventory before and after delivery, safety-stock gap, stockout,
required delivery volume and capacity overflow. Strict unit semantics and
structured provenance are preserved; no implicit unit conversion is performed.

The current screens and capabilities are:

- **Healthcare Supply Assurance:** structured medical-oxygen inputs and explicit
  what-if comparisons for earlier delivery, planned delivery quantity and
  consumption forecast.
- **Food & Beverage Supply Assurance:** independent CO₂ and N₂ branches with
  separate installations, units and projections.
- **Supply Portfolio:** ordered evaluation of independent requests with individual
  results retained, existing operational facts surfaced per position, and explicit
  one-at-a-time what-if comparisons. It does not aggregate physical quantities
  across gases or positions, rank positions, or select a winner. Its small Decision
  Model records a baseline and explicit alternatives; it does not choose among them.
- **Industrial Knowledge / Supply Agent (v1.11.1):** an optional grounded
  assistant embedded in Supply Portfolio. It uses a fictional, identity-tagged demo
  corpus with the existing local RAG stack, and can explain an existing position,
  retrieve applicable documents, combine both evidence types, or evaluate one
  explicitly requested what-if through the existing scenario evaluator. Structured
  domain facts, cited document chunks and generated interpretation remain distinct.
- **Portfolio Intelligence (v1.12.0):** deterministic filters
  select ordered positions from existing portfolio results; bounded structured
  session context resolves unambiguous follow-ups; each position retains its own
  domain, attention, scenario and documentary evidence. Documentary retrieval and
  citation checks remain identity-scoped, with only explicitly applicable global
  Industrial Gases sources shared. What-if remains explicit and single-target.
  This does not add portfolio totals, rankings, recommendations or health scores.
- **Conversational Workspace (v1.14.0 release candidate):** the default
  conversation-first entry point routes natural-language questions over the
  existing portfolio query, per-position operational evidence, identity-scoped
  Industrial Knowledge and explicit single-position what-if capabilities. Bounded
  structured context preserves current-set, focus, documentary scope and scenario
  continuity. Deterministic reference resolution and unit-safe factual comparisons
  operate on current structured results. Cards keep positions, sources, comparisons
  and scenario projections separate. Advanced/demo modes remain under
  **Developer / Demo views**.
  Deterministic selection and supply results, operational findings, scenario
  projections and retrieved sources remain authoritative; generated prose only
  explains this evidence.

## Available modes

The default mode is **indAI MA**, a conversational workspace for operations,
documents and explicit scenarios. Existing **Business**, **Healthcare Supply
Assurance**, **Food & Beverage Supply Assurance** and **Supply Portfolio** modes,
plus Chat, Gas B2B Portfolio Analysis, ProcurementAgent, CommercialAgent,
RiskAgent, Multi-Agent Supervisor and Evaluation, remain available under
**Developer / Demo views**.

## Architecture at a glance

Energy / Commercial analysis:

```text
Business question → Supervisor → specialist agents → RAG/tools → Executive Result
```

Industrial Gases assurance:

```text
Structured installation data → SupplyAssuranceService → deterministic projection
                                      ↑
              Healthcare / Food & Beverage / Portfolio
                                              ↓
                      Operational Attention → explicit Portfolio what-if
```

Supply Portfolio what-if evaluates only a user-selected, explicit change with the
same deterministic Supply Assurance service. This path requires no generation
LLM, RAG, agent or Supervisor, and provides no scoring, ranking, recommendation,
optimization or cross-position physical aggregation.

The optional Supply Agent is used by the Conversational Workspace and remains
available in Supply Portfolio. It requires a configured generation provider for
generated explanations and a local embedding service for documentary retrieval.
Its demo corpus is fictional and is scoped by customer, site, application, gas
product and installation metadata before semantic ranking. Portfolio filtering is
deterministic and authoritative; item evidence remains separate. Session context is
bounded structured references, scopes and explicit scenario changes, not free-form
model memory. The agent
explains evidence; it does not calculate supply values, recommend actions, rank
alternatives or generate scenarios automatically. The existing deterministic flows
remain usable without generation; the workspace can answer deterministic portfolio
list requests without a model call. A safety-stock breach means projected inventory
is below the configured safety stock and does not itself mean stockout; only the
structured projection's `stockout_before_delivery` supports that statement.
v1.14.0 is a release candidate based on published v1.13.0; it is not yet a published release.

See [ARCHITECTURE.md](docs/ARCHITECTURE.md), [DECISIONS.md](docs/DECISIONS.md), [PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md), and [V1_MVP_SPEC.md](docs/V1_MVP_SPEC.md).

## Requirements

- Windows PowerShell and Python 3.10 or newer.
- `pip` and a virtual environment.
- LM Studio with an OpenAI-compatible local server for RAG embeddings.
- A chat model in LM Studio is optional for LLM interpretation, Chat, or optional synthesis.

## Quick Start — Windows

```powershell
git clone https://github.com/pedrera/indai-ma.git
cd indai-ma
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks activation, either run `Set-ExecutionPolicy -Scope Process Bypass` for the current shell or invoke `venv\Scripts\python.exe` directly.

## LM Studio setup

For the energy/Commercial contract-RAG flow and optional Industrial Knowledge,
start the local embedding server at
`http://localhost:1234/v1` and serve the embedding model identified by
`RAG_EMBEDDING_MODEL`, currently `text-embedding-nomic-embed-text-v1.5`. Model
identifiers must match LM Studio. `LMSTUDIO_MODEL` is optional for deterministic
flows and is only needed when a generation model is used. Healthcare,
Food & Beverage and the baseline Supply Portfolio use deterministic
supply-assurance paths and do not require LLM generation or RAG. Supply Agent is
optional and uses the configured generation model plus embeddings when explicitly
invoked.

## Environment configuration

Copy `.env.example` to `.env`. The energy/Commercial contract-RAG demo needs:

```env
LLM_PROVIDER=lmstudio
LMSTUDIO_BASE_URL=http://localhost:1234/v1
RAG_EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5
RAG_TOP_K=4
RAG_INDEX_PATH=.indai_ma/rag_index
```

`OPENAI_API_KEY` and `OPENAI_MODEL` are OpenAI-only and optional. Never commit `.env` or API keys. Runtime limits are optional and default to the values shown in `.env.example`.

## Prepare the energy / Commercial RAG index

1. Start LM Studio and serve the embedding model.
2. Start the app with `streamlit run app.py`.
3. Open **Documentación RAG** in the sidebar.
4. Upload both files from `demo/contracts/`: `contrato_hospital_costa_sur.txt` and `contrato_industrias_mediterraneo_2026.txt`.
5. Select **Indexar documentos** and verify the document and chunk counts.
6. Return to Business mode.

The persisted index is local under `.indai_ma/` and is never committed. If it is missing, Commercial is unavailable until documents are indexed. If it is incompatible, use the explicit **Reconstruir índice** action with the source documents. Embeddings alone cannot rebuild an index.

## Start indAI MA

```powershell
streamlit run app.py
```

The application opens in the **indAI MA** conversational workspace. Ask a
question about operations, documents or an explicit scenario and continue with
follow-up questions without changing modes. Advanced supply-assurance screens,
the energy Business flow, RAG administration, Evaluation and Pipeline Inspector
remain available under **Developer / Demo views** or expandable diagnostics.

## Demo scenarios

The canonical source documents are in [demo/contracts](demo/contracts). The four questions are defined in `demo_scenarios.py`; they are inputs, not hardcoded results. See [DEMO_GUIDE.md](docs/DEMO_GUIDE.md) for the 5–10 minute flow.

## Executive Result

Results are presented as summary, key metrics, business explanation, evidence, provenance, warnings and expandable technical details. Contractual excess and Procurement SHORT remain separate concepts. Deterministic results remain valid when optional LLM interpretation is skipped.

## Technical traceability

Pipeline Inspector shows routing, operation identity, RAG retrieval, tools, timings, call counts and specialist stages. It does not expose private chain-of-thought.

## Tests and evaluation

```powershell
python -m pytest
python -m evals --mode fast
```

FAST is deterministic/offline and must make zero real generation-LLM calls.

## Troubleshooting

**Blocking:** Python/version errors, activation policy, dependency failures, missing `.env`, unavailable LM Studio endpoint, wrong model identifier, unavailable embedding model, missing/incompatible RAG index, missing demo documents, or a port conflict.

**Optional degradation:** an unavailable chat model, skipped optional interpretation, or skipped optional synthesis. Deterministic Commercial, Procurement and Risk results remain valid when their required inputs and RAG evidence are available.

## Architecture documentation

See [ARCHITECTURE.md](docs/ARCHITECTURE.md), [DECISIONS.md](docs/DECISIONS.md), [PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md), and [ROADMAP.md](docs/ROADMAP.md).

## Roadmap

See [ROADMAP.md](docs/ROADMAP.md) for completed releases and future candidates.

## HTTP API (v1.1 Increment B)

The optional FastAPI adapter exposes the same application use case through
`AnalysisService`. Start it with `uvicorn api.app:app --reload`.

Endpoints are `GET /health`, `GET /ready` and `POST /api/v1/analysis`. The
request body contains only business intent, for example:

```json
{"text":"La demanda prevista es de 120 GWh, tenemos 95 GWh aprovisionados y el precio spot es de 42 EUR/MWh. Analiza nuestra posición de aprovisionamiento."}
```

Runtime policy remains server-controlled. Analysis is synchronous;
`operation_id` is a trace identifier, not a polling handle.

## Business UI through the API

The Business mode uses the HTTP client and therefore requires two local processes:

```powershell
python -m uvicorn api.app:app --reload
streamlit run app.py
```

Business execution sends only `{"text":"..."}` to the backend. Provider, model,
LLM policy and RAG configuration remain server-controlled. The remote Inspector
shows safe aggregate diagnostics; technical direct modes retain the full local
Inspector. Stopping the frontend cancels its wait, but does not guarantee
cancellation of work already running in the backend.
