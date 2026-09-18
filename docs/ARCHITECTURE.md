# Current architecture (v0.9.0)

```text
User
  |
Streamlit app.py
  |
AgentJob / execution views / diagnostics
  |
Supervisor (guardrails, routing, collection, synthesis)
  |---------------------|-------------------|
ProcurementAgent   CommercialAgent      RiskAgent
tools               RAG + tools          deterministic tools
                         |
                 local contract index
                         |
              parse -> structural chunks -> metadata
                    -> embeddings -> persisted index
                    -> query embedding -> vector search
```

## Layers

The Streamlit entry point is `app.py`. Result presentation is split across
`commercial_ui.py`, `risk_ui.py`, `supervisor_ui.py`, `business_output.py`, and
`execution_view.py`. `generation.py` provides `AgentJob` and structured execution
results; `pipeline_inspector.py` and `diagnostics.py` provide trace views.

`supervisor.py` owns coordination. It calls specialist runners through their
interfaces; specialists do not invoke one another. `supervisor_models.py` and the
agent-specific model modules define structured results.

`llm_client.py` and provider adapters in the specialist modules abstract local LLM
providers. Generation is optional in deterministic paths. A multi-agent execution
therefore does not imply multiple LLM calls.

RAG is implemented by `document_ingestion.py`, `chunking.py`, `rag_service.py`,
`embeddings.py`, and `vector_store.py`. Ingestion parses documents, detects strong
structural headings, creates metadata-bearing chunks, embeds them, and persists a
local index. Retrieval returns `RetrievedChunk` evidence. The index validates schema,
chunker version, embedding model, dimensions and document metadata before use.

Deterministic tools live in the agent and analysis modules, including contractual
volume calculations, procurement position/exposure, and risk scenarios. Retrieved
facts retain source metadata; calculated facts identify their deterministic origin.

Supervisor guardrails in `guardrails.py` validate input and output. The evaluation
pipeline is `evals/models.py` → `evals/runner.py` → `evals/offline.py`; cases are
JSONL and assertions cover routing, values, sources, budgets and guardrails.

FAST evaluation builds temporary local fixture indexes with deterministic embeddings
and does not call a generation LLM. The repository does not currently provide a
separate production/full evaluation service; real-provider validation remains
environment-dependent.

Session state holds active executions, views, messages and diagnostics. Contract
indexes persist locally under the configured RAG index path; generated evaluation
reports and runtime diagnostics are ignored. There is no production API, enterprise
database, authentication/RBAC layer, or enterprise-system integration in the
current codebase.

The important RAG lesson is that quality begins before embeddings: structure
detection and chunking must preserve semantic relationships. A prior rule treated
prose ending in `:` as a heading and separated contractual price introductions from
their Spot formulas; the current structural rule avoids that failure.
