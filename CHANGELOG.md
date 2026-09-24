# Changelog

This maintained changelog begins with v1.9.0. Earlier release history is recorded
in annotated Git tags and selected historical release notes; the repository does
not reconstruct every development milestone as a release.

## v1.13.0 — Conversational Workspace (release candidate)

### Conversation-first industrial workspace

- Make **indAI MA** the default application experience. Users can ask about
  operational positions, applicable documents and explicit what-if scenarios in
  one conversation; previous specialized demo and technical modes remain under
  **Developer / Demo views**.
- Route requests through deterministic portfolio selection and the existing
  Supply Agent, identity-scoped Industrial Knowledge retrieval and single-item
  scenario evaluator. Bounded structured session context resolves follow-ups;
  explicit current identity takes precedence and ambiguous/stale references ask
  for clarification.
- Render position-level results, attention facts, evaluation issues, scoped
  document citations and baseline/alternative projections as structured cards.
  Keep provenance, tool trace and timings behind expandable diagnostics.
- Preserve independent evidence per position and avoid repeated domain-read or
  retrieval tool output in the workspace model context. Provider or retrieval
  failures do not erase the structured evidence already collected.
- Guard against unsupported stockout or capacity-overflow claims. A
  `safety_stock_breach` remains distinct from a physical stockout; structured
  projections and attention facts remain authoritative.

### Boundaries and limitations

- This is a UX/orchestration layer over existing capabilities. It adds no supply
  formulas, thresholds, totals, portfolio health state, ranking, priority,
  recommendation, optimization, logistics planning, unit conversion, automatic
  scenarios or autonomous agent loop.
- A configured generation model is used for explanations requiring synthesis;
  deterministic portfolio lists and explicit scenario calculations remain
  generation-independent. Documentary responses still require eligible scoped
  sources and valid citations.
- v1.13.0 is a release candidate; it has not been released or tagged.

## v1.12.0 — Portfolio Intelligence

### Portfolio-aware Supply Agent

- Add immutable deterministic portfolio query contracts for exact item,
  customer, site, application, gas product, installation, evaluation-status,
  finding-code and attention-fact filters. Matching preserves source order and
  records the structured fields that caused each match.
- Add independent per-position evidence bundles that retain original assurance
  results, requests, identities, attention findings, scoped sources, explicit
  scenarios and per-scope retrieval failures.
- Add bounded structured session context for selected item IDs, a unique focus,
  prior filter contract and a single explicit what-if target. Ambiguous and stale
  follow-ups request clarification; current explicit identity filters supersede
  prior context.
- Extend Supply Agent with deterministic attention/evaluation selection, identity
  questions, multi-position explanations and per-selected-item documentary
  retrieval. Exact citations are checked against per-item source scopes; approved
  explicitly global Industrial Gases policy remains separately identified.
- Keep explicit what-if evaluation limited to one resolved item and the existing
  deterministic scenario evaluator. Portfolio selection questions can be
  answered without a generation call.
- Extend Supply Portfolio UI and Pipeline Inspector to show resolved items,
  focused session scope, per-item facts and sources, explicit scenario execution,
  retrieval failures and citation validation.

### Boundaries and limitations

- Portfolio positions remain independent. No physical totals, portfolio status,
  severity, priority, scoring, ranking, winner, recommendation, optimization,
  batch what-if, automatic scenarios or unit conversion are introduced.
- The model explains observed structured and retrieved evidence; it is not
  authoritative for deterministic selection, calculations, findings, identity,
  provenance or citation scope. Retrieval or citation failure stays attached to
  its item and does not erase other items' evidence.
- v1.12.0 is the published baseline for the v1.13.0 release candidate.

## v1.11.0 — Industrial AI (release candidate)

### Industrial Knowledge and Supply Agent

