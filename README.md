# indAI MA

## What it is

indAI MA is a local prototype for traceable B2B energy decision support. The Commercial MVP lets a business user ask a natural-language question, routes it through Supervisor, retrieves contract evidence when needed, runs deterministic tools, and presents an Executive Result. It is not a replacement for trading, procurement, CRM, contract-management or enterprise systems.

Product principle: **Simple for the business user, traceable for the technical user.**

## Commercial MVP capabilities

Business mode provides four editable examples:

1. Hospital Costa Sur integrated analysis.
2. Hospital Costa Sur versus Industrias Mediterráneo contract comparison.
3. Procurement position for 120/95/42.
4. Risk stress scenario with an explicit +10% demand change.

The deterministic path does not require chat generation. Commercial and contract comparison require RAG embeddings; Procurement and Risk can run without RAG.

## Architecture at a glance

```text
Business question → Supervisor → specialist agents → RAG/tools → Executive Result
```

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

Start the local server at `http://localhost:1234/v1`. Load and serve the embedding model identified by `RAG_EMBEDDING_MODEL`, currently `text-embedding-nomic-embed-text-v1.5`. Model identifiers must match LM Studio. `LMSTUDIO_MODEL` is optional for deterministic demos and is only needed when a generation model is used.

## Environment configuration

Copy `.env.example` to `.env`. The deterministic local RAG demo needs:

```env
LLM_PROVIDER=lmstudio
LMSTUDIO_BASE_URL=http://localhost:1234/v1
RAG_EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5
RAG_TOP_K=4
RAG_INDEX_PATH=.indai_ma/rag_index
```

`OPENAI_API_KEY` and `OPENAI_MODEL` are OpenAI-only and optional. Never commit `.env` or API keys. Runtime limits are optional and default to the values shown in `.env.example`.

## Prepare the RAG index

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

The application opens in Business mode. Select an example or write a question, review it, and press **Analizar**. Advanced modes, configuration, RAG administration, Evaluation and Pipeline Inspector remain available under **Advanced / Technical**.

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

The repository currently contains the v0.9 evaluation/guardrails baseline and the v1.0 Commercial MVP increments. Enterprise API, persistence, integrations, SSO/RBAC and cloud packaging remain future roadmap work.
