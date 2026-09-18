# Project context

indAI MA (Multi Agent) is a development prototype for intelligent decision support
in B2B energy and gas operations. It is an intelligent layer for questions,
contract knowledge, deterministic calculations, specialist coordination and
traceable explanations; it is not a replacement for procurement, trading, CRM, or
contract-management systems.

The current fictitious domain uses gas-contract and procurement scenarios. The
repository fixtures include Hospital Costa Sur and Industrias Mediterráneo. Their
values are development examples, not hardcoded product requirements.

Hospital Costa Sur has annual volume 48 GWh, monthly reference 4 GWh, flexibility
±15% (3.4–4.6 GWh), take-or-pay 85% (40.8 GWh minimum), and excess pricing Spot +
4 EUR/MWh. Industrias Mediterráneo has annual volume 72 GWh, monthly reference 6
GWh, flexibility ±20% (4.8–7.2 GWh), take-or-pay 80% (57.6 GWh minimum), and excess
pricing Spot + 6 EUR/MWh. These values are present in the contract fixtures and
evaluation data.

The specialists have separate responsibilities:

- `ProcurementAgent` calculates supply position, SHORT/BALANCED/LONG and spot
  coverage exposure.
- `CommercialAgent` retrieves contractual evidence, extracts facts, calculates
  contractual volume impact, and optionally asks an LLM to select explanatory
  evidence. It also supports deterministic documentary comparison by document.
- `RiskAgent` runs base and stress demand/supply scenarios and calculates deltas.
- `Supervisor` validates input, routes to specialists, collects results and performs
  deterministic or optional LLM synthesis.

The key business distinction is contractual excess versus procurement short. For
Hospital Costa Sur, forecast 4.8 GWh against a contractual maximum of 4.6 GWh is
0.2 GWh contractual excess. Against 4.3 GWh contracted supply, procurement
position is 4.3 − 4.8 = −0.5 GWh, a 0.5 GWh SHORT. They are different quantities.

See [Architecture](ARCHITECTURE.md) for implementation details and [Roadmap](ROADMAP.md)
for the boundary between v0.9.0 and future work.
