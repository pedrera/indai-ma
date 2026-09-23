# indAI MA v1.9.0 — Release candidate

**Status:** release candidate; the v1.9.0 tag has not been created.

## Industrial Gases Supply Assurance

This release adds an Industrial Gases capability alongside the existing energy
and Commercial flows. It addresses a different operational question: whether
physical inventory can maintain supply continuity until a planned delivery, and
how inventory is projected around that delivery.

The domain models customers, sites, applications, gas products, supply
installations, inventory snapshots, consumption forecasts and delivery plans.
Deterministic calculations produce days of supply, consumption through delivery,
inventory before and after delivery, safety-stock gap, stockout timing, required
delivery volume and tank-capacity overflow. Structured provenance records input
and calculation origins. Quantities use strict unit dimensions; the system does
not implicitly convert units.

## Interpretation

Structured interpretation can compose requests from explicit facts. An optional
LLM-assisted extractor proposes facts that are checked against strict schemas and
grounded in source evidence. Real-provider evaluation is manual and
environment-dependent. This path is experimental and is not needed by Healthcare,
Food & Beverage or Supply Portfolio screens.

## Healthcare

The Healthcare screen supports structured medical-oxygen supply assurance and
three explicit what-if comparisons: earlier delivery, a different planned
delivery quantity, and a hypothetical consumption forecast. Baseline and
alternative results are independently evaluated by the same deterministic
Supply Assurance service.

## Food & Beverage

The Food & Beverage screen evaluates separate CO₂ and N₂ supply branches. Each
branch retains its own application, product, installation, operational unit,
inputs, findings and projection. Results are not combined across gases.

## Supply Portfolio

Supply Portfolio evaluates an ordered set of independent requests and preserves
the result for each item. It does not derive an aggregate portfolio outcome.

## Quality / Evaluation

Domain and service validation, unit semantics, structured provenance and grounded
interpretation are covered by automated tests. FAST provides deterministic offline
application evaluation; the real-provider extraction harness is a separate manual,
environment-dependent check.

## Release boundaries

v1.9.0 does not provide cross-gas aggregation, portfolio physical totals, ranking,
winner selection, optimization, logistics recommendations, medical thresholds or
automatic decision making. The existing energy/Commercial vertical remains
separate from Industrial Gases Supply Assurance.