- Add eight clearly fictional Industrial Gases demo documents covering Hospital
  Costa Sur O₂, Alimentos del Sur CO₂/N₂ and an explicitly applicable global
  demo policy. Document metadata is preserved with the existing RAG chunks.
- Add identity-scoped retrieval that filters eligible chunks by structured
  customer, site, application, gas product and installation identifiers before
  similarity ranking. Incomplete identity permits only the explicit global demo
  policy.
- Add an optional Supply Agent inside Supply Portfolio. It can read the existing
  position and attention result, search applicable knowledge, and evaluate one
  user-requested what-if through the existing deterministic scenario evaluator.
- Documentary-intent questions perform identity-scoped retrieval before the
  answer can be finalized; without retrieved chunks, documentary claims and
  unverified chunk references are replaced with an explicit evidence-unavailable
  response while structured operational results remain separate.
- Keep operational projection, findings, documentary chunks and LLM explanation
  as separate structured evidence with source references and tool trace.
- Distinguish domain `INVALID` / `MISSING_INPUTS`, no applicable documents,
  retrieval operational errors and generation/provider failures. Provider failure
  preserves evidence already collected.

### Boundaries

- Existing deterministic Healthcare, Food & Beverage and Supply Portfolio paths
  remain usable without generation. The optional Supply Agent does not calculate
  supply facts, convert units, create thresholds, recommend, rank, optimize,
  aggregate gases or search scenarios automatically.
- FAST remains offline and does not invoke generation LLMs.
- Supply Agent diagnostics retain the active operation recorder and distinguish
  executed stages from stages skipped because no knowledge retrieval occurred.

## v1.10.0 — Operational Intelligence (release candidate)

### Operational Attention

- Project existing Supply Assurance findings into ordered, position-level
  operational facts. `INVALID` and `MISSING_INPUTS` remain separate evaluation
  issues; positions without findings are labeled “No attention facts.”
- Add a Supply Portfolio summary and presentation-only filters without creating
  portfolio health, risk or status.

### Explicit Portfolio What-if

- Evaluate one user-selected delivery timing, planned delivery quantity or
  consumption-rate hypothesis at a time against a baseline using the existing
  deterministic Supply Assurance service.
- Preserve baseline and alternative results, the declared input change and
  position identity. No alternative is selected automatically.

### Decision Model foundation

- Add small structured records for the source position, baseline and ordered
  explicit alternatives. These records preserve existing service results and do
  not evaluate, rank or recommend decisions.

### Boundaries

- Supply Portfolio Operational Intelligence is deterministic and requires no
  LLM, RAG, Agent, Supervisor or Business API.
- No physical aggregation across gases or positions, unit conversion, scoring,
  ranking, priority, winner selection, recommendation, optimization or automatic
  scenario search.

## v1.9.0

### Industrial Gases Foundation

- Add a separate supply-assurance domain for physical inventory continuity, with
  structured customer, site, application, gas product, installation, inventory,
  forecast and delivery data.
- Add strict dimensional unit semantics, deterministic inventory projections,
  validation, and structured calculation provenance.

### Interpretation

- Add deterministic structured interpretation and grounded LLM-assisted fact
  extraction. LLM extraction is experimental and manually evaluated; it is not
  required by the deterministic application flows.

### Healthcare

- Add a structured medical-oxygen supply-assurance screen and explicit what-if
  comparisons for delivery timing, planned delivery quantity and consumption
  forecast.

### Food & Beverage

- Add isolated CO₂ and N₂ supply-assurance branches with separate applications,
  products, installations, units and projections.

### Supply Portfolio

- Add ordered evaluation of independent supply-assurance requests while retaining
  each result separately.

### Quality / Evaluation

- Add domain, service, interpretation, UI and portfolio tests, plus deterministic
  offline FAST evaluation and a manual real-provider extraction harness.

### Boundaries

- No cross-gas aggregation or portfolio physical totals.
- No ranking, winner selection, optimization, logistics recommendation, medical
  thresholds or automatic decision making.
