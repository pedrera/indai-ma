# Changelog

This maintained changelog begins with v1.9.0. Earlier release history is recorded
in annotated Git tags and selected historical release notes; the repository does
not reconstruct every development milestone as a release.

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
