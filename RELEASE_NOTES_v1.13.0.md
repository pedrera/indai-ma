# indAI MA v1.13.0 — Conversational Workspace

**Release candidate — not yet released or tagged.**

## Overview

The default application experience is now a conversational workspace. Ask about
operational positions, applicable documents or an explicitly requested scenario,
then continue with contextual follow-up questions without manually switching
between capabilities.

## What is included

- Conversation-first **indAI MA** home with starter prompts and a New conversation
  action.
- Deterministic portfolio selection and bounded structured session references
  over the existing evaluated demo portfolio. Current explicit identity filters
  override previous scope; ambiguous or stale references request clarification.
- Per-position cards for projection facts, operational attention and evaluation
  issues. Compact source cards show a human-readable position, document type and
  page where available; full chunk text, identifiers, applicability metadata,
  citation details and provenance remain under Evidence & trace.
- Explicit single-position what-if results displayed as baseline and alternative
  projections without synthesizing deltas.
- RAG retrieval and generation remain intent-gated. Operational selection and
  explicit scenario calculation do not require RAG; a deterministic portfolio
  list query does not require a generation call.
- Technical evidence, tool traces and Pipeline Inspector are available through
  expandable diagnostics. Existing Business, Healthcare, Food & Beverage,
  Supply Portfolio and other developer/demo views remain available in secondary
  navigation.

## Authority and safety boundaries

Portfolio Query remains authoritative for selected positions. Existing Supply
Assurance projections and Operational Attention facts remain authoritative for
calculated values and findings. Industrial Knowledge retrieval and citation
validation remain authoritative for documentary evidence. The existing scenario
evaluator remains authoritative for explicit what-if consequences. Generated
prose only explains evidence already returned by these capabilities.

A safety-stock breach means projected inventory is below the configured safety
stock; it does **not** by itself mean a physical stockout. Stockout and capacity
overflow are presented only when the structured projection supports them.
Evidence from different positions remains separate.

## Limitations

- Uses the current demo supply portfolio and existing capabilities; it is not a
  general data connector or unrestricted conversation memory.
- Follow-up context is bounded structured identifiers, not remembered free-form
  preferences or hidden model reasoning.
- Documentary answers require eligible retrieved evidence and a valid citation.
- For documentary questions covering multiple positions, each position-specific
  claim remains paired with its own selected-position source and citation;
  applicable global guidance stays a separate claim.
- Scenarios must be explicit and resolve to one position. No automatic scenario
  generation, batch what-if, ranking, recommendation, optimization, logistics
  planning, portfolio totals or new supply calculations are included.
- A configured generation model is needed for synthesized explanations. Provider
  or retrieval failures preserve already-collected structured operational
  evidence where available.

## Validation

Automated tests and FAST use fake/offline providers and do not invoke LM Studio.
Real-provider validation remains a separate release-candidate step.
