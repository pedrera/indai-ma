# indAI MA v1.15.0 — Conversational Multi-Scenario Decision Support

**Release candidate.** This release builds on published v1.14.0.

## Overview

The Conversational Workspace can retain multiple explicitly evaluated delivery
scenarios for one focused supply position and compare the existing deterministic
SupplyProjection facts. Each alternative is evaluated against the original
baseline, even when a user asks for another change after an earlier alternative.

## Included

- Bounded immutable scenario history scoped to the focused portfolio item.
- Deterministic comparison of consumption until delivery, inventory before and
  after delivery, signed safety-stock gap, stockout, required delivery volume and
  capacity exceedance, with operational units preserved and no conversions.
- Deterministic answers to factual questions such as highest inventory or which
  evaluated scenario maintains configured safety stock.
- Documentary turns continue to use the existing focused, scoped retrieval,
  generation, citation validation and semantic guard path; they preserve the
  evaluated scenario history for later comparison.
- Compact comparison tables appear in the existing Workspace and its Copy
  action. The Workspace now has a persistent right-side Pipeline Inspector with
  conversation-level execution history, historical operation selection, and
  safe diagnostic Copy. Failed executions remain inspectable after later turns.
- Inspector history retains at most the latest 50 Workspace operations per
  conversation and is cleared by New conversation. It reuses existing execution
  snapshots and views; it does not persist raw provider payloads or prompts.

## Boundaries

Only explicitly requested alternatives are evaluated. The Workspace does not
search dates or thresholds and does not recommend, rank, score, optimize, or
select a preferred scenario. It adds no supply calculations, physical aggregation,
unit conversion, new application mode, agent, or weakened documentary validation.
Deterministic comparison turns invoke neither generation nor retrieval.
