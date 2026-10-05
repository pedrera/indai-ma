# indAI MA v1.16.0 — Efficient & Deterministic Intelligence

## Overview

v1.16.0 reduces unnecessary local generation for documentary questions while
preserving the existing Workspace routing, retrieval, evidence boundaries and
validation behavior. The generation LLM remains available when a response needs
synthesis or operational context.

## Included

- Documentary final-generation prompts project the state required for document
  scope, retrieved evidence, provenance and citation validation.
- A conservative deterministic documentary response path is used only when
  retrieved evidence is proven sufficient for a directly supported factual
  answer. It preserves the supporting source identity and exact citation token.
- Item-specific documentary requests with unresolved scope can ask for
  clarification before RAG retrieval, embedding inference or generation.
- Citation validation remains strict and unchanged; the system does not inject
  citations after generation.
- Embedding-request telemetry and session-scoped reuse of compatible embedding
  providers/clients expose safe lifecycle diagnostics without logging secrets,
  query text or embedding values.
- Query-embedding and Workspace-generation benchmark tools support repeatable
  local performance characterization. Qwen3-8B with LM Studio was characterized;
  experimental runtime tuning values are not production defaults.

## No-LLM terminology

“No-LLM” means zero generative-LLM calls. A documentary response may still require
embedding inference for RAG retrieval. When early scope resolution determines
that clarification is required, retrieval and embedding can also be avoided.

## Boundaries

Retrieval, source scope, grounding and citation validation remain authoritative.
The deterministic path does not infer unsupported facts or summarize ambiguous
or conflicting evidence. Generation remains the fallback where direct evidence
is insufficient or the request requires synthesis or operational context.
