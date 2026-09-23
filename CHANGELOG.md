# Changelog

This maintained changelog begins with v1.9.0. Earlier release history is recorded
in annotated Git tags and selected historical release notes; the repository does
not reconstruct every development milestone as a release.

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
