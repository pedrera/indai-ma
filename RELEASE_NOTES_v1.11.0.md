# indAI MA v1.11.0 — Industrial AI

**Status:** release candidate; no v1.11.0 tag has been created.

## Industrial Knowledge

The optional Supply Agent uses eight fictional demo documents for Hospital Costa
Sur O₂, Alimentos del Sur CO₂ and N₂, plus a global Industrial Gases demo policy.
The corpus is explicitly illustrative and is not an industry standard or a real
customer contract. Structured document metadata is stored with the existing RAG
chunks. Retrieval checks identity eligibility before semantic ranking and keeps
the source document, chunk, page, metadata, text and score available as provenance.
An incomplete identity can retrieve only the explicitly global demo policy.

## Supply Agent

Supply Agent is embedded in Supply Portfolio and runs only when the user submits a
position-specific question. Its small tool set reads the existing supply position
and Operational Attention results, searches scoped Industrial Knowledge, and
evaluates one explicit what-if through the existing deterministic scenario
evaluator. It does not rerun baseline portfolio evaluation or calculate supply
facts itself.

## Grounded explanation and evidence

The response keeps three sources distinct: domain facts from the existing
`SupplyAssuranceResult` / `SupplyProjection`, documented knowledge from retrieved
chunks, and the LLM's explanation. Structured evidence references identify domain
fields or knowledge chunks; tool executions and provider/model trace remain
available in the existing execution inspector. RAG citations are checked against
the chunks actually retrieved.

## Failure handling

Domain `COMPLETED`, `INVALID` and `MISSING_INPUTS` states remain intact. Knowledge
retrieval distinguishes applicable documents, no applicable knowledge and an
operational retrieval failure. Generation/provider failure is a separate agent
status and preserves domain, attention and knowledge evidence collected before
the failure. FAST and ordinary tests use offline fixtures/fake providers; they do
not require a live model server.

## Limits

Supply Assurance remains the authority for physical calculations. Supply Agent
does not convert units, create thresholds, recommend actions, rank positions,
select a winner, optimize, aggregate gases or positions, or generate scenarios
automatically. It evaluates only an explicit user-requested what-if. Existing
deterministic Healthcare, Food & Beverage and Supply Portfolio paths do not require
LLM generation or RAG.
