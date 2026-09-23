# Changelog

This maintained changelog begins with v1.9.0. Earlier release history is recorded
in annotated Git tags and selected historical release notes; the repository does
not reconstruct every development milestone as a release.

## v1.9.0 — Release candidate

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
